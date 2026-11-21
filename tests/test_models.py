from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction

from apps.core.enums import SyncJobStatus

from .factories import (
    CityFactory,
    PriceSnapshotFactory,
    RouteFactory,
    SourceFactory,
    StationFactory,
    SyncJobFactory,
    SyncTaskFactory,
    TripFactory,
)

pytestmark = pytest.mark.django_db


def test_route_slug_is_built_from_cities():
    route = RouteFactory(
        origin=CityFactory(slug="volgograd", name="Волгоград"),
        destination=CityFactory(slug="moskva", name="Москва"),
    )
    assert route.slug == "volgograd-moskva"
    assert str(route) == "Волгоград → Москва"


def test_route_pair_is_unique():
    route = RouteFactory()
    with pytest.raises(IntegrityError), transaction.atomic():
        RouteFactory(origin=route.origin, destination=route.destination, slug="other")


def test_route_cannot_lead_to_itself():
    city = CityFactory()
    with pytest.raises(IntegrityError), transaction.atomic():
        RouteFactory(origin=city, destination=city)


def test_trip_key_is_unique():
    trip = TripFactory()
    with pytest.raises(IntegrityError), transaction.atomic():
        TripFactory(
            route=trip.route, train_number=trip.train_number, departure_date=trip.departure_date
        )


def test_trip_arrival_must_follow_departure():
    with pytest.raises(IntegrityError), transaction.atomic():
        trip = TripFactory.build(route=RouteFactory())
        trip.arrival_at = trip.departure_at - timedelta(minutes=1)
        trip.save()


def test_snapshot_price_must_be_positive():
    with pytest.raises(IntegrityError), transaction.atomic():
        PriceSnapshotFactory(price=Decimal("0"))


def test_only_one_active_job_per_route_and_date():
    job = SyncJobFactory()
    with pytest.raises(IntegrityError), transaction.atomic():
        SyncJobFactory(route=job.route, travel_date=job.travel_date)


def test_finished_job_does_not_block_a_new_one():
    job = SyncJobFactory(status=SyncJobStatus.DONE)
    again = SyncJobFactory(route=job.route, travel_date=job.travel_date)
    assert again.pk != job.pk


def test_one_task_per_source_in_a_job():
    task = SyncTaskFactory()
    with pytest.raises(IntegrityError), transaction.atomic():
        SyncTaskFactory(job=task.job, source=task.source)


def test_station_express_code_unique_but_blank_allowed():
    city = CityFactory()
    StationFactory(city=city, express_code="2020500")
    StationFactory(city=city)
    StationFactory(city=city)
    with pytest.raises(IntegrityError), transaction.atomic():
        StationFactory(city=city, express_code="2020500")


def test_source_ordering_follows_priority():
    SourceFactory(code="b", priority=20)
    SourceFactory(code="a", priority=10)
    from apps.catalog.models import Source

    assert list(Source.objects.values_list("code", flat=True)) == ["a", "b"]
