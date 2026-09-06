"""Durable machine resource reservations shared by every managed launch.

Revision ID: 372_machine_reservations
Revises: 371_profile_execution_config
Create Date: 2026-09-05

Source issue: MoonLadderStudios/MoonMind#3881. Host-row counting bounds how
many containers exist; it does not bound the CPU, memory, process and
temporary-storage demand those containers place on one machine. This table is
the single deployment-owned accounting ledger those demands are reserved in,
scoped by exact Docker backend identity so independent backends are never
pooled.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "372_machine_reservations"
down_revision: Union[str, None] = "371_profile_execution_config"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "machine_capacity_reservations",
        sa.Column("reservation_id", sa.String(length=255), primary_key=True),
        sa.Column("backend_ref", sa.String(length=128), nullable=False),
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


def downgrade() -> None:
    op.drop_index(
        "ix_machine_capacity_reservations_expiry",
        table_name="machine_capacity_reservations",
    )
    op.drop_index(
        "ix_machine_capacity_reservations_state",
        table_name="machine_capacity_reservations",
    )
    op.drop_table("machine_capacity_reservations")
