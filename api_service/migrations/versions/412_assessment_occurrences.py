"""Retain continuing assessment occurrence reservations independently of schedules."""

import sqlalchemy as sa
from alembic import op

revision = "412_assessment_occurrences"
down_revision = "411_assessment_job_authority"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "assessment_occurrences",
        sa.Column("workflow_id", sa.String(255), primary_key=True),
        sa.Column("assessment_ref", sa.String(128), nullable=True),
        sa.Column("authority_json", sa.JSON(), nullable=False),
    )
    op.create_index(
        "ix_assessment_occurrences_assessment_ref",
        "assessment_occurrences",
        ["assessment_ref"],
    )


def downgrade():
    if (
        op.get_bind()
        .execute(sa.text("SELECT 1 FROM assessment_occurrences LIMIT 1"))
        .first()
    ):
        raise RuntimeError(
            "Cannot discard continuing assessment reservations; repair forward."
        )
    op.drop_table("assessment_occurrences")
