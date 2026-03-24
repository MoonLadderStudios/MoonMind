"""Add anthropic_api_key_encrypted to user_profile

Revision ID: b2c3d4e5f6a7
Revises: a8b9c0d1e2f3
Create Date: 2026-03-24

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlalchemy_utils


revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, None] = "a8b9c0d1e2f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "user_profile",
        sa.Column(
            "anthropic_api_key_encrypted",
            sqlalchemy_utils.types.encrypted.encrypted_type.EncryptedType(),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("user_profile", "anthropic_api_key_encrypted")
