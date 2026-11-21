"""Scraper command line, works without Django and the database.

python -m scraper one --source tutu --from volgograd --to moskva --date 2026-12-05
python -m scraper replay data/raw/tutu/2026-11-21/volgograd-moskva_20261205_101500.json.gz
python -m scraper fixture <raw file> regular
python -m scraper status
"""

import argparse
import asyncio
import logging
import shutil
import sys
from datetime import date
from pathlib import Path

from .adapters import get_adapter
from .config import ROOT, ScraperConfig
from .refs import city_refs
from .runner import Runner
from .schemas import ScrapeRequest, ScrapeResult
from .storage import cleanup_raw, load_raw
from .throttle import Throttle
from .validate import validate_offers

FIXTURES = ROOT / "tests" / "scraper" / "fixtures"


def print_offers(offers, title: str = "") -> None:
    if title:
        print(title)
    for o in sorted(offers, key=lambda o: (o.departure_at, o.train_number, o.price)):
        old = f" (было {o.price_old})" if o.price_old else ""
        seats = f", мест {o.seats}" if o.seats is not None else ""
        print(
            f"  {o.train_number:<5} {o.departure_at:%d.%m %H:%M} → {o.arrival_at:%d.%m %H:%M}"
            f"  {o.car_class:<9} {o.price:>9} ₽{old}{seats}"
        )


def print_result(result: ScrapeResult) -> None:
    print(
        f"\n{result.source}: {result.status}  offers={len(result.offers)}  "
        f"attempts={result.attempts}  {result.duration_ms} ms  strategy={result.strategy}"
    )
    if result.error:
        print(f"error: {result.error}")
    for notice in result.warnings:
        print(f"warning [{notice.code}] {notice.detail}")
    print_offers(result.offers)
    if result.reference_offers:
        print_offers(result.reference_offers, "reference prices:")
    if result.raw_ref:
        print(f"raw: data/raw/{result.raw_ref}")


def cmd_one(args) -> int:
    config = ScraperConfig()
    try:
        adapter = get_adapter(args.source)
    except KeyError as exc:
        print(exc.args[0], file=sys.stderr)
        return 2
    refs = city_refs(args.source)
    missing = [slug for slug in (args.origin, args.destination) if slug not in refs]
    if missing:
        print(f"{args.source} has no IDs for: {', '.join(missing)}", file=sys.stderr)
        return 2
    request = ScrapeRequest(
        source=args.source,
        origin=refs[args.origin],
        destination=refs[args.destination],
        travel_date=date.fromisoformat(args.date),
    )
    runner = Runner(config, Throttle(config.state_file), save_raw_files=not args.no_raw)
    result = asyncio.run(runner.run(adapter, request, priority=True, use_proxy=args.proxy))
    if args.json:
        print(result.model_dump_json(indent=2))
    else:
        print_result(result)
    return 0 if result.is_success else 1


def cmd_replay(args) -> int:
    request, capture = load_raw(Path(args.path))
    parsed = get_adapter(request.source).parse(capture, request)
    offers, notices, too_many = validate_offers(parsed.offers, request)
    print(f"{request.source} {request.route_slug} {request.travel_date}: {len(offers)} offers")
    for notice in [*parsed.warnings, *notices]:
        print(f"warning [{notice.code}] {notice.detail}")
    if too_many:
        print("too many rows dropped — the format probably changed")
    print_offers(offers)
    if parsed.reference_offers:
        print_offers(parsed.reference_offers, "reference prices:")
    return 0


def cmd_fixture(args) -> int:
    source_path = Path(args.path)
    request, _ = load_raw(source_path)
    target = FIXTURES / request.source / f"{args.name}.json.gz"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source_path, target)
    print(f"saved {target.relative_to(ROOT)}")
    return 0


def cmd_status(args) -> int:
    config = ScraperConfig()
    throttle = Throttle(config.state_file)
    for source in ("tutu", "onetwotrip", "aviakassa"):
        until = throttle.cooldown_until(source)
        cooldown = f"cooling down until {until:%H:%M} UTC" if until else "ok"
        print(f"{source:<11} {cooldown}")
    print(f"proxies configured: {len(config.proxies)}")
    return 0


def cmd_cleanup(args) -> int:
    config = ScraperConfig()
    removed = cleanup_raw(config.raw_dir, config.raw_retention_days)
    print(f"removed {removed} old raw folders")
    return 0


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
    parser = argparse.ArgumentParser(prog="python -m scraper")
    sub = parser.add_subparsers(dest="command", required=True)

    one = sub.add_parser("one", help="scrape one source, route and date")
    one.add_argument("--source", required=True)
    one.add_argument("--from", dest="origin", required=True, help="city slug, e.g. volgograd")
    one.add_argument("--to", dest="destination", required=True)
    one.add_argument("--date", required=True, help="YYYY-MM-DD")
    one.add_argument("--proxy", action="store_true", help="go through SCRAPER_PROXIES")
    one.add_argument("--no-raw", action="store_true", help="do not keep the raw response")
    one.add_argument("--json", action="store_true")
    one.set_defaults(func=cmd_one)

    replay = sub.add_parser("replay", help="parse a stored raw response again")
    replay.add_argument("path")
    replay.set_defaults(func=cmd_replay)

    fixture = sub.add_parser("fixture", help="copy a raw response into test fixtures")
    fixture.add_argument("path")
    fixture.add_argument("name")
    fixture.set_defaults(func=cmd_fixture)

    sub.add_parser("status", help="cooldowns and proxies").set_defaults(func=cmd_status)
    sub.add_parser("cleanup", help="delete old raw responses").set_defaults(func=cmd_cleanup)

    args = parser.parse_args(argv)
    return args.func(args)
