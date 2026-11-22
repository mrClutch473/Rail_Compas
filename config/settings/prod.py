from .base import *  # noqa: F403

DEBUG = False

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
# Until the domain and HTTPS are set up (roadmap phase 14) the site runs on plain HTTP
# behind an SSH tunnel, so these are switchable.
SECURE_SSL_REDIRECT = env.bool("DJANGO_SECURE_SSL_REDIRECT", default=True)  # noqa: F405
SESSION_COOKIE_SECURE = env.bool("DJANGO_SECURE_COOKIES", default=True)  # noqa: F405
CSRF_COOKIE_SECURE = SESSION_COOKIE_SECURE
SECURE_HSTS_SECONDS = env.int("DJANGO_HSTS_SECONDS", default=3600)  # noqa: F405
SECURE_HSTS_INCLUDE_SUBDOMAINS = False
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
