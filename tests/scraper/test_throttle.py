import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from scraper.errors import BlockedError, BudgetExhausted
from scraper.throttle import SourceLimits, Throttle


class Clock:
    def __init__(self):
        self.t = 1000.0
        self.slept = []

    def __call__(self):
        return self.t

    async def sleep(self, seconds):
        self.slept.append(round(seconds, 3))
        self.t += seconds


def make(clock, state_file=None, now=None, **limits):
    throttle = Throttle(
        state_file, clock=clock, sleep=clock.sleep, now=now or (lambda: datetime.now(UTC))
    )
    throttle.configure(
        "s", SourceLimits(**{"min_interval": 5, "daily_budget": 100, "jitter": 0, **limits})
    )
    return throttle


def test_pause_between_requests():
    clock = Clock()
    throttle = make(clock)
    asyncio.run(throttle.acquire("s"))
    clock.t += 2
    asyncio.run(throttle.acquire("s"))
    assert clock.slept == [3.0]


def test_no_pause_when_enough_time_passed():
    clock = Clock()
    throttle = make(clock)
    asyncio.run(throttle.acquire("s"))
    clock.t += 10
    asyncio.run(throttle.acquire("s"))
    assert clock.slept == []


def test_budget_resets_next_day():
    clock = Clock()
    today = [datetime(2026, 12, 1, 10, tzinfo=UTC)]
    throttle = make(clock, now=lambda: today[0], min_interval=0, daily_budget=2)
    asyncio.run(throttle.acquire("s", priority=True))
    asyncio.run(throttle.acquire("s", priority=True))
    with pytest.raises(BudgetExhausted):
        asyncio.run(throttle.acquire("s", priority=True))
    today[0] += timedelta(days=1)
    asyncio.run(throttle.acquire("s", priority=True))


def test_cooldown_blocks_until_it_expires():
    clock = Clock()
    moment = [datetime(2026, 12, 1, 10, tzinfo=UTC)]
    throttle = make(clock, now=lambda: moment[0])
    throttle.start_cooldown("s", minutes=60)
    with pytest.raises(BlockedError):
        asyncio.run(throttle.acquire("s"))
    moment[0] += timedelta(minutes=61)
    asyncio.run(throttle.acquire("s"))


def test_state_survives_restart(tmp_path):
    clock = Clock()
    state = tmp_path / "state.json"
    first = make(clock, state_file=state, min_interval=0, daily_budget=10)
    first.start_cooldown("s", minutes=30)
    second = make(clock, state_file=state, min_interval=0, daily_budget=10)
    assert second.cooldown_until("s") is not None
