from datetime import timedelta

import pytest
from django.utils import timezone

from apps.catalog.models import City, Route, Source, SourceCity


@pytest.fixture
def world():
    volgograd = City.objects.create(name="Волгоград", slug="volgograd", timezone="Europe/Volgograd")
    samara = City.objects.create(name="Самара", slug="samara", timezone="Europe/Samara")
    tutu = Source.objects.create(
        code="tutu", name="Туту", base_url="https://www.tutu.ru", priority=10
    )
    ott = Source.objects.create(
        code="onetwotrip", name="OneTwoTrip", base_url="https://www.onetwotrip.com", priority=20
    )
    rzd = Source.objects.create(
        code="rzd_base", name="Тариф РЖД", base_url="https://ticket.rzd.ru", is_reference=True
    )
    for source in (tutu, ott):
        for city in (volgograd, samara):
            SourceCity.objects.create(source=source, city=city, external_id=city.slug)
    route = Route.objects.create(origin=volgograd, destination=samara, is_scheduled=True)
    day = timezone.localdate() + timedelta(days=10)
    return {"route": route, "day": day, "tutu": tutu, "ott": ott, "rzd": rzd}
