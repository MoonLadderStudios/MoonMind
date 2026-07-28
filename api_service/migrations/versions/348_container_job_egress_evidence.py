"""Persist terminal restricted-egress evidence on Container Jobs.

Revision ID: 348_container_job_egress
Revises: 347_control_stop_continuations
"""

from alembic import op
import sqlalchemy as sa

revision = "348_container_job_egress"
down_revision = "347_control_stop_continuations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "container_jobs",
        sa.Column("egress_evidence_ref", sa.String(1024), nullable=True),
    )
    op.add_column(
        "omnigent_oauth_host_leases",
        sa.Column("egress_attestation_json", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("omnigent_oauth_host_leases", "egress_attestation_json")
    op.drop_column("container_jobs", "egress_evidence_ref")
