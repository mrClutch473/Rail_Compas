import json
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone

from apps.analytics.services import build_report
from apps.core.enums import SyncJobStatus
from apps.web.presenters import Filters, apply_filters, build_trains
from apps.web.templatetags.ui import series

from .factories import PriceSnapshotFactory, SyncJobFactory, SyncTaskFactory, TripFactory
from .test_web import results_url, synced

pytestmark = pytest.mark.django_db
MSK = ZoneInfo("Europe/Moscow")


@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()


def add_train(world, number, hour, duration_h, prices, car_class="coupe", job=None):
    departure = datetime.combine(world["day"], datetime.min.time(), MSK) + timedelta(hours=hour)
    trip = TripFactory(
        route=world["route"],
        train_number=number,
        departure_date=world["day"],
        departure_at=departure,
        arrival_at=departure + timedelta(hours=duration_h),
        duration_min=duration_h * 60,
    )
    job = job or SyncJobFactory(
        route=world["route"], travel_date=world["day"], status=SyncJobStatus.DONE
    )
    for code, price in prices.items():
        task = job.tasks.filter(source=world[code]).first() or SyncTaskFactory(
            job=job, source=world[code], finished_at=timezone.now()
        )
        PriceSnapshotFactory(
            trip=trip, task=task, source=world[code], car_class=car_class, price=Decimal(price)
        )
    return trip


def sellers(world):
    return [world["tutu"], world["ott"]]


def test_filters_ignore_garbage():
    filters = Filters.from_query({"class": "yacht", "dep": "noon", "hours": "x", "sort": "?"})
    assert filters == Filters()
    assert not filters.active


def test_filter_and_sort_trains(world):
    job = SyncJobFactory(route=world["route"], travel_date=world["day"], status=SyncJobStatus.DONE)
    add_train(world, "001Ж", 15, 17, {"tutu": "5000", "ott": "5600"}, job=job)
    add_train(world, "089Ж", 2, 24, {"tutu": "3500", "ott": "3600"}, job=job)
    add_train(
        world, "015Ж", 19, 20, {"tutu": "4100", "ott": "4000"}, car_class="platzkart", job=job
    )
    trains = build_trains(world["route"], world["day"], sellers(world))

    def numbers(**query):
        return [t.trip.train_number for t in apply_filters(trains, Filters.from_query(query))]

    assert numbers() == ["089Ж", "001Ж", "015Ж"]
    assert numbers(sort="price") == ["089Ж", "015Ж", "001Ж"]
    assert numbers(sort="duration") == ["001Ж", "015Ж", "089Ж"]
    assert numbers(sort="savings") == ["001Ж", "089Ж", "015Ж"]
    assert numbers(dep="night") == ["089Ж"]
    assert numbers(hours="18") == ["001Ж"]
    assert numbers(**{"class": "platzkart"}) == ["015Ж"]


def test_results_page_keeps_filters_in_links(client, world):
    add_train(world, "001Ж", 15, 17, {"tutu": "5000", "ott": "5600"})
    html = client.get(results_url(world) + "?class=coupe&sort=price").content.decode()
    assert 'id="trains-block"' in html
    assert "?class=coupe&amp;sort=price" in html  # date strip keeps the filters


def test_filters_that_match_nothing(client, world):
    add_train(world, "001Ж", 15, 17, {"tutu": "5000", "ott": "5600"})
    html = client.get(results_url(world) + "?dep=night").content.decode()
    assert "Под фильтры ничего не подошло" in html


def test_trip_page_has_history_chart(client, world):
    snaps = synced(world, {"tutu": "4371.32", "ott": "4664.00"}, reference="3886.40")
    trip = snaps["tutu"].trip
    html = client.get(reverse("web:trip", args=[trip.pk])).content.decode()
    assert "История цены" in html
    data = json.loads(
        html.split('id="chart-data" type="application/json">')[1].split("</script>")[0]
    )
    lines = data["coupe"]["series"]
    assert [(line["name"], line["token"]) for line in lines] == [
        ("Туту", "--series-1"),
        ("OneTwoTrip", "--series-2"),
        ("Тариф РЖД", "--series-ref"),
    ]
    assert lines[0]["points"][0][1] == 4371.32
    assert "Пока один замер" in html


def test_trip_page_query_budget(client, world, django_assert_max_num_queries):
    snaps = synced(world, {"tutu": "4371.32", "ott": "4664.00"}, reference="3886.40")
    with django_assert_max_num_queries(15):
        client.get(reverse("web:trip", args=[snaps["tutu"].trip.pk]))


def test_calendar_marks_cheapest_day(client, world):
    add_train(world, "001Ж", 15, 17, {"tutu": "5000", "ott": "5600"})
    url = reverse("web:calendar", args=["volgograd", "samara"])
    html = client.get(url).content.decode()
    assert "Календарь минимальных цен" in html
    assert "самый дешёвый день от 5 000 ₽" in html
    assert results_url(world) in html


def test_calendar_for_unknown_route(client, world):
    assert client.get(reverse("web:calendar", args=["samara", "volgograd"])).status_code == 404


def test_analytics_report_numbers(world):
    # one moment, one train: Tutu 4000, OneTwoTrip 4400, tariff 3600
    synced(world, {"tutu": "4000", "ott": "4400"}, reference="3600")
    report = build_report(7)
    assert report.comparisons == 1
    by_code = {s.source.code: s for s in report.sellers}
    assert by_code["tutu"].cheapest_share == 100
    assert by_code["onetwotrip"].cheapest_share == 0
    assert round(by_code["tutu"].avg_markup, 1) == 11.1
    assert round(by_code["onetwotrip"].avg_over_best, 1) == 10.0
    coupe = report.classes[0]
    assert coupe.avg_spread == Decimal("400")
    assert report.max_spread == Decimal("400")


def test_analytics_page(client, world):
    synced(world, {"tutu": "4000", "ott": "4400"}, reference="3600")
    html = client.get(reverse("web:analytics") + "?days=30").content.decode()
    assert "Кто чаще дешевле всех" in html
    assert "+11,1%" in html


def test_analytics_without_data(client, world):
    assert "Данных пока мало" in client.get(reverse("web:analytics")).content.decode()


def test_series_colours_follow_priority_not_price(world):
    assert series(world["tutu"]) == "var(--series-1)"
    assert series(world["ott"]) == "var(--series-2)"
    assert series(world["rzd"]) == "var(--series-ref)"


def test_robots_and_sitemap(client, world):
    robots = client.get("/robots.txt").content.decode()
    assert "Disallow: /go/" in robots and "Sitemap:" in robots
    sitemap = client.get("/sitemap.xml").content.decode()
    assert reverse("web:calendar", args=["volgograd", "samara"]) in sitemap
