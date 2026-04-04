"""add_execution_dependencies

Revision ID: d5c6f7a8b9c0
Revises: b47e8ad48d4c
Create Date: 2026-04-04 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d5c6f7a8b9c0"
down_revision: Union[str, None] = "b47e8ad48d4c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "execution_dependencies",
        sa.Column("dependent_workflow_id", sa.String(length=64), nullable=False),
        sa.Column("prerequisite_workflow_id", sa.String(length=64), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["dependent_workflow_id"],
            ["temporal_execution_sources.workflow_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["prerequisite_workflow_id"],
            ["temporal_execution_sources.workflow_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "dependent_workflow_id",
            "prerequisite_workflow_id",
        ),
    )
    op.create_index(
        "ix_execution_dependencies_dependent_workflow_id",
        "execution_dependencies",
        ["dependent_workflow_id"],
        unique=False,
    )
    op.create_index(
        "ix_execution_dependencies_prerequisite_workflow_id",
        "execution_dependencies",
        ["prerequisite_workflow_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_execution_dependencies_prerequisite_workflow_id",
        table_name="execution_dependencies",
    )
    op.drop_index(
        "ix_execution_dependencies_dependent_workflow_id",
        table_name="execution_dependencies",
    )
    op.drop_table("execution_dependencies")
