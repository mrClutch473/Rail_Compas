from datetime import timedelta

from django.contrib import admin, messages
from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone
from unfold.admin import ModelAdmin, TabularInline
from unfold.decorators import action, display

from apps.core.enums import SyncTrigger
from apps.offers.models import Trip
from apps.sync.services import NoSourcesForRoute, request_sync

from .lookup import fill_source_ids
from .models import City, Route, Source, SourceCity, Station


class SourceCityInline(TabularInline):
    model = SourceCity
    extra = 0
    fields = ["source", "external_id", "url_slug"]


class StationInline(TabularInline):
    model = Station
    extra = 0
    fields = ["name", "express_code", "is_verified"]


@admin.register(City)
class CityAdmin(ModelAdmin):
    list_display = ["name", "slug", "timezone", "sellers", "is_active"]
    list_filter = ["is_active", "timezone"]
    search_fields = ["name", "slug"]
    prepopulated_fields = {"slug": ["name"]}
    inlines = [SourceCityInline, StationInline]
    actions = ["find_seller_ids"]

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(seller_count=Count("source_ids"))

    @display(description="Площадок знают город", ordering="seller_count")
    def sellers(self, obj):
        return obj.seller_count

    @action(description="Найти ID у площадок по названию")
    def find_seller_ids(self, request, queryset):
        found = 0
        for city in queryset:
            report = fill_source_ids(city)
            found += len(report.added)
            for source, problem in report.problems.items():
                self.message_user(request, f"{city}: {source} — {problem}", messages.WARNING)
        self.message_user(request, f"Добавлено ID: {found}", messages.SUCCESS)


@admin.register(Station)
class StationAdmin(ModelAdmin):
    list_display = ["name", "city", "express_code", "aliases_list", "verified"]
    list_filter = ["is_verified", "city"]
    search_fields = ["name", "express_code"]
    autocomplete_fields = ["city"]
    actions = ["mark_verified", "merge_into_first"]

    @display(description="Проверен", label={True: "success", False: "warning"})
    def verified(self, obj):
        return obj.is_verified, "проверен" if obj.is_verified else "новый"

    @display(description="Другие написания")
    def aliases_list(self, obj):
        return ", ".join(obj.aliases) or "—"

    @action(description="Подтвердить вокзалы")
    def mark_verified(self, request, queryset):
        updated = queryset.update(is_verified=True)
        self.message_user(request, f"Подтверждено: {updated}", messages.SUCCESS)

    @action(description="Объединить в один (главный — проверенный или первый)")
    def merge_into_first(self, request, queryset):
        stations = list(queryset.order_by("-is_verified", "pk"))
        if len(stations) < 2:
            self.message_user(request, "Выберите хотя бы два вокзала", messages.WARNING)
            return
        if len({s.city_id for s in stations}) > 1:
            self.message_user(request, "Объединять можно вокзалы одного города", messages.ERROR)
            return
        main, rest = stations[0], stations[1:]
        with transaction.atomic():
            aliases = set(main.aliases)
            for other in rest:
                aliases.update([other.name, *other.aliases])
                Trip.objects.filter(origin_station=other).update(origin_station=main)
                Trip.objects.filter(destination_station=other).update(destination_station=main)
                if not main.express_code and other.express_code:
                    main.express_code = other.express_code
                other.delete()
            main.aliases = sorted(aliases - {main.name})
            main.is_verified = True
            main.save()
        self.message_user(request, f"Объединено в «{main.name}»", messages.SUCCESS)


@admin.register(Source)
class SourceAdmin(ModelAdmin):
    list_display = [
        "name",
        "code",
        "state",
        "is_reference",
        "priority",
        "min_interval_sec",
        "daily_budget",
        "fee_policy",
    ]
    list_filter = ["is_active", "is_reference"]
    search_fields = ["name", "code"]
    actions = ["activate", "deactivate"]

    @display(description="Состояние", label={True: "success", False: "danger"})
    def state(self, obj):
        return obj.is_active, "включена" if obj.is_active else "выключена"

    @action(description="Включить площадки")
    def activate(self, request, queryset):
        queryset.update(is_active=True)

    @action(description="Выключить площадки")
    def deactivate(self, request, queryset):
        queryset.update(is_active=False)


@admin.register(Route)
class RouteAdmin(ModelAdmin):
    list_display = [
        "__str__",
        "schedule",
        "horizon_days",
        "search_count",
        "last_searched_at",
        "trips_ahead",
    ]
    list_filter = ["is_scheduled", "origin"]
    search_fields = ["origin__name", "destination__name", "slug"]
    autocomplete_fields = ["origin", "destination"]
    readonly_fields = ["search_count", "last_searched_at", "created_at"]
    actions = ["sync_week", "schedule_on", "schedule_off"]

    def get_queryset(self, request):
        today = timezone.localdate()
        return (
            super()
            .get_queryset(request)
            .select_related("origin", "destination")
            .annotate(ahead=Count("trips", filter=Q(trips__departure_date__gte=today)))
        )

    @display(description="Расписание", label={True: "success", False: "info"})
    def schedule(self, obj):
        return obj.is_scheduled, "по расписанию" if obj.is_scheduled else "по запросу"

    @display(description="Рейсов впереди", ordering="ahead")
    def trips_ahead(self, obj):
        return obj.ahead

    @action(description="Синхронизировать на 7 дней вперёд")
    def sync_week(self, request, queryset):
        today = timezone.localdate()
        created = 0
        for route in queryset:
            for offset in range(7):
                try:
                    outcome = request_sync(route, today + timedelta(days=offset), SyncTrigger.ADMIN)
                except NoSourcesForRoute:
                    self.message_user(request, f"{route}: нет площадок", messages.WARNING)
                    break
                created += outcome.outcome == "created"
        self.message_user(request, f"Поставлено в очередь заданий: {created}", messages.SUCCESS)

    @action(description="Включить расписание")
    def schedule_on(self, request, queryset):
        queryset.update(is_scheduled=True)

    @action(description="Выключить расписание")
    def schedule_off(self, request, queryset):
        queryset.update(is_scheduled=False)
