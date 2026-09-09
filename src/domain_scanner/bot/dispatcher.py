from __future__ import annotations

from typing import TYPE_CHECKING

from aiogram import Dispatcher

from domain_scanner.bot.handlers import common, domains
from domain_scanner.bot.middlewares import AdminOnlyMiddleware

if TYPE_CHECKING:
    from domain_scanner.app import Application


def build_dispatcher(app: Application) -> Dispatcher:
    dp = Dispatcher()
    dp["app"] = app

    admin_mw = AdminOnlyMiddleware(app.settings.admin_id_set)
    dp.message.middleware(admin_mw)
    dp.callback_query.middleware(admin_mw)

    dp.include_router(common.router)
    dp.include_router(domains.router)
    return dp
