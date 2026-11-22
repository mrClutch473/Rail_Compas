from dataclasses import dataclass, field

import httpx

from scraper import lookup

from .models import City, Source, SourceCity


@dataclass
class LookupReport:
    added: list[str] = field(default_factory=list)
    problems: dict[str, str] = field(default_factory=dict)


def _tutu(city: City) -> tuple[str, str] | None:
    code = lookup.tutu_code(city.name)
    if not code:
        return None
    # Tutu only gives the URL slug for a pair of cities, so pair it with any known one
    partner = (
        SourceCity.objects.filter(source__code="tutu").exclude(city=city).select_related("city")
    ).first()
    slug = ""
    if partner:
        pair = lookup.tutu_slugs(code, partner.external_id)
        slug = pair[0] if pair else ""
    return code, slug


def _onetwotrip(city: City) -> tuple[str, str] | None:
    return lookup.onetwotrip_station(city.name)


def _aviakassa(city: City) -> tuple[str, str] | None:
    code = lookup.aviakassa_code(city.name)
    return (code, "") if code else None


FINDERS = {"tutu": _tutu, "onetwotrip": _onetwotrip, "aviakassa": _aviakassa}


def fill_source_ids(city: City) -> LookupReport:
    """Add the missing seller IDs for a city. Existing ones are left alone."""
    report = LookupReport()
    known = set(city.source_ids.values_list("source__code", flat=True))
    for source in Source.objects.filter(is_reference=False, code__in=FINDERS.keys()):
        if source.code in known:
            continue
        try:
            found = FINDERS[source.code](city)
        except (httpx.HTTPError, ValueError, KeyError, IndexError) as exc:
            report.problems[source.name] = f"ошибка запроса: {exc.__class__.__name__}"
            continue
        if not found:
            report.problems[source.name] = "город не найден в подсказках"
            continue
        external_id, slug = found
        SourceCity.objects.create(source=source, city=city, external_id=external_id, url_slug=slug)
        report.added.append(source.name)
    return report
