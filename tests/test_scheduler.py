from datetime import timedelta

import pytest
from django.core.management import call_command
from django.utils import timezone

from apps.catalog.models import City, Route, Source, SourceCity
from apps.core.enums import SyncJobStatus, SyncTrigger
from apps.sync.models import SyncJob
from apps.sync.scheduler import due_syncs, schedule_tick

from .factories import SyncJobFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def route(settings):
    settings.SCHEDULE_HOT_DAYS = 2
    settings.SCHEDULE_HOT_HOURS = 3
    settings.SCHEDULE_WARM_HOURS = 12
    settings.SCHEDULE_MAX_QUEUED = 3
    a = City.objects.create(name="А", slug="a")
    b = City.objects.create(name="Б", slug="b")
    source = Source.objects.create(code="s", name="S", base_url="https://s.test")
    for city in (a, b):
        SourceCity.objects.create(source=source, city=city, external_id=city.slug)
    return Route.objects.create(origin=a, destination=b, is_scheduled=True, horizon_days=5)


def synced(route, offset: int, hours_ago: float):
    day = timezone.localdate() + timedelta(days=offset)
    job = SyncJobFactory(route=route, travel_date=day, status=SyncJobStatus.DONE)
    SyncJob.objects.filter(pk=job.pk).update(
        finished_at=timezone.now() - timedelta(hours=hours_ago)
    )
    return day


def due_days(route):
    return [d.travel_date for d in due_syncs() if d.route == route]


def test_every_date_in_horizon_is_due_when_nothing_synced(route):
    today = timezone.localdate()
    assert due_days(route) == [today + timedelta(days=i) for i in range(5)]


def test_near_dates_refresh_after_three_hours(route):
    fresh = synced(route, 0, hours_ago=1)
    old = synced(route, 1, hours_ago=4)
    days = due_days(route)
    assert fresh not in days and old in days


def test_far_dates_refresh_after_twelve_hours(route):
    fresh = synced(route, 3, hours_ago=6)
    old = synced(route, 4, hours_ago=13)
    days = due_days(route)
    assert fresh not in days and old in days


def test_never_synced_go_first_then_near_dates(route):
    synced(route, 0, hours_ago=5)
    synced(route, 3, hours_ago=20)
    order = due_days(route)
    today = timezone.localdate()
    assert order[:3] == [today + timedelta(days=i) for i in (1, 2, 4)]
    assert order[3:] == [today, today + timedelta(days=3)]


def test_routes_without_schedule_are_ignored(route):
    route.is_scheduled = False
    route.save()
    assert due_syncs() == []


def test_dates_with_running_job_are_skipped(route):
    today = timezone.localdate()
    SyncJobFactory(route=route, travel_date=today, status=SyncJobStatus.RUNNING)
    assert today not in due_days(route)


def test_tick_fills_the_queue_only_up_to_the_limit(route):
    created = schedule_tick()
    assert len(created) == 3
    assert {j.trigger for j in created} == {SyncTrigger.SCHEDULE}
    assert {j.priority for j in created} == {50}
    assert schedule_tick() == []


def test_schedule_command_dry_run(route, capsys):
    call_command("schedule", "--dry-run")
    out = capsys.readouterr().out
    assert "due now: 5" in out
    assert "jobs/day" in out
    assert SyncJob.objects.count() == 0
