"""Rename Temporal workflow type enum labels to workflow-native names.

Revision ID: 315_workflow_type_enum_cutover
Revises: 314_preset_catalog_table_rename
Create Date: 2026-06-11
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "315_workflow_type_enum_cutover"
down_revision: Union[str, None] = "314_preset_catalog_table_rename"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TYPE_NAME = "temporalworkflowtype"
_OLD_USER_WORKFLOW_TYPE = "MoonMind." + "Run"
_OLD_PROVIDER_PROFILE_MANAGER_TYPE = "MoonMind.AuthProfileManager"


def _postgresql_connection() -> sa.Connection | None:
    connection = op.get_bind()
    if connection is None or connection.dialect.name != "postgresql":
        return None
    return connection


def _enum_labels(connection: sa.Connection) -> set[str]:
    rows = connection.execute(
        sa.text(
            """
            select enumlabel
            from pg_enum
            join pg_type on pg_enum.enumtypid = pg_type.oid
            where pg_type.typname = :type_name
            """
        ),
        {"type_name": _TYPE_NAME},
    )
    return {str(row[0]) for row in rows}


def _rename_enum_value_if_needed(
    connection: sa.Connection,
    *,
    old_value: str,
    new_value: str,
) -> None:
    labels = _enum_labels(connection)
    if old_value not in labels or new_value in labels:
        return
    op.execute(
        sa.text(
            f"alter type {_TYPE_NAME} rename value "
            f"{old_value!r} to {new_value!r}"
        )
    )


def upgrade() -> None:
    connection = _postgresql_connection()
    if connection is None:
        return

    _rename_enum_value_if_needed(
        connection,
        old_value=_OLD_USER_WORKFLOW_TYPE,
        new_value="MoonMind.UserWorkflow",
    )
    _rename_enum_value_if_needed(
        connection,
        old_value=_OLD_PROVIDER_PROFILE_MANAGER_TYPE,
        new_value="MoonMind.ProviderProfileManager",
    )


def downgrade() -> None:
    # Revision 314 already uses the workflow-native enum labels in the ORM and
    # base migration, so reverting this label rename would leave downgraded
    # databases incompatible with that revision's code.
    return
