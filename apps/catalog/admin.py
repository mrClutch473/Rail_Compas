from django.contrib import admin

from .models import City, Route, Source, SourceCity, Station


class SourceCityInline(admin.TabularInline):
    model = SourceCity
    extra = 0
    fields = ["source", "external_id", "url_slug"]


class StationInline(admin.TabularInline):
    model = Station
    extra = 0
    fields = ["name", "express_code", "is_verified"]


@admin.register(City)
class CityAdmin(admin.ModelAdmin):
    list_display = ["name", "slug", "timezone", "is_active"]
    list_filter = ["is_active", "timezone"]
    search_fields = ["name", "slug"]
    prepopulated_fields = {"slug": ["name"]}
    inlines = [SourceCityInline, StationInline]


@admin.register(Station)
class StationAdmin(admin.ModelAdmin):
    list_display = ["name", "city", "express_code", "is_verified"]
    list_filter = ["is_verified", "city"]
    search_fields = ["name", "express_code"]
    autocomplete_fields = ["city"]


@admin.register(Source)
class SourceAdmin(admin.ModelAdmin):
    list_display = ["name", "code", "is_active", "is_reference", "priority", "fee_policy"]
    list_editable = ["is_active", "priority"]
    list_filter = ["is_active", "is_reference"]
    search_fields = ["name", "code"]


@admin.register(Route)
class RouteAdmin(admin.ModelAdmin):
    list_display = [
        "__str__",
        "is_scheduled",
        "horizon_days",
        "search_count",
        "last_searched_at",
    ]
    list_editable = ["is_scheduled"]
    list_filter = ["is_scheduled", "origin"]
    search_fields = ["origin__name", "destination__name", "slug"]
    autocomplete_fields = ["origin", "destination"]
    readonly_fields = ["search_count", "last_searched_at", "created_at"]
