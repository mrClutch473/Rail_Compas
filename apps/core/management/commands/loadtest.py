"""Load on our own site (13.8): N users walk pages that already have data, nothing is synced.

python manage.py loadtest --base http://127.0.0.1:8000 --users 50 --seconds 60"""

import asyncio
import logging
import math
import random
import statistics
import time
from collections import defaultdict

import httpx
from django.core.management.base import BaseCommand
from django.urls import reverse
from django.utils import timezone

from apps.catalog.models import Route
from apps.offers.models import PriceSnapshot


def collect_urls(limit: int = 40) -> list[str]:
    days = (
        PriceSnapshot.objects.filter(trip__departure_date__gte=timezone.localdate())
        .values_list(
            "trip__route__origin__slug",
            "trip__route__destination__slug",
            "trip__departure_date",
        )
        .distinct()[:limit]
    )
    trips = (
        PriceSnapshot.objects.filter(trip__departure_date__gte=timezone.localdate())
        .values_list("trip_id", flat=True)
        .distinct()[:limit]
    )
    urls = [reverse("web:home"), reverse("web:analytics"), reverse("web:about")]
    urls += [reverse("web:results", args=[o, d, day.isoformat()]) for o, d, day in days]
    urls += [reverse("web:trip", args=[pk]) for pk in trips]
    urls += [
        reverse("web:calendar", args=[r.origin.slug, r.destination.slug])
        for r in Route.objects.select_related("origin", "destination")[:10]
    ]
    return urls


def page_kind(url: str) -> str:
    parts = [p for p in url.split("/") if p]
    if not parts:
        return "home"
    if parts[0] == "r":
        return "results" if len(parts) == 4 else "calendar"
    return parts[0]


def percentile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(p / 100 * len(ordered)) - 1)]


class Command(BaseCommand):
    help = "Measure response times of our pages under N concurrent users."

    def add_arguments(self, parser):
        parser.add_argument("--base", default="http://127.0.0.1:8000")
        parser.add_argument("--users", type=int, default=30)
        parser.add_argument("--seconds", type=int, default=60)
        parser.add_argument("--think", type=float, default=1.0, help="pause between clicks, s")

    def handle(self, *args, **options):
        logging.getLogger("httpx").setLevel(logging.WARNING)
        urls = collect_urls()
        # results pages count searches; give the numbers back afterwards
        counters = list(Route.objects.values_list("pk", "search_count", "last_searched_at"))
        try:
            timings, errors = asyncio.run(self.run_users(urls, options))
        finally:
            for pk, count, searched in counters:
                Route.objects.filter(pk=pk).update(search_count=count, last_searched_at=searched)

        total = len(timings) + errors
        out = self.stdout.write
        out(f"users {options['users']}, {options['seconds']} s, {len(urls)} pages")
        out(f"requests {total}, {total / options['seconds']:.1f}/s, errors {errors}")
        if not timings:
            return
        groups = defaultdict(list)
        for url, seconds in timings:
            groups["all"].append(seconds * 1000)
            groups[page_kind(url)].append(seconds * 1000)
        for kind, ms in sorted(groups.items(), key=lambda item: -len(item[1])):
            out(
                f"  {kind:<10} {len(ms):>5}  median {statistics.median(ms):>5.0f} ms  "
                f"p95 {percentile(ms, 95):>5.0f} ms  max {max(ms):>5.0f} ms"
            )

    async def run_users(self, urls, options):
        timings: list[tuple[str, float]] = []
        errors = 0
        deadline = time.monotonic() + options["seconds"]

        async def user(client):
            nonlocal errors
            while time.monotonic() < deadline:
                url = random.choice(urls)
                started = time.monotonic()
                try:
                    response = await client.get(url)
                    if response.status_code == 200:
                        timings.append((url, time.monotonic() - started))
                    else:
                        errors += 1
                except httpx.HTTPError:
                    errors += 1
                await asyncio.sleep(random.uniform(0, 2 * options["think"]))

        limits = httpx.Limits(max_connections=options["users"])
        async with httpx.AsyncClient(base_url=options["base"], timeout=30, limits=limits) as client:
            await asyncio.gather(*(user(client) for _ in range(options["users"])))
        return timings, errors
