"""Background worker: takes sync jobs from the database queue and runs the scrapers.

One event loop, up to WORKER_SLOTS jobs at a time, sellers inside a job in parallel.
Requests to the same seller still go one by one through the shared Throttle."""

import asyncio
import logging
from collections.abc import Callable

from asgiref.sync import sync_to_async
from django.conf import settings
from django.db import close_old_connections, connections
from django.utils import timezone

from apps.catalog.models import Source, SourceCity
from apps.core.enums import SyncTaskStatus, SyncTrigger
from scraper.adapters import SourceAdapter, get_adapter
from scraper.config import ScraperConfig
from scraper.enums import ResultStatus
from scraper.runner import Runner
from scraper.schemas import CityRef, ScrapeRequest, ScrapeResult
from scraper.throttle import SourceLimits, Throttle

from .ingest import ingest_result
from .models import SyncJob, SyncTask
from .services import claim_next_job, finish_job, recover_stale_jobs

log = logging.getLogger("worker")


def city_ref(source: Source, city) -> CityRef | None:
    link = SourceCity.objects.filter(source=source, city=city).first()
    if link is None:
        return None
    return CityRef(
        slug=city.slug,
        name=city.name,
        timezone=city.timezone,
        external_id=link.external_id,
        url_slug=link.url_slug,
    )


def prepare(job: SyncJob) -> list[tuple[SyncTask, ScrapeRequest | None]]:
    route = job.route
    pairs = []
    for task in job.tasks.select_related("source").filter(status=SyncTaskStatus.PENDING):
        origin, destination = (
            city_ref(task.source, route.origin),
            city_ref(task.source, route.destination),
        )
        request = None
        if origin and destination:
            request = ScrapeRequest(
                source=task.source.code,
                origin=origin,
                destination=destination,
                travel_date=job.travel_date,
            )
        pairs.append((task, request))
    SyncTask.objects.filter(pk__in=[t.pk for t, _ in pairs]).update(
        status=SyncTaskStatus.RUNNING, started_at=timezone.now()
    )
    return pairs


def fail_task(task: SyncTask, error: str) -> None:
    task.status = SyncTaskStatus.ERROR
    task.error = error
    task.finished_at = timezone.now()
    task.save(update_fields=["status", "error", "finished_at"])


class Worker:
    def __init__(
        self,
        slots: int | None = None,
        idle_sleep: float = 1.0,
        runner: Runner | None = None,
        adapter_factory: Callable[[str], SourceAdapter] = get_adapter,
    ):
        config = ScraperConfig()
        self.slots = slots or settings.WORKER_SLOTS
        self.idle_sleep = idle_sleep
        self.throttle = runner.throttle if runner else Throttle(config.state_file)
        self.runner = runner or Runner(config, self.throttle)
        self.adapter_factory = adapter_factory
        self.stopping = False

    def stop(self) -> None:
        self.stopping = True

    def configure_limits(self) -> None:
        for source in Source.objects.filter(is_reference=False):
            self.throttle.configure(
                source.code,
                SourceLimits(
                    min_interval=source.min_interval_sec, daily_budget=source.daily_budget
                ),
            )

    async def run(self, until_empty: bool = False) -> None:
        recovered = await sync_to_async(recover_stale_jobs)()
        if recovered:
            log.warning("closed %d jobs abandoned by a previous worker", recovered)
        await sync_to_async(self.configure_limits)()
        log.info("worker started, slots=%d", self.slots)

        running: set[asyncio.Task] = set()
        while not self.stopping:
            if len(running) < self.slots:
                job = await sync_to_async(claim_next_job)()
                if job:
                    task = asyncio.create_task(self.process(job))
                    running.add(task)
                    task.add_done_callback(running.discard)
                    continue
            if until_empty and not running:
                break
            await sync_to_async(close_old_connections)()
            await asyncio.sleep(self.idle_sleep)

        if running:
            await asyncio.gather(*running, return_exceptions=True)
        await self.close()
        log.info("worker stopped")

    async def close(self) -> None:
        await sync_to_async(connections.close_all)()

    async def process(self, job: SyncJob) -> SyncJob:
        log.info("job %s: %s %s (%s)", job.pk, job.route_id, job.travel_date, job.trigger)
        pairs = await sync_to_async(prepare)(job)
        priority = job.trigger == SyncTrigger.USER
        error = ""
        try:
            await asyncio.wait_for(
                asyncio.gather(*(self._run_task(t, r, priority) for t, r in pairs)),
                timeout=settings.SYNC_JOB_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            error = "job timed out"
            for task, _ in pairs:
                await sync_to_async(self._fail_if_unfinished)(task.pk)
        job = await sync_to_async(finish_job)(job, error)
        log.info("job %s finished: %s", job.pk, job.status)
        return job

    async def _run_task(self, task: SyncTask, request: ScrapeRequest | None, priority: bool):
        if request is None:
            await sync_to_async(fail_task)(task, "source has no IDs for these cities")
            return
        try:
            adapter = self.adapter_factory(task.source.code)
        except KeyError as exc:
            await sync_to_async(fail_task)(task, str(exc))
            return
        result: ScrapeResult = await self.runner.run(
            adapter, request, priority=priority, use_proxy=task.source.use_proxy
        )
        if result.status == ResultStatus.SKIPPED:
            log.warning("%s skipped: %s", task.source.code, result.error)
        await sync_to_async(ingest_result)(task, result)

    @staticmethod
    def _fail_if_unfinished(task_pk: int) -> None:
        task = SyncTask.objects.get(pk=task_pk)
        if task.status in (SyncTaskStatus.PENDING, SyncTaskStatus.RUNNING):
            fail_task(task, "job timed out")
