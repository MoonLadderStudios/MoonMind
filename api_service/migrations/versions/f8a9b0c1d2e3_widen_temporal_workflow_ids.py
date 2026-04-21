"""widen temporal workflow ids

Revision ID: f8a9b0c1d2e3
Revises: e6f7a8b9c0d1
Create Date: 2026-04-21 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "f8a9b0c1d2e3"
down_revision: Union[str, None] = "e6f7a8b9c0d1"
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
    op.alter_column(
        "temporal_execution_sources",
        "workflow_id",
        existing_type=sa.String(length=64),
        type_=sa.String(length=255),
        existing_nullable=False,
    )
    op.alter_column(
        "temporal_executions",
        "workflow_id",
        existing_type=sa.String(length=64),
        type_=sa.String(length=255),
        existing_nullable=False,
    )
    op.alter_column(
        "execution_dependencies",
        "dependent_workflow_id",
        existing_type=sa.String(length=64),
        type_=sa.String(length=255),
        existing_nullable=False,
    )
    op.alter_column(
        "execution_dependencies",
        "prerequisite_workflow_id",
        existing_type=sa.String(length=64),
        type_=sa.String(length=255),
        existing_nullable=False,
    )
    op.alter_column(
        "temporal_integration_correlations",
        "workflow_id",
        existing_type=sa.String(length=64),
        type_=sa.String(length=255),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "temporal_integration_correlations",
        "workflow_id",
        existing_type=sa.String(length=255),
        type_=sa.String(length=64),
        existing_nullable=False,
    )
    op.alter_column(
        "execution_dependencies",
        "prerequisite_workflow_id",
        existing_type=sa.String(length=255),
        type_=sa.String(length=64),
        existing_nullable=False,
    )
    op.alter_column(
        "execution_dependencies",
        "dependent_workflow_id",
        existing_type=sa.String(length=255),
        type_=sa.String(length=64),
        existing_nullable=False,
    )
    op.alter_column(
        "temporal_executions",
        "workflow_id",
        existing_type=sa.String(length=255),
        type_=sa.String(length=64),
        existing_nullable=False,
    )
    op.alter_column(
        "temporal_execution_sources",
        "workflow_id",
        existing_type=sa.String(length=255),
        type_=sa.String(length=64),
        existing_nullable=False,
    )
