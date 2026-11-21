"""Read side: what the site shows for a route and date.

The current state of a seller is the set of snapshots from its latest successful task for
that route and date (D-15). A class missing there means no seats, not "unknown"."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from django.conf import settings
from django.utils import timezone

from apps.catalog.models import Route, Source
from apps.core.enums import SUCCESS_TASK_STATUSES
from apps.sync.models import SyncTask

from .models import PriceSnapshot, Trip


@dataclass
class SourceState:
    source: Source
    synced_at: datetime | None
    last_status: str | None
    last_error: str

    @property
    def is_stale(self) -> bool:
        if self.synced_at is None:
            return True
        return timezone.now() - self.synced_at > timedelta(hours=settings.STALE_AFTER_HOURS)


def latest_success_tasks(route: Route, travel_date: date) -> dict[int, SyncTask]:
    tasks = (
        SyncTask.objects.filter(
            route=route, travel_date=travel_date, status__in=SUCCESS_TASK_STATUSES
        )
        .select_related("source")
        .order_by("source_id", "-finished_at")
    )
    latest: dict[int, SyncTask] = {}
    for task in tasks:
        latest.setdefault(task.source_id, task)
    return latest


def current_snapshots(route: Route, travel_date: date) -> list[PriceSnapshot]:
    tasks = latest_success_tasks(route, travel_date)
    return list(
        PriceSnapshot.objects.filter(task__in=tasks.values())
        .select_related("trip__origin_station", "trip__destination_station", "source")
        .order_by("trip__departure_at", "car_class", "source__priority")
    )


def source_states(route: Route, travel_date: date) -> list[SourceState]:
    latest_ok = latest_success_tasks(route, travel_date)
    last_any = {}
    for task in SyncTask.objects.filter(route=route, travel_date=travel_date).order_by(
        "source_id", "-finished_at"
    ):
        last_any.setdefault(task.source_id, task)

    states = []
    for source in Source.objects.filter(is_active=True, is_reference=False):
        ok = latest_ok.get(source.pk)
        last = last_any.get(source.pk)
        states.append(
            SourceState(
                source=source,
                synced_at=ok.finished_at if ok else None,
                last_status=last.status if last else None,
                last_error=last.error if last else "",
            )
        )
    return states


def price_history(trip: Trip, car_class: str) -> list[PriceSnapshot]:
    return list(
        PriceSnapshot.objects.filter(trip=trip, car_class=car_class)
        .select_related("source")
        .order_by("collected_at")
    )
