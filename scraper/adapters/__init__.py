from importlib import import_module
from importlib.util import find_spec

from .base import SourceAdapter

ADAPTERS: dict[str, type[SourceAdapter]] = {}
BUILTIN = ("tutu", "onetwotrip", "aviakassa")


def register(cls: type[SourceAdapter]) -> type[SourceAdapter]:
    ADAPTERS[cls.code] = cls
    return cls


def load_builtin() -> None:
    for name in BUILTIN:
        module = f"{__name__}.{name}"
        if find_spec(module):
            import_module(module)


def get_adapter(code: str) -> SourceAdapter:
    load_builtin()
    try:
        return ADAPTERS[code]()
    except KeyError:
        raise KeyError(f"no adapter for source {code!r}") from None


__all__ = ["ADAPTERS", "SourceAdapter", "get_adapter", "load_builtin", "register"]
