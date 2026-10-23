"""Detach historical resource attribution from application accounts.

Retain every value and resource reference. Account-era tables are archived only
by the existing guarded converter after its production transforms succeed; an
unresolved source remains intact and cannot expose product data.
"""

import sqlalchemy as sa
from alembic import op

revision = "399_retire_shared_accounts"
down_revision = "398_instance_settings"
branch_labels = None
depends_on = None

_ACCOUNT_TABLES = {
    "user",
    "user_profile",
    "user_external_identities",
    "moonmind_sessions",
    "moonmind_user_session_generations",
    "moonmind_account_nonces",
}
_NAMING = {"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"}


def upgrade():
    bind = op.get_bind()
    # Historical Alembic revisions can leave this connection in autocommit.
    # ALTER supports a bounded lock timeout in either transaction posture.
    bounded_default = False
    if bind.dialect.name == "postgresql":
        previous = bind.execute(
            sa.text("SELECT current_setting('lock_timeout')")
        ).scalar_one()
        bounded_default = previous == "0"
        if bounded_default:
            bind.execute(sa.text("SELECT set_config('lock_timeout', '2s', false)"))
    inspector = sa.inspect(bind)
    for table in inspector.get_table_names():
        if table in _ACCOUNT_TABLES or table.startswith("retired_"):
            continue
        foreign_keys = [
            fk
            for fk in inspector.get_foreign_keys(table)
            if fk["referred_table"] == "user"
        ]
        if not foreign_keys:
            continue
        if (
            bind.dialect.name == "sqlite"
            and bind.exec_driver_sql("PRAGMA foreign_keys").scalar()
        ):
            raise RuntimeError(
                "Account FK detachment requires SQLite foreign_keys=OFF to preserve child rows"
            )
        with op.batch_alter_table(table, naming_convention=_NAMING) as batch:
            for fk in foreign_keys:
                name = fk["name"] or f"fk_{table}_{fk['constrained_columns'][0]}_user"
                batch.drop_constraint(name, type_="foreignkey")
    if bounded_default:
        bind.execute(sa.text("SELECT set_config('lock_timeout', '0', false)"))


def downgrade():
    raise RuntimeError(
        "Repair forward after account retirement; restoring person constraints "
        "would invalidate supported account-free writes and retained archives."
    )
