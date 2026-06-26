"""add remediation context artifact ref

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2026-04-21 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d0e1f2a3b4c5"
down_revision: Union[str, None] = "c9d0e1f2a3b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = [
    "revision",
    "down_revision",
    "branch_labels",
    "depends_on",
    "upgrade",
    "downgrade",
]

def upgrade() -> None:
    op.add_column(
        "execution_remediation_links",
        sa.Column("context_artifact_ref", sa.String(length=64), nullable=True),
    )
    op.create_foreign_key(
        "fk_execution_remediation_links_context_artifact_ref",
        "execution_remediation_links",
        "temporal_artifacts",
        ["context_artifact_ref"],
        ["artifact_id"],
        ondelete="SET NULL",
    )

def downgrade() -> None:
    op.drop_constraint(
        "fk_execution_remediation_links_context_artifact_ref",
        "execution_remediation_links",
        type_="foreignkey",
    )
    op.drop_column("execution_remediation_links", "context_artifact_ref")
