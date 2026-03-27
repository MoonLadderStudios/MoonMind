"""rename_tables_to_provider_profiles

Revision ID: 4b53a4790314
Revises: 61c25ebc7201
Create Date: 2026-03-27 00:02:53.101165

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4b53a4790314'
down_revision: Union[str, None] = '61c25ebc7201'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.rename_table('managed_agent_auth_profiles', 'managed_agent_provider_profiles')
    op.rename_table('auth_profile_slot_leases', 'provider_profile_slot_leases')


def downgrade() -> None:
    op.rename_table('provider_profile_slot_leases', 'auth_profile_slot_leases')
    op.rename_table('managed_agent_provider_profiles', 'managed_agent_auth_profiles')
