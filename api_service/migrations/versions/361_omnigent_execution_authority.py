"""Scope fenced runtime binding authority to one execution of a plan.

Source: MoonLadderStudios/MoonMind#3706
Revision ID: 361_omnigent_execution_authority
Revises: 360_omnigent_authority
Create Date: 2026-08-21
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "361_omnigent_execution_authority"
down_revision: Union[str, None] = "360_omnigent_authority"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "omnigent_runtime_bindings",
        sa.Column("execution_scope_ref", sa.String(length=255), nullable=True),
    )
    op.create_unique_constraint(
        "uq_omnigent_runtime_binding_plan_scope",
        "omnigent_runtime_bindings",
        ["execution_plan_ref", "execution_scope_ref"],
    )
    op.add_column(
        "omnigent_runtime_bindings",
        sa.Column("runner_ref", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "omnigent_runtime_bindings",
        sa.Column("chat_binding_ref", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("omnigent_runtime_bindings", "chat_binding_ref")
    op.drop_column("omnigent_runtime_bindings", "runner_ref")
    op.drop_constraint(
        "uq_omnigent_runtime_binding_plan_scope",
        "omnigent_runtime_bindings",
        type_="unique",
    )
    op.drop_column("omnigent_runtime_bindings", "execution_scope_ref")
