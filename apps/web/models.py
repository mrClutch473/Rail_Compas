from django.db import models

from apps.core.enums import CarClass


class ClickOut(models.Model):
    """A visitor went to a seller's site. No IP or cookies are stored."""

    trip = models.ForeignKey(
        "offers.Trip", verbose_name="рейс", on_delete=models.CASCADE, related_name="clicks"
    )
    source = models.ForeignKey(
        "catalog.Source", verbose_name="площадка", on_delete=models.PROTECT, related_name="clicks"
    )
    car_class = models.CharField("класс", max_length=16, choices=CarClass.choices)
    price_shown = models.DecimalField("показанная цена", max_digits=10, decimal_places=2)
    was_cheapest = models.BooleanField("была самой дешёвой", default=False)
    created_at = models.DateTimeField("время", auto_now_add=True)

    class Meta:
        verbose_name = "переход на площадку"
        verbose_name_plural = "переходы на площадки"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.source.code} {self.trip} {self.created_at:%d.%m %H:%M}"
