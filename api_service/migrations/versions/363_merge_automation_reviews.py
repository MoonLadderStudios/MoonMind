"""Durable side-effect ledger for automated review requests.

Merge automation posts one automated-review request per head SHA. GitHub issue
comment creation has no native idempotency key, so a lost response after a
successful POST is indistinguishable from "never posted". This ledger binds one
logical request identity to at most one posted comment and records when the
attempt started, so an activity retry reconciles against comments created after
that instant instead of posting a duplicate request.

Revision ID: 363_merge_automation_review_requests
Revises: 362_omnigent_catalog_reobserve
Create Date: 2026-08-25
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "363_merge_automation_reviews"
down_revision: Union[str, None] = "362_omnigent_catalog_reobserve"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "merge_automation_review_requests",
        sa.Column("request_key", sa.String(length=128), primary_key=True),
        sa.Column("parent_workflow_id", sa.String(length=255), nullable=False),
        sa.Column("repository", sa.String(length=255), nullable=False),
        sa.Column("pr_number", sa.Integer(), nullable=False),
        sa.Column("head_sha", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("command", sa.String(length=255), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("request_comment_id", sa.BigInteger(), nullable=True),
        sa.Column("request_comment_url", sa.String(length=1024), nullable=True),
        sa.Column("actor", sa.String(length=255), nullable=True),
        sa.Column(
            "reconciled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("failure_reason", sa.String(length=500), nullable=True),
        sa.Column(
            "attempt_started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_merge_automation_review_requests_pr",
        "merge_automation_review_requests",
        ["repository", "pr_number"],
    )
    op.create_index(
        "ix_merge_automation_review_requests_parent",
        "merge_automation_review_requests",
        ["parent_workflow_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_merge_automation_review_requests_parent",
        table_name="merge_automation_review_requests",
    )
    op.drop_index(
        "ix_merge_automation_review_requests_pr",
        table_name="merge_automation_review_requests",
    )
    op.drop_table("merge_automation_review_requests")
