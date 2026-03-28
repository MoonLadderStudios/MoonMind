"""Merge managed_secrets and cooldown_default_900 migration heads

Revision ID: 1d15a2944011
Revises: a9b8c7d6e5f4, b7431e5f8a92
Create Date: 2026-03-28 17:50:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '1d15a2944011'
down_revision = ('a9b8c7d6e5f4', 'b7431e5f8a92')
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
