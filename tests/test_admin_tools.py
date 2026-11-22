from datetime import timedelta

import httpx
import pytest
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from apps.catalog.lookup import fill_source_ids
from apps.catalog.models import City, Source, SourceCity, Station
from apps.core.enums import SyncTaskStatus
from apps.sync.models import SyncJob
from apps.web.dashboard import environment_callback, source_health

from .factories import SyncTaskFactory, TripFactory

pytestmark = pytest.mark.django_db


def run_action(client, model, action, objects):
    url = reverse(f"admin:{model._meta.app_label}_{model._meta.model_name}_changelist")
    data = {"action": action, "_selected_action": [obj.pk for obj in objects]}
    return client.post(url, data, follow=True)


def test_dashboard_renders(admin_client, world):
    SyncTaskFactory(source=world["tutu"], finished_at=timezone.now())
    response = admin_client.get(reverse("admin:index"))
    assert response.status_code == 200
    content = response.content.decode()
    assert "Площадки" in content
    assert "Туту" in content


def test_source_health_reports_cooldown(world):
    now = timezone.now()
    SyncTaskFactory(source=world["tutu"], finished_at=now - timedelta(hours=1))
    SyncTaskFactory(
        source=world["ott"],
        status=SyncTaskStatus.BLOCKED,
        error="ATTEMPTS_EXCEEDED, cooling down until 14:20",
        finished_at=now,
    )
    rows = {row["source"].code: row for row in source_health(now)}
    assert rows["tutu"]["state"] == ("success", "в порядке")
    assert rows["onetwotrip"]["state"][0] == "danger"
    assert rows["onetwotrip"]["cooling"] == "14:20 UTC"
    assert "rzd_base" not in rows


def test_environment_label():
    with override_settings(DEBUG=False):
        assert environment_callback(None) == ["Сервер", "success"]


def test_sync_week_queues_seven_days(admin_client, world):
    run_action(admin_client, type(world["route"]), "sync_week", [world["route"]])
    assert SyncJob.objects.filter(route=world["route"]).count() == 7


def test_mark_verified(admin_client):
    city = City.objects.create(name="Самара", slug="samara")
    station = Station.objects.create(city=city, name="Самара")
    run_action(admin_client, Station, "mark_verified", [station])
    station.refresh_from_db()
    assert station.is_verified


def test_merge_stations_moves_trips_and_keeps_names(admin_client):
    city = City.objects.create(name="Самара", slug="samara")
    main = Station.objects.create(city=city, name="Самара", is_verified=True)
    dup = Station.objects.create(city=city, name="Самара Пасс.", express_code="2024600")
    trip = TripFactory(destination_station=dup)
    run_action(admin_client, Station, "merge_into_first", [main, dup])
    main.refresh_from_db()
    trip.refresh_from_db()
    assert not Station.objects.filter(pk=dup.pk).exists()
    assert trip.destination_station == main
    assert main.aliases == ["Самара Пасс."]
    assert main.express_code == "2024600"


def test_merge_refuses_different_cities(admin_client):
    a = Station.objects.create(city=City.objects.create(name="А", slug="a"), name="А")
    b = Station.objects.create(city=City.objects.create(name="Б", slug="b"), name="Б")
    run_action(admin_client, Station, "merge_into_first", [a, b])
    assert Station.objects.count() == 2


def test_deactivate_source(admin_client, world):
    run_action(admin_client, Source, "deactivate", [world["ott"]])
    world["ott"].refresh_from_db()
    assert not world["ott"].is_active


@pytest.fixture
def fake_lookup(monkeypatch):
    from scraper import lookup

    monkeypatch.setattr(lookup, "tutu_code", lambda name: "2024000")
    monkeypatch.setattr(lookup, "tutu_slugs", lambda a, b: ("Samara", "Volgograd"))
    monkeypatch.setattr(lookup, "onetwotrip_station", lambda name: ("22871", "samara"))

    def broken(name):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(lookup, "aviakassa_code", broken)


def test_fill_source_ids(world, fake_lookup):
    Source.objects.create(code="aviakassa", name="Aviakassa", base_url="https://www.aviakassa.com")
    city = City.objects.create(name="Самара-2", slug="samara-2")
    report = fill_source_ids(city)
    assert sorted(report.added) == ["OneTwoTrip", "Туту"]
    assert "Aviakassa" in report.problems
    tutu = SourceCity.objects.get(city=city, source__code="tutu")
    assert (tutu.external_id, tutu.url_slug) == ("2024000", "Samara")


def test_fill_source_ids_keeps_existing(world, fake_lookup):
    samara = City.objects.get(slug="samara")
    report = fill_source_ids(samara)
    assert report.added == []
    assert SourceCity.objects.get(city=samara, source__code="tutu").external_id == "samara"


def test_setup_roles_is_idempotent():
    call_command("setup_roles")
    call_command("setup_roles")
    group = Group.objects.get(name="Оператор")
    codes = set(group.permissions.values_list("codename", flat=True))
    assert {"view_trip", "add_syncjob", "change_route"} <= codes
    assert "delete_pricesnapshot" not in codes
