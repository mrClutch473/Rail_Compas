import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from .adapters import SourceAdapter
from .config import ScraperConfig
from .enums import ResultStatus
from .errors import BlockedError, ScrapeError
from .schemas import Notice, ParseResult, RawCapture, ScrapeRequest, ScrapeResult
from .storage import save_raw
from .throttle import Throttle
from .transport import FetchContext, make_client
from .validate import validate_offers

log = logging.getLogger("scraper")

# Pause before each retry, by error type; anything not listed is not retried.
RETRY_DELAYS = {
    "NetworkError": (5, 15, 45),
    "FetchTimeout": (5,),
}


class Runner:
    """Turns a ScrapeRequest into a ScrapeResult: waits its turn, fetches with retries,
    stores the raw answer, parses, validates. Never raises for source problems — they end
    up as the result status, so one broken seller does not break the others."""

    def __init__(
        self,
        config: ScraperConfig,
        throttle: Throttle,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        save_raw_files: bool = True,
        client_factory=make_client,
    ):
        self.config = config
        self.throttle = throttle
        self.sleep = sleep
        self.save_raw_files = save_raw_files
        self.client_factory = client_factory

    async def run(
        self,
        adapter: SourceAdapter,
        request: ScrapeRequest,
        *,
        priority: bool = False,
        use_proxy: bool = False,
    ) -> ScrapeResult:
        started = datetime.now(UTC)
        clock = time.monotonic()
        proxy = random.choice(self.config.proxies) if use_proxy and self.config.proxies else None
        result = {"strategy": "", "raw_ref": "", "attempts": 0, "proxy_used": bool(proxy)}

        try:
            parsed = await asyncio.wait_for(
                self._fetch_and_parse(adapter, request, proxy, priority, result),
                timeout=self.config.task_timeout,
            )
            status, warnings, error = self._judge(parsed, request)
            offers, references = parsed.offers, parsed.reference_offers
            self.throttle.record_success(request.source)
        except TimeoutError:
            status, warnings, error, offers, references = (
                ResultStatus.TIMEOUT,
                [],
                "task timed out",
                [],
                [],
            )
        except ScrapeError as exc:
            if isinstance(exc, BlockedError) and "cooling down" not in str(exc):
                self.throttle.start_cooldown(request.source, self.config.cooldown_minutes)
            status, warnings, error, offers, references = exc.status, [], str(exc), [], []
        except Exception as exc:  # a bug in an adapter must not kill the worker
            log.exception("adapter %s crashed", request.source)
            status, warnings, error, offers, references = (
                ResultStatus.ERROR,
                [],
                f"{exc.__class__.__name__}: {exc}",
                [],
                [],
            )

        if status not in (ResultStatus.OK, ResultStatus.EMPTY):
            offers, references = [], []

        finished = datetime.now(UTC)
        outcome = ScrapeResult(
            source=request.source,
            request=request,
            status=status,
            offers=offers,
            reference_offers=references,
            warnings=warnings,
            error=error,
            started_at=started,
            finished_at=finished,
            duration_ms=int((time.monotonic() - clock) * 1000),
            **result,
        )
        log.info(
            "%-10s %s %s %-8s offers=%d %dms%s",
            request.source,
            request.route_slug,
            request.travel_date,
            outcome.status,
            len(outcome.offers),
            outcome.duration_ms,
            f" error={error}" if error else "",
        )
        return outcome

    async def _fetch_and_parse(
        self,
        adapter: SourceAdapter,
        request: ScrapeRequest,
        proxy: str | None,
        priority: bool,
        result: dict,
    ) -> ParseResult:
        capture = await self._fetch_with_retries(adapter, request, proxy, priority, result)
        result["strategy"] = capture.strategy
        if self.save_raw_files:
            result["raw_ref"] = save_raw(self.config.raw_dir, request, capture, datetime.now(UTC))
        return adapter.parse(capture, request)

    async def _fetch_with_retries(
        self,
        adapter: SourceAdapter,
        request: ScrapeRequest,
        proxy: str | None,
        priority: bool,
        result: dict,
    ) -> RawCapture:
        async with self.client_factory(self.config, proxy, **adapter.headers) as client:
            attempt = 0
            while True:
                attempt += 1
                result["attempts"] = attempt
                await self.throttle.acquire(request.source, priority=priority)
                ctx = FetchContext(http=client, proxy_used=bool(proxy))
                try:
                    capture = await adapter.fetch(ctx, request)
                    capture.payloads = capture.payloads or ctx.payloads
                    return capture
                except ScrapeError as exc:
                    delays = RETRY_DELAYS.get(exc.__class__.__name__, ())
                    if not exc.retryable or attempt > len(delays):
                        raise
                    log.warning("%s attempt %d failed: %s", request.source, attempt, exc)
                    await self.sleep(delays[attempt - 1])

    def _judge(self, parsed: ParseResult, request: ScrapeRequest):
        offers, notices, too_many_dropped = validate_offers(parsed.offers, request)
        references, ref_notices, _ = validate_offers(parsed.reference_offers, request)
        parsed.offers, parsed.reference_offers = offers, references
        warnings = [*parsed.warnings, *notices, *ref_notices]

        if too_many_dropped:
            return ResultStatus.INVALID, warnings, "too many rows failed validation"
        if not offers:
            if parsed.is_empty:
                return ResultStatus.EMPTY, warnings, ""
            if notices:
                return ResultStatus.INVALID, warnings, "no row passed validation"
            warnings.append(Notice(code="no_offers", detail="source said nothing about emptiness"))
            return ResultStatus.LAYOUT_CHANGED, warnings, "no offers and no 'empty' marker"
        return ResultStatus.OK, warnings, ""
