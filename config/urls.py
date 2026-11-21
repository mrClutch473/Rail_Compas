from django.conf import settings
from django.contrib import admin
from django.urls import include, path

admin.site.site_header = "ЖД Компас"
admin.site.site_title = "ЖД Компас"
admin.site.index_title = "Управление"

urlpatterns = [
    path(settings.ADMIN_URL, admin.site.urls),
    path("", include("apps.web.urls")),
]
