"""Update default cooldown_after_429_seconds to 900

Revision ID: b7431e5f8a92
Revises: fa1b2c3d4e5f
Create Date: 2026-03-28 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b7431e5f8a92'
down_revision = 'fa1b2c3d4e5f'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        'managed_agent_provider_profiles',
        'cooldown_after_429_seconds',
        server_default=sa.text('900')
    )


def downgrade() -> None:
    op.alter_column(
        'managed_agent_provider_profiles',
        'cooldown_after_429_seconds',
        server_default=sa.text('300')
    )
