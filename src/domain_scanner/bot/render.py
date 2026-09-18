"""Every user-facing Telegram message, as pure functions returning HTML.

Kept free of network and DB access so the layout can be unit-tested and tweaked
in one place. Telegram HTML supports <b>, <i>, <code>, <blockquote> and friends;
anything that comes from outside (domains, API errors) must go through `_e`.
"""

from __future__ import annotations

import html
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, tzinfo

from domain_scanner.checkers.base import CheckOutcome
from domain_scanner.db.models import Domain, Verdict
from domain_scanner.labels import (
    VERDICT_EMOJI,
    VERDICT_ORDER,
    VERDICT_RU,
    VERDICT_TITLE,
    checker_label,
    source_label,
)
from domain_scanner.repositories.domains import DomainStats
from domain_scanner.services.scanner import ScanReport
from domain_scanner.services.sync import SyncResult

LIST_LIMIT = 60
# Telegram rejects messages over 4096 chars; leave room for the header/footer.
MESSAGE_BUDGET = 3600
DETAIL_LIMIT = 180
BAR_WIDTH = 10


def _e(text: object) -> str:
    return html.escape(str(text), quote=False)


def _clip(text: str, limit: int = DETAIL_LIMIT) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def fmt_time(moment: datetime, tz: tzinfo) -> str:
    local = moment.astimezone(tz)
    return f"{local:%d.%m, %H:%M} {local.tzname()}"


def bar(part: int, total: int, width: int = BAR_WIDTH) -> str:
    if total <= 0:
        return "▱" * width
    # Floor, so a full bar means exactly 100% — 99% must not look perfect.
    filled = width * part // total
    return "▰" * filled + "▱" * (width - filled)


def _pct(part: int, total: int) -> int:
    return round(100 * part / total) if total else 0


# ── Help / startup ────────────────────────────────────────────────────────────

BOT_COMMANDS: tuple[tuple[str, str], ...] = (
    ("status", "Сводка по доменам"),
    ("list", "Проблемные домены"),
    ("check", "Проверить домен сейчас"),
    ("jobs", "Расписание автопроверок"),
    ("scan_now", "Прогнать очередь проверки"),
    ("sync_now", "Подтянуть домены из источников"),
    ("add", "Добавить домен вручную"),
    ("help", "Справка"),
)


def render_help() -> str:
    return (
        "🛰 <b>DomainScannerBot</b>\n"
        "Слежу за репутацией доменов и пишу в группу, как только домен "
        "начинает зашквариваться.\n\n"
        "<b>Обзор</b>\n"
        "/status — сводка по доменам\n"
        "/list — проблемные домены · <code>/list all</code> — все\n"
        "/jobs — расписание автопроверок\n\n"
        "<b>Действия</b>\n"
        "/check <code>домен</code> — проверить прямо сейчас\n"
        "/add <code>домен</code> — добавить вручную\n"
        "/scan_now — прогнать очередь проверки\n"
        "/sync_now — подтянуть домены из источников\n\n"
        "<i>Под алертами есть кнопки: перепроверить домен или "
        "убрать его из мониторинга.</i>"
    )


def render_startup(
    sources: Sequence[str],
    checkers: Sequence[str],
    sync_interval: int,
    scan_interval: int,
) -> str:
    src = ", ".join(sources) if sources else "⚠️ не подключены — проверьте .env"
    chk = ", ".join(checker_label(c) for c in checkers)
    return (
        "🟢 <b>DomainScannerBot в строю</b>\n\n"
        f"🔌 <b>Источники:</b> {_e(src)}\n"
        f"🧪 <b>Проверки:</b> {_e(chk)}\n"
        f"⏱ Синк раз в {sync_interval} мин · перепроверка раз в {scan_interval} мин"
    )


# ── Scan reports (alert + /check card) ───────────────────────────────────────


def _outcome_line(o: CheckOutcome) -> str:
    detail = o.summary or o.error or "—"
    return f"{VERDICT_EMOJI[o.verdict]} <b>{_e(checker_label(o.checker))}</b> — {_e(_clip(detail))}"


def render_report(report: ScanReport, tz: tzinfo, *, alert: bool = False) -> str:
    """Card for one scanned domain.

    `alert=True` is the group notification: it always shows where the domain came
    from and what it was before, since the change is the whole point.
    """
    v = report.verdict
    meta = [f"🔌 {_e(source_label(report.source))}"]
    if alert or report.changed:
        prev = report.previous_verdict
        meta.append(f"было: {VERDICT_EMOJI[prev]} {VERDICT_RU[prev]}")

    lines = [
        f"{VERDICT_EMOJI[v]} <b>{VERDICT_TITLE[v]}</b>",
        "",
        f"🌐 <code>{_e(report.domain)}</code>",
        "  ·  ".join(meta),
    ]
    if report.outcomes:
        checks = "\n".join(_outcome_line(o) for o in report.outcomes)
        lines += ["", f"<blockquote>{checks}</blockquote>"]
    if report.finished_at:
        lines += ["", f"<i>🕒 {fmt_time(report.finished_at, tz)}</i>"]
    return "\n".join(lines)


def render_checking(domain: str) -> str:
    return f"⏳ Проверяю <code>{_e(domain)}</code>…"


# ── /status ──────────────────────────────────────────────────────────────────


