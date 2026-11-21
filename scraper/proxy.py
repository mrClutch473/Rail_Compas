import os
from urllib.parse import unquote, urlsplit


def parse_proxy(url: str) -> dict[str, str]:
    """Turn `scheme://user:pass@host:port` into Playwright's proxy settings."""
    parts = urlsplit(url if "://" in url else f"http://{url}")
    if not parts.hostname or not parts.port:
        raise ValueError(f"proxy must look like scheme://[user:pass@]host:port, got {url!r}")

    proxy = {"server": f"{parts.scheme}://{parts.hostname}:{parts.port}"}
    if parts.username:
        proxy["username"] = unquote(parts.username)
        proxy["password"] = unquote(parts.password or "")
    return proxy


def proxies_from_env() -> list[str]:
    raw = os.getenv("SCRAPER_PROXIES", "")
    return [p.strip() for p in raw.replace("\n", ",").split(",") if p.strip()]


def mask(url: str) -> str:
    parts = urlsplit(url if "://" in url else f"http://{url}")
    return f"{parts.scheme}://{parts.hostname}:{parts.port}"
