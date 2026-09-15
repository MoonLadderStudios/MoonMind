"""Separate reservation ownership from terminal bookkeeping.

An attempt whose label or comment bookkeeping cannot complete must still be
able to stop reserving its issue. ``ownership_ended`` records that retirement
and joins the partial unique index so a successor can reserve the same issue
while the predecessor's cleanup retries.
"""

import sqlalchemy as sa
from alembic import op

revision = "382_issue_claim_ownership_end"
down_revision = "381_manifest_drain_audit_4191"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "github_issue_claims",
        sa.Column(
            "ownership_ended", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.add_column(
        "github_issue_claims", sa.Column("ownership_ended_reason", sa.Text())
    )
    op.drop_index("uq_github_issue_active_claim", table_name="github_issue_claims")
    op.create_index(
        "uq_github_issue_active_claim",
        "github_issue_claims",
        ["repository", "issue_number"],
        unique=True,
        postgresql_where=sa.text("released = false and ownership_ended = false"),
        sqlite_where=sa.text("released = 0 and ownership_ended = 0"),
    )


def downgrade():
    op.drop_index("uq_github_issue_active_claim", table_name="github_issue_claims")
    claims = sa.table(
        "github_issue_claims",
        sa.column("released"),
        sa.column("ownership_ended"),
    )
    # A retired reservation whose bookkeeping never completed has no
    # representation in the old schema; releasing it preserves the invariant
    # that at most one live claim exists per issue.
    op.execute(
        claims.update()
        .where(claims.c.ownership_ended.is_(True))
        .values(released=True)
    )
    op.drop_column("github_issue_claims", "ownership_ended_reason")
    op.drop_column("github_issue_claims", "ownership_ended")
    op.create_index(
        "uq_github_issue_active_claim",
        "github_issue_claims",
        ["repository", "issue_number"],
        unique=True,
        postgresql_where=sa.text("released = false"),
        sqlite_where=sa.text("released = 0"),
    )
