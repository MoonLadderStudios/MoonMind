"""Extend OAuth-home exclusive capacity to Claude.

Revision ID: 348_claude_oauth_capacity
Revises: 347_control_stop_continuations
Create Date: 2026-07-28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "348_claude_oauth_capacity"
down_revision = "347_control_stop_continuations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    profiles = sa.table(
        "managed_agent_provider_profiles",
        sa.column("runtime_id", sa.String()),
        sa.column("credential_source", sa.String()),
        sa.column("runtime_materialization_mode", sa.String()),
        sa.column("max_parallel_runs", sa.Integer()),
    )
    op.get_bind().execute(
        profiles.update()
        .where(
            sa.and_(
                profiles.c.runtime_id == "claude_code",
                profiles.c.credential_source == "oauth_volume",
                profiles.c.runtime_materialization_mode == "oauth_home",
                profiles.c.max_parallel_runs != 1,
            )
        )
        .values(max_parallel_runs=1)
    )
    op.drop_constraint(
        "ck_provider_profiles_codex_oauth_exclusive_capacity",
        "managed_agent_provider_profiles",
        type_="check",
    )
    op.create_check_constraint(
        "ck_provider_profiles_oauth_home_exclusive_capacity",
        "managed_agent_provider_profiles",
        """
        NOT (
            runtime_id IN ('codex_cli', 'claude_code')
            AND credential_source = 'oauth_volume'
            AND runtime_materialization_mode = 'oauth_home'
        ) OR max_parallel_runs = 1
        """.strip(),
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_provider_profiles_oauth_home_exclusive_capacity",
        "managed_agent_provider_profiles",
        type_="check",
    )
    op.create_check_constraint(
        "ck_provider_profiles_codex_oauth_exclusive_capacity",
        "managed_agent_provider_profiles",
        """
        NOT (
            runtime_id = 'codex_cli'
            AND credential_source = 'oauth_volume'
            AND runtime_materialization_mode = 'oauth_home'
        ) OR max_parallel_runs = 1
        """.strip(),
    )
