"""add managed_secrets table

Revision ID: a9b8c7d6e5f4
Revises: fa1b2c3d4e5f
Create Date: 2026-03-28 16:30:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'a9b8c7d6e5f4'
down_revision = 'fa1b2c3d4e5f'
branch_labels = None
depends_on = None

def upgrade() -> None:
    # Create enum type first
    secret_status = postgresql.ENUM('active', 'disabled', 'rotated', 'deleted', 'invalid', name='secretstatus')
    secret_status.create(op.get_bind())

    # Create managed_secrets table
    op.create_table('managed_secrets',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('slug', sa.String(length=255), nullable=False),
    sa.Column('ciphertext', sa.Text(), nullable=False),
    sa.Column('status', secret_status, nullable=False, server_default='active'),
    sa.Column('details', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('slug')
    )
    
    op.create_index('ix_managed_secrets_slug', 'managed_secrets', ['slug'], unique=True)
    op.create_index('ix_managed_secrets_status', 'managed_secrets', ['status'], unique=False)

def downgrade() -> None:
    op.drop_index('ix_managed_secrets_status', table_name='managed_secrets')
    op.drop_index('ix_managed_secrets_slug', table_name='managed_secrets')
    op.drop_table('managed_secrets')
    
    # Drop enum type
    secret_status = postgresql.ENUM('active', 'disabled', 'rotated', 'deleted', 'invalid', name='secretstatus')
    secret_status.drop(op.get_bind())
