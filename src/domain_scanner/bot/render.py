"""Every user-facing Telegram message, as pure functions returning HTML.

Kept free of network and DB access so the layout can be unit-tested and tweaked
in one place. Style is deliberately plain: no emoji, hierarchy comes from bold
headlines, monospaced domains and italic metadata. Anything that comes from
outside (domains, API errors) must go through `_e`.
"""

from __future__ import annotations

import html
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, tzinfo

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


def fmt_time(moment: datetime, tz: tzinfo) -> str:
    local = moment.astimezone(tz)
    return f"{local:%d.%m, %H:%M} {local.tzname()}"


def _pct(part: int, total: int) -> int:
    return round(100 * part / total) if total else 0


# ── Help / startup ────────────────────────────────────────────────────────────

BOT_COMMANDS: tuple[tuple[str, str], ...] = (
    ("status", "Сводка по доменам"),
    ("list", "Проблемные домены"),
    ("check", "Проверить домен"),
    ("jobs", "Расписание проверок"),
    ("scan_now", "Проверить очередь сейчас"),
    ("sync_now", "Обновить списки из источников"),
    ("add", "Добавить домен вручную"),
    ("help", "Справка"),
)


def render_help() -> str:
    return (
        "<b>DomainScannerBot</b>\n"
        "Следит за репутацией доменов и сообщает в группу, когда домен зашкварен.\n\n"
        "/status — сводка\n"
        "/list — проблемные домены, <code>/list all</code> — все\n"
        "/jobs — расписание проверок\n\n"
        "/check <code>домен</code> — проверить сейчас\n"
        "/add <code>домен</code> — добавить вручную\n"
        "/scan_now — проверить очередь\n"
        "/sync_now — обновить списки из источников"
    )


def render_startup(
    sources: Sequence[str],
    checkers: Sequence[str],
    sync_interval: int,
    scan_interval: int,
) -> str:
    src = ", ".join(sources) if sources else "не подключены — проверьте .env"
    chk = ", ".join(checker_label(c) for c in checkers)
    return (
        "<b>DomainScannerBot запущен</b>\n\n"
        f"Источники: {_e(src)}\n"
        f"Проверки: {_e(chk)}\n"
        f"<i>Синхронизация раз в {sync_interval} мин, "
        f"перепроверка раз в {scan_interval} мин</i>"
    )


# ── Scan reports (alert + /check card) ───────────────────────────────────────


def _outcome_line(o: CheckOutcome) -> str:
    name = _e(checker_label(o.checker))
    # Only what needs attention is bold; a clean check stays quiet.
    if o.verdict is not Verdict.CLEAN:
        name = f"<b>{name}</b>"
    return f"{name} — {_e(_clip(o.summary or o.error or '—'))}"


def render_report(report: ScanReport, tz: tzinfo, *, alert: bool = False) -> str:
    """Card for one scanned domain.

    `alert=True` is the group notification: it always shows the previous state,
    since the change is the whole point.
    """
    meta = [_e(source_label(report.source))]
    if alert or report.changed:
        meta.append(f"было: {VERDICT_RU[report.previous_verdict]}")

    lines = [
        f"<b>{VERDICT_TITLE[report.verdict]}</b>",
        f"<code>{_e(report.domain)}</code>",
        f"<i>{' · '.join(meta)}</i>",
    ]
    if report.outcomes:
        lines += ["", *(_outcome_line(o) for o in report.outcomes)]
    if report.finished_at:
        lines += ["", f"<i>{fmt_time(report.finished_at, tz)}</i>"]
    return "\n".join(lines)


def render_checking(domain: str) -> str:
    return f"Проверяю <code>{_e(domain)}</code>…"


# ── /status ──────────────────────────────────────────────────────────────────


