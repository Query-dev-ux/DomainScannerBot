from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from domain_scanner.bot import render
from domain_scanner.bot.keyboards import domain_keyboard
from domain_scanner.db import session_scope
from domain_scanner.db.models import Verdict
from domain_scanner.labels import VERDICT_RU
from domain_scanner.repositories import DomainRepository
from domain_scanner.scheduler.jobs import SCAN_JOB_ID, SYNC_JOB_ID
from domain_scanner.services.scanner import (
    ScanReport,
    collect_due_domain_ids,
    count_due_domains,
)
from domain_scanner.utils import normalize_domain

if TYPE_CHECKING:
    from domain_scanner.app import Application

router = Router(name="domains")

_JOB_NAMES = {
    SYNC_JOB_ID: "Синхронизация источников",
    SCAN_JOB_ID: "Проверка доменов",
}


async def alert_if_needed(app: Application, report: ScanReport, chat_id: int) -> None:
    """A manual check can be the one that catches a domain going bad — the group
    must still hear about it, or the next scheduled scan will see no change."""
    if report.needs_alert and chat_id != app.notifier.chat_id:
        await app.notifier.notify_scan(report)


@router.message(Command("status"))
async def cmd_status(message: Message) -> None:
    async with session_scope() as session:
        stats = await DomainRepository(session).stats()
    await message.answer(render.render_status(stats))


@router.message(Command("list"))
async def cmd_list(message: Message, command: CommandObject) -> None:
    arg = (command.args or "").strip().lower()
    if not arg:
        verdicts: set[Verdict] | None = set(render.PROBLEM_VERDICTS)
        title = "Проблемные домены"
        hint = "Проблемных доменов нет. Все домены: /list all"
    elif arg == "all":
        verdicts, title, hint = None, "Все домены", "Доменов пока нет: /sync_now"
    else:
        try:
            wanted = Verdict(arg)
        except ValueError:
            options = " · ".join(f"<code>{v.value}</code>" for v in Verdict)
            await message.answer(f"Не знаю такой фильтр. Можно: <code>all</code> · {options}")
            return
        verdicts = {wanted}
        title = VERDICT_RU[wanted].capitalize()
        hint = "Таких доменов нет."

    async with session_scope() as session:
        domains = await DomainRepository(session).list_for_display(verdicts)
    await message.answer(render.render_list(domains, title, empty_hint=hint))


@router.message(Command("add"))
async def cmd_add(message: Message, command: CommandObject) -> None:
    name = normalize_domain(command.args)
    if name is None:
        await message.answer("Использование: <code>/add example.com</code>")
        return
    async with session_scope() as session:
        _, created = await DomainRepository(session).add_manual(name)
    if created:
        await message.answer(f"Добавлен <code>{render._e(name)}</code>.")
    else:
        await message.answer(f"<code>{render._e(name)}</code> уже в списке.")


@router.message(Command("check"))
async def cmd_check(message: Message, command: CommandObject, app: Application) -> None:
    name = normalize_domain(command.args)
    if name is None:
        await message.answer("Использование: <code>/check example.com</code>")
        return

    async with session_scope() as session:
        domain, _ = await DomainRepository(session).add_manual(name)
        domain_id, monitoring = domain.id, domain.monitoring_enabled

    progress = await message.answer(render.render_checking(name))
    report = await app.scanner.scan_domain(domain_id)
    if report is None:
        await progress.edit_text("Не удалось проверить домен.")
        return

    await progress.edit_text(
        render.render_report(report),
        reply_markup=domain_keyboard(domain_id, monitoring_enabled=monitoring),
    )
    await alert_if_needed(app, report, message.chat.id)


@router.message(Command("sync_now"))
async def cmd_sync_now(message: Message, app: Application) -> None:
    progress = await message.answer("Обновляю списки из источников…")
    results = await app.sync_service.run()
    await progress.edit_text(render.render_sync(results))


@router.message(Command("scan_now"))
async def cmd_scan_now(message: Message, command: CommandObject, app: Application) -> None:
    interval = app.settings.scan_interval_minutes
    limit = app.settings.scan_batch_size
    arg = (command.args or "").strip()
    if arg:
        if not arg.isdigit() or int(arg) < 1:
            await message.answer("Использование: <code>/scan_now [сколько доменов]</code>")
            return
        limit = int(arg)

    queue = await count_due_domains(interval)
    if not queue:
        await message.answer("Все домены проверены недавно.")
        return

    ids = await collect_due_domain_ids(interval, limit=limit)
    progress = await message.answer(render.render_scan_started(len(ids)))

    reports = await app.scanner.scan_many(ids)
    for report in reports:
        if report.needs_alert:
            await app.notifier.notify_scan(report)

    await progress.edit_text(render.render_scan_done(reports))


@router.message(Command("jobs"))
async def cmd_jobs(message: Message, app: Application) -> None:
    jobs = []
    for job in app.scheduler.get_jobs():
        name = _JOB_NAMES.get(job.id, job.name or job.id)
        interval = getattr(job.trigger, "interval", None)
        every = int(interval.total_seconds() // 60) if interval else 0
        jobs.append(render.JobInfo(name, every, job.next_run_time))

    if not jobs:
        await message.answer("Планировщик не зарегистрировал ни одной задачи.")
        return

    queue = await count_due_domains(app.settings.scan_interval_minutes)
    await message.answer(
        render.render_jobs(
            jobs,
            datetime.now(UTC),
            app.tz,
            scan_interval=app.settings.scan_interval_minutes,
            batch_size=app.settings.scan_batch_size,
            queue=queue,
        )
    )
