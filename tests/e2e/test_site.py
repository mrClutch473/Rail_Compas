"""User scenarios in a real browser: python -m pytest -m e2e"""

import re
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.sync.models import SyncJob, SyncTask
from apps.web.models import ClickOut

from ..factories import SyncJobFactory
from ..test_web import results_url
from ..test_web_v2 import add_train

pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]

expect = pytest.importorskip("playwright.sync_api").expect


@pytest.fixture
def trains(world):
    job = SyncJobFactory(route=world["route"], travel_date=world["day"], status="done")
    add_train(world, "001Ж", 15, 17, {"tutu": "5000", "ott": "5600"}, job=job)
    add_train(world, "089Ж", 2, 24, {"tutu": "3500", "ott": "3600"}, job=job)
    add_train(
        world, "015Ж", 19, 20, {"tutu": "4100", "ott": "4000"}, car_class="platzkart", job=job
    )
    return world


def card(page, number):
    return page.locator("article", has_text=number)


def no_horizontal_scroll(page):
    return page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def test_search_from_home(page, trains):
    page.goto("/")
    page.fill("#from-name", "Волг")
    page.locator("[data-city-list] li", has_text="Волгоград").first.click()
    page.fill("#to-name", "Сам")
    page.locator("[data-city-list] li", has_text="Самара").last.click()
    page.fill("#travel-date", trains["day"].isoformat())
    page.get_by_role("button", name="Найти").click()
    expect(page).to_have_url(re.compile(r"/r/volgograd/samara/"))
    for number in ("001Ж", "089Ж", "015Ж"):
        expect(card(page, number)).to_be_visible()


def test_filters_update_list_without_reload(page, trains):
    page.goto(results_url(trains))
    page.evaluate("window.marker = 1")
    page.locator("label", has_text="Плацкарт").click()
    expect(page).to_have_url(re.compile(r"class=platzkart"))
    expect(card(page, "015Ж")).to_be_visible()
    expect(card(page, "001Ж")).to_have_count(0)
    assert page.evaluate("window.marker") == 1

    page.select_option("select[name=sort]", "price")
    expect(page).to_have_url(re.compile(r"sort=price"))


def test_sync_button(page, trains):
    page.goto(results_url(trains))
    page.get_by_role("button", name="Обновить цены").click()
    expect(page.get_by_text("Цены только что обновлены")).to_be_visible()
    assert not SyncJob.objects.filter(status="queued").exists()

    hour_ago = timezone.now() - timedelta(hours=1)
    SyncJob.objects.update(finished_at=hour_ago)
    SyncTask.objects.update(finished_at=hour_ago)
    page.reload()
    page.get_by_role("button", name="Обновить цены").click()
    expect(page.get_by_text("Запрашиваем цены у площадок")).to_be_visible()
    assert SyncJob.objects.filter(route=trains["route"], status="queued").exists()


def test_seller_link_records_click(page, trains):
    page.route("https://www.tutu.ru/**", lambda route: route.fulfill(body="tutu"))
    page.route("https://www.onetwotrip.com/**", lambda route: route.fulfill(body="ott"))
    page.goto(results_url(trains))
    with page.context.expect_page() as popup:
        page.locator("a[href*='/go/']").first.click()
    popup.value.wait_for_load_state()
    assert popup.value.url.startswith("https://www.")
    assert ClickOut.objects.count() == 1


def test_trip_page_draws_chart(page, trains):
    trip = trains["route"].trips.get(train_number="001Ж")
    page.goto(reverse("web:trip", args=[trip.pk]))
    expect(page.locator("#price-chart")).to_be_visible()
    assert page.evaluate("Boolean(Chart.getChart(document.getElementById('price-chart')))")


@pytest.mark.parametrize("name", ["calendar", "analytics", "about"])
def test_other_pages_open(page, trains, name):
    args = ["volgograd", "samara"] if name == "calendar" else []
    response = page.goto(reverse(f"web:{name}", args=args))
    assert response.status == 200
    expect(page.locator("h1").first).to_be_visible()


def test_theme_toggle_is_remembered(page, trains):
    page.goto("/")
    before = page.evaluate("document.documentElement.dataset.theme || 'light'")
    page.click("[data-theme-toggle]")
    page.reload()
    assert page.evaluate("document.documentElement.dataset.theme") != before


def test_phone_width(page, trains):
    page.set_viewport_size({"width": 375, "height": 812})
    page.goto("/")
    assert no_horizontal_scroll(page)

    page.goto(results_url(trains))
    assert no_horizontal_scroll(page)
    panel = page.locator("[data-search-panel]")
    expect(panel).to_be_hidden()
    page.click("[data-toggle-search]")
    expect(panel).to_be_visible()

    trip = trains["route"].trips.get(train_number="001Ж")
    page.goto(reverse("web:trip", args=[trip.pk]))
    assert no_horizontal_scroll(page)
