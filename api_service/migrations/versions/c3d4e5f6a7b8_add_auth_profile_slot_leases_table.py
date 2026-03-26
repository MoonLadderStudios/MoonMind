"""Add auth_profile_slot_leases table for manager crash recovery

Revision ID: c3d4e5f6a7b8
Revises: b7c8d9e0f1a2
Create Date: 2026-03-26 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, None] = "b7c8d9e0f1a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "auth_profile_slot_leases",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("runtime_id", sa.String(length=64), nullable=False),
        sa.Column("workflow_id", sa.String(length=255), nullable=False),
        sa.Column("profile_id", sa.String(length=128), nullable=False),
        sa.Column(
            "granted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_slot_leases_runtime", "auth_profile_slot_leases", ["runtime_id"])
    op.create_index("ix_slot_leases_workflow", "auth_profile_slot_leases", ["workflow_id"])
    op.create_unique_constraint(
        "uq_slot_lease_runtime_workflow",
        "auth_profile_slot_leases",
        ["runtime_id", "workflow_id"],
    )


def downgrade() -> None:
    op.drop_table("auth_profile_slot_leases")
