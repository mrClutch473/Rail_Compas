from pathlib import Path

import environ
from django.templatetags.static import static
from django.urls import reverse_lazy

BASE_DIR = Path(__file__).resolve().parents[2]

env = environ.Env(
    DJANGO_DEBUG=(bool, False),
    DJANGO_ALLOWED_HOSTS=(list, []),
    DJANGO_CSRF_TRUSTED_ORIGINS=(list, []),
    DJANGO_ADMIN_URL=(str, "admin/"),
)
environ.Env.read_env(BASE_DIR / ".env", overwrite=False)

SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = env("DJANGO_DEBUG")
ALLOWED_HOSTS = env("DJANGO_ALLOWED_HOSTS")
CSRF_TRUSTED_ORIGINS = env("DJANGO_CSRF_TRUSTED_ORIGINS")
ADMIN_URL = env("DJANGO_ADMIN_URL")

INSTALLED_APPS = [
    "unfold",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "apps.core",
    "apps.catalog",
    "apps.offers",
    "apps.sync",
    "apps.web",
    "apps.analytics",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

DATABASES = {"default": env.db("DATABASE_URL")}
DATABASES["default"]["CONN_MAX_AGE"] = 60
if DATABASES["default"]["ENGINE"].endswith("postgresql"):
    DATABASES["default"].setdefault("OPTIONS", {})["connect_timeout"] = 5

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "ru"
TIME_ZONE = "Europe/Moscow"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

# Shared by all gunicorn workers and the worker: rate limits and page data (apps/core/cache.py)
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.db.DatabaseCache",
        "LOCATION": "django_cache",
    }
}

SYNC_COOLDOWN_SECONDS = env.int("SYNC_COOLDOWN_SECONDS", default=300)
SYNC_JOB_TIMEOUT_SECONDS = env.int("SYNC_JOB_TIMEOUT_SECONDS", default=180)
SYNC_STALE_MINUTES = env.int("SYNC_STALE_MINUTES", default=10)
WORKER_SLOTS = env.int("WORKER_SLOTS", default=3)
STALE_AFTER_HOURS = env.int("STALE_AFTER_HOURS", default=24)

SCHEDULER_ENABLED = env.bool("SCHEDULER_ENABLED", default=True)
SCHEDULE_TICK_SECONDS = env.int("SCHEDULE_TICK_SECONDS", default=60)
SCHEDULE_HOT_DAYS = env.int("SCHEDULE_HOT_DAYS", default=7)
SCHEDULE_HOT_HOURS = env.int("SCHEDULE_HOT_HOURS", default=3)
SCHEDULE_WARM_HOURS = env.int("SCHEDULE_WARM_HOURS", default=12)
SCHEDULE_MAX_QUEUED = env.int("SCHEDULE_MAX_QUEUED", default=10)

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
FORMS_URLFIELD_ASSUME_HTTPS = True

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "plain": {"format": "{asctime} {levelname:<7} {name}: {message}", "style": "{"},
    },
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"}},
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {"django.db.backends": {"level": "WARNING"}},
}


def _nav(title, icon, url):
    return {"title": title, "icon": icon, "link": reverse_lazy(url)}


UNFOLD = {
    "SITE_TITLE": "ЖД Компас",
    "SITE_HEADER": "ЖД Компас",
    "SITE_SUBHEADER": "Управление сервисом",
    "SITE_URL": "/",
    "SITE_ICON": lambda request: static("img/favicon.svg"),
    "SITE_FAVICONS": [
        {"rel": "icon", "type": "image/svg+xml", "href": lambda request: static("img/favicon.svg")}
    ],
    "SHOW_VIEW_ON_SITE": True,
    "STYLES": [lambda request: static("css/admin.css")],
    "DASHBOARD_CALLBACK": "apps.web.dashboard.dashboard_callback",
    "ENVIRONMENT": "apps.web.dashboard.environment_callback",
    "COLORS": {
        "primary": {
            "50": "#eef3ff",
            "100": "#dbe5ff",
            "200": "#bfd0ff",
            "300": "#93b1ff",
            "400": "#6189fb",
            "500": "#3d68f2",
            "600": "#2557e8",
            "700": "#1d45c4",
            "800": "#1d3b9c",
            "900": "#1e357b",
            "950": "#14214a",
        },
    },
    "SIDEBAR": {
        "show_search": True,
        "show_all_applications": False,
        "navigation": [
            {
                "title": "Обзор",
                "items": [_nav("Сводка", "dashboard", "admin:index")],
            },
            {
                "title": "Сбор данных",
                "items": [
                    _nav("Задания синхронизации", "sync", "admin:sync_syncjob_changelist"),
                    _nav("Задачи площадок", "task_alt", "admin:sync_synctask_changelist"),
                ],
            },
            {
                "title": "Данные",
                "items": [
                    _nav("Рейсы", "train", "admin:offers_trip_changelist"),
                    _nav("Снимки цен", "payments", "admin:offers_pricesnapshot_changelist"),
                    _nav("Переходы на площадки", "ads_click", "admin:web_clickout_changelist"),
                ],
            },
            {
                "title": "Справочники",
                "items": [
                    _nav("Направления", "route", "admin:catalog_route_changelist"),
                    _nav("Города", "location_city", "admin:catalog_city_changelist"),
                    _nav("Вокзалы", "home_work", "admin:catalog_station_changelist"),
                    _nav("Площадки", "storefront", "admin:catalog_source_changelist"),
                ],
            },
            {
                "title": "Доступ",
                "items": [
                    _nav("Пользователи", "person", "admin:auth_user_changelist"),
                    _nav("Группы", "group", "admin:auth_group_changelist"),
                ],
            },
        ],
    },
}
