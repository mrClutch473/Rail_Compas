"""Shared cache for page data. Pages put what their data depends on into the key
(for prices: when each seller was last synced), so new prices are seen at once."""

from collections.abc import Callable

from django.core.cache import cache


def _key(name: str, parts) -> str:
    return ":".join(str(p) for p in (name, *parts))


def cached[T](name: str, *parts, build: Callable[[], T], ttl: int = 600) -> T:
    value = cache.get(_key(name, parts))
    if value is None:
        value = refresh(name, *parts, build=build, ttl=ttl)
    return value


def refresh[T](name: str, *parts, build: Callable[[], T], ttl: int = 600) -> T:
    value = build()
    cache.set(_key(name, parts), value, ttl)
    return value
