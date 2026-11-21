import asyncio
import logging
from datetime import date

from django.core.management.base import BaseCommand, CommandError

from apps.catalog.models import City, Route
from apps.core.enums import SyncTrigger
from apps.sync.services import NoSourcesForRoute, claim_job, request_sync
from apps.sync.worker import Worker


class Command(BaseCommand):
    help = "Queue a sync for a route and date; --now runs it right here without the worker."

    def add_arguments(self, parser):
        parser.add_argument("origin", help="city slug, e.g. volgograd")
        parser.add_argument("destination")
        parser.add_argument("date", help="YYYY-MM-DD")
        parser.add_argument("--now", action="store_true", help="process the job in this process")

    def handle(self, *args, origin, destination, date: str, now: bool, **options):
        try:
            origin_city = City.objects.get(slug=origin)
            destination_city = City.objects.get(slug=destination)
        except City.DoesNotExist as exc:
            raise CommandError(f"unknown city: {exc}") from exc
        route, _ = Route.objects.get_or_create(origin=origin_city, destination=destination_city)

        try:
            outcome = request_sync(route, self._date(date), SyncTrigger.ADMIN)
        except NoSourcesForRoute as exc:
            raise CommandError(f"no active source knows both cities of {exc}") from exc
        job = outcome.job
        self.stdout.write(f"job {job.pk if job else '-'}: {outcome.outcome}")

        if now and outcome.outcome == "created":
            logging.getLogger("httpx").setLevel(logging.WARNING)
            worker = Worker(slots=1)
            worker.configure_limits()
            if not claim_job(job):
                raise CommandError("the worker took this job first")
            job = asyncio.run(self._process(worker, job))
            for task in job.tasks.select_related("source"):
                line = f"  {task.source.code:<11} {task.status:<14} offers={task.offers_count}"
                if task.error:
                    line += f"  {task.error}"
                self.stdout.write(line)
            self.stdout.write(self.style.SUCCESS(f"job {job.pk}: {job.status}"))

    @staticmethod
    async def _process(worker: Worker, job):
        try:
            return await worker.process(job)
        finally:
            await worker.close()

    @staticmethod
    def _date(value: str) -> date:
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise CommandError("date must be YYYY-MM-DD") from exc
