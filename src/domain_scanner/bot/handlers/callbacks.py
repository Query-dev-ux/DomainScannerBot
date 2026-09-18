from __future__ import annotations

from typing import TYPE_CHECKING

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Message

from domain_scanner.bot import render
from domain_scanner.bot.handlers.domains import alert_if_needed
from domain_scanner.bot.keyboards import DomainAction, domain_keyboard
from domain_scanner.db import session_scope
from domain_scanner.repositories import DomainRepository

if TYPE_CHECKING:
    from domain_scanner.app import Application

router = Router(name="callbacks")


@router.callback_query(DomainAction.filter())
async def on_domain_action(
    query: CallbackQuery, callback_data: DomainAction, app: Application
) -> None:
    message = query.message if isinstance(query.message, Message) else None

    if callback_data.action == "recheck":
        await _recheck(query, message, callback_data.domain_id, app)
    elif callback_data.action in ("mute", "unmute"):
        await _toggle_monitoring(query, message, callback_data)
    else:
        await query.answer()


async def _recheck(
    query: CallbackQuery, message: Message | None, domain_id: int, app: Application
) -> None:
    async with session_scope() as session:
        domain = await DomainRepository(session).get(domain_id)
        monitoring = domain.monitoring_enabled if domain else True
    if domain is None:
        await query.answer("Домен уже удалён из базы", show_alert=True)
        return

    await query.answer("Проверяю…")
    report = await app.scanner.scan_domain(domain_id)
    if report is None:
        return
    text = render.render_report(report, app.tz)
    markup = domain_keyboard(domain_id, monitoring_enabled=monitoring)
    if message is not None:
        # A fresh card as a reply, so the original alert stays as history.
        await message.reply(text, reply_markup=markup)
        await alert_if_needed(app, report, message.chat.id)


async def _toggle_monitoring(
    query: CallbackQuery, message: Message | None, data: DomainAction
) -> None:
    enabled = data.action == "unmute"
    async with session_scope() as session:
        domain = await DomainRepository(session).set_monitoring(data.domain_id, enabled)
        name = domain.name if domain else None
    if name is None:
        await query.answer("Домен уже удалён из базы", show_alert=True)
        return

    await query.answer(
        f"{name}: снова отслеживается" if enabled else f"{name}: больше не отслеживается"
    )
    if message is not None:
        # Same message, flipped button — the text of the alert stays intact.
        try:
            await message.edit_reply_markup(
                reply_markup=domain_keyboard(data.domain_id, monitoring_enabled=enabled)
            )
        except TelegramBadRequest:
            pass  # markup unchanged (double tap) or message too old to edit
