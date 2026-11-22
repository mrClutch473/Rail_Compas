import copy
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from scraper.adapters import get_adapter
from scraper.enums import CarClass
from scraper.errors import BlockedError, LayoutChanged
from scraper.storage import load_raw

FIXTURES = Path(__file__).parent / "fixtures" / "onetwotrip"

# Prices shown by ticket.rzd.ru (screenshots taken by the author on 21.11.2026).
RZD_SITE = {
    ("regular", "001Ж", CarClass.PLATZKART): 4655,
    ("regular", "001Ж", CarClass.COUPE): 6523,
    ("regular", "001Ж", CarClass.SV): 25228,
    ("regular", "015Ж", CarClass.PLATZKART): 3103,
    ("regular", "015Ж", CarClass.COUPE): 4811,
    ("rzd_checked_1212", "001Ж", CarClass.PLATZKART): 4655,
    ("rzd_checked_1212", "001Ж", CarClass.COUPE): 5818,
    ("rzd_checked_1212", "001Ж", CarClass.SV): 24061,
    ("rzd_checked_1212", "089Ж", CarClass.PLATZKART): 3325,
    ("rzd_checked_1212", "089Ж", CarClass.COUPE): 3468,
    ("timezone_samara", "358С", CarClass.COUPE): 2872,
    ("timezone_samara", "358С", CarClass.SV): 12351,
    ("timezone_samara", "097С", CarClass.PLATZKART): 2659,
}


@pytest.fixture(scope="module")
def adapter():
    return get_adapter("onetwotrip")


def parse(adapter, name):
    request, capture = load_raw(FIXTURES / f"{name}.json.gz")
    return adapter.parse(capture, request)


def by_key(offers):
    return {(o.train_number, o.car_class): o for o in offers}


def test_regular_day(adapter):
    result = parse(adapter, "regular")
    offers = by_key(result.offers)
    assert len(offers) == 5
    coupe = offers[("001Ж", CarClass.COUPE)]
    assert coupe.price == Decimal("7723.00")
    assert coupe.seats == 76
    assert coupe.departure_at.isoformat() == "2026-12-05T15:55:00+03:00"
    assert (coupe.origin_station_code, coupe.destination_station_code) == ("2020500", "2000005")
    assert coupe.destination_station == "Москва Павелецкая"
    assert coupe.url.startswith("https://www.onetwotrip.com/ru/poezda/train/?date=05122026")


def test_platzkart_without_bedding_and_its_markup(adapter):
    platzkart = by_key(parse(adapter, "regular").offers)[("001Ж", CarClass.PLATZKART)]
    # 5437 with bedding - 287.60 bedding - 30 OneTwoTrip markup on bedding
    assert platzkart.price == Decimal("5119.40")
    assert platzkart.extras == {"price_with_bedding": "5437.00"}


@pytest.mark.parametrize(("fixture", "train", "car_class"), list(RZD_SITE))
def test_reference_matches_rzd_site(adapter, fixture, train, car_class):
    reference = by_key(parse(adapter, fixture).reference_offers)[(train, car_class)]
    assert reference.price == RZD_SITE[(fixture, train, car_class)]


def test_seller_price_is_never_below_tariff(adapter):
    result = parse(adapter, "regular")
    tariffs = by_key(result.reference_offers)
    for key, offer in by_key(result.offers).items():
        assert offer.price >= tariffs[key].price


def test_samara_arrival_keeps_local_offset(adapter):
    train = by_key(parse(adapter, "timezone_samara").offers)[("358С", CarClass.COUPE)]
    assert train.arrival_at.utcoffset() == timedelta(hours=4)


def test_no_seats_error_means_empty_day(adapter):
    result = parse(adapter, "empty")
    assert result.is_empty
    assert result.offers == []
    assert "мест нет" in result.warnings[0].detail


def test_no_trains_on_date_means_empty_day(adapter):
    request, capture = load_raw(FIXTURES / "regular.json.gz")
    capture = copy.deepcopy(capture)
    capture.payloads[-1]["body"] = {
        "success": False,
        "error": {"code": 1310, "details": "В запрашиваемую дату поездов нет"},
    }
    assert adapter.parse(capture, request).is_empty


@pytest.mark.parametrize(
    "body",
    [
        [],
        {"success": True},
        {"success": False, "error": {"code": 500, "details": "boom"}},
        {"success": True, "result": [{"trainNumber": "001Ж"}]},
    ],
)
def test_unknown_shape_is_layout_change(adapter, body):
    request, capture = load_raw(FIXTURES / "regular.json.gz")
    capture = copy.deepcopy(capture)
    capture.payloads[-1]["body"] = body
    with pytest.raises(LayoutChanged):
        adapter.parse(capture, request)


def test_route_page_link(adapter):
    request, _ = load_raw(FIXTURES / "regular.json.gz")
    assert adapter.offer_url(request) == (
        "https://www.onetwotrip.com/ru/poezda/volgograd_moskva/?date=05122026&adults=1&s=true"
    )


def test_rate_limit_answer_is_a_block_not_a_crash(adapter):
    request, capture = load_raw(FIXTURES / "regular.json.gz")
    capture = copy.deepcopy(capture)
    capture.payloads[-1]["body"] = {
        "error": "ATTEMPTS_EXCEEDED",
        "result": [],
        "meta": {"time": 10},
    }
    with pytest.raises(BlockedError):
        adapter.parse(capture, request)


def test_string_error_is_layout_change(adapter):
    request, capture = load_raw(FIXTURES / "regular.json.gz")
    capture = copy.deepcopy(capture)
    capture.payloads[-1]["body"] = {"error": "SOMETHING_NEW"}
    with pytest.raises(LayoutChanged):
        adapter.parse(capture, request)
