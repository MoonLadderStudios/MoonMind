"""merge provider profiles schema and claude rename heads

Revision ID: fa1b2c3d4e5f
Revises: 053758f254f3, e1f2a3b4c5d6
Create Date: 2026-03-27 10:42:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'fa1b2c3d4e5f'
down_revision: Union[str, None, Sequence[str]] = ('053758f254f3', 'e1f2a3b4c5d6')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
