# ЖД Компас

Веб-сервис сравнения цен на железнодорожные билеты: один и тот же поезд на нескольких площадках продажи рядом, синхронизация цен по запросу, история цен и переход на площадку для покупки.

Выпускная квалификационная работа, ВолгГТУ, 09.03.03 «Прикладная информатика».

**Стек:** Python 3.13, Django 5.2, PostgreSQL 16, Tailwind CSS 4, htmx, Chart.js, Docker Compose.
**Площадки:** Туту, OneTwoTrip, Aviakassa.

## Запуск

Нужны Docker и `.env` (скопировать из `.env.example`, задать `DJANGO_SECRET_KEY`).

```bash
docker compose up --build
```

Сайт — http://localhost:8000, админка — http://localhost:8000/admin/. Поднимаются `db` (PostgreSQL), `web` (Django) и `worker` (фоновая синхронизация).

Фоновое расписание локально выключено (оно весь день опрашивает площадки). Включить: `SCHEDULER_ENABLED=true docker compose up -d worker`. Посмотреть, что устарело: `docker compose exec web python manage.py schedule --dry-run`.

## Разработка без Docker для Django

```bash
uv sync
docker compose up -d db
uv run python manage.py migrate
uv run python manage.py createsuperuser
uv run python manage.py runserver
```

Стили (Tailwind standalone CLI в `bin/`, не в git):

```bash
./bin/tailwindcss -i assets/css/app.css -o static/dist/app.css --watch
```

Проверки:

```bash
uv run ruff check . && uv run ruff format --check .
uv run pytest
```

## Документация

- [Роадмап](docs/ROADMAP.md)
- [Архитектура](docs/ARCHITECTURE.md)
- [Площадки](docs/SOURCES.md)
- [Решения](docs/DECISIONS.md)
