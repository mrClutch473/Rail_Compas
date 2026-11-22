"""A seller changes one train or one car type: we lose only that piece and leave a warning."""

import copy
from pathlib import Path

from scraper.adapters import get_adapter
from scraper.storage import load_raw

FIXTURES = Path(__file__).parent / "fixtures"


def spoil(code, mutate):
    request, capture = load_raw(FIXTURES / code / "regular.json.gz")
    adapter = get_adapter(code)
    before = adapter.parse(capture, request)
    capture = copy.deepcopy(capture)
    mutate(capture.payloads[-1]["body"])
    after = adapter.parse(capture, request)
    return before, after, {w.code for w in after.warnings}


def assert_partial_loss(before, after):
    assert 0 < len(after.offers) < len(before.offers)


def test_onetwotrip():
    def mutate(body):
        body["result"][0]["trainNumber"] = "???"
        body["result"][1]["places"][0]["typeName"] = "Дирижабль"
        body["infoMsgs"] = ["Технический перерыв"]

    before, after, codes = spoil("onetwotrip", mutate)
    assert_partial_loss(before, after)
    assert {"bad_train_number", "unknown_car_class", "source_warning"} <= codes


def test_aviakassa():
    def mutate(body):
        items = body["data"]["items"]
        items[0]["train_number"] = "???"
        group = items[1]["car_groups"][0]
        group["car_type_name"] = group["car_type"] = "Дирижабль"

    before, after, codes = spoil("aviakassa", mutate)
    assert_partial_loss(before, after)
    assert {"bad_train_number", "unknown_car_class"} <= codes


def test_tutu():
    def mutate(body):
        train = body[0]["dictionary"]["train"]
        first_voyage = next(iter(train["voyages"].values()))
        first_voyage["number"] = "???"
        first_condition = next(iter(train["conditions"].values()))
        first_condition["carType"] = "ZEPPELIN"
        body[0]["warnings"] = [{"content": "<b>Поезд</b> отменён"}]

    before, after, codes = spoil("tutu", mutate)
    assert_partial_loss(before, after)
    assert {"bad_train_number", "unknown_car_class", "source_warning"} <= codes
    assert any(w.detail == "Поезд отменён" for w in after.warnings)
