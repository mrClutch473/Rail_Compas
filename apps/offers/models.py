from django.db import models
from django.db.models import F, Q

from apps.core.enums import CarClass


class Trip(models.Model):
    route = models.ForeignKey(
        "catalog.Route", verbose_name="направление", on_delete=models.CASCADE, related_name="trips"
    )
    train_number = models.CharField("поезд", max_length=10)
    alt_numbers = models.JSONField(
        "другие номера",
        default=list,
        blank=True,
        help_text="Номера этого же поезда у других площадок (двойная нумерация)",
    )
    train_name = models.CharField("название поезда", max_length=150, blank=True)
    departure_date = models.DateField("дата отправления (местная)")
    departure_at = models.DateTimeField("отправление")
    arrival_at = models.DateTimeField("прибытие")
    duration_min = models.PositiveIntegerField("в пути, мин")
    origin_station = models.ForeignKey(
        "catalog.Station",
        verbose_name="вокзал отправления",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )
    destination_station = models.ForeignKey(
        "catalog.Station",
        verbose_name="вокзал прибытия",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )
    created_at = models.DateTimeField("создан", auto_now_add=True)
    updated_at = models.DateTimeField("обновлён", auto_now=True)

    class Meta:
        verbose_name = "рейс"
        verbose_name_plural = "рейсы"
        ordering = ["departure_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["route", "train_number", "departure_date"], name="trip_unique_key"
            ),
            models.CheckConstraint(
                condition=Q(arrival_at__gt=F("departure_at")), name="trip_arrival_after_departure"
            ),
        ]
        indexes = [models.Index(fields=["route", "departure_date"], name="trip_route_date_idx")]

    def __str__(self) -> str:
        return f"{self.train_number} {self.departure_date:%d.%m.%Y}"


class PriceSnapshot(models.Model):
    trip = models.ForeignKey(
        Trip, verbose_name="рейс", on_delete=models.CASCADE, related_name="snapshots"
    )
    source = models.ForeignKey(
        "catalog.Source",
        verbose_name="площадка",
        on_delete=models.PROTECT,
        related_name="snapshots",
    )
    task = models.ForeignKey(
        "sync.SyncTask", verbose_name="задача", on_delete=models.CASCADE, related_name="snapshots"
    )
    car_class = models.CharField("класс", max_length=16, choices=CarClass.choices)
    price = models.DecimalField("цена", max_digits=10, decimal_places=2)
    price_old = models.DecimalField(
        "цена без скидки площадки", max_digits=10, decimal_places=2, null=True, blank=True
    )
    seats = models.PositiveIntegerField("мест", null=True, blank=True)
    url = models.URLField("ссылка", max_length=500, blank=True)
    extras = models.JSONField("дополнительно", default=dict, blank=True)
    collected_at = models.DateTimeField("собрано")

    class Meta:
        verbose_name = "снимок цены"
        verbose_name_plural = "снимки цен"
        ordering = ["-collected_at"]
        constraints = [
            models.CheckConstraint(condition=Q(price__gt=0), name="snapshot_price_positive"),
        ]
        indexes = [
            models.Index(
                fields=["trip", "source", "car_class", "collected_at"], name="snapshot_history_idx"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.trip} {self.get_car_class_display()} {self.price} ₽ ({self.source.code})"
