from django.contrib import admin

from .models import SyncJob, SyncTask


class SyncTaskInline(admin.TabularInline):
    model = SyncTask
    extra = 0
    can_delete = False
    fields = ["source", "status", "offers_count", "attempts", "duration_ms", "error"]
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(SyncJob)
class SyncJobAdmin(admin.ModelAdmin):
    list_display = ["route", "travel_date", "trigger", "status", "created_at", "finished_at"]
    list_filter = ["status", "trigger"]
    search_fields = ["route__slug"]
    date_hierarchy = "created_at"
    list_select_related = ["route__origin", "route__destination"]
    readonly_fields = ["created_at", "started_at", "finished_at"]
    inlines = [SyncTaskInline]


@admin.register(SyncTask)
class SyncTaskAdmin(admin.ModelAdmin):
    list_display = [
        "source",
        "route",
        "travel_date",
        "status",
        "offers_count",
        "attempts",
        "duration_ms",
        "finished_at",
    ]
    list_filter = ["status", "source"]
    search_fields = ["route__slug", "error"]
    list_select_related = ["source", "route__origin", "route__destination"]
    raw_id_fields = ["job", "route"]
