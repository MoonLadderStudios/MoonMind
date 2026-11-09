"""Retain continuing assessment occurrence reservations on canonical executions."""
import sqlalchemy as sa
from alembic import op

revision = "412_assessment_occurrence_authority"
down_revision = "411_assessment_job_authority"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("temporal_execution_sources", sa.Column("assessment_occurrence_json", sa.JSON(), nullable=True))


def downgrade():
    if op.get_bind().execute(sa.text(
        "SELECT 1 FROM temporal_execution_sources WHERE assessment_occurrence_json IS NOT NULL LIMIT 1"
    )).first():
        raise RuntimeError("Cannot discard continuing assessment reservations; repair forward.")
    op.drop_column("temporal_execution_sources", "assessment_occurrence_json")
