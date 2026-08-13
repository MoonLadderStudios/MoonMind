"""Persist one canonical chat authority across continuation attempts.

MoonLadderStudios/MoonMind#3685.

Revision ID: 355_omnigent_chat_authority
Revises: 354_workflow_linked_continuation
"""

from alembic import op
import sqlalchemy as sa

revision = "355_omnigent_chat_authority"
down_revision = "354_workflow_linked_continuation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "omnigent_bridge_sessions",
        sa.Column("canonical_bridge_session_id", sa.String(255), nullable=True),
    )
    op.add_column(
        "omnigent_bridge_sessions",
        sa.Column("canonical_provider_session_key", sa.String(128), nullable=True),
    )
    op.create_foreign_key(
        "fk_omnigent_bridge_sessions_canonical",
        "omnigent_bridge_sessions",
        "omnigent_bridge_sessions",
        ["canonical_bridge_session_id"],
        ["bridge_session_id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "uq_omnigent_bridge_sessions_canonical_provider_session",
        "omnigent_bridge_sessions",
        ["canonical_provider_session_key"],
        unique=True,
    )
    op.create_index(
        "ix_omnigent_bridge_sessions_canonical",
        "omnigent_bridge_sessions",
        ["canonical_bridge_session_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_omnigent_bridge_sessions_canonical",
        table_name="omnigent_bridge_sessions",
    )
    op.drop_index(
        "uq_omnigent_bridge_sessions_canonical_provider_session",
        table_name="omnigent_bridge_sessions",
    )
    op.drop_constraint(
        "fk_omnigent_bridge_sessions_canonical",
        "omnigent_bridge_sessions",
        type_="foreignkey",
    )
    op.drop_column("omnigent_bridge_sessions", "canonical_provider_session_key")
    op.drop_column("omnigent_bridge_sessions", "canonical_bridge_session_id")
