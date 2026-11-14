"""Retain settings reset generations without pinning inherited values."""

import sqlalchemy as sa
from alembic import op

revision = "414_settings_generations"
down_revision = "413_assignment_permissions"
branch_labels = None
depends_on = None


def upgrade():
    # Existing rows, including explicit nulls and retained subjects, remain
    # active overrides with their original values, versions and provenance.
    op.add_column(
        "settings_overrides",
        sa.Column("is_reset", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade():
    if (
        op.get_bind()
        .execute(
            sa.text("SELECT 1 FROM settings_overrides WHERE is_reset = true LIMIT 1")
        )
        .first()
    ):
        raise RuntimeError(
            "Cannot discard retained settings generations; repair forward. "
            "Older applications cannot interpret reset rows."
        )
    op.drop_column("settings_overrides", "is_reset")
