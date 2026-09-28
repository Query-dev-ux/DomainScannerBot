"""Ask PWApartners what it really has, when the dashboard shows more than we sync.

Run it inside the container, where the credentials live:

    docker compose run --rm bot python -m domain_scanner.tools.pwa_audit a.com b.com

Without arguments it only reports what the API returns. With domain names it also
says, for each one, whether the API mentions it at all and with which status --
that is what tells apart "the platform hides these from the API" from "we parse
them wrong". Every request is a plain GET; nothing is written anywhere.
"""

from __future__ import annotations

import asyncio
import sys
from collections import Counter
from typing import Any

import aiohttp

from domain_scanner.config import get_settings
from domain_scanner.utils import normalize_domain

PAGE_SIZE = 200
PATH = "/dash_api/domains/list"


async def _fetch(
    base_url: str, headers: dict[str, str], params: dict[str, Any]
) -> tuple[list[dict[str, Any]], int]:
    """Every page of the domain list, plus the total the API claims."""
    items: list[dict[str, Any]] = []
    total = 0
    timeout = aiohttp.ClientTimeout(total=60)
    async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
        page = 1
        while True:
            query = {"page": page, "page_size": PAGE_SIZE, **params}
            async with session.get(f"{base_url}{PATH}", params=query) as resp:
                body = await resp.text()
                if resp.status >= 400:
                    raise SystemExit(f"HTTP {resp.status}: {body[:400]}")
                data = await resp.json(content_type=None)
            batch = data.get("domains") or []
            items.extend(batch)
            total = int(data.get("total") or 0)
            if not batch or page * PAGE_SIZE >= total:
                break
            page += 1
    return items, total


def _names(items: list[dict[str, Any]]) -> dict[str, str]:
    """domain -> status, as the API reports them."""
    found: dict[str, str] = {}
    for item in items:
        name = normalize_domain(item.get("domain"))
        if name:
            found[name] = str(item.get("status"))
    return found


async def main() -> None:
    settings = get_settings()
    if not (settings.pwa_api_key and settings.pwa_team_uuid):
        raise SystemExit("PWApartners не настроен: заполните PWA_* в .env")

    base_url = settings.pwa_api_base_url.rstrip("/")
    headers = {
        "X-Api-Key": settings.pwa_api_key,
        "X-Team-UUID": settings.pwa_team_uuid,
        "Accept": "application/json",
    }
    if settings.pwa_teamate_uuid:
        headers["X-Teamate-UUID"] = settings.pwa_teamate_uuid

    wanted = [n for n in (normalize_domain(a) for a in sys.argv[1:]) if n]

    # 1. Exactly what the bot syncs today.
    items, total = await _fetch(base_url, headers, {})
    found = _names(items)
    print(f"Как синкает бот: получено {len(items)}, total по API {total}, "
          f"уникальных доменов {len(found)}")
    statuses = Counter(str(i.get("status")) for i in items)
    print("Статусы:", ", ".join(f"{s}={n}" for s, n in sorted(statuses.items())))
    if items:
        print("Поля домена:", ", ".join(sorted(items[0])))

    # 2. Same call for the whole team, in case the teamate header narrows it down.
    if "X-Teamate-UUID" in headers:
        team_headers = {k: v for k, v in headers.items() if k != "X-Teamate-UUID"}
        try:
            team_items, team_total = await _fetch(base_url, team_headers, {})
            extra = set(_names(team_items)) - set(found)
            print(f"Без X-Teamate-UUID: получено {len(team_items)}, total {team_total}, "
                  f"из них новых для нас {len(extra)}")
            if extra:
                print("  например:", ", ".join(sorted(extra)[:10]))
        except SystemExit as exc:
            print(f"Без X-Teamate-UUID: {exc}")

    # 3. Maybe banned domains are simply filtered out unless asked for by status.
    for status in ("9", "8"):
        try:
            banned, banned_total = await _fetch(base_url, headers, {"status": status})
            extra = set(_names(banned)) - set(found)
            print(f"?status={status}: получено {len(banned)}, total {banned_total}, "
                  f"из них новых для нас {len(extra)}")
            if extra:
                print("  например:", ", ".join(sorted(extra)[:10]))
        except SystemExit as exc:
            print(f"?status={status}: {exc}")

    if not wanted:
        return
    print()
    print(f"Проверяю {len(wanted)} доменов из списка:")
    missing = []
    for name in wanted:
        if name in found:
            print(f"  {name} — API отдаёт, status={found[name]}")
        else:
            missing.append(name)
    print(f"API не отдаёт вовсе: {len(missing)}")
    for name in missing:
        print(f"  {name}")


if __name__ == "__main__":
    asyncio.run(main())
