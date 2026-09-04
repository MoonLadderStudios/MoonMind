"""Version Provider Profile leases for incremental durable operations.

Revision ID: 369_provider_lease_incremental_contract
Revises: 368_provider_capacity_scopes
Create Date: 2026-09-04

Extends provider_profile_slot_leases with a versioned incremental contract
(state, fencing, scope, credential generation, compatibility, plan ref,
owner kind, heartbeat/release times, bounded metadata) plus indexes for
lease/owner/profile/scope lookups. Existing rows remain readable; new
non-null lease_ids are unique (nulls allowed for pre-contract rows).
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "369_provider_lease_incr_contract"
down_revision: Union[str, None] = "368_provider_capacity_scopes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("provider_profile_slot_leases") as batch:
        batch.add_column(sa.Column("owner_kind", sa.String(length=32), nullable=True))
        batch.add_column(
            sa.Column("compatibility_class", sa.String(length=128), nullable=True)
        )
        batch.add_column(
            sa.Column("execution_plan_ref", sa.String(length=255), nullable=True)
        )
        batch.add_column(
            sa.Column("capacity_scope_ref", sa.String(length=255), nullable=True)
        )
        batch.add_column(sa.Column("scope_generation", sa.Integer(), nullable=True))
        batch.add_column(
            sa.Column("credential_generation", sa.Integer(), nullable=True)
        )
        batch.add_column(sa.Column("lease_state", sa.String(length=32), nullable=True))
        batch.add_column(
            sa.Column("fencing_generation", sa.Integer(), nullable=True)
        )
        batch.add_column(sa.Column("safe_metadata_json", sa.JSON(), nullable=True))
        batch.add_column(
            sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch.add_column(
            sa.Column("released_at", sa.DateTime(timezone=True), nullable=True)
        )
    op.execute(
        "UPDATE provider_profile_slot_leases SET owner_kind = "
        "CASE WHEN owner_is_workflow THEN 'workflow' ELSE 'activity' END "
        "WHERE owner_kind IS NULL"
    )
    op.execute(
        "UPDATE provider_profile_slot_leases SET compatibility_class = purpose "
        "WHERE compatibility_class IS NULL"
    )
    op.execute(
        "UPDATE provider_profile_slot_leases SET scope_generation = 1 "
        "WHERE scope_generation IS NULL"
    )
    op.execute(
        "UPDATE provider_profile_slot_leases SET fencing_generation = 1 "
        "WHERE fencing_generation IS NULL"
    )
    op.execute(
        "UPDATE provider_profile_slot_leases SET lease_state = 'held' "
        "WHERE lease_state IS NULL"
    )
    op.execute(
        "UPDATE provider_profile_slot_leases SET lease_id = workflow_id "
        "WHERE lease_id IS NULL"
    )
    with op.batch_alter_table("provider_profile_slot_leases") as batch:
        batch.alter_column("owner_kind", existing_type=sa.String(32), nullable=False)
        batch.alter_column(
            "compatibility_class", existing_type=sa.String(128), nullable=False
        )
        batch.alter_column("scope_generation", existing_type=sa.Integer(), nullable=False)
        batch.alter_column("lease_state", existing_type=sa.String(32), nullable=False)
        batch.alter_column(
            "fencing_generation", existing_type=sa.Integer(), nullable=False
        )
        batch.create_index("ix_provider_slot_leases_lease", ["lease_id"])
        batch.create_index("ix_provider_slot_leases_owner", ["owner_id"])
        batch.create_index("ix_provider_slot_leases_profile", ["profile_id"])
        batch.create_index("ix_provider_slot_leases_scope", ["capacity_scope_ref"])
        batch.create_unique_constraint(
            "uq_provider_slot_lease_lease_id", ["lease_id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("provider_profile_slot_leases") as batch:
        batch.drop_constraint("uq_provider_slot_lease_lease_id", type_="unique")
        batch.drop_index("ix_provider_slot_leases_scope")
        batch.drop_index("ix_provider_slot_leases_profile")
        batch.drop_index("ix_provider_slot_leases_owner")
        batch.drop_index("ix_provider_slot_leases_lease")
        batch.drop_column("released_at")
        batch.drop_column("heartbeat_at")
        batch.drop_column("safe_metadata_json")
        batch.drop_column("fencing_generation")
        batch.drop_column("lease_state")
        batch.drop_column("credential_generation")
        batch.drop_column("scope_generation")
        batch.drop_column("capacity_scope_ref")
        batch.drop_column("execution_plan_ref")
        batch.drop_column("compatibility_class")
        batch.drop_column("owner_kind")


_ = (revision, down_revision, branch_labels, depends_on)
