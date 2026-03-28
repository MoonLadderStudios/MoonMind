"""Recreate task_run_live_sessions

Revision ID: c8a9b0c1d2e3
Revises: 1a2b3c4d5e6f
Create Date: 2026-03-25 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'c8a9b0c1d2e3'
down_revision: Union[str, None] = '1a2b3c4d5e6f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('task_run_live_sessions',
    sa.Column('id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('task_run_id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('provider', postgresql.ENUM('none', name='agentjoblivesessionprovider', create_type=False), autoincrement=False, nullable=False),
    sa.Column('status', postgresql.ENUM('disabled', 'starting', 'ready', 'revoked', 'ended', 'error', name='agentjoblivesessionstatus', create_type=False), autoincrement=False, nullable=False),
    sa.Column('ready_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True),
    sa.Column('ended_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True),
    sa.Column('expires_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True),
    sa.Column('worker_id', sa.VARCHAR(length=255), autoincrement=False, nullable=True),
    sa.Column('worker_hostname', sa.VARCHAR(length=255), autoincrement=False, nullable=True),
    sa.Column('live_session_name', sa.VARCHAR(length=255), autoincrement=False, nullable=True),
    sa.Column('live_session_socket_path', sa.VARCHAR(length=1024), autoincrement=False, nullable=True),
    sa.Column('attach_ro', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('attach_rw_encrypted', postgresql.BYTEA(), autoincrement=False, nullable=True),
    sa.Column('web_ro', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('web_rw_encrypted', postgresql.BYTEA(), autoincrement=False, nullable=True),
    sa.Column('rw_granted_until', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True),
    sa.Column('last_heartbeat_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True),
    sa.Column('error_message', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('task_run_live_sessions_pkey'))
    )
    op.create_index(op.f('ix_task_run_live_sessions_worker_id'), 'task_run_live_sessions', ['worker_id'], unique=False)
    op.create_index(op.f('ix_task_run_live_sessions_task_run_id'), 'task_run_live_sessions', ['task_run_id'], unique=True)
    op.create_index(op.f('ix_task_run_live_sessions_status_expires_at'), 'task_run_live_sessions', ['status', 'expires_at'], unique=False)
    op.create_index(op.f('ix_task_run_live_sessions_last_heartbeat_at'), 'task_run_live_sessions', ['last_heartbeat_at'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_task_run_live_sessions_last_heartbeat_at'), table_name='task_run_live_sessions')
    op.drop_index(op.f('ix_task_run_live_sessions_status_expires_at'), table_name='task_run_live_sessions')
    op.drop_index(op.f('ix_task_run_live_sessions_task_run_id'), table_name='task_run_live_sessions')
    op.drop_index(op.f('ix_task_run_live_sessions_worker_id'), table_name='task_run_live_sessions')
    op.drop_table('task_run_live_sessions')
