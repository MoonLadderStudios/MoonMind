"""Store instance settings without a synthetic subject.

Rows and audit events saved through operator admission used the zero-UUID
workspace subject. They already described the instance configuration, so this
revision only changes their representation to null subject columns. Personal and other predecessor
rows keep their columns: reconciling them needs attribution and writer
exclusion, which the shared guarded conversion owns.
"""

from uuid import UUID

import sqlalchemy as sa
from alembic import op

revision = "398_instance_settings"
down_revision = "397_instance_preset_catalog"
branch_labels = None
depends_on = None

_ZERO = UUID("00000000-0000-0000-0000-000000000000")
_TABLES = ("settings_overrides", "settings_audit_events")
_SUBJECT_COLUMNS = ("workspace_id", "user_id")


def _rows(name: str):
    return sa.table(
        name,
        sa.column("scope", sa.String(32)),
        sa.column("workspace_id", sa.Uuid()),
        sa.column("user_id", sa.Uuid()),
    )


def _alter(table: str, *, nullable: bool, server_default) -> None:
    with op.batch_alter_table(table) as batch:
        batch.alter_column(
            "scope", existing_type=sa.String(32), nullable=nullable
        )
        for column in _SUBJECT_COLUMNS:
            batch.alter_column(
                column,
                existing_type=sa.Uuid(),
                nullable=nullable,
                server_default=server_default,
            )


def upgrade():
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    for table in _TABLES:
        if table in tables:
            _alter(table, nullable=True, server_default=None)
    if "settings_audit_events" in tables:
        # Deployment-subject events already described the instance. Their
        # scope label stays as history; personal events keep their subject.
        events = _rows("settings_audit_events")
        op.execute(
            events.update()
            .where(events.c.workspace_id == _ZERO, events.c.user_id == _ZERO)
            .values(workspace_id=None, user_id=None)
        )
    if "settings_overrides" not in tables:
        return
    op.create_index(
        "uq_settings_overrides_instance_key",
        "settings_overrides",
        ["key"],
        unique=True,
        postgresql_where=sa.text("scope IS NULL"),
        sqlite_where=sa.text("scope IS NULL"),
    )
    rows = _rows("settings_overrides")
    op.execute(
        rows.update()
        .where(
            rows.c.scope == "workspace",
            rows.c.workspace_id == _ZERO,
            rows.c.user_id == _ZERO,
        )
        .values(scope=None, workspace_id=None, user_id=None)
    )


def downgrade():
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    # Instance rows are the same configuration the predecessor stored under
    # the deployment subject, so the representation change is reversible.
    for table in _TABLES:
        if table not in tables:
            continue
        rows = _rows(table)
        op.execute(
            rows.update().where(rows.c.scope.is_(None)).values(scope="workspace")
        )
        for column in _SUBJECT_COLUMNS:
            op.execute(
                rows.update()
                .where(rows.c[column].is_(None))
                .values({column: _ZERO})
            )
    if "settings_overrides" in tables:
        op.drop_index(
            "uq_settings_overrides_instance_key", table_name="settings_overrides"
        )
    for table in _TABLES:
        if table in tables:
            _alter(
                table,
                nullable=False,
                server_default=sa.text(f"'{_ZERO}'"),
            )
