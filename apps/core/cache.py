"""Shared cache for page data. Pages put what their data depends on into the key
(for prices: when each seller was last synced), so new prices are seen at once."""

from collections.abc import Callable

from django.core.cache import cache


def cached[T](name: str, *parts, build: Callable[[], T], ttl: int = 600) -> T:
    key = ":".join(str(p) for p in (name, *parts))
    value = cache.get(key)
    if value is None:
        value = build()
        cache.set(key, value, ttl)
    return value
