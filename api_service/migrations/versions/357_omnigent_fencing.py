"""Omnigent control-plane revisions and fencing enforcement.

Adds the durable state required to enforce optimistic concurrency and lease
fencing across the Omnigent control plane:

  * ``omnigent_commands.revision``     - own monotonic state_version so a
    claim/delivery/result transition is a revision-fenced compare-and-swap.
  * ``omnigent_commands.owner_class``  - low-cardinality owner identity class for
    at-most-once command execution (never a high-cardinality identity).
  * ``omnigent_commands.claim_token``  - per-claim fencing token binding delivery
    settlement to the exact winning claimant, so a racing loser that shares an
    ``owner_class`` cannot settle the command.
  * ``omnigent_cleanup_authority``     - durable, fenced cleanup/janitor authority
    so a former owner cannot stop or release resources that now belong to a
    replacement host / lease / provider-session generation. Its ``claim_token``
    binds completion to the exact winning janitor.

Legacy compatibility: existing ``omnigent_commands`` rows backfill to
``revision = 1`` and ``owner_class = NULL`` via the column server defaults, and
existing canonical sessions have no cleanup-authority row until one is claimed.
An absent cleanup-authority row means *unclaimed* (the fail-closed default), not
"universally current authority": nothing may complete cleanup it never claimed.

Source: MoonLadderStudios/MoonMind#3704 ([Omnigent control plane 3/11]).

Revision ID: 357_omnigent_fencing
Revises: 356_omnigent_ctrl_plane
Create Date: 2026-08-18

The revision id is kept <= 32 characters so it fits Alembic's
``alembic_version.version_num`` column.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "357_omnigent_fencing"
down_revision: Union[str, None] = "356_omnigent_ctrl_plane"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "omnigent_commands",
        sa.Column("owner_class", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "omnigent_commands",
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "omnigent_commands",
        sa.Column("claim_token", sa.String(length=255), nullable=True),
    )

    op.create_table(
        "omnigent_cleanup_authority",
        sa.Column(
            "session_id",
            sa.String(length=255),
            sa.ForeignKey("omnigent_sessions.session_id"),
            primary_key=True,
        ),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("generation", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("state", sa.String(length=32), nullable=False, server_default="unclaimed"),
        sa.Column("owner_class", sa.String(length=64), nullable=True),
        sa.Column("claim_token", sa.String(length=255), nullable=True),
        sa.Column("fenced_host_generation", sa.Integer(), nullable=True),
        sa.Column("fenced_profile_generation", sa.Integer(), nullable=True),
        sa.Column("fenced_provider_epoch", sa.String(length=255), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_omnigent_cleanup_authority_state",
        "omnigent_cleanup_authority",
        ["state"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_omnigent_cleanup_authority_state",
        table_name="omnigent_cleanup_authority",
    )
    op.drop_table("omnigent_cleanup_authority")

    op.drop_column("omnigent_commands", "claim_token")
    op.drop_column("omnigent_commands", "revision")
    op.drop_column("omnigent_commands", "owner_class")
