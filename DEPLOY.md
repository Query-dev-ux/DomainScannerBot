# Развёртывание на сервере

Инструкция для сервера, где **уже крутится другое приложение** в Docker. Конфликтов
нет по построению, но ниже объясняется почему и на что обратить внимание.

## Почему не конфликтует с существующими контейнерами

| Ресурс | Как изолирован |
|---|---|
| **Порты** | Бот работает по long-polling (только исходящие соединения). Ни один сервис не публикует порты на хост — строки `ports:` в compose нет. Postgres слушает только внутри своей сети. |
| **Имя проекта** | В `docker-compose.yml` задано `name: domain-scanner-bot`. Контейнеры/сеть/том получают префикс `domain-scanner-bot_*` и не пересекаются с чужими. |
| **Сеть** | Compose создаёт отдельную bridge-сеть `domain-scanner-bot_default`. Другое приложение её не видит. |
| **Том с данными** | `domain-scanner-bot_pgdata` — отдельный именованный volume. |
| **Образы** | Собираются как `domain-scanner-bot-bot` / `domain-scanner-bot-migrate`. |

Единственный общий ресурс — **демон Docker и ресурсы хоста** (CPU/RAM/диск). При
необходимости ограничьте (см. «Лимиты ресурсов»).

## Требования

- Docker Engine 24+ и плагин Compose v2 (`docker compose version`)
- Исходящий HTTPS к `api.telegram.org`, `openapi.pwa.partners`,
  `safebrowsing.googleapis.com`, `www.virustotal.com`
- ~300 МБ RAM и немного диска под Postgres

## 1. Забрать код в отдельную директорию

```bash
sudo mkdir -p /opt/domain-scanner-bot
sudo chown "$USER" /opt/domain-scanner-bot
git clone https://github.com/Query-dev-ux/DomainScannerBot.git /opt/domain-scanner-bot
cd /opt/domain-scanner-bot
```

## 2. Заполнить `.env`

```bash
cp .env.example .env
nano .env
```

Обязательно:

| Переменная | Значение |
|---|---|
| `BOT_TOKEN` | токен бота от @BotFather |
| `ALERT_CHAT_ID` | id группы для алертов (для супергруппы — вида `-100…`) |
| `ADMIN_IDS` | ваши Telegram user id через запятую |
| `PWA_API_KEY`, `PWA_TEAM_UUID` | доступ к PWA.partners Open API |
| `GSB_API_KEY` | ключ Google Safe Browsing (можно оставить пустым — чекер отключится) |
| `POSTGRES_PASSWORD` | придумать надёжный пароль |

`POSTGRES_HOST=db` и `POSTGRES_PORT=5432` менять не нужно — это адрес контейнера
внутри сети проекта.

> Бот добавит себя в группу как обычного участника. Дайте ему право писать
> сообщения; права администратора не требуются.

## 3. Собрать и запустить

```bash
docker compose up -d --build
```

Порядок: `db` (ждёт healthcheck) → `migrate` (прогоняет `alembic upgrade head` и
завершается) → `bot`.

## 4. Проверить

```bash
docker compose ps
docker compose logs -f bot
```

Признак успеха — в логах `app.started` и сообщение «🟢 DomainScannerBot запущен» в
группе. Проверьте команды: `/status`, затем `/sync_now` (подтянет домены из PWA API).

Разовая проверка миграций:

```bash
docker compose run --rm migrate alembic current
```

## 5. Обновление версии

```bash
cd /opt/domain-scanner-bot
git pull
docker compose up -d --build      # migrate прогонится автоматически заново
docker image prune -f             # убрать старые слои
```

## 6. Бэкап базы

```bash
# дамп
docker compose exec -T db pg_dump -U domain_scanner domain_scanner | gzip > backup-$(date +%F).sql.gz

# восстановление
gunzip -c backup-YYYY-MM-DD.sql.gz | docker compose exec -T db psql -U domain_scanner -d domain_scanner
```

Данные лежат в volume `domain-scanner-bot_pgdata` (`docker volume inspect …`).

## Вариант: использовать уже существующий PostgreSQL

Если на сервере есть свой Postgres (в контейнере другого приложения или на хосте) и
вы не хотите второй инстанс:

1. Создайте БД и пользователя:
   ```sql
   CREATE USER domain_scanner WITH PASSWORD '...';
   CREATE DATABASE domain_scanner OWNER domain_scanner;
   ```
2. В `.env` укажите реальные `POSTGRES_HOST` / `POSTGRES_PORT` / `POSTGRES_USER` /
   `POSTGRES_PASSWORD` / `POSTGRES_DB`.
   - Postgres на хосте: `POSTGRES_HOST=host.docker.internal` (добавьте в compose
     `extra_hosts: ["host.docker.internal:host-gateway"]`) или IP docker-моста.
   - Postgres в другом compose-стеке: раскомментируйте блок `networks:` в
     `docker-compose.external-db.yml` и укажите имя чужой сети
     (`docker network ls`).
3. Запуск с оверрайдом (контейнер `db` не поднимается):
   ```bash
   docker compose -f docker-compose.yml -f docker-compose.external-db.yml up -d --build
   ```

## Эксплуатация

### Лимиты ресурсов (по желанию)

Создайте `docker-compose.override.yml` — Compose подхватит его автоматически:

```yaml
services:
  bot:
    deploy:
      resources:
        limits:
          cpus: "0.5"
          memory: 256M
  db:
    deploy:
      resources:
        limits:
          memory: 256M
```

### Ротация логов Docker

Чтобы логи бота не забили диск — в `/etc/docker/daemon.json` (влияет на все
контейнеры хоста, согласуйте с владельцем второго приложения):

```json
{ "log-driver": "json-file", "log-opts": { "max-size": "10m", "max-file": "3" } }
```

затем `sudo systemctl restart docker`.

### DNS для чекера `dns_rbl`

Блоклисты SURBL/Spamhaus DBL отказывают публичным резолверам (8.8.8.8 и т.п.). Если
`/check` стабильно отдаёт «не найден в блоклистах» для явно плохих доменов —
пропишите контейнеру собственный рекурсивный резолвер в `docker-compose.override.yml`:

```yaml
services:
  bot:
    dns: ["<ip рекурсивного резолвера>"]
```

GSB и VirusTotal от резолвера не зависят.

### Автозапуск после перезагрузки

`restart: unless-stopped` уже стоит — контейнеры поднимутся сами, если запущен
Docker. Отдельный systemd-юнит не нужен.

## Удаление

```bash
docker compose down            # остановить, БД сохранится
docker compose down -v         # + удалить том с данными
```
