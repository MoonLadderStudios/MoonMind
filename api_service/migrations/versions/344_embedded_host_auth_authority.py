"""Bind embedded host authentication authority to the durable host lease."""

from alembic import op
import sqlalchemy as sa

revision = "344_embedded_host_auth_authority"
down_revision = "343_embedded_host_lifecycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "omnigent_oauth_host_leases",
        sa.Column("host_auth_profile_id", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "omnigent_oauth_host_leases",
        sa.Column("host_auth_generation", sa.Integer(), nullable=True),
    )
    op.create_check_constraint(
        "ck_omnigent_oauth_host_lease_host_auth_generation",
        "omnigent_oauth_host_leases",
        "host_auth_generation IS NULL OR host_auth_generation >= 1",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_omnigent_oauth_host_lease_host_auth_generation",
        "omnigent_oauth_host_leases",
        type_="check",
    )
    op.drop_column("omnigent_oauth_host_leases", "host_auth_generation")
    op.drop_column("omnigent_oauth_host_leases", "host_auth_profile_id")
