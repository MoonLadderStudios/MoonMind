"""add_manifest_table

Revision ID: cb32e6509d1a
Revises: 909263807406
Create Date: 2025-07-01 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "cb32e6509d1a"
down_revision: Union[str, None] = "909263807406"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "manifest",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("last_indexed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("name"),
    )


def downgrade() -> None:
    op.drop_table("manifest")
