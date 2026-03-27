"""Fix encrypted session column types from bytea to text

Revision ID: d5e6f7a8b9c0
Revises: c3d4e5f6a7b8
Create Date: 2026-03-27 05:20:00.000000

StringEncryptedType (sqlalchemy_utils) stores encrypted values as
base64-encoded strings, requiring TEXT. The recreate migration
c8a9b0c1d2e3 incorrectly used BYTEA, causing INSERT failures.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd5e6f7a8b9c0'
down_revision: Union[str, None] = 'c3d4e5f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        'task_run_live_sessions',
        'attach_rw_encrypted',
        existing_type=sa.LargeBinary(),
        type_=sa.Text(),
        existing_nullable=True,
        postgresql_using='attach_rw_encrypted::text',
    )
    op.alter_column(
        'task_run_live_sessions',
        'web_rw_encrypted',
        existing_type=sa.LargeBinary(),
        type_=sa.Text(),
        existing_nullable=True,
        postgresql_using='web_rw_encrypted::text',
    )


def downgrade() -> None:
    op.alter_column(
        'task_run_live_sessions',
        'web_rw_encrypted',
        existing_type=sa.Text(),
        type_=sa.LargeBinary(),
        existing_nullable=True,
        postgresql_using='web_rw_encrypted::bytea',
    )
    op.alter_column(
        'task_run_live_sessions',
        'attach_rw_encrypted',
        existing_type=sa.Text(),
        type_=sa.LargeBinary(),
        existing_nullable=True,
        postgresql_using='attach_rw_encrypted::bytea',
    )
