"""merge heads

Revision ID: 1a2b3c4d5e6f
Revises: b2c3d4e5f6a7, c7e8f9a0b1c3
Create Date: 2026-03-24 17:35:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1a2b3c4d5e6f'
down_revision: Union[str, None, Sequence[str]] = ('b2c3d4e5f6a7', 'c7e8f9a0b1c3')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
