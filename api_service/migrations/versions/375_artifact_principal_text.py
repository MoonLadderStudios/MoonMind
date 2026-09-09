"""Preserve complete artifact principals, including the owner-type prefix.

Container jobs accept a 255-character owner id. Its artifact principal adds
``service:`` (or another owner type), so VARCHAR(255) rejects valid launch,
terminal, and cleanup evidence. Keep the exact identity for existing histories
and authorization; only widen the artifact ownership storage.
"""

import sqlalchemy as sa
from alembic import op

revision = "375_artifact_principal_text"
down_revision = "374_identity_mapping_k3"
branch_labels = None
depends_on = None

_COLUMNS = (
    ("temporal_artifacts", "created_by_principal", True),
    ("temporal_artifact_pins", "pinned_by_principal", False),
)


def upgrade() -> None:
    for table, column, nullable in _COLUMNS:
        with op.batch_alter_table(table) as batch:
            batch.alter_column(
                column,
                existing_type=sa.String(255),
                type_=sa.Text(),
                existing_nullable=nullable,
            )


def downgrade() -> None:
    # Validate both tables before any DDL. Never truncate an authorization
    # identity or leave the two ownership fields on different schemas.
    for table_name, column, _nullable in _COLUMNS:
        table = sa.table(table_name, sa.column(column, sa.Text()))
        oversized = sa.select(sa.exists().where(sa.func.length(table.c[column]) > 255))
        if op.get_bind().execute(oversized).scalar():
            raise RuntimeError(
                "Cannot downgrade artifact principals to VARCHAR(255): retained "
                "artifacts or pins contain longer identities. Keep the widened "
                "schema until their retention period has expired."
            )
    for table, column, nullable in _COLUMNS:
        with op.batch_alter_table(table) as batch:
            batch.alter_column(
                column,
                existing_type=sa.Text(),
                type_=sa.String(255),
                existing_nullable=nullable,
            )
