import copy
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from scraper.adapters import get_adapter
from scraper.adapters.aviakassa import search_id
from scraper.enums import CarClass
from scraper.errors import LayoutChanged
from scraper.storage import load_raw

FIXTURES = Path(__file__).parent / "fixtures" / "aviakassa"


@pytest.fixture(scope="module")
def adapter():
    return get_adapter("aviakassa")


def parse(adapter, name):
    request, capture = load_raw(FIXTURES / f"{name}.json.gz")
    return adapter.parse(capture, request)


def by_key(offers):
    return {(o.train_number, o.car_class): o for o in offers}


def test_regular_day(adapter):
    offers = by_key(parse(adapter, "regular").offers)
    assert len(offers) == 5
    coupe = offers[("001Ж", CarClass.COUPE)]
    assert coupe.price == Decimal("7561.00")
    assert coupe.seats == 80
    assert coupe.service_classes == ["2Ф", "2Э", "2Т"]
    assert (coupe.origin_station_code, coupe.destination_station_code) == ("2020500", "2000005")
    assert coupe.arrival_at.isoformat() == "2026-12-06T09:30:00+03:00"
    assert offers[("001Ж", CarClass.SV)].price == Decimal("27946.00")


def test_platzkart_bedding_is_taken_out(adapter):
    platzkart = by_key(parse(adapter, "regular").offers)[("001Ж", CarClass.PLATZKART)]
    assert platzkart.extras == {"price_with_bedding": "5781.00"}
    assert platzkart.price == Decimal("5493.40")  # 5781 - 287.60 bedding


def test_trains_that_change_number_are_kept(adapter):
    offers = parse(adapter, "twin_numbers_samara").offers
    numbers = {o.train_number for o in offers}
    assert {"357С", "345С", "097С", "211Э"} <= numbers
    raw = next(o for o in offers if o.train_number == "357С").raw_number
    assert raw == "357*С"
    arrival = next(o for o in offers if o.train_number == "345С").arrival_at
    assert arrival.utcoffset() == timedelta(hours=4)


def test_empty_day(adapter):
    result = parse(adapter, "empty")
    assert result.is_empty and result.offers == []


def test_night_train(adapter):
    offers = by_key(parse(adapter, "night_train").offers)
    train = offers[("089Ж", CarClass.COUPE)]
    assert train.departure_at.isoformat().startswith("2026-12-12T02:36")
    assert train.arrival_at.date() == date(2026, 12, 13)


@pytest.mark.parametrize(
    "body",
    [
        {"success": False, "code": 500},
        {"success": True, "data": {}},
        {"success": True, "data": {"items": [{"train_number": "001Ж"}]}},
    ],
)
def test_unknown_shape_is_layout_change(adapter, body):
    request, capture = load_raw(FIXTURES / "regular.json.gz")
    capture = copy.deepcopy(capture)
    capture.payloads[-1]["body"] = body
    with pytest.raises(LayoutChanged):
        adapter.parse(capture, request)


def test_search_id_and_link(adapter):
    request, _ = load_raw(FIXTURES / "regular.json.gz")
    assert search_id(request) == "AKV4.AKV40000.1.0.0.ru.2020500.2000000.05122026.0.24"
    assert adapter.offer_url(request).endswith("/zhd/results/" + search_id(request))
