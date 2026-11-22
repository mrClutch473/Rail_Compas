import asyncio
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.core.management import call_command
from django.utils import timezone

from apps.catalog.models import City, Route, Source, SourceCity, Station
from apps.core.cache import bump_data_version, cached, data_version
from apps.core.enums import SyncJobStatus, SyncTaskStatus, SyncTrigger
from apps.offers.models import PriceSnapshot, Trip
from apps.offers.selectors import current_snapshots, price_history, source_states
from apps.sync.ingest import ingest_result
from apps.sync.models import SyncJob
from apps.sync.services import (
    NoSourcesForRoute,
    claim_next_job,
    recover_stale_jobs,
    request_sync,
)
from apps.sync.worker import Worker
from scraper import adapters
from scraper.adapters import SourceAdapter
from scraper.config import ScraperConfig
from scraper.enums import CarClass, ResultStatus
from scraper.errors import BlockedError
from scraper.runner import Runner
from scraper.schemas import (
    CityRef,
    ParseResult,
    RawCapture,
    RawOffer,
    ScrapeRequest,
    ScrapeResult,
)
from scraper.throttle import Throttle

pytestmark = pytest.mark.django_db(transaction=True)

MSK = ZoneInfo("Europe/Moscow")
DAY = date(2026, 12, 5)


def offer(number="001Ж", car_class=CarClass.COUPE, price="5000", at="15:55", **kw):
    hour, minute = map(int, at.split(":"))
    departure = datetime(2026, 12, 5, hour, minute, tzinfo=MSK)
    return RawOffer(
        train_number=number,
        departure_at=departure,
        arrival_at=departure + timedelta(hours=17),
        origin_station=kw.pop("origin_station", "Волгоград-1"),
        origin_station_code=kw.pop("origin_station_code", "2020500"),
        destination_station=kw.pop("destination_station", "Москва Павелецкая"),
        destination_station_code=kw.pop("destination_station_code", "2000005"),
        car_class=car_class,
        price=Decimal(price),
        seats=10,
        url="https://seller.test/",
        **kw,
    )


SCRIPTS: dict[str, list] = {}


def make_adapter(code: str) -> type[SourceAdapter]:
    class ScriptedAdapter(SourceAdapter):
        async def fetch(self, ctx, request):
            step = SCRIPTS[code].pop(0)
            if isinstance(step, Exception):
                raise step
            return RawCapture(strategy="api", payloads=[{"result": step}])

        def parse(self, capture, request):
            return capture.payloads[0]["result"]

        def offer_url(self, request):
            return "https://seller.test/"

    ScriptedAdapter.code = code
    return ScriptedAdapter


@pytest.fixture
def catalog(monkeypatch, tmp_path):
    monkeypatch.setenv("SCRAPER_DATA_DIR", str(tmp_path))
    for code in ("alpha", "beta"):
        monkeypatch.setitem(adapters.ADAPTERS, code, make_adapter(code))
    SCRIPTS.clear()

    volgograd = City.objects.create(name="Волгоград", slug="volgograd", timezone="Europe/Volgograd")
    moskva = City.objects.create(name="Москва", slug="moskva")
    alpha = Source.objects.create(
        code="alpha", name="Alpha", base_url="https://a.test", priority=10, min_interval_sec=0
    )
    beta = Source.objects.create(
        code="beta", name="Beta", base_url="https://b.test", priority=20, min_interval_sec=0
    )
    Source.objects.create(
        code="rzd_base", name="Тариф РЖД", base_url="https://r.test", is_reference=True
    )
    for source in (alpha, beta):
        for city in (volgograd, moskva):
            SourceCity.objects.create(source=source, city=city, external_id=city.slug)
    route = Route.objects.create(origin=volgograd, destination=moskva)
    return route


def run_queue():
    config = ScraperConfig()
    throttle = Throttle(state_file=None)
    worker = Worker(slots=2, idle_sleep=0.01, runner=Runner(config, throttle))
    asyncio.run(worker.run(until_empty=True))


