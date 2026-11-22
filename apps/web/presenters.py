"""Turn snapshots into what the results page shows: trains, classes, a price per seller."""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Self
from zoneinfo import ZoneInfo

from django.db.models import Min
from django.utils import timezone

from apps.catalog.models import Route, Source
from apps.core.enums import CarClass
from apps.offers.models import PriceSnapshot, Trip
from apps.offers.selectors import SourceState, current_snapshots

CLASS_ORDER = [CarClass.SEATED, CarClass.PLATZKART, CarClass.COUPE, CarClass.SV, CarClass.LUX]
SALE_DAYS = 89


@dataclass
class Cell:
    source: Source
    snapshot: PriceSnapshot | None
    is_best: bool = False


@dataclass
class ClassRow:
    car_class: str
    label: str
    cells: list[Cell]
    reference: PriceSnapshot | None
    best: Decimal | None = None
    spread: Decimal | None = None
    spread_pct: float | None = None
    seats: int | None = None

    @property
    def offered(self) -> int:
        return sum(c.snapshot is not None for c in self.cells)

    @property
    def markup_pct(self) -> float | None:
        """How much the cheapest seller adds on top of the carrier tariff."""
        if self.best is None or self.reference is None or not self.reference.price:
            return None
        return float((self.best - self.reference.price) / self.reference.price * 100)


@dataclass
class TrainRow:
    trip: Trip
    departure: datetime
    arrival: datetime
    classes: list[ClassRow] = field(default_factory=list)

    @property
    def best(self) -> Decimal | None:
        prices = [c.best for c in self.classes if c.best is not None]
        return min(prices) if prices else None

    @property
    def max_spread(self) -> Decimal | None:
        spreads = [c.spread for c in self.classes if c.spread]
        return max(spreads) if spreads else None

    @property
    def has_reference(self) -> bool:
        return any(c.reference for c in self.classes)

    @property
    def days(self) -> int:
        return (self.arrival.date() - self.departure.date()).days


def build_trains(route: Route, travel_date: date, sellers: list[Source]) -> list[TrainRow]:
    origin_tz = ZoneInfo(route.origin.timezone)
    destination_tz = ZoneInfo(route.destination.timezone)
    grouped: dict[int, dict] = {}

    for snap in current_snapshots(route, travel_date):
        item = grouped.setdefault(snap.trip_id, {"trip": snap.trip, "prices": {}, "reference": {}})
        if snap.source.is_reference:
            item["reference"][snap.car_class] = snap
        else:
            item["prices"].setdefault(snap.car_class, {})[snap.source_id] = snap

    trains = []
    for item in grouped.values():
        trip = item["trip"]
        # naive local times: templates would otherwise convert them back to Moscow time
        row = TrainRow(
            trip=trip,
            departure=trip.departure_at.astimezone(origin_tz).replace(tzinfo=None),
            arrival=trip.arrival_at.astimezone(destination_tz).replace(tzinfo=None),
        )
        for car_class in CLASS_ORDER:
            prices = item["prices"].get(car_class.value)
            if not prices:
                continue
            cells = [Cell(source, prices.get(source.pk)) for source in sellers]
            values = [c.snapshot.price for c in cells if c.snapshot]
            best, worst = min(values), max(values)
            for cell in cells:
                cell.is_best = (
                    bool(cell.snapshot) and cell.snapshot.price == best and len(values) > 1
                )
            spread = worst - best if len(values) > 1 else None
            row.classes.append(
                ClassRow(
                    car_class=car_class.value,
                    label=car_class.label,
                    cells=cells,
                    reference=item["reference"].get(car_class.value),
                    best=best,
                    spread=spread,
                    spread_pct=float(spread / worst * 100) if spread else None,
                    seats=max((c.snapshot.seats or 0 for c in cells if c.snapshot), default=None),
                )
            )
        if row.classes:
            trains.append(row)
    return sorted(trains, key=lambda t: t.departure)


DEPARTURE_WINDOWS = {
    "night": (0, 6, "ночью"),
    "morning": (6, 12, "утром"),
    "day": (12, 18, "днём"),
    "evening": (18, 24, "вечером"),
}
SORTS = {
    "price": "по цене",
    "departure": "по отправлению",
    "duration": "по времени в пути",
    "savings": "по выгоде",
}


