from django.contrib import admin

from .models import ClickOut


@admin.register(ClickOut)
class ClickOutAdmin(admin.ModelAdmin):
    list_display = ["created_at", "source", "trip", "car_class", "price_shown", "was_cheapest"]
    list_filter = ["source", "was_cheapest", "car_class"]
    date_hierarchy = "created_at"
    list_select_related = ["source", "trip"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
