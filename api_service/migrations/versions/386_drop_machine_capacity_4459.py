"""Drop the retired machine-capacity reservation table.

Revision ID: 386_drop_machine_capacity_4459
Revises: 385_runtime_issuance
Create Date: 2026-09-20

Source issue: MoonLadderStudios/MoonMind#4459. The custom CPU pool and
automatic machine-resource admission were removed while the old
``machine_capacity_reservations`` ledger (created by revision
``372_machine_reservations``) was intentionally retained for
upgrade/rollback safety. No current code reads or writes this table, so
this ordinary forward migration drops only it with its table-owned
indexes and unique constraint. Historical migration 372 stays in history
and every other table is untouched.

Supported rollback behavior: ``downgrade()`` recreates the empty table
schema so a rolled-back release boots against the schema it expects, but
it does not restore historical rows. Old-code rollback that requires the
dropped reservation data must restore a pre-upgrade database backup, and
the destructive release must wait until that rollback requirement ends
(see the existing release process). No resource-accounting logic is
reintroduced here.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

__all__ = [
    "revision",
    "down_revision",
    "branch_labels",
    "depends_on",
    "upgrade",
    "downgrade",
]

revision: str = "386_drop_machine_capacity_4459"
down_revision: Union[str, None] = "385_runtime_issuance"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index(
        "ix_machine_capacity_reservations_expiry",
        table_name="machine_capacity_reservations",
    )
    op.drop_index(
        "ix_machine_capacity_reservations_state",
        table_name="machine_capacity_reservations",
    )
    op.drop_table("machine_capacity_reservations")


def downgrade() -> None:
    # Empty-schema recreate only: historical reservation rows removed by
    # upgrade() are not restored. Restore a pre-upgrade backup when the
    # rolled-back release needs that data.
    op.create_table(
        "machine_capacity_reservations",
        sa.Column("reservation_id", sa.String(length=255), primary_key=True),
        sa.Column("backend_ref", sa.String(length=255), nullable=False),
        sa.Column("workload_class", sa.String(length=32), nullable=False),
        sa.Column("owner_kind", sa.String(length=32), nullable=False),
        sa.Column("owner_ref", sa.String(length=255), nullable=False),
        sa.Column(
            "generation",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("plan_ref", sa.String(length=255), nullable=True),
        sa.Column("host_class_ref", sa.String(length=128), nullable=True),
        sa.Column("launch_policy_ref", sa.String(length=128), nullable=True),
        sa.Column(
            "cpu_millis", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "memory_mib", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "processes", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "temporary_storage_mib",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("container_ref", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "backend_ref",
            "owner_kind",
            "owner_ref",
            "generation",
            name="uq_machine_capacity_reservation_owner",
        ),
    )
    op.create_index(
        "ix_machine_capacity_reservations_state",
        "machine_capacity_reservations",
        ["backend_ref", "state"],
    )
    op.create_index(
        "ix_machine_capacity_reservations_expiry",
        "machine_capacity_reservations",
        ["expires_at"],
    )
