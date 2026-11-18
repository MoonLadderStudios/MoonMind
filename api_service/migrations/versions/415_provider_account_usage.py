"""Cache provider account identity and usage observations per profile.

Existing profiles get no rows: their observation state is unknown until a
collector or runtime reports one. Nothing is inferred from ``account_label``.
"""

import sqlalchemy as sa
from alembic import op

revision = "415_provider_account_usage"
down_revision = "414_settings_generations"
branch_labels = None
depends_on = None

_TABLE = "provider_account_usage_observations"


def upgrade():
    op.create_table(
        _TABLE,
        sa.Column(
            "profile_id",
            sa.String(length=128),
            sa.ForeignKey(
                "managed_agent_provider_profiles.profile_id", ondelete="CASCADE"
            ),
            primary_key=True,
        ),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("credential_generation", sa.Integer(), nullable=False),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "snapshot_revision", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("provider_id", sa.String(length=64), nullable=False),
        sa.Column("runtime_id", sa.String(length=64), nullable=False),
        sa.Column("bound_account_id", sa.String(length=128), nullable=True),
        sa.Column("bound_workspace_id", sa.String(length=128), nullable=True),
        sa.Column("account_key", sa.String(length=64), nullable=True),
        sa.Column("identity_json", sa.JSON(), nullable=True),
        sa.Column("usage_json", sa.JSON(), nullable=True),
        sa.Column(
            "refresh_state",
            sa.String(length=16),
            nullable=False,
            server_default="idle",
        ),
        sa.Column("refresh_trigger", sa.String(length=16), nullable=True),
        sa.Column("refresh_attempt_id", sa.String(length=64), nullable=True),
        sa.Column("refresh_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("refresh_deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_failure_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_failure_code", sa.String(length=32), nullable=True),
        sa.Column(
            "consecutive_failures", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("next_allowed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_manual_request_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reset_requested_for", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint(
            "refresh_state IN ('idle', 'queued', 'running', 'deferred')",
            name="ck_provider_account_usage_refresh_state",
        ),
        sa.CheckConstraint(
            "credential_generation >= 1",
            name="ck_provider_account_usage_generation_positive",
        ),
    )
    op.create_index("ix_provider_account_usage_account_key", _TABLE, ["account_key"])


def downgrade():
    # Observations are a re-collectable cache, never the only copy of
    # operator data, so an older application can simply start without them.
    op.drop_index("ix_provider_account_usage_account_key", table_name=_TABLE)
    op.drop_table(_TABLE)
