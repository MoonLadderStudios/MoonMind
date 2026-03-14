"""Add Codex path columns to spec workflow runs."""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "1b4d0f8a3c0a"
down_revision: Union[str, None] = "add_workflow_tables"  # noqa
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_context().bind
    inspector = sa.inspect(bind)
    columns = [c["name"] for c in inspector.get_columns("workflow_runs")]

    if "codex_logs_path" not in columns:
        op.add_column(
            "workflow_runs",
            sa.Column("codex_logs_path", sa.String(length=1024), nullable=True),
        )
    if "codex_patch_path" not in columns:
        op.add_column(
            "workflow_runs",
            sa.Column("codex_patch_path", sa.String(length=1024), nullable=True),
        )


def downgrade() -> None:
    op.drop_column("workflow_runs", "codex_patch_path")
    op.drop_column("workflow_runs", "codex_logs_path")
