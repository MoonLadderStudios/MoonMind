"""Jules API integration settings."""

from __future__ import annotations

from typing import Optional

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from moonmind.config.paths import ENV_FILE


class JulesSettings(BaseSettings):
    """Jules API settings."""

    jules_api_url: Optional[str] = Field(
        "https://jules.googleapis.com/v1alpha",
        validation_alias=AliasChoices("jules_api_url", "JULES_API_URL"),
    )
    jules_api_key: Optional[str] = Field(
        None,
        validation_alias=AliasChoices("jules_api_key", "JULES_API_KEY"),
    )
    jules_enabled: Optional[bool] = Field(None, validation_alias=AliasChoices("jules_enabled", "JULES_ENABLED"))
    jules_timeout_seconds: float = Field(30.0, env="JULES_TIMEOUT_SECONDS")
    jules_retry_attempts: int = Field(3, env="JULES_RETRY_ATTEMPTS")
    jules_retry_delay_seconds: float = Field(1.0, env="JULES_RETRY_DELAY_SECONDS")
    jules_callback_token: Optional[str] = Field(
        None,
        validation_alias=AliasChoices(
            "jules_callback_token",
            "JULES_CALLBACK_TOKEN",
        ),
    )
    jules_callback_max_payload_bytes: int = Field(
        64 * 1024,
        validation_alias=AliasChoices(
            "jules_callback_max_payload_bytes",
            "JULES_CALLBACK_MAX_PAYLOAD_BYTES",
        ),
        ge=1,
    )
    jules_callback_rate_limit_per_window: int = Field(
        30,
        validation_alias=AliasChoices(
            "jules_callback_rate_limit_per_window",
            "JULES_CALLBACK_RATE_LIMIT_PER_WINDOW",
        ),
        ge=1,
    )
    jules_callback_rate_limit_window_seconds: int = Field(
        60,
        validation_alias=AliasChoices(
            "jules_callback_rate_limit_window_seconds",
            "JULES_CALLBACK_RATE_LIMIT_WINDOW_SECONDS",
        ),
        ge=1,
    )
    jules_callback_artifact_capture_enabled: bool = Field(
        False,
        validation_alias=AliasChoices(
            "jules_callback_artifact_capture_enabled",
            "JULES_CALLBACK_ARTIFACT_CAPTURE_ENABLED",
        ),
    )
    jules_poll_initial_seconds: int = Field(
        5,
        validation_alias=AliasChoices(
            "jules_poll_initial_seconds",
            "JULES_POLL_INITIAL_SECONDS",
        ),
        ge=1,
    )
    jules_poll_max_seconds: int = Field(
        300,
        validation_alias=AliasChoices(
            "jules_poll_max_seconds",
            "JULES_POLL_MAX_SECONDS",
        ),
        ge=1,
    )
    jules_monitoring_task_queue: str = Field(
        "mm.activity.integrations",
        validation_alias=AliasChoices(
            "jules_monitoring_task_queue",
            "JULES_MONITORING_TASK_QUEUE",
        ),
    )

    model_config = SettingsConfigDict(
        env_prefix="",
        env_file=str(ENV_FILE),
        extra="ignore",
    )
