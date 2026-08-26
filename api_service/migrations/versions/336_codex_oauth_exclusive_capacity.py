"""Enforce exclusive capacity for Codex OAuth provider profiles.

Revision ID: 336_codex_oauth_capacity
Revises: 335_mm1172_provider_tiers
Create Date: 2026-07-12
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "336_codex_oauth_capacity"
down_revision: Union[str, None] = "335_mm1172_provider_tiers"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Required Alembic revision metadata and entrypoints.
__all__ = [
    "revision",
    "down_revision",
    "branch_labels",
    "depends_on",
    "upgrade",
    "downgrade",
]

CODEX_OAUTH_EXCLUSIVE_CAPACITY_CHECK = """
NOT (
    runtime_id = 'codex_cli'
    AND credential_source = 'oauth_volume'
    AND runtime_materialization_mode = 'oauth_home'
) OR max_parallel_runs = 1
""".strip()


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
                profiles.c.runtime_id == "codex_cli",
                profiles.c.credential_source == "oauth_volume",
                profiles.c.runtime_materialization_mode == "oauth_home",
                profiles.c.max_parallel_runs != 1,
            )
        )
        .values(max_parallel_runs=1)
    )
    op.create_check_constraint(
        "ck_provider_profiles_codex_oauth_exclusive_capacity",
        "managed_agent_provider_profiles",
        CODEX_OAUTH_EXCLUSIVE_CAPACITY_CHECK,
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_provider_profiles_codex_oauth_exclusive_capacity",
        "managed_agent_provider_profiles",
        type_="check",
    )
