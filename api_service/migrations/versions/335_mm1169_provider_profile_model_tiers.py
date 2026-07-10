"""add provider profile model tiers

Revision ID: 335_mm1169_profile_tiers
Revises: 334_mm1152_bridge_sessions
Create Date: 2026-07-10 00:00:00.000000

Implementation issue: MM-1169
Source issue traceability: MM-1168

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.sql import column, table

revision: str = "335_mm1169_profile_tiers"
down_revision: Union[str, None] = "334_mm1152_bridge_sessions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "upgrade", "downgrade"]

profiles_table = table(
    "managed_agent_provider_profiles",
    column("profile_id", sa.String),
    column("default_model", sa.String),
    column("default_effort", sa.String),
    column("model_tiers", sa.JSON),
    column("default_model_tier", sa.Integer),
)


def upgrade() -> None:
    op.add_column(
        "managed_agent_provider_profiles",
        sa.Column(
            "model_tiers",
            sa.JSON(),
            nullable=False,
            server_default=sa.text(
                """'[{"label":"Runtime default","model":null,"effort":null,"parameters":{},"annotations":{}}]'"""
            ),
        ),
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

    bind = op.get_bind()
    rows = list(
        bind.execute(
            sa.select(
                profiles_table.c.profile_id,
                profiles_table.c.default_model,
                profiles_table.c.default_effort,
            )
        )
    )
    for row in rows:
        if row.default_model is not None or row.default_effort is not None:
            tiers = [
                {
                    "label": "Legacy default",
                    "model": row.default_model,
                    "effort": row.default_effort,
                    "parameters": {},
                    "annotations": {},
                }
            ]
        else:
            tiers = [
                {
                    "label": "Runtime default",
                    "model": None,
                    "effort": None,
                    "parameters": {},
                    "annotations": {},
                }
            ]
        bind.execute(
            profiles_table.update()
            .where(profiles_table.c.profile_id == row.profile_id)
            .values(model_tiers=tiers, default_model_tier=1)
        )


def downgrade() -> None:
    op.drop_column("managed_agent_provider_profiles", "default_model_tier")
    op.drop_column("managed_agent_provider_profiles", "model_tiers")
