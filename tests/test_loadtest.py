import pytest

from apps.core.management.commands.loadtest import collect_urls, percentile

from .factories import SyncJobFactory
from .test_web_v2 import add_train

pytestmark = pytest.mark.django_db


def test_percentile():
    values = list(range(1, 101))
    assert percentile(values, 50) == 50
    assert percentile(values, 95) == 95
    assert percentile([7], 99) == 7


def test_only_pages_with_data(world):
    job = SyncJobFactory(route=world["route"], travel_date=world["day"], status="done")
    trip = add_train(world, "001Ж", 15, 17, {"tutu": "5000"}, job=job)
    urls = collect_urls()
    assert f"/r/volgograd/samara/{world['day'].isoformat()}/" in urls
    assert f"/trip/{trip.pk}/" in urls
    assert sum("/r/volgograd/samara/20" in u for u in urls) == 1
