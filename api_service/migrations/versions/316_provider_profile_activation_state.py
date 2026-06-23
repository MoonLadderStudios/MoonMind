"""add provider profile activation state

Revision ID: 316_provider_profile_activation_state
Revises: 315_workflow_type_enum_cutover
Create Date: 2026-06-23 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "316_provider_profile_activation_state"
down_revision: Union[str, None] = "315_workflow_type_enum_cutover"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "upgrade", "downgrade"]


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


def upgrade() -> None:
    auth_state_enum = sa.Enum(*AUTH_STATES, name="providerprofileauthstate")
    disabled_reason_enum = sa.Enum(
        *DISABLED_REASONS, name="providerprofiledisabledreason"
    )
    auth_method_enum = sa.Enum(*AUTH_METHODS, name="providerprofileauthmethod")
    bind = op.get_bind()
    auth_state_enum.create(bind, checkfirst=True)
    disabled_reason_enum.create(bind, checkfirst=True)
    auth_method_enum.create(bind, checkfirst=True)

    op.alter_column(
        "managed_agent_provider_profiles",
        "enabled",
        server_default=sa.text("false"),
        existing_type=sa.Boolean(),
        existing_nullable=False,
    )
    op.add_column(
        "managed_agent_provider_profiles",
        sa.Column(
            "auth_state",
            auth_state_enum,
            nullable=False,
            server_default="not_configured",
        ),
    )
    op.add_column(
        "managed_agent_provider_profiles",
        sa.Column("disabled_reason", disabled_reason_enum, nullable=True),
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
        sa.Column("last_auth_method", auth_method_enum, nullable=True),
    )

    op.create_check_constraint(
        "ck_provider_profiles_auth_state",
        "managed_agent_provider_profiles",
        "auth_state IN ("
        "'not_configured', 'oauth_pending', 'api_key_pending', "
        "'connected', 'validation_failed', 'disconnected'"
        ")",
    )
    op.create_check_constraint(
        "ck_provider_profiles_disabled_reason",
        "managed_agent_provider_profiles",
        "disabled_reason IS NULL OR disabled_reason IN ("
        "'missing_credentials', 'auth_invalid', 'user_disabled', "
        "'policy_disabled', 'disconnected'"
        ")",
    )
    op.create_check_constraint(
        "ck_provider_profiles_last_auth_method",
        "managed_agent_provider_profiles",
        "last_auth_method IS NULL OR last_auth_method IN ("
        "'oauth_volume', 'secret_ref', 'manual'"
        ")",
    )

    op.create_index(
        "ix_provider_profiles_runtime_provider",
        "managed_agent_provider_profiles",
        ["runtime_id", "provider_id"],
        unique=False,
    )
    op.create_index(
        "ix_provider_profiles_auth_state",
        "managed_agent_provider_profiles",
        ["auth_state"],
        unique=False,
    )
    op.create_index(
        "ix_provider_profiles_readiness",
        "managed_agent_provider_profiles",
        ["runtime_id", "provider_id", "enabled", "auth_state"],
        unique=False,
    )


def downgrade() -> None:
    raise NotImplementedError("One-way migration: MM-872 provider profile activation state")
