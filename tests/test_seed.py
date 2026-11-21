import pytest
from django.core.management import call_command

from apps.catalog.models import City, Route, Source, SourceCity

pytestmark = pytest.mark.django_db


def test_seed_loads_reference_data():
    call_command("seed_reference")

    assert set(Source.objects.values_list("code", flat=True)) == {
        "tutu",
        "onetwotrip",
        "aviakassa",
        "rzd_base",
    }
    assert Source.objects.get(code="rzd_base").is_reference
    assert City.objects.get(slug="volgograd").timezone == "Europe/Volgograd"
    assert Route.objects.filter(origin__slug="volgograd", is_scheduled=True).count() == 13

    tutu_moscow = SourceCity.objects.get(source__code="tutu", city__slug="moskva")
    assert (tutu_moscow.external_id, tutu_moscow.url_slug) == ("2000000", "Moskva")


def test_seed_is_idempotent():
    call_command("seed_reference")
    counts = (Source.objects.count(), City.objects.count(), SourceCity.objects.count())
    call_command("seed_reference")
    assert (Source.objects.count(), City.objects.count(), SourceCity.objects.count()) == counts
