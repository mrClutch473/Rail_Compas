"""Turn snapshots into what the results page shows: trains, classes, a price per seller."""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
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
