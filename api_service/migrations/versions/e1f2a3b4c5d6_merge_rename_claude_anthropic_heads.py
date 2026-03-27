"""merge rename claude anthropic and encryption heads

Revision ID: e1f2a3b4c5d6
Revises: d5e6f7a8b9c0, b1c2d3e4f5a6
Create Date: 2026-03-27 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e1f2a3b4c5d6'
down_revision: Union[str, None, Sequence[str]] = ('d5e6f7a8b9c0', 'b1c2d3e4f5a6')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
