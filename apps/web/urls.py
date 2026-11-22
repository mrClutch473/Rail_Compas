from django.urls import path

from . import views

app_name = "web"

urlpatterns = [
    path("", views.home, name="home"),
    path("search/", views.search, name="search"),
    path("r/<slug:origin>/<slug:destination>/", views.route_calendar, name="calendar"),
    path("r/<slug:origin>/<slug:destination>/<str:day>/", views.results, name="results"),
    path("r/<slug:origin>/<slug:destination>/<str:day>/sync/", views.sync, name="sync"),
    path("sync/<int:job_id>/", views.sync_status, name="sync_status"),
    path("trip/<int:trip_id>/", views.trip_detail, name="trip"),
    path("go/<int:snapshot_id>/", views.go, name="go"),
    path("analytics/", views.analytics, name="analytics"),
    path("about/", views.about, name="about"),
    path("healthz/", views.healthz, name="healthz"),
    path("robots.txt", views.robots_txt, name="robots"),
    path("sitemap.xml", views.sitemap_xml, name="sitemap"),
]
