from django.db import models
from django.db.models import Q

from apps.core.enums import (
    ACTIVE_JOB_STATUSES,
    SUCCESS_TASK_STATUSES,
    SyncJobStatus,
    SyncTaskStatus,
    SyncTrigger,
)


class SyncJob(models.Model):
    route = models.ForeignKey("catalog.Route", on_delete=models.CASCADE, related_name="sync_jobs")
    travel_date = models.DateField("дата поездки")
    trigger = models.CharField("источник запуска", max_length=16, choices=SyncTrigger.choices)
    priority = models.PositiveSmallIntegerField("приоритет", default=50)
    status = models.CharField(
        "статус", max_length=16, choices=SyncJobStatus.choices, default=SyncJobStatus.QUEUED
    )
    error = models.TextField("ошибка", blank=True)
    created_at = models.DateTimeField("создано", auto_now_add=True)
    started_at = models.DateTimeField("начато", null=True, blank=True)
    finished_at = models.DateTimeField("завершено", null=True, blank=True)

    class Meta:
        verbose_name = "задание синхронизации"
        verbose_name_plural = "задания синхронизации"
        ordering = ["-created_at"]
        constraints = [
            # one live job per route and date: a second click joins the running one
            models.UniqueConstraint(
                fields=["route", "travel_date"],
                condition=Q(status__in=ACTIVE_JOB_STATUSES),
                name="syncjob_one_active_per_route_date",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "priority", "created_at"], name="syncjob_queue_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.route} {self.travel_date:%d.%m.%Y} [{self.get_status_display()}]"


class SyncTask(models.Model):
    job = models.ForeignKey(SyncJob, on_delete=models.CASCADE, related_name="tasks")
    source = models.ForeignKey("catalog.Source", on_delete=models.PROTECT, related_name="tasks")
    # copied from the job to find the latest successful task per source quickly
    route = models.ForeignKey("catalog.Route", on_delete=models.CASCADE, related_name="+")
    travel_date = models.DateField("дата поездки")
    status = models.CharField(
        "статус", max_length=16, choices=SyncTaskStatus.choices, default=SyncTaskStatus.PENDING
    )
    offers_count = models.PositiveIntegerField("предложений", default=0)
    attempts = models.PositiveSmallIntegerField("попыток", default=0)
    duration_ms = models.PositiveIntegerField("длительность, мс", null=True, blank=True)
    strategy = models.CharField("способ получения", max_length=32, blank=True)
    proxy_used = models.BooleanField("через прокси", default=False)
    error = models.TextField("ошибка", blank=True)
    warnings = models.JSONField("предупреждения", default=list, blank=True)
    raw_ref = models.CharField("сырой ответ", max_length=255, blank=True)
    started_at = models.DateTimeField("начато", null=True, blank=True)
    finished_at = models.DateTimeField("завершено", null=True, blank=True)

    class Meta:
        verbose_name = "задача площадки"
        verbose_name_plural = "задачи площадок"
        ordering = ["-finished_at"]
        constraints = [
            models.UniqueConstraint(fields=["job", "source"], name="synctask_one_per_source"),
        ]
        indexes = [
            models.Index(
                fields=["source", "route", "travel_date", "-finished_at"],
                condition=Q(status__in=SUCCESS_TASK_STATUSES),
                name="synctask_latest_success_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.source.code} {self.route} {self.travel_date:%d.%m.%Y}"

    @property
    def is_success(self) -> bool:
        return self.status in SUCCESS_TASK_STATUSES
