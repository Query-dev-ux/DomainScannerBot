from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject

from domain_scanner.logging import get_logger

log = get_logger(__name__)


class AdminOnlyMiddleware(BaseMiddleware):
    """Drops updates from users not in the admin allowlist."""

    def __init__(self, admin_ids: set[int]) -> None:
        self._admin_ids = admin_ids

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if user is not None and self._admin_ids and user.id not in self._admin_ids:
            if isinstance(event, Message):
                await event.answer("⛔ Нет доступа.")
            log.warning("bot.access_denied", user_id=user.id)
            return None
        return await handler(event, data)
