"""Add finish summary projection fields.

Revision ID: 313_finish_summary_fields
Revises: 312_source_mapping_cutover
Create Date: 2026-06-03
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "313_finish_summary_fields"
down_revision: Union[str, None] = "312_source_mapping_cutover"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _json_type() -> sa.types.TypeEngine:
    return postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite")


def upgrade() -> None:
    for table_name in ("temporal_execution_sources", "temporal_executions"):
        op.add_column(
            table_name,
            sa.Column("finish_outcome_code", sa.String(length=64), nullable=True),
        )
        op.create_index(
            f"ix_{table_name}_finish_outcome_code",
            table_name,
            ["finish_outcome_code"],
            unique=False,
        )
        op.add_column(
            table_name,
            sa.Column("finish_summary_json", _json_type(), nullable=True),
        )


def downgrade() -> None:
    for table_name in ("temporal_executions", "temporal_execution_sources"):
        op.drop_index(f"ix_{table_name}_finish_outcome_code", table_name=table_name)
        op.drop_column(table_name, "finish_summary_json")
        op.drop_column(table_name, "finish_outcome_code")
