from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from domain_scanner.bot import render
from domain_scanner.db import session_scope
from domain_scanner.db.models import DomainSource
from domain_scanner.logging import get_logger
from domain_scanner.repositories import AlertRouteRepository

if TYPE_CHECKING:
    from domain_scanner.app import Application

log = get_logger(__name__)

router = Router(name="routes")

# A name typed as a dash means "this person has no account in that platform".
NO_NAME = {"-", "—", "–"}
# The order the two names are given in, which is also the order /routes lists them.
ROUTE_SOURCES: tuple[DomainSource, ...] = (DomainSource.PWA, DomainSource.SKAKAPP)


@dataclass(slots=True)
class RouteRequest:
    chat_id: int
    owners: dict[DomainSource, str]


def parse_route(args: str | None) -> RouteRequest | None:
    """`<chat id> <PWApartners name> <SkakApp name>`, a dash for a missing name.

    Returns None for anything malformed; the handler answers with the usage line
    rather than guessing what was meant.
    """
    parts = (args or "").split()
    if len(parts) != 3:
        return None
    raw_chat, *names = parts
    try:
        chat_id = int(raw_chat)
    except ValueError:
        return None
    owners = {
        source: name
        for source, name in zip(ROUTE_SOURCES, names, strict=True)
        if name not in NO_NAME
    }
    if not owners:
        return None
    return RouteRequest(chat_id=chat_id, owners=owners)


async def _chat_title(app: Application, chat_id: int) -> str | None:
    """The group's name, which is also the proof that the bot can post there."""
    chat = await app.bot.get_chat(chat_id)
    return chat.title or chat.full_name


@router.message(Command("route"))
async def cmd_route(message: Message, command: CommandObject, app: Application) -> None:
    request = parse_route(command.args)
    if request is None:
        await message.answer(render.render_route_usage())
        return

    # Writing to the chat is what the rule is for: check it before saving, or a
    # typo in the id would quietly send a team's alerts nowhere.
    try:
        title = await _chat_title(app, request.chat_id)
    except Exception as exc:
        log.warning("route.chat_unreachable", chat_id=request.chat_id, error=str(exc))
        await message.answer(render.render_route_unreachable(request.chat_id))
        return

    async with session_scope() as session:
        repo = AlertRouteRepository(session)
        for source, owner in request.owners.items():
            await repo.set_route(source, owner, request.chat_id, title)
        routes = await repo.list_all()
    await message.answer(render.render_routes(routes, app.notifier.chat_id))


@router.message(Command("routes"))
async def cmd_routes(message: Message, app: Application) -> None:
    async with session_scope() as session:
        routes = await AlertRouteRepository(session).list_all()
    await message.answer(render.render_routes(routes, app.notifier.chat_id))


@router.message(Command("route_del"))
async def cmd_route_del(message: Message, command: CommandObject, app: Application) -> None:
    name = (command.args or "").strip()
    if not name:
        await message.answer(render.render_route_usage())
        return

    async with session_scope() as session:
        repo = AlertRouteRepository(session)
        removed = await repo.delete_owner(name)
        routes = await repo.list_all()
    if not removed:
        await message.answer(render.render_route_unknown(name))
        return
    await message.answer(render.render_routes(routes, app.notifier.chat_id))
