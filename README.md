# DomainScannerBot

Telegram-бот для мониторинга репутации доменов. Периодически выгружает домены из
PWA-платформ (PWA.partners, UClient), проверяет их в security-сервисах, хранит историю
результатов и присылает уведомление в группу, если статус домена ухудшился.

## Как это работает

```
PWA.partners ─┐
              ├─sync──▶  PostgreSQL  ◀──scan── Checkers (DNSBL, GSB, Facebook)
UClient ──────┘              │
                             ▼
                  APScheduler (внутри бота)
                             │
                             ▼
                Telegram: алерт в группу + команды
```

- **Sync** (`SYNC_INTERVAL_MINUTES`) — тянет списки доменов из каждого подключённого
  источника, добавляет новые, обновляет статус, деактивирует пропавшие.
- **Scan** — каждый домен проверяется не чаще, чем раз в `SCAN_INTERVAL_MINUTES`.
  Проверки выполняются параллельно (`SCAN_CONCURRENCY`), вердикт агрегируется по
  худшему результату.
- **Alert** — сообщение в `ALERT_CHAT_ID` отправляется только при **изменении**
  вердикта на `подозрительно` или `зашкварен`.

### Источники доменов

Источник включается, как только в `.env` заполнены его ключи; нужен хотя бы один.

| Источник | Ключи | Откуда берутся домены | Активен, если |
|---|---|---|---|
| PWA.partners | `PWA_API_KEY`, `PWA_TEAM_UUID`, `PWA_TEAMATE_UUID` | `GET /dash_api/domains/list` | `status = 1` |
| UClient (skakapp) | `UCLIENT_LOGIN` + `UCLIENT_PASSWORD` (Basic-auth; API-ключ UClient не нужен) | `POST /pwa/list`: основной домен, `extDomains` и домены сплитов | PWA в статусе `ACTIVE` |

У каждого домена в базе есть владелец (`domains.source`). Синк источника обновляет и
выключает **только свои** домены — PWA.partners никогда не отключит домен UClient и
наоборот. Если домен приходит из двух источников, им владеет первый, второй его не трогает.
Домен, добавленный вручную через `/add`, «забирает» первый источник, который его вернёт.
Источники синхронизируются независимо: если один упал, второй всё равно обновится, а в
группу придёт сообщение, какой именно лёг.

Новый источник = класс с `source`, `title` и `async def fetch_domains() -> list[SourceDomain]`
в `src/domain_scanner/sources/`, добавленный в `build_providers()`.

### Автоматический режим

Оба задания крутит APScheduler внутри процесса бота — отдельный воркер или cron не нужен.

| | Как часто |
|---|---|
| Синхронизация источников | раз в `SYNC_INTERVAL_MINUTES`, первый прогон через 30 сек после старта |
| Тик сканера | раз в `SCAN_INTERVAL_MINUTES / 3`, первый — через 2 мин после старта |
| Перепроверка одного домена | не чаще раза в `SCAN_INTERVAL_MINUTES` |
| Доменов за один прогон | до `SCAN_BATCH_SIZE` (по умолчанию 50) |

Тик сканера чаще, чем интервал перепроверки, поэтому домены «дозревают» волнами, а
не все разом; из очереди берутся самые давно проверенные (и ни разу не проверенные)
вперёд. Если доменов больше `SCAN_BATCH_SIZE`, остаток разбирается следующими
прогонами — это защита от burst'а в лимиты Graph API.

`max_instances=1` не даёт прогонам накладываться, `coalesce=True` схлопывает
пропущенные запуски в один, `misfire_grace_time=600` позволяет догнать прогон,
пропущенный из-за короткого даунтайма.

Посмотреть текущее расписание и очередь — команда `/jobs`.

### Вердикты

| Вердикт | Значение |
|---|---|
| `clean` | чисто во всех проверках |
| `suspicious` | домен не резолвится, или краулер FB не смог прочитать страницу |
| `flagged` | найден в блоклистах / GSB / заблокирован в Facebook |
| `error` | все проверки завершились ошибкой |

## Проверки (checkers)

