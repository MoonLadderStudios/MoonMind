"""Increase manifest.content_hash length for sha256-prefixed values."""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "202602190004"
down_revision: Union[str, None] = "202602190003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "manifest",
        "content_hash",
        existing_type=sa.String(length=64),
        type_=sa.String(length=80),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "manifest",
        "content_hash",
        existing_type=sa.String(length=80),
        type_=sa.String(length=64),
        existing_nullable=False,
    )
