"""Revision 398 stores instance settings without a synthetic subject.

Zero-subject workspace rows already were the instance configuration, so the
upgrade only changes their representation. Personal and other predecessor
rows keep their subject for the guarded conversion, and downgrade restores
the predecessor representation without losing values.
"""

from __future__ import annotations

import importlib
from unittest.mock import patch

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

MIGRATION = "api_service.migrations.versions.398_instance_settings"
ZERO = "00000000000000000000000000000000"
PERSON = "11111111111111111111111111111111"
OTHER_WORKSPACE = "22222222222222222222222222222222"

_PREDECESSOR_SCHEMA = (
    f"""
    CREATE TABLE settings_overrides (
        id CHAR(32) PRIMARY KEY,
        scope VARCHAR(32) NOT NULL,
        workspace_id CHAR(32) NOT NULL DEFAULT '{ZERO}',
        user_id CHAR(32) NOT NULL DEFAULT '{ZERO}',
        key VARCHAR(255) NOT NULL,
        value_json JSON,
        schema_version INTEGER NOT NULL DEFAULT 1,
        value_version INTEGER NOT NULL DEFAULT 1,
        CONSTRAINT ck_settings_overrides_scope CHECK (scope in ('user', 'workspace')),
        CONSTRAINT uq_settings_overrides_scope_subject_key
            UNIQUE (scope, workspace_id, user_id, key)
    )
    """,
    f"""
    CREATE TABLE settings_audit_events (
        id CHAR(32) PRIMARY KEY,
        event_type VARCHAR(64) NOT NULL,
        key VARCHAR(255) NOT NULL,
        scope VARCHAR(32) NOT NULL,
        workspace_id CHAR(32) NOT NULL DEFAULT '{ZERO}',
        user_id CHAR(32) NOT NULL DEFAULT '{ZERO}',
        actor_user_id CHAR(32)
    )
    """,
)


def _run(connection, step: str) -> None:
    migration = importlib.import_module(MIGRATION)
    with patch.object(
        migration, "op", Operations(MigrationContext.configure(connection))
    ):
        getattr(migration, step)()


def _overrides(connection):
    rows = connection.execute(
        sa.text(
            "SELECT key, scope, workspace_id, user_id, value_json "
            "FROM settings_overrides"
        )
    ).all()
    return sorted((tuple(row) for row in rows), key=lambda row: [str(v) for v in row])


def test_upgrade_converts_only_deployment_subject_rows_and_downgrade_restores(
    tmp_path,
) -> None:
    engine = sa.create_engine(f"sqlite:///{tmp_path}/settings.db")
    with engine.begin() as connection:
        for statement in _PREDECESSOR_SCHEMA:
            connection.execute(sa.text(statement))
        connection.execute(
            sa.text(
                "INSERT INTO settings_overrides "
                "(id, scope, workspace_id, user_id, key, value_json) VALUES "
                f"('a', 'workspace', '{ZERO}', '{ZERO}', 'shared', '\"instance\"'), "
                f"('b', 'user', '{ZERO}', '{PERSON}', 'shared', '\"personal\"'), "
                f"('c', 'workspace', '{OTHER_WORKSPACE}', '{ZERO}', 'shared', "
                "'\"context\"'), "
                f"('d', 'workspace', '{ZERO}', '{ZERO}', 'nulled', 'null')"
            )
        )
        connection.execute(
            sa.text(
                "INSERT INTO settings_audit_events "
                "(id, event_type, key, scope, workspace_id, user_id) VALUES "
                f"('e', 'settings.override.updated', 'shared', 'workspace', "
                f"'{ZERO}', '{ZERO}'), "
                f"('f', 'settings.override.updated', 'shared', 'user', "
                f"'{ZERO}', '{PERSON}'), "
                f"('g', 'settings.override.updated', 'shared', 'user', "
                f"'{ZERO}', '{ZERO}')"
            )
        )

    with engine.begin() as connection:
        _run(connection, "upgrade")
        assert _overrides(connection) == [
            ("nulled", None, None, None, "null"),
            ("shared", None, None, None, '"instance"'),
            ("shared", "user", ZERO, PERSON, '"personal"'),
            ("shared", "workspace", OTHER_WORKSPACE, ZERO, '"context"'),
        ]
        events = sorted(
            tuple(row)
            for row in connection.execute(
                sa.text(
                    "SELECT id, scope, workspace_id, user_id "
                    "FROM settings_audit_events"
                )
            ).all()
        )
        assert events == [
            ("e", "workspace", None, None),
            ("f", "user", ZERO, PERSON),
            # A persisted zero-UUID User is still a person.
            ("g", "user", ZERO, ZERO),
        ]
        # One instance row per key; the predecessor key still bounds legacy rows.
        connection.execute(
            sa.text(
                "INSERT INTO settings_overrides (id, scope, key, value_json) "
                "VALUES ('g', NULL, 'new', '\"one\"')"
            )
        )
        try:
            with connection.begin_nested():
                connection.execute(
                    sa.text(
                        "INSERT INTO settings_overrides (id, scope, key, value_json) "
                        "VALUES ('h', NULL, 'new', '\"two\"')"
                    )
                )
        except sa.exc.IntegrityError:
            pass
        else:  # pragma: no cover - assertion path
            raise AssertionError("duplicate instance key was accepted")

    with engine.begin() as connection:
        _run(connection, "downgrade")
        assert _overrides(connection) == [
            ("new", "workspace", ZERO, ZERO, '"one"'),
            ("nulled", "workspace", ZERO, ZERO, "null"),
            ("shared", "user", ZERO, PERSON, '"personal"'),
            ("shared", "workspace", ZERO, ZERO, '"instance"'),
            ("shared", "workspace", OTHER_WORKSPACE, ZERO, '"context"'),
        ]
        nullable = {
            column["name"]: column["nullable"]
            for column in sa.inspect(connection).get_columns("settings_overrides")
        }
        assert not any(nullable[name] for name in ("scope", "workspace_id", "user_id"))
    engine.dispose()
