"""Rename legacy task template catalog tables to presets.

Revision ID: 314_preset_catalog_table_rename
Revises: 313_finish_summary_fields
Create Date: 2026-06-11
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "314_preset_catalog_table_rename"
down_revision: Union[str, None] = "313_finish_summary_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_LEGACY_PRESET_ROOT = "task_step" + "_templates"


def _table_exists(connection: sa.Connection, table_name: str) -> bool:
    return bool(
        connection.execute(
            sa.text(
                """
                select exists (
                    select 1
                    from information_schema.tables
                    where table_schema = current_schema()
                      and table_name = :table_name
                )
                """
            ),
            {"table_name": table_name},
        ).scalar()
    )


def _rename_table_if_needed(
    connection: sa.Connection,
    *,
    old_name: str,
    new_name: str,
) -> None:
    if _table_exists(connection, new_name):
        return
    if _table_exists(connection, old_name):
        op.rename_table(old_name, new_name)


def _rename_relation_if_exists(
    connection: sa.Connection,
    *,
    old_name: str,
    new_name: str,
) -> None:
    exists = connection.execute(
        sa.text("select to_regclass(:old_name) is not null"),
        {"old_name": old_name},
    ).scalar()
    new_exists = connection.execute(
        sa.text("select to_regclass(:new_name) is not null"),
        {"new_name": new_name},
    ).scalar()
    if exists and not new_exists:
        op.execute(
            sa.text(f'alter index "{old_name}" rename to "{new_name}"')
        )


def _rename_constraint_if_exists(
    connection: sa.Connection,
    *,
    table_name: str,
    old_name: str,
    new_name: str,
) -> None:
    exists = connection.execute(
        sa.text(
            """
            select exists (
                select 1
                from pg_constraint
                where conrelid = to_regclass(:table_name)
                  and conname = :old_name
            )
            """
        ),
        {"table_name": table_name, "old_name": old_name},
    ).scalar()
    new_exists = connection.execute(
        sa.text(
            """
            select exists (
                select 1
                from pg_constraint
                where conrelid = to_regclass(:table_name)
                  and conname = :new_name
            )
            """
        ),
        {"table_name": table_name, "new_name": new_name},
    ).scalar()
    if exists and not new_exists:
        op.execute(
            sa.text(
                f'alter table "{table_name}" rename constraint '
                f'"{old_name}" to "{new_name}"'
            )
        )


def _rename_type_if_needed(
    connection: sa.Connection,
    *,
    old_name: str,
    new_name: str,
) -> None:
    exists = connection.execute(
        sa.text("select to_regtype(:old_name) is not null"),
        {"old_name": old_name},
    ).scalar()
    new_exists = connection.execute(
        sa.text("select to_regtype(:new_name) is not null"),
        {"new_name": new_name},
    ).scalar()
    if exists and not new_exists:
        op.execute(sa.text(f'alter type "{old_name}" rename to "{new_name}"'))


def _rename_sequence_if_needed(
    connection: sa.Connection,
    *,
    old_name: str,
    new_name: str,
) -> None:
    exists = connection.execute(
        sa.text("select to_regclass(:old_name) is not null"),
        {"old_name": old_name},
    ).scalar()
    new_exists = connection.execute(
        sa.text("select to_regclass(:new_name) is not null"),
        {"new_name": new_name},
    ).scalar()
    if exists and not new_exists:
        op.execute(sa.text(f'alter sequence "{old_name}" rename to "{new_name}"'))


def _upgrade_postgresql_catalog_names(connection: sa.Connection) -> None:
    _rename_type_if_needed(
        connection,
        old_name="tasktemplatescopetype",
        new_name="presetscopetype",
    )
    _rename_type_if_needed(
        connection,
        old_name="tasktemplatereleasestatus",
        new_name="presetreleasestatus",
    )

    _rename_table_if_needed(
        connection,
        old_name=_LEGACY_PRESET_ROOT,
        new_name="presets",
    )
    _rename_table_if_needed(
        connection,
        old_name="task_step_template_versions",
        new_name="preset_versions",
    )
    _rename_table_if_needed(
        connection,
        old_name="task_step_template_favorites",
        new_name="preset_favorites",
    )
    _rename_table_if_needed(
        connection,
        old_name="task_step_template_recents",
        new_name="preset_recents",
    )

    for old_name, new_name in (
        (f"ix_{_LEGACY_PRESET_ROOT}_active", "ix_presets_active"),
        (f"ix_{_LEGACY_PRESET_ROOT}_scope", "ix_presets_scope"),
        (f"ix_{_LEGACY_PRESET_ROOT}_slug", "ix_presets_slug"),
        ("ix_task_step_template_versions_template", "ix_preset_versions_template"),
        ("ix_task_step_template_favorites_user", "ix_preset_favorites_user"),
        ("ix_task_step_template_recents_user", "ix_preset_recents_user"),
    ):
        _rename_relation_if_exists(connection, old_name=old_name, new_name=new_name)

    for old_name, new_name in (
        ("task_step_template_favorites_id_seq", "preset_favorites_id_seq"),
        ("task_step_template_recents_id_seq", "preset_recents_id_seq"),
    ):
        _rename_sequence_if_needed(connection, old_name=old_name, new_name=new_name)

    constraint_renames = {
        "presets": (
            (f"{_LEGACY_PRESET_ROOT}_pkey", "presets_pkey"),
            ("uq_task_step_template_slug_scope", "uq_preset_slug_scope"),
            (
                f"{_LEGACY_PRESET_ROOT}_created_by_fkey",
                "presets_created_by_fkey",
            ),
        ),
        "preset_versions": (
            ("task_step_template_versions_pkey", "preset_versions_pkey"),
            ("uq_task_step_template_version_label", "uq_preset_version_label"),
            (
                "task_step_template_versions_template_id_fkey",
                "preset_versions_template_id_fkey",
            ),
            (
                "task_step_template_versions_reviewed_by_fkey",
                "preset_versions_reviewed_by_fkey",
            ),
        ),
        "preset_favorites": (
            ("task_step_template_favorites_pkey", "preset_favorites_pkey"),
            ("uq_task_template_favorite", "uq_preset_favorite"),
            (
                "task_step_template_favorites_template_id_fkey",
                "preset_favorites_template_id_fkey",
            ),
            (
                "task_step_template_favorites_user_id_fkey",
                "preset_favorites_user_id_fkey",
            ),
        ),
        "preset_recents": (
            ("task_step_template_recents_pkey", "preset_recents_pkey"),
            ("uq_task_template_recent_user_version", "uq_preset_recent_user_version"),
            (
                "task_step_template_recents_template_version_id_fkey",
                "preset_recents_template_version_id_fkey",
            ),
            (
                "task_step_template_recents_user_id_fkey",
                "preset_recents_user_id_fkey",
            ),
        ),
    }
    for table_name, renames in constraint_renames.items():
        if not _table_exists(connection, table_name):
            continue
        for old_name, new_name in renames:
            _rename_constraint_if_exists(
                connection,
                table_name=table_name,
                old_name=old_name,
                new_name=new_name,
            )

    if _table_exists(connection, "presets") and _table_exists(
        connection, "preset_versions"
    ):
        _rename_constraint_if_exists(
            connection,
            table_name="presets",
            old_name=f"{_LEGACY_PRESET_ROOT}_latest_version_id_fkey",
            new_name="fk_preset_latest_version",
        )
        latest_fk_exists = connection.execute(
            sa.text(
                """
                select exists (
                    select 1
                    from pg_constraint
                    where conrelid = 'presets'::regclass
                      and conname = 'fk_preset_latest_version'
                )
                """
            )
        ).scalar()
        if not latest_fk_exists:
            op.create_foreign_key(
                "fk_preset_latest_version",
                "presets",
                "preset_versions",
                ["latest_version_id"],
                ["id"],
                ondelete="SET NULL",
            )

    op.execute(
        """
        alter table if exists preset_favorites
        alter column id set default nextval('preset_favorites_id_seq'::regclass)
        """
    )
    op.execute(
        """
        alter table if exists preset_recents
        alter column id set default nextval('preset_recents_id_seq'::regclass)
        """
    )


def upgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name != "postgresql":
        return
    _upgrade_postgresql_catalog_names(connection)


def downgrade() -> None:
    raise NotImplementedError(
        "Downgrading preset catalog table rename is not supported."
    )
