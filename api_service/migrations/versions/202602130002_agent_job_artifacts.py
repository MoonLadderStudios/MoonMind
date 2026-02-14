"""Add agent queue artifact metadata table."""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "202602130002"
down_revision: Union[str, None] = "202602130001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create metadata table for queue job artifacts."""

    op.create_table(
        "agent_job_artifacts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=512), nullable=False),
        sa.Column("content_type", sa.String(length=255), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("digest", sa.String(length=255), nullable=True),
        sa.Column("storage_path", sa.String(length=1024), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["agent_jobs.id"],
            name="fk_agent_job_artifacts_job_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_agent_job_artifacts"),
    )

    op.create_index(
        "ix_agent_job_artifacts_job_id_created_at",
        "agent_job_artifacts",
        ["job_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_agent_job_artifacts_job_id_name",
        "agent_job_artifacts",
        ["job_id", "name"],
        unique=False,
    )


def downgrade() -> None:
    """Drop metadata table for queue job artifacts."""

    op.drop_index(
        "ix_agent_job_artifacts_job_id_name", table_name="agent_job_artifacts"
    )
    op.drop_index(
        "ix_agent_job_artifacts_job_id_created_at",
        table_name="agent_job_artifacts",
    )
    op.drop_table("agent_job_artifacts")
