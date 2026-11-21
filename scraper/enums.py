from enum import StrEnum


class CarClass(StrEnum):
    # values must match apps.core.enums.CarClass (checked by a test)
    SEATED = "seated"
    PLATZKART = "platzkart"
    COUPE = "coupe"
    SV = "sv"
    LUX = "lux"


class ResultStatus(StrEnum):
    OK = "ok"
    EMPTY = "empty"
    BLOCKED = "blocked"
    TIMEOUT = "timeout"
    NETWORK = "network"
    LAYOUT_CHANGED = "layout_changed"
    INVALID = "invalid"
    ERROR = "error"
    SKIPPED = "skipped"
