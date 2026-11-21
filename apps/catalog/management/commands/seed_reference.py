from pathlib import Path

import yaml
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.catalog.models import City, Route, Source, SourceCity

SOURCE_FIELDS = {
    "name",
    "base_url",
    "priority",
    "color",
    "fee_policy",
    "price_note",
    "min_interval_sec",
    "daily_budget",
    "is_reference",
    "use_proxy",
}


class Command(BaseCommand):
    help = "Load sources, cities, seller city IDs and routes from a YAML file. Safe to re-run."

    def add_arguments(self, parser):
        parser.add_argument("--file", default=str(settings.BASE_DIR / "seed" / "reference.yaml"))

    @transaction.atomic
    def handle(self, *args, file: str, **options):
        path = Path(file)
        if not path.exists():
            raise CommandError(f"{path} not found")
        data = yaml.safe_load(path.read_text(encoding="utf-8"))

        sources = {}
        for item in data.get("sources", []):
            defaults = {k: v for k, v in item.items() if k in SOURCE_FIELDS}
            sources[item["code"]], _ = Source.objects.update_or_create(
                code=item["code"], defaults=defaults
            )

        cities = {}
        for item in data.get("cities", []):
            city, _ = City.objects.update_or_create(
                slug=item["slug"], defaults={"name": item["name"], "timezone": item["timezone"]}
            )
            cities[city.slug] = city
            for code, ids in (item.get("sources") or {}).items():
                if code not in sources:
                    raise CommandError(f"city {city.slug}: unknown source {code}")
                SourceCity.objects.update_or_create(
                    source=sources[code],
                    city=city,
                    defaults={
                        "external_id": str(ids["external_id"]),
                        "url_slug": ids.get("url_slug", ""),
                    },
                )

        routes = 0
        for item in data.get("routes", []):
            origin = cities.get(item["origin"]) or City.objects.get(slug=item["origin"])
            destination = cities.get(item["destination"]) or City.objects.get(
                slug=item["destination"]
            )
            Route.objects.update_or_create(
                origin=origin,
                destination=destination,
                defaults={
                    "slug": f"{origin.slug}-{destination.slug}",
                    "is_scheduled": item.get("is_scheduled", False),
                    "horizon_days": item.get("horizon_days", 30),
                },
            )
            routes += 1

        self.stdout.write(
            self.style.SUCCESS(f"sources: {len(sources)}, cities: {len(cities)}, routes: {routes}")
        )
