from dataclasses import dataclass
from datetime import date, timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Max
from django.utils import timezone

from apps.catalog.models import Route, Source
from apps.core.enums import (
    ACTIVE_JOB_STATUSES,
    SUCCESS_TASK_STATUSES,
    TRIGGER_PRIORITY,
    SyncJobStatus,
    SyncTaskStatus,
    SyncTrigger,
)

from .models import SyncJob, SyncTask


class NoSourcesForRoute(Exception):
    pass


@dataclass
class SyncRequest:
    job: SyncJob | None
    outcome: str  # "created", "joined" (already running) or "fresh" (synced moments ago)


def sources_for_route(route: Route) -> list[Source]:
    """Active sellers that know both cities of the route."""
    return list(
        Source.objects.filter(is_active=True, is_reference=False)
        .filter(cities__city=route.origin)
        .filter(cities__city=route.destination)
        .distinct()
    )


def last_success_at(route: Route, travel_date: date, sources: list[Source]):
    """When the least recently synced source was last synced successfully (None if never)."""
    finished = (
        SyncTask.objects.filter(
            route=route, travel_date=travel_date, status__in=SUCCESS_TASK_STATUSES
        )
        .values("source_id")
        .annotate(last=Max("finished_at"))
    )
    by_source = {row["source_id"]: row["last"] for row in finished}
    if any(s.pk not in by_source for s in sources):
        return None
    return min(by_source[s.pk] for s in sources)


def request_sync(route: Route, travel_date: date, trigger: SyncTrigger) -> SyncRequest:
    active = SyncJob.objects.filter(
        route=route, travel_date=travel_date, status__in=ACTIVE_JOB_STATUSES
    ).first()
    if active:
        return SyncRequest(active, "joined")

    sources = sources_for_route(route)
    if not sources:
        raise NoSourcesForRoute(str(route))

    synced_at = last_success_at(route, travel_date, sources)
    cooldown = timedelta(seconds=settings.SYNC_COOLDOWN_SECONDS)
    if synced_at and timezone.now() - synced_at < cooldown:
        latest = SyncJob.objects.filter(route=route, travel_date=travel_date).first()
        return SyncRequest(latest, "fresh")

    try:
        with transaction.atomic():
            job = SyncJob.objects.create(
                route=route,
                travel_date=travel_date,
                trigger=trigger,
                priority=TRIGGER_PRIORITY[trigger],
            )
            SyncTask.objects.bulk_create(
                SyncTask(job=job, source=s, route=route, travel_date=travel_date) for s in sources
            )
    except IntegrityError:
        # someone queued the same route and date a moment earlier
        active = SyncJob.objects.get(
            route=route, travel_date=travel_date, status__in=ACTIVE_JOB_STATUSES
        )
        return SyncRequest(active, "joined")
    return SyncRequest(job, "created")


def claim_job(job: SyncJob) -> bool:
    """Take a specific queued job (used when a sync is run in-process, not by the worker)."""
    taken = SyncJob.objects.filter(pk=job.pk, status=SyncJobStatus.QUEUED).update(
        status=SyncJobStatus.RUNNING, started_at=timezone.now()
    )
    if taken:
        job.refresh_from_db()
    return bool(taken)


def claim_next_job() -> SyncJob | None:
    with transaction.atomic():
        job = (
            SyncJob.objects.select_for_update(skip_locked=True)
            .filter(status=SyncJobStatus.QUEUED)
            .order_by("priority", "created_at")
            .first()
        )
        if job is None:
            return None
        job.status = SyncJobStatus.RUNNING
        job.started_at = timezone.now()
        job.save(update_fields=["status", "started_at"])
    return job


def finish_job(job: SyncJob, error: str = "") -> SyncJob:
    statuses = list(job.tasks.values_list("status", flat=True))
    succeeded = sum(s in SUCCESS_TASK_STATUSES for s in statuses)
    if statuses and succeeded == len(statuses):
        job.status = SyncJobStatus.DONE
    elif succeeded:
        job.status = SyncJobStatus.PARTIAL
    else:
        job.status = SyncJobStatus.FAILED
    job.error = error
    job.finished_at = timezone.now()
    job.save(update_fields=["status", "error", "finished_at"])
    return job


def recover_stale_jobs() -> int:
    """Jobs left 'running' by a worker that died are closed as failed."""
    border = timezone.now() - timedelta(minutes=settings.SYNC_STALE_MINUTES)
    stale = list(SyncJob.objects.filter(status=SyncJobStatus.RUNNING, started_at__lt=border))
    for job in stale:
        job.tasks.filter(status__in=[SyncTaskStatus.PENDING, SyncTaskStatus.RUNNING]).update(
            status=SyncTaskStatus.ERROR, error="abandoned by worker", finished_at=timezone.now()
        )
        finish_job(job, error="abandoned by worker")
    return len(stale)
