import asyncio

import httpx
import pytest

from scraper.enums import ResultStatus
from scraper.runner import Runner
from scraper.storage import load_raw

from .conftest import FakeAdapter, Responder, client_factory


def run(config, throttle, sleeps, responder, request, **kw):
    async def fake_sleep(seconds):
        sleeps.append(seconds)

    runner = Runner(config, throttle, sleep=fake_sleep, client_factory=client_factory(responder))
    return asyncio.run(runner.run(FakeAdapter(), request, **kw))


def ok(body):
    return httpx.Response(200, json=body)


def test_successful_run_keeps_raw_and_can_be_replayed(config, throttle, sleeps, request_):
    responder = Responder(ok({"offers": [{"number": "001Ж"}, {"number": "015Ж", "price": "3100"}]}))
    result = run(config, throttle, sleeps, responder, request_)

    assert result.status == ResultStatus.OK
    assert [o.train_number for o in result.offers] == ["001Ж", "015Ж"]
    assert result.attempts == 1 and result.strategy == "api"

    stored_request, capture = load_raw(config.raw_dir / result.raw_ref)
    assert stored_request == request_
    assert len(FakeAdapter().parse(capture, stored_request).offers) == 2


def test_network_error_is_retried(config, throttle, sleeps, request_):
    responder = Responder(httpx.ConnectError("boom"), ok({"offers": [{}]}))
    result = run(config, throttle, sleeps, responder, request_)

    assert result.status == ResultStatus.OK
    assert result.attempts == 2
    assert 5 in sleeps


def test_network_error_gives_up_after_retries(config, throttle, sleeps, request_):
    responder = Responder(httpx.ConnectError("boom"))
    result = run(config, throttle, sleeps, responder, request_)

    assert result.status == ResultStatus.NETWORK
    assert responder.calls == 4  # first try + 3 retries
    assert result.offers == []


def test_block_starts_cooldown_and_is_not_retried(config, throttle, sleeps, request_):
    responder = Responder(httpx.Response(429, text="slow down"))
    result = run(config, throttle, sleeps, responder, request_)

    assert result.status == ResultStatus.BLOCKED
    assert responder.calls == 1
    assert throttle.cooldown_until("fake") is not None

    again = run(config, throttle, sleeps, Responder(ok({"offers": [{}]})), request_)
    assert again.status == ResultStatus.BLOCKED
    assert "cooling down" in again.error


def test_captcha_page_counts_as_block(config, throttle, sleeps, request_):
    page = httpx.Response(
        200, text="<html>Вы не робот? SmartCaptcha</html>", headers={"content-type": "text/html"}
    )
    result = run(config, throttle, sleeps, Responder(page), request_)
    assert result.status == ResultStatus.BLOCKED


def test_html_instead_of_json_is_layout_change(config, throttle, sleeps, request_):
    page = httpx.Response(200, text="<html>new site</html>", headers={"content-type": "text/html"})
    result = run(config, throttle, sleeps, Responder(page), request_)
    assert result.status == ResultStatus.LAYOUT_CHANGED


def test_explicit_empty_answer(config, throttle, sleeps, request_):
    result = run(config, throttle, sleeps, Responder(ok({"empty": True})), request_)
    assert result.status == ResultStatus.EMPTY
    assert result.is_success


def test_silence_without_empty_marker_is_suspicious(config, throttle, sleeps, request_):
    result = run(config, throttle, sleeps, Responder(ok({"offers": []})), request_)
    assert result.status == ResultStatus.LAYOUT_CHANGED


def test_mostly_invalid_rows_fail_the_task(config, throttle, sleeps, request_):
    rows = [{"price": "5"}, {"price": "7"}, {"price": "4000"}]
    result = run(config, throttle, sleeps, Responder(ok({"offers": rows})), request_)
    assert result.status == ResultStatus.INVALID
    assert sum(w.code == "dropped_row" for w in result.warnings) == 2


def test_single_bad_row_is_dropped_with_warning(config, throttle, sleeps, request_):
    rows = [{"price": "4000"}, {"price": "4100"}, {"price": "4200"}, {"price": "5"}]
    result = run(config, throttle, sleeps, Responder(ok({"offers": rows})), request_)
    assert result.status == ResultStatus.OK
    assert len(result.offers) == 3


def test_adapter_bug_becomes_error_status(config, throttle, sleeps, request_):
    result = run(config, throttle, sleeps, Responder(ok({"crash": True})), request_)
    assert result.status == ResultStatus.ERROR
    assert "adapter bug" in result.error


@pytest.mark.parametrize(
    ("priority", "expected"), [(False, ResultStatus.SKIPPED), (True, ResultStatus.OK)]
)
def test_background_runs_leave_budget_for_people(
    config, throttle, sleeps, request_, priority, expected
):
    from scraper.throttle import SourceLimits

    throttle.configure("fake", SourceLimits(min_interval=0, daily_budget=10, jitter=0))
    for _ in range(8):
        asyncio.run(throttle.acquire("fake"))
    result = run(
        config, throttle, sleeps, Responder(ok({"offers": [{}]})), request_, priority=priority
    )
    assert result.status == expected
