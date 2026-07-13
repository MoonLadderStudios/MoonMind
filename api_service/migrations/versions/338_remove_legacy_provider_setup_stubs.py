"""Remove untouched legacy provider setup stubs.

Revision ID: 338_remove_legacy_provider_stubs
Revises: 337_mm1207_oauth_hosts
Create Date: 2026-07-13
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "338_remove_legacy_provider_stubs"
down_revision: Union[str, None] = "337_mm1207_oauth_hosts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

LEGACY_SETUP_PROFILE_SPECS = (
    (
        "claude_anthropic_default",
        "claude_code",
        "anthropic",
        "Claude Code (setup required)",
    ),
    (
        "claude_anthropic",
        "claude_code",
        "anthropic",
        "Claude Code (setup required)",
    ),
    (
        "codex_openai_default",
        "codex_cli",
        "openai",
        "Codex CLI (setup required)",
    ),
    ("codex_default", "codex_cli", "openai", "Codex CLI (setup required)"),
    (
        "gemini_google_default",
        "gemini_cli",
        "google",
        "Gemini CLI (setup required)",
    ),
    ("gemini_default", "gemini_cli", "google", "Gemini CLI (setup required)"),
)


def upgrade() -> None:
    for profile_id, runtime_id, provider_id, account_label in LEGACY_SETUP_PROFILE_SPECS:
        op.execute(
            sa.text(
                """
                DELETE FROM managed_agent_provider_profiles
                WHERE profile_id = :profile_id
                  AND runtime_id = :runtime_id
                  AND provider_id = :provider_id
                  AND provider_label = :provider_label
                  AND account_label = :account_label
                  AND default_model IS NULL
                  AND default_effort IS NULL
                  AND (model_overrides IS NULL OR CAST(model_overrides AS TEXT) = 'null')
                  AND enabled = false
                  AND is_default = false
                  AND (tags IS NULL OR CAST(tags AS TEXT) = 'null')
                  AND priority = 100
                  AND credential_source = 'none'
                  AND runtime_materialization_mode = 'api_key_env'
                  AND auth_state = 'not_configured'
                  AND disabled_reason = 'missing_credentials'
                  AND (secret_refs IS NULL OR CAST(secret_refs AS TEXT) IN ('{}', 'null'))
                  AND (clear_env_keys IS NULL OR CAST(clear_env_keys AS TEXT) = 'null')
                  AND (env_template IS NULL OR CAST(env_template AS TEXT) = 'null')
                  AND (file_templates IS NULL OR CAST(file_templates AS TEXT) = 'null')
                  AND (home_path_overrides IS NULL OR CAST(home_path_overrides AS TEXT) = 'null')
                  AND (command_behavior IS NULL OR CAST(command_behavior AS TEXT) = 'null')
                  AND max_parallel_runs = 1
                  AND cooldown_after_429_seconds = 900
                  AND rate_limit_policy = 'backoff'
                  AND max_lease_duration_seconds = 7200
                  AND volume_ref IS NULL
                  AND volume_mount_path IS NULL
                  AND last_auth_method IS NULL
                """
            ).bindparams(
                profile_id=profile_id,
                runtime_id=runtime_id,
                provider_id=provider_id,
                provider_label={
                    "anthropic": "Anthropic",
                    "openai": "OpenAI",
                    "google": "Google",
                }[provider_id],
                account_label=account_label,
            )
        )


def downgrade() -> None:
    # The removed rows were generated, unconfigured stubs with no user data and
    # cannot be meaningfully reconstructed.
    pass
