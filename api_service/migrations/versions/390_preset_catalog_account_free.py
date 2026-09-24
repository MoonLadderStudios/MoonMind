"""Drop account foreign keys from the preset catalog.

MoonLadderStudios/MoonMind#4350: fresh and converted single-user installs
serve a transient local operator without a ``user`` row (#4346). The preset
catalog still referenced ``user.id`` from its preference and provenance
columns, so expanding a preset (which records a recent), favoriting one, or
saving one failed with a foreign-key violation and surfaced as
``Unexpected preset error.`` in Create.

Favorites and recents are instance preferences and creator/reviewer are
provenance only (``docs/SingleUserApplicationDesign.md`` sections 5 and 9),
so their ``user`` foreign keys are dropped. Columns, values, uniqueness, and
the ``presets.id`` references are kept; no row is rewritten or removed.
Constraint names are discovered rather than assumed because historical
renames (migration 314) left deployment-specific names.

Revision ID: 390_preset_catalog_account_free
Revises: 389_opencode_validation_repair
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "390_preset_catalog_account_free"
down_revision: Union[str, None] = "389_opencode_validation_repair"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (table, column, ondelete restored by downgrade)
_ACCOUNT_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("presets", "created_by", "SET NULL"),
    ("presets", "reviewed_by", "SET NULL"),
    ("preset_favorites", "user_id", "CASCADE"),
    ("preset_recents", "user_id", "CASCADE"),
)

# Names reflected SQLite foreign keys (which are unnamed) so batch mode can
# drop them; PostgreSQL constraints keep their reflected names.
_NAMING_CONVENTION = {
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"
}


def _user_foreign_keys(inspector, table: str) -> dict[str, str]:
    """Map account column -> constraint name for FKs from ``table`` to ``user``."""
    found: dict[str, str] = {}
    for fk in inspector.get_foreign_keys(table):
        columns = fk.get("constrained_columns") or []
        if fk.get("referred_table") != "user" or len(columns) != 1:
            continue
        found[columns[0]] = fk.get("name") or _NAMING_CONVENTION["fk"] % {
            "table_name": table,
            "column_0_name": columns[0],
            "referred_table_name": "user",
        }
    return found


def _refuse_cascading_sqlite_rebuild(bind) -> None:
    # SQLite batch mode rebuilds each table; with foreign keys enforced,
    # dropping the old ``presets`` table cascades deletes into favorites and
    # recents. PostgreSQL drops the constraint in place and is unaffected.
    if (
        bind.dialect.name == "sqlite"
        and bind.exec_driver_sql("PRAGMA foreign_keys").scalar()
    ):
        raise RuntimeError(
            f"{revision} rebuilds SQLite tables; run it with foreign key "
            "enforcement off so the rebuild cannot cascade-delete preset rows."
        )


def upgrade() -> None:
    bind = op.get_bind()
    _refuse_cascading_sqlite_rebuild(bind)
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    for table in dict.fromkeys(table for table, _, _ in _ACCOUNT_COLUMNS):
        if table not in tables:
            continue
        wanted = {column for t, column, _ in _ACCOUNT_COLUMNS if t == table}
        drops = {
            column: name
            for column, name in _user_foreign_keys(inspector, table).items()
            if column in wanted
        }
        if not drops:
            continue
        with op.batch_alter_table(table, naming_convention=_NAMING_CONVENTION) as batch:
            for name in drops.values():
                batch.drop_constraint(name, type_="foreignkey")


def downgrade() -> None:
    bind = op.get_bind()
    _refuse_cascading_sqlite_rebuild(bind)
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "user" not in tables:
        return
    for table, column, _ in _ACCOUNT_COLUMNS:
        if table not in tables:
            continue
        orphans = bind.execute(
            sa.text(
                f"SELECT COUNT(*) FROM {table} WHERE {column} IS NOT NULL "
                f'AND {column} NOT IN (SELECT id FROM "user")'
            )
        ).scalar()
        if orphans:
            raise RuntimeError(
                f"Cannot downgrade {revision} (parent {down_revision}): "
                f"{table}.{column} holds {orphans} value(s) with no user row, "
                "such as the account-free local operator. Repair forward so "
                "no preset preference or provenance is discarded."
            )
    for table, column, ondelete in _ACCOUNT_COLUMNS:
        if table not in tables or column in _user_foreign_keys(inspector, table):
            continue
        with op.batch_alter_table(table, naming_convention=_NAMING_CONVENTION) as batch:
            batch.create_foreign_key(
                f"{table}_{column}_fkey",
                "user",
                [column],
                ["id"],
                ondelete=ondelete,
            )
