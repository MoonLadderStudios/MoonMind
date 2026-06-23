"""provider profile activation state

Revision ID: 316_provider_profile_activation_state
Revises: 315_workflow_type_enum_cutover
Create Date: 2026-06-23 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.sql import column, table

revision: str = "316_provider_profile_activation_state"
down_revision: Union[str, None] = "315_workflow_type_enum_cutover"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "upgrade", "downgrade"]

profiles_table = table(
    "managed_agent_provider_profiles",
    column("enabled", sa.Boolean),
    column("auth_state", sa.String),
    column("disabled_reason", sa.String),
    column("first_authenticated_at", sa.DateTime(timezone=True)),
    column("last_validated_at", sa.DateTime(timezone=True)),
    column("last_auth_method", sa.String),
)


def upgrade() -> None:
    op.add_column(
        "managed_agent_provider_profiles",
        sa.Column(
            "auth_state",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'not_configured'"),
        ),
    )
    op.add_column(
        "managed_agent_provider_profiles",
        sa.Column("disabled_reason", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "managed_agent_provider_profiles",
        sa.Column("first_authenticated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "managed_agent_provider_profiles",
        sa.Column("last_validated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "managed_agent_provider_profiles",
        sa.Column("last_auth_method", sa.String(length=32), nullable=True),
    )

    bind = op.get_bind()
    bind.execute(
        profiles_table.update()
        .where(profiles_table.c.enabled.is_(True))
        .values(auth_state="connected", disabled_reason=None)
    )
    bind.execute(
        profiles_table.update()
        .where(profiles_table.c.enabled.is_(False))
        .values(auth_state="connected", disabled_reason="user_disabled")
    )
    op.create_index(
        "ix_provider_profiles_auth_state",
        "managed_agent_provider_profiles",
        ["auth_state"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_provider_profiles_auth_state",
        table_name="managed_agent_provider_profiles",
    )
    op.drop_column("managed_agent_provider_profiles", "last_auth_method")
    op.drop_column("managed_agent_provider_profiles", "last_validated_at")
    op.drop_column("managed_agent_provider_profiles", "first_authenticated_at")
    op.drop_column("managed_agent_provider_profiles", "disabled_reason")
    op.drop_column("managed_agent_provider_profiles", "auth_state")
