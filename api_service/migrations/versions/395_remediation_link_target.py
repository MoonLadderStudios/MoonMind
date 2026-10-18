"""Let a remediation target any visible execution, not only API-created ones.

Temporal Schedules start ``MoonMind.UserWorkflow`` runs directly, so those runs
have a Temporal-authoritative projection row in ``temporal_executions`` but no
API source row in ``temporal_execution_sources``. The remediation link's
target foreign key pointed at the source table, which made every
schedule-started failure impossible to remediate. Every source row has a
projection row, so the target key now references the projection.

The constraint is added ``NOT VALID`` in both directions: existing rows are
kept as they are and new rows are checked. Downgrade keeps links that target
schedule-started runs rather than deleting remediation history.

Revision ID: 395_remediation_link_target
Revises: 394_default_conn_issues_grant
"""

from __future__ import annotations

from typing import Union

from alembic import op

revision: str = "395_remediation_link_target"
down_revision: Union[str, None] = "394_default_conn_issues_grant"
branch_labels = None
depends_on = None

_TABLE = "execution_remediation_links"
_CONSTRAINT = "execution_remediation_links_target_workflow_id_fkey"


def _retarget(referent: str) -> None:
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="foreignkey")
    op.create_foreign_key(
        _CONSTRAINT,
        _TABLE,
        referent,
        ["target_workflow_id"],
        ["workflow_id"],
        ondelete="CASCADE",
        postgresql_not_valid=True,
    )


def upgrade() -> None:
    _retarget("temporal_executions")


def downgrade() -> None:
    _retarget("temporal_execution_sources")
