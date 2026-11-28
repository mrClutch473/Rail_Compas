import pickle
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone

from apps.catalog.models import City, Route
from apps.core.enums import SyncJobStatus, SyncTaskStatus, SyncTrigger
from apps.sync.models import SyncJob
from apps.web.models import ClickOut
from apps.web.templatetags.ui import ago, duration, plural, rub
from apps.web.warm import warm_caches

from .factories import PriceSnapshotFactory, SyncJobFactory, SyncTaskFactory, TripFactory

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()


def synced(world, prices: dict[str, str], reference: str | None = None, car_class="coupe"):
    """One finished sync job; prices per source code for one train."""
    msk = ZoneInfo("Europe/Moscow")
    departure = datetime.combine(world["day"], datetime.min.time(), msk) + timedelta(
        hours=18, minutes=44
    )
    trip = TripFactory(
        route=world["route"],
        train_number="346С",
        departure_date=world["day"],
        departure_at=departure,
        arrival_at=departure + timedelta(hours=17, minutes=43),
        duration_min=17 * 60 + 43,
    )
    job = SyncJobFactory(route=world["route"], travel_date=world["day"], status=SyncJobStatus.DONE)
    snaps = {}
    for code, price in prices.items():
        task = SyncTaskFactory(job=job, source=world[code], finished_at=timezone.now())
        snaps[code] = PriceSnapshotFactory(
            trip=trip,
            task=task,
            source=world[code],
            car_class=car_class,
            price=Decimal(price),
            seats=12,
            url=f"{world[code].base_url}/train/346",
        )
    if reference:
        snaps["rzd"] = PriceSnapshotFactory(
            trip=trip, task=task, source=world["rzd"], car_class=car_class, price=Decimal(reference)
        )
    return snaps


def results_url(world, day=None):
    day = day or world["day"]
    return reverse("web:results", args=["volgograd", "samara", day.isoformat()])


def test_home_lists_cities_and_popular_routes(client, world):
    synced(world, {"tutu": "4371.32"})
    html = client.get(reverse("web:home")).content.decode()
    assert "cities-data" in html and "Самара" in html
    assert "4 372 ₽" in html


def test_search_redirects_to_results(client, world):
    response = client.get(
        reverse("web:search"),
        {"from": "volgograd", "to": "samara", "date": world["day"].isoformat()},
    )
    assert response.status_code == 302
    assert response.url == results_url(world)


def test_search_without_cities_goes_home(client):
    response = client.get(reverse("web:search"), {"from": "volgograd", "to": "volgograd"})
    assert response.url == reverse("web:home")


def test_results_compare_sellers_and_reference(client, world):
    synced(world, {"tutu": "4371.32", "ott": "4664.00"}, reference="3886.40")
    html = client.get(results_url(world)).content.decode()
    assert "346С" in html
    assert "лучшая" in html
    assert "разница 293 ₽ (6%)" in html
    assert "наценка от 12%" in html
    assert "13:27" in html  # arrival in Samara local time (UTC+4), not Moscow 12:27


def test_results_without_data_start_a_sync(client, world):
    html = client.get(results_url(world)).content.decode()
    job = SyncJob.objects.get()
    assert job.trigger == SyncTrigger.USER
    assert reverse("web:sync_status", args=[job.pk]) in html
    assert "Запрашиваем цены" in html


def test_results_for_past_date_go_to_today(client, world):
    yesterday = timezone.localdate() - timedelta(days=1)
    response = client.get(results_url(world, yesterday))
    assert response.url == results_url(world, timezone.localdate())


def test_results_too_far_ahead(client, world):
    html = client.get(
        results_url(world, timezone.localdate() + timedelta(days=120))
    ).content.decode()
    assert "Продажа ещё не открыта" in html


def test_unknown_pair_is_not_supported(client, world):
    City.objects.create(name="Нигде", slug="nowhere")
    url = reverse("web:results", args=["volgograd", "nowhere", world["day"].isoformat()])
    html = client.get(url).content.decode()
    assert "не поддерживается" in html
    assert not Route.objects.filter(destination__slug="nowhere").exists()


def test_sync_button_queues_and_polls(client, world):
    url = reverse("web:sync", args=["volgograd", "samara", world["day"].isoformat()])
    html = client.post(url).content.decode()
    job = SyncJob.objects.get()
    assert f'hx-get="{reverse("web:sync_status", args=[job.pk])}"' in html
    client.post(url)
    assert SyncJob.objects.count() == 1


def test_sync_button_after_fresh_sync_says_so(client, world):
    synced(world, {"tutu": "4000", "ott": "4100"})
    url = reverse("web:sync", args=["volgograd", "samara", world["day"].isoformat()])
    html = client.post(url).content.decode()
    assert "только что обновлены" in html


