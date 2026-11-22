from django.core.management.base import BaseCommand

from ...services import build_report


def pct(value) -> str:
    return "—" if value is None else f"{value:+.1f}%".replace(".", ",")


class Command(BaseCommand):
    help = "Seller comparison over the collected data, as markdown tables for the thesis."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=7)

    def handle(self, *args, **options):
        r = build_report(options["days"])
        out = self.stdout.write
        out(f"Период: {r.days} дн., снимков {r.snapshots}, рейсов {r.trips}, ")
        out(f"сравнений (один поезд, класс и момент) {r.comparisons}\n")
        out("| Площадка | Сравнений | Дешевле всех | Наценка к тарифу РЖД | Дороже лучшей |")
        out("|---|---|---|---|---|")
        for s in r.sellers:
            share = f"{s.cheapest_share:.0f}%"
            out(
                f"| {s.source.name} | {s.offers} | {share} | {pct(s.avg_markup)} "
                f"| {pct(s.avg_over_best)} |"
            )
        out("\n| Класс | Сравнений | Средняя разница, ₽ | Средняя разница, % |")
        out("|---|---|---|---|")
        for c in r.classes:
            out(
                f"| {c.label} | {c.comparisons} | {c.avg_spread:.0f} "
                f"| {c.avg_spread_pct:.1f}% |".replace(".", ",")
            )
        if r.max_spread is not None:
            out(f"\nСамая большая разница: {r.max_spread:.0f} ₽ ({r.max_spread_label})")
