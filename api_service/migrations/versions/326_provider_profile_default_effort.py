"""add provider profile default effort

Revision ID: 326_profile_effort
Revises: 316_provider_profile_auth_state
Create Date: 2026-06-24 00:00:00.000000

"""

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "326_profile_effort"
down_revision: Union[str, None] = "316_provider_profile_auth_state"

__all__ = ["revision", "down_revision", "upgrade", "downgrade"]


def upgrade() -> None:
    op.add_column(
        "managed_agent_provider_profiles",
        sa.Column("default_effort", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("managed_agent_provider_profiles", "default_effort")
