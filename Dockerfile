FROM debian:bookworm-slim AS css
ARG TAILWIND_VERSION=v4.3.5
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN curl -fsSL -o /usr/local/bin/tailwindcss \
    "https://github.com/tailwindlabs/tailwindcss/releases/download/${TAILWIND_VERSION}/tailwindcss-linux-x64" \
    && chmod +x /usr/local/bin/tailwindcss
WORKDIR /src
COPY assets assets
COPY templates templates
COPY apps apps
RUN tailwindcss -i assets/css/app.css -o static/dist/app.css --minify


FROM python:3.13-slim AS app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

RUN pip install --no-cache-dir uv==0.13.9 \
    && useradd --create-home --uid 1000 app

WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-default-groups --no-install-project

COPY . .
COPY --from=css /src/static/dist static/dist
RUN DJANGO_SECRET_KEY=build DATABASE_URL=sqlite:///:memory: \
    python manage.py collectstatic --noinput --settings=config.settings.prod \
    && mkdir -p /data && chown -R app:app /app /data

USER app
EXPOSE 8000
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--access-logfile", "-"]
