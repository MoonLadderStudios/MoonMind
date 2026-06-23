"""Add provider profile auth lifecycle fields.

Revision ID: 316_provider_profile_auth_lifecycle
Revises: 315_workflow_type_enum_cutover
Create Date: 2026-06-23
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "316_provider_profile_auth_lifecycle"
down_revision: Union[str, None] = "315_workflow_type_enum_cutover"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


AUTH_STATES = (
    "not_configured",
    "oauth_pending",
    "api_key_pending",
    "connected",
    "validation_failed",
    "disconnected",
)
DISABLED_REASONS = (
    "missing_credentials",
    "auth_invalid",
    "user_disabled",
    "policy_disabled",
    "disconnected",
)
AUTH_METHODS = ("oauth_volume", "secret_ref", "manual")


def _enum_type(name: str, values: tuple[str, ...]) -> sa.Enum:
    return sa.Enum(*values, name=name)


def upgrade() -> None:
    auth_state = _enum_type("providerprofileauthstate", AUTH_STATES)
    disabled_reason = _enum_type("providerprofiledisabledreason", DISABLED_REASONS)
    auth_method = _enum_type("providerprofileauthmethod", AUTH_METHODS)

    bind = op.get_bind()
    auth_state.create(bind, checkfirst=True)
    disabled_reason.create(bind, checkfirst=True)
    auth_method.create(bind, checkfirst=True)

    op.add_column(
        "managed_agent_provider_profiles",
        sa.Column(
            "auth_state",
            auth_state,
            nullable=False,
            server_default="not_configured",
        ),
    )
    op.add_column(
        "managed_agent_provider_profiles",
        sa.Column("disabled_reason", disabled_reason, nullable=True),
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
        sa.Column("last_auth_method", auth_method, nullable=True),
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

    bind = op.get_bind()
    _enum_type("providerprofileauthmethod", AUTH_METHODS).drop(bind, checkfirst=True)
    _enum_type("providerprofiledisabledreason", DISABLED_REASONS).drop(
        bind, checkfirst=True
    )
    _enum_type("providerprofileauthstate", AUTH_STATES).drop(bind, checkfirst=True)
