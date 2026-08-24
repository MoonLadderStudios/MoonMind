"""Finish durable authority records for the generic Omnigent host plane.

Revision ID: 359_generic_host
Revises: 358_generic_host
Create Date: 2026-08-21
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "359_generic_host"
down_revision: Union[str, None] = "358_generic_host"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _json() -> sa.JSON:
    return sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "omnigent_harness_catalog_snapshots",
        sa.Column("catalog_ref", sa.String(length=255), primary_key=True),
        sa.Column("endpoint_ref", sa.String(length=128), nullable=False),
        sa.Column("omnigent_version", sa.String(length=64), nullable=False),
        sa.Column("omnigent_build_digest", sa.String(length=71), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_digest", sa.String(length=71), nullable=False),
        sa.Column("snapshot_json", _json(), nullable=False),
        sa.Column("diagnostics_json", _json(), nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "endpoint_ref", "source_digest", name="uq_omnigent_catalog_endpoint_source"
        ),
    )
    op.create_index(
        "ix_omnigent_catalog_endpoint_observed",
        "omnigent_harness_catalog_snapshots",
        ["endpoint_ref", "observed_at"],
    )

    op.create_table(
        "omnigent_harness_trust_records",
        sa.Column("implementation_ref", sa.String(length=255), primary_key=True),
        sa.Column("harness_id", sa.String(length=128), nullable=False),
        sa.Column(
            "catalog_ref",
            sa.String(length=255),
            sa.ForeignKey(
                "omnigent_harness_catalog_snapshots.catalog_ref", ondelete="RESTRICT"
            ),
            nullable=False,
        ),
        sa.Column("trust_state", sa.String(length=32), nullable=False),
        sa.Column("reason_code", sa.String(length=128), nullable=True),
        sa.Column("plugin_load_error_json", _json(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_omnigent_trust_harness", "omnigent_harness_trust_records", ["harness_id"]
    )

    op.create_table(
        "omnigent_execution_plan_usages",
        sa.Column("usage_id", sa.String(length=255), primary_key=True),
        sa.Column("workflow_id", sa.String(length=255), nullable=False),
        sa.Column("step_execution_id", sa.String(length=255), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column(
            "plan_ref",
            sa.String(length=255),
            sa.ForeignKey("omnigent_execution_plans.plan_ref", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("request_digest", sa.String(length=71), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_omnigent_plan_usage_idempotency"
        ),
        sa.UniqueConstraint(
            "workflow_id",
            "step_execution_id",
            "idempotency_key",
            name="uq_omnigent_plan_usage_execution",
        ),
    )
    op.create_index(
        "ix_omnigent_plan_usage_plan", "omnigent_execution_plan_usages", ["plan_ref"]
    )

    with op.batch_alter_table("omnigent_runtime_bindings") as batch:
        batch.add_column(sa.Column("binding_id", sa.String(length=255), nullable=True))
        batch.add_column(
            sa.Column("latest_snapshot_ref", sa.String(length=255), nullable=True)
        )
        batch.add_column(
            sa.Column("failure_code", sa.String(length=128), nullable=True)
        )
        batch.add_column(sa.Column("terminal_result_json", _json(), nullable=True))
        batch.add_column(
            sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True)
        )
    op.execute(
        "UPDATE omnigent_runtime_bindings "
        "SET binding_id = runtime_binding_ref, "
        "latest_snapshot_ref = runtime_binding_ref, "
        "state = 'cleaned', "
        "failure_code = 'OMNIGENT_GENERIC_REALIZER_NOT_READY', "
        "heartbeat_at = CURRENT_TIMESTAMP"
    )
    with op.batch_alter_table("omnigent_runtime_bindings") as batch:
        batch.alter_column(
            "binding_id", existing_type=sa.String(length=255), nullable=False
        )
        batch.alter_column(
            "latest_snapshot_ref", existing_type=sa.String(length=255), nullable=False
        )
        batch.create_unique_constraint("uq_omnigent_runtime_binding_id", ["binding_id"])
        batch.create_index(
            "ix_omnigent_runtime_binding_state_heartbeat", ["state", "heartbeat_at"]
        )

    with op.batch_alter_table("omnigent_host_leases_v2") as batch:
        batch.add_column(
            sa.Column("runtime_binding_id", sa.String(length=255), nullable=True)
        )
        batch.add_column(sa.Column("cleanup_handle_json", _json(), nullable=True))
        batch.add_column(
            sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch.add_column(
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            )
        )
        batch.create_index(
            "ix_omnigent_host_leases_v2_runtime_binding", ["runtime_binding_id"]
        )
        batch.create_index(
            "ix_omnigent_host_leases_v2_status_heartbeat", ["status", "heartbeat_at"]
        )

    with op.batch_alter_table("omnigent_credential_runtimes") as batch:
        batch.add_column(
            sa.Column("attachments_json", _json(), nullable=False, server_default="[]")
        )
        batch.add_column(
            sa.Column(
                "cleanup_state",
                sa.String(length=32),
                nullable=False,
                server_default="active",
            )
        )
        batch.add_column(sa.Column("cleanup_evidence_json", _json(), nullable=True))

    with op.batch_alter_table("managed_agent_provider_profiles") as batch:
        batch.add_column(
            sa.Column("capacity_scope_ref", sa.String(length=255), nullable=True)
        )
        batch.add_column(
            sa.Column("model_catalog_evidence_json", _json(), nullable=True)
        )
        batch.add_column(
            sa.Column(
                "runtime_validation_image_ref", sa.String(length=255), nullable=True
            )
        )
        batch.add_column(
            sa.Column(
                "runtime_validation_version", sa.String(length=128), nullable=True
            )
        )
    op.execute(
        "UPDATE managed_agent_provider_profiles "
        "SET capacity_scope_ref = 'provider-profile:' || profile_id"
    )
    with op.batch_alter_table("managed_agent_provider_profiles") as batch:
        batch.alter_column(
            "capacity_scope_ref", existing_type=sa.String(length=255), nullable=False
        )
        batch.create_unique_constraint(
            "uq_provider_profile_capacity_scope", ["capacity_scope_ref"]
        )


def downgrade() -> None:
    with op.batch_alter_table("managed_agent_provider_profiles") as batch:
        batch.drop_constraint("uq_provider_profile_capacity_scope", type_="unique")
        batch.drop_column("runtime_validation_version")
        batch.drop_column("runtime_validation_image_ref")
        batch.drop_column("model_catalog_evidence_json")
        batch.drop_column("capacity_scope_ref")
    with op.batch_alter_table("omnigent_runtime_bindings") as batch:
        batch.drop_index("ix_omnigent_runtime_binding_state_heartbeat")
        batch.drop_constraint("uq_omnigent_runtime_binding_id", type_="unique")
        batch.drop_column("heartbeat_at")
        batch.drop_column("terminal_result_json")
        batch.drop_column("failure_code")
        batch.drop_column("latest_snapshot_ref")
        batch.drop_column("binding_id")
    with op.batch_alter_table("omnigent_credential_runtimes") as batch:
        batch.drop_column("cleanup_evidence_json")
        batch.drop_column("cleanup_state")
        batch.drop_column("attachments_json")
    with op.batch_alter_table("omnigent_host_leases_v2") as batch:
        batch.drop_index("ix_omnigent_host_leases_v2_status_heartbeat")
        batch.drop_index("ix_omnigent_host_leases_v2_runtime_binding")
        batch.drop_column("updated_at")
        batch.drop_column("heartbeat_at")
        batch.drop_column("cleanup_handle_json")
        batch.drop_column("runtime_binding_id")
    op.drop_index(
        "ix_omnigent_plan_usage_plan", table_name="omnigent_execution_plan_usages"
    )
    op.drop_table("omnigent_execution_plan_usages")
    op.drop_index(
        "ix_omnigent_trust_harness", table_name="omnigent_harness_trust_records"
    )
    op.drop_table("omnigent_harness_trust_records")
    op.drop_index(
        "ix_omnigent_catalog_endpoint_observed",
        table_name="omnigent_harness_catalog_snapshots",
    )
    op.drop_table("omnigent_harness_catalog_snapshots")


_ = (revision, down_revision, branch_labels, depends_on)
