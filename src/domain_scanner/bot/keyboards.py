from __future__ import annotations

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


class DomainAction(CallbackData, prefix="dom"):
    action: str  # "recheck" | "mute" | "unmute"
    domain_id: int


class StatusAction(CallbackData, prefix="st"):
    action: str  # "bad"


def status_keyboard() -> InlineKeyboardMarkup:
    """Button under /status that sends the bad domains as a plain list."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Плохие домены",
                    callback_data=StatusAction(action="bad").pack(),
                )
            ]
        ]
    )


def domain_keyboard(domain_id: int, *, monitoring_enabled: bool = True) -> InlineKeyboardMarkup:
    """Buttons under an alert or a /check card."""
    toggle = (
        InlineKeyboardButton(
            text="Не отслеживать",
            callback_data=DomainAction(action="mute", domain_id=domain_id).pack(),
        )
        if monitoring_enabled
        else InlineKeyboardButton(
            text="Отслеживать",
            callback_data=DomainAction(action="unmute", domain_id=domain_id).pack(),
        )
    )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Перепроверить",
                    callback_data=DomainAction(action="recheck", domain_id=domain_id).pack(),
                ),
                toggle,
            ]
        ]
    )
