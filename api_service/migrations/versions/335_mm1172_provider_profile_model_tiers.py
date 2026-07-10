"""add provider profile model tiers for MM-1172.

Source traceability: MM-1172 / MM-1168.

Revision ID: 335_mm1172_provider_tiers
Revises: 334_mm1152_bridge_sessions
Create Date: 2026-07-10
"""

from __future__ import annotations

from typing import Any, Union

import sqlalchemy as sa
from alembic import op

revision: str = "335_mm1172_provider_tiers"
down_revision: Union[str, None] = "334_mm1152_bridge_sessions"

__all__ = ["revision", "down_revision", "upgrade", "downgrade"]

_RUNTIME_DEFAULT_TIER = [
    {
        "label": "Runtime default",
        "model": None,
        "effort": None,
        "parameters": {},
        "annotations": {"migratedFrom": "runtime_default"},
    }
]


def _backfilled_tiers(default_model: Any, default_effort: Any) -> list[dict[str, Any]]:
    model = str(default_model).strip() if default_model is not None else None
    effort = str(default_effort).strip() if default_effort is not None else None
    model = model or None
    effort = effort or None
    if model or effort:
        return [
            {
                "label": "Default",
                "model": model,
                "effort": effort,
                "parameters": {},
                "annotations": {"migratedFrom": "default_model_default_effort"},
            }
        ]
    return [dict(_RUNTIME_DEFAULT_TIER[0])]


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
    op.create_check_constraint(
        "ck_provider_profiles_default_model_tier_positive",
        "managed_agent_provider_profiles",
        "default_model_tier >= 1",
    )

    conn = op.get_bind()
    profiles = sa.table(
        "managed_agent_provider_profiles",
        sa.column("profile_id", sa.String()),
        sa.column("default_model", sa.String()),
        sa.column("default_effort", sa.String()),
        sa.column("model_tiers", sa.JSON()),
        sa.column("default_model_tier", sa.Integer()),
    )
    rows = conn.execute(
        sa.select(
            profiles.c.profile_id,
            profiles.c.default_model,
            profiles.c.default_effort,
        )
    ).fetchall()
    for profile_id, default_model, default_effort in rows:
        conn.execute(
            sa.update(profiles)
            .where(profiles.c.profile_id == profile_id)
            .values(
                model_tiers=_backfilled_tiers(default_model, default_effort),
                default_model_tier=1,
            )
        )


def downgrade() -> None:
    op.drop_constraint(
        "ck_provider_profiles_default_model_tier_positive",
        "managed_agent_provider_profiles",
        type_="check",
    )
    op.drop_column("managed_agent_provider_profiles", "default_model_tier")
    op.drop_column("managed_agent_provider_profiles", "model_tiers")
