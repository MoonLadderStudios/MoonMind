"""add provider profile activation state

Revision ID: 316_provider_profile_activation_state
Revises: 315_workflow_type_enum_cutover
Create Date: 2026-06-23 00:00:00.000000

"""

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "316_provider_profile_activation_state"
down_revision: Union[str, None] = "315_workflow_type_enum_cutover"

__all__ = ["revision", "down_revision", "upgrade", "downgrade"]


AUTH_STATES = (
    "not_configured",
    "oauth_pending",
    "api_key_pending",
    "connected",
    "validation_failed",
    "disconnected",
)
DISABLED_REASONS = (
    "missing_credentials",
    "auth_invalid",
    "user_disabled",
    "policy_disabled",
    "disconnected",
)
AUTH_METHODS = ("oauth_volume", "secret_ref", "manual")
FIRST_PARTY_SETUP_STUBS = (
    {
        "profile_id": "claude_anthropic_default",
        "runtime_id": "claude_code",
        "provider_id": "anthropic",
        "provider_label": "Anthropic",
        "account_label": "Claude Code (setup required)",
    },
    {
        "profile_id": "claude_anthropic",
        "runtime_id": "claude_code",
        "provider_id": "anthropic",
        "provider_label": "Anthropic",
        "account_label": "Claude Code (setup required)",
    },
    {
        "profile_id": "codex_openai_default",
        "runtime_id": "codex_cli",
        "provider_id": "openai",
        "provider_label": "OpenAI",
        "account_label": "Codex CLI (setup required)",
    },
    {
        "profile_id": "codex_default",
        "runtime_id": "codex_cli",
        "provider_id": "openai",
        "provider_label": "OpenAI",
        "account_label": "Codex CLI (setup required)",
    },
    {
        "profile_id": "gemini_google_default",
        "runtime_id": "gemini_cli",
        "provider_id": "google",
        "provider_label": "Google",
        "account_label": "Gemini CLI (setup required)",
    },
    {
        "profile_id": "gemini_default",
        "runtime_id": "gemini_cli",
        "provider_id": "google",
        "provider_label": "Google",
        "account_label": "Gemini CLI (setup required)",
    },
)


def activation_backfill_for_row(row: dict[str, object]) -> dict[str, object]:
    """Classify a pre-activation provider profile for migration backfill."""

    enabled = bool(row.get("enabled"))
    disabled_reason = str(row.get("disabled_reason") or "").strip()
    credential_source = str(row.get("credential_source") or "").strip()
    volume_ref = str(row.get("volume_ref") or "").strip()
    volume_mount_path = str(row.get("volume_mount_path") or "").strip()
    secret_refs = row.get("secret_refs")
    has_secret_refs = bool(secret_refs) and secret_refs != {}

    if disabled_reason == "policy_disabled":
        return {
            "enabled": False,
            "auth_state": "not_configured",
            "disabled_reason": "policy_disabled",
            "last_auth_method": None,
            "stamp_validated": False,
        }
    if not enabled:
        return {
            "enabled": False,
            "auth_state": "not_configured",
            "disabled_reason": "user_disabled",
            "last_auth_method": None,
            "stamp_validated": False,
        }
    if credential_source == "oauth_volume" and volume_ref and volume_mount_path:
        return {
            "enabled": True,
            "auth_state": "connected",
            "disabled_reason": None,
            "last_auth_method": "oauth_volume",
            "stamp_validated": True,
        }
    if credential_source == "secret_ref" and has_secret_refs:
        return {
            "enabled": True,
            "auth_state": "connected",
            "disabled_reason": None,
            "last_auth_method": "secret_ref",
            "stamp_validated": True,
        }
    if str(row.get("validation_status") or "").strip() in {
        "invalid",
        "failed",
        "validation_failed",
    }:
        return {
            "enabled": False,
            "auth_state": "validation_failed",
            "disabled_reason": "auth_invalid",
            "last_auth_method": None,
            "stamp_validated": False,
        }
    return {
        "enabled": False,
        "auth_state": "not_configured",
        "disabled_reason": "missing_credentials",
        "last_auth_method": None,
        "stamp_validated": False,
    }


def _insert_first_party_setup_stubs() -> None:
    for stub in FIRST_PARTY_SETUP_STUBS:
        op.execute(
            sa.text(
                """
                INSERT INTO managed_agent_provider_profiles (
                    profile_id,
                    runtime_id,
                    provider_id,
                    provider_label,
                    credential_source,
                    runtime_materialization_mode,
                    account_label,
                    enabled,
                    is_default,
                    tags,
                    priority,
                    max_parallel_runs,
                    cooldown_after_429_seconds,
                    rate_limit_policy,
                    max_lease_duration_seconds,
                    auth_state,
                    disabled_reason
                )
                SELECT
                    :profile_id,
                    :runtime_id,
                    :provider_id,
                    :provider_label,
                    'none',
                    'api_key_env',
                    :account_label,
                    false,
                    false,
                    NULL,
                    100,
                    1,
                    900,
                    'backoff',
                    7200,
                    'not_configured',
                    'missing_credentials'
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM managed_agent_provider_profiles
                    WHERE profile_id = :profile_id
                )
                """
            ).bindparams(
                profile_id=stub["profile_id"],
                runtime_id=stub["runtime_id"],
                provider_id=stub["provider_id"],
                provider_label=stub["provider_label"],
                account_label=stub["account_label"],
            )
        )


