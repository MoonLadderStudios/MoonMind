"""Jules API integration settings."""

from __future__ import annotations

from typing import Optional

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from moonmind.config.paths import ENV_FILE


class JulesSettings(BaseSettings):
    """Jules API settings."""

    jules_api_url: Optional[str] = Field(
        None,
        validation_alias=AliasChoices("jules_api_url", "JULES_API_URL"),
    )
    jules_api_key: Optional[str] = Field(
        None,
        validation_alias=AliasChoices("jules_api_key", "JULES_API_KEY"),
    )
    jules_enabled: bool = Field(False, env="JULES_ENABLED")
    jules_timeout_seconds: float = Field(30.0, env="JULES_TIMEOUT_SECONDS")
    jules_retry_attempts: int = Field(3, env="JULES_RETRY_ATTEMPTS")
    jules_retry_delay_seconds: float = Field(1.0, env="JULES_RETRY_DELAY_SECONDS")

    model_config = SettingsConfigDict(
        env_prefix="",
        env_file=str(ENV_FILE),
        extra="ignore",
    )
