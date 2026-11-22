from django.contrib import admin, messages
from django.utils.html import format_html_join
from unfold.admin import ModelAdmin, TabularInline
from unfold.decorators import action, display

from apps.core.enums import SyncTrigger

from .models import SyncJob, SyncTask
from .services import request_sync

JOB_COLORS = {
    "queued": "info",
    "running": "primary",
    "done": "success",
    "partial": "warning",
    "failed": "danger",
}
TASK_COLORS = {
    "pending": "info",
    "running": "primary",
    "ok": "success",
    "empty": "success",
    "blocked": "danger",
    "timeout": "warning",
    "network": "warning",
    "layout_changed": "danger",
    "invalid": "danger",
    "error": "danger",
    "skipped": "info",
}


def seconds_between(start, end) -> str:
    if not start or not end:
        return "—"
    return f"{(end - start).total_seconds():.1f} с"


class SyncTaskInline(TabularInline):
    model = SyncTask
    extra = 0
    can_delete = False
    fields = ["source", "status_badge", "offers_count", "attempts", "duration", "error"]
    readonly_fields = fields

    @display(description="Статус", label=TASK_COLORS)
    def status_badge(self, obj):
        return obj.status, obj.get_status_display()

    @display(description="Время")
    def duration(self, obj):
        return f"{obj.duration_ms / 1000:.1f} с" if obj.duration_ms else "—"

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(SyncJob)
class SyncJobAdmin(ModelAdmin):
    list_display = [
        "route",
        "travel_date",
        "trigger_badge",
        "status_badge",
        "created_at",
        "took",
    ]
    list_filter = ["status", "trigger", "route"]
    search_fields = ["route__slug", "route__origin__name", "route__destination__name"]
    date_hierarchy = "created_at"
    list_select_related = ["route__origin", "route__destination"]
    readonly_fields = ["created_at", "started_at", "finished_at", "error"]
    inlines = [SyncTaskInline]
    actions = ["repeat"]

    @display(description="Статус", label=JOB_COLORS)
    def status_badge(self, obj):
        return obj.status, obj.get_status_display()

    @display(
        description="Кто запустил",
        label={"user": "primary", "admin": "warning", "schedule": "info"},
    )
    def trigger_badge(self, obj):
        return obj.trigger, obj.get_trigger_display()

    @display(description="Длительность")
    def took(self, obj):
        return seconds_between(obj.started_at, obj.finished_at)

    @action(description="Повторить синхронизацию")
    def repeat(self, request, queryset):
        created = 0
        for job in queryset.select_related("route"):
            created += (
                request_sync(job.route, job.travel_date, SyncTrigger.ADMIN).outcome == "created"
            )
        self.message_user(request, f"Поставлено в очередь: {created}", messages.SUCCESS)


@admin.register(SyncTask)
class SyncTaskAdmin(ModelAdmin):
    list_display = [
        "source",
        "route",
        "travel_date",
        "status_badge",
        "offers_count",
        "attempts",
        "duration",
        "finished_at",
    ]
    list_filter = ["status", "source", "route"]
    search_fields = ["route__slug", "error"]
    list_select_related = ["source", "route__origin", "route__destination"]
    raw_id_fields = ["job", "route"]
    readonly_fields = ["warnings_list"]
    exclude = ["warnings"]

    @display(description="Статус", label=TASK_COLORS)
    def status_badge(self, obj):
        return obj.status, obj.get_status_display()

    @display(description="Время")
    def duration(self, obj):
        return f"{obj.duration_ms / 1000:.1f} с" if obj.duration_ms else "—"

    @display(description="Предупреждения")
    def warnings_list(self, obj):
        if not obj.warnings:
            return "нет"
        return format_html_join(
            "",
            "<div><b>{}</b> {}</div>",
            ((w.get("code", ""), w.get("detail", "")) for w in obj.warnings),
        )