def test_sync_button_is_rate_limited(client, world, settings):
    settings.SYNC_COOLDOWN_SECONDS = 0
    url = reverse("web:sync", args=["volgograd", "samara", world["day"].isoformat()])
    for _ in range(10):
        client.post(url)
        SyncJob.objects.update(status=SyncJobStatus.DONE)
    html = client.post(url).content.decode()
    assert "Слишком много обновлений" in html


def test_finished_job_refreshes_the_page(client, world):
    job = SyncJobFactory(route=world["route"], travel_date=world["day"], status=SyncJobStatus.DONE)
    response = client.get(reverse("web:sync_status", args=[job.pk]))
    assert response.status_code == 204
    assert response["HX-Refresh"] == "true"


def test_running_job_shows_progress_per_seller(client, world):
    job = SyncJobFactory(
        route=world["route"], travel_date=world["day"], status=SyncJobStatus.RUNNING
    )
    SyncTaskFactory(job=job, source=world["tutu"], status=SyncTaskStatus.OK, offers_count=9)
    SyncTaskFactory(job=job, source=world["ott"], status=SyncTaskStatus.RUNNING)
    html = client.get(reverse("web:sync_status", args=[job.pk])).content.decode()
    assert "9 предложений" in html
    assert "запрашиваем…" in html


def test_go_records_click_and_redirects(client, world):
    snaps = synced(world, {"tutu": "4371.32", "ott": "4664.00"})
    response = client.get(reverse("web:go", args=[snaps["tutu"].pk]))
    assert response.url == "https://www.tutu.ru/train/346"
    click = ClickOut.objects.get()
    assert click.was_cheapest and click.price_shown == Decimal("4371.32")


def test_go_never_redirects_outside_the_seller(client, world):
    snaps = synced(world, {"tutu": "4371.32"})
    snaps["tutu"].url = "https://evil.example/phish"
    snaps["tutu"].save()
    response = client.get(reverse("web:go", args=[snaps["tutu"].pk]))
    assert response.url == "https://www.tutu.ru"


def test_reference_price_is_not_a_purchase_link(client, world):
    snaps = synced(world, {"tutu": "4371.32"}, reference="3886.40")
    assert client.get(reverse("web:go", args=[snaps["rzd"].pk])).status_code == 404


def test_results_page_query_budget(client, world, django_assert_max_num_queries):
    synced(world, {"tutu": "4371.32", "ott": "4664.00"}, reference="3886.40")
    with django_assert_max_num_queries(15):
        client.get(results_url(world))


def test_about_page(client, world):
    assert "Как это работает" in client.get(reverse("web:about")).content.decode()


def test_unknown_page_uses_custom_404(client, settings):
    settings.DEBUG = False
    response = client.get("/no/such/page/")
    assert response.status_code == 404
    assert "Такой станции нет" in response.content.decode()


@pytest.mark.parametrize(
    ("value", "expected"),
    [(Decimal("4905.86"), "4 906 ₽"), (Decimal("890"), "890 ₽"), (None, "—")],
)
def test_rub(value, expected):
    assert rub(value) == expected


@pytest.mark.parametrize(
    ("n", "expected"), [(1, "1 место"), (3, "3 места"), (11, "11 мест"), (22, "22 места")]
)
def test_plural(n, expected):
    assert plural(n, "место,места,мест") == expected


def test_duration_and_ago():
    assert duration(1055) == "17 ч 35 мин"
    assert duration(1452) == "1 д"
    assert ago(timezone.now() - timedelta(minutes=20)) == "20 мин назад"
    assert ago(None) == "ещё не обновлялось"


def test_small_percent_is_not_zero():
    from apps.web.templatetags.ui import percent

    assert percent(0.4) == "<1%"
    assert percent(6.3) == "6%"


def test_new_sync_shows_through_the_cache(client, world):
    snaps = synced(world, {"tutu": "4371", "ott": "4664"})
    assert "разница 293 ₽" in client.get(results_url(world)).content.decode()
    job = SyncJobFactory(route=world["route"], travel_date=world["day"], status=SyncJobStatus.DONE)
    task = SyncTaskFactory(job=job, source=world["tutu"], finished_at=timezone.now())
    PriceSnapshotFactory(trip=snaps["tutu"].trip, task=task, source=world["tutu"], price=4000)
    assert "разница 664 ₽" in client.get(results_url(world)).content.decode()


def test_warm_caches_fill_home_and_analytics(world):
    synced(world, {"tutu": "4371", "ott": "4664"}, reference="3886")
    warm_caches()
    assert cache.get("popular")[0].price == Decimal("4371")
    report = cache.get("analytics-report:7")
    assert report.comparisons == 1
    assert len(pickle.dumps(report)) < 5000
