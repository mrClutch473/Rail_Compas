"""Aviakassa: GET www.aviakassa.com/api/v4/railway/search/get-trains-list, JSON, no browser.

The search id is built from the request itself, so one call is enough. Stations use the
same Express codes as Tutu.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from ..errors import LayoutChanged
from ..normalize import aware, car_class_from, normalize_train_number, to_price
from ..schemas import Notice, ParseResult, RawCapture, RawOffer, ScrapeRequest
from ..transport import FetchContext
from . import SourceAdapter, register

TRAINS_URL = "https://www.aviakassa.com/api/v4/railway/search/get-trains-list"
SESSION_NAMESPACE = uuid.UUID("0b6c8f0e-2a31-4b5e-8a8d-6f0c1c7e2d44")
PLATZKART_CAR_TYPE = "ReservedSeat"


def search_id(request: ScrapeRequest) -> str:
    o, d = request.origin.external_id, request.destination.external_id
    return f"AKV4.AKV40000.{request.adults}.0.0.ru.{o}.{d}.{request.travel_date:%d%m%Y}.0.24"


def daily_uuid() -> str:
    return str(uuid.uuid5(SESSION_NAMESPACE, datetime.now(UTC).date().isoformat()))


@register
class AviakassaAdapter(SourceAdapter):
    code = "aviakassa"
    headers = {"Referer": "https://www.aviakassa.com/zhd"}

    async def fetch(self, ctx: FetchContext, request: ScrapeRequest) -> RawCapture:
        params = {
            "search_id": search_id(request),
            "channel_token": "",
            "lang": "ru",
            "uuid": daily_uuid(),
            "partner": "AKV4",
        }
        await ctx.get_json(TRAINS_URL, params=params)
        return RawCapture(strategy="api", url=self.offer_url(request), payloads=ctx.payloads)

    def offer_url(self, request: ScrapeRequest) -> str:
        return f"https://www.aviakassa.com/zhd/results/{search_id(request)}"

    def parse(self, capture: RawCapture, request: ScrapeRequest) -> ParseResult:
        body = capture.payloads[-1].get("body") if capture.payloads else None
        if not isinstance(body, dict) or not body.get("success"):
            raise LayoutChanged(f"aviakassa: unexpected answer {str(body)[:200]}")
        data = body.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            raise LayoutChanged("aviakassa: expected data.items")

        offers, notices = [], []
        try:
            for item in data["items"]:
                offers.extend(self._train(item, request, notices))
        except (KeyError, TypeError, AttributeError) as exc:
            raise LayoutChanged(f"aviakassa: unexpected structure ({exc!r})") from exc
        return ParseResult(offers=offers, warnings=notices, is_empty=not data["items"])

    def _train(self, item: dict, request: ScrapeRequest, notices: list[Notice]):
        # is_component marks trains that change number en route (357*С), still direct trains
        try:
            number = normalize_train_number(item["train_number"])
        except ValueError:
            notices.append(Notice(code="bad_train_number", detail=str(item.get("train_number"))))
            return

        common = {
            "train_number": number,
            "raw_number": item["train_number"],
            "train_name": item.get("train_name") or "",
            "departure_at": aware(item["departure_date_time"], request.origin.timezone),
            "arrival_at": aware(item["arrival_date_time"], request.destination.timezone),
            "origin_station": item.get("passenger_departure_station_name", ""),
            "origin_station_code": str(item.get("passenger_departure_station_code", "")),
            "destination_station": item.get("passenger_arrival_station_name", ""),
            "destination_station_code": str(item.get("passenger_arrival_station_code", "")),
            "url": item.get("book_url") or self.offer_url(request),
        }

        for group in item.get("car_groups") or []:
            classes = group.get("service_classes") or []
            try:
                car_class = car_class_from(group.get("car_type_name", ""), classes)
            except KeyError:
                try:
                    car_class = car_class_from(group.get("car_type", ""), classes)
                except KeyError:
                    notices.append(
                        Notice(code="unknown_car_class", detail=group.get("car_type_name", ""))
                    )
                    continue
            if car_class is None or not group.get("total_place_quantity"):
                continue

            price = Decimal(str(group["min_ticket_price"]))
            extras = {}
            costs = group.get("service_costs") or []
            if group.get("car_type") == PLATZKART_CAR_TYPE and len(costs) == 1:
                # D-29: the single service cost of platzkart is bedding, included in the price
                extras["price_with_bedding"] = str(to_price(price))
                price -= Decimal(str(costs[0]))

            yield RawOffer(
                **common,
                car_class=car_class,
                service_classes=classes,
                price=to_price(price),
                seats=group.get("total_place_quantity"),
                extras=extras,
            )
