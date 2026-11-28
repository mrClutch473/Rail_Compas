from datetime import date, timedelta
from urllib.parse import urlsplit

from django.contrib import messages
from django.core.cache import cache
from django.db import connection
from django.db.models import F
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.analytics.services import cached_report
from apps.catalog.models import City, Route, Source
from apps.core.cache import cached
from apps.core.enums import ACTIVE_JOB_STATUSES, CarClass, SyncTrigger
from apps.offers.models import PriceSnapshot, Trip
from apps.offers.selectors import current_snapshots, source_states
from apps.sync.models import SyncJob
from apps.sync.services import request_sync, sources_for_route

from .models import ClickOut
from .presenters import (
    DEPARTURE_WINDOWS,
    SALE_DAYS,
    SORTS,
    Filters,
    apply_filters,
    build_trains,
    date_strip,
    freshest,
    price_calendar,
    trip_history,
)
from .warm import popular

SYNC_CLICKS_PER_WINDOW = 10
SYNC_WINDOW_SECONDS = 600
QUEUE_LIMIT = 30


def synced_marks(states) -> list[str]:
    """Cache key part that changes whenever any seller of this route and date gets new prices."""
    return [f"{s.source.pk}-{s.synced_at.timestamp() if s.synced_at else 0}" for s in states]


def home(request):
    tomorrow = timezone.localdate() + timedelta(days=1)
    return render(
        request,
        "web/home.html",
        {
            "cities": list(City.objects.filter(is_active=True).values("slug", "name")),
            "popular": popular(),
            "default_date": tomorrow,
            "max_date": timezone.localdate() + timedelta(days=SALE_DAYS),
            "sellers": Source.objects.filter(is_active=True, is_reference=False),
        },
    )


def search(request):
    origin, destination = request.GET.get("from", ""), request.GET.get("to", "")
    try:
        day = date.fromisoformat(request.GET.get("date", ""))
    except ValueError:
        day = timezone.localdate() + timedelta(days=1)
    if not origin or not destination or origin == destination:
        messages.error(request, "Выберите два разных города.")
        return redirect("web:home")
    return redirect("web:results", origin=origin, destination=destination, day=day.isoformat())


