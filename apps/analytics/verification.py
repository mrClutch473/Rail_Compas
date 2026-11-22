"""Price check against the sellers' own sites (13.4).

A sheet lists what we show for a few routes and dates; the author opens each link, writes down
what the seller shows, and `score` counts how often we agree."""

import csv
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from django.db.models import Count, Q
from django.utils import timezone

from apps.catalog.models import Route
from apps.offers.selectors import current_snapshots

COLUMNS = [
    "route",
    "date",
    "train",
    "departure",
    "class",
    "seller",
    "our_price",
    "collected_at",
    "url",
    "site_price",
    "site_departure",
    "note",
]
PRICE_TOLERANCE = Decimal("1")
NOT_ON_SITE = "нет на сайте"


def pick_routes(limit: int) -> list[Route]:
    today = timezone.localdate()
    return list(
        Route.objects.filter(is_scheduled=True)
        .select_related("origin", "destination")
        .annotate(ahead=Count("trips", filter=Q(trips__departure_date__gte=today)))
        .order_by("-ahead", "destination__name")[:limit]
    )


def spread(items: list, n: int) -> list:
    """n items evenly across the list: the first, the last and some in between."""
    if len(items) <= n:
        return items
    if n == 1:
        return items[:1]
    step = (len(items) - 1) / (n - 1)
    return [items[round(i * step)] for i in range(n)]


def sheet_rows(routes: list[Route], offsets: list[int], trains: int) -> list[dict]:
    today = timezone.localdate()
    rows = []
    for route in routes:
        tz = ZoneInfo(route.origin.timezone)
        for offset in offsets:
            day = today + timedelta(days=offset)
            snapshots = current_snapshots(route, day)
            trips = sorted({s.trip for s in snapshots}, key=lambda t: t.departure_at)
            chosen = {t.pk for t in spread(trips, trains)}
            for s in snapshots:
                if s.trip_id not in chosen:
                    continue
                rows.append(
                    {
                        "route": str(route),
                        "date": day.isoformat(),
                        "train": s.trip.train_number,
                        "departure": s.trip.departure_at.astimezone(tz).strftime("%H:%M"),
                        "class": s.get_car_class_display(),
                        "seller": s.source.name,
                        "our_price": f"{s.price:.2f}",
                        "collected_at": timezone.localtime(s.collected_at).strftime("%d.%m %H:%M"),
                        "url": s.url or s.source.base_url,
                        "site_price": "",
                        "site_departure": "",
                        "note": "",
                    }
                )
    return rows


def write_sheet(rows: list[dict], stream) -> None:
    # semicolons and a BOM so Excel with Russian settings opens it as a table
    stream.write("﻿")
    writer = csv.DictWriter(stream, fieldnames=COLUMNS, delimiter=";")
    writer.writeheader()
    writer.writerows(rows)


def read_sheet(stream) -> list[dict]:
    text = stream.read().lstrip("﻿")
    delimiter = ";" if text.split("\n", 1)[0].count(";") else ","
    return list(csv.DictReader(text.splitlines(), delimiter=delimiter))


def to_decimal(value: str) -> Decimal | None:
    cleaned = (value or "").replace(" ", "").replace(" ", "").replace("₽", "")
    try:
        return Decimal(cleaned.replace(",", "."))
    except InvalidOperation:
        return None


@dataclass
class SellerScore:
    checked: int = 0
    price_ok: int = 0
    time_ok: int = 0
    both_ok: int = 0

    @property
    def rate(self) -> float:
        return self.both_ok / self.checked * 100 if self.checked else 0.0


@dataclass
class Score:
    by_seller: dict[str, SellerScore] = field(default_factory=lambda: defaultdict(SellerScore))
    mismatches: list[dict] = field(default_factory=list)
    unchecked: int = 0

    @property
    def total(self) -> SellerScore:
        total = SellerScore()
        for s in self.by_seller.values():
            total.checked += s.checked
            total.price_ok += s.price_ok
            total.time_ok += s.time_ok
            total.both_ok += s.both_ok
        return total


def score(rows: list[dict]) -> Score:
    result = Score()
    for row in rows:
        site_price = to_decimal(row.get("site_price", ""))
        missing = NOT_ON_SITE in (row.get("note") or "").lower()
        if site_price is None and not missing:
            result.unchecked += 1
            continue
        seller = result.by_seller[row["seller"]]
        seller.checked += 1
        price_ok = (
            site_price is not None
            and abs(site_price - to_decimal(row["our_price"])) <= PRICE_TOLERANCE
        )
        site_time = (row.get("site_departure") or "").strip()
        time_ok = not missing and (not site_time or site_time == row["departure"])
        seller.price_ok += price_ok
        seller.time_ok += time_ok
        seller.both_ok += price_ok and time_ok
        if not (price_ok and time_ok):
            result.mismatches.append(row)
    return result


def parse_offsets(raw: str) -> list[int]:
    return [int(x) for x in raw.split(",") if x.strip()]


def travel_dates(offsets: list[int]) -> list[date]:
    today = timezone.localdate()
    return [today + timedelta(days=o) for o in offsets]
