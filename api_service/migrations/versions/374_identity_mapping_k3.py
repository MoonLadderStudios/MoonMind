"""K3 explicit identity mapping tables (single active authority).

Revision ID: 374_identity_mapping_k3
Revises: 373_lease_identity_text
Create Date: 2026-09-08

Source issue: MoonLadderStudios/MoonMind#4119 (parent #4116; plan K3 and
sections 3/6 of docs/tmp/KeycloakRemovalPlan.md).

Additive migration: creates ``user_external_identities`` (the single active
``(issuer, subject) -> user.id`` authority) and ``identity_migration_runs``
(the idempotent apply ledger). No existing table, column, constraint, or
historical revision is altered: the legacy ``user.oidc_provider`` /
``user.oidc_subject`` columns and their ``uq_oidc_identity`` pair constraint
stay in place as frozen read-only evidence, every retained ``User.id`` and
foreign-key owner is untouched, and no data is backfilled here (backfill runs
through the reviewed operator dry-run/apply command, never as an automatic
side effect of upgrading).

Rollback compatibility (bounded window): until the cutover is accepted, the
last known-good application reads only the legacy columns it already knows,
which this revision leaves byte-identical, so it keeps starting and serving
against the upgraded schema. Downgrade drops only the two new tables and
refuses when they still hold migrated authority, so rollback cannot silently
strand a resolver that depends on them; it never touches ``user`` rows,
profiles, workflow/artifact/schedule owners, or shared PostgreSQL/Temporal
data.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "374_identity_mapping_k3"
down_revision: Union[str, None] = "373_lease_identity_text"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_external_identities",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("issuer", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("issuer", "subject", name="uq_user_external_identity"),
    )
    op.create_index(
        "ix_user_external_identities_user",
        "user_external_identities",
        ["user_id"],
    )
    op.create_table(
        "identity_migration_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("preflight_digest", sa.String(length=128), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="in_progress",
        ),
        sa.Column(
            "result_json",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "preflight_digest", name="uq_identity_migration_runs_digest"
        ),
    )


def downgrade() -> None:
    bind = op.get_bind()
    remaining = bind.execute(sa.text("SELECT COUNT(*) FROM user_external_identities")).scalar()
    if remaining:
        raise RuntimeError(
            "Cannot downgrade K3 identity mapping: user_external_identities still "
            f"holds {remaining} migrated identit(ies). Keep this schema until the "
            "cutover is accepted; rollback must restore a matching application "
            "set and intentionally invalidate incompatible sessions instead of "
            "stranding the active resolver."
        )
    op.drop_table("identity_migration_runs")
    op.drop_index(
        "ix_user_external_identities_user", table_name="user_external_identities"
    )
    op.drop_table("user_external_identities")
