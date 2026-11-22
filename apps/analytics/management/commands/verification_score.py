from django.core.management.base import BaseCommand

from ...verification import read_sheet, score


class Command(BaseCommand):
    help = "Count matches in a filled price check sheet."

    def add_arguments(self, parser):
        parser.add_argument("path")

    def handle(self, *args, **options):
        with open(options["path"], encoding="utf-8") as fh:
            result = score(read_sheet(fh))
        for name, s in sorted(result.by_seller.items()):
            self.stdout.write(
                f"{name:<12} checked {s.checked:>3}  price {s.price_ok:>3}  "
                f"time {s.time_ok:>3}  match {s.rate:5.1f}%"
            )
        total = result.total
        self.stdout.write(f"{'total':<12} checked {total.checked:>3}  match {total.rate:5.1f}%")
        if result.unchecked:
            self.stdout.write(f"not filled yet: {result.unchecked}")
        for row in result.mismatches:
            self.stdout.write(
                f"  {row['date']} {row['train']} {row['class']} {row['seller']}: "
                f"ours {row['our_price']} {row['departure']}, "
                f"site {row['site_price'] or '—'} {row['site_departure'] or ''} {row['note']}"
            )
