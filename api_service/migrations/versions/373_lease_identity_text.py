"""Preserve composed lease identities without a storage-only length cap.

An AgentRun remediation operation can already produce a 268-character
idempotency key. Widen the related opaque identities together, without changing
the values that in-flight workflows use for retry, inspection and release.
"""

import sqlalchemy as sa
from alembic import op

revision = "373_lease_identity_text"
down_revision = "372_machine_reservations"
branch_labels = None
depends_on = None

_TABLE = "provider_profile_slot_leases"
_COLUMNS = (
    "workflow_id",
    "lease_id",
    "owner_id",
    "step_execution_id",
    "idempotency_key",
)


def upgrade() -> None:
    with op.batch_alter_table(_TABLE) as batch:
        for column in _COLUMNS:
            batch.alter_column(
                column,
                existing_type=sa.String(255),
                type_=sa.Text(),
                existing_nullable=column != "workflow_id",
            )


def downgrade() -> None:
    # Never silently truncate an identity that a live workflow still quotes.
    # Check every column before issuing any DDL, including on SQLite.
    table = sa.table(_TABLE, *(sa.column(name, sa.Text()) for name in _COLUMNS))
    oversized = sa.or_(*(sa.func.length(table.c[name]) > 255 for name in _COLUMNS))
    if op.get_bind().execute(sa.select(sa.exists().where(oversized))).scalar():
        raise RuntimeError(
            "Cannot downgrade lease identities to VARCHAR(255): retained rows "
            "contain longer identities. Keep this schema until those leases "
            "have completed and their retention period has expired."
        )
    with op.batch_alter_table(_TABLE) as batch:
        for column in _COLUMNS:
            batch.alter_column(
                column,
                existing_type=sa.Text(),
                type_=sa.String(255),
                existing_nullable=column != "workflow_id",
            )
