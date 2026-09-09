from __future__ import annotations

from functools import lru_cache

from pydantic import PostgresDsn, computed_field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Telegram
    bot_token: str
    alert_chat_id: int
    alert_thread_id: int | None = None
    # Comma-separated list of Telegram user IDs in the env; empty = allow everyone.
    admin_ids: str = ""

    # PWA.partners Open API
    pwa_api_base_url: str = "https://openapi.pwa.partners/api"
    pwa_api_key: str
    pwa_team_uuid: str
    pwa_teamate_uuid: str | None = None

    # Checkers
    gsb_api_key: str | None = None
    virustotal_api_key: str | None = None

    # Database
    postgres_host: str = "db"
    postgres_port: int = 5432
    postgres_db: str = "domain_scanner"
    postgres_user: str = "domain_scanner"
    postgres_password: str = "change-me"

    # Scheduler
    sync_interval_minutes: int = 60
    scan_interval_minutes: int = 180
    scan_concurrency: int = 5

    log_level: str = "INFO"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def database_url(self) -> str:
        dsn = PostgresDsn.build(
            scheme="postgresql+asyncpg",
            username=self.postgres_user,
            password=self.postgres_password,
            host=self.postgres_host,
            port=self.postgres_port,
            path=self.postgres_db,
        )
        return str(dsn)

    @field_validator("alert_thread_id", mode="before")
    @classmethod
    def _empty_thread_id(cls, value: object) -> object:
        return None if value in ("", None) else value

    @property
    def admin_id_set(self) -> set[int]:
        return {
            int(part.strip())
            for part in self.admin_ids.split(",")
            if part.strip().lstrip("-").isdigit()
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
