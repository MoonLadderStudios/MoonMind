"""Add managed_agent_auth_profiles table and AUTH_PROFILE_MANAGER workflow type.

Revision ID: 202603140002
Revises: 1dd6e627fef8
Create Date: 2026-03-14 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "202603140002"
down_revision: Union[str, None] = "1dd6e627fef8"
branch_labels: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    # --- Add new enum values for PostgreSQL ---
    if is_postgres:
        op.execute(
            "ALTER TYPE temporalworkflowtype ADD VALUE IF NOT EXISTS "
            "'MoonMind.AuthProfileManager'"
        )
        op.execute(
            "DO $$ BEGIN "
            "CREATE TYPE managedagentauthmode AS ENUM ('oauth', 'api_key'); "
            "EXCEPTION WHEN duplicate_object THEN null; END $$;"
        )
        op.execute(
            "DO $$ BEGIN "
            "CREATE TYPE managedagentratelimitpolicy AS ENUM "
            "('backoff', 'queue', 'fail_fast'); "
            "EXCEPTION WHEN duplicate_object THEN null; END $$;"
        )

    # --- Create managed_agent_auth_profiles table ---
    auth_mode_type: sa.types.TypeEngine
    rate_limit_type: sa.types.TypeEngine
    if is_postgres:
        from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM
        auth_mode_type = PG_ENUM(
            "oauth", "api_key",
            name="managedagentauthmode",
            create_type=False,
        )
        rate_limit_type = PG_ENUM(
            "backoff", "queue", "fail_fast",
            name="managedagentratelimitpolicy",
            create_type=False,
        )
    else:
        auth_mode_type = sa.Enum("oauth", "api_key", name="managedagentauthmode")
        rate_limit_type = sa.Enum(
            "backoff", "queue", "fail_fast", name="managedagentratelimitpolicy"
        )

    op.create_table(
        "managed_agent_auth_profiles",
        sa.Column("profile_id", sa.String(128), primary_key=True),
        sa.Column("runtime_id", sa.String(64), nullable=False),
        sa.Column("auth_mode", auth_mode_type, nullable=False),
        sa.Column("volume_ref", sa.String(255), nullable=True),
        sa.Column("volume_mount_path", sa.String(512), nullable=True),
        sa.Column("account_label", sa.String(255), nullable=True),
        sa.Column("api_key_ref", sa.String(255), nullable=True),
        sa.Column(
            "max_parallel_runs",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column(
            "cooldown_after_429_seconds",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("300"),
        ),
        sa.Column(
            "rate_limit_policy",
            rate_limit_type,
            nullable=False,
            server_default="backoff",
        ),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_auth_profiles_runtime",
        "managed_agent_auth_profiles",
        ["runtime_id"],
    )
    op.create_index(
        "ix_auth_profiles_enabled",
        "managed_agent_auth_profiles",
        ["enabled"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    op.drop_index("ix_auth_profiles_enabled", table_name="managed_agent_auth_profiles")
    op.drop_index("ix_auth_profiles_runtime", table_name="managed_agent_auth_profiles")
    op.drop_table("managed_agent_auth_profiles")

    if is_postgres:
        op.execute("DROP TYPE IF EXISTS managedagentratelimitpolicy")
        op.execute("DROP TYPE IF EXISTS managedagentauthmode")
