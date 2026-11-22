import io

import pytest
from django.core.management import call_command
from django.utils import timezone

from apps.analytics.verification import read_sheet, score, sheet_rows, spread, write_sheet
from apps.sync.models import SyncJob

from .factories import SyncJobFactory
from .test_web_v2 import add_train

pytestmark = pytest.mark.django_db


def test_spread_takes_first_last_and_middle():
    assert spread([1, 2, 3, 4, 5], 3) == [1, 3, 5]
    assert spread([1, 2], 3) == [1, 2]
    assert spread([1, 2, 3], 1) == [1]


@pytest.fixture
def sheet(world):
    job = SyncJobFactory(route=world["route"], travel_date=world["day"], status="done")
    add_train(world, "001Ж", 15, 17, {"tutu": "5000", "ott": "5600.40"}, job=job)
    add_train(world, "089Ж", 2, 24, {"tutu": "3500"}, job=job)
    add_train(world, "015Ж", 19, 20, {"tutu": "4100"}, job=job)
    offset = (world["day"] - timezone.localdate()).days
    return sheet_rows([world["route"]], [offset], trains=2)


def test_sheet_picks_trains_and_survives_csv(sheet):
    assert {r["train"] for r in sheet} == {"089Ж", "015Ж"}
    assert sheet[0]["departure"] == "02:00"
    stream = io.StringIO()
    write_sheet(sheet, stream)
    stream.seek(0)
    assert read_sheet(stream) == sheet


def test_score_counts_price_time_and_missing(sheet):
    a, b = sheet
    a.update(site_price="3 500", site_departure="02:00")
    b.update(site_price="4 150", site_departure="19:00")
    extra = dict(a, seller="OneTwoTrip", site_price="", note="Нет на сайте")
    unchecked = dict(a, site_price="")
    result = score([a, b, extra, unchecked])
    assert result.unchecked == 1
    assert result.by_seller["Туту"].both_ok == 1
    assert result.by_seller["Туту"].checked == 2
    assert result.total.checked == 3
    assert round(result.total.rate) == 33
    assert [r["train"] for r in result.mismatches] == ["015Ж", "089Ж"]


def test_commands_write_and_score(world, sheet, tmp_path):
    path = tmp_path / "check.csv"
    call_command("verification_sheet", "--routes", "1", "--days", "10", "-o", str(path))
    rows = read_sheet(path.open(encoding="utf-8"))
    assert len(rows) == len(sheet)
    out = io.StringIO()
    call_command("verification_score", str(path), stdout=out)
    assert "not filled yet" in out.getvalue()


def test_sync_option_queues_jobs(world):
    call_command("verification_sheet", "--routes", "1", "--days", "1,7", "--sync")
    assert SyncJob.objects.filter(trigger="admin").count() == 2
