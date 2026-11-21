import json
from dataclasses import dataclass, field
from typing import Any

import httpx

from .config import ScraperConfig
from .errors import BlockedError, FetchTimeout, LayoutChanged, NetworkError

BLOCK_MARKERS = (
    "captcha",
    "smartcaptcha",
    "похожи на автоматические",
    "вы не робот",
    "access denied",
)


@dataclass
class FetchContext:
    """What an adapter gets to talk to its source: a ready HTTP client and a place to keep
    every response it received, so the run can be stored and replayed later."""

    http: httpx.AsyncClient
    payloads: list[dict[str, Any]] = field(default_factory=list)
    proxy_used: bool = False

    async def get_json(self, url: str, **kwargs) -> Any:
        return await self._json("GET", url, **kwargs)

    async def post_json(self, url: str, body: Any, **kwargs) -> Any:
        return await self._json("POST", url, json=body, **kwargs)

    async def _json(self, method: str, url: str, **kwargs) -> Any:
        try:
            response = await self.http.request(method, url, **kwargs)
        except httpx.TimeoutException as exc:
            raise FetchTimeout(f"{method} {url}: timeout") from exc
        except httpx.TransportError as exc:
            raise NetworkError(f"{method} {url}: {exc.__class__.__name__}") from exc

        text = response.text
        record = {"method": method, "url": str(response.url), "status": response.status_code}
        if kwargs.get("json") is not None:
            record["request"] = kwargs["json"]

        if response.status_code in (403, 429) or _looks_blocked(response, text):
            record["body"] = text[:2000]
            self.payloads.append(record)
            raise BlockedError(f"{method} {url}: HTTP {response.status_code}, looks like a block")
        if response.status_code >= 500:
            raise NetworkError(f"{method} {url}: HTTP {response.status_code}")
        if response.status_code >= 400:
            raise LayoutChanged(f"{method} {url}: HTTP {response.status_code}")

        try:
            data = json.loads(text)
        except ValueError as exc:
            record["body"] = text[:2000]
            self.payloads.append(record)
            raise LayoutChanged(f"{method} {url}: response is not JSON") from exc
        record["body"] = data
        self.payloads.append(record)
        return data


def _looks_blocked(response: httpx.Response, text: str) -> bool:
    if "html" not in response.headers.get("content-type", ""):
        return False
    lowered = text[:20000].lower()
    return any(marker in lowered for marker in BLOCK_MARKERS)


def make_client(config: ScraperConfig, proxy: str | None = None, **headers) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        proxy=proxy,
        timeout=httpx.Timeout(config.request_timeout, connect=10),
        follow_redirects=True,
        headers={
            "User-Agent": config.user_agent,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "ru-RU,ru;q=0.9",
            **headers,
        },
    )
