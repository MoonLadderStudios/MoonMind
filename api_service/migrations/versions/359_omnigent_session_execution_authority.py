"""Bind canonical Omnigent sessions to plan/runtime authority.

Source: MoonLadderStudios/MoonMind#3701.
Revision ID: 359_omnigent_authority
Revises: 358_generic_host
Create Date: 2026-08-21
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "359_omnigent_authority"
down_revision: Union[str, None] = "358_generic_host"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "omnigent_sessions",
        sa.Column("execution_plan_ref", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "omnigent_sessions",
        sa.Column("runtime_binding_ref", sa.String(length=255), nullable=True),
    )
    op.create_index(
        "ix_omnigent_sessions_execution_plan",
        "omnigent_sessions",
        ["execution_plan_ref"],
    )
    op.create_index(
        "ix_omnigent_sessions_runtime_binding",
        "omnigent_sessions",
        ["runtime_binding_ref"],
    )
    op.create_foreign_key(
        "fk_omnigent_sessions_execution_plan",
        "omnigent_sessions",
        "omnigent_execution_plans",
        ["execution_plan_ref"],
        ["plan_ref"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_omnigent_sessions_runtime_binding",
        "omnigent_sessions",
        "omnigent_runtime_bindings",
        ["runtime_binding_ref"],
        ["runtime_binding_ref"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_omnigent_sessions_runtime_binding",
        "omnigent_sessions",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_omnigent_sessions_execution_plan",
        "omnigent_sessions",
        type_="foreignkey",
    )
    op.drop_index(
        "ix_omnigent_sessions_runtime_binding", table_name="omnigent_sessions"
    )
    op.drop_index("ix_omnigent_sessions_execution_plan", table_name="omnigent_sessions")
    op.drop_column("omnigent_sessions", "runtime_binding_ref")
    op.drop_column("omnigent_sessions", "execution_plan_ref")


_ = (revision, down_revision, branch_labels, depends_on)
