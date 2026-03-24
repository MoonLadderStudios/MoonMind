"""Merge multiple heads

Revision ID: f9d1b627d0eb
Revises: a1b2c3d4e5f7, b92f4891f27c
Create Date: 2026-03-22 23:10:30.306539

"""
from typing import Sequence, Union



# revision identifiers, used by Alembic.
revision: str = 'f9d1b627d0eb'
down_revision: Union[str, None] = ('a1b2c3d4e5f7', 'b92f4891f27c')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