def sync(route, trigger=SyncTrigger.USER, **scripts):
    for code, steps in scripts.items():
        SCRIPTS.setdefault(code, []).extend(steps)
    outcome = request_sync(route, DAY, trigger)
    run_queue()
    outcome.job.refresh_from_db()
    return outcome.job


def test_request_creates_one_task_per_seller(catalog):
    outcome = request_sync(catalog, DAY, SyncTrigger.USER)
    assert outcome.outcome == "created"
    assert sorted(outcome.job.tasks.values_list("source__code", flat=True)) == ["alpha", "beta"]
    assert outcome.job.priority == 0


def test_second_click_joins_the_running_job(catalog):
    first = request_sync(catalog, DAY, SyncTrigger.USER)
    second = request_sync(catalog, DAY, SyncTrigger.USER)
    assert second.outcome == "joined"
    assert second.job.pk == first.job.pk


def test_fresh_data_is_not_synced_again(catalog, settings):
    sync(catalog, alpha=[ParseResult(offers=[offer()])], beta=[ParseResult(is_empty=True)])
    assert request_sync(catalog, DAY, SyncTrigger.USER).outcome == "fresh"
    settings.SYNC_COOLDOWN_SECONDS = 0
    assert request_sync(catalog, DAY, SyncTrigger.USER).outcome == "created"


def test_route_without_sellers_is_refused(catalog):
    kazan = City.objects.create(name="Казань", slug="kazan")
    route = Route.objects.create(origin=catalog.origin, destination=kazan)
    with pytest.raises(NoSourcesForRoute):
        request_sync(route, DAY, SyncTrigger.USER)


def test_worker_writes_trips_stations_and_snapshots(catalog):
    job = sync(
        catalog,
        alpha=[ParseResult(offers=[offer(), offer(car_class=CarClass.PLATZKART, price="3500")])],
        beta=[
            ParseResult(
                offers=[offer(price="5200")],
                reference_offers=[offer(price="4500")],
            )
        ],
    )
    assert job.status == SyncJobStatus.DONE
    trip = Trip.objects.get()
    assert (trip.train_number, trip.departure_date) == ("001Ж", DAY)
    assert trip.origin_station.express_code == "2020500"
    assert not trip.origin_station.is_verified

    by_source = sorted(PriceSnapshot.objects.values_list("source__code", "car_class", "price"))
    assert by_source == [
        ("alpha", "coupe", Decimal("5000.00")),
        ("alpha", "platzkart", Decimal("3500.00")),
        ("beta", "coupe", Decimal("5200.00")),
        ("rzd_base", "coupe", Decimal("4500.00")),
    ]
    assert len(current_snapshots(catalog, DAY)) == 4


def test_twin_train_numbers_land_in_one_trip(catalog):
    sync(
        catalog,
        alpha=[ParseResult(offers=[offer(number="358С", at="10:27")])],
        beta=[ParseResult(offers=[offer(number="357С", at="10:27", price="5100")])],
    )
    trip = Trip.objects.get()
    assert {trip.train_number, *trip.alt_numbers} == {"357С", "358С"}
    assert PriceSnapshot.objects.filter(trip=trip).count() == 2


def test_station_names_from_other_sellers_become_aliases(catalog):
    sync(
        catalog,
        alpha=[ParseResult(offers=[offer(destination_station="Павелецкий вокзал")])],
        beta=[ParseResult(is_empty=True)],
    )
    station = Station.objects.get(express_code="2000005")
    assert station.name == "Павелецкий вокзал"


def test_one_blocked_seller_makes_the_job_partial(catalog):
    job = sync(
        catalog,
        alpha=[ParseResult(offers=[offer()])],
        beta=[BlockedError("captcha")],
    )
    assert job.status == SyncJobStatus.PARTIAL
    statuses = dict(job.tasks.values_list("source__code", "status"))
    assert statuses == {"alpha": SyncTaskStatus.OK, "beta": SyncTaskStatus.BLOCKED}
    assert set(PriceSnapshot.objects.values_list("source__code", flat=True)) == {"alpha"}

    states = {s.source.code: s for s in source_states(catalog, DAY)}
    assert states["alpha"].synced_at is not None
    assert states["beta"].synced_at is None and states["beta"].last_status == "blocked"


