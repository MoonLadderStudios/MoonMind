"""Retain scoped operator access to eligible account-era container jobs."""

import sqlalchemy as sa
from alembic import op

revision = "400_job_operator_access"
down_revision = "399_retire_shared_accounts"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "container_jobs",
        sa.Column(
            "operator_access_granted",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade():
    op.drop_column("container_jobs", "operator_access_granted")