@dataclass
class Filters:
    car_class: str = ""
    departure: str = ""
    max_hours: int | None = None
    sort: str = "departure"

    @classmethod
    def from_query(cls, query) -> Self:
        car_class = query.get("class", "")
        departure = query.get("dep", "")
        try:
            max_hours = int(query.get("hours", "")) or None
        except ValueError:
            max_hours = None
        sort = query.get("sort", "departure")
        return cls(
            car_class=car_class if car_class in CarClass.values else "",
            departure=departure if departure in DEPARTURE_WINDOWS else "",
            max_hours=max_hours if max_hours and 0 < max_hours <= 96 else None,
            sort=sort if sort in SORTS else "departure",
        )

    @property
    def active(self) -> bool:
        return bool(self.car_class or self.departure or self.max_hours)


def apply_filters(trains: list[TrainRow], filters: Filters) -> list[TrainRow]:
    result = []
    for train in trains:
        if filters.departure:
            start, end, _ = DEPARTURE_WINDOWS[filters.departure]
            if not start <= train.departure.hour < end:
                continue
        if filters.max_hours and train.trip.duration_min > filters.max_hours * 60:
            continue
        if filters.car_class:
            rows = [r for r in train.classes if r.car_class == filters.car_class]
            if not rows:
                continue
            train = TrainRow(train.trip, train.departure, train.arrival, rows)
        result.append(train)

    keys = {
        "price": lambda t: (t.best is None, t.best or 0),
        "departure": lambda t: t.departure,
        "duration": lambda t: t.trip.duration_min,
        "savings": lambda t: -(t.max_spread or 0),
    }
    return sorted(result, key=keys[filters.sort])


@dataclass
class DayPrice:
    day: date
    price: Decimal | None
    is_current: bool


def date_strip(route: Route, travel_date: date, radius: int = 3) -> list[DayPrice]:
    today = timezone.localdate()
    start = max(today, travel_date - timedelta(days=radius))
    days = [start + timedelta(days=i) for i in range(radius * 2 + 1)]
    days = [d for d in days if d <= today + timedelta(days=SALE_DAYS)]
    fresh = timezone.now() - timedelta(hours=48)
    prices = dict(
        PriceSnapshot.objects.filter(
            trip__route=route,
            trip__departure_date__in=days,
            source__is_reference=False,
            collected_at__gte=fresh,
        )
        .values_list("trip__departure_date")
        .annotate(low=Min("price"))
    )
    return [DayPrice(d, prices.get(d), d == travel_date) for d in days]


@dataclass
class PopularRoute:
    route: Route
    price: Decimal | None
    day: date | None


def popular_routes(limit: int = 9) -> list[PopularRoute]:
    routes = list(
        Route.objects.filter(is_scheduled=True)
        .select_related("origin", "destination")
        .order_by("-search_count", "destination__name")[:limit]
    )
    fresh = timezone.now() - timedelta(hours=48)
    rows = (
        PriceSnapshot.objects.filter(
            trip__route__in=routes,
            trip__departure_date__gte=timezone.localdate(),
            source__is_reference=False,
            collected_at__gte=fresh,
        )
        .values("trip__route_id")
        .annotate(low=Min("price"))
    )
    lows = {row["trip__route_id"]: row["low"] for row in rows}
    days = {}
    for route_id, low in lows.items():
        days[route_id] = (
            PriceSnapshot.objects.filter(
                trip__route_id=route_id, price=low, collected_at__gte=fresh
            )
            .values_list("trip__departure_date", flat=True)
            .first()
        )
    items = [PopularRoute(r, lows.get(r.pk), days.get(r.pk)) for r in routes]
    return sorted(items, key=lambda item: item.price is None)


def freshest(states: list[SourceState]) -> datetime | None:
    moments = [s.synced_at for s in states if s.synced_at]
    return max(moments) if moments else None


@dataclass
class ClassHistory:
    car_class: str
    label: str
    series: list[dict]
    rows: list[dict]
    low: Decimal | None
    high: Decimal | None
    first_best: Decimal | None
    last_best: Decimal | None
    points: int

    @property
    def change(self) -> Decimal | None:
        if self.first_best is None or self.last_best is None:
            return None
        return self.last_best - self.first_best


