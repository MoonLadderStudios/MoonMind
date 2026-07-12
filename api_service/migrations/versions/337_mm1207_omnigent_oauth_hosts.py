"""Persist profile-bound Omnigent OAuth host bindings and leases.

Revision ID: 337_mm1207_oauth_hosts
Revises: 336_codex_oauth_capacity
Create Date: 2026-07-12
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "337_mm1207_oauth_hosts"
down_revision: Union[str, None] = "336_codex_oauth_capacity"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "omnigent_oauth_host_bindings",
        sa.Column("binding_ref", sa.String(length=255), primary_key=True),
        sa.Column("provider_profile_id", sa.String(length=128), nullable=False),
        sa.Column("endpoint_ref", sa.String(length=255), nullable=False),
        sa.Column("harness", sa.String(length=64), nullable=False),
        sa.Column("credential_mount_json", sa.JSON(), nullable=False),
        sa.Column("static_host_id", sa.String(length=255), nullable=True),
        sa.Column("host_launch_profile_ref", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["provider_profile_id"], ["managed_agent_provider_profiles.profile_id"], ondelete="CASCADE"),
        sa.UniqueConstraint("provider_profile_id", name="uq_omnigent_oauth_binding_profile"),
    )
    op.create_table(
        "omnigent_oauth_host_leases",
        sa.Column("lease_id", sa.String(length=255), primary_key=True),
        sa.Column("provider_profile_id", sa.String(length=128), nullable=False),
        sa.Column("provider_lease_id", sa.String(length=255), nullable=False),
        sa.Column("binding_ref", sa.String(length=255), nullable=False),
        sa.Column("credential_generation", sa.Integer(), nullable=False),
        sa.Column("container_id", sa.String(length=255), nullable=True),
        sa.Column("container_name", sa.String(length=255), nullable=True),
        sa.Column("omnigent_host_id", sa.String(length=255), nullable=True),
        sa.Column("omnigent_session_id", sa.String(length=255), nullable=True),
        sa.Column("bridge_session_id", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("credential_generation >= 1", name="ck_omnigent_oauth_host_lease_generation"),
        sa.CheckConstraint("expires_at > acquired_at", name="ck_omnigent_oauth_host_lease_expiry"),
        sa.ForeignKeyConstraint(["binding_ref"], ["omnigent_oauth_host_bindings.binding_ref"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["provider_profile_id"], ["managed_agent_provider_profiles.profile_id"], ondelete="CASCADE"),
        sa.UniqueConstraint("provider_profile_id", name="uq_omnigent_oauth_host_lease_profile"),
        sa.UniqueConstraint("provider_lease_id", name="uq_omnigent_oauth_host_provider_lease"),
    )
    op.create_index("ix_omnigent_oauth_host_lease_expiry", "omnigent_oauth_host_leases", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_omnigent_oauth_host_lease_expiry", table_name="omnigent_oauth_host_leases")
    op.drop_table("omnigent_oauth_host_leases")
    op.drop_table("omnigent_oauth_host_bindings")
