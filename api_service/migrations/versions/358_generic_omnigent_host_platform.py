"""Generic Omnigent host platform persistence.

Creates durable tables for the harness-neutral execution plane:

  * ``omnigent_execution_plans``      – immutable, secret-free, digest-addressed
  * ``omnigent_runtime_bindings``     – staged, fenced, immutable core
  * ``omnigent_host_bindings_v2``     – generic host binding (any HostClass)
  * ``omnigent_host_leases_v2``       – generic host lease with fencing
  * ``omnigent_credential_runtimes``  – secret-free materialization evidence
  * ``omnigent_credential_binding_sets`` – versioned binding sets

All tables are additive; no existing table is altered. Downgrade drops them
in reverse dependency order.

Source: docs/Omnigent/OmnigentHarnessPlatformDesign.md §§16-17,
        docs/Omnigent/OpenCodeHost.md, pr-resolver P1 3828196596.
Revision ID: 358_generic_host
Revises: 357_omnigent_fencing
Create Date: 2026-08-21
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "358_generic_host"
down_revision: Union[str, None] = "357_omnigent_fencing"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Execution plans – immutable, digest-addressed, secret-free
    op.create_table(
        "omnigent_execution_plans",
        sa.Column("plan_ref", sa.String(length=255), primary_key=True),
        sa.Column("schema_version", sa.String(length=64), nullable=False),
        sa.Column("payload_json", sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"), nullable=False),
        sa.Column("agent_profile_snapshot_ref", sa.String(length=255), nullable=True),
        sa.Column("credential_binding_set_ref", sa.String(length=255), nullable=True),
        sa.Column("harness_id", sa.String(length=64), nullable=False),
        sa.Column("harness_implementation_ref", sa.String(length=255), nullable=False),
        sa.Column("host_class_ref", sa.String(length=128), nullable=False),
        sa.Column("launch_policy_ref", sa.String(length=128), nullable=False),
        sa.Column("execution_realizer_ref", sa.String(length=64), nullable=False),
        sa.Column("support_combination_key", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by", sa.String(length=255), nullable=True),
    )
    op.create_index("ix_omnigent_execution_plans_created", "omnigent_execution_plans", ["created_at"])
    op.create_index("ix_omnigent_execution_plans_harness", "omnigent_execution_plans", ["harness_id"])

    # Runtime bindings – staged, fenced, immutable core
    op.create_table(
        "omnigent_runtime_bindings",
        sa.Column("runtime_binding_ref", sa.String(length=255), primary_key=True),
        sa.Column("execution_plan_ref", sa.String(length=255), sa.ForeignKey("omnigent_execution_plans.plan_ref", ondelete="CASCADE"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("fencing_generation", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("state", sa.String(length=32), nullable=False, server_default="credentials_acquired"),
        sa.Column("provider_leases_json", sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"), nullable=False, server_default="{}"),
        sa.Column("host_binding_ref", sa.String(length=255), nullable=True),
        sa.Column("host_lease_ref", sa.String(length=255), nullable=True),
        sa.Column("host_lease_generation", sa.Integer(), nullable=True),
        sa.Column("omnigent_host_id", sa.String(length=255), nullable=True),
        sa.Column("omnigent_session_id", sa.String(length=255), nullable=True),
        sa.Column("credential_runtime_handles_json", sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"), nullable=False, server_default="{}"),
        sa.Column("attestation_refs_json", sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"), nullable=False, server_default="{}"),
        sa.Column("cleanup_authority_refs_json", sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_omnigent_runtime_bindings_plan", "omnigent_runtime_bindings", ["execution_plan_ref"])
    op.create_index("ix_omnigent_runtime_bindings_host", "omnigent_runtime_bindings", ["omnigent_host_id"])
    op.create_index("ix_omnigent_runtime_bindings_session", "omnigent_runtime_bindings", ["omnigent_session_id"])

    # Generic host bindings V2 – any HostClass/harness, not just OAuth
    op.create_table(
        "omnigent_host_bindings_v2",
        sa.Column("binding_id", sa.String(length=255), primary_key=True),
        sa.Column("host_class_ref", sa.String(length=128), nullable=False),
        sa.Column("launch_policy_ref", sa.String(length=128), nullable=False),
        sa.Column("harness_id", sa.String(length=64), nullable=False),
        sa.Column("harness_implementation_ref", sa.String(length=255), nullable=False),
        sa.Column("execution_plan_ref", sa.String(length=255), sa.ForeignKey("omnigent_execution_plans.plan_ref", ondelete="SET NULL"), nullable=True),
        sa.Column("provider_profile_refs_json", sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("binding_id", name="uq_omnigent_host_binding_v2_id"),
    )
    op.create_index("ix_omnigent_host_bindings_v2_class", "omnigent_host_bindings_v2", ["host_class_ref"])
    op.create_index("ix_omnigent_host_bindings_v2_harness", "omnigent_host_bindings_v2", ["harness_id"])

    # Generic host leases V2
    op.create_table(
        "omnigent_host_leases_v2",
        sa.Column("lease_id", sa.String(length=255), primary_key=True),
        sa.Column("binding_id", sa.String(length=255), sa.ForeignKey("omnigent_host_bindings_v2.binding_id", ondelete="CASCADE"), nullable=False),
        sa.Column("host_class_ref", sa.String(length=128), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="allocating"),
        sa.Column("omnigent_host_id", sa.String(length=255), nullable=True),
        sa.Column("host_lease_generation", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_omnigent_host_leases_v2_binding", "omnigent_host_leases_v2", ["binding_id"])
    op.create_index("ix_omnigent_host_leases_v2_host", "omnigent_host_leases_v2", ["omnigent_host_id"])
    op.create_index("ix_omnigent_host_leases_v2_status", "omnigent_host_leases_v2", ["status"])

    # Credential runtime evidence – secret-free
    op.create_table(
        "omnigent_credential_runtimes",
        sa.Column("credential_runtime_ref", sa.String(length=255), primary_key=True),
        sa.Column("provider_profile_ref", sa.String(length=128), nullable=False),
        sa.Column("provider_lease_ref", sa.String(length=255), nullable=False),
        sa.Column("credential_generation", sa.Integer(), nullable=False),
        sa.Column("materializer_ref", sa.String(length=64), nullable=False),
        sa.Column("target_path", sa.String(length=512), nullable=False),
        sa.Column("access_mode", sa.String(length=32), nullable=False),
        sa.Column("cleanup_ref", sa.String(length=255), nullable=False),
        sa.Column("attestation_ref", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_omnigent_credential_runtimes_profile", "omnigent_credential_runtimes", ["provider_profile_ref"])
    op.create_index("ix_omnigent_credential_runtimes_materializer", "omnigent_credential_runtimes", ["materializer_ref"])

    # Credential binding sets – versioned, immutable per version
    op.create_table(
        "omnigent_credential_binding_sets",
        sa.Column("binding_set_id", sa.String(length=128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("digest", sa.String(length=80), nullable=False),
        sa.Column("canonical_json", sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"), nullable=False),
        sa.Column("ref", sa.String(length=512), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("binding_set_id", "version"),
        sa.UniqueConstraint("binding_set_id", "version", name="uq_omnigent_binding_set_version"),
        sa.UniqueConstraint("ref", name="uq_omnigent_binding_set_ref"),
    )
    op.create_index("ix_omnigent_binding_sets_id", "omnigent_credential_binding_sets", ["binding_set_id"])


def downgrade() -> None:
    op.drop_index("ix_omnigent_binding_sets_id", table_name="omnigent_credential_binding_sets")
    op.drop_table("omnigent_credential_binding_sets")

    op.drop_index("ix_omnigent_credential_runtimes_materializer", table_name="omnigent_credential_runtimes")
    op.drop_index("ix_omnigent_credential_runtimes_profile", table_name="omnigent_credential_runtimes")
    op.drop_table("omnigent_credential_runtimes")

    op.drop_index("ix_omnigent_host_leases_v2_status", table_name="omnigent_host_leases_v2")
    op.drop_index("ix_omnigent_host_leases_v2_host", table_name="omnigent_host_leases_v2")
    op.drop_index("ix_omnigent_host_leases_v2_binding", table_name="omnigent_host_leases_v2")
    op.drop_table("omnigent_host_leases_v2")

    op.drop_index("ix_omnigent_host_bindings_v2_harness", table_name="omnigent_host_bindings_v2")
    op.drop_index("ix_omnigent_host_bindings_v2_class", table_name="omnigent_host_bindings_v2")
    op.drop_table("omnigent_host_bindings_v2")

    op.drop_index("ix_omnigent_runtime_bindings_session", table_name="omnigent_runtime_bindings")
    op.drop_index("ix_omnigent_runtime_bindings_host", table_name="omnigent_runtime_bindings")
    op.drop_index("ix_omnigent_runtime_bindings_plan", table_name="omnigent_runtime_bindings")
    op.drop_table("omnigent_runtime_bindings")

    op.drop_index("ix_omnigent_execution_plans_harness", table_name="omnigent_execution_plans")
    op.drop_index("ix_omnigent_execution_plans_created", table_name="omnigent_execution_plans")
    op.drop_table("omnigent_execution_plans")


# Mark alembic globals as used for CodeQL
_ = (revision, down_revision, branch_labels, depends_on)
