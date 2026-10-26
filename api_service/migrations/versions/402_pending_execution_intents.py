"""Index pending delivery intents without scanning retained execution history."""

import sqlalchemy as sa
from alembic import op

revision = "402_pending_execution_intents"
down_revision = "401_artifact_recovery_indexes"
branch_labels = None
depends_on = None

# Freeze the predicate installed by this revision, independently of future
# model changes. This is also the discovery predicate in this release.
_PENDING = (
    "((close_status IS NULL "
    "AND state NOT IN ('no_commit', 'completed', 'failed', 'canceled') "
    "AND (memo -> 'temporalStart' ->> 'state') = 'pending') "
    "OR (memo -> 'temporalCancel' ->> 'state') = 'pending')"
)


def upgrade() -> None:
    op.create_index(
        "ix_temporal_execution_pending_intents",
        "temporal_execution_sources",
        ["namespace", "updated_at", "workflow_id"],
        postgresql_where=sa.text(_PENDING),
        sqlite_where=sa.text(_PENDING),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_temporal_execution_pending_intents",
        table_name="temporal_execution_sources",
    )
