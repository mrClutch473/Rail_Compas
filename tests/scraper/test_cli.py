import os
import time
from datetime import UTC, datetime

from scraper import cli
from scraper.adapters import get_adapter
from scraper.cli import main
from scraper.config import ROOT
from scraper.enums import ResultStatus
from scraper.schemas import ScrapeResult
from scraper.storage import load_raw


def test_status_runs(capsys):
    assert main(["status"]) == 0
    assert "proxies configured" in capsys.readouterr().out


def test_unknown_source_is_reported(capsys):
    code = main(
        ["one", "--source", "nope", "--from", "volgograd", "--to", "moskva", "--date", "2026-12-05"]
    )
    assert code == 2
    assert "no adapter" in capsys.readouterr().err


FIXTURE = ROOT / "tests" / "scraper" / "fixtures" / "onetwotrip" / "regular.json.gz"


def test_replay_prints_offers_and_reference(capsys):
    assert main(["replay", str(FIXTURE)]) == 0
    out = capsys.readouterr().out
    assert "volgograd-moskva" in out
    assert "reference prices:" in out


def test_fixture_copies_file(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    monkeypatch.setattr(cli, "FIXTURES", tmp_path / "fixtures")
    assert main(["fixture", str(FIXTURE), "copy"]) == 0
    assert (tmp_path / "fixtures" / "onetwotrip" / "copy.json.gz").exists()


def test_cleanup_removes_old_days(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SCRAPER_DATA_DIR", str(tmp_path))
    old = tmp_path / "raw" / "tutu" / "2026-01-01"
    fresh = tmp_path / "raw" / "tutu" / "2026-11-22"
    old.mkdir(parents=True)
    fresh.mkdir(parents=True)
    month_ago = time.time() - 30 * 86400
    os.utime(old, (month_ago, month_ago))
    assert main(["cleanup"]) == 0
    assert not old.exists()
    assert fresh.exists()
    assert "removed 1" in capsys.readouterr().out


def test_one_runs_the_adapter(monkeypatch, capsys):
    request, capture = load_raw(FIXTURE)
    parsed = get_adapter("onetwotrip").parse(capture, request)

    class FakeRunner:
        def __init__(self, *args, **kwargs):
            pass

        async def run(self, adapter, scrape_request, **kwargs):
            now = datetime.now(UTC)
            return ScrapeResult(
                source=adapter.code,
                request=scrape_request,
                status=ResultStatus.OK,
                offers=parsed.offers,
                reference_offers=parsed.reference_offers,
                started_at=now,
                finished_at=now,
            )

    monkeypatch.setattr(cli, "Runner", FakeRunner)
    argv = ["one", "--source", "onetwotrip", "--from", "volgograd", "--to", "moskva"]
    assert main([*argv, "--date", "2026-12-05", "--no-raw"]) == 0
    assert "onetwotrip: ok" in capsys.readouterr().out
    assert main([*argv, "--date", "2026-12-05", "--json"]) == 0
    assert '"status": "ok"' in capsys.readouterr().out


def test_one_without_city_ids(capsys):
    argv = ["one", "--source", "tutu", "--from", "volgograd", "--to", "atlantis"]
    assert main([*argv, "--date", "2026-12-05"]) == 2
    assert "atlantis" in capsys.readouterr().err