def _backfill_activation_state() -> None:
    op.execute(
        """
        UPDATE managed_agent_provider_profiles
        SET
            auth_state = 'connected',
            disabled_reason = NULL,
            first_authenticated_at = COALESCE(first_authenticated_at, CURRENT_TIMESTAMP),
            last_validated_at = CURRENT_TIMESTAMP,
            last_auth_method = 'oauth_volume'
        WHERE enabled = true
          AND credential_source = 'oauth_volume'
          AND COALESCE(volume_ref, '') <> ''
          AND COALESCE(volume_mount_path, '') <> ''
        """
    )
    op.execute(
        """
        UPDATE managed_agent_provider_profiles
        SET
            auth_state = 'connected',
            disabled_reason = NULL,
            first_authenticated_at = COALESCE(first_authenticated_at, CURRENT_TIMESTAMP),
            last_validated_at = CURRENT_TIMESTAMP,
            last_auth_method = 'secret_ref'
        WHERE enabled = true
          AND credential_source = 'secret_ref'
          AND secret_refs IS NOT NULL
          AND CAST(secret_refs AS TEXT) NOT IN ('', '{}', 'null')
        """
    )
    op.execute(
        """
        UPDATE managed_agent_provider_profiles
        SET
            enabled = false,
            auth_state = 'not_configured',
            disabled_reason = 'missing_credentials',
            last_auth_method = NULL
        WHERE enabled = true
          AND auth_state <> 'connected'
        """
    )
    op.execute(
        """
        UPDATE managed_agent_provider_profiles
        SET
            auth_state = 'not_configured',
            disabled_reason = 'user_disabled',
            last_auth_method = NULL
        WHERE enabled = false
          AND disabled_reason IS NULL
          AND profile_id NOT IN (
              'claude_anthropic_default',
              'claude_anthropic',
              'codex_openai_default',
              'codex_default',
              'gemini_google_default',
              'gemini_default'
          )
        """
    )
    op.execute(
        """
        UPDATE managed_agent_provider_profiles
        SET
            enabled = false,
            auth_state = 'not_configured',
            disabled_reason = 'missing_credentials',
            last_auth_method = NULL
        WHERE profile_id IN (
              'claude_anthropic_default',
              'claude_anthropic',
              'codex_openai_default',
              'codex_default',
              'gemini_google_default',
              'gemini_default'
          )
          AND auth_state <> 'connected'
        """
    )


def upgrade() -> None:
    auth_state_enum = sa.Enum(*AUTH_STATES, name="providerprofileauthstate")
    disabled_reason_enum = sa.Enum(
        *DISABLED_REASONS, name="providerprofiledisabledreason"
    )
    auth_method_enum = sa.Enum(*AUTH_METHODS, name="providerprofileauthmethod")
    bind = op.get_bind()
    auth_state_enum.create(bind, checkfirst=True)
    disabled_reason_enum.create(bind, checkfirst=True)
    auth_method_enum.create(bind, checkfirst=True)

    op.alter_column(
        "managed_agent_provider_profiles",
        "enabled",
        server_default=sa.text("false"),
        existing_type=sa.Boolean(),
        existing_nullable=False,
    )
    op.add_column(
        "managed_agent_provider_profiles",
        sa.Column(
            "auth_state",
            auth_state_enum,
            nullable=False,
            server_default="not_configured",
        ),
    )
    op.add_column(
        "managed_agent_provider_profiles",
        sa.Column("disabled_reason", disabled_reason_enum, nullable=True),
    )
    op.add_column(
        "managed_agent_provider_profiles",
        sa.Column("first_authenticated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "managed_agent_provider_profiles",
        sa.Column("last_validated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "managed_agent_provider_profiles",
        sa.Column("last_auth_method", auth_method_enum, nullable=True),
    )
    _insert_first_party_setup_stubs()
    _backfill_activation_state()

    op.create_check_constraint(
        "ck_provider_profiles_auth_state",
        "managed_agent_provider_profiles",
        "auth_state IN ("
        "'not_configured', 'oauth_pending', 'api_key_pending', "
        "'connected', 'validation_failed', 'disconnected'"
        ")",
    )
    op.create_check_constraint(
        "ck_provider_profiles_disabled_reason",
        "managed_agent_provider_profiles",
        "disabled_reason IS NULL OR disabled_reason IN ("
        "'missing_credentials', 'auth_invalid', 'user_disabled', "
        "'policy_disabled', 'disconnected'"
        ")",
    )
    op.create_check_constraint(
        "ck_provider_profiles_last_auth_method",
        "managed_agent_provider_profiles",
        "last_auth_method IS NULL OR last_auth_method IN ("
        "'oauth_volume', 'secret_ref', 'manual'"
        ")",
    )

    op.create_index(
        "ix_provider_profiles_runtime_provider",
        "managed_agent_provider_profiles",
        ["runtime_id", "provider_id"],
        unique=False,
    )
    op.create_index(
        "ix_provider_profiles_auth_state",
        "managed_agent_provider_profiles",
        ["auth_state"],
        unique=False,
    )
    op.create_index(
        "ix_provider_profiles_readiness",
        "managed_agent_provider_profiles",
        ["runtime_id", "provider_id", "enabled", "auth_state"],
        unique=False,
    )


def downgrade() -> None:
    raise NotImplementedError("One-way migration: MM-872 provider profile activation state")
