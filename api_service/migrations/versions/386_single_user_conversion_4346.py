"""Guarded single-user conversion ledger (#4346).

Revision ID: 386_single_user_conversion_4346
Revises: 385_runtime_issuance
Create Date: 2026-09-20

Source issue: MoonLadderStudios/MoonMind#4346 (parent #4345;
design docs/SingleUserApplicationDesign.md sections 9-10).

Additive migration: creates ``single_user_conversion_runs`` (one row per
preflight digest for the guarded single-user upgrade-eligibility and
data-conversion entrypoint in
``api_service/services/single_user_conversion.py``). The digest unique
constraint is the mutual-exclusion and idempotency enforcement: a rerun
with the same digest replays the recorded result instead of duplicating
work, and a concurrent apply against an ``in_progress`` row fails closed.
``result_json`` carries only sanitized dispositions (counts, reason
codes, redacted identifier prefixes) — never credentials or resource
content.

No existing table, column, constraint, or historical revision is
altered; user rows, ownership records, secrets, and all existing
migration state stay untouched. The destructive conversion itself runs
only through the guarded entrypoint, never as an automatic side effect
of this schema upgrade.

Rollback compatibility (bounded window): until a conversion is accepted
the previous application never reads this table, so it keeps starting
and serving against the upgraded schema. Downgrade refuses when rows
still carry conversion progress, so rollback cannot silently discard a
recorded conversion outcome.
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op

__all__ = ["revision", "down_revision", "upgrade", "downgrade"]

revision: str = "386_single_user_conversion_4346"
down_revision: Union[str, None] = "385_runtime_issuance"


def upgrade() -> None:
    op.create_table(
        "single_user_conversion_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("preflight_digest", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "preflight_digest", name="uq_single_user_conversion_runs_digest"
        ),
    )


def downgrade() -> None:
    bind = op.get_bind()
    remaining = bind.execute(
        sa.text("SELECT COUNT(*) FROM single_user_conversion_runs")
    ).scalar()
    if remaining:
        raise RuntimeError(
            f"Cannot downgrade single-user conversion {revision} (parent "
            f"{down_revision}): single_user_conversion_runs still holds "
            f"{remaining} conversion record(s). Reconcile or repair "
            "forward so no recorded conversion outcome is discarded."
        )
    op.drop_table("single_user_conversion_runs")
