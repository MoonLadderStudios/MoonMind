"""K4 durable session/revocation tables (single authority).

Revision ID: 375_session_authority_4121
Revises: 374_identity_mapping_k3
Create Date: 2026-09-09

Source issue: MoonLadderStudios/MoonMind#4121 (parent #4116; plan session and
browser security, K2/K4 in docs/tmp/KeycloakRemovalPlan.md).

Additive migration: creates ``moonmind_sessions`` (one row per issued
session JWT: jti, user, mint generation, expiry, revocation) and
``moonmind_user_session_generations`` (per-user revocation generation for
logout/reset/disable/admin-revoke/key-rotation). No existing table, column,
constraint, or historical revision is altered; the legacy ``user.oidc_*``
columns, K3 identity tables, and all ownership records stay untouched.

Rollback compatibility (bounded window): until the cutover is accepted the
previous application never reads these tables, so it keeps starting and
serving against the upgraded schema. Downgrade refuses when rows still carry
live session authority, so rollback cannot silently strand or resurrect a
revoked session.
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op

# Alembic discovers migration identity from these module attributes (it
# reads ``branch_labels``/``depends_on`` via getattr with None defaults, so
# only the non-null chain links are declared here); ``__all__`` keeps that
# external contract explicit for static analysis.
__all__ = ["revision", "down_revision", "upgrade", "downgrade"]

revision: str = "375_session_authority_4121"
down_revision: Union[str, None] = "374_identity_mapping_k3"


def upgrade() -> None:
    op.create_table(
        "moonmind_user_session_generations",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "generation",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_table(
        "moonmind_sessions",
        sa.Column("jti", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_reason", sa.String(length=32), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("jti"),
    )
    op.create_index(
        "ix_moonmind_sessions_user", "moonmind_sessions", ["user_id"]
    )
    op.create_index(
        "ix_moonmind_sessions_expires", "moonmind_sessions", ["expires_at"]
    )


def downgrade() -> None:
    bind = op.get_bind()
    # Only unexpired, unrevoked rows carry live session authority. Expired
    # rows linger until explicit logout/revocation cleanup, so the gate
    # must ignore them or the "let them expire before rollback" path can
    # never succeed once such a row exists.
    live_sessions = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM moonmind_sessions "
            "WHERE revoked_at IS NULL AND expires_at > CURRENT_TIMESTAMP"
        )
    ).scalar()
    if live_sessions:
        raise RuntimeError(
            f"Cannot downgrade session authority {revision} (parent "
            f"{down_revision}): moonmind_sessions still holds "
            f"{live_sessions} unrevoked, unexpired session(s). Revoke or let "
            "them expire before rollback so no live authority is stranded."
        )
    remaining_generations = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM moonmind_user_session_generations WHERE generation <> 0"
        )
    ).scalar()
    if remaining_generations:
        raise RuntimeError(
            f"Cannot downgrade session authority {revision} (parent "
            f"{down_revision}): {remaining_generations} user generation "
            "row(s) carry revocation state. Rollback must restore a matching "
            "application set and intentionally invalidate incompatible "
            "sessions."
        )
    op.drop_index("ix_moonmind_sessions_expires", table_name="moonmind_sessions")
    op.drop_index("ix_moonmind_sessions_user", table_name="moonmind_sessions")
    op.drop_table("moonmind_sessions")
    op.drop_table("moonmind_user_session_generations")
