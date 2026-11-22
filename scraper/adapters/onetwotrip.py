"""OneTwoTrip: GET www.onetwotrip.com/_railways/rzd/metaTimetable, JSON, no browser.

Besides its own price every car type carries the price from the booking system without
OneTwoTrip's commission (`gdsCost`). That is the carrier tariff we show as "Тариф РЖД"
(checked against ticket.rzd.ru, 9 of 9 prices, docs/SOURCES.md).
"""

import math
from decimal import Decimal

from ..errors import LayoutChanged
from ..normalize import aware, car_class_from, normalize_train_number, to_price
from ..schemas import Notice, ParseResult, RawCapture, RawOffer, ScrapeRequest
from ..transport import FetchContext
from . import SourceAdapter, register

TIMETABLE_URL = "https://www.onetwotrip.com/_railways/rzd/metaTimetable"
BEDDING_MARKUP = "bedding_markup"
NO_SEATS_ERRORS = {1311}  # "На заданном направлении (или поезде) мест нет"


@register
class OneTwoTripAdapter(SourceAdapter):
    code = "onetwotrip"
    headers = {"Referer": "https://www.onetwotrip.com/ru/poezda/"}

    async def fetch(self, ctx: FetchContext, request: ScrapeRequest) -> RawCapture:
        params = {
            "adults": request.adults,
            "infants": 0,
            "date": request.travel_date.strftime("%d%m%Y"),
            "from": request.origin.external_id,
            "to": request.destination.external_id,
            "isReturn": "false",
        }
        await ctx.get_json(TIMETABLE_URL, params=params)
        return RawCapture(strategy="api", url=self.offer_url(request), payloads=ctx.payloads)

    def offer_url(self, request: ScrapeRequest) -> str:
        day = request.travel_date.strftime("%d%m%Y")
        o, d = request.origin.url_slug, request.destination.url_slug
        return f"https://www.onetwotrip.com/ru/poezda/{o}_{d}/?date={day}&adults=1&s=true"

    def parse(self, capture: RawCapture, request: ScrapeRequest) -> ParseResult:
        body = capture.payloads[-1].get("body") if capture.payloads else None
        if not isinstance(body, dict):
            raise LayoutChanged("onetwotrip: expected a JSON object")
        if not body.get("success", False):
            error = body.get("error") or {}
            if error.get("code") in NO_SEATS_ERRORS:
                notice = Notice(code="source_warning", detail=str(error.get("details", ""))[:300])
                return ParseResult(is_empty=True, warnings=[notice])
            raise LayoutChanged(f"onetwotrip: error {error.get('code')} {error.get('details')}")
        if "result" not in body:
            raise LayoutChanged("onetwotrip: expected 'result'")

        offers, references, notices = [], [], []
        try:
            for train in body["result"] or []:
                self._train(train, request, offers, references, notices)
        except (KeyError, TypeError, AttributeError) as exc:
            raise LayoutChanged(f"onetwotrip: unexpected structure ({exc!r})") from exc

        for message in body.get("infoMsgs") or []:
            notices.append(Notice(code="source_warning", detail=str(message)[:300]))
        return ParseResult(
            offers=offers,
            reference_offers=references,
            warnings=notices,
            is_empty=not body["result"],
        )

    def _train(self, train: dict, request: ScrapeRequest, offers, references, notices) -> None:
        try:
            number = normalize_train_number(train["trainNumber"])
        except ValueError:
            notices.append(Notice(code="bad_train_number", detail=str(train.get("trainNumber"))))
            return
        common = {
            "train_number": number,
            "raw_number": train["trainNumber"],
            "train_name": train.get("name") or "",
            "departure_at": aware(train["departure"]["isoTime"], request.origin.timezone),
            "arrival_at": aware(train["arrival"]["isoTime"], request.destination.timezone),
            "origin_station": train["from"].get("station", ""),
            "origin_station_code": str(train["from"].get("code", "")),
            "destination_station": train["to"].get("station", ""),
            "destination_station_code": str(train["to"].get("code", "")),
        }
        url = train.get("deeplink") or self.offer_url(request)

        for place in train.get("places") or []:
            classes = place.get("serviceClasses") or []
            try:
                car_class = car_class_from(place["typeName"], classes)
            except KeyError:
                notices.append(Notice(code="unknown_car_class", detail=place.get("typeName", "")))
                continue
            if car_class is None or not place.get("freeSeats"):
                continue

            # D-29: platzkart is compared without bedding; OneTwoTrip lists its bedding
            # price (tariffService) and its markup on bedding separately.
            bedding = Decimal(str(place.get("tariffService") or 0))
            bedding_markup = sum(
                Decimal(str(c.get("cost", 0)))
                for c in place.get("commissionInfo") or []
                if c.get("type") == BEDDING_MARKUP
            )
            price = to_price(Decimal(str(place["cost"])) - bedding - bedding_markup)
            extras = {"price_with_bedding": str(to_price(place["cost"]))} if bedding else {}

            offers.append(
                RawOffer(
                    **common,
                    car_class=car_class,
                    service_classes=classes,
                    price=price,
                    seats=place.get("freeSeats"),
                    url=url,
                    extras=extras,
                )
            )
            if place.get("gdsCost"):
                tariff = Decimal(str(place["gdsCost"])) - bedding
                references.append(
                    RawOffer(
                        **common,
                        car_class=car_class,
                        service_classes=classes,
                        price=to_price(math.ceil(tariff)),  # ticket.rzd.ru rounds up
                        url="https://ticket.rzd.ru/",
                    )
                )
