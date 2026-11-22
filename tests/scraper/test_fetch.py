"""The fetch side of adapters against a fake server that answers with stored responses."""

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from scraper import lookup
from scraper.adapters import get_adapter
from scraper.errors import BlockedError, FetchTimeout, LayoutChanged, NetworkError
from scraper.refs import city_refs
from scraper.storage import load_raw
from scraper.transport import FetchContext

FIXTURES = Path(__file__).parent / "fixtures"


def run_fetch(adapter, request, handler):
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await adapter.fetch(FetchContext(http=client), request)

    return asyncio.run(go())


@pytest.mark.parametrize("code", ["tutu", "onetwotrip", "aviakassa"])
def test_fetch_then_parse_gives_same_offers(code):
    adapter = get_adapter(code)
    request, stored = load_raw(FIXTURES / code / "regular.json.gz")
    seen = []

    def handler(http_request):
        seen.append(http_request)
        return httpx.Response(200, json=stored.payloads[-1]["body"])

    capture = run_fetch(adapter, request, handler)
    assert len(seen) == 1
    assert capture.url == adapter.offer_url(request)
    fresh = adapter.parse(capture, request).offers
    assert fresh == adapter.parse(stored, request).offers


def test_request_shapes():
    tutu_request, _ = load_raw(FIXTURES / "tutu" / "regular.json.gz")
    ott_request, _ = load_raw(FIXTURES / "onetwotrip" / "regular.json.gz")
    ak_request, _ = load_raw(FIXTURES / "aviakassa" / "regular.json.gz")
    seen = {}

    def handler(http_request):
        seen[http_request.url.host] = http_request
        return httpx.Response(200, json={})

    for code, request in (
        ("tutu", tutu_request),
        ("onetwotrip", ott_request),
        ("aviakassa", ak_request),
    ):
        run_fetch(get_adapter(code), request, handler)

    tutu = seen["offers-api.tutu.ru"]
    route = json.loads(tutu.content)["routes"][0]
    assert tutu.method == "POST"
    assert route["departureDate"] == tutu_request.travel_date.isoformat()
    assert route["departureStationCode"] == tutu_request.origin.external_id

    ott = seen["www.onetwotrip.com"].url.params
    assert ott["date"] == ott_request.travel_date.strftime("%d%m%Y")
    assert ott["from"] == ott_request.origin.external_id

    ak = seen["www.aviakassa.com"].url.params
    assert ak["search_id"].endswith(f"{ak_request.travel_date:%d%m%Y}.0.24")


def fetch_one(response=None, error=None):
    def handler(http_request):
        if error:
            raise error
        return response

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            ctx = FetchContext(http=client)
            try:
                return await ctx.get_json("https://seller.test/api")
            finally:
                assert len(ctx.payloads) <= 1

    return asyncio.run(go())


CAPTCHA = "<html><body>Подтвердите, что вы не робот</body></html>"


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"response": httpx.Response(429)}, BlockedError),
        ({"response": httpx.Response(200, html=CAPTCHA)}, BlockedError),
        ({"response": httpx.Response(502)}, NetworkError),
        ({"response": httpx.Response(404)}, LayoutChanged),
        ({"response": httpx.Response(200, text="<html>ok</html>")}, LayoutChanged),
        ({"error": httpx.ReadTimeout("slow")}, FetchTimeout),
        ({"error": httpx.ConnectError("refused")}, NetworkError),
    ],
)
def test_transport_errors(kwargs, expected):
    with pytest.raises(expected):
        fetch_one(**kwargs)


def test_transport_keeps_json():
    assert fetch_one(httpx.Response(200, json={"a": 1})) == {"a": 1}


def test_city_refs_from_seed():
    refs = city_refs("tutu")
    assert refs["volgograd"].external_id == "2020500"
    assert refs["volgograd"].timezone == "Europe/Volgograd"


@pytest.fixture
def fake_sellers(monkeypatch):
    answers = {
        "/suggest/railway_simple/": [{"id": 2024000, "value": "Самара"}],
        "/api/seo/schedule/link": {"url": "/poezda/Samara/Volgograd/"},
        "/_railways/rzd/suggestStations": {"result": [{"id": 22871, "trans": "samara"}]},
        "/api/v4/railway/search/get-autocomplete-list": {
            "data": {"items": [{"parent_code": "2024000"}]}
        },
    }

    def handler(http_request):
        return httpx.Response(200, json=answers[http_request.url.path])

    monkeypatch.setattr(
        lookup, "_client", lambda: httpx.Client(transport=httpx.MockTransport(handler))
    )
    return answers


def test_lookup_reads_suggestions(fake_sellers):
    assert lookup.tutu_code("Самара") == "2024000"
    assert lookup.tutu_slugs("2024000", "2020500") == ("Samara", "Volgograd")
    assert lookup.onetwotrip_station("Самара") == ("22871", "samara")
    assert lookup.aviakassa_code("Самара") == "2024000"


def test_lookup_nothing_found(fake_sellers):
    fake_sellers["/suggest/railway_simple/"] = []
    fake_sellers["/_railways/rzd/suggestStations"] = {"result": []}
    fake_sellers["/api/seo/schedule/link"] = {"url": None}
    assert lookup.tutu_code("Нигде") is None
    assert lookup.onetwotrip_station("Нигде") is None
    assert lookup.tutu_slugs("1", "2") is None
