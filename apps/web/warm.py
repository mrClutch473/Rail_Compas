from django.db import close_old_connections

from apps.analytics.services import refresh_reports
from apps.core.cache import cached, refresh

from .presenters import popular_routes

POPULAR_TTL = 900


def popular() -> list:
    return cached("popular", build=popular_routes, ttl=POPULAR_TTL)


def warm_caches() -> None:
    """The heavy blocks of the home and analytics pages, rebuilt in the background."""
    try:
        refresh("popular", build=popular_routes, ttl=POPULAR_TTL)
        refresh_reports()
    finally:
        close_old_connections()
