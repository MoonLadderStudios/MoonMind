"""Merge heads before provider profiles

Revision ID: 61c25ebc7201
Revises: b1c2d3e4f5a6, d5e6f7a8b9c0
Create Date: 2026-03-27 00:01:12.751293

"""
from typing import Union

# revision identifiers, used by Alembic.
revision: str = '61c25ebc7201'
down_revision: Union[str, None] = ('b1c2d3e4f5a6', 'd5e6f7a8b9c0')


def upgrade() -> None:
    _ = revision
    _ = down_revision
    pass


def downgrade() -> None:
    pass
