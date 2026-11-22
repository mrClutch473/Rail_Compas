from django.contrib import admin
from unfold.admin import ModelAdmin
from unfold.decorators import display

from .models import ClickOut


@admin.register(ClickOut)
class ClickOutAdmin(ModelAdmin):
    list_display = ["created_at", "source", "trip", "car_class", "price_shown", "cheapest"]
    list_filter = ["source", "was_cheapest", "car_class"]
    date_hierarchy = "created_at"
    list_select_related = ["source", "trip"]

    @display(description="Лучшая цена", label={True: "success", False: "info"})
    def cheapest(self, obj):
        return obj.was_cheapest, "да" if obj.was_cheapest else "нет"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
