"""Process settings read from HEIMDALL_* environment variables and an optional .env file."""

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]


class Settings(BaseSettings):
    """Typed configuration for every Heimdall process.

    Required values have no default, so a missing one fails at startup, not mid-request.

    Attributes:
        database_url: Postgres DSN.
        github_app_id: Numeric id of the Heimdall GitHub App.
        github_private_key_path: Path to the mounted App private key (.pem).
        github_webhook_secret: Shared secret for verifying webhook signatures.
        switch_url: Base URL of the Switch server, if the bridge is used.
        switch_token: Bot token for Switch.
        default_switch_room_id: Room newly enabled repos post to until an operator re-links them.
        log_level: Minimum level that gets logged.
    """

    model_config = SettingsConfigDict(
        env_prefix="HEIMDALL_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: SecretStr
    github_app_id: int
    github_private_key_path: str
    github_webhook_secret: SecretStr
    switch_url: str | None = None
    switch_token: SecretStr | None = None
    default_switch_room_id: str | None = None
    log_level: LogLevel = "INFO"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load settings once per process and reuse them.

    Returns:
        The process-wide Settings.

    Raises:
        pydantic.ValidationError: a required value is missing or malformed.
    """
    return Settings()  # pyright: ignore[reportCallIssue]
