import copy
import math
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from scraper.adapters import get_adapter
from scraper.enums import CarClass
from scraper.errors import LayoutChanged
from scraper.schemas import CityRef, ScrapeRequest
from scraper.storage import load_raw

FIXTURES = Path(__file__).parent / "fixtures" / "tutu"


@pytest.fixture(scope="module")
def adapter():
    return get_adapter("tutu")


def load(name):
    return load_raw(FIXTURES / f"{name}.json.gz")


def by_key(offers):
    return {(o.train_number, o.car_class): o for o in offers}


def test_regular_day(adapter):
    request, capture = load("regular")
    result = adapter.parse(capture, request)
    offers = by_key(result.offers)

    assert not result.is_empty
    assert set(offers) == {
        ("001Ж", CarClass.PLATZKART),
        ("001Ж", CarClass.COUPE),
        ("001Ж", CarClass.SV),
        ("015Ж", CarClass.PLATZKART),
        ("015Ж", CarClass.COUPE),
    }
    coupe = offers[("001Ж", CarClass.COUPE)]
    assert coupe.price == Decimal("7430.00")
    assert coupe.seats == 79
    assert coupe.departure_at.isoformat() == "2026-12-05T15:55:00+03:00"
    assert coupe.arrival_at.isoformat() == "2026-12-06T09:30:00+03:00"
    assert (coupe.origin_station, coupe.origin_station_code) == ("Волгоград-1", "2020500")
    assert coupe.destination_station_code == "2000005"
    assert coupe.url == "https://www.tutu.ru/poezda/Volgograd/Moskva/?date=05.12.2026&travelers=1"


def test_prices_are_without_bank_discount(adapter):
    request, capture = load("regular")
    platzkart = by_key(adapter.parse(capture, request).offers)[("015Ж", CarClass.PLATZKART)]
    assert platzkart.price == Decimal("3423.98")
    assert platzkart.extras == {"price_with_discount": "3081.59", "discounts": ["alfa_wallet"]}
    assert platzkart.price > Decimal(platzkart.extras["price_with_discount"])


def test_seats_come_from_car_list(adapter):
    request, capture = load("regular")
    offers = by_key(adapter.parse(capture, request).offers)
    assert offers[("015Ж", CarClass.COUPE)].seats == 45
    assert offers[("001Ж", CarClass.SV)].seats == 1


def test_arrival_in_samara_keeps_local_offset(adapter):
    request, capture = load("timezone_samara")
    offers = adapter.parse(capture, request).offers
    assert len(offers) == 9
    train = next(o for o in offers if o.train_number == "346С")
    assert train.arrival_at.utcoffset() == timedelta(hours=4)
    assert train.destination_station == "Самара"


def test_day_without_trains_is_empty_not_an_error(adapter):
    request, capture = load("empty")
    result = adapter.parse(capture, request)
    assert result.is_empty
    assert result.offers == []


def test_night_train_arrives_next_day(adapter):
    request, capture = load("night_train")
    train = by_key(adapter.parse(capture, request).offers)[("089Ж", CarClass.COUPE)]
    assert train.departure_at.isoformat() == "2026-12-12T02:36:00+03:00"
    assert train.arrival_at.date() == date(2026, 12, 13)
    assert train.destination_station == "Восточный вокзал"


def test_two_day_trip_to_ekaterinburg(adapter):
    request, capture = load("two_days_ekaterinburg")
    train = by_key(adapter.parse(capture, request).offers)[("107Ж", CarClass.PLATZKART)]
    assert train.arrival_at.utcoffset() == timedelta(hours=5)
    assert train.arrival_at - train.departure_at > timedelta(days=1)


def test_trip_with_transfer_is_skipped(adapter):
    request, capture = load("regular")
    capture = copy.deepcopy(capture)
    for chunk in capture.payloads[-1]["body"]:
        for route in chunk["dictionary"]["common"]["routes"].values():
            route["segmentIds"].append("extra-segment")
    result = adapter.parse(capture, request)
    assert result.offers == []


@pytest.mark.parametrize("body", [{"error": "x"}, [{"offers": {}}], [{"dictionary": {}}]])
def test_unknown_shape_is_layout_change(adapter, body):
    request, capture = load("regular")
    capture = copy.deepcopy(capture)
    capture.payloads[-1]["body"] = body
    with pytest.raises(LayoutChanged):
        adapter.parse(capture, request)


def test_offer_url_falls_back_to_station_codes(adapter):
    request = ScrapeRequest(
        source="tutu",
        origin=CityRef(slug="a", external_id="2020500"),
        destination=CityRef(slug="b", external_id="2000000"),
        travel_date=date(2026, 12, 5),
    )
    assert adapter.offer_url(request) == (
        "https://www.tutu.ru/poezda/rasp_d.php?nnst1=2020500&nnst2=2000000&date=05.12.2026"
    )


# What tutu.ru showed in the browser for this very response (screenshot, 21.11.2026):
# price with the bank discount / crossed-out price, rubles rounded up, and free seats.
SITE_SCREENSHOT = {
    ("001Ж", CarClass.PLATZKART): (4906, 5451, 27 + 114),
    ("001Ж", CarClass.COUPE): (6976, 7751, 14 + 65),
    ("001Ж", CarClass.SV): (25074, 26574, 1),
    ("015Ж", CarClass.PLATZKART): (3184, 3538, 44 + 159),
    ("015Ж", CarClass.COUPE): (4994, 5549, 4 + 39),
}


def test_parse_matches_what_the_site_showed(adapter):
    request, capture = load("site_screenshot_0512")
    offers = by_key(adapter.parse(capture, request).offers)
    assert set(offers) == set(SITE_SCREENSHOT)
    for key, (shown, crossed, seats) in SITE_SCREENSHOT.items():
        offer = offers[key]
        assert math.ceil(Decimal(offer.extras["price_with_discount"])) == shown, key
        assert math.ceil(offer.price) == crossed, key
        assert offer.seats == seats, key
