"""Retain assessment admission, readable review and original job deadline."""

from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision = "411_assessment_job_authority"
down_revision = "410_github_project_claims"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "container_jobs", sa.Column("assessment_scope_json", sa.JSON(), nullable=True)
    )
    op.add_column(
        "container_jobs", sa.Column("assessment_review_json", sa.JSON(), nullable=True)
    )
    op.add_column(
        "container_jobs",
        sa.Column("assessment_deadline_at", sa.DateTime(timezone=True), nullable=True),
    )
    # This empty document grants no authority; legacy continuations use it as
    # the same transaction lock as later reviewed authorization reservations.
    table = sa.table(
        "settings_overrides",
        sa.column("id", sa.Uuid()),
        sa.column("key"),
        sa.column("scope"),
        sa.column("value_json", sa.JSON()),
        sa.column("schema_version"),
        sa.column("value_version"),
    )
    connection = op.get_bind()
    exists = connection.execute(
        sa.select(table.c.id).where(
            table.c.key == "security.assessment_authorizations", table.c.scope.is_(None)
        )
    ).first()
    if not exists:
        connection.execute(
            table.insert().values(
                id=uuid4(),
                key="security.assessment_authorizations",
                scope=None,
                value_json={"authorizations": []},
                schema_version=1,
                value_version=1,
            )
        )


def downgrade():
    if (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT 1 FROM container_jobs WHERE assessment_scope_json IS NOT NULL "
                "OR assessment_review_json IS NOT NULL LIMIT 1"
            )
        )
        .first()
    ):
        raise RuntimeError(
            "Cannot discard retained assessment authority; repair forward."
        )
    op.drop_column("container_jobs", "assessment_deadline_at")
    op.drop_column("container_jobs", "assessment_review_json")
    op.drop_column("container_jobs", "assessment_scope_json")
