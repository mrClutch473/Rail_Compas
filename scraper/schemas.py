from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field, field_validator

from .enums import CarClass, ResultStatus


class CityRef(BaseModel):
    slug: str
    name: str = ""
    timezone: str = "Europe/Moscow"
    external_id: str
    url_slug: str = ""


class ScrapeRequest(BaseModel):
    source: str
    origin: CityRef
    destination: CityRef
    travel_date: date
    adults: int = 1

    @property
    def route_slug(self) -> str:
        return f"{self.origin.slug}-{self.destination.slug}"


class RawOffer(BaseModel):
    """One seller row normalized to our terms: one train, one car class, the cheapest price."""

    train_number: str
    raw_number: str = ""
    train_name: str = ""
    departure_at: datetime
    arrival_at: datetime
    origin_station: str = ""
    origin_station_code: str = ""
    destination_station: str = ""
    destination_station_code: str = ""
    car_class: CarClass
    service_classes: list[str] = Field(default_factory=list)
    price: Decimal
    price_old: Decimal | None = None
    seats: int | None = None
    url: str = ""
    extras: dict[str, Any] = Field(default_factory=dict)

    @field_validator("departure_at", "arrival_at")
    @classmethod
    def must_be_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("datetime must carry a timezone")
        return value


class Notice(BaseModel):
    code: str
    detail: str = ""


class ParseResult(BaseModel):
    offers: list[RawOffer] = Field(default_factory=list)
    warnings: list[Notice] = Field(default_factory=list)
    is_empty: bool = False
    # some sources also report a reference price (carrier tariff) next to their own
    reference_offers: list[RawOffer] = Field(default_factory=list)


class RawCapture(BaseModel):
    strategy: str
    url: str = ""
    payloads: list[dict[str, Any]] = Field(default_factory=list)


class ScrapeResult(BaseModel):
    source: str
    request: ScrapeRequest
    status: ResultStatus
    offers: list[RawOffer] = Field(default_factory=list)
    reference_offers: list[RawOffer] = Field(default_factory=list)
    warnings: list[Notice] = Field(default_factory=list)
    error: str = ""
    strategy: str = ""
    attempts: int = 0
    duration_ms: int = 0
    raw_ref: str = ""
    proxy_used: bool = False
    started_at: datetime
    finished_at: datetime

    @property
    def is_success(self) -> bool:
        return self.status in (ResultStatus.OK, ResultStatus.EMPTY)
