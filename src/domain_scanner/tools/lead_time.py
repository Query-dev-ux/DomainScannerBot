"""Did our own checks see a domain go bad before the platform banned it?

The honest version of "our checks outrun Facebook": we have no timestamps for
Facebook blocks, so this measures the one death we do timestamp -- the ban in
PWApartners / SkakApp -- against the first time dns_rbl or Safe Browsing said
anything about the same domain.

Run it where the database is:

    docker compose run --rm bot python -m domain_scanner.tools.lead_time

Read only: one SELECT, nothing is written.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from statistics import median

from sqlalchemy import text

from domain_scanner.config import get_settings
from domain_scanner.db import init_engine, session_scope, shutdown_engine

# What our own (non-platform) checkers said, and when they first said it.
QUERY = text(
    """
    WITH banned AS (
        SELECT DISTINCT ON (s.domain_id)
               s.domain_id, s.id AS scan_id, c.created_at AS ban_at
        FROM scan_checks c JOIN scans s ON s.id = c.scan_id
        WHERE c.checker = 'source_status' AND c.verdict = 'flagged'
        ORDER BY s.domain_id, c.created_at
    ),
    own AS (
        SELECT DISTINCT ON (s.domain_id)
               s.domain_id, s.id AS scan_id, c.created_at AS own_at, c.checker
        FROM scan_checks c JOIN scans s ON s.id = c.scan_id
        WHERE c.checker <> 'source_status'
          AND c.verdict IN ('suspicious', 'flagged')
        ORDER BY s.domain_id, c.created_at
    ),
    first_scan AS (
        SELECT domain_id, MIN(created_at) AS first_at FROM scans GROUP BY domain_id
    )
    SELECT d.name,
           d.source::text AS source,
           b.ban_at,
           b.scan_id AS ban_scan_id,
           f.first_at,
           o.own_at,
           o.scan_id AS own_scan_id,
           o.checker AS first_checker
    FROM banned b
    JOIN domains d ON d.id = b.domain_id
    JOIN first_scan f ON f.domain_id = b.domain_id
    LEFT JOIN own o ON o.domain_id = b.domain_id
    ORDER BY b.ban_at
    """
)

COVERAGE = text(
    """
    SELECT MIN(created_at) AS since, MAX(created_at) AS until,
           COUNT(*) AS scans, COUNT(DISTINCT domain_id) AS domains
    FROM scans
    """
)


def _hours(later: datetime, earlier: datetime) -> float:
    return (later - earlier).total_seconds() / 3600


def _fmt(value: datetime) -> str:
    return value.strftime("%d.%m %H:%M")


async def main() -> None:
    settings = get_settings()
    init_engine(settings.database_url)
    try:
        async with session_scope() as session:
            coverage = (await session.execute(COVERAGE)).one()
            rows = (await session.execute(QUERY)).all()
    finally:
        await shutdown_engine()

    print("Опережают ли наши проверки бан в платформе")
    print()
    if not coverage.scans:
        print("Проверок в базе нет.")
        return
    print(f"Данные: {_fmt(coverage.since)} - {_fmt(coverage.until)}, "
          f"проверок {coverage.scans}, доменов {coverage.domains}")
    print()

    if not rows:
        print("Ни одного бана в истории проверок - сравнивать не с чем.")
        return

    # A domain banned in its very first scan was already dead when we met it:
    # there was no window in which we could have warned about it.
    already = [r for r in rows if r.ban_at <= r.first_at]
    comparable = [r for r in rows if r.ban_at > r.first_at]
    print(f"Доменов с баном в истории: {len(rows)}")
    print(f"  уже были забанены на первой проверке: {len(already)} (сравнивать не с чем)")
    print(f"  забанены при нас: {len(comparable)}")
    if not comparable:
        return

    # Checks inside one scan are written a few milliseconds apart, so compare
    # scans, not timestamps: a signal from the very scan that saw the ban is not
    # a warning, it is the same news.
    seen = [r for r in comparable if r.own_at is not None]
    same = [r for r in seen if r.own_scan_id == r.ban_scan_id]
    earlier = [r for r in seen if r.own_scan_id != r.ban_scan_id and r.own_at < r.ban_at]
    later = [r for r in seen if r.own_scan_id != r.ban_scan_id and r.own_at > r.ban_at]
    silent = [r for r in comparable if r.own_at is None]

    print()
    print(f"Свои проверки заметили раньше бана: {len(earlier)}")
    if earlier:
        leads = sorted(_hours(r.ban_at, r.own_at) for r in earlier)
        print(f"  опережение: медиана {median(leads):.1f} ч, "
              f"от {leads[0]:.1f} до {leads[-1]:.1f} ч")
        by_checker: dict[str, int] = {}
        for r in earlier:
            by_checker[r.first_checker] = by_checker.get(r.first_checker, 0) + 1
        print("  кто дал сигнал: "
              + ", ".join(f"{k} {v}" for k, v in sorted(by_checker.items())))
    print(f"В той же проверке, что и бан: {len(same)}")
    print(f"Позже бана: {len(later)}")
    print(f"Не заметили вовсе: {len(silent)}")

    if earlier:
        print()
        print("Кого мы предупредили заранее:")
        for r in sorted(earlier, key=lambda r: -_hours(r.ban_at, r.own_at))[:20]:
            print(f"  {r.name} ({r.source}) — {r.first_checker} в {_fmt(r.own_at)}, "
                  f"бан в {_fmt(r.ban_at)}, опережение {_hours(r.ban_at, r.own_at):.1f} ч")

    print()
    print("Важно: время бана здесь - когда его увидели мы, а не когда его поставила")
    print("платформа; из-за обрыва выгрузки часть доменов приходила к нам с задержкой.")
    print("Отсчёт идёт от первой проверки домена, а история короткая, поэтому")
    print("цифры говорят о порядке величин, а не о точном опережении.")


if __name__ == "__main__":
    asyncio.run(main())
