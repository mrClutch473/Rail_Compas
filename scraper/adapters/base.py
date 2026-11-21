from abc import ABC, abstractmethod
from typing import ClassVar

from ..schemas import ParseResult, RawCapture, ScrapeRequest
from ..transport import FetchContext


class SourceAdapter(ABC):
    """One seller. `fetch` is the only place that touches the network; `parse` is a pure
    function of what was fetched, so it can be tested on saved responses and re-run later."""

    code: ClassVar[str]
    headers: ClassVar[dict[str, str]] = {}

    @abstractmethod
    async def fetch(self, ctx: FetchContext, request: ScrapeRequest) -> RawCapture: ...

    @abstractmethod
    def parse(self, capture: RawCapture, request: ScrapeRequest) -> ParseResult: ...

    @abstractmethod
    def offer_url(self, request: ScrapeRequest) -> str: ...
