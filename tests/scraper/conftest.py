from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import httpx
import pytest

from scraper.adapters import SourceAdapter
from scraper.config import ScraperConfig
from scraper.enums import CarClass
from scraper.schemas import CityRef, ParseResult, RawCapture, RawOffer, ScrapeRequest
from scraper.throttle import SourceLimits, Throttle
from scraper.transport import FetchContext

MSK = ZoneInfo("Europe/Moscow")
TRAVEL_DATE = date(2026, 12, 5)


def offer(number="001Ж", price="4523.40", hours=17, **kw) -> RawOffer:
    departure = datetime(2026, 12, 5, 15, 55, tzinfo=MSK)
    return RawOffer(
        train_number=number,
        departure_at=departure,
        arrival_at=departure + timedelta(hours=hours),
        car_class=kw.pop("car_class", CarClass.COUPE),
        price=Decimal(price),
        **kw,
    )


@pytest.fixture
def request_():
    return ScrapeRequest(
        source="fake",
        origin=CityRef(slug="volgograd", timezone="Europe/Volgograd", external_id="1"),
        destination=CityRef(slug="moskva", external_id="2"),
        travel_date=TRAVEL_DATE,
    )


class FakeAdapter(SourceAdapter):
    """Reads JSON from a mocked endpoint; the body decides what `parse` returns."""

    code = "fake"

    async def fetch(self, ctx: FetchContext, request: ScrapeRequest) -> RawCapture:
        await ctx.get_json("https://fake.test/offers")
        return RawCapture(strategy="api", payloads=ctx.payloads)

    def parse(self, capture: RawCapture, request: ScrapeRequest) -> ParseResult:
        body = capture.payloads[-1]["body"]
        if body.get("crash"):
            raise RuntimeError("adapter bug")
        offers = [offer(**row) for row in body.get("offers", [])]
        return ParseResult(offers=offers, is_empty=body.get("empty", False))

    def offer_url(self, request: ScrapeRequest) -> str:
        return "https://fake.test/"


class Responder:
    """Queue of responses for httpx.MockTransport; each call takes the next one."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        item = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def config(tmp_path):
    return ScraperConfig(data_dir=tmp_path, task_timeout=10, cooldown_minutes=60)


@pytest.fixture
def sleeps():
    return []


@pytest.fixture
def throttle(sleeps):
    async def fake_sleep(seconds):
        sleeps.append(seconds)

    t = Throttle(state_file=None, sleep=fake_sleep)
    t.configure("fake", SourceLimits(min_interval=0, daily_budget=100, jitter=0))
    return t


def client_factory(responder):
    def factory(config, proxy=None, **headers):
        return httpx.AsyncClient(transport=httpx.MockTransport(responder))

    return factory