def render_status(stats: DomainStats) -> str:
    total = stats.monitored
    if not total and not stats.muted:
        return "<b>Сводка</b>\n\nДоменов пока нет. Обновите списки из источников: /sync_now"

    lines = ["<b>Сводка</b>", _domains(total), ""]
    lines += [
        f"{VERDICT_RU[v].capitalize()} — {stats.by_verdict[v]}"
        for v in VERDICT_ORDER
        if stats.by_verdict.get(v)
    ]

    checked = total - stats.by_verdict.get(Verdict.UNKNOWN, 0)
    if checked:
        clean = stats.by_verdict.get(Verdict.CLEAN, 0)
        lines += ["", f"Чистых — {_pct(clean, checked)}%"]

    footer = []
    if stats.by_source:
        footer.append(
            " · ".join(
                f"{_e(source_label(s))} {n}"
                for s, n in sorted(stats.by_source.items(), key=lambda kv: -kv[1])
            )
        )
    if stats.muted:
        footer.append(f"Не отслеживается — {stats.muted}")
    if footer:
        lines += ["", *(f"<i>{f}</i>" for f in footer)]
    return "\n".join(lines)


# ── /list ────────────────────────────────────────────────────────────────────

PROBLEM_VERDICTS = frozenset({Verdict.FLAGGED, Verdict.SUSPICIOUS, Verdict.ERROR})


def _domain_line(d: Domain) -> str:
    meta = source_label(d.source)
    if not d.monitoring_enabled:
        meta += " · не отслеживается"
    return f"<code>{_e(d.name)}</code>  <i>{_e(meta)}</i>"


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


# ── /jobs ────────────────────────────────────────────────────────────────────


@dataclass(slots=True)
class JobInfo:
    name: str
    every_minutes: int
    next_run: datetime | None


def render_jobs(
    jobs: Iterable[JobInfo],
    now: datetime,
    tz: tzinfo,
    *,
    scan_interval: int,
    batch_size: int,
    queue: int,
) -> str:
    lines = ["<b>Расписание</b>", ""]
    for job in jobs:
        if job.next_run is None:
            when = "на паузе"
        else:
            mins = max(0, round((job.next_run - now).total_seconds() / 60))
            when = f"следующий запуск {fmt_time(job.next_run, tz)}, через {mins} мин"
        lines.append(f"{_e(job.name)} — каждые {job.every_minutes} мин")
        lines.append(f"<i>{when}</i>")
    lines += [
        "",
        f"Домен перепроверяется раз в {scan_interval} мин, до {batch_size} за прогон.",
        f"В очереди: {queue}",
    ]
    return "\n".join(lines)


# ── /sync_now, sync failures ─────────────────────────────────────────────────


def _sync_block(r: SyncResult) -> str:
    title = _e(r.title)
    if not r.ok:
        return f"<b>{title}</b> — ошибка\n<i>{_e(_clip(r.error or ''))}</i>"
    parts = [f"новых {r.created}", f"обновлено {r.updated}", f"удалено из источника {r.removed}"]
    if r.foreign:
        parts.append(f"уже в другом источнике {r.foreign}")
    return f"<b>{title}</b> — {_domains(r.fetched)}\n<i>{' · '.join(parts)}</i>"


def render_sync(results: Sequence[SyncResult]) -> str:
    if not results:
        return (
            "<b>Синхронизация</b>\n\n"
            "Ни один источник не подключён. Заполните в .env доступы "
            "PWA.partners и/или SkakApp."
        )
    return "<b>Синхронизация</b>\n\n" + "\n\n".join(_sync_block(r) for r in results)


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


def render_scan_started(count: int, queue: int) -> str:
    tail = f" из {queue} в очереди" if queue > count else ""
    return f"Проверяю {_domains(count)}{tail}…"


def render_scan_done(reports: Sequence[ScanReport], remaining: int) -> str:
    counts: dict[Verdict, int] = {}
    for r in reports:
        counts[r.verdict] = counts.get(r.verdict, 0) + 1
    summary = " · ".join(
        f"{VERDICT_RU[v]} {counts[v]}" for v in VERDICT_ORDER if counts.get(v)
    )
    lines = [f"<b>Проверено: {_domains(len(reports))}</b>", summary or "—"]
    alerts = sum(r.needs_alert for r in reports)
    if alerts:
        lines.append(f"Новых проблем: {alerts}, отправлено в группу")
    if remaining:
        lines += [
            "",
            f"<i>В очереди ещё {remaining} — проверит планировщик "
            f"или <code>/scan_now {remaining}</code></i>",
        ]
    return "\n".join(lines)
