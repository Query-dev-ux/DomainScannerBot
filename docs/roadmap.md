# Roadmap

## v0.1 (сделано)
- [x] Каркас: config, БД (SQLAlchemy async + Alembic), Docker Compose
- [x] Клиент PWA.partners Open API (`/dash_api/domains/list`)
- [x] Синхронизация доменов в БД + деактивация пропавших
- [x] Фреймворк проверок + checkers: `dns_rbl`, `google_safe_browsing`
- [x] Планировщик (APScheduler) внутри бота
- [x] Агрегация вердикта, история сканов, алерт в группу при изменении статуса
- [x] Команды бота: `/status /list /check /add /scan_now /sync_now`

## v0.2 — приоритет: пригодность домена для FB-трафика
- [x] **Facebook URL status** — чекер `facebook` через Graph API URL node
      (app access token). Блокировка по Community Standards → `flagged`,
      непрочитанная краулером страница → `suspicious`.
- [x] **Отказались от VirusTotal** — дублирует GSB/DNSBL, free-лимит (4 req/min,
      500/сутки) не тянет ~200 доменов, Premium от $10k/год не окупается.
- [ ] **Калибровка маркеров `facebook`** — собрать реальные ответы Graph API из
      `scan_checks.raw`, уточнить `_BLOCKED_MARKERS` / `_UNFETCHABLE_MARKERS`.
      Смотреть в логах `facebook.unknown_error`.
- [ ] **Проверка редиректов/клоаки** — куда реально ведёт домен, отдаётся ли white page.
- [ ] **SSL/TLS** — валидность сертификата, срок, издатель.
- [ ] **Возраст домена** (RDAP/WHOIS) — свежий домен = выше риск бана.
- [ ] Учитывать `Domain.status` из PWA API (код 7 «занят», 11 «ошибка выпуска» и т.д.).

## v0.3 — эксплуатация
- [ ] Ретеншн истории сканов (партиционирование / чистка `scan_checks`)
- [ ] Rate-limit обёртка на checker'ы (Graph API лимитирует по app usage)
- [ ] Метрики (Prometheus) + `/health`
- [ ] Ежедневный дайджест в группу (сколько проверено, сколько зашкварено)
- [ ] Кнопки в алерте: «проверить снова», «игнорировать домен»
- [ ] Тесты интеграции с БД (testcontainers / pytest-postgresql)

## Открытые вопросы
- Нужен ли отдельный резолвер в контейнере для DNSBL (SURBL/Spamhaus блокируют
  публичные резолверы).
- Точные коды/сообщения Graph API для заблокированных доменов — подтвердить на
  реальном забаненном домене (см. «Калибровка маркеров» выше).
- Не упрёмся ли в app-level rate limit Graph API при ~200 доменах × несколько
  сканов в сутки.
