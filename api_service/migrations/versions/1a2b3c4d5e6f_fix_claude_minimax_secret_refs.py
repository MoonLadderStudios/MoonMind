"""Fix claude_minimax secret_refs to use direct ANTHROPIC_AUTH_TOKEN injection

Revision ID: 1a2b3c4d5e6f
Revises: 0b8e4befb8e5
Create Date: 2026-03-28 00:00:00.000000

The original claude_minimax profile used a logical alias in secret_refs:
  secret_refs = {"anthropic_api_key": "MINIMAX_API_KEY"}
  env_template = {"ANTHROPIC_AUTH_TOKEN": "${anthropic_api_key}", ...}

The template entry was filtered by the adapter's sensitive-key guard before
reaching the launcher, so ANTHROPIC_AUTH_TOKEN was never set. Fix by switching
secret_refs to name the target env var directly and removing the template entry.
"""
from typing import Sequence, Union
import json
from alembic import op
import sqlalchemy as sa


revision: str = '1a2b3c4d5e6f'
down_revision: Union[str, None, Sequence[str]] = '0b8e4befb8e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    result = conn.execute(
        sa.text(
            "SELECT secret_refs, env_template FROM managed_agent_provider_profiles "
            "WHERE profile_id = 'claude_minimax'"
        )
    )
    row = result.fetchone()
    if not row:
        return

    secret_refs = row[0] or {}
    env_template = row[1] or {}

    if isinstance(secret_refs, str):
        secret_refs = json.loads(secret_refs)
    if isinstance(env_template, str):
        env_template = json.loads(env_template)

    # Only apply if still in the broken state:
    # - legacy "anthropic_api_key" still present (original broken state), OR
    # - ANTHROPIC_AUTH_TOKEN exists but lacks the env:// prefix (migration
    #   was previously run but with the wrong value)
    anthropic_auth_token = secret_refs.get("ANTHROPIC_AUTH_TOKEN", "")
    if "anthropic_api_key" not in secret_refs and not (
        isinstance(anthropic_auth_token, str) and anthropic_auth_token.startswith("env://")
    ):
        return

    secret_refs.pop("anthropic_api_key", None)
    secret_refs["ANTHROPIC_AUTH_TOKEN"] = "env://MINIMAX_API_KEY"
    env_template.pop("ANTHROPIC_AUTH_TOKEN", None)

    conn.execute(
        sa.text(
            "UPDATE managed_agent_provider_profiles "
            "SET secret_refs = CAST(:secret_refs AS jsonb), "
            "env_template = CAST(:env_template AS jsonb) "
            "WHERE profile_id = 'claude_minimax'"
        ),
        {
            "secret_refs": json.dumps(secret_refs),
            "env_template": json.dumps(env_template),
        },
    )


def downgrade() -> None:
    conn = op.get_bind()
    result = conn.execute(
        sa.text(
            "SELECT secret_refs, env_template FROM managed_agent_provider_profiles "
            "WHERE profile_id = 'claude_minimax'"
        )
    )
    row = result.fetchone()
    if not row:
        return

    secret_refs = row[0] or {}
    env_template = row[1] or {}

    if isinstance(secret_refs, str):
        secret_refs = json.loads(secret_refs)
    if isinstance(env_template, str):
        env_template = json.loads(env_template)

    if "ANTHROPIC_AUTH_TOKEN" not in secret_refs:
        return

    secret_refs.pop("ANTHROPIC_AUTH_TOKEN", None)
    secret_refs["anthropic_api_key"] = "MINIMAX_API_KEY"
    env_template["ANTHROPIC_AUTH_TOKEN"] = "${anthropic_api_key}"

    conn.execute(
        sa.text(
            "UPDATE managed_agent_provider_profiles "
            "SET secret_refs = CAST(:secret_refs AS jsonb), "
            "env_template = CAST(:env_template AS jsonb) "
            "WHERE profile_id = 'claude_minimax'"
        ),
        {
            "secret_refs": json.dumps(secret_refs),
            "env_template": json.dumps(env_template),
        },
    )
