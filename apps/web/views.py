from datetime import date, timedelta
from urllib.parse import urlsplit

from django.contrib import messages
from django.core.cache import cache
from django.db import connection
from django.db.models import F
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.catalog.models import City, Route, Source
from apps.core.enums import ACTIVE_JOB_STATUSES, SyncTrigger
from apps.offers.models import PriceSnapshot
from apps.offers.selectors import current_snapshots, source_states
from apps.sync.models import SyncJob
from apps.sync.services import request_sync, sources_for_route

from .models import ClickOut
from .presenters import SALE_DAYS, build_trains, date_strip, freshest, popular_routes

SYNC_CLICKS_PER_WINDOW = 10
SYNC_WINDOW_SECONDS = 600
QUEUE_LIMIT = 30


def home(request):
    tomorrow = timezone.localdate() + timedelta(days=1)
    return render(
        request,
        "web/home.html",
        {
            "cities": list(City.objects.filter(is_active=True).values("slug", "name")),
            "popular": popular_routes(),
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
    context.update(
        route=route,
        states=states,
        sellers=sellers,
        job=job,
        trains=build_trains(route, travel_date, sellers),
        days=date_strip(route, travel_date),
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
