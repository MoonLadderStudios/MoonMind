"""add_oauth_sessions

Revision ID: f1b2c3d4e5f6
Revises: e8f9a0b1c2d4
Create Date: 2026-03-21 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "f1b2c3d4e5f6"
down_revision: Union[str, None] = "e8f9a0b1c2d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Enum types
    oauth_status_enum = postgresql.ENUM(
        'pending', 'starting', 'tmate_ready', 'awaiting_user', 'verifying', 'registering_profile', 'succeeded', 'failed', 'cancelled', 'expired',
        name='oauthsessionstatus',
        create_type=False
    )
    oauth_status_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        'managed_agent_oauth_sessions',
        sa.Column('session_id', sa.String(length=128), nullable=False),
        sa.Column('runtime_id', sa.String(length=64), nullable=False),
        sa.Column('profile_id', sa.String(length=128), nullable=False),
        sa.Column('auth_mode', sa.Enum('oauth', 'api_key', name='managedagentauthmode'), server_default='oauth', nullable=False),
        sa.Column('session_transport', sa.String(length=64), server_default=sa.text("'tmate'"), nullable=False),
        sa.Column('volume_ref', sa.String(length=255), nullable=True),
        sa.Column('volume_mount_path', sa.String(length=512), nullable=True),
        sa.Column('status', oauth_status_enum, server_default='pending', nullable=False),
        sa.Column('requested_by_user_id', sa.String(length=128), nullable=True),
        sa.Column('account_label', sa.String(length=255), nullable=True),
        sa.Column('tmate_web_url', sa.String(length=1024), nullable=True),
        sa.Column('tmate_ssh_url', sa.String(length=1024), nullable=True),
        sa.Column('container_name', sa.String(length=255), nullable=True),
        sa.Column('worker_service', sa.String(length=255), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('cancelled_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('failure_reason', sa.String(length=1024), nullable=True),
        sa.Column('metadata_json', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('session_id')
    )
    op.create_index('ix_oauth_sessions_profile', 'managed_agent_oauth_sessions', ['profile_id'], unique=False)
    op.create_index('ix_oauth_sessions_status', 'managed_agent_oauth_sessions', ['status'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_oauth_sessions_status', table_name='managed_agent_oauth_sessions')
    op.drop_index('ix_oauth_sessions_profile', table_name='managed_agent_oauth_sessions')
    op.drop_table('managed_agent_oauth_sessions')
    
    oauth_status_enum = postgresql.ENUM(name='oauthsessionstatus', create_type=False)
    oauth_status_enum.drop(op.get_bind(), checkfirst=True)
