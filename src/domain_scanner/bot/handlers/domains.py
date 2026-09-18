from __future__ import annotations

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
from domain_scanner.services.scanner import ScanReport, collect_monitored_domain_ids
from domain_scanner.utils import normalize_domain

if TYPE_CHECKING:
    from domain_scanner.app import Application

router = Router(name="domains")

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
async def cmd_scan_now(message: Message, app: Application) -> None:
    ids = await collect_monitored_domain_ids()
    if not ids:
        await message.answer("Доменов пока нет: /sync_now")
        return
    progress = await message.answer(render.render_scan_started(len(ids)))

    reports = await app.scanner.scan_many(ids)
    for report in reports:
        if report.needs_alert:
            await app.notifier.notify_scan(report)

    await progress.edit_text(render.render_scan_done(reports))
