from collections import Counter

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.catalog.models import Route
from apps.sync.scheduler import SALE_DAYS, due_syncs, schedule_tick
from apps.sync.services import sources_for_route


class Command(BaseCommand):
    help = "Show what the background schedule would refresh, or queue one batch of jobs."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="only show what is due")

    def handle(self, *args, dry_run: bool, **options):
        due = due_syncs()
        per_route = Counter(d.route for d in due)
        never = sum(d.last_synced is None for d in due)
        hot = sum(d.hot for d in due)
        self.stdout.write(f"due now: {len(due)} (never synced: {never}, near dates: {hot})")
        for route, count in sorted(per_route.items(), key=lambda kv: str(kv[0])):
            self.stdout.write(f"  {route}: {count}")
        self.stdout.write(self._daily_estimate())

        if not dry_run:
            created = schedule_tick()
            self.stdout.write(self.style.SUCCESS(f"queued {len(created)} jobs"))

    @staticmethod
    def _daily_estimate() -> str:
        jobs = requests = 0
        for route in Route.objects.filter(is_scheduled=True):
            days = min(route.horizon_days, SALE_DAYS)
            hot = min(days, settings.SCHEDULE_HOT_DAYS)
            per_day = hot * 24 / settings.SCHEDULE_HOT_HOURS + (days - hot) * 24 / (
                settings.SCHEDULE_WARM_HOURS
            )
            jobs += per_day
            requests += per_day * len(sources_for_route(route))
        return f"steady state: ~{jobs:.0f} jobs/day, ~{requests:.0f} seller requests/day"
