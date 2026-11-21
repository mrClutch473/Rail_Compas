import re
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from .enums import CarClass

LATIN_TWINS = str.maketrans("ABEKMHOPCTXY", "АВЕКМНОРСТХУ")
TRANSLIT = {"ZH": "Ж", "CH": "Ч", "SH": "Ш", "YA": "Я", "YU": "Ю", "E'": "Э"}
TRAIN_NUMBER = re.compile(r"^\d{3}[А-ЯЁ]$")


def normalize_train_number(raw: str) -> str:
    """'№ 89 Ж' -> '089Ж', '357*С' -> '357С', '016A' (latin) -> '016А'."""
    text = re.sub(r"[№\s*.\-]", "", raw or "").upper()
    match = re.fullmatch(r"(\d{1,3})([A-ZА-ЯЁ']{1,2})", text)
    if not match:
        raise ValueError(f"bad train number: {raw!r}")
    digits, letters = match.groups()
    letter = TRANSLIT.get(letters) or letters.translate(LATIN_TWINS)
    number = f"{int(digits):03d}{letter}"
    if not TRAIN_NUMBER.match(number):
        raise ValueError(f"bad train number: {raw!r}")
    return number


def twin_numbers(number: str) -> set[str]:
    """Numbers the same train may carry on another seller: odd/even pair (357С <-> 358С)."""
    digits, letter = int(number[:3]), number[3:]
    twin = digits + 1 if digits % 2 else digits - 1
    return {f"{twin:03d}{letter}"} if twin > 0 else set()


# Service classes that mean a "soft"/luxury car rather than a two-berth SV
LUX_SERVICE_CLASSES = {"1А", "1И", "1М"}

CAR_CLASS_HINTS = {
    CarClass.PLATZKART: {"reserved_seat", "плацкарт", "плацкартный", "platzkart"},
    CarClass.COUPE: {"compartment", "купе", "купейный", "coupe"},
    CarClass.SEATED: {"sedentary", "сидячий", "seated", "эконом", "sitting"},
    CarClass.SV: {"lux", "luxury", "св", "sv", "спальный"},
    CarClass.LUX: {"soft", "мягкий", "люкс"},
}
SKIPPED_HINTS = {"shared", "общий"}


def car_class_from(hint: str, service_classes: list[str] | None = None) -> CarClass | None:
    """Map a seller's car type label to our class. None means 'not comparable, skip'."""
    key = (hint or "").strip().lower()
    if key in SKIPPED_HINTS:
        return None
    classes = {c.upper() for c in service_classes or []}
    for car_class, hints in CAR_CLASS_HINTS.items():
        if key in hints:
            if car_class is CarClass.SV and classes and classes <= LUX_SERVICE_CLASSES:
                return CarClass.LUX
            return car_class
    raise KeyError(hint)


CENT = Decimal("0.01")


def to_price(value, fraction: int = 1) -> Decimal:
    """Money from int/float/str; `fraction` is for sources that send kopecks (fraction=100)."""
    amount = Decimal(str(value)) / Decimal(fraction)
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)


def aware(value: str | datetime, timezone: str) -> datetime:
    """ISO string or datetime -> timezone-aware datetime; naive values are local to `timezone`."""
    moment = datetime.fromisoformat(value) if isinstance(value, str) else value
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=ZoneInfo(timezone))
    return moment


def normalize_station(name: str) -> str:
    """Key for matching station names written differently by sellers."""
    text = (name or "").lower().replace("ё", "е")
    text = re.sub(r"\bвокзал\b|[()«»\"']", " ", text)
    text = re.sub(r"[-–—]", " ", text)
    return re.sub(r"\s+", " ", text).strip()
