"""Introduce authoritative provider capacity scopes with adaptive backpressure.

Revision ID: 368_provider_capacity_scopes
Revises: 367_remove_workflow_proposals
Create Date: 2026-09-04

Each existing Provider Profile is migrated to an automatically created
one-profile scope without changing effective behavior. Several profiles may
intentionally reference the same scope afterwards; admission must satisfy
both profile and scope effective limits.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "368_provider_capacity_scopes"
down_revision: Union[str, None] = "367_remove_workflow_proposals"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "provider_capacity_scopes",
        sa.Column("scope_ref", sa.String(length=255), nullable=False),
        sa.Column("runtime_id", sa.String(length=64), nullable=False),
        sa.Column(
            "provider_class",
            sa.String(length=64),
            nullable=False,
            server_default="unknown",
        ),
        sa.Column("generation", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("configured_limit", sa.Integer(), nullable=False),
        sa.Column("effective_limit", sa.Integer(), nullable=False),
        sa.Column("cooldown_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "backpressure_state",
            sa.String(length=32),
            nullable=False,
            server_default="healthy",
        ),
        sa.Column(
            "recovery_policy_ref",
            sa.String(length=128),
            nullable=False,
            server_default="additive-increase-multiplicative-decrease@1",
        ),
        sa.Column("healthy_since", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_decrease_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_increase_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("configured_limit >= 1", name="ck_capacity_scopes_configured_positive"),
        sa.CheckConstraint("effective_limit >= 1", name="ck_capacity_scopes_effective_positive"),
        sa.CheckConstraint("generation >= 1", name="ck_capacity_scopes_generation_positive"),
        sa.CheckConstraint(
            "backpressure_state IN ('healthy', 'reduced', 'cooldown', 'probing', 'disabled')",
            name="ck_capacity_scopes_backpressure_state",
        ),
        sa.PrimaryKeyConstraint("scope_ref"),
    )
    op.create_index(
        "ix_capacity_scopes_runtime", "provider_capacity_scopes", ["runtime_id"]
    )
    # One equivalent default scope per existing profile; effective behavior unchanged.
    op.execute(
        "INSERT INTO provider_capacity_scopes "
        "(scope_ref, runtime_id, provider_class, generation, configured_limit, "
        "effective_limit, backpressure_state, recovery_policy_ref) "
        "SELECT capacity_scope_ref, runtime_id, provider_id, 1, max_parallel_runs, "
        "max_parallel_runs, 'healthy', 'additive-increase-multiplicative-decrease@1' "
        "FROM managed_agent_provider_profiles"
    )
    with op.batch_alter_table("managed_agent_provider_profiles") as batch:
        batch.drop_constraint("uq_provider_profile_capacity_scope", type_="unique")
        batch.create_index("ix_provider_profile_capacity_scope", ["capacity_scope_ref"])


def downgrade() -> None:
    with op.batch_alter_table("managed_agent_provider_profiles") as batch:
        batch.drop_index("ix_provider_profile_capacity_scope")
        # Fail-closed when scopes are shared: restoring 1:1 uniqueness with
        # shared refs would silently discard the shared allowance.
        batch.create_unique_constraint(
            "uq_provider_profile_capacity_scope", ["capacity_scope_ref"]
        )
    op.drop_index("ix_capacity_scopes_runtime", table_name="provider_capacity_scopes")
    op.drop_table("provider_capacity_scopes")


_ = (revision, down_revision, branch_labels, depends_on)
