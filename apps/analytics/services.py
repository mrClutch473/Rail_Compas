"""Numbers for the analytics page: how sellers compare on the very same train and moment.

A comparison is one train, one car class, one sync job: all sellers were asked at the same
time, so their prices are directly comparable."""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal

from django.core.cache import cache
from django.utils import timezone

from apps.catalog.models import Source
from apps.core.enums import CarClass
from apps.offers.models import PriceSnapshot


@dataclass
class SellerStats:
    source: Source
    offers: int = 0
    cheapest: int = 0
    markups: list[float] = field(default_factory=list)
    over_best: list[float] = field(default_factory=list)

    @property
    def cheapest_share(self) -> float:
        return self.cheapest / self.offers * 100 if self.offers else 0.0

    @property
    def avg_markup(self) -> float | None:
        return sum(self.markups) / len(self.markups) if self.markups else None

    @property
    def avg_over_best(self) -> float | None:
        return sum(self.over_best) / len(self.over_best) if self.over_best else None


@dataclass
class ClassStats:
    car_class: str
    label: str
    spreads: list[Decimal] = field(default_factory=list)
    spread_pcts: list[float] = field(default_factory=list)

    @property
    def comparisons(self) -> int:
        return len(self.spreads)

    @property
    def avg_spread(self) -> Decimal | None:
        return sum(self.spreads) / len(self.spreads) if self.spreads else None

    @property
    def avg_spread_pct(self) -> float | None:
        return sum(self.spread_pcts) / len(self.spread_pcts) if self.spread_pcts else None


@dataclass
class Report:
    days: int
    comparisons: int
    trips: int
    snapshots: int
    sellers: list[SellerStats]
    classes: list[ClassStats]
    max_spread: Decimal | None
    max_spread_label: str

    @property
    def top_classes(self) -> list[ClassStats]:
        return sorted(self.classes, key=lambda c: -c.comparisons)[:2]


def build_report(days: int) -> Report:
    since = timezone.now() - timedelta(days=days)
    rows = PriceSnapshot.objects.filter(collected_at__gte=since).values_list(
        "task__job_id",
        "trip_id",
        "car_class",
        "source_id",
        "source__is_reference",
        "price",
        "trip__train_number",
        "trip__route__origin__name",
        "trip__route__destination__name",
    )
    groups: dict[tuple, dict] = defaultdict(lambda: {"prices": {}, "reference": None})
    labels = {}
    total = 0
    for job, trip, car_class, source, is_ref, price, number, origin, destination in rows.iterator():
        total += 1
        group = groups[(job, trip, car_class)]
        if is_ref:
            group["reference"] = price
        else:
            group["prices"][source] = price
        labels[(job, trip, car_class)] = f"{number}, {origin} → {destination}"

    sellers = {
        s.pk: SellerStats(s)
        for s in Source.objects.filter(is_reference=False).order_by("priority", "code")
    }
    classes = {c.value: ClassStats(c.value, c.label) for c in CarClass}
    comparisons, trips = 0, set()
    max_spread, max_label = None, ""

    for key, group in groups.items():
        prices, reference = group["prices"], group["reference"]
        for source_id, price in prices.items():
            if reference and source_id in sellers:
                sellers[source_id].markups.append(float((price - reference) / reference * 100))
        if len(prices) < 2:
            continue
        comparisons += 1
        trips.add(key[1])
        best, worst = min(prices.values()), max(prices.values())
        for source_id, price in prices.items():
            stats = sellers.get(source_id)
            if stats is None:
                continue
            stats.offers += 1
            stats.cheapest += price == best
            stats.over_best.append(float((price - best) / best * 100))
        spread = worst - best
        classes[key[2]].spreads.append(spread)
        classes[key[2]].spread_pcts.append(float(spread / best * 100))
        if max_spread is None or spread > max_spread:
            max_spread, max_label = spread, labels[key]

    return Report(
        days=days,
        comparisons=comparisons,
        trips=len(trips),
        snapshots=total,
        sellers=[s for s in sellers.values() if s.offers or s.markups],
        classes=[c for c in classes.values() if c.comparisons],
        max_spread=max_spread,
        max_spread_label=max_label,
    )


def cached_report(days: int) -> Report:
    key = f"analytics-report:{days}"
    report = cache.get(key)
    if report is None:
        report = build_report(days)
        cache.set(key, report, 600)
    return report
