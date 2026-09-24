from __future__ import annotations

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


class DomainAction(CallbackData, prefix="dom"):
    action: str  # "recheck" | "mute" | "unmute"
    domain_id: int


class ListAction(CallbackData, prefix="lst"):
    action: str  # "mute_new"


def list_keyboard(new_count: int) -> InlineKeyboardMarkup | None:
    """Button under /list that stops watching everything in "Новые" at once.

    The count is in the label so a tap is never a guess about what it touches.
    """
    if not new_count:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"Не отслеживать новые ({new_count})",
                    callback_data=ListAction(action="mute_new").pack(),
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
