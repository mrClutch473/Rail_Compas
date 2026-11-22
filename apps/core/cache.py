"""Page data cache that follows new prices: every ingested task bumps the data version,
so cached pieces are rebuilt right after a sync and otherwise live for a while."""

from collections.abc import Callable

from django.core.cache import cache

VERSION_KEY = "data-version"


def data_version() -> int:
    return cache.get_or_set(VERSION_KEY, 1, None)


def bump_data_version() -> None:
    try:
        cache.incr(VERSION_KEY)
    except ValueError:
        cache.set(VERSION_KEY, 2, None)


def cached[T](name: str, *parts, build: Callable[[], T], ttl: int = 600) -> T:
    key = ":".join(str(p) for p in (name, data_version(), *parts))
    value = cache.get(key)
    if value is None:
        value = build()
        cache.set(key, value, ttl)
    return value
