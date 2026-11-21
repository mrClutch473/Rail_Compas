from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import factory

from apps.catalog.models import City, Route, Source, Station
from apps.core.enums import CarClass, SyncTaskStatus, SyncTrigger
from apps.offers.models import PriceSnapshot, Trip
from apps.sync.models import SyncJob, SyncTask

MSK = ZoneInfo("Europe/Moscow")


class CityFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = City
        django_get_or_create = ["slug"]

    name = factory.Sequence(lambda n: f"Город {n}")
    slug = factory.Sequence(lambda n: f"city-{n}")


class StationFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Station

    city = factory.SubFactory(CityFactory)
    name = factory.Sequence(lambda n: f"Вокзал {n}")


class SourceFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Source
        django_get_or_create = ["code"]

    code = factory.Sequence(lambda n: f"source{n}")
    name = factory.LazyAttribute(lambda o: o.code.title())
    base_url = "https://example.com"


class RouteFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Route

    origin = factory.SubFactory(CityFactory)
    destination = factory.SubFactory(CityFactory)


class TripFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Trip

    route = factory.SubFactory(RouteFactory)
    train_number = factory.Sequence(lambda n: f"{n % 1000:03d}Ж")
    departure_date = factory.LazyFunction(lambda: date.today() + timedelta(days=7))
    departure_at = factory.LazyAttribute(
        lambda o: datetime.combine(o.departure_date, datetime.min.time(), MSK) + timedelta(hours=15)
    )
    arrival_at = factory.LazyAttribute(lambda o: o.departure_at + timedelta(hours=17))
    duration_min = 17 * 60


class SyncJobFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = SyncJob

    route = factory.SubFactory(RouteFactory)
    travel_date = factory.LazyFunction(lambda: date.today() + timedelta(days=7))
    trigger = SyncTrigger.USER


class SyncTaskFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = SyncTask

    job = factory.SubFactory(SyncJobFactory)
    source = factory.SubFactory(SourceFactory)
    route = factory.LazyAttribute(lambda o: o.job.route)
    travel_date = factory.LazyAttribute(lambda o: o.job.travel_date)
    status = SyncTaskStatus.OK


class PriceSnapshotFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = PriceSnapshot

    trip = factory.SubFactory(TripFactory)
    task = factory.SubFactory(SyncTaskFactory)
    source = factory.LazyAttribute(lambda o: o.task.source)
    car_class = CarClass.COUPE
    price = Decimal("4523.40")
    collected_at = factory.LazyFunction(lambda: datetime.now(MSK))
