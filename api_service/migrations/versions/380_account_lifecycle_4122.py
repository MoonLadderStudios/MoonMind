"""K3 account-lifecycle nonce authority (single-use capabilities).

Revision ID: 380_account_lifecycle_4122
Revises: 379_durable_issue_claims
Create Date: 2026-09-13

Source issue: MoonLadderStudios/MoonMind#4122 (parent #4116; docs #4130).

Additive migration: creates ``moonmind_account_nonces`` (one row per
redeemed bootstrap/invite/recovery capability nonce). The primary-key
constraint is the single-use enforcement for the ``LifecycleStore``
protocol in ``moonmind/security/account_lifecycle_4122.py``: redemption
inserts the capability nonce in the same transaction as the account
change, so concurrent or replayed redemptions converge on the winner
instead of admitting a second account. No secret material is stored —
only the nonce, its purpose, and the bound login name.

No existing table, column, constraint, or historical revision is
altered; the ``user`` table, profile data, K3 identity tables, and all
ownership records stay untouched.

Rollback compatibility (bounded window): until the cutover is accepted
the previous application never reads this table, so it keeps starting
and serving against the upgraded schema. Downgrade refuses when rows
still carry unconsumed single-use authority, so rollback cannot silently
resurrect a redeemed capability.
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

revision: str = "380_account_lifecycle_4122"
down_revision: Union[str, None] = "379_durable_issue_claims"


def upgrade() -> None:
    op.create_table(
        "moonmind_account_nonces",
        sa.Column("nonce", sa.String(length=64), nullable=False),
        sa.Column("purpose", sa.String(length=16), nullable=False),
        sa.Column("login", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("nonce"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    remaining = bind.execute(sa.text("SELECT COUNT(*) FROM moonmind_account_nonces")).scalar()
    if remaining:
        raise RuntimeError(
            f"Cannot downgrade account lifecycle {revision} (parent "
            f"{down_revision}): moonmind_account_nonces still holds "
            f"{remaining} redeemed capability record(s). Reconcile or repair "
            "forward so no redeemed single-use capability is resurrected."
        )
    op.drop_table("moonmind_account_nonces")
