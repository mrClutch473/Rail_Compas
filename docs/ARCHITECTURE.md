# Архитектура «ЖД Компас»

Сжатое описание для работы. Решения и их причины — [DECISIONS.md](DECISIONS.md).

## Продукт в двух фразах

Сравнение цен одного и того же поезда на разных площадках продажи ЖД билетов (Туту, Ozon Travel, +1). Пользователь ищет «откуда → куда → дата», видит поезда, классы вагонов и цены каждой площадки, может синхронизировать данные прямо сейчас и уйти на площадку за покупкой.

## Сервисы

Всё на одном сервере, Docker Compose. Локально — тот же compose.

| Сервис | Что делает |
|---|---|
| `web` | Django: сайт, админка, постановка заданий синхронизации. Gunicorn в prod |
| `worker` | `manage.py run_worker`: очередь заданий, расписание, Playwright + адаптеры, запись в БД |
| `db` | PostgreSQL 16: данные и очередь |
| `nginx` (prod) | HTTPS, статика |

Образ один на `web` и `worker`: сборка CSS (Tailwind CLI) + `python:3.13-slim`, без браузера (D-28). Сервер: HOSTKEY, Подольск, 2 vCPU / 2 ГБ + 2 ГБ swap, `/opt/railcompas`. Локально БД на порту 5433.

## Код

```
config/              settings/{base,dev,prod,test}.py, urls
apps/core/           перечисления, утилиты времени и форматов
apps/catalog/        City, Station, Source, SourceCity, Route
apps/offers/         Trip, PriceSnapshot, selectors (выдача, история, свежесть)
apps/sync/           SyncJob, SyncTask, request_sync, worker, scheduler, ingest (сопоставление и запись)
apps/web/            страницы, htmx-фрагменты, ClickOut, templatetags
apps/analytics/      расчёты разницы цен
scraper/             БЕЗ Django: adapters/, browser.py, capture.py, throttle.py, errors.py, normalize/, validate.py, schemas.py, cli.py
assets/css/          исходник Tailwind (app.css, токены тем)
templates/, static/  dist/ (собранный CSS), vendor/ (htmx, Chart.js), img/
seed/                YAML справочников
tests/               scraper/ (fixtures/<source>/), apps/, e2e/
recon/, data/        дампы разведки и сырые ответы (не в git)
```

Граница: `scraper` получает `ScrapeRequest` и возвращает `ScrapeResult` (pydantic), про БД ничего не знает. `apps/sync` переводит модели Django в запрос и записывает результат.

## Синхронизация

### Поток

```
кнопка / автозапуск / расписание / админка
  → request_sync(route, date, trigger)
      если есть SyncJob(queued|running) на route+date → вернуть его
      если все площадки успешно синхронизированы < 5 мин назад → не создавать (cooldown)
      иначе SyncJob(queued) + SyncTask(pending) на каждую активную площадку
  → worker забирает job: SELECT ... FOR UPDATE SKIP LOCKED, порядок priority, created_at
  → задачи площадок параллельно; к одной площадке строго одна загрузка за раз + пауза
      scraper: fetch → raw → parse → normalize → validate → ScrapeResult
      ingest: одна транзакция на SyncTask: Trip get_or_create, Station по псевдонимам, PriceSnapshot пачкой
  → job done / partial / failed
страница опрашивает /sync/<job>/status/ каждые 2 с (htmx) → по окончании перерисовывает выдачу
```

### Приоритеты и лимиты

| Параметр | Значение по умолчанию |
|---|---|
| Приоритет | user 0 > admin 10 > schedule 50 |
| Cooldown маршрут+дата | 5 мин |
| Слотов заданий в воркере | 3 |
| Тайм-аут задачи площадки | 45 с; задания — 3 мин |
| Пауза между загрузками площадки | Туту 5 с, Ozon 10 с (+0–50% случайно), в `Source` |
| Дневной бюджет площадки | в `Source`; расписание останавливается первым, пользователь последним |
| Кнопка с одного IP | 10 заданий / 10 мин (кэш Django, IP в БД не храним) |
| Предел очереди | 30 заданий в `queued` → «попробуйте через минуту» |
| Зависшее задание | `running` дольше 10 мин → `failed` при старте воркера |

### Расписание (внутри воркера, раз в минуту)

Для `Route.is_scheduled`: даты 0–7 дней — если последняя успешная синхронизация старше 3 ч; 8–горизонт — старше 12 ч. Ставит задания с приоритетом `schedule`, не больше N за проход.

