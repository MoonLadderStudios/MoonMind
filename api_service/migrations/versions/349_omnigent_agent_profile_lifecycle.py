"""Add immutable Omnigent profile lifecycle and usage evidence.

Revision ID: 349_omnigent_agent_profile_lifecycle
Revises: 348_omnigent_agent_profiles
"""
from alembic import op
import sqlalchemy as sa

revision = "349_omnigent_agent_profile_lifecycle"
down_revision = "348_omnigent_agent_profiles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "omnigent_agent_profile_audit_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "profile_id",
            sa.String(128),
            sa.ForeignKey("omnigent_agent_profiles.profile_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer()),
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("user.id")),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_omnigent_agent_profile_audit_profile",
        "omnigent_agent_profile_audit_events",
        ["profile_id", "created_at"],
    )
    op.create_table(
        "omnigent_agent_profile_usage",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("consumer_type", sa.String(32), nullable=False),
        sa.Column("consumer_id", sa.String(255), nullable=False),
        sa.Column(
            "profile_id",
            sa.String(128),
            sa.ForeignKey("omnigent_agent_profiles.profile_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("digest", sa.String(71), nullable=False),
        sa.Column("effective_snapshot", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "consumer_type", "consumer_id", name="uq_omnigent_profile_consumer"
        ),
    )
    op.create_index(
        "ix_omnigent_profile_usage_profile",
        "omnigent_agent_profile_usage",
        ["profile_id", "version"],
    )


def downgrade() -> None:
    op.drop_table("omnigent_agent_profile_usage")
    op.drop_table("omnigent_agent_profile_audit_events")
