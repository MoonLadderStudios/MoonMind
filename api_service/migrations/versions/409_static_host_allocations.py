"""Keep allocation history while reserving one physical static host."""

from alembic import op
import sqlalchemy as sa

revision = "409_static_host_allocations"
down_revision = "408_execution_wait_reason_text"
branch_labels = None
depends_on = None

_TABLE = "omnigent_oauth_host_leases"
_ACTIVE = "status IN ('allocating','starting','ready','assigned','draining') OR (status = 'failed' AND cleanup_completed_at IS NULL)"


def upgrade() -> None:
    with op.batch_alter_table(_TABLE) as batch:
        batch.add_column(sa.Column("static_scope_ref", sa.String(128), nullable=True))
        batch.drop_constraint("uq_omnigent_oauth_host_provider_lease", type_="unique")
        batch.create_index(
            "ux_omnigent_oauth_host_lease_static_scope",
            ["static_scope_ref"],
            unique=True,
            sqlite_where=sa.text(_ACTIVE),
            postgresql_where=sa.text(_ACTIVE),
        )


def downgrade() -> None:
    duplicate = (
        op.get_bind()
        .execute(
            sa.text(
                f"SELECT provider_lease_id FROM {_TABLE} "
                "GROUP BY provider_lease_id HAVING COUNT(*) > 1 LIMIT 1"
            )
        )
        .first()
    )
    if duplicate is not None:
        raise RuntimeError(
            "Cannot restore one-row-per-grant storage without losing retained "
            "host allocations; keep the forward-compatible schema."
        )
    with op.batch_alter_table(_TABLE) as batch:
        batch.drop_index("ux_omnigent_oauth_host_lease_static_scope")
        batch.drop_column("static_scope_ref")
        batch.create_unique_constraint(
            "uq_omnigent_oauth_host_provider_lease", ["provider_lease_id"]
        )
