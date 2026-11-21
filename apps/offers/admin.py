from django.contrib import admin

from .models import PriceSnapshot, Trip


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Trip)
class TripAdmin(admin.ModelAdmin):
    list_display = ["train_number", "route", "departure_at", "arrival_at", "duration_min"]
    list_filter = ["route", "departure_date"]
    search_fields = ["train_number", "train_name"]
    date_hierarchy = "departure_date"
    list_select_related = ["route__origin", "route__destination"]
    raw_id_fields = ["route", "origin_station", "destination_station"]


@admin.register(PriceSnapshot)
class PriceSnapshotAdmin(ReadOnlyAdmin):
    list_display = ["trip", "source", "car_class", "price", "price_old", "seats", "collected_at"]
    list_filter = ["source", "car_class"]
    search_fields = ["trip__train_number"]
    date_hierarchy = "collected_at"
    list_select_related = ["trip", "source"]
