from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from domain_scanner.db.models import AlertRoute, DomainSource


def owner_key(owner: str) -> str:
    """Logins are typed by hand, so match them case-insensitively."""
    return owner.strip().lower()


class AlertRouteRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_all(self) -> Sequence[AlertRoute]:
        return (
            await self._session.scalars(
                select(AlertRoute).order_by(AlertRoute.source, AlertRoute.owner)
            )
        ).all()

    async def chat_for(self, source: DomainSource | None, owner: str | None) -> int | None:
        """The chat this domain's alerts belong in, or None for the default one."""
        if source is None or not owner:
            return None
        return await self._session.scalar(
            select(AlertRoute.chat_id).where(
                AlertRoute.source == source,
                AlertRoute.owner_key == owner_key(owner),
            )
        )

    async def set_route(
        self,
        source: DomainSource,
        owner: str,
        chat_id: int,
        chat_title: str | None = None,
    ) -> AlertRoute:
        """Point this owner's alerts at a chat, replacing any earlier rule."""
        key = owner_key(owner)
        route = await self._session.scalar(
            select(AlertRoute).where(
                AlertRoute.source == source, AlertRoute.owner_key == key
            )
        )
        if route is None:
            route = AlertRoute(source=source, owner_key=key)
            self._session.add(route)
        route.owner = owner.strip()
        route.chat_id = chat_id
        route.chat_title = chat_title
        await self._session.flush()
        return route

    async def delete_owner(self, owner: str) -> int:
        """Drop the rules for this name in every source; returns how many went."""
        result = await self._session.execute(
            delete(AlertRoute).where(AlertRoute.owner_key == owner_key(owner))
        )
        return result.rowcount or 0
