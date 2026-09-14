from __future__ import annotations

import html
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from sqlalchemy import select

from domain_scanner.db import session_scope
from domain_scanner.db.models import Domain, Verdict
from domain_scanner.labels import VERDICT_EMOJI, VERDICT_RU
from domain_scanner.repositories import DomainRepository

if TYPE_CHECKING:
    from domain_scanner.app import Application

router = Router(name="domains")

_DOMAIN_RE = re.compile(r"^(?=.{1,253}$)(?!-)[a-z0-9-]{1,63}(\.[a-z0-9-]{1,63})+$")


def _normalize_domain(raw: str) -> str | None:
    value = raw.strip().lower()
    value = re.sub(r"^https?://", "", value)
    value = value.split("/")[0].split("?")[0]
    if value.startswith("www."):
        value = value[4:]
    return value if _DOMAIN_RE.match(value) else None


def _e(text: str) -> str:
    return html.escape(text, quote=False)


@router.message(Command("status"))
async def cmd_status(message: Message, app: Application) -> None:
    async with session_scope() as session:
        repo = DomainRepository(session)
        counts = await repo.counts_by_verdict()
        total = sum(counts.values())
    order = (
        Verdict.FLAGGED,
        Verdict.SUSPICIOUS,
        Verdict.CLEAN,
        Verdict.ERROR,
        Verdict.UNKNOWN,
    )
    lines = [f"<b>Всего доменов:</b> {total}", ""]
    lines += [
        f"{VERDICT_EMOJI[v]} {VERDICT_RU[v]}: {counts.get(v, 0)}" for v in order
    ]
    await message.answer("\n".join(lines))


@router.message(Command("list"))
async def cmd_list(message: Message, command: CommandObject, app: Application) -> None:
    arg = (command.args or "").strip().lower()
    wanted: Verdict | None = None
    if arg:
        try:
            wanted = Verdict(arg)
        except ValueError:
            await message.answer("Неизвестный фильтр. Используй: clean | suspicious | flagged")
            return

    async with session_scope() as session:
        stmt = select(Domain).order_by(Domain.name)
        if wanted is not None:
            stmt = stmt.where(Domain.current_verdict == wanted)
        rows = (await session.scalars(stmt)).all()

    if not rows:
        await message.answer("Ничего не найдено.")
        return

    lines = [
        f"{VERDICT_EMOJI[d.current_verdict]} <code>{_e(d.name)}</code>"
        + ("" if d.is_active else " <i>(неактивен)</i>")
        for d in rows[:50]
    ]
    if len(rows) > 50:
        lines.append(f"… и ещё {len(rows) - 50}")
    await message.answer("\n".join(lines))


@router.message(Command("add"))
async def cmd_add(message: Message, command: CommandObject, app: Application) -> None:
    if not command.args:
        await message.answer("Использование: /add example.com")
        return
    name = _normalize_domain(command.args)
    if name is None:
        await message.answer("Не похоже на домен.")
        return
    async with session_scope() as session:
        repo = DomainRepository(session)
        _, created = await repo.add_manual(name)
    if created:
        await message.answer(f"Добавлен: <code>{_e(name)}</code>. Проверю при следующем скане.")
    else:
        await message.answer(f"<code>{_e(name)}</code> уже в списке.")


@router.message(Command("check"))
async def cmd_check(message: Message, command: CommandObject, app: Application) -> None:
    if not command.args:
        await message.answer("Использование: /check example.com")
        return
    name = _normalize_domain(command.args)
    if name is None:
        await message.answer("Не похоже на домен.")
        return

    async with session_scope() as session:
        repo = DomainRepository(session)
        domain, _ = await repo.add_manual(name)
        domain_id = domain.id

    await message.answer(f"🔍 Проверяю <code>{_e(name)}</code>…")
    report = await app.scanner.scan_domain(domain_id)
    if report is None:
        await message.answer("Не удалось проверить домен.")
        return

    lines = [
        f"{VERDICT_EMOJI[report.verdict]} <code>{_e(name)}</code> — "
        f"<b>{VERDICT_RU[report.verdict]}</b>",
        "",
    ]
    for o in report.outcomes:
        lines.append(
            f"{VERDICT_EMOJI[o.verdict]} <b>{_e(o.checker)}</b>: "
            f"{_e(o.summary or o.error or '—')}"
        )
    await message.answer("\n".join(lines))


@router.message(Command("sync_now"))
async def cmd_sync_now(message: Message, app: Application) -> None:
    await message.answer("⏳ Запускаю синхронизацию с PWA API…")
    try:
        result = await app.sync_service.run()
    except Exception as exc:
        await message.answer(f"🛑 Ошибка: {_e(str(exc))}")
        return
    await message.answer(
        f"✅ Готово. Получено: {result.fetched}, новых: {result.created}, "
        f"обновлено: {result.updated}, деактивировано: {result.deactivated}"
    )


@router.message(Command("scan_now"))
async def cmd_scan_now(message: Message, command: CommandObject, app: Application) -> None:
    from domain_scanner.services.scanner import collect_due_domain_ids, count_due_domains

    interval = app.settings.scan_interval_minutes
    limit = app.settings.scan_batch_size
    arg = (command.args or "").strip()
    if arg:
        if not arg.isdigit() or int(arg) < 1:
            await message.answer("Использование: /scan_now [сколько доменов]")
            return
        limit = int(arg)

    total_due = await count_due_domains(interval)
    if not total_due:
        await message.answer("Нет доменов, готовых к проверке.")
        return

    ids = await collect_due_domain_ids(interval, limit=limit)
    tail = f" (всего в очереди {total_due})" if total_due > len(ids) else ""
    await message.answer(f"🔍 Сканирую {len(ids)} доменов{tail}…")

    reports = await app.scanner.scan_many(ids)
    alerts = [r for r in reports if r.needs_alert]
    for report in alerts:
        await app.notifier.notify_scan(report)

    remaining = await count_due_domains(interval)
    lines = [
        f"✅ Просканировано: {len(reports)}. Изменений со статусом «алерт»: {len(alerts)}."
    ]
    if remaining:
        lines.append(
            f"Осталось в очереди: {remaining} — доберёт планировщик, "
            f"либо запусти <code>/scan_now {remaining}</code>."
        )
    await message.answer("\n".join(lines))


@router.message(Command("jobs"))
async def cmd_jobs(message: Message, app: Application) -> None:
    from domain_scanner.services.scanner import count_due_domains

    jobs = app.scheduler.get_jobs()
    if not jobs:
        await message.answer("🛑 Планировщик не зарегистрировал ни одной задачи.")
        return

    now = datetime.now(UTC)
    lines = ["<b>Автоматический режим</b>", ""]
    for job in jobs:
        nxt = job.next_run_time
        if nxt is None:
            when = "⏸ на паузе"
        else:
            mins = max(0, round((nxt - now).total_seconds() / 60))
            when = f"через ~{mins} мин ({nxt:%H:%M} UTC)"
        lines.append(f"• <b>{_e(job.name or job.id)}</b>\n  {when}")

    due = await count_due_domains(app.settings.scan_interval_minutes)
    lines += [
        "",
        f"Синхронизация раз в {app.settings.sync_interval_minutes} мин.",
        f"Домен перепроверяется не чаще раза в {app.settings.scan_interval_minutes} мин, "
        f"до {app.settings.scan_batch_size} шт. за прогон.",
        f"Сейчас в очереди на проверку: <b>{due}</b>.",
    ]
    await message.answer("\n".join(lines))
