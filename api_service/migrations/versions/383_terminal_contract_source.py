"""Allow Skill terminal-contract continuations without changing retained turns."""

import sqlalchemy as sa
from alembic import op

revision = "383_terminal_contract_source"
down_revision = "382_issue_claim_ownership_end"
branch_labels = None
depends_on = None

_TABLE = "omnigent_turn_attempts"
_CHECK = "ck_omnigent_turn_attempts_lineage_kind"
_OLD_SOURCES = (
    "initial",
    "repository_continuation",
    "remediation",
    "workflow_chat",
    "steering",
    "approval_response",
    "checkpoint_resume",
    "linked_branch",
)
_NEW_SOURCE = "terminal_contract_continuation"


def _replace_check(sources):
    op.drop_constraint(_CHECK, _TABLE, type_="check")
    values = ", ".join(f"'{source}'" for source in sources)
    op.create_check_constraint(_CHECK, _TABLE, f"lineage_kind IN ({values})")


def upgrade():
    _replace_check((*_OLD_SOURCES, _NEW_SOURCE))


def downgrade():
    if (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT 1 FROM omnigent_turn_attempts "
                "WHERE lineage_kind = 'terminal_contract_continuation' LIMIT 1"
            )
        )
        .first()
    ):
        raise RuntimeError(
            "Cannot remove terminal_contract_continuation while retained turns use it; "
            "keep the expanded constraint to preserve their recorded lineage."
        )
    _replace_check(_OLD_SOURCES)
