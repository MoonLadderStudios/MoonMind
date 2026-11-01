"""Retain project item identity and pending phase effects across host loss."""

from alembic import op
import sqlalchemy as sa

revision = "410_github_project_claims"
down_revision = "409_static_host_allocations"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "github_issue_claims",
        sa.Column("project_binding_json", sa.JSON(), nullable=True),
    )


def downgrade():
    if (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT 1 FROM github_issue_claims WHERE project_binding_json IS NOT NULL LIMIT 1"
            )
        )
        .first()
    ):
        raise RuntimeError(
            "Cannot discard retained project synchronization receipts; repair forward."
        )
    op.drop_column("github_issue_claims", "project_binding_json")
