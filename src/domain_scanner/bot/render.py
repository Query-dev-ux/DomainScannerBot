"""Every user-facing Telegram message, as pure functions returning HTML.

Kept free of network and DB access so the layout can be unit-tested and tweaked
in one place. Style is deliberately plain: no emoji, hierarchy comes from bold
headlines, monospaced domains and italic metadata. Anything that comes from
outside (domains, API errors) must go through `_e`.
"""

from __future__ import annotations

import html
from collections.abc import Sequence

from domain_scanner.checkers.base import CheckOutcome
from domain_scanner.db.models import Domain, Verdict
from domain_scanner.labels import (
    VERDICT_ORDER,
    VERDICT_RU,
    VERDICT_TITLE,
    checker_label,
    plural,
    source_label,
)
from domain_scanner.repositories.domains import DomainStats
from domain_scanner.services.scanner import ScanReport
from domain_scanner.services.sync import SyncResult

LIST_LIMIT = 60
# Telegram rejects messages over 4096 chars; leave room for the header/footer.
MESSAGE_BUDGET = 3600
DETAIL_LIMIT = 180


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
        "/list — проблемные домены, <code>/list all</code> — все\n"
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


def _outcome_line(o: CheckOutcome) -> str:
    name = _e(checker_label(o.checker))
    # Only what needs attention is bold; a clean check stays quiet.
    if o.verdict is not Verdict.CLEAN:
        name = f"<b>{name}</b>"
    return f"{name} — {_e(_clip(o.summary or o.error or '—'))}"


def render_report(report: ScanReport, *, alert: bool = False) -> str:
    """Card for one scanned domain.

    Shows the previous state only when the domain had a real one and it changed —
    "было: не проверен" says nothing. No timestamp: Telegram shows the message time.
    """
    meta = [_e(source_label(report.source))]
    prev = report.previous_verdict
    if (alert or report.changed) and prev is not Verdict.UNKNOWN and prev is not report.verdict:
        meta.append(f"было: {VERDICT_RU[prev]}")

    lines = [
        f"<b>{VERDICT_TITLE[report.verdict]}</b>",
        f"<code>{_e(report.domain)}</code>",
        f"<i>{' · '.join(meta)}</i>",
    ]
    if report.outcomes:
        lines += ["", *(_outcome_line(o) for o in report.outcomes)]
    return "\n".join(lines)


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

PROBLEM_VERDICTS = frozenset({Verdict.FLAGGED, Verdict.SUSPICIOUS, Verdict.ERROR})


def _domain_line(d: Domain) -> str:
    return f"<code>{_e(d.name)}</code>  <i>{_e(source_label(d.source))}</i>"


def render_list(domains: Sequence[Domain], title: str, *, empty_hint: str) -> str:
    if not domains:
        return f"<b>{_e(title)}</b>\n\n{empty_hint}"

    groups: dict[Verdict, list[Domain]] = {}
    for d in domains:
        groups.setdefault(d.current_verdict, []).append(d)

    lines = [f"<b>{_e(title)}</b> · {len(domains)}"]
    size = len(lines[0])
    shown = 0
    for v in VERDICT_ORDER:
        group = groups.get(v)
        if not group:
            continue
        header = f"<b>{VERDICT_RU[v].capitalize()}</b> · {len(group)}"
        if shown >= LIST_LIMIT or size + len(header) > MESSAGE_BUDGET:
            break
        lines += ["", header]
        size += len(header) + 2
        for d in group:
            line = _domain_line(d)
            if shown >= LIST_LIMIT or size + len(line) > MESSAGE_BUDGET:
                break
            lines.append(line)
            size += len(line) + 1
            shown += 1
    if shown < len(domains):
        lines += ["", f"<i>И ещё {len(domains) - shown}. Сузьте фильтр: /list flagged</i>"]
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
