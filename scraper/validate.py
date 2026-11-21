from datetime import timedelta
from decimal import Decimal

from .normalize import TRAIN_NUMBER
from .schemas import Notice, RawOffer, ScrapeRequest

MIN_PRICE = Decimal("100")
MAX_PRICE = Decimal("300000")
MIN_TRIP = timedelta(minutes=10)
MAX_TRIP = timedelta(days=7)
MAX_SEATS = 1000
MAX_DROPPED_SHARE = 0.3


def check_offer(offer: RawOffer, request: ScrapeRequest) -> str | None:
    """Return a reason to drop the row, or None if it looks sane."""
    if not TRAIN_NUMBER.match(offer.train_number):
        return f"train number {offer.train_number!r}"
    if not MIN_PRICE <= offer.price <= MAX_PRICE:
        return f"price {offer.price}"
    if offer.price_old is not None and offer.price_old <= offer.price:
        offer.price_old = None
    trip = offer.arrival_at - offer.departure_at
    if not MIN_TRIP <= trip <= MAX_TRIP:
        return f"trip duration {trip}"
    days_off = abs((offer.departure_at.date() - request.travel_date).days)
    if days_off > 1:
        return f"departs {offer.departure_at.date()} for {request.travel_date}"
    if offer.seats is not None and not 0 <= offer.seats <= MAX_SEATS:
        return f"seats {offer.seats}"
    return None


def validate_offers(
    offers: list[RawOffer], request: ScrapeRequest
) -> tuple[list[RawOffer], list[Notice], bool]:
    """Drop insane rows with a warning each. Third value: too much was dropped to trust the rest."""
    kept, notices = [], []
    for offer in offers:
        reason = check_offer(offer, request)
        if reason:
            notices.append(Notice(code="dropped_row", detail=f"{offer.train_number}: {reason}"))
        else:
            kept.append(offer)
    too_many = bool(offers) and (len(offers) - len(kept)) / len(offers) > MAX_DROPPED_SHARE
    return kept, notices, too_many
