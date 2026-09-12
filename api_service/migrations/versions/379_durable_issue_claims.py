"""Persist issue selection before its first external mutation."""

import sqlalchemy as sa
from alembic import op

revision = "379_durable_issue_claims"
down_revision = "378_saved_work_retention_4017"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "github_issue_claims",
        sa.Column("owner", sa.Text(), primary_key=True),
        sa.Column("repository", sa.Text(), nullable=False),
        sa.Column("issue_number", sa.Integer(), nullable=False),
        sa.Column("attempt_id", sa.String(64), nullable=False, unique=True),
        sa.Column("actor_id", sa.Text(), nullable=False),
        sa.Column("comment_body", sa.Text(), nullable=False),
        sa.Column("pending_comment_body", sa.Text()),
        sa.Column("finalization_json", sa.JSON()),
        sa.Column("comment_id", sa.Text()),
        sa.Column(
            "announcement_started",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("confirmed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("released", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.add_column(
        "omnigent_runtime_bindings", sa.Column("phase_results_json", sa.JSON())
    )
    op.create_index(
        "uq_github_issue_active_claim",
        "github_issue_claims",
        ["repository", "issue_number"],
        unique=True,
        postgresql_where=sa.text("released = false"),
        sqlite_where=sa.text("released = 0"),
    )


def downgrade():
    bindings = sa.table("omnigent_runtime_bindings", sa.column("phase_results_json"))
    if (
        op.get_bind()
        .execute(
            sa.select(sa.exists().where(bindings.c.phase_results_json.is_not(None)))
        )
        .scalar()
    ):
        raise RuntimeError(
            "Runtime phase receipts must drain before removing recovery authority"
        )
    table = sa.table("github_issue_claims", sa.column("owner"))
    if op.get_bind().execute(sa.select(sa.exists().select_from(table))).scalar():
        raise RuntimeError(
            "Retained issue claims must drain before removing their ownership receipts"
        )
    op.drop_table("github_issue_claims")
    op.drop_column("omnigent_runtime_bindings", "phase_results_json")
