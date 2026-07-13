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
    ("claude_anthropic", "claude_code", "anthropic", "Claude Code (setup required)"),
    ("codex_openai_default", "codex_cli", "openai", "Codex CLI (setup required)"),
    ("codex_default", "codex_cli", "openai", "Codex CLI (setup required)"),
    ("gemini_google_default", "gemini_cli", "google", "Gemini CLI (setup required)"),
    ("gemini_default", "gemini_cli", "google", "Gemini CLI (setup required)"),
)

__all__ = [
    "revision",
    "down_revision",
    "branch_labels",
    "depends_on",
    "upgrade",
    "downgrade",
]


def upgrade() -> None:
    bind = op.get_bind()
    statement = sa.text(
        """
        DELETE FROM managed_agent_provider_profiles
        WHERE profile_id = :profile_id
          AND runtime_id = :runtime_id
          AND provider_id = :provider_id
          AND account_label = :account_label
          AND enabled = false
          AND is_default = false
          AND credential_source = 'none'
          AND runtime_materialization_mode = 'api_key_env'
          AND auth_state = 'not_configured'
          AND disabled_reason = 'missing_credentials'
          AND (secret_refs IS NULL OR CAST(secret_refs AS TEXT) IN ('{}', 'null'))
          AND volume_ref IS NULL
          AND volume_mount_path IS NULL
          AND last_auth_method IS NULL
        """
    )
    for (
        profile_id,
        runtime_id,
        provider_id,
        account_label,
    ) in LEGACY_SETUP_PROFILE_SPECS:
        bind.execute(
            statement,
            {
                "profile_id": profile_id,
                "runtime_id": runtime_id,
                "provider_id": provider_id,
                "account_label": account_label,
            },
        )


def downgrade() -> None:
    # Untouched generated stubs contain no user data and cannot be meaningfully reconstructed.
    pass
