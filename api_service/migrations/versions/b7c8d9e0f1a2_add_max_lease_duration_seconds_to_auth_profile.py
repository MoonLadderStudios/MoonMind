"""Add max_lease_duration_seconds to managed_agent_auth_profiles

Revision ID: b7c8d9e0f1a2
Revises: f9d1b627d0eb
Create Date: 2026-03-25 21:35:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "b7c8d9e0f1a2"
down_revision: Union[str, None] = "f9d1b627d0eb"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "managed_agent_auth_profiles",
        sa.Column("max_lease_duration_seconds", sa.Integer(), nullable=False, server_default=sa.text("7200")),
    )
    # Backfill existing rows to 7200 (matches server_default)
    op.execute(
        "UPDATE managed_agent_auth_profiles SET max_lease_duration_seconds = 7200 WHERE max_lease_duration_seconds IS NULL"
    )


def downgrade() -> None:
    op.drop_column("managed_agent_auth_profiles", "max_lease_duration_seconds")
