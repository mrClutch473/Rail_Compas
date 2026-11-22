"""Admin home page: is collection healthy, what is queued, how much data, what needs a look."""

import re
from datetime import timedelta

from django.conf import settings
from django.db.models import Count, Max, Q
from django.urls import reverse
from django.utils import timezone

from apps.catalog.models import Route, Source, Station
from apps.core.enums import SUCCESS_TASK_STATUSES, SyncJobStatus, SyncTaskStatus
from apps.offers.models import PriceSnapshot, Trip
from apps.sync.models import SyncJob, SyncTask

from .models import ClickOut

COOLDOWN = re.compile(r"cooling down until (\d\d:\d\d)")


def environment_callback(request):
    return ["Разработка", "warning"] if settings.DEBUG else ["Сервер", "success"]


def source_health(now) -> list[dict]:
    day_ago = now - timedelta(hours=24)
    rows = []
    for source in Source.objects.filter(is_reference=False).order_by("priority"):
        tasks = SyncTask.objects.filter(source=source, finished_at__gte=day_ago)
        total = tasks.count()
        ok = tasks.filter(status__in=SUCCESS_TASK_STATUSES).count()
        last_ok = SyncTask.objects.filter(
            source=source, status__in=SUCCESS_TASK_STATUSES
        ).aggregate(at=Max("finished_at"))["at"]
        last = (
            SyncTask.objects.filter(source=source)
            .exclude(finished_at=None)
            .order_by("-finished_at")
            .first()
        )
        cooling = None
        if last and last.status == SyncTaskStatus.BLOCKED:
            match = COOLDOWN.search(last.error)
            cooling = match.group(1) + " UTC" if match else "да"
        rate = ok / total * 100 if total else None
        if not source.is_active:
            state = ("info", "выключена")
        elif cooling:
            state = ("danger", "остывает")
        elif rate is None or (last_ok and now - last_ok > timedelta(hours=24)):
            state = ("warning", "нет свежих данных")
        elif rate < 80:
            state = ("warning", "есть сбои")
        else:
            state = ("success", "в порядке")
        rows.append(
            {
                "source": source,
                "tasks": total,
                "rate": rate,
                "last_ok": last_ok,
                "last_error": last.error
                if last and last.status not in SUCCESS_TASK_STATUSES
                else "",
                "cooling": cooling,
                "state": state,
            }
        )
    return rows


def dashboard_callback(request, context):
    now = timezone.now()
    day_ago = now - timedelta(hours=24)
    week_ago = now - timedelta(days=7)

    jobs_day = SyncJob.objects.filter(created_at__gte=day_ago)
    by_status = dict(jobs_day.values_list("status").annotate(n=Count("id")))
    clicks = ClickOut.objects.filter(created_at__gte=week_ago)
    click_total = clicks.count()
    unknown_classes = sum(
        any(w.get("code") == "unknown_car_class" for w in task.warnings)
        for task in SyncTask.objects.filter(finished_at__gte=day_ago).exclude(warnings=[])
    )
    broken = SyncTask.objects.filter(
        finished_at__gte=day_ago,
        status__in=[SyncTaskStatus.LAYOUT_CHANGED, SyncTaskStatus.INVALID, SyncTaskStatus.ERROR],
    ).count()

    context.update(
        {
            "health": source_health(now),
            "kpis": [
                (
                    "Снимков за сутки",
                    PriceSnapshot.objects.filter(collected_at__gte=day_ago).count(),
                ),
                ("Снимков всего", PriceSnapshot.objects.count()),
                (
                    "Рейсов впереди",
                    Trip.objects.filter(departure_date__gte=timezone.localdate()).count(),
                ),
                (
                    "Направлений",
                    f"{Route.objects.filter(is_scheduled=True).count()} / {Route.objects.count()}",
                ),
            ],
            "jobs_day": [
                ("Готово", by_status.get(SyncJobStatus.DONE, 0), "success"),
                ("Частично", by_status.get(SyncJobStatus.PARTIAL, 0), "warning"),
                ("Ошибка", by_status.get(SyncJobStatus.FAILED, 0), "danger"),
            ],
            "queue": {
                "queued": SyncJob.objects.filter(status=SyncJobStatus.QUEUED).count(),
                "running": SyncJob.objects.filter(status=SyncJobStatus.RUNNING).count(),
                "by_trigger": dict(
                    SyncJob.objects.filter(status=SyncJobStatus.QUEUED)
                    .values_list("trigger")
                    .annotate(n=Count("id"))
                ),
                "people": SyncJob.objects.filter(
                    status=SyncJobStatus.QUEUED, trigger__in=["user", "admin"]
                ).count(),
                "last_activity": SyncTask.objects.aggregate(at=Max("finished_at"))["at"],
            },
            "recent_jobs": SyncJob.objects.select_related("route__origin", "route__destination")[
                :8
            ],
            "clicks": {
                "total": click_total,
                "cheapest": clicks.filter(was_cheapest=True).count(),
                "by_source": clicks.values_list("source__name")
                .annotate(n=Count("id"))
                .order_by("-n"),
            },
            "attention": [
                (
                    "Новые вокзалы без проверки",
                    Station.objects.filter(is_verified=False).count(),
                    reverse("admin:catalog_station_changelist") + "?is_verified__exact=0",
                ),
                (
                    "Задачи со сменой формата или ошибкой за сутки",
                    broken,
                    reverse("admin:sync_synctask_changelist")
                    + "?status__in=layout_changed,invalid,error",
                ),
                (
                    "Задачи с неизвестным классом вагона",
                    unknown_classes,
                    reverse("admin:sync_synctask_changelist"),
                ),
                (
                    "Направлений без площадок",
                    Route.objects.annotate(
                        n=Count(
                            "origin__source_ids",
                            filter=Q(origin__source_ids__source__is_active=True),
                        )
                    )
                    .filter(n=0)
                    .count(),
                    reverse("admin:catalog_route_changelist"),
                ),
            ],
        }
    )
    return context