def render_status(stats: DomainStats) -> str:
    total = stats.monitored
    if not total and not stats.muted and not stats.inactive:
        return (
            "📊 <b>Сводка</b>\n\n"
            "Доменов пока нет. Подтяните их из источников — /sync_now"
        )

    lines = [f"📊 <b>Сводка</b> · {total} в мониторинге", ""]
    for v in VERDICT_ORDER:
        count = stats.by_verdict.get(v, 0)
        lines.append(f"{VERDICT_EMOJI[v]} {VERDICT_RU[v].capitalize()} — <b>{count}</b>")

    checked = total - stats.by_verdict.get(Verdict.UNKNOWN, 0)
    clean = stats.by_verdict.get(Verdict.CLEAN, 0)
    if checked:
        lines += ["", f"<code>{bar(clean, checked)}</code>  {_pct(clean, checked)}% чистых"]

    if stats.by_source:
        parts = [
            f"{_e(source_label(s))} — {n}"
            for s, n in sorted(stats.by_source.items(), key=lambda kv: -kv[1])
        ]
        lines += ["", "🔌 " + " · ".join(parts)]
    extra = []
    if stats.muted:
        extra.append(f"🔕 без мониторинга — {stats.muted}")
    if stats.inactive:
        extra.append(f"💤 неактивных — {stats.inactive}")
    if extra:
        lines.append(" · ".join(extra))
    return "\n".join(lines)


# ── /list ────────────────────────────────────────────────────────────────────

PROBLEM_VERDICTS = frozenset({Verdict.FLAGGED, Verdict.SUSPICIOUS, Verdict.ERROR})


def _domain_line(d: Domain) -> str:
    marks = ""
    if not d.is_active:
        marks += " 💤"
    if not d.monitoring_enabled:
        marks += " 🔕"
    return f"<code>{_e(d.name)}</code>{marks}  <i>{_e(source_label(d.source))}</i>"


def render_list(domains: Sequence[Domain], title: str, *, empty_hint: str) -> str:
    if not domains:
        return f"📋 <b>{_e(title)}</b>\n\n{empty_hint}"

    groups: dict[Verdict, list[Domain]] = {}
    for d in domains:
        groups.setdefault(d.current_verdict, []).append(d)

    lines = [f"📋 <b>{_e(title)}</b> · {len(domains)}"]
    size = len(lines[0])
    shown = 0
    for v in VERDICT_ORDER:
        group = groups.get(v)
        if not group:
            continue
        header = f"{VERDICT_EMOJI[v]} <b>{VERDICT_RU[v].capitalize()}</b> · {len(group)}"
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
        lines += ["", f"<i>…и ещё {len(domains) - shown}. Сузьте фильтр: /list flagged</i>"]
    return "\n".join(lines)


# ── /jobs ────────────────────────────────────────────────────────────────────


@dataclass(slots=True)
class JobInfo:
    icon: str
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
    lines = ["⏱ <b>Автоматический режим</b>", ""]
    for job in jobs:
        if job.next_run is None:
            when = "⏸ на паузе"
        else:
            mins = max(0, round((job.next_run - now).total_seconds() / 60))
            when = f"следующий через ~{mins} мин ({fmt_time(job.next_run, tz)})"
        lines.append(f"{job.icon} <b>{_e(job.name)}</b>")
        lines.append(f"<i>каждые {job.every_minutes} мин · {when}</i>")
    lines += [
        "",
        f"<blockquote>Домен перепроверяется раз в {scan_interval} мин, "
        f"до {batch_size} шт. за прогон.\n"
        f"В очереди сейчас: <b>{queue}</b></blockquote>",
    ]
    return "\n".join(lines)


# ── /sync_now, sync failures ─────────────────────────────────────────────────


def _sync_block(r: SyncResult) -> str:
    title = _e(r.title)
    if not r.ok:
        return f"🛑 <b>{title}</b> — ошибка\n<i>{_e(_clip(r.error or ''))}</i>"
    stats = f"+{r.created} новых · {r.updated} обновлено · {r.deactivated} выключено"
    if r.foreign:
        stats += f" · {r.foreign} у другого источника"
    return f"✅ <b>{title}</b> — {r.fetched} доменов\n<i>{stats}</i>"


def render_sync(results: Sequence[SyncResult]) -> str:
    if not results:
        return (
            "🔄 <b>Синхронизация</b>\n\n"
            "Ни один источник не подключён. Заполните в .env ключи "
            "PWA.partners и/или UClient."
        )
    return "🔄 <b>Синхронизация</b>\n\n" + "\n\n".join(_sync_block(r) for r in results)


def render_sync_failure(results: Sequence[SyncResult]) -> str:
    failed = [r for r in results if not r.ok]
    return "🛑 <b>Синхронизация не удалась</b>\n\n" + "\n\n".join(
        _sync_block(r) for r in failed
    )


def render_job_crash(job: str) -> str:
    return f"🛑 <b>{_e(job)} упало</b>\nПодробности в логах: <code>docker compose logs bot</code>"


# ── /scan_now ────────────────────────────────────────────────────────────────


def render_scan_started(count: int, queue: int) -> str:
    tail = f" из {queue} в очереди" if queue > count else ""
    return f"🔍 Проверяю <b>{count}</b> доменов{tail}…"


def render_scan_done(reports: Sequence[ScanReport], remaining: int) -> str:
    counts: dict[Verdict, int] = {}
    for r in reports:
        counts[r.verdict] = counts.get(r.verdict, 0) + 1
    summary = " · ".join(
        f"{VERDICT_EMOJI[v]} {counts[v]}" for v in VERDICT_ORDER if counts.get(v)
    )
    alerts = sum(r.needs_alert for r in reports)
    lines = [f"✅ <b>Проверено {len(reports)}</b>", summary or "—"]
    if alerts:
        lines.append(f"🔔 Новых проблем: <b>{alerts}</b> — алерты ушли в группу")
    if remaining:
        lines += [
            "",
            f"<i>В очереди ещё {remaining} — доберёт планировщик "
            f"или <code>/scan_now {remaining}</code></i>",
        ]
    return "\n".join(lines)
