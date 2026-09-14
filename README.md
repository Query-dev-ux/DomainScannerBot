# DomainScannerBot

Telegram-бот для мониторинга репутации доменов. Периодически выгружает домены из
API PWA.partners, проверяет их в security-сервисах, хранит историю результатов и
присылает уведомление в группу, если статус домена ухудшился.

## Как это работает

```
PWA.partners Open API ──sync──▶  PostgreSQL  ◀──scan── Checkers (DNSBL, GSB, Facebook)
                                     │
                                     ▼
                          APScheduler (внутри бота)
                                     │
                                     ▼
                        Telegram: алерт в группу + команды
```

- **Sync** (`SYNC_INTERVAL_MINUTES`) — тянет `GET /dash_api/domains/list`, добавляет новые
  домены, обновляет статус, деактивирует пропавшие.
- **Scan** — каждый домен проверяется не чаще, чем раз в `SCAN_INTERVAL_MINUTES`.
  Проверки выполняются параллельно (`SCAN_CONCURRENCY`), вердикт агрегируется по
  худшему результату.
- **Alert** — сообщение в `ALERT_CHAT_ID` отправляется только при **изменении**
  вердикта на `подозрительно` или `зашкварен`.

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
| `/status` | сводка по вердиктам |
| `/list [clean\|suspicious\|flagged]` | список доменов |
| `/check <домен>` | проверить домен сейчас |
| `/add <домен>` | добавить домен вручную |
| `/scan_now` | запустить плановое сканирование |
| `/sync_now` | подтянуть домены из PWA API |

Доступ ограничен списком `ADMIN_IDS` (если пуст — команды доступны всем).

## Разработка

```bash
pytest
ruff check src tests
alembic revision --autogenerate -m "..."   # новая миграция
```

Стек: Python 3.12, aiogram 3.x, SQLAlchemy 2 (async) + asyncpg, Alembic,
APScheduler, aiohttp, pydantic-settings.