def _parse_day(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise Http404("bad date") from None


def results(request, origin: str, destination: str, day: str):
    travel_date = _parse_day(day)
    today = timezone.localdate()
    if travel_date < today:
        return redirect("web:results", origin=origin, destination=destination, day=today)
    origin_city = get_object_or_404(City, slug=origin, is_active=True)
    destination_city = get_object_or_404(City, slug=destination, is_active=True)
    if origin_city == destination_city:
        return redirect("web:home")

    context = {
        "origin": origin_city,
        "destination": destination_city,
        "travel_date": travel_date,
        "cities": list(City.objects.filter(is_active=True).values("slug", "name")),
        "max_date": today + timedelta(days=SALE_DAYS),
    }
    if travel_date > context["max_date"]:
        return render(request, "web/results.html", {**context, "too_far": True})

    route = Route.objects.filter(origin=origin_city, destination=destination_city).first()
    if route is None:
        probe = Route(origin=origin_city, destination=destination_city)
        if not sources_for_route(probe):
            return render(request, "web/results.html", {**context, "unsupported": True})
        route, _ = Route.objects.get_or_create(origin=origin_city, destination=destination_city)

    Route.objects.filter(pk=route.pk).update(
        search_count=F("search_count") + 1, last_searched_at=timezone.now()
    )
    states = source_states(route, travel_date)
    job = SyncJob.objects.filter(
        route=route, travel_date=travel_date, status__in=ACTIVE_JOB_STATUSES
    ).first()
    if job is None and all(s.synced_at is None for s in states):
        job = request_sync(route, travel_date, SyncTrigger.USER).job

    sellers = [s.source for s in states]
    all_trains = cached(
        "trains",
        route.pk,
        travel_date,
        *synced_marks(states),
        build=lambda: build_trains(route, travel_date, sellers),
    )
    filters = Filters.from_query(request.GET)
    present = {row.car_class for train in all_trains for row in train.classes}
    context.update(
        route=route,
        states=states,
        sellers=sellers,
        job=job,
        trains=apply_filters(all_trains, filters),
        total_trains=len(all_trains),
        filters=filters,
        class_options=[c for c in CarClass if c.value in present],
        departure_options=[(k, label) for k, (_, _, label) in DEPARTURE_WINDOWS.items()],
        sort_options=SORTS.items(),
        hour_options=[12, 18, 24, 36],
        query=request.GET.urlencode(),
        days=cached(
            "strip", route.pk, travel_date, build=lambda: date_strip(route, travel_date), ttl=300
        ),
        synced_at=freshest(states),
    )
    return render(request, "web/results.html", context)


def _client_ip(request) -> str:
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    return forwarded.split(",")[0].strip() or request.META.get("REMOTE_ADDR", "")


def _too_many_clicks(request) -> bool:
    key = f"sync-clicks:{_client_ip(request)}"
    cache.add(key, 0, SYNC_WINDOW_SECONDS)
    return cache.incr(key) > SYNC_CLICKS_PER_WINDOW


@require_POST
def sync(request, origin: str, destination: str, day: str):
    travel_date = _parse_day(day)
    route = get_object_or_404(
        Route.objects.select_related("origin", "destination"),
        origin__slug=origin,
        destination__slug=destination,
    )
    notice = ""
    job = None
    queued = SyncJob.objects.filter(status__in=ACTIVE_JOB_STATUSES).count()
    if _too_many_clicks(request):
        notice = "Слишком много обновлений подряд. Попробуйте через несколько минут."
    elif queued >= QUEUE_LIMIT:
        notice = "Сейчас много запросов. Попробуйте через минуту."
    else:
        outcome = request_sync(route, travel_date, SyncTrigger.USER)
        job = outcome.job if outcome.outcome != "fresh" else None
        if outcome.outcome == "fresh":
            notice = "Цены только что обновлены — новые данные уже на странице."
    return _sync_panel(request, route, travel_date, job, notice)


def sync_status(request, job_id: int):
    job = get_object_or_404(SyncJob.objects.select_related("route"), pk=job_id)
    if job.status not in ACTIVE_JOB_STATUSES:
        response = HttpResponse(status=204)
        response["HX-Refresh"] = "true"
        return response
    return _sync_panel(request, job.route, job.travel_date, job, "")


def _sync_panel(request, route, travel_date, job, notice):
    states = source_states(route, travel_date)
    tasks = {}
    if job is not None:
        tasks = {t.source_id: t for t in job.tasks.all()}
    return render(
        request,
        "web/_sync_panel.html",
        {
            "route": route,
            "origin": route.origin,
            "destination": route.destination,
            "travel_date": travel_date,
            "states": states,
            "job": job,
            "tasks": tasks,
            "notice": notice,
            "synced_at": freshest(states),
        },
    )


def go(request, snapshot_id: int):
    snapshot = get_object_or_404(
        PriceSnapshot.objects.select_related("source", "trip__route"), pk=snapshot_id
    )
    if snapshot.source.is_reference:
        raise Http404("reference prices are not sold")
    trip = snapshot.trip
    rivals = [
        s.price
        for s in current_snapshots(trip.route, trip.departure_date)
        if s.trip_id == trip.pk and s.car_class == snapshot.car_class and not s.source.is_reference
    ]
    ClickOut.objects.create(
        trip=trip,
        source=snapshot.source,
        car_class=snapshot.car_class,
        price_shown=snapshot.price,
        was_cheapest=not rivals or snapshot.price <= min(rivals),
    )
    return redirect(_safe_target(snapshot))


def _safe_target(snapshot: PriceSnapshot) -> str:
    allowed = urlsplit(snapshot.source.base_url).hostname or ""
    host = urlsplit(snapshot.url).hostname or ""
    root = allowed.removeprefix("www.")
    if host == root or host.endswith("." + root):
        return snapshot.url
    return snapshot.source.base_url


def about(request):
    return render(
        request,
        "web/about.html",
        {"sources": Source.objects.filter(is_active=True).order_by("is_reference", "priority")},
    )


def healthz(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except Exception:
        return JsonResponse({"status": "error", "db": False}, status=503)
    return JsonResponse({"status": "ok", "db": True})


def trip_detail(request, trip_id: int):
    trip = get_object_or_404(
        Trip.objects.select_related(
            "route__origin", "route__destination", "origin_station", "destination_station"
        ),
        pk=trip_id,
    )
    route = trip.route
    states = source_states(route, trip.departure_date)
    sellers = [s.source for s in states]
    trains = cached(
        "trains",
        route.pk,
        trip.departure_date,
        *synced_marks(states),
        build=lambda: build_trains(route, trip.departure_date, sellers),
    )
    train = next((t for t in trains if t.trip.pk == trip.pk), None)
    history = cached(
        "history", trip.pk, *synced_marks(states), build=lambda: trip_history(trip, sellers)
    )
    chart = {h.car_class: {"label": h.label, "series": h.series} for h in history}
    selected = request.GET.get("class") or (history[0].car_class if history else "")
    return render(
        request,
        "web/trip.html",
        {
            "trip": trip,
            "origin": route.origin,
            "destination": route.destination,
            "travel_date": trip.departure_date,
            "train": train,
            "sellers": sellers,
            "states": states,
            "history": history,
            "chart": chart,
            "selected": selected,
            "synced_at": freshest(states),
        },
    )


def route_calendar(request, origin: str, destination: str):
    route = get_object_or_404(
        Route.objects.select_related("origin", "destination"),
        origin__slug=origin,
        destination__slug=destination,
    )
    months, cheapest = cached("calendar", route.pk, build=lambda: price_calendar(route), ttl=300)
    return render(
        request,
        "web/calendar.html",
        {
            "route": route,
            "origin": route.origin,
            "destination": route.destination,
            "months": months,
            "cheapest": cheapest,
            "weekdays": ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"],
        },
    )


ANALYTICS_PERIODS = {1: "сутки", 7: "неделя", 30: "месяц"}


def analytics(request):
    try:
        days = int(request.GET.get("days", 7))
    except ValueError:
        days = 7
    if days not in ANALYTICS_PERIODS:
        days = 7
    report = cached_report(days)
    widest = max((s.avg_markup or 0 for s in report.sellers), default=0) or 1
    return render(
        request,
        "web/analytics.html",
        {"report": report, "periods": ANALYTICS_PERIODS.items(), "widest_markup": widest},
    )


def robots_txt(request):
    lines = [
        "User-agent: *",
        "Disallow: /go/",
        "Disallow: /sync/",
        "Disallow: /search/",
        f"Sitemap: {request.build_absolute_uri('/sitemap.xml')}",
    ]
    return HttpResponse("\n".join(lines) + "\n", content_type="text/plain")


def sitemap_xml(request):
    pages = [reverse("web:home"), reverse("web:analytics"), reverse("web:about")]
    for route in Route.objects.filter(is_scheduled=True).select_related("origin", "destination"):
        pages.append(reverse("web:calendar", args=[route.origin.slug, route.destination.slug]))
    urls = "".join(f"<url><loc>{request.build_absolute_uri(path)}</loc></url>" for path in pages)
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>'
    )
    return HttpResponse(body, content_type="application/xml")