def test_class_missing_from_latest_sync_means_no_seats(catalog, settings):
    settings.SYNC_COOLDOWN_SECONDS = 0
    both = [offer(), offer(car_class=CarClass.PLATZKART, price="3500")]
    sync(catalog, alpha=[ParseResult(offers=both)], beta=[ParseResult(is_empty=True)])
    sync(
        catalog,
        alpha=[ParseResult(offers=[offer(price="5100")])],
        beta=[ParseResult(is_empty=True)],
    )

    current = [(s.source.code, s.car_class, s.price) for s in current_snapshots(catalog, DAY)]
    assert current == [("alpha", "coupe", Decimal("5100.00"))]
    history = price_history(Trip.objects.get(), "coupe")
    assert [s.price for s in history] == [Decimal("5000.00"), Decimal("5100.00")]


def test_sudden_drop_in_trains_is_flagged(catalog, settings):
    settings.SYNC_COOLDOWN_SECONDS = 0
    many = [offer(number=f"00{i}Ж", at=f"1{i}:00") for i in range(1, 6)]
    sync(catalog, alpha=[ParseResult(offers=many)], beta=[ParseResult(is_empty=True)])
    job = sync(catalog, alpha=[ParseResult(offers=many[:1])], beta=[ParseResult(is_empty=True)])
    warnings = job.tasks.get(source__code="alpha").warnings
    assert any(w["code"] == "offers_drop" for w in warnings)


def test_people_go_before_the_schedule(catalog):
    kazan = City.objects.create(name="Казань", slug="kazan")
    for source in Source.objects.filter(is_reference=False):
        SourceCity.objects.create(source=source, city=kazan, external_id="kazan")
    background = Route.objects.create(origin=catalog.origin, destination=kazan)
    request_sync(background, DAY, SyncTrigger.SCHEDULE)
    user_job = request_sync(catalog, DAY, SyncTrigger.USER).job
    assert claim_next_job().pk == user_job.pk


def test_job_left_running_by_a_dead_worker_is_closed(catalog):
    job = request_sync(catalog, DAY, SyncTrigger.USER).job
    SyncJob.objects.filter(pk=job.pk).update(
        status=SyncJobStatus.RUNNING, started_at=timezone.now() - timedelta(hours=1)
    )
    assert recover_stale_jobs() == 1
    job.refresh_from_db()
    assert job.status == SyncJobStatus.FAILED
    assert set(job.tasks.values_list("status", flat=True)) == {SyncTaskStatus.ERROR}


def test_sync_command_runs_in_process(catalog, capsys):
    SCRIPTS["alpha"] = [ParseResult(offers=[offer()])]
    SCRIPTS["beta"] = [ParseResult(is_empty=True)]
    call_command("sync", "volgograd", "moskva", "2026-12-05", "--now")
    out = capsys.readouterr().out
    assert "created" in out and "done" in out
    assert PriceSnapshot.objects.count() == 1


def test_new_prices_refresh_page_cache(catalog):
    task = request_sync(catalog, DAY, SyncTrigger.USER).job.tasks.get(source__code="alpha")
    city = CityRef(slug="x", name="X", timezone="Europe/Moscow", external_id="x")
    now = datetime.now(MSK)
    result = ScrapeResult(
        source="alpha",
        request=ScrapeRequest(source="alpha", origin=city, destination=city, travel_date=DAY),
        status=ResultStatus.OK,
        offers=[offer()],
        started_at=now,
        finished_at=now,
    )
    before = data_version()
    ingest_result(task, result)
    assert data_version() == before + 1


def test_cached_value_lives_until_new_data():
    calls = []

    def build():
        calls.append(1)
        return len(calls)

    assert cached("x", 1, build=build) == 1
    assert cached("x", 1, build=build) == 1
    assert cached("x", 2, build=build) == 2
    bump_data_version()
    assert cached("x", 1, build=build) == 3
