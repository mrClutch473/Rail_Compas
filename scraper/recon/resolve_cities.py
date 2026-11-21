"""Look up how each seller identifies a city and check that direct trains run from the origin.

Prints JSON that becomes seed/reference.yaml. Standard library only, so it runs anywhere.

    python -m scraper.recon.resolve_cities > cities.json
"""

import gzip
import json
import sys
import time
import urllib.parse
import urllib.request
import uuid
from datetime import date, timedelta

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0"
PAUSE = 1.5
SESSION = str(uuid.uuid4())

ORIGIN = ("Волгоград", "volgograd", "Europe/Volgograd")
DESTINATIONS = [
    ("Москва", "moskva", "Europe/Moscow"),
    ("Санкт-Петербург", "sankt-peterburg", "Europe/Moscow"),
    ("Саратов", "saratov", "Europe/Saratov"),
    ("Ростов-на-Дону", "rostov-na-donu", "Europe/Moscow"),
    ("Астрахань", "astrahan", "Europe/Astrakhan"),
    ("Самара", "samara", "Europe/Samara"),
    ("Воронеж", "voronezh", "Europe/Moscow"),
    ("Краснодар", "krasnodar", "Europe/Moscow"),
    ("Адлер", "adler", "Europe/Moscow"),
    ("Минеральные Воды", "mineralnye-vody", "Europe/Moscow"),
    ("Кисловодск", "kislovodsk", "Europe/Moscow"),
    ("Нижний Новгород", "nizhniy-novgorod", "Europe/Moscow"),
    ("Казань", "kazan", "Europe/Moscow"),
    ("Екатеринбург", "ekaterinburg", "Asia/Yekaterinburg"),
]


def get_json(url: str, referer: str):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "Referer": referer,
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        raw = response.read()
        if response.headers.get("content-encoding") == "gzip":
            raw = gzip.decompress(raw)
    time.sleep(PAUSE)
    return json.loads(raw)


def tutu_code(name: str) -> str | None:
    url = "https://www.tutu.ru/suggest/railway_simple/?name=" + urllib.parse.quote(name)
    items = get_json(url, "https://www.tutu.ru/")
    return items[0]["id"] if items else None


def tutu_slugs(from_code: str, to_code: str) -> tuple[str, str] | None:
    url = (
        "https://train-gateway.tutu.ru/api/seo/schedule/link"
        f"?departure_rzd_code={from_code}&arrival_rzd_code={to_code}"
    )
    path = get_json(url, "https://www.tutu.ru/").get("url", "")
    parts = [p for p in path.split("/") if p]
    return (parts[1], parts[2]) if len(parts) == 3 else None


def onetwotrip_station(name: str) -> dict | None:
    base = "https://www.onetwotrip.com/_railways/rzd/suggestStations?searchText="
    data = get_json(base + urllib.parse.quote(name), "https://www.onetwotrip.com/ru/poezda/")
    items = data.get("result") or []
    return {"id": str(items[0]["id"]), "slug": items[0]["trans"]} if items else None


def aviakassa_code(name: str) -> str | None:
    query = urllib.parse.urlencode(
        {"prefix": name, "channel_token": "", "lang": "ru", "uuid": SESSION, "partner": "AKV4"}
    )
    url = "https://www.aviakassa.com/api/v4/railway/search/get-autocomplete-list?" + query
    items = get_json(url, "https://www.aviakassa.com/zhd")["data"]["items"]
    return str(items[0]["parent_code"]) if items else None


def direct_trains(from_code: str, to_code: str, day: date) -> int:
    search_id = f"AKV4.AKV40000.1.0.0.ru.{from_code}.{to_code}.{day:%d%m%Y}.0.24"
    params = {"search_id": search_id, "channel_token": "", "lang": "ru", "uuid": SESSION}
    query = urllib.parse.urlencode({**params, "partner": "AKV4"})
    url = "https://www.aviakassa.com/api/v4/railway/search/get-trains-list?" + query
    return len(get_json(url, "https://www.aviakassa.com/zhd")["data"]["items"])


def resolve(name: str, slug: str, tz: str) -> dict:
    city = {"name": name, "slug": slug, "timezone": tz, "sources": {}}
    if code := tutu_code(name):
        city["sources"]["tutu"] = {"external_id": code}
    if station := onetwotrip_station(name):
        city["sources"]["onetwotrip"] = {"external_id": station["id"], "url_slug": station["slug"]}
    if code := aviakassa_code(name):
        city["sources"]["aviakassa"] = {"external_id": code}
    return city


def main() -> None:
    origin = resolve(*ORIGIN)
    cities, routes = [origin], []
    check_day = date.today() + timedelta(days=14)

    for name, slug, tz in DESTINATIONS:
        city = resolve(name, slug, tz)
        cities.append(city)
        tutu_from = origin["sources"].get("tutu", {}).get("external_id")
        tutu_to = city["sources"].get("tutu", {}).get("external_id")
        if tutu_from and tutu_to and (slugs := tutu_slugs(tutu_from, tutu_to)):
            origin["sources"]["tutu"]["url_slug"] = slugs[0]
            city["sources"]["tutu"]["url_slug"] = slugs[1]

        ak_from = origin["sources"].get("aviakassa", {}).get("external_id")
        ak_to = city["sources"].get("aviakassa", {}).get("external_id")
        trains = direct_trains(ak_from, ak_to, check_day) if ak_from and ak_to else 0
        routes.append({"origin": origin["slug"], "destination": slug, "trains_checked": trains})
        print(f"{name}: {city['sources']} trains={trains}", file=sys.stderr)

    print(
        json.dumps(
            {"checked_on": str(check_day), "cities": cities, "routes": routes},
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
