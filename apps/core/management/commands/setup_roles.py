from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand

OPERATOR = "Оператор"
PROJECT_APPS = ["catalog", "offers", "sync", "web"]
# besides viewing everything, an operator runs syncs and keeps routes and stations tidy
EXTRA = [
    "catalog.change_route",
    "catalog.change_station",
    "catalog.delete_station",
    "catalog.change_city",
    "catalog.add_sourcecity",
    "catalog.change_sourcecity",
    "sync.add_syncjob",
    "sync.change_syncjob",
]


class Command(BaseCommand):
    help = "Create or update the «Оператор» group: sees everything, manages syncs and routes."

    def handle(self, *args, **options):
        group, _ = Group.objects.get_or_create(name=OPERATOR)
        perms = list(
            Permission.objects.filter(
                content_type__app_label__in=PROJECT_APPS, codename__startswith="view_"
            )
        )
        for full in EXTRA:
            app_label, codename = full.split(".")
            perms.append(
                Permission.objects.get(content_type__app_label=app_label, codename=codename)
            )
        group.permissions.set(perms)
        self.stdout.write(self.style.SUCCESS(f"{OPERATOR}: {len(perms)} permissions"))
