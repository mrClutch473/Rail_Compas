"""Write a scraper result into the database: match trains to trips, stations to the
reference, and append price snapshots. One transaction per task."""

from datetime import timedelta
from zoneinfo import ZoneInfo

from django.db import transaction

from apps.catalog.models import City, Route, Source, Station
from apps.core.enums import SUCCESS_TASK_STATUSES
from apps.offers.models import PriceSnapshot, Trip
from scraper.normalize import normalize_station, twin_numbers
from scraper.schemas import Notice, RawOffer, ScrapeResult

from .models import SyncTask

REFERENCE_SOURCE = "rzd_base"
TIME_MISMATCH = timedelta(minutes=5)
DROP_MIN_PREVIOUS = 4


def resolve_station(city: City, name: str, code: str, cache: dict) -> Station | None:
    if not name and not code:
        return None
    key = (city.pk, code, name)
    if key in cache:
        return cache[key]

    station = None
    if code:
        station = Station.objects.filter(express_code=code).first()
    if station is None and name:
        wanted = normalize_station(name)
        for candidate in Station.objects.filter(city=city):
            names = [candidate.name, *candidate.aliases]
            if wanted in {normalize_station(n) for n in names}:
                station = candidate
                break
    if station is None:
        station, _ = Station.objects.get_or_create(
            city=city,
            name=name or code,
            defaults={"express_code": code, "is_verified": False},
        )
    elif name and name != station.name and name not in station.aliases:
        station.aliases = [*station.aliases, name]
        station.save(update_fields=["aliases"])
    if code and not station.express_code and not Station.objects.filter(express_code=code).exists():
        station.express_code = code
        station.save(update_fields=["express_code"])

    cache[key] = station
    return station


def match_trip(route: Route, offer: RawOffer, notices: list[Notice], cache: dict) -> Trip:
    local_date = offer.departure_at.astimezone(ZoneInfo(route.origin.timezone)).date()
    key = (offer.train_number, local_date)
    if key in cache:
        return cache[key]

    trip = Trip.objects.filter(
        route=route, train_number=offer.train_number, departure_date=local_date
    ).first()
    if trip is None:
        # same train under its twin number (357С at one seller, 358С at another)
        for candidate in Trip.objects.filter(
            route=route, departure_date=local_date, departure_at=offer.departure_at
        ):
            if offer.train_number in twin_numbers(candidate.train_number) | set(
                candidate.alt_numbers
            ):
                trip = candidate
                break
        if trip and offer.train_number not in trip.alt_numbers:
            trip.alt_numbers = [*trip.alt_numbers, offer.train_number]
            trip.save(update_fields=["alt_numbers", "updated_at"])

    if trip is None:
        trip = Trip.objects.create(
            route=route,
            train_number=offer.train_number,
            train_name=offer.train_name,
            departure_date=local_date,
            departure_at=offer.departure_at,
            arrival_at=offer.arrival_at,
            duration_min=int((offer.arrival_at - offer.departure_at).total_seconds() // 60),
        )
    elif abs(trip.departure_at - offer.departure_at) > TIME_MISMATCH:
        detail = f"{trip.train_number}: {trip.departure_at:%H:%M} vs {offer.departure_at:%H:%M}"
        notices.append(Notice(code="time_mismatch", detail=detail))

    cache[key] = trip
    return trip


def fill_stations(trip: Trip, route: Route, offer: RawOffer, station_cache: dict) -> None:
    changed = []
    if trip.origin_station_id is None:
        trip.origin_station = resolve_station(
            route.origin, offer.origin_station, offer.origin_station_code, station_cache
        )
        changed.append("origin_station")
    if trip.destination_station_id is None:
        trip.destination_station = resolve_station(
            route.destination,
            offer.destination_station,
            offer.destination_station_code,
            station_cache,
        )
        changed.append("destination_station")
    if not trip.train_name and offer.train_name:
        trip.train_name = offer.train_name
        changed.append("train_name")
    if changed:
        trip.save(update_fields=[*changed, "updated_at"])


def _offers_drop(task: SyncTask, count: int) -> Notice | None:
    previous = (
        SyncTask.objects.filter(
            source=task.source,
            route=task.route,
            travel_date=task.travel_date,
            status__in=SUCCESS_TASK_STATUSES,
        )
        .exclude(pk=task.pk)
        .order_by("-finished_at")
        .first()
    )
    if (
        previous
        and previous.offers_count >= DROP_MIN_PREVIOUS
        and count < previous.offers_count / 2
    ):
        return Notice(code="offers_drop", detail=f"{previous.offers_count} -> {count}")
    return None


@transaction.atomic
def ingest_result(task: SyncTask, result: ScrapeResult) -> SyncTask:
    route = Route.objects.select_related("origin", "destination").get(pk=task.route_id)
    notices = list(result.warnings)
    snapshots = []

    if result.is_success:
        trip_cache, station_cache = {}, {}
        reference = Source.objects.filter(code=REFERENCE_SOURCE).first()
        rows = [(task.source, o) for o in result.offers]
        if reference:
            rows += [(reference, o) for o in result.reference_offers]
        for source, offer in rows:
            trip = match_trip(route, offer, notices, trip_cache)
            fill_stations(trip, route, offer, station_cache)
            snapshots.append(
                PriceSnapshot(
                    trip=trip,
                    source=source,
                    task=task,
                    car_class=offer.car_class.value,
                    price=offer.price,
                    price_old=offer.price_old,
                    seats=offer.seats,
                    url=offer.url[:500],
                    extras=offer.extras,
                    collected_at=result.finished_at,
                )
            )
        PriceSnapshot.objects.bulk_create(snapshots)
        if drop := _offers_drop(task, len(result.offers)):
            notices.append(drop)

    task.status = result.status.value
    task.offers_count = len(result.offers)
    task.attempts = result.attempts
    task.duration_ms = result.duration_ms
    task.strategy = result.strategy
    task.proxy_used = result.proxy_used
    task.error = result.error[:2000]
    task.warnings = [n.model_dump() for n in notices]
    task.raw_ref = result.raw_ref
    task.started_at = result.started_at
    task.finished_at = result.finished_at
    task.save()
    return task
