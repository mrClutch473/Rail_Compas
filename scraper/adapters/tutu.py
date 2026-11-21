"""Tutu: POST offers-api.tutu.ru/railway/offers, JSON, no browser.

The answer is a list of chunks with the same shape: `dictionary` (trains, segments, fares,
conditions, stations) and `offers.actual` (one offer per train, with price variants).
See docs/SOURCES.md, section "Туту".
"""

import re
import uuid
from collections import defaultdict
from datetime import UTC, datetime

from ..enums import CarClass
from ..errors import LayoutChanged
from ..normalize import aware, car_class_from, normalize_train_number, to_price
from ..schemas import Notice, ParseResult, RawCapture, RawOffer, ScrapeRequest
from ..transport import FetchContext
from . import SourceAdapter, register

OFFERS_URL = "https://offers-api.tutu.ru/railway/offers"
SESSION_NAMESPACE = uuid.UUID("6f1c3f4e-8d7a-4a3b-9a51-1b2a5f3c9e10")


def daily_session_id() -> str:
    # Tutu prices depend on the session (D-22). One stable id per day keeps history smooth.
    return str(uuid.uuid5(SESSION_NAMESPACE, datetime.now(UTC).date().isoformat()))


@register
class TutuAdapter(SourceAdapter):
    code = "tutu"
    headers = {"Origin": "https://www.tutu.ru", "Referer": "https://www.tutu.ru/"}

    async def fetch(self, ctx: FetchContext, request: ScrapeRequest) -> RawCapture:
        body = {
            "routes": [
                {
                    "departureStationCode": request.origin.external_id,
                    "arrivalStationCode": request.destination.external_id,
                    "departureDate": request.travel_date.isoformat(),
                }
            ],
            "analyticsPassengers": {"adultCount": request.adults, "children": []},
            "searchId": str(uuid.uuid4()),
            "sessionId": daily_session_id(),
            "flags": [{"name": "possibleOffersEnabled", "value": "ENABLED"}],
            "userData": {},
            "source": "trainOffers",
        }
        await ctx.post_json(OFFERS_URL, body)
        return RawCapture(strategy="api", url=self.offer_url(request), payloads=ctx.payloads)

    def offer_url(self, request: ScrapeRequest) -> str:
        day = request.travel_date.strftime("%d.%m.%Y")
        o, d = request.origin, request.destination
        if o.url_slug and d.url_slug:
            return f"https://www.tutu.ru/poezda/{o.url_slug}/{d.url_slug}/?date={day}&travelers=1"
        return (
            f"https://www.tutu.ru/poezda/rasp_d.php?nnst1={o.external_id}"
            f"&nnst2={d.external_id}&date={day}"
        )

    def parse(self, capture: RawCapture, request: ScrapeRequest) -> ParseResult:
        chunks = capture.payloads[-1].get("body") if capture.payloads else None
        if not isinstance(chunks, list):
            raise LayoutChanged("tutu: expected a list of chunks")

        offers: dict[tuple, RawOffer] = {}
        notices: list[Notice] = []
        seen_trains = 0
        try:
            for chunk in chunks:
                for offer in _chunk_offers(chunk, request, notices, self.offer_url(request)):
                    seen_trains += 1
                    key = (offer.train_number, offer.departure_at, offer.car_class)
                    if key not in offers or offer.price < offers[key].price:
                        offers[key] = offer
                for warning in chunk.get("warnings") or []:
                    text = re.sub(r"<[^>]+>", "", warning.get("content", "")).strip()
                    notices.append(Notice(code="source_warning", detail=text[:300]))
        except (KeyError, TypeError, AttributeError) as exc:
            raise LayoutChanged(f"tutu: unexpected structure ({exc!r})") from exc

        result = list(offers.values())
        return ParseResult(offers=result, warnings=_dedupe(notices), is_empty=not result)


