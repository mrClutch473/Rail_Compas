import math
from datetime import datetime, timedelta
from decimal import Decimal

from django import template
from django.core.cache import cache
from django.utils import timezone
from django.utils.html import format_html
from django.utils.safestring import mark_safe

register = template.Library()

NBSP = " "


@register.filter
def rub(value) -> str:
    """4905.86 -> '4 906 ₽' (sellers round up to the ruble)."""
    if value in (None, ""):
        return "—"
    number = math.ceil(Decimal(value))
    return f"{number:,}".replace(",", NBSP) + " ₽"


@register.filter
def plural(number, forms: str) -> str:
    """{{ 5|plural:"место,места,мест" }} -> '5 мест'."""
    one, few, many = forms.split(",")
    n = abs(int(number))
    if n % 10 == 1 and n % 100 != 11:
        word = one
    elif 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        word = few
    else:
        word = many
    return f"{number} {word}"


@register.filter
def duration(minutes) -> str:
    minutes = int(minutes or 0)
    days, rest = divmod(minutes, 24 * 60)
    hours, mins = divmod(rest, 60)
    parts = []
    if days:
        parts.append(f"{days} д")
    if hours:
        parts.append(f"{hours} ч")
    if mins and not days:
        parts.append(f"{mins} мин")
    return " ".join(parts) or "0 мин"


@register.filter
def ago(moment: datetime | None) -> str:
    if moment is None:
        return "ещё не обновлялось"
    delta = timezone.now() - moment
    if delta < timedelta(minutes=1):
        return "только что"
    if delta < timedelta(hours=1):
        return f"{int(delta.total_seconds() // 60)} мин назад"
    if delta < timedelta(hours=6):
        return f"{int(delta.total_seconds() // 3600)} ч назад"
    local = timezone.localtime(moment)
    today = timezone.localdate()
    if local.date() == today:
        return f"сегодня в {local:%H:%M}"
    if local.date() == today - timedelta(days=1):
        return f"вчера в {local:%H:%M}"
    return f"{local:%d.%m} в {local:%H:%M}"


def _series_slots() -> dict[str, int]:
    slots = cache.get("series-slots")
    if slots is None:
        from apps.catalog.models import Source

        codes = Source.objects.filter(is_reference=False).order_by("priority", "code")
        slots = {code: i + 1 for i, code in enumerate(codes.values_list("code", flat=True))}
        cache.set("series-slots", slots, 300)
    return slots


@register.filter
def series(source) -> str:
    """CSS colour of a seller: fixed slot by priority, never by price rank."""
    if source.is_reference:
        return "var(--series-ref)"
    slot = _series_slots().get(source.code, 0)
    return f"var(--series-{slot})" if 1 <= slot <= 3 else "var(--muted)"


@register.filter
def get(mapping, key):
    return mapping.get(key) if mapping else None


@register.filter
def percent(value) -> str:
    number = float(value)
    if 0 < abs(number) < 1:
        return "<1%"
    return f"{round(number)}%"


ICONS = {
    "swap": '<path d="M7 7h13l-4-4M17 17H4l4 4"/>',
    "calendar": '<rect x="3" y="4.5" width="18" height="16" rx="3"/><path d="M3 9.5h18M8 2.5v4M16 2.5v4"/>',
    "search": '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
    "refresh": '<path d="M20 11a8 8 0 0 0-14.5-4.5L4 8M4 4v4h4M4 13a8 8 0 0 0 14.5 4.5L20 16M20 20v-4h-4"/>',
    "arrow-right": '<path d="M5 12h14M13 6l6 6-6 6"/>',
    "external": '<path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
    "check": '<path d="m5 12 5 5 9-10"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "seat": '<path d="M7 4v8a2 2 0 0 0 2 2h7M5 20h14M8 14l-1 6M17 14l1 6"/>',
    "alert": '<path d="M12 4 2.5 20h19L12 4ZM12 10v4M12 17.5v.01"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
    "moon": '<path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5Z"/>',
    "train": '<rect x="5" y="3" width="14" height="14" rx="4"/><path d="M5 11h14M9 21l1.5-4M15 21l-1.5-4M9 14h.01M15 14h.01"/>',
    "spark": '<path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M6 18l2.5-2.5M15.5 8.5 18 6"/>',
    "scale": '<path d="M12 3v18M5 7h14M5 7l-3 7a4 4 0 0 0 6 0L5 7ZM19 7l-3 7a4 4 0 0 0 6 0l-3-7ZM8 21h8"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8v.01"/>',
}


@register.simple_tag
def icon(name: str, size: int = 20, cls: str = "") -> str:
    paths = ICONS.get(name, "")
    return format_html(
        '<svg xmlns="http://www.w3.org/2000/svg" width="{}" height="{}" viewBox="0 0 24 24" '
        'fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" '
        'stroke-linejoin="round" class="{}" aria-hidden="true">{}</svg>',
        size,
        size,
        cls,
        mark_safe(paths),
    )
