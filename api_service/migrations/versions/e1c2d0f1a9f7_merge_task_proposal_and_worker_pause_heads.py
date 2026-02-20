"""Merge task proposal and worker pause migration branches."""

from typing import Sequence, Union

revision: str = "e1c2d0f1a9f7"
down_revision: Union[str, Sequence[str], None] = (
    "202602200001",
    "202602200002",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Merge migration branch heads for head resolution."""


def downgrade() -> None:
    """No-op merge rollback is not supported for this migration."""