### Что видит пользователь

| Состояние route+date | Показ |
|---|---|
| Никогда не синхронизировалось | Автозапуск синхронизации, индикатор по площадкам |
| Есть данные | Выдача + «обновлено N мин назад» по каждой площадке + кнопка «Синхронизировать» |
| Синхронизация идёт | Прогресс «Туту ✓ · Ozon …», старая выдача остаётся видна |
| Успешно, поездов нет | «Поездов на эту дату нет» + повтор |
| Площадка упала / заблокирована | Её старые цены приглушены: «Ozon временно недоступен, данные от …» |
| Данные площадки старше 24 ч | Приглушены, подпись времени |

## Парсер (`scraper/`)

### Адаптер

```python
class SourceAdapter(ABC):
    code: ClassVar[str]
    async def fetch(self, ctx: FetchContext, req: ScrapeRequest) -> RawCapture: ...  # единственная сеть
    # ctx.http — httpx-клиент площадки (прокси, сессия); await ctx.page() — браузер, только если нужен
    def parse(self, raw: RawCapture, req: ScrapeRequest) -> ParseResult: ...  # чистая функция
    def offer_url(self, req: ScrapeRequest) -> str: ...
```

`parse` без сети и диска → тесты на фикстурах, `scrape replay` по сырым ответам, починка без запросов к площадке. Реестр `ADAPTERS = {"tutu": TutuAdapter, ...}`, ключ = `Source.code`.

### Стратегии получения (по убыванию надёжности)

0. Прямой HTTP-запрос к внутреннему API площадки без браузера, если он работает (Туту — да). Быстро, мало памяти; браузер поднимается лениво только для запасного пути.
1. Сеть в браузере: `page.on("websocket")` → `framereceived`, `page.on("response")` для JSON.
2. Состояние страницы: `__NEXT_DATA__`, `__NUXT__`.
3. DOM: `data-*`, семантика, в последнюю очередь классы.

Не первая стратегия → предупреждение `fallback_strategy`. Конец выдачи: явный маркер протокола > тишина 3 с после данных > признак на странице > тайм-аут.

### Сеть

`scraper/transport.py`: `httpx.AsyncClient` на задачу, обычный UA и заголовки площадки (`adapter.headers`), тайм-аут запроса 20 с, задачи 45 с. `FetchContext.get_json/post_json` записывает каждый ответ в `payloads` (уходит в сырой файл). 403/429 или HTML с признаками капчи → `BlockedError`; не JSON → `LayoutChanged`; 5xx → `NetworkError`. Прокси: `Source.use_proxy` → случайный из `SCRAPER_PROXIES`. Браузер (Playwright) не используется — запасной путь в `FUTURE` (4.6).

### Ошибки

| Класс | Повтор | Статус задачи |
|---|---|---|
| `NetworkError` | 3 раза, 5 → 15 → 45 с | `network` |
| `FetchTimeout` | 1 раз | `timeout` |
| `BlockedError` (капча, 429, явная блокировка) | нет; другой прокси, иначе охлаждение площадки 1 ч | `blocked` |
| `LayoutChanged` (данных нет ни одной стратегией) | нет | `layout_changed` |
| `ParseError` (данные не по схеме) | нет | `invalid` |
| Площадка сообщила «поездов нет» | — | `empty` (успех) |

Капчи не решаем никак. Признаки: SmartCaptcha, recaptcha, hcaptcha, «похожи на автоматические».

### Проверки

Строка отбрасывается с предупреждением: номер не `^\d{3}[А-ЯЁ]$`; цена вне 100–300 000 ₽; `price_old <= price`; прибытие не позже отправления; в пути < 10 мин или > 7 сут; дата отправления дальше ±1 сут от запрошенной; неизвестный класс (`unknown_car_class` с исходным текстом). Задача `invalid`, если отброшено > 30% строк. На стороне ingest: поездов меньше половины от прошлой успешной задачи → `offers_drop`.

### Отладка

Сырые ответы `data/raw/<source>/<date>/<route>_<travel>_<time>.json.gz`, 14 дней. CLI без Django и БД (ID городов из `seed/reference.yaml`): `python -m scraper one --source S --from volgograd --to moskva --date YYYY-MM-DD [--proxy] [--json]`, `replay <raw>`, `fixture <raw> <name>` → `tests/scraper/fixtures/<source>/`, `status`, `cleanup`. Разведка: `python -m scraper.recon.probe|search|shape|resolve_cities`. Django: `manage.py sync <from> <to> <date>` (фаза 6).

