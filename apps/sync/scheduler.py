"""Background schedule: keeps popular routes fresh so most visitors find prices already there.

Near dates change faster and matter more, so they are refreshed more often. The queue never
holds more than a few background jobs, which keeps the button fast for people."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from django.conf import settings
from django.db.models import Max
from django.utils import timezone

from apps.catalog.models import Route
from apps.core.enums import ACTIVE_JOB_STATUSES, SyncJobStatus, SyncTrigger

from .models import SyncJob
from .services import NoSourcesForRoute, request_sync

FINISHED = (SyncJobStatus.DONE, SyncJobStatus.PARTIAL)
SALE_DAYS = 89


@dataclass
class Due:
    route: Route
    travel_date: date
    last_synced: datetime | None
    hot: bool

    @property
    def sort_key(self):
        # never synced first, then near dates, then the oldest data
        never = self.last_synced is None
        return (not never, not self.hot, self.last_synced or datetime.min, self.travel_date)


def due_syncs(now: datetime | None = None) -> list[Due]:
    now = now or timezone.now()
    today = timezone.localdate(now)
    hot_age = timedelta(hours=settings.SCHEDULE_HOT_HOURS)
    warm_age = timedelta(hours=settings.SCHEDULE_WARM_HOURS)

    routes = list(Route.objects.filter(is_scheduled=True).select_related("origin", "destination"))
    last = {
        (row["route_id"], row["travel_date"]): row["last"]
        for row in SyncJob.objects.filter(
            route__in=routes, travel_date__gte=today, status__in=FINISHED
        )
        .values("route_id", "travel_date")
        .annotate(last=Max("finished_at"))
    }
    busy = set(
        SyncJob.objects.filter(route__in=routes, status__in=ACTIVE_JOB_STATUSES).values_list(
            "route_id", "travel_date"
        )
    )

    due = []
    for route in routes:
        horizon = min(route.horizon_days, SALE_DAYS)
        for offset in range(horizon):
            day = today + timedelta(days=offset)
            if (route.pk, day) in busy:
                continue
            hot = offset < settings.SCHEDULE_HOT_DAYS
            synced = last.get((route.pk, day))
            if synced is None or now - synced >= (hot_age if hot else warm_age):
                due.append(Due(route, day, synced, hot))
    return sorted(due, key=lambda d: d.sort_key)


def schedule_tick(now: datetime | None = None) -> list[SyncJob]:
    """Queue background jobs up to the limit. Returns the jobs created."""
    queued = SyncJob.objects.filter(
        trigger=SyncTrigger.SCHEDULE, status=SyncJobStatus.QUEUED
    ).count()
    room = settings.SCHEDULE_MAX_QUEUED - queued
    created = []
    if room <= 0:
        return created
    for item in due_syncs(now):
        try:
            outcome = request_sync(item.route, item.travel_date, SyncTrigger.SCHEDULE)
        except NoSourcesForRoute:
            continue
        if outcome.outcome == "created":
            created.append(outcome.job)
            if len(created) >= room:
                break
    return created
