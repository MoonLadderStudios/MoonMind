"""Persist embedded host lifecycle evidence on profile host leases."""

from alembic import op
import sqlalchemy as sa

revision = "343_embedded_host_lifecycle"
down_revision = "342_omnigent_event_journal"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "omnigent_oauth_host_leases",
        sa.Column("host_capabilities_json", sa.JSON(), nullable=False, server_default="{}"),
    )
    op.add_column(
        "omnigent_oauth_host_leases",
        sa.Column("host_readiness", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "omnigent_oauth_host_leases",
        sa.Column("disconnected_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("omnigent_oauth_host_leases", "disconnected_at")
    op.drop_column("omnigent_oauth_host_leases", "host_readiness")
    op.drop_column("omnigent_oauth_host_leases", "host_capabilities_json")
