"""Add provider profile model/effort tiers (MM-1173, source MM-1168).

Revision ID: 335_mm1173_model_tiers
Revises: 334_mm1152_bridge_sessions
Create Date: 2026-07-10
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "335_mm1173_model_tiers"
down_revision: Union[str, None] = "334_mm1152_bridge_sessions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "managed_agent_provider_profiles",
        sa.Column("model_tiers", sa.JSON(), nullable=True),
    )
    op.add_column(
        "managed_agent_provider_profiles",
        sa.Column(
            "default_model_tier",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
    )


def downgrade() -> None:
    op.drop_column("managed_agent_provider_profiles", "default_model_tier")
    op.drop_column("managed_agent_provider_profiles", "model_tiers")
