from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from domain_scanner.clients.pwa_partners import PwaPartnersClient
from domain_scanner.db import session_scope
from domain_scanner.db.models import Domain, DomainSource, SyncLog
from domain_scanner.logging import get_logger
from domain_scanner.repositories import DomainRepository

log = get_logger(__name__)


@dataclass(slots=True)
class SyncResult:
    fetched: int = 0
    created: int = 0
    updated: int = 0
    deactivated: int = 0


class DomainSyncService:
    """Pulls the domain list from PWA.partners and reconciles it with the DB."""

    def __init__(self, client_factory) -> None:
        self._client_factory = client_factory

    async def run(self) -> SyncResult:
        started = datetime.now(UTC)
        result = SyncResult()
        error: str | None = None
        try:
            client: PwaPartnersClient
            async with self._client_factory() as client:
                pwa_domains = await client.iter_domains()
            result.fetched = len(pwa_domains)

            async with session_scope() as session:
                repo = DomainRepository(session)
                seen_uuids: set[str] = set()

                for pd in pwa_domains:
                    if pd.uuid:
                        seen_uuids.add(pd.uuid)
                    domain = None
                    if pd.uuid:
                        domain = await repo.get_by_pwa_uuid(pd.uuid)
                    if domain is None:
                        domain = await repo.get_by_name(pd.domain)

                    if domain is None:
                        session.add(
                            Domain(
                                name=pd.domain,
                                source=DomainSource.PWA,
                                pwa_uuid=pd.uuid or None,
                                pwa_status=pd.status,
                                pwa_pwa_uuid=pd.pwa_uuid,
                                is_active=pd.is_active,
                            )
                        )
                        result.created += 1
                    else:
                        domain.pwa_uuid = domain.pwa_uuid or (pd.uuid or None)
                        domain.pwa_status = pd.status
                        domain.pwa_pwa_uuid = pd.pwa_uuid
                        domain.is_active = pd.is_active
                        if domain.source == DomainSource.MANUAL and pd.uuid:
                            domain.source = DomainSource.PWA
                        result.updated += 1

                # Deactivate PWA-sourced domains that vanished from the API.
                for domain in await repo.list_all():
                    if (
                        domain.source == DomainSource.PWA
                        and domain.is_active
                        and domain.pwa_uuid
                        and domain.pwa_uuid not in seen_uuids
                    ):
                        domain.is_active = False
                        result.deactivated += 1

                session.add(
                    SyncLog(
                        started_at=started,
                        finished_at=datetime.now(UTC),
                        fetched=result.fetched,
                        created=result.created,
                        updated=result.updated,
                        deactivated=result.deactivated,
                    )
                )
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            log.exception("sync.failed")
            async with session_scope() as session:
                session.add(
                    SyncLog(
                        started_at=started,
                        finished_at=datetime.now(UTC),
                        error=error,
                    )
                )
            raise

        log.info(
            "sync.done",
            fetched=result.fetched,
            created=result.created,
            updated=result.updated,
            deactivated=result.deactivated,
        )
        return result
