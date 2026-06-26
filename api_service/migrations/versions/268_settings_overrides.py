"""Add scoped settings overrides and audit events.

Revision ID: 268_settings_overrides
Revises: f2a3b4c5d6e7
Create Date: 2026-04-28
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "268_settings_overrides"
down_revision = "f2a3b4c5d6e7"
branch_labels = None
depends_on = None


def _json_type() -> sa.types.TypeEngine:
    return postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite")


def upgrade() -> None:
    default_subject = sa.text("'00000000-0000-0000-0000-000000000000'")
    op.create_table(
        "settings_overrides",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("scope", sa.String(length=32), nullable=False),
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            nullable=False,
            server_default=default_subject,
        ),
        sa.Column("user_id", sa.Uuid(), nullable=False, server_default=default_subject),
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column("value_json", _json_type(), nullable=True),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("value_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint(
            "scope in ('user', 'workspace')",
            name="ck_settings_overrides_scope",
        ),
        sa.UniqueConstraint(
            "scope",
            "workspace_id",
            "user_id",
            "key",
            name="uq_settings_overrides_scope_subject_key",
        ),
    )
    op.create_table(
        "settings_audit_events",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column("scope", sa.String(length=32), nullable=False),
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            nullable=False,
            server_default=default_subject,
        ),
        sa.Column("user_id", sa.Uuid(), nullable=False, server_default=default_subject),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("old_value_json", _json_type(), nullable=True),
        sa.Column("new_value_json", _json_type(), nullable=True),
        sa.Column("redacted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("request_id", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index(
        "ix_settings_audit_events_key_scope",
        "settings_audit_events",
        ["key", "scope"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_settings_audit_events_key_scope",
        table_name="settings_audit_events",
    )
    op.drop_table("settings_audit_events")
    op.drop_table("settings_overrides")
