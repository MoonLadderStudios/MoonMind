"""Persistent versioned Omnigent agent profiles.

MoonLadderStudios/MoonMind#3517

Revision ID: 349_omnigent_agent_profiles
Revises: 348_omnigent_policy_authority
"""
from alembic import op
import sqlalchemy as sa

revision = "349_omnigent_agent_profiles"
down_revision = "348_omnigent_policy_authority"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table("omnigent_agent_profiles",
        sa.Column("profile_id", sa.String(128), primary_key=True),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text()), sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("user.id", ondelete="SET NULL")),
        sa.Column("visibility", sa.String(32), nullable=False), sa.Column("state", sa.String(32), nullable=False),
        sa.Column("active_version", sa.Integer()), sa.Column("default_for_runtime", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
    op.create_index("ix_omnigent_agent_profiles_state", "omnigent_agent_profiles", ["state"])
    op.create_index("ix_omnigent_agent_profiles_owner", "omnigent_agent_profiles", ["owner_id"])
    op.create_table("omnigent_agent_profile_versions",
        sa.Column("id", sa.Uuid(), primary_key=True), sa.Column("profile_id", sa.String(128), sa.ForeignKey("omnigent_agent_profiles.profile_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False), sa.Column("digest", sa.String(71), nullable=False), sa.Column("document", sa.JSON(), nullable=False),
        sa.Column("parent_version", sa.Integer()), sa.Column("cloned_from_profile_id", sa.String(128)), sa.Column("cloned_from_version", sa.Integer()),
        sa.Column("upstream_snapshot", sa.JSON()), sa.Column("validation_result", sa.JSON()), sa.Column("rollout_metadata", sa.JSON()),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("user.id", ondelete="SET NULL")), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("profile_id", "version", name="uq_omnigent_agent_profile_version"), sa.UniqueConstraint("profile_id", "digest", name="uq_omnigent_agent_profile_digest"))
    op.create_table("omnigent_upstream_agent_projections",
        sa.Column("projection_id", sa.String(255), primary_key=True), sa.Column("endpoint_ref", sa.String(128), nullable=False),
        sa.Column("bridge_mode", sa.String(64), nullable=False), sa.Column("upstream_id", sa.String(255), nullable=False), sa.Column("upstream_version", sa.String(255)),
        sa.Column("metadata_snapshot", sa.JSON(), nullable=False), sa.Column("available", sa.Boolean(), nullable=False), sa.Column("compatible", sa.Boolean(), nullable=False),
        sa.Column("last_successful_sync_at", sa.DateTime(timezone=True)), sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=False), sa.Column("error", sa.Text()))
    op.create_index("ix_omnigent_upstream_agents_endpoint", "omnigent_upstream_agent_projections", ["endpoint_ref"])

def downgrade() -> None:
    op.drop_table("omnigent_upstream_agent_projections")
    op.drop_table("omnigent_agent_profile_versions")
    op.drop_table("omnigent_agent_profiles")
