"""Seed default cursor_cli auth profile

Revision ID: a7f1b2c3d4e5
Revises: c1d2e3f4a5b6
Create Date: 2026-03-20 23:08:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'a7f1b2c3d4e5'
down_revision: Union[str, None] = 'c1d2e3f4a5b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        INSERT INTO managed_agent_auth_profiles (
            profile_id, runtime_id, auth_mode,
            max_parallel_runs, cooldown_after_429_seconds,
            rate_limit_policy, enabled
        ) VALUES (
            'cursor-cli-default', 'cursor_cli', 'api_key',
            1, 300,
            'backoff', true
        ) ON CONFLICT (profile_id) DO NOTHING
    """)


def downgrade() -> None:
    op.execute(
        "DELETE FROM managed_agent_auth_profiles "
        "WHERE profile_id = 'cursor-cli-default'"
    )
