from __future__ import annotations

from domain_scanner.config import Settings
from domain_scanner.logging import get_logger
from domain_scanner.sources.base import DomainProvider, SourceDomain, SourceError, dedupe
from domain_scanner.sources.pwa_partners import PwaPartnersProvider
from domain_scanner.sources.uclient import UClientProvider

__all__ = [
    "DomainProvider",
    "PwaPartnersProvider",
    "SourceDomain",
    "SourceError",
    "UClientProvider",
    "build_providers",
    "dedupe",
]

log = get_logger(__name__)


def build_providers(settings: Settings) -> list[DomainProvider]:
    """Every domain source whose credentials are configured."""
    providers: list[DomainProvider] = []
    if settings.pwa_api_key and settings.pwa_team_uuid:
        providers.append(
            PwaPartnersProvider(
                base_url=settings.pwa_api_base_url,
                api_key=settings.pwa_api_key,
                team_uuid=settings.pwa_team_uuid,
                teamate_uuid=settings.pwa_teamate_uuid,
            )
        )
    if settings.uclient_login and settings.uclient_password:
        providers.append(
            UClientProvider(
                base_url=settings.uclient_api_base_url,
                login=settings.uclient_login,
                password=settings.uclient_password,
            )
        )
    elif settings.uclient_login or settings.uclient_password:
        # Half-configured: say so instead of silently leaving the source out.
        log.warning("source.uclient.incomplete", need="UCLIENT_LOGIN and UCLIENT_PASSWORD")
    return providers
