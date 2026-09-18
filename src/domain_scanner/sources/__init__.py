from __future__ import annotations

from domain_scanner.config import Settings
from domain_scanner.logging import get_logger
from domain_scanner.sources.base import DomainProvider, SourceDomain, SourceError, dedupe
from domain_scanner.sources.pwa_partners import PwaPartnersProvider
from domain_scanner.sources.skakapp import SkakAppProvider

__all__ = [
    "DomainProvider",
    "PwaPartnersProvider",
    "SkakAppProvider",
    "SourceDomain",
    "SourceError",
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
    if settings.skakapp_login and settings.skakapp_password:
        providers.append(
            SkakAppProvider(
                base_url=settings.skakapp_api_base_url,
                login=settings.skakapp_login,
                password=settings.skakapp_password,
            )
        )
    elif settings.skakapp_login or settings.skakapp_password:
        # Half-configured: say so instead of silently leaving the source out.
        log.warning("source.skakapp.incomplete", need="SKAKAPP_LOGIN and SKAKAPP_PASSWORD")
    return providers
