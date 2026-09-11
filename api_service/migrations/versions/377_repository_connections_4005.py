"""Scoped repository connections and transactional routes (#4005).

Revision ID: 377_repository_connections_4005
Revises: 376_drop_manifest_registry_4192

Single writable authority for scoped RepositoryConnections, many-to-many
assignments, and per-scope route defaults.  Deployment JSON files become
versioned read-only snapshots published from these rows (or classified
legacy input); they are never an independently editable fallback policy.
Credential configuration is metadata-only (SecretRef locators / App refs);
raw secret bodies live only in Managed Secrets and trusted delivery.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

__all__ = [
    "revision",
    "down_revision",
    "branch_labels",
    "depends_on",
    "upgrade",
    "downgrade",
]

revision: str = "377_repository_connections_4005"
down_revision: Union[str, None] = "376_drop_manifest_registry_4192"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _json_column() -> sa.Column:
    return sa.Column(
        postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite"),
        nullable=False,
        server_default=sa.text("'{}'"),
    )


def _json_list_column() -> sa.Column:
    return sa.Column(
        postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite"),
        nullable=False,
        server_default=sa.text("'[]'"),
    )


def upgrade() -> None:
    op.create_table(
        "repository_connection_records",
        sa.Column("connection_id", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("provider", sa.String(length=16), nullable=False),
        sa.Column("hosting_service", sa.String(length=32), nullable=False),
        sa.Column("endpoint_normalized", sa.String(length=1024), nullable=False),
        sa.Column("endpoint_ref", sa.String(length=1024), nullable=False),
        sa.Column("trust_bundle_ref", sa.String(length=1024), nullable=True),
        sa.Column("allowed_operations", sa.JSON(), nullable=False),
        sa.Column("client_policy", sa.JSON(), nullable=False),
        sa.Column("credential_config", sa.JSON(), nullable=False),
        sa.Column(
            "lifecycle",
            sa.String(length=16),
            nullable=False,
            server_default="active",
        ),
        sa.Column("policy_revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "credential_revision", sa.Integer(), nullable=False, server_default="1"
        ),
        sa.Column("owner_ref", sa.String(length=255), nullable=False),
        sa.Column("scope_type", sa.String(length=16), nullable=False),
        sa.Column("scope_ref", sa.String(length=255), nullable=True),
        sa.Column("allowed_principal_refs", sa.JSON(), nullable=False),
        sa.Column(
            "tombstone", sa.Boolean(), nullable=False, server_default=sa.text("false")
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
        sa.PrimaryKeyConstraint("connection_id"),
    )
    op.create_index(
        "ix_repository_connections_owner",
        "repository_connection_records",
        ["owner_ref"],
    )
    op.create_index(
        "ix_repository_connections_scope",
        "repository_connection_records",
        ["scope_type", "scope_ref"],
    )
    op.create_index(
        "ix_repository_connections_lifecycle",
        "repository_connection_records",
        ["lifecycle"],
    )

    op.create_table(
        "repository_connection_assignments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("connection_id", sa.String(length=255), nullable=False),
        sa.Column("endpoint_normalized", sa.String(length=1024), nullable=False),
        sa.Column("repo_key", sa.String(length=1024), nullable=False),
        sa.Column("provider_repo_id", sa.String(length=1024), nullable=True),
        sa.Column("canonical_remote", sa.String(length=1024), nullable=True),
        sa.Column("display_name", sa.String(length=2000), nullable=False),
        sa.Column("operations", sa.JSON(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "verified", sa.Boolean(), nullable=False, server_default=sa.text("true")
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
        sa.ForeignKeyConstraint(
            ["connection_id"],
            ["repository_connection_records.connection_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "connection_id",
            "endpoint_normalized",
            "repo_key",
            name="uq_repo_assignment_connection_repo",
        ),
    )
    op.create_index(
        "ix_repo_assignment_repo",
        "repository_connection_assignments",
        ["endpoint_normalized", "repo_key"],
    )
    op.create_index(
        "ix_repo_assignment_connection",
        "repository_connection_assignments",
        ["connection_id"],
    )

    op.create_table(
        "repository_route_defaults",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("scope_type", sa.String(length=16), nullable=False),
        sa.Column("scope_ref", sa.String(length=255), nullable=True),
        sa.Column("endpoint_normalized", sa.String(length=1024), nullable=False),
        sa.Column("repo_key", sa.String(length=1024), nullable=False),
        sa.Column("capability_bundle", sa.String(length=1024), nullable=False),
        sa.Column("connection_id", sa.String(length=255), nullable=False),
        sa.Column("policy_revision", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.String(length=256), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["connection_id"],
            ["repository_connection_records.connection_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "scope_type",
            "scope_ref",
            "endpoint_normalized",
            "repo_key",
            "capability_bundle",
            name="uq_repo_route_default",
        ),
    )
    op.create_index(
        "ix_repo_route_default_repo",
        "repository_route_defaults",
        ["endpoint_normalized", "repo_key"],
    )

    op.create_table(
        "repository_connection_audit_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.String(length=256), nullable=False),
        sa.Column("actor_ref", sa.String(length=255), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("connection_id", sa.String(length=255), nullable=False),
        sa.Column("scope_type", sa.String(length=16), nullable=False),
        sa.Column("scope_ref", sa.String(length=255), nullable=True),
        sa.Column("policy_revision", sa.Integer(), nullable=True),
        sa.Column("detail_json", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "request_id", "action", name="uq_repo_audit_request_action"
        ),
    )
    op.create_index(
        "ix_repo_audit_connection",
        "repository_connection_audit_events",
        ["connection_id"],
    )
    op.create_index(
        "ix_repo_audit_created",
        "repository_connection_audit_events",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_repo_audit_created", table_name="repository_connection_audit_events")
    op.drop_index(
        "ix_repo_audit_connection", table_name="repository_connection_audit_events"
    )
    op.drop_table("repository_connection_audit_events")
    op.drop_index("ix_repo_route_default_repo", table_name="repository_route_defaults")
    op.drop_table("repository_route_defaults")
    op.drop_index(
        "ix_repo_assignment_connection",
        table_name="repository_connection_assignments",
    )
    op.drop_index(
        "ix_repo_assignment_repo", table_name="repository_connection_assignments"
    )
    op.drop_table("repository_connection_assignments")
    op.drop_index(
        "ix_repository_connections_lifecycle",
        table_name="repository_connection_records",
    )
    op.drop_index(
        "ix_repository_connections_scope",
        table_name="repository_connection_records",
    )
    op.drop_index(
        "ix_repository_connections_owner",
        table_name="repository_connection_records",
    )
    op.drop_table("repository_connection_records")
