"""Persist immutable Omnigent policy authority for MoonLadderStudios/MoonMind#3515.

Revision ID: 348_omnigent_policy_authority
Revises: 347_control_stop_continuations
"""

from alembic import op
import sqlalchemy as sa

revision = "348_omnigent_policy_authority"
down_revision = "347_control_stop_continuations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "omnigent_policies",
        sa.Column("policy_id", sa.String(128), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False, unique=True),
        sa.Column("owner_user_id", sa.Uuid(), sa.ForeignKey("user.id"), nullable=True),
        sa.Column("visibility", sa.String(32), nullable=False),
        sa.Column("default_version", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "omnigent_policy_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("policy_id", sa.String(128), sa.ForeignKey("omnigent_policies.policy_id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("document_json", sa.JSON(), nullable=False),
        sa.Column("digest", sa.String(80), nullable=False),
        sa.Column("parent_ref", sa.String(255), nullable=True),
        sa.Column("clone_source_ref", sa.String(255), nullable=True),
        sa.Column("supersedes_ref", sa.String(255), nullable=True),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("activated_by", sa.String(255), nullable=True),
        sa.Column("disabled_by", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("validation_json", sa.JSON(), nullable=False),
        sa.Column("compatibility_json", sa.JSON(), nullable=False),
        sa.Column("rollout_json", sa.JSON(), nullable=False),
        sa.Column("env_fallback_used", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.UniqueConstraint("policy_id", "version", name="uq_omnigent_policy_version"),
    )
    op.create_index("ix_omnigent_policy_versions_policy_id", "omnigent_policy_versions", ["policy_id"])
    op.create_index("ix_omnigent_policy_versions_digest", "omnigent_policy_versions", ["digest"])
    op.create_table(
        "omnigent_policy_events",
        sa.Column("event_id", sa.Uuid(), primary_key=True),
        sa.Column("policy_id", sa.String(128), sa.ForeignKey("omnigent_policies.policy_id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=True),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("actor", sa.String(255), nullable=False),
        sa.Column("detail_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_omnigent_policy_events_policy_created",
        "omnigent_policy_events",
        ["policy_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_omnigent_policy_events_policy_created", table_name="omnigent_policy_events")
    op.drop_table("omnigent_policy_events")
    op.drop_index("ix_omnigent_policy_versions_digest", table_name="omnigent_policy_versions")
    op.drop_index("ix_omnigent_policy_versions_policy_id", table_name="omnigent_policy_versions")
    op.drop_table("omnigent_policy_versions")
    op.drop_table("omnigent_policies")
