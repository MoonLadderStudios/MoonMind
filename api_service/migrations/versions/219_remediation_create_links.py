"""add execution remediation links

Revision ID: c9d0e1f2a3b4
Revises: f8a9b0c1d2e3
Create Date: 2026-04-21 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c9d0e1f2a3b4"
down_revision: Union[str, None] = "f8a9b0c1d2e3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = [
    "revision",
    "down_revision",
    "branch_labels",
    "depends_on",
    "upgrade",
    "downgrade",
]

def upgrade() -> None:
    op.create_table(
        "execution_remediation_links",
        sa.Column("remediation_workflow_id", sa.String(length=255), nullable=False),
        sa.Column("remediation_run_id", sa.String(length=64), nullable=False),
        sa.Column("target_workflow_id", sa.String(length=255), nullable=False),
        sa.Column("target_run_id", sa.String(length=64), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("authority_mode", sa.String(length=32), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default="created",
            nullable=False,
        ),
        sa.Column("trigger_type", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["remediation_workflow_id"],
            ["temporal_execution_sources.workflow_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["target_workflow_id"],
            ["temporal_execution_sources.workflow_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("remediation_workflow_id"),
    )
    op.create_index(
        "ix_execution_remediation_links_target_workflow_id",
        "execution_remediation_links",
        ["target_workflow_id"],
        unique=False,
    )

def downgrade() -> None:
    op.drop_index(
        "ix_execution_remediation_links_target_workflow_id",
        table_name="execution_remediation_links",
    )
    op.drop_table("execution_remediation_links")
