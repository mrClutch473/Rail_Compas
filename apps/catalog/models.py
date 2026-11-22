from zoneinfo import available_timezones

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q

from apps.core.enums import FeePolicy


def validate_timezone(value: str) -> None:
    if value not in available_timezones():
        raise ValidationError(f"Неизвестный часовой пояс: {value}")


class City(models.Model):
    name = models.CharField("название", max_length=100)
    slug = models.SlugField("слаг", unique=True)
    timezone = models.CharField(
        "часовой пояс", max_length=64, default="Europe/Moscow", validators=[validate_timezone]
    )
    is_active = models.BooleanField("активен", default=True)

    class Meta:
        verbose_name = "город"
        verbose_name_plural = "города"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class Station(models.Model):
    city = models.ForeignKey(
        City, verbose_name="город", on_delete=models.PROTECT, related_name="stations"
    )
    name = models.CharField("название", max_length=150)
    aliases = models.JSONField("варианты написания", default=list, blank=True)
    express_code = models.CharField("код Экспресс", max_length=16, blank=True)
    is_verified = models.BooleanField("проверен", default=True)

    class Meta:
        verbose_name = "вокзал"
        verbose_name_plural = "вокзалы"
        ordering = ["city__name", "name"]
        constraints = [
            models.UniqueConstraint(fields=["city", "name"], name="station_unique_name_in_city"),
            models.UniqueConstraint(
                fields=["express_code"],
                condition=~Q(express_code=""),
                name="station_unique_express_code",
            ),
        ]

    def __str__(self) -> str:
        return self.name


class Source(models.Model):
    code = models.SlugField("код", unique=True, help_text="Совпадает с кодом адаптера парсера")
    name = models.CharField("название", max_length=100)
    base_url = models.URLField("сайт")
    is_active = models.BooleanField("активна", default=True)
    is_reference = models.BooleanField(
        "эталон",
        default=False,
        help_text="Не площадка продажи, а ориентир цены (базовый тариф перевозчика)",
    )
    priority = models.PositiveSmallIntegerField("порядок", default=100)
    color = models.CharField("цвет на графиках", max_length=7, default="#1f5bd8")
    fee_policy = models.CharField(
        "сервисный сбор", max_length=16, choices=FeePolicy.choices, default=FeePolicy.UNKNOWN
    )
    price_note = models.CharField("пояснение к цене", max_length=255, blank=True)
    min_interval_sec = models.PositiveIntegerField("пауза между запросами, с", default=5)
    daily_budget = models.PositiveIntegerField("запросов в сутки", default=1000)
    use_proxy = models.BooleanField("через прокси", default=False)

    class Meta:
        verbose_name = "площадка"
        verbose_name_plural = "площадки"
        ordering = ["priority", "name"]

    def __str__(self) -> str:
        return self.name


class SourceCity(models.Model):
    source = models.ForeignKey(
        Source, verbose_name="площадка", on_delete=models.CASCADE, related_name="cities"
    )
    city = models.ForeignKey(
        City, verbose_name="город", on_delete=models.CASCADE, related_name="source_ids"
    )
    external_id = models.CharField("внешний ID", max_length=64)
    url_slug = models.CharField("слаг в адресе", max_length=100, blank=True)

    class Meta:
        verbose_name = "город у площадки"
        verbose_name_plural = "города у площадок"
        constraints = [
            models.UniqueConstraint(fields=["source", "city"], name="source_city_unique"),
        ]

    def __str__(self) -> str:
        return f"{self.city} @ {self.source.code}: {self.external_id}"


class Route(models.Model):
    origin = models.ForeignKey(
        City, verbose_name="откуда", on_delete=models.PROTECT, related_name="routes_from"
    )
    destination = models.ForeignKey(
        City, verbose_name="куда", on_delete=models.PROTECT, related_name="routes_to"
    )
    slug = models.SlugField("слаг", unique=True)
    is_scheduled = models.BooleanField(
        "по расписанию", default=False, help_text="Обновлять в фоне, а не только по запросу"
    )
    horizon_days = models.PositiveSmallIntegerField("горизонт, дней", default=30)
    search_count = models.PositiveIntegerField("поисков", default=0)
    last_searched_at = models.DateTimeField("последний поиск", null=True, blank=True)
    created_at = models.DateTimeField("создан", auto_now_add=True)

    class Meta:
        verbose_name = "направление"
        verbose_name_plural = "направления"
        ordering = ["origin__name", "destination__name"]
        constraints = [
            models.UniqueConstraint(fields=["origin", "destination"], name="route_unique_pair"),
            models.CheckConstraint(
                condition=~Q(origin=F("destination")), name="route_origin_not_destination"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.origin} → {self.destination}"

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = f"{self.origin.slug}-{self.destination.slug}"
        super().save(*args, **kwargs)