| Checker | Ключ | Что покрывает |
|---|---|---|
| `dns_rbl` | не нужен | резолв домена + Spamhaus DBL / SURBL |
| `google_safe_browsing` | `GSB_API_KEY` | malware / phishing / unwanted software |
| `facebook` | `FB_APP_ID` + `FB_APP_SECRET` | блокировка ссылки внутри Facebook |

### Как работает проверка Facebook

Официального API «заблокирован ли домен» нет. Чекер использует **Graph API URL node**
(`GET /v21.0/?id=https://<домен>/&fields=og_object,engagement`) — тот же запрос, на
котором построен [Sharing Debugger](https://developers.facebook.com/tools/debug/).
Аутентификация — app access token (`{app_id}|{app_secret}`), логин пользователя не нужен.

| Ответ Graph API | Вердикт |
|---|---|
| объект отдался (`id` + `og_object`) | `clean` |
| ошибка с маркером блокировки (`Community Standards`, `not allowed`, `unsafe`…) | `flagged` |
| краулер не смог прочитать страницу (`no data was scraped`…) | `suspicious` |
| transient / rate limit / незнакомая ошибка | `error` (алерт не шлётся) |

Полный ответ Graph API всегда сохраняется в `scan_checks.raw` — по накопленным данным
списки маркеров в [checkers/facebook.py](src/domain_scanner/checkers/facebook.py)
можно уточнять. Незнакомые ошибки пишутся в лог как `facebook.unknown_error`.

Новый checker = класс с атрибутом `name` и методом `async def check(domain) -> CheckOutcome`,
добавленный в `build_checkers()` (`src/domain_scanner/checkers/__init__.py`).

## Запуск

```bash
cp .env.example .env      # заполнить токены и ключи
docker compose up -d --build
```

`docker compose` поднимает Postgres, прогоняет миграции (`migrate`) и запускает `bot`.

Развёртывание на сервере (в т.ч. рядом с другими контейнерами, переиспользование
существующего Postgres, бэкапы, обновление) — [DEPLOY.md](DEPLOY.md).

### Локально без Docker

```bash
python -m venv .venv && . .venv/Scripts/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
# поднять Postgres где-то локально, прописать POSTGRES_* в .env
alembic upgrade head
python -m domain_scanner
```

## Команды бота

| Команда | Действие |
|---|---|
| `/status` | сводка: вердикты, доля чистых, разбивка по источникам |
| `/list` | проблемные домены (зашкварен / подозрительно / ошибка) |
| `/list all` · `/list clean` · `/list flagged` … | все домены или по вердикту |
| `/check <домен>` | проверить домен сейчас |
| `/add <домен>` | добавить домен вручную |
| `/jobs` | расписание автопроверок, время следующего запуска, размер очереди |
| `/scan_now [N]` | сканировать сейчас; без аргумента — до `SCAN_BATCH_SIZE` доменов |
| `/sync_now` | подтянуть домены из всех источников |

Под алертами и карточками `/check` — кнопки **🔁 Перепроверить** (новая карточка
ответом, исходный алерт остаётся в истории) и **🔕 Не следить** / **🔔 Вернуть в
мониторинг**. Если ручная проверка обнаружила ухудшение, алерт всё равно уходит в группу.

Все тексты бота собраны в [bot/render.py](src/domain_scanner/bot/render.py). Время в
сообщениях — в часовом поясе `DISPLAY_TIMEZONE` (по умолчанию UTC).

Доступ ограничен списком `ADMIN_IDS` (если пуст — команды доступны всем).

## Разработка

```bash
pytest
ruff check src tests
alembic revision --autogenerate -m "..."   # новая миграция
alembic check                               # модели и миграции не разъехались
```

Тесты синхронизации (`tests/test_sync_db.py`) работают с настоящим PostgreSQL и
пропускаются, если не задан `TEST_DATABASE_URL` — укажите одноразовую базу с
применёнными миграциями, таблицы в ней очищаются перед каждым тестом:

```bash
TEST_DATABASE_URL=postgresql+asyncpg://user:pass@127.0.0.1:5432/domain_scanner_test pytest
```

Стек: Python 3.12, aiogram 3.x, SQLAlchemy 2 (async) + asyncpg, Alembic,
APScheduler, aiohttp, pydantic-settings.
