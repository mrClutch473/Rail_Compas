from django.urls import path

from . import views

app_name = "web"

urlpatterns = [
    path("", views.home, name="home"),
    path("search/", views.search, name="search"),
    path("r/<slug:origin>/<slug:destination>/<str:day>/", views.results, name="results"),
    path("r/<slug:origin>/<slug:destination>/<str:day>/sync/", views.sync, name="sync"),
    path("sync/<int:job_id>/", views.sync_status, name="sync_status"),
    path("go/<int:snapshot_id>/", views.go, name="go"),
    path("about/", views.about, name="about"),
    path("healthz/", views.healthz, name="healthz"),
]
