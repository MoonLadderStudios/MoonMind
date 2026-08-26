"""Persist authoritative remediation workspace heads.

Revision ID: 346_remediation_workspace_head
Revises: 345_omnigent_launch
"""

from alembic import op
import sqlalchemy as sa

revision = "346_remediation_workspace_head"
down_revision = "345_omnigent_launch"
branch_labels = None
depends_on = None


def upgrade() -> None:
    table = "workflow_checkpoint_branches"
    op.add_column(table, sa.Column("current_head_checkpoint_digest", sa.String(128), nullable=True))
    op.add_column(table, sa.Column("current_head_version", sa.Integer(), nullable=True))
    op.add_column(table, sa.Column("current_head_attempt_ordinal", sa.Integer(), nullable=True))
    op.add_column(table, sa.Column("remediation_loop_id", sa.String(255), nullable=True))
    op.add_column(table, sa.Column("remediation_head_status", sa.String(64), nullable=True))
    op.add_column(table, sa.Column("latest_verification_ref", sa.String(1024), nullable=True))
    op.add_column(table, sa.Column("latest_verification_verdict", sa.String(64), nullable=True))


def downgrade() -> None:
    table = "workflow_checkpoint_branches"
    for column in (
        "latest_verification_verdict", "latest_verification_ref",
        "remediation_head_status", "remediation_loop_id",
        "current_head_attempt_ordinal", "current_head_version",
        "current_head_checkpoint_digest",
    ):
        op.drop_column(table, column)