### Если площадка сломалась

`blocked` → увеличить паузу, уменьшить бюджет, включить прокси; постоянная капча → выключить площадку. `layout_changed`/`invalid` → `python -m scraper one` → `scraper.recon.probe` по странице площадки → сравнить с фикстурой → новая фикстура + падающий тест → правка `parse` → `replay` на свежих сырых.

## Нормализация

- **Номер поезда:** убрать `№`, пробелы, `*`; латинские двойники → кириллица (`A→А, B→В, E→Е, K→К, M→М, H→Н, O→О, P→Р, C→С, T→Т, X→Х, Y→У`), транслит (`ZH→Ж` и т.п.); верхний регистр; цифры дополнить до 3. Звёздочку (`357*С`) отбрасываем. Двойная нумерация подтверждена: одна площадка пишет `357С`, другая `358С` для одного поезда — см. склейку.
- **Класс вагона:** `seated` (сидячий, эконом, базовый), `platzkart`, `coupe`, `sv` (СВ, спальный), `lux` (люкс, мягкий, гранд). Код обслуживания (`2К`, `1Б`…) приоритетнее названия, если площадка его отдаёт. Словарь пополняется по `unknown_car_class`.
- **Цена:** `Decimal`, рубли, один взрослый. Скидка, уже применённая на витрине, учитывается; зачёркнутая → `price_old`. Кешбэк и баллы не вычитаем, в `extras`. Сбор включён или нет — свойство площадки `Source.fee_policy`.
- **Время:** площадки показывают местное время станции [проверить в разведке]. Привязка к поясу: отправление — пояс города отправления, прибытие — города прибытия. Хранение UTC. `Trip.departure_date` — по местному времени отправления. Время в пути считаем сами.
- **Вокзал:** нижний регистр, без «вокзал», скобок, `ё→е`; поиск по `name` и `aliases` в городе; не найден → `Station(is_verified=False)`.
- **Склейка рейсов:** ключ `route + train_number + departure_date`; если не нашли — запасной: тот же маршрут, то же время отправления до минуты, номера отличаются на 1 (D-26). `Trip.alt_numbers` хранит все встреченные номера. Время отправления от разных площадок расходится > 5 мин → `time_mismatch`, приоритет у площадки с меньшим `Source.priority`.

## Модели

| Модель | Ключевые поля | Ограничения / индексы |
|---|---|---|
| `City` | name, slug, timezone (IANA), is_active | slug unique |
| `Station` | city, name, aliases (JSON list), express_code, is_verified | (city, name) unique; express_code unique, если не пустой |
| `Source` | code, name, base_url, is_active, is_reference, priority, color, fee_policy (included/excluded/unknown), price_note, min_interval_sec, daily_budget, use_proxy | code unique. `rzd_base` (`is_reference`) — тариф РЖД из `gdsCost` OneTwoTrip, хранится снимками как обычная площадка |
| `SourceCity` | source, city, external_id, url_slug | (source, city) unique |
| `Route` | origin, destination, slug, is_scheduled, horizon_days=30, search_count, last_searched_at | (origin, destination) unique, origin ≠ destination |
| `Trip` | route, train_number, alt_numbers (JSON), train_name, departure_date, departure_at, arrival_at, duration_min, origin_station, destination_station | (route, train_number, departure_date) unique; arrival > departure; idx (route, departure_date) |
| `PriceSnapshot` | trip, source, task, car_class, price, price_old, seats, url, extras, collected_at | price > 0; idx (trip, source, car_class, collected_at), idx (task) |
| `SyncJob` | route, travel_date, trigger (user/admin/schedule), priority, status (queued/running/done/partial/failed), created/started/finished_at, error | partial unique (route, travel_date) where status in (queued, running); idx (status, priority, created_at) |
| `SyncTask` | job, source, route, travel_date, status (pending/running/ok/empty/blocked/timeout/network/layout_changed/invalid/error/skipped), offers_count, attempts, duration_ms, strategy, proxy_used, error, warnings (JSON), raw_ref, finished_at | (job, source) unique; idx (source, route, travel_date, -finished_at) where status in (ok, empty) |
| `ClickOut` | trip, source, car_class, price_shown, was_cheapest, created_at | без IP и cookie |

