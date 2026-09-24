"""Every user-facing Telegram message, as pure functions returning HTML.

Kept free of network and DB access so the layout can be unit-tested and tweaked
in one place. Style is deliberately plain: no emoji, hierarchy comes from bold
headlines, monospaced domains and italic metadata. Anything that comes from
outside (domains, API errors) must go through `_e`.
"""

from __future__ import annotations

import html
from collections.abc import Sequence

from domain_scanner.db.models import DomainSource, Verdict
from domain_scanner.labels import (
    VERDICT_ORDER,
    VERDICT_RU,
    VERDICT_TITLE,
    checker_label,
    plural,
    source_label,
)
from domain_scanner.repositories.domains import DomainStats, DomainWithChecks
from domain_scanner.services.scanner import ScanReport
from domain_scanner.services.sync import SyncResult

LIST_LIMIT = 60
# Telegram rejects messages over 4096 chars; leave room for the header/footer.
MESSAGE_BUDGET = 3600
DETAIL_LIMIT = 180

# Verdicts that put a domain on the problem list.
_PROBLEM = (Verdict.SUSPICIOUS, Verdict.FLAGGED)


def _e(text: object) -> str:
    return html.escape(str(text), quote=False)


def _clip(text: str, limit: int = DETAIL_LIMIT) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _domains(n: int) -> str:
    return f"{n} {plural(n, 'домен', 'домена', 'доменов')}"


# ── Help / startup ────────────────────────────────────────────────────────────

BOT_COMMANDS: tuple[tuple[str, str], ...] = (
    ("status", "Сводка по доменам"),
    ("list", "Проблемные домены"),
    ("check", "Проверить домен"),
    ("scan_now", "Проверить все домены сейчас"),
    ("sync_now", "Обновить списки из источников"),
    ("help", "Справка"),
)


def render_help() -> str:
    return (
        "<b>DomainScannerBot</b>\n"
        "Следит за репутацией доменов и сообщает в группу, когда домен зашкварен.\n\n"
        "/status — сводка\n"
        "/list — проблемные домены\n"
        "\n"
        "/check <code>домен</code> — проверить сейчас\n"
        "/scan_now — проверить все домены сейчас\n"
        "/sync_now — обновить списки из источников"
    )


def render_startup(sources: Sequence[str], checkers: Sequence[str]) -> str:
    src = ", ".join(sources) if sources else "не подключены — проверьте .env"
    chk = ", ".join(checker_label(c) for c in checkers)
    return (
        "<b>DomainScannerBot запущен</b>\n\n"
        f"Источники: {_e(src)}\n"
        f"Проверки: {_e(chk)}"
    )


# ── Scan reports (alert + /check card) ───────────────────────────────────────


def report_headline(report: ScanReport) -> str:
    """The one thing wrong with the domain, named the way /list names it."""
    checks = {o.checker: o.verdict for o in report.outcomes}
    if checks.get("source_status") is Verdict.FLAGGED:
        return "Домен заблокирован в PWA сервисе"
    if checks.get("facebook") is Verdict.FLAGGED:
        return "Домен заблокирован в FB"
    if report.verdict in _PROBLEM:
        return "Домен под подозрением"
    return VERDICT_TITLE[report.verdict]


def render_report(report: ScanReport, *, alert: bool = False) -> str:
    """Card for one scanned domain: what is wrong, which domain, whose it is."""
    return (
        f"<b>{report_headline(report)}</b>\n"
        f"<code>{_e(report.domain)}</code> {_e(source_label(report.source))}"
    )


def render_checking(domain: str) -> str:
    return f"Проверяю <code>{_e(domain)}</code>…"


# ── /status ──────────────────────────────────────────────────────────────────


def render_status(stats: DomainStats) -> str:
    total = stats.monitored
    if not total:
        return "<b>Сводка</b>\n\nДоменов пока нет. Обновите списки из источников: /sync_now"

    lines = ["<b>Сводка</b>", _domains(total), ""]
    lines += [
        f"{VERDICT_RU[v].capitalize()} — {stats.by_verdict[v]}"
        for v in VERDICT_ORDER
        if stats.by_verdict.get(v)
    ]
    return "\n".join(lines)


# ── /list ────────────────────────────────────────────────────────────────────

PROBLEM_VERDICTS = frozenset({Verdict.FLAGGED, Verdict.SUSPICIOUS})

# Sources in the order their groups appear.
SOURCE_ORDER: tuple[DomainSource, ...] = (
    DomainSource.PWA,
    DomainSource.SKAKAPP,
    DomainSource.MANUAL,
)

TAG_BANNED_IN_SOURCE = "Заблокирован в PWA сервисе"
TAG_BANNED_IN_FB = "Заблокирован в FB"
TAG_SUSPICIOUS = "Под подозрением"


