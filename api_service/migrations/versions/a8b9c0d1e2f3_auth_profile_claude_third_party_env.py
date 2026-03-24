"""Add runtime_env_overrides and api_key_env_var to auth profiles

Revision ID: a8b9c0d1e2f3
Revises: f2a3b4c5d6e7
Create Date: 2026-03-23

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a8b9c0d1e2f3"
down_revision: Union[str, None] = "f2a3b4c5d6e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "managed_agent_auth_profiles",
        sa.Column(
            "runtime_env_overrides",
            sa.JSON(),
            nullable=True,
        ),
    )
    op.add_column(
        "managed_agent_auth_profiles",
        sa.Column("api_key_env_var", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("managed_agent_auth_profiles", "api_key_env_var")
    op.drop_column("managed_agent_auth_profiles", "runtime_env_overrides")
