"""Admit account-free instance catalog rows without exposing legacy data.

Nullable legacy ownership columns distinguish unconverted retained rows from
instance rows. Schema installation rewrites no data: the shared guarded
conversion owns reconciliation after attribution and writer exclusion.
"""

import sqlalchemy as sa
from alembic import op

revision = "397_instance_preset_catalog"
down_revision = "396_artifact_operator_recovery"
branch_labels = None
depends_on = None

_COLUMNS = (
    ("presets", "scope_type"),
    ("preset_favorites", "user_id"),
    ("preset_recents", "user_id"),
)
_INDEXES = (
    ("presets", "uq_presets_instance_slug", "slug", "scope_type"),
    ("preset_favorites", "uq_preset_favorite_instance", "template_id", "user_id"),
    ("preset_recents", "uq_preset_recent_instance", "template_id", "user_id"),
)


def _guard_sqlite_rebuild(bind):
    if (
        bind.dialect.name == "sqlite"
        and bind.exec_driver_sql("PRAGMA foreign_keys").scalar()
    ):
        raise RuntimeError(
            "Preset schema rebuild requires SQLite foreign key enforcement off; "
            "otherwise rebuilding presets can cascade-delete preferences."
        )


def upgrade():
    bind = op.get_bind()
    _guard_sqlite_rebuild(bind)
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    for table, column in _COLUMNS:
        if table not in tables:
            continue
        existing = next(c for c in inspector.get_columns(table) if c["name"] == column)
        with op.batch_alter_table(table) as batch:
            batch.alter_column(column, existing_type=existing["type"], nullable=True)
    for table, name, column, legacy in _INDEXES:
        if table in tables:
            op.create_index(
                name,
                table,
                [column],
                unique=True,
                postgresql_where=sa.text(f"{legacy} IS NULL"),
                sqlite_where=sa.text(f"{legacy} IS NULL"),
            )


def downgrade():
    bind = op.get_bind()
    _guard_sqlite_rebuild(bind)
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if bind.dialect.name == "postgresql":
        present = [table for table, _ in _COLUMNS if table in tables]
        if present:
            bind.execute(
                sa.text(
                    "LOCK TABLE " + ", ".join(present) + " IN ACCESS EXCLUSIVE MODE"
                )
            )
    for table, column in _COLUMNS:
        if (
            table in tables
            and bind.execute(
                sa.text(f"SELECT COUNT(*) FROM {table} WHERE {column} IS NULL")
            ).scalar()
        ):
            raise RuntimeError(
                "Cannot downgrade instance presets/preferences into person ownership; "
                "repair forward without discarding retained data."
            )
    for table, name, _column, _legacy in _INDEXES:
        if table in tables:
            op.drop_index(name, table_name=table)
    for table, column in _COLUMNS:
        if table not in tables:
            continue
        existing = next(c for c in inspector.get_columns(table) if c["name"] == column)
        with op.batch_alter_table(table) as batch:
            batch.alter_column(column, existing_type=existing["type"], nullable=False)
