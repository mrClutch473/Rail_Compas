"""How each seller names a city: looked up through their own search suggestions.

Synchronous on purpose: used from the admin, one city at a time."""

import uuid

import httpx

from .config import ScraperConfig

TIMEOUT = 20


def _client() -> httpx.Client:
    config = ScraperConfig()
    return httpx.Client(
        timeout=TIMEOUT,
        headers={"User-Agent": config.user_agent, "Accept": "application/json"},
        follow_redirects=True,
    )


def tutu_code(name: str) -> str | None:
    with _client() as client:
        items = client.get(
            "https://www.tutu.ru/suggest/railway_simple/", params={"name": name}
        ).json()
    return str(items[0]["id"]) if items else None


def tutu_slugs(from_code: str, to_code: str) -> tuple[str, str] | None:
    with _client() as client:
        data = client.get(
            "https://train-gateway.tutu.ru/api/seo/schedule/link",
            params={"departure_rzd_code": from_code, "arrival_rzd_code": to_code},
        ).json()
    parts = [p for p in (data.get("url") or "").split("/") if p]
    return (parts[1], parts[2]) if len(parts) == 3 else None


def onetwotrip_station(name: str) -> tuple[str, str] | None:
    with _client() as client:
        data = client.get(
            "https://www.onetwotrip.com/_railways/rzd/suggestStations",
            params={"searchText": name},
        ).json()
    items = data.get("result") or []
    return (str(items[0]["id"]), items[0]["trans"]) if items else None


def aviakassa_code(name: str) -> str | None:
    params = {
        "prefix": name,
        "channel_token": "",
        "lang": "ru",
        "uuid": str(uuid.uuid4()),
        "partner": "AKV4",
    }
    with _client() as client:
        data = client.get(
            "https://www.aviakassa.com/api/v4/railway/search/get-autocomplete-list",
            params=params,
        ).json()
    items = (data.get("data") or {}).get("items") or []
    return str(items[0]["parent_code"]) if items else None
