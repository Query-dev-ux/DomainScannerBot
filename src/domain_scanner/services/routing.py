from __future__ import annotations

from domain_scanner.db import session_scope
from domain_scanner.repositories import AlertRouteRepository
from domain_scanner.services.scanner import ScanReport


async def route_for_report(report: ScanReport) -> int | None:
    """The chat this domain's alert belongs in, or None for the default group."""
    async with session_scope() as session:
        return await AlertRouteRepository(session).chat_for(report.source, report.owner)
