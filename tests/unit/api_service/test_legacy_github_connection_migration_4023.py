"""Migration coverage for retiring legacy GitHub credential discovery.

MoonLadderStudios/MoonMind#4023: one idempotent data revision maps the
effective legacy GitHub credential reference into the recorded
``repository-connection:git-default`` connection. It decides from configured
references only (no token values, no GitHub calls), never overwrites newer
connection writes, reconciles a lost commit acknowledgment through the
existing audit identity, and reports unknowable choices without blocking
startup.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
import uuid
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from api_service.db.models import Base

MIGRATION = "api_service.migrations.versions.391_legacy_github_cred_4023"
DEFAULT_REF = "repository-connection:git-default"
TOKEN_VALUE = "ghp_" + "S3cr3tTokenValueThatMustNeverLeak0123"
_TABLES = (
    "managed_secrets",
    "repository_connection_records",
    "repository_connection_assignments",
    "repository_route_defaults",
    "repository_connection_audit_events",
)


@pytest.fixture
def migration():
    return importlib.import_module(MIGRATION)


@pytest.fixture
def engine(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path}/migration.db")
    Base.metadata.create_all(
        engine, tables=[Base.metadata.tables[name] for name in _TABLES]
    )
    yield engine
    engine.dispose()


def _add_managed_secret(engine, slug: str, status: str = "active") -> None:
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO managed_secrets (id, slug, ciphertext, status, details) "
                "VALUES (:id, :slug, :ciphertext, :status, '{}')"
            ),
            {
                "id": uuid.uuid4().hex,
                "slug": slug,
                "ciphertext": "opaque-ciphertext",
                "status": status,
            },
        )


def _migrate(engine, migration, environ: dict[str, str]):
    with engine.begin() as connection:
        return migration.migrate_legacy_github_connection(connection, environ)


def _rows(engine, table: str) -> list[dict]:
    with engine.connect() as connection:
        return [
            dict(row._mapping)
            for row in connection.execute(sa.text(f"SELECT * FROM {table}"))
        ]


def _credential(row: dict) -> dict:
    value = row["credential_config"]
    return json.loads(value) if isinstance(value, str) else value


def _secret_ref(row: dict) -> str:
    ref = _credential(row)["credentialRef"]
    return f"{ref['provider']}://{ref['key']}"


def test_populated_deployment_maps_the_effective_reference(engine, migration):
    _add_managed_secret(engine, "GITHUB_TOKEN")
    _add_managed_secret(engine, "GITHUB_PAT")

    result = _migrate(
        engine,
        migration,
        {
            "GITHUB_TOKEN": TOKEN_VALUE,
            "GH_TOKEN": "another-token",
            "GITHUB_TOKEN_SECRET_REF": "db://github-pat-main",
        },
    )

    records = _rows(engine, "repository_connection_records")
    audits = _rows(engine, "repository_connection_audit_events")
    assert result.outcome == "mapped"
    assert [row["connection_id"] for row in records] == [DEFAULT_REF]
    assert _secret_ref(records[0]) == "env://GITHUB_TOKEN"
    assert records[0]["scope_type"] == "system"
    assert records[0]["hosting_service"] == "github"
    allowed_operations = records[0]["allowed_operations"]
    if isinstance(allowed_operations, str):
        allowed_operations = json.loads(allowed_operations)
    assert "merge_request" in allowed_operations
    assert len(audits) == 1
    assert audits[0]["action"] == "connection.create"
    # No assignment or route default: an unknown legacy allowlist does not
    # become routed (wildcard) authority.
    assert _rows(engine, "repository_connection_assignments") == []
    assert _rows(engine, "repository_route_defaults") == []


def test_migrated_connection_is_readable_through_the_connection_owner(
    engine, migration, tmp_path
):
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    from api_service.services.repository_connections import (
        RepositoryConnectionService,
    )

    _migrate(engine, migration, {"WORKFLOW_GITHUB_TOKEN_SECRET_REF": "db://github-pat"})

    async def _read():
        async_engine = create_async_engine(
            f"sqlite+aiosqlite:///{tmp_path}/migration.db"
        )
        try:
            async with AsyncSession(async_engine) as session:
                return await RepositoryConnectionService(session).get_connection(
                    DEFAULT_REF,
                    principal_ref="system:managed-runtime-launch",
                    principal_scope=("system", None),
                )
        finally:
            await async_engine.dispose()

    connection = asyncio.run(_read())

    assert connection is not None
    assert connection.credential.source == "secret_ref"
    assert connection.credential.credential_ref.provider == "db"
    assert connection.credential.credential_ref.key == "github-pat"


@pytest.mark.parametrize(
    ("environ", "slugs", "expected"),
    [
        ({"GH_TOKEN": "x", "WORKFLOW_GITHUB_TOKEN": "y"}, (), "env://GH_TOKEN"),
        (
            {
                "WORKFLOW_GITHUB_TOKEN_SECRET_REF": "db://second",
                "MOONMIND_GITHUB_TOKEN_REF": "db://third",
            },
            ("GITHUB_TOKEN",),
            "db://second",
        ),
        ({"MOONMIND_GITHUB_TOKEN_REF": "PAT_ENV_NAME"}, (), "env://PAT_ENV_NAME"),
        ({}, (("GITHUB_TOKEN", "disabled"), "GITHUB_PAT"), "db://GITHUB_PAT"),
        ({"GITHUB_TOKEN": "   "}, ("GITHUB_TOKEN",), "db://GITHUB_TOKEN"),
    ],
)
def test_legacy_precedence_is_decided_from_references_only(
    engine, migration, environ, slugs, expected
):
    for slug in slugs:
        name, status = (slug, "active") if isinstance(slug, str) else slug
        _add_managed_secret(engine, name, status)

    result = _migrate(engine, migration, environ)

    assert result.outcome == "mapped"
    assert _secret_ref(_rows(engine, "repository_connection_records")[0]) == expected


def test_equal_values_in_distinct_sources_are_not_merged_or_compared(engine, migration):
    _add_managed_secret(engine, "GITHUB_PAT")

    result = _migrate(
        engine,
        migration,
        {"GITHUB_TOKEN": "same-value", "GH_TOKEN": "same-value"},
    )

    assert result.outcome == "mapped"
    assert result.credential_ref == "env://GITHUB_TOKEN"
    assert len(_rows(engine, "repository_connection_records")) == 1


def test_absent_configuration_records_nothing_and_does_not_block(engine, migration):
    result = _migrate(engine, migration, {"GITHUB_TOKEN": ""})

    assert result.outcome == "absent"
    assert _rows(engine, "repository_connection_records") == []
    assert _rows(engine, "repository_connection_audit_events") == []


@pytest.mark.parametrize(
    "configured",
    ["https://example.com/token", "db://bad key", TOKEN_VALUE, "env://" + TOKEN_VALUE],
)
def test_unknowable_reference_is_reported_without_a_mapping(
    engine, migration, configured, caplog
):
    _add_managed_secret(engine, "GITHUB_TOKEN")
    caplog.set_level(logging.WARNING)

    result = _migrate(engine, migration, {"GITHUB_TOKEN_SECRET_REF": configured})

    assert result.outcome == "unresolvable"
    assert "GITHUB_TOKEN_SECRET_REF" in result.diagnostic
    # The correction is one the operator can apply in this deployment: set
    # the variable to a supported reference.
    assert "env://" in result.diagnostic and "db://" in result.diagnostic
    assert TOKEN_VALUE not in result.diagnostic
    assert TOKEN_VALUE not in caplog.text
    # A configured-but-unusable source is not silently replaced by the next
    # source in the legacy order.
    assert _rows(engine, "repository_connection_records") == []


def test_rerun_converges_without_duplicate_mapping(engine, migration):
    environ = {"GITHUB_TOKEN": TOKEN_VALUE}

    first = _migrate(engine, migration, environ)
    second = _migrate(engine, migration, environ)

    assert (first.outcome, second.outcome) == ("mapped", "already_mapped")
    assert len(_rows(engine, "repository_connection_records")) == 1
    assert len(_rows(engine, "repository_connection_audit_events")) == 1


def test_lost_acknowledgment_is_reconciled_through_the_audit_identity(
    engine, migration
):
    environ = {"GITHUB_TOKEN": TOKEN_VALUE}
    _migrate(engine, migration, environ)
    with engine.begin() as connection:
        connection.execute(sa.text("DELETE FROM repository_connection_audit_events"))

    result = _migrate(engine, migration, environ)

    assert result.outcome == "already_mapped"
    assert len(_rows(engine, "repository_connection_records")) == 1
    assert len(_rows(engine, "repository_connection_audit_events")) == 1


def test_interrupted_run_after_downgrade_restores_the_same_mapping(engine, migration):
    environ = {"GITHUB_TOKEN": TOKEN_VALUE}
    _migrate(engine, migration, environ)
    with engine.begin() as connection:
        assert migration.remove_untouched_legacy_github_connection(connection)

    result = _migrate(engine, migration, environ)

    assert result.outcome == "mapped"
    assert len(_rows(engine, "repository_connection_records")) == 1
    assert len(_rows(engine, "repository_connection_audit_events")) == 1


def test_concurrent_operator_connection_is_never_overwritten(engine, migration):
    from api_service.db.models import RepositoryConnectionRecord

    with engine.begin() as connection:
        connection.execute(
            sa.insert(RepositoryConnectionRecord.__table__).values(
                connection_id=DEFAULT_REF,
                display_name="Operator connection",
                provider="git",
                hosting_service="github",
                endpoint_normalized="https://github.com",
                endpoint_ref="https://github.com",
                allowed_operations=["read"],
                client_policy={},
                credential_config={
                    "source": "secret_ref",
                    "credentialRef": {
                        "provider": "db",
                        "key": "operator-pat",
                        "extra": {},
                    },
                },
                policy_revision=2,
                credential_revision=2,
                owner_ref="system:operator",
                scope_type="system",
                allowed_principal_refs=[],
            )
        )

    result = _migrate(engine, migration, {"GITHUB_TOKEN": TOKEN_VALUE})

    records = _rows(engine, "repository_connection_records")
    assert result.outcome == "kept_existing"
    assert len(records) == 1
    assert _secret_ref(records[0]) == "db://operator-pat"
    assert records[0]["policy_revision"] == 2
    assert _rows(engine, "repository_connection_audit_events") == []


def test_secret_rotation_does_not_remap_the_reference(engine, migration):
    _add_managed_secret(engine, "GITHUB_TOKEN")
    _migrate(engine, migration, {})
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "UPDATE managed_secrets SET credential_revision = 2 "
                "WHERE slug = 'GITHUB_TOKEN'"
            )
        )
    _add_managed_secret(engine, "GITHUB_PAT")

    result = _migrate(engine, migration, {})

    assert result.outcome == "already_mapped"
    assert _secret_ref(_rows(engine, "repository_connection_records")[0]) == (
        "db://GITHUB_TOKEN"
    )


def test_diagnostics_and_audit_carry_only_safe_references(engine, migration, caplog):
    caplog.set_level(logging.INFO)

    result = _migrate(engine, migration, {"GITHUB_TOKEN": TOKEN_VALUE})

    audit = _rows(engine, "repository_connection_audit_events")[0]
    detail = audit["detail_json"]
    rendered = json.dumps(detail) if not isinstance(detail, str) else detail
    assert "env://GITHUB_TOKEN" in rendered
    assert TOKEN_VALUE not in rendered
    assert TOKEN_VALUE not in caplog.text
    assert TOKEN_VALUE not in repr(result)
    assert TOKEN_VALUE not in json.dumps(
        _rows(engine, "repository_connection_records")[0], default=str
    )


def test_downgrade_keeps_a_connection_with_newer_writes(engine, migration):
    _migrate(engine, migration, {"GITHUB_TOKEN": TOKEN_VALUE})
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "UPDATE repository_connection_records SET policy_revision = 2 "
                "WHERE connection_id = :ref"
            ),
            {"ref": DEFAULT_REF},
        )
        removed = migration.remove_untouched_legacy_github_connection(connection)

    assert removed is False
    assert len(_rows(engine, "repository_connection_records")) == 1


def test_alembic_upgrade_reads_the_deployment_environment(
    engine, migration, monkeypatch
):
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN_VALUE)
    for name in (
        "GH_TOKEN",
        "WORKFLOW_GITHUB_TOKEN",
        "GITHUB_TOKEN_SECRET_REF",
        "WORKFLOW_GITHUB_TOKEN_SECRET_REF",
        "MOONMIND_GITHUB_TOKEN_REF",
    ):
        monkeypatch.delenv(name, raising=False)

    with engine.begin() as connection, patch.object(
        migration, "op", Operations(MigrationContext.configure(connection))
    ):
        migration.upgrade()
        migration.upgrade()

    records = _rows(engine, "repository_connection_records")
    assert [_secret_ref(row) for row in records] == ["env://GITHUB_TOKEN"]
    with engine.begin() as connection, patch.object(
        migration, "op", Operations(MigrationContext.configure(connection))
    ):
        migration.downgrade()
    assert _rows(engine, "repository_connection_records") == []
