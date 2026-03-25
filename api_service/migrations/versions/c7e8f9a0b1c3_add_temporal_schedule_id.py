"""add temporal_schedule_id to recurring_task_definitions

Revision ID: c7e8f9a0b1c3
Revises: f9d1b627d0eb
Create Date: 2026-03-24 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c7e8f9a0b1c3'
down_revision: Union[str, None] = 'f9d1b627d0eb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('recurring_task_definitions', sa.Column('temporal_schedule_id', sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column('recurring_task_definitions', 'temporal_schedule_id')
