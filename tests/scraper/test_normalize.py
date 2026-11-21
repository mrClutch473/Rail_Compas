from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from apps.core.enums import CarClass as DjangoCarClass
from scraper.enums import CarClass
from scraper.normalize import (
    aware,
    car_class_from,
    normalize_station,
    normalize_train_number,
    to_price,
    twin_numbers,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("001Ж", "001Ж"),
        ("89Ж", "089Ж"),
        ("№ 001 А", "001А"),
        ("016A", "016А"),  # latin A
        ("357*С", "357С"),
        ("103zh", "103Ж"),
        ("7С", "007С"),
        (" 015ж ", "015Ж"),
    ],
)
def test_train_number(raw, expected):
    assert normalize_train_number(raw) == expected


@pytest.mark.parametrize("raw", ["", "Ж", "1234Ж", "015", "01ЖЖ"])
def test_bad_train_number(raw):
    with pytest.raises(ValueError):
        normalize_train_number(raw)


def test_twin_numbers_pair_odd_and_even():
    assert twin_numbers("357С") == {"358С"}
    assert twin_numbers("358С") == {"357С"}


@pytest.mark.parametrize(
    ("hint", "classes", "expected"),
    [
        ("RESERVED_SEAT", ["3Б"], CarClass.PLATZKART),
        ("Плацкарт", [], CarClass.PLATZKART),
        ("КУПЕ", ["2Ф"], CarClass.COUPE),
        ("COMPARTMENT", [], CarClass.COUPE),
        ("LUX", ["1Э"], CarClass.SV),  # Tutu calls SV "LUX"
        ("Luxury", ["1Э"], CarClass.SV),
        ("СВ", [], CarClass.SV),
        ("LUX", ["1А"], CarClass.LUX),
        ("SOFT", ["1М"], CarClass.LUX),
        ("SEDENTARY", ["2С"], CarClass.SEATED),
        ("SHARED", ["3О"], None),
    ],
)
def test_car_class(hint, classes, expected):
    assert car_class_from(hint, classes) == expected


def test_unknown_car_class_is_reported():
    with pytest.raises(KeyError):
        car_class_from("Бизнес")


def test_car_classes_match_django_enum():
    assert {c.value for c in CarClass} == set(DjangoCarClass.values)


@pytest.mark.parametrize(
    ("value", "fraction", "expected"),
    [
        (499354, 100, Decimal("4993.54")),
        (5437, 1, Decimal("5437.00")),
        (4941.9, 1, Decimal("4941.90")),
        ("3390.5", 1, Decimal("3390.50")),
    ],
)
def test_price(value, fraction, expected):
    assert to_price(value, fraction) == expected


def test_aware_keeps_offset_and_localizes_naive():
    with_offset = aware("2026-12-06T13:27:00+04:00", "Europe/Moscow")
    assert with_offset.utcoffset().total_seconds() == 4 * 3600
    naive = aware("2026-12-05T15:55:00", "Europe/Samara")
    assert naive == datetime(2026, 12, 5, 15, 55, tzinfo=ZoneInfo("Europe/Samara"))


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Москва Павелецкая", "Москва (Павелецкая)"),
        ("Павелецкий вокзал", "павелецкий"),
        ("Волгоград-1", "Волгоград 1"),
        ("Ряжск-1", "ряжск 1"),
    ],
)
def test_station_names_match(a, b):
    assert normalize_station(a) == normalize_station(b)
