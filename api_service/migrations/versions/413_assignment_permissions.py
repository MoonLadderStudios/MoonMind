"""Repository assignments inherit their connection's allowed operations.

Membership, identity and revisions are retained. Downgrade materializes the
connection's current permissions; retired per-repository overrides are
intentionally not reconstructed.
"""

import sqlalchemy as sa
from alembic import op

revision = "413_assignment_permissions"
down_revision = "412_assessment_occurrences"
branch_labels = None
depends_on = None

_TABLE = "repository_connection_assignments"


def upgrade():
    with op.batch_alter_table(_TABLE) as batch:
        batch.drop_column("operations")


def downgrade():
    op.add_column(_TABLE, sa.Column("operations", sa.JSON(), nullable=True))
    assignments = sa.table(
        _TABLE,
        sa.column("connection_id", sa.String()),
        sa.column("operations", sa.JSON()),
    )
    records = sa.table(
        "repository_connection_records",
        sa.column("connection_id", sa.String()),
        sa.column("allowed_operations", sa.JSON()),
    )
    op.get_bind().execute(
        assignments.update().values(
            operations=sa.select(records.c.allowed_operations)
            .where(records.c.connection_id == assignments.c.connection_id)
            .scalar_subquery()
        )
    )
    with op.batch_alter_table(_TABLE) as batch:
        batch.alter_column("operations", existing_type=sa.JSON(), nullable=False)
