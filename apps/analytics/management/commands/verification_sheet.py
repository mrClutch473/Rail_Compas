import sys

from django.core.management.base import BaseCommand

from apps.core.enums import SyncTrigger
from apps.sync.services import request_sync

from ...verification import parse_offsets, pick_routes, sheet_rows, travel_dates, write_sheet


class Command(BaseCommand):
    help = "Price check sheet (CSV): what we show, with links to check on the sellers' sites."

    def add_arguments(self, parser):
        parser.add_argument("--routes", type=int, default=5)
        parser.add_argument("--days", default="1,7,20", help="days from today, comma separated")
        parser.add_argument("--trains", type=int, default=2, help="trains per route and date")
        parser.add_argument("--sync", action="store_true", help="queue fresh syncs and exit")
        parser.add_argument("-o", "--output", help="file instead of stdout")

    def handle(self, *args, **options):
        routes = pick_routes(options["routes"])
        offsets = parse_offsets(options["days"])
        if options["sync"]:
            for route in routes:
                for day in travel_dates(offsets):
                    request_sync(route, day, SyncTrigger.ADMIN)
            self.stderr.write(f"queued {len(routes) * len(offsets)} syncs, run again in a minute")
            return
        rows = sheet_rows(routes, offsets, options["trains"])
        if options["output"]:
            with open(options["output"], "w", encoding="utf-8", newline="") as fh:
                write_sheet(rows, fh)
        else:
            write_sheet(rows, sys.stdout)
        self.stderr.write(f"{len(rows)} rows")
