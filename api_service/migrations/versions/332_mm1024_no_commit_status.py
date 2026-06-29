"""Add no_commit workflow automation status.

Revision ID: 332_mm1024_no_commit_status
Revises: 331_moonspec_phase_names
Create Date: 2026-06-29
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "332_mm1024_no_commit_status"
down_revision: Union[str, None] = "331_moonspec_phase_names"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ENUM_TYPE_NAMES = ("specautomationrunstatus", "moonmindworkflowstate")


def _enum_labels(connection: sa.Connection, type_name: str) -> set[str]:
    rows = connection.execute(
        sa.text(
            """
            select enumlabel
            from pg_enum
            join pg_type on pg_enum.enumtypid = pg_type.oid
            where pg_type.typname = :type_name
            """
        ),
        {"type_name": type_name},
    )
    return {str(row[0]) for row in rows}


def _add_enum_value_if_needed(
    connection: sa.Connection, *, type_name: str, value: str
) -> None:
    if value in _enum_labels(connection, type_name):
        return
    op.execute(sa.text(f"alter type {type_name} add value {value!r}"))


def upgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        connection.execute(sa.text("COMMIT"))
        for type_name in _ENUM_TYPE_NAMES:
            _add_enum_value_if_needed(
                connection, type_name=type_name, value="no_commit"
            )
    op.execute(
        sa.text(
            "update automation_runs set status = 'no_commit' where status = 'no_changes'"
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "update automation_runs set status = 'no_changes' where status = 'no_commit'"
        )
    )
