"""Persist product-owned Omnigent effective launch decisions.

Revision ID: 345_omnigent_launch
Revises: 344_embedded_host_auth_authority
"""

from alembic import op
import sqlalchemy as sa

revision = "345_omnigent_launch"
down_revision = "344_embedded_host_auth_authority"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("omnigent_oauth_host_bindings", sa.Column("execution_profile_ref", sa.String(255), nullable=True))
    op.add_column("omnigent_oauth_host_bindings", sa.Column("launch_policy_ref", sa.String(255), nullable=True))
    op.add_column("omnigent_oauth_host_bindings", sa.Column("effective_launch_snapshot_json", sa.JSON(), nullable=True))
    op.add_column("omnigent_oauth_host_leases", sa.Column("effective_launch_snapshot_json", sa.JSON(), nullable=True))
    op.add_column("omnigent_bridge_sessions", sa.Column("effective_launch_snapshot_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("omnigent_bridge_sessions", "effective_launch_snapshot_json")
    op.drop_column("omnigent_oauth_host_leases", "effective_launch_snapshot_json")
    op.drop_column("omnigent_oauth_host_bindings", "effective_launch_snapshot_json")
    op.drop_column("omnigent_oauth_host_bindings", "launch_policy_ref")
    op.drop_column("omnigent_oauth_host_bindings", "execution_profile_ref")