def domain_tags(item: DomainWithChecks) -> list[str]:
    """What is wrong with the domain, from its last scan.

    A ban in the platform and a block in Facebook get named; everything else —
    blocklists, Safe Browsing, a page Facebook could not read, an expired domain —
    is "Под подозрением". All three can apply at once.
    """
    banned_in_source = banned_in_fb = suspicious = False
    for checker, verdict in item.checks.items():
        if checker == "source_status" and verdict is Verdict.FLAGGED:
            banned_in_source = True
        elif checker == "facebook" and verdict is Verdict.FLAGGED:
            banned_in_fb = True
        elif verdict in _PROBLEM:
            suspicious = True

    # No per-check data (an old scan, or none yet) — fall back to the verdict.
    if not item.checks and item.domain.current_verdict in _PROBLEM:
        suspicious = True

    return [
        tag
        for tag, applies in (
            (TAG_BANNED_IN_SOURCE, banned_in_source),
            (TAG_BANNED_IN_FB, banned_in_fb),
            (TAG_SUSPICIOUS, suspicious),
        )
        if applies
    ]


SECTION_WATCHED = "Новые"
SECTION_MUTED = "Не отслеживаемые"


def _domain_line(item: DomainWithChecks) -> str:
    tags = domain_tags(item)
    suffix = f" — {_e(' · '.join(tags))}" if tags else ""
    return f"<code>{_e(item.domain.name)}</code>{suffix}"


def _worst_first(item: DomainWithChecks) -> tuple[int, str]:
    return -item.domain.current_verdict.severity, item.domain.name


def _by_source(items: Sequence[DomainWithChecks]) -> list[tuple[DomainSource, list]]:
    groups: dict[DomainSource, list[DomainWithChecks]] = {}
    for item in items:
        groups.setdefault(item.domain.source, []).append(item)
    order = [s for s in SOURCE_ORDER if s in groups]
    order += [s for s in groups if s not in SOURCE_ORDER]
    return [(s, sorted(groups[s], key=_worst_first)) for s in order]


def render_list(items: Sequence[DomainWithChecks], title: str, *, empty_hint: str) -> str:
    """Problem domains: still watched first, then the ones muted by hand.

    Inside each section the domains are grouped by the platform they come from.
    """
    if not items:
        return f"<b>{_e(title)}</b>\n\n{empty_hint}"

    sections = [
        (SECTION_WATCHED, [i for i in items if i.domain.monitoring_enabled]),
        (SECTION_MUTED, [i for i in items if not i.domain.monitoring_enabled]),
    ]

    lines = [f"<b>{_e(title)}</b> · {len(items)}"]
    size = len(lines[0])
    shown = 0
    for section, section_items in sections:
        if not section_items:
            continue
        header = f"<b>{section}</b> · {len(section_items)}"
        if shown >= LIST_LIMIT or size + len(header) > MESSAGE_BUDGET:
            break
        lines += ["", header]
        size += len(header) + 2
        for source, group in _by_source(section_items):
            source_line = f"<i>{_e(source_label(source))}</i>"
            if shown >= LIST_LIMIT or size + len(source_line) > MESSAGE_BUDGET:
                break
            lines.append(source_line)
            size += len(source_line) + 1
            for item in group:
                line = _domain_line(item)
                if shown >= LIST_LIMIT or size + len(line) > MESSAGE_BUDGET:
                    break
                lines.append(line)
                size += len(line) + 1
                shown += 1
    if shown < len(items):
        lines += ["", f"<i>И ещё {len(items) - shown}</i>"]
    return "\n".join(lines)


# ── /sync_now, sync failures ─────────────────────────────────────────────────


def _sync_block(r: SyncResult) -> str:
    title = _e(r.title)
    if not r.ok:
        return f"<b>{title}</b> — ошибка\n<i>{_e(_clip(r.error or ''))}</i>"
    return f"<b>{title}</b> — {_domains(r.fetched)}"


def render_sync(results: Sequence[SyncResult]) -> str:
    if not results:
        return (
            "<b>Синхронизация</b>\n\n"
            "Ни один источник не подключён. Заполните в .env доступы "
            "PWApartners и/или SkakApp."
        )
    return "<b>Синхронизация</b>\n\n" + "\n".join(_sync_block(r) for r in results)


def render_sync_failure(results: Sequence[SyncResult]) -> str:
    failed = [r for r in results if not r.ok]
    return "<b>Синхронизация не удалась</b>\n\n" + "\n\n".join(
        _sync_block(r) for r in failed
    )


def render_job_crash(job: str) -> str:
    return (
        f"<b>{_e(job)} завершилась с ошибкой</b>\n"
        "<i>Подробности: <code>docker compose logs bot</code></i>"
    )


# ── /scan_now ────────────────────────────────────────────────────────────────


def render_scan_started(count: int) -> str:
    return f"Проверяю {_domains(count)}…"


def render_scan_done(reports: Sequence[ScanReport]) -> str:
    counts: dict[Verdict, int] = {}
    for r in reports:
        counts[r.verdict] = counts.get(r.verdict, 0) + 1
    summary = " · ".join(
        f"{VERDICT_RU[v]} {counts[v]}" for v in VERDICT_ORDER if counts.get(v)
    )
    return f"<b>Проверено: {_domains(len(reports))}</b>\n{summary or '—'}"