def _chunk_offers(chunk: dict, request: ScrapeRequest, notices: list[Notice], url: str):
    common = chunk["dictionary"]["common"]
    train = chunk["dictionary"]["train"]
    voyages = train.get("voyages") or {}
    points = train.get("points") or {}
    conditions = train.get("conditions") or {}

    for offer in (chunk["offers"].get("actual") or {}).values():
        route = common["routes"][offer["routeIds"][0]]
        if len(route["segmentIds"]) != 1:
            continue  # a trip with a transfer, not a direct train
        segment = common["segments"][route["segmentIds"][0]]
        if segment.get("type") != "train":
            continue
        voyage = voyages[segment["voyageNumber"]]
        try:
            number = normalize_train_number(voyage["number"])
        except ValueError:
            notices.append(Notice(code="bad_train_number", detail=str(voyage["number"])))
            continue

        origin = points.get(str(segment["departureGeoPointId"]), {})
        destination = points.get(str(segment["arrivalGeoPointId"]), {})
        seats = _seats_by_car_type(voyage)
        best: dict[CarClass, dict] = {}
        service_classes: dict[CarClass, set[str]] = defaultdict(set)

        for variant in offer["offerVariants"]:
            price = variant.get("price") or {}
            if price.get("type") != "exact":
                continue
            if (variant.get("status") or {}).get("saleStatus") != "ALLOWED":
                continue
            fare_hashes = next(iter(variant["fareApplications"].values()))
            fare = common["fareApplications"][fare_hashes[0]]
            if fare.get("passengerType") != "ADULT":
                continue
            car_type = conditions.get(fare["segmentConditions"], {}).get("carType", "")
            try:
                car_class = car_class_from(car_type, [fare["serviceClass"]])
            except KeyError:
                notices.append(
                    Notice(code="unknown_car_class", detail=f"{car_type} {fare['serviceClass']}")
                )
                continue
            if car_class is None:
                continue
            service_classes[car_class].add(fare["serviceClass"])

            shown = to_price(price["value"]["amount"], price["value"]["fraction"])
            old = variant.get("priceOld") or {}
            discounts = [d["discountName"] for d in variant.get("discounts") or []]
            # D-22: compare prices without the seller's payment discounts
            base = (
                to_price(old["value"]["amount"], old["value"]["fraction"])
                if discounts and old.get("type") == "exact"
                else shown
            )
            current = best.get(car_class)
            if current is None or base < current["price"]:
                best[car_class] = {
                    "price": base,
                    "shown": shown,
                    "discounts": discounts,
                    "car_type": car_type,
                }

        for car_class, item in best.items():
            extras = {}
            if item["discounts"]:
                extras = {"price_with_discount": str(item["shown"]), "discounts": item["discounts"]}
            yield RawOffer(
                train_number=number,
                raw_number=voyage["number"],
                departure_at=aware(segment["departureDateTime"], request.origin.timezone),
                arrival_at=aware(segment["arrivalDateTime"], request.destination.timezone),
                origin_station=(origin.get("name") or {}).get("nominative", ""),
                origin_station_code=str(origin.get("code", "")),
                destination_station=(destination.get("name") or {}).get("nominative", ""),
                destination_station_code=str(destination.get("code", "")),
                car_class=car_class,
                service_classes=sorted(service_classes[car_class]),
                price=item["price"],
                seats=seats.get(item["car_type"]),
                url=url,
                extras=extras,
            )


def _seats_by_car_type(voyage: dict) -> dict[str, int]:
    # Free seats come from the car list; fare rows repeat the same seats per tariff.
    totals: dict[str, int] = defaultdict(int)
    for car in voyage.get("cars") or []:
        for group in car.get("seatsGroups") or []:
            totals[car["type"]] += group.get("seatsCount", 0)
    return dict(totals)


def _dedupe(notices: list[Notice]) -> list[Notice]:
    seen, unique = set(), []
    for notice in notices:
        if (notice.code, notice.detail) not in seen:
            seen.add((notice.code, notice.detail))
            unique.append(notice)
    return unique
