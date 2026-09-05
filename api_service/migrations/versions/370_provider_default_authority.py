"""Record explicit operator ownership of provider-profile runtime defaults.

MoonLadderStudios/MoonMind#3877 makes ``opencode-zen-free`` hold runtime-default
authority for the ``opencode`` runtime unless an operator explicitly disables it
or explicitly selects another profile. Before this revision the persisted row
carried no way to tell those two states apart: an operator ``make_default`` and
the automatic transfer that deployment ``OPENCODE_API_KEY`` enrollment used to
perform both left the same ``is_default = true`` and nothing else.

This revision adds ``default_selected_by_operator`` so the two are distinct
going forward, and backfills it to ``false`` for every existing row. The
backfill is the controlled cutover: pre-change deployments recorded no explicit
selection, so startup seeding reclaims the ``opencode`` runtime default for the
credentialless Zen profile on the next restart. An operator who wants another
``opencode`` profile to hold the default re-selects it once after the upgrade,
and that selection is then durable.

Revision ID: 370_provider_default_authority
Revises: 369_provider_lease_incr_contract
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "370_provider_default_authority"
down_revision: Union[str, None] = "369_provider_lease_incr_contract"
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

_TABLE = "managed_agent_provider_profiles"
_COLUMN = "default_selected_by_operator"


def upgrade() -> None:
    op.add_column(
        _TABLE,
        sa.Column(
            _COLUMN,
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column(_TABLE, _COLUMN)
