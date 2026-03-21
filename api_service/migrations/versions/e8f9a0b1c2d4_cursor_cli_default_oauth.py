"""Cursor CLI default auth profile: OAuth

Revision ID: e8f9a0b1c2d4
Revises: a7f1b2c3d4e5
Create Date: 2026-03-21 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


revision: str = "e8f9a0b1c2d4"
down_revision: Union[str, None] = "a7f1b2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        UPDATE managed_agent_auth_profiles
        SET
            auth_mode = 'oauth',
            volume_ref = COALESCE(volume_ref, 'cursor_auth_volume'),
            volume_mount_path = COALESCE(volume_mount_path, '/home/app/.cursor')
        WHERE profile_id = 'cursor-cli-default' AND runtime_id = 'cursor_cli'
    """)


def downgrade() -> None:
    op.execute("""
        UPDATE managed_agent_auth_profiles
        SET auth_mode = 'api_key'
        WHERE profile_id = 'cursor-cli-default' AND runtime_id = 'cursor_cli'
    """)
