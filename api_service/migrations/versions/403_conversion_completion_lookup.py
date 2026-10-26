"""Index published production conversion evidence by schema revision.

Only candidate lookup changes; the converter still validates the full proof
and checks live pending resources before admitting a request.
"""

import sqlalchemy as sa
from alembic import op

revision = "403_conversion_completion_lookup"
down_revision = "401_artifact_recovery_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Freeze the installed predicate instead of importing mutable runtime policy.
    predicate = sa.text(
        "status = 'complete' "
        "AND CAST(result_json -> 'published' AS TEXT) = 'true' "
        "AND CAST(result_json -> 'production_transforms_completed' AS TEXT) = 'true'"
    )
    op.create_index(
        "ix_single_user_conversion_published_schema",
        "single_user_conversion_runs",
        [sa.text("(result_json ->> 'schema_revision')")],
        postgresql_where=predicate,
        sqlite_where=predicate,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_single_user_conversion_published_schema",
        table_name="single_user_conversion_runs",
    )