def trip_history(trip: Trip, sellers: list[Source]) -> list[ClassHistory]:
    """Price history per class: one line per seller plus the carrier tariff."""
    slots = {s.pk: i + 1 for i, s in enumerate(sellers)}
    snapshots = (
        PriceSnapshot.objects.filter(trip=trip)
        .select_related("source", "task")
        .order_by("collected_at")
    )
    by_class: dict[str, list[PriceSnapshot]] = {}
    for snap in snapshots:
        by_class.setdefault(snap.car_class, []).append(snap)

    result = []
    for car_class in CLASS_ORDER:
        snaps = by_class.get(car_class.value)
        if not snaps:
            continue
        lines: dict[int, dict] = {}
        for snap in snaps:
            source = snap.source
            line = lines.setdefault(
                source.pk,
                {
                    "name": source.name,
                    "token": "--series-ref"
                    if source.is_reference
                    else f"--series-{slots.get(source.pk, 0)}",
                    "reference": source.is_reference,
                    "order": 99 if source.is_reference else slots.get(source.pk, 50),
                    "points": [],
                },
            )
            line["points"].append([int(snap.collected_at.timestamp() * 1000), float(snap.price)])

        seller_snaps = [s for s in snaps if not s.source.is_reference]
        batches: dict[int, list[Decimal]] = {}
        for snap in seller_snaps:
            batches.setdefault(snap.task.job_id, []).append(snap.price)
        bests = [min(prices) for _, prices in sorted(batches.items())]
        result.append(
            ClassHistory(
                car_class=car_class.value,
                label=car_class.label,
                series=sorted(lines.values(), key=lambda line: line["order"]),
                rows=[
                    {"at": s.collected_at, "source": s.source, "price": s.price}
                    for s in reversed(snaps)
                ][:60],
                low=min((s.price for s in seller_snaps), default=None),
                high=max((s.price for s in seller_snaps), default=None),
                first_best=bests[0] if bests else None,
                last_best=bests[-1] if bests else None,
                points=len(batches),
            )
        )
    return result


@dataclass
class CalendarDay:
    day: date
    price: Decimal | None = None
    level: int = 0
    cheapest: bool = False
    in_range: bool = True


@dataclass
class CalendarMonth:
    title: date
    weeks: list[list[CalendarDay | None]]


def price_calendar(route: Route, days: int = 45) -> tuple[list[CalendarMonth], Decimal | None]:
    """Cheapest current price per day, binned into 5 shades for a heat calendar."""
    today = timezone.localdate()
    last = today + timedelta(days=min(days, SALE_DAYS) - 1)
    fresh = timezone.now() - timedelta(hours=48)
    lows = dict(
        PriceSnapshot.objects.filter(
            trip__route=route,
            trip__departure_date__range=(today, last),
            source__is_reference=False,
            collected_at__gte=fresh,
        )
        .values_list("trip__departure_date")
        .annotate(low=Min("price"))
    )
    prices = sorted(lows.values())
    cheapest = prices[0] if prices else None

    def level(price: Decimal | None) -> int:
        if price is None or len(prices) < 2 or prices[0] == prices[-1]:
            return 0 if price is None else 1
        share = (price - prices[0]) / (prices[-1] - prices[0])
        return 1 + min(4, int(share * 5))

    months: list[CalendarMonth] = []
    cursor = today.replace(day=1)
    while cursor <= last:
        next_month = (cursor + timedelta(days=32)).replace(day=1)
        weeks, week = [], [None] * cursor.weekday()
        day = cursor
        while day < next_month:
            price = lows.get(day)
            week.append(
                CalendarDay(
                    day=day,
                    price=price,
                    level=level(price),
                    cheapest=price is not None and price == cheapest,
                    in_range=today <= day <= last,
                )
            )
            if len(week) == 7:
                weeks.append(week)
                week = []
            day += timedelta(days=1)
        if week:
            weeks.append(week + [None] * (7 - len(week)))
        months.append(CalendarMonth(title=cursor, weeks=weeks))
        cursor = next_month
    return months, cheapest