`route` и `travel_date` в `SyncTask` продублированы ради быстрого поиска «последней успешной задачи площадки». **Текущая выдача** = снимки последней успешной (`ok`/`empty`) `SyncTask` каждой активной площадки по route+date. Класс пропал из свежей задачи → «нет мест». Снимки только добавляются.

Объём: ~15 плановых маршрутов × 30 дней × ~6 поездов × 3 класса × 2–3 площадки, обновление каждые 3–12 ч → порядка 20–40 тыс. снимков в сутки, ~150–300 МБ в месяц. Без оптимизаций.

## Сайт

| Адрес | Страница |
|---|---|
| `/` | Поиск (откуда/куда с автодополнением по справочнику, дата), популярные направления с ценой «от» |
| `/r/<from>-<to>/` | Календарь минимальных цен направления |
| `/r/<from>-<to>/<date>/` | Выдача: карточки поездов, таблица «класс × площадка», лучшая цена, выгода, свежесть, синхронизация, фильтры, лента дат |
| `/sync/` (POST), `/sync/<job>/status/` | Постановка задания, htmx-опрос прогресса |
| `/trip/<id>/` | Рейс: цены сейчас, график истории (Chart.js, линия на площадку, ступенчатая) |
| `/go/<trip>/<source>/<class>/` | 302 на площадку + `ClickOut` |
| `/analytics/` | Разница цен площадок |
| `/about/` | Как это работает, оговорки про «от» и сбор |

Маршрут создаётся при первом поиске, если хотя бы одна активная площадка знает оба города (есть `SourceCity`). Иначе «направление пока не поддерживается». Площадки без данных о городах в выдаче этого маршрута не показываются.

Вёрстка: Tailwind, цвета только токенами (светлая и тёмная тема), цифры `tabular-nums`, мобильная выдача — карточки вместо таблицы. Форматы: `2 340 ₽`, `172 ₽ (7%)`, `среда, 3 декабря`, `15 ч 45 мин`, склонения «место/места/мест», время местное с пометкой `МСК+1` при отличии. Фильтры: класс, отправление (ночь/утро/день/вечер), время в пути, только с местами; сортировка: цена, отправление, время в пути, выгода; всё в адресе. ≤ 10 SQL на страницу.

## Админка

Тема django-unfold (или свои стили). Дашборд: здоровье площадок (последняя успешная, доля ошибок 24 ч, охлаждение), очередь и воркер (размер очереди, последний сигнал воркера), объём данных, переходы, «требует внимания» (непроверенные вокзалы, неизвестные классы). Действия: синхронизировать маршрут, включить расписание, выключить площадку, подтвердить или объединить вокзал. Снимки и переходы только для чтения. Адрес админки из `DJANGO_ADMIN_URL`.

## Окружение и prod

Переменные: `DJANGO_SETTINGS_MODULE`, `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS`, `DJANGO_ADMIN_URL`, `DATABASE_URL`, `SCRAPER_PROXIES`, `SCRAPER_HEADLESS`, `WORKER_SLOTS`. Секреты только в `.env` (не в git, не в чат).

Prod: Ubuntu 24.04, compose (`web` Gunicorn, `worker` с лимитом памяти, `db` без внешнего порта, `nginx`, `certbot`), `check --deploy` чистый, HSTS, secure cookies, ограничение попыток входа в админку, `pg_dump` ежедневно ×7 + еженедельно к автору, `/healthz/` (БД, сигнал воркера, свежесть площадок) + внешний мониторинг.

## Тестирование

- Без сети в CI (`pytest-socket`), время фиксировано (`time-machine`), данные — `factory_boy`.
- Фикстуры площадок: `regular`, `empty`, `ads`, `soldout`, `discount`, `night`, `tz`, `broken` (должен дать ошибку, а не пустой успех).
- Воркер тестируется с подменённым адаптером. `fetch` проверяется только живыми прогонами.
- E2E Playwright по своему сайту. Ручная сверка цен — протокол в фазе 13.

## Код

Английский, аннотации типов, `ruff` (E, F, W, I, B, UP, DJ, SIM, RUF), строка 100. Комментарии только «почему», без шапок и пересказа кода. Логика: `selectors.py` (чтение), `services.py` (запись), представления тонкие. Деньги `Decimal`, время с поясом. Git: `main` + ветки `phase-N-...`, Conventional Commits на английском, `Roadmap: N.M` в теле.
