import asyncio
import json
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from .errors import BlockedError, BudgetExhausted

MAX_COOLDOWN_MINUTES = 24 * 60


@dataclass
class SourceLimits:
    min_interval: float = 5.0
    daily_budget: int = 1000
    jitter: float = 0.5


@dataclass
class _SourceState:
    day: str = ""
    used: int = 0
    cooldown_until: str = ""
    blocks: int = 0
    last_request: float = 0.0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)


class Throttle:
    """Per-source politeness: one request at a time, a pause between them, a daily budget
    and a cooldown after the source showed signs of blocking. Budget and cooldown survive
    restarts through a small JSON file."""

    def __init__(
        self,
        state_file: Path | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ):
        self.state_file = state_file
        self.clock, self.sleep, self.now = clock, sleep, now
        self.limits: dict[str, SourceLimits] = {}
        self._state: dict[str, _SourceState] = {}
        self._load()

    def configure(self, source: str, limits: SourceLimits) -> None:
        self.limits[source] = limits

    def _get(self, source: str) -> _SourceState:
        return self._state.setdefault(source, _SourceState())

    def cooldown_until(self, source: str) -> datetime | None:
        raw = self._get(source).cooldown_until
        if not raw:
            return None
        until = datetime.fromisoformat(raw)
        return until if until > self.now() else None

    def start_cooldown(self, source: str, minutes: int) -> timedelta:
        """Each block in a row doubles the pause, up to a day; a success resets it."""
        state = self._get(source)
        state.blocks += 1
        pause = timedelta(minutes=min(minutes * 2 ** (state.blocks - 1), MAX_COOLDOWN_MINUTES))
        state.cooldown_until = (self.now() + pause).isoformat()
        self._save()
        return pause

    def record_success(self, source: str) -> None:
        state = self._get(source)
        if state.blocks:
            state.blocks = 0
            self._save()

    def remaining(self, source: str) -> int:
        state = self._get(source)
        used = state.used if state.day == self._today() else 0
        return max(0, self.limits.get(source, SourceLimits()).daily_budget - used)

    async def acquire(self, source: str, *, priority: bool = False) -> None:
        """Wait for our turn. Raises if the source is cooling down or out of budget;
        `priority` requests (a user pressed the button) may use the budget to the end,
        background ones stop at 80% to leave room for people."""
        limits = self.limits.get(source, SourceLimits())
        state = self._get(source)
        if until := self.cooldown_until(source):
            raise BlockedError(f"{source} is cooling down until {until:%H:%M} UTC")

        today = self._today()
        if state.day != today:
            state.day, state.used = today, 0
        reserve = 0 if priority else int(limits.daily_budget * 0.2)
        if state.used >= limits.daily_budget - reserve:
            raise BudgetExhausted(f"{source} daily budget used: {state.used}")

        await state.lock.acquire()
        try:
            pause = limits.min_interval * (1 + random.uniform(0, limits.jitter))
            wait = state.last_request + pause - self.clock()
            if state.last_request and wait > 0:
                await self.sleep(wait)
            # the request ahead of us in the queue may have been blocked while we waited
            if until := self.cooldown_until(source):
                raise BlockedError(f"{source} is cooling down until {until:%H:%M} UTC")
            state.last_request = self.clock()
            state.used += 1
            self._save()
        finally:
            state.lock.release()

    def _today(self) -> str:
        return date.isoformat(self.now().date())

    def _load(self) -> None:
        if not self.state_file or not self.state_file.exists():
            return
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
        except ValueError:
            return
        for source, item in data.items():
            self._state[source] = _SourceState(
                day=item.get("day", ""),
                used=item.get("used", 0),
                cooldown_until=item.get("cooldown_until", ""),
                blocks=item.get("blocks", 0),
            )

    def _save(self) -> None:
        if not self.state_file:
            return
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        data = {
            source: {
                "day": s.day,
                "used": s.used,
                "cooldown_until": s.cooldown_until,
                "blocks": s.blocks,
            }
            for source, s in self._state.items()
        }
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        tmp.replace(self.state_file)
