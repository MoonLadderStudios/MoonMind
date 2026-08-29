"""Regression coverage for the provider profile model tiers migrations."""

from __future__ import annotations

import importlib
import json

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.schema import CreateColumn, CreateTable

from api_service.db.models import ManagedAgentProviderProfile
from moonmind.provider_profiles.model_tiers import (
    LEGACY_DEFAULT_TIER_MIGRATION_SOURCE,
    MODEL_TIER_MIGRATION_ANNOTATION,
    RUNTIME_DEFAULT_TIER_MIGRATION_SOURCE,
    coerce_model_effort_tier_policy,
    is_single_legacy_default_model_effort_tier,
    is_single_runtime_default_model_effort_tier,
)

_REVISION_335 = "api_service.migrations.versions.335_provider_profile_model_tiers"
_REVISION_365 = "api_service.migrations.versions.365_profile_tier_provenance"


class _RecordingOp:
    def __init__(self) -> None:
        self.columns = []

    def add_column(self, table_name, column) -> None:
        self.columns.append((table_name, column))

    def get_bind(self):
        return self

    def execute(self, _statement):
        return []

    def create_check_constraint(self, *_args, **_kwargs) -> None:
        return None


def _assert_valid_model_tiers_default(column) -> None:
    default_sql = str(
        column.server_default.arg.compile(
            dialect=postgresql.dialect(),
        )
    )
    assert default_sql.startswith("'") and default_sql.endswith("'")
    assert json.loads(default_sql[1:-1]) == [
        {
            "label": "Runtime default",
            "model": None,
            "effort": None,
            "parameters": {},
            "annotations": {},
        }
    ]

    ddl = str(CreateColumn(column).compile(dialect=postgresql.dialect()))
    assert '"model":null' in ddl
    assert '"effort":null' in ddl


def test_model_tiers_server_default_compiles_as_valid_postgresql_json(
    monkeypatch,
) -> None:
    migration = importlib.import_module(_REVISION_335)
    operations = _RecordingOp()
    monkeypatch.setattr(migration, "op", operations)

    migration.upgrade()

    model_tiers_column = next(
        column
        for table_name, column in operations.columns
        if table_name == "managed_agent_provider_profiles"
        and column.name == "model_tiers"
    )
    _assert_valid_model_tiers_default(model_tiers_column)


def test_model_tiers_model_default_compiles_as_valid_postgresql_json() -> None:
    _assert_valid_model_tiers_default(
        ManagedAgentProviderProfile.__table__.c.model_tiers
    )


class _SqliteMigrationOperations(Operations):
    """Real alembic operations for SQLite that record ALTER-ADD/DROP CHECK calls.

    SQLite has no ``ALTER TABLE ... ADD CONSTRAINT``, so the check constraints the
    migration creates for the PostgreSQL deployment backend are recorded instead of
    executed. Every schema change and the data backfill still run for real against
    the seeded database.
    """

    def __init__(self, context) -> None:
        super().__init__(context)
        self.created_check_constraints: list[tuple[str, str, str]] = []
        self.dropped_constraints: list[tuple[str, str, str]] = []

    def create_check_constraint(  # type: ignore[override]
        self, constraint_name, table_name, condition, **kw
    ) -> None:
        self.created_check_constraints.append(
            (constraint_name, table_name, str(condition))
        )

    def drop_constraint(  # type: ignore[override]
        self, constraint_name, table_name, type_=None, **kw
    ) -> None:
        self.dropped_constraints.append((constraint_name, table_name, str(type_)))


def _bind_migration(module_name, connection, monkeypatch):
    """Bind a revision module to real SQLite operations over ``connection``."""

    migration = importlib.import_module(module_name)
    operations = _SqliteMigrationOperations(MigrationContext.configure(connection))
    monkeypatch.setattr(migration, "op", operations)
    return migration, operations


_SEEDED_PROFILES = (
    ("legacy_model_and_effort", "gpt-custom", "xhigh"),
    ("legacy_model_only", "gpt-custom", None),
    ("legacy_effort_only", None, "low"),
    ("no_legacy_defaults", None, None),
)


def _expected_migration_source(default_model, default_effort) -> str:
    if default_model is not None or default_effort is not None:
        return LEGACY_DEFAULT_TIER_MIGRATION_SOURCE
    return RUNTIME_DEFAULT_TIER_MIGRATION_SOURCE


def _seed_pre_migration_profiles(connection) -> None:
    connection.execute(
        sa.text(
            "CREATE TABLE managed_agent_provider_profiles ("
            "profile_id VARCHAR(128) NOT NULL PRIMARY KEY, "
            "default_model VARCHAR(255), "
            "default_effort VARCHAR(64)"
            ")"
        )
    )
    for profile_id, default_model, default_effort in _SEEDED_PROFILES:
        connection.execute(
            sa.text(
                "INSERT INTO managed_agent_provider_profiles "
                "(profile_id, default_model, default_effort) "
                "VALUES (:profile_id, :default_model, :default_effort)"
            ),
            {
                "profile_id": profile_id,
                "default_model": default_model,
                "default_effort": default_effort,
            },
        )


def _read_migrated_profiles(connection) -> dict[str, dict[str, object]]:
    rows = connection.execute(
        sa.text(
            "SELECT profile_id, default_model, default_effort, model_tiers, "
            "default_model_tier FROM managed_agent_provider_profiles"
        )
    ).all()
    return {
        row.profile_id: {
            "default_model": row.default_model,
            "default_effort": row.default_effort,
            "model_tiers": json.loads(row.model_tiers),
            "default_model_tier": row.default_model_tier,
        }
        for row in rows
    }


def _write_model_tiers(connection, profile_id, tiers) -> None:
    connection.execute(
        sa.text(
            "UPDATE managed_agent_provider_profiles SET model_tiers = :tiers "
            "WHERE profile_id = :profile_id"
        ),
        {"profile_id": profile_id, "tiers": json.dumps(tiers)},
    )


def test_seeded_profiles_backfill_to_one_annotated_tier(monkeypatch) -> None:
    """MoonLadderStudios/MoonMind#3793: run both revisions against seeded profiles."""

    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        _seed_pre_migration_profiles(connection)

        revision_335, _ = _bind_migration(_REVISION_335, connection, monkeypatch)
        revision_335.upgrade()
        revision_365, _ = _bind_migration(_REVISION_365, connection, monkeypatch)
        revision_365.upgrade()

        migrated = _read_migrated_profiles(connection)

    assert set(migrated) == {profile_id for profile_id, _, _ in _SEEDED_PROFILES}

    for profile_id, default_model, default_effort in _SEEDED_PROFILES:
        profile = migrated[profile_id]
        # default_model/default_effort stay populated compatibility mirrors, so the
        # pre-migration effective model/effort is preserved.
        assert profile["default_model"] == default_model
        assert profile["default_effort"] == default_effort
        assert profile["default_model_tier"] == 1

        if default_model is not None or default_effort is not None:
            assert profile["model_tiers"] == [
                {
                    "label": "Legacy default",
                    "model": default_model,
                    "effort": default_effort,
                    "parameters": {},
                    "annotations": {
                        "migratedFrom": "default_model_default_effort",
                    },
                }
            ]
            # The stamped provenance must not hide the tier from the legacy-default
            # refresh path in api_service/api/routers/provider_profiles.py.
            assert is_single_legacy_default_model_effort_tier(
                profile["model_tiers"],
                legacy_default_model=default_model,
                legacy_default_effort=default_effort,
            )
        else:
            assert profile["model_tiers"] == [
                {
                    "label": "Runtime default",
                    "model": None,
                    "effort": None,
                    "parameters": {},
                    "annotations": {"migratedFrom": "runtime_default"},
                }
            ]
            assert is_single_runtime_default_model_effort_tier(profile["model_tiers"])

        # No profile is left in a state that fails tier policy validation.
        model_tiers, default_model_tier = coerce_model_effort_tier_policy(
            model_tiers=profile["model_tiers"],
            default_model_tier=profile["default_model_tier"],
            legacy_default_model=default_model,
            legacy_default_effort=default_effort,
            empty_as_missing=True,
        )
        assert model_tiers == profile["model_tiers"]
        assert default_model_tier == 1


def test_revision_365_stamps_databases_already_migrated_by_335(monkeypatch) -> None:
    """Databases stamped at 335 or a descendant never re-run 335, so 365 carries
    the provenance and the array check forward for them."""

    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        _seed_pre_migration_profiles(connection)
        revision_335, _ = _bind_migration(_REVISION_335, connection, monkeypatch)
        revision_335.upgrade()

        already_migrated = _read_migrated_profiles(connection)
        # This is the durable state of every deployment stamped at 335..364.
        assert all(
            profile["model_tiers"][0]["annotations"] == {}
            for profile in already_migrated.values()
        )

        revision_365, operations = _bind_migration(
            _REVISION_365, connection, monkeypatch
        )
        revision_365.upgrade()

        stamped = _read_migrated_profiles(connection)

    assert operations.created_check_constraints == [
        (
            "ck_provider_profiles_model_tiers_array",
            "managed_agent_provider_profiles",
            "jsonb_typeof(model_tiers) = 'array' "
            "AND jsonb_array_length(model_tiers) >= 1",
        )
    ]

    for profile_id, default_model, default_effort in _SEEDED_PROFILES:
        before = already_migrated[profile_id]["model_tiers"][0]
        after = stamped[profile_id]["model_tiers"][0]
        assert after["annotations"] == {
            MODEL_TIER_MIGRATION_ANNOTATION: _expected_migration_source(
                default_model, default_effort
            )
        }
        # Only the provenance changes; the rest of the migrated tier is preserved.
        assert {key: value for key, value in after.items() if key != "annotations"} == {
            key: value for key, value in before.items() if key != "annotations"
        }


def test_revision_365_preserves_operator_authored_tiers(monkeypatch) -> None:
    """Only the exact un-annotated shape revision 335 wrote is restamped."""

    multi_tier = [
        {
            "label": "Legacy default",
            "model": "gpt-custom",
            "effort": "xhigh",
            "parameters": {},
            "annotations": {},
        },
        {
            "label": "Deep",
            "model": "gpt-deep",
            "effort": "high",
            "parameters": {},
            "annotations": {},
        },
    ]
    relabelled_tier = [
        {
            "label": "Operator default",
            "model": "gpt-custom",
            "effort": "xhigh",
            "parameters": {},
            "annotations": {},
        }
    ]
    annotated_tier = [
        {
            "label": "Runtime default",
            "model": None,
            "effort": None,
            "parameters": {},
            "annotations": {"owner": "platform"},
        }
    ]

    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        _seed_pre_migration_profiles(connection)
        revision_335, _ = _bind_migration(_REVISION_335, connection, monkeypatch)
        revision_335.upgrade()

        _write_model_tiers(connection, "legacy_model_and_effort", multi_tier)
        _write_model_tiers(connection, "legacy_model_only", relabelled_tier)
        _write_model_tiers(connection, "legacy_effort_only", annotated_tier)

        revision_365, _ = _bind_migration(_REVISION_365, connection, monkeypatch)
        revision_365.upgrade()

        after = _read_migrated_profiles(connection)

    assert after["legacy_model_and_effort"]["model_tiers"] == multi_tier
    assert after["legacy_model_only"]["model_tiers"] == relabelled_tier
    assert after["legacy_effort_only"]["model_tiers"] == annotated_tier
    # The untouched profile still carries the 335 shape, so 365 stamps it.
    assert after["no_legacy_defaults"]["model_tiers"][0]["annotations"] == {
        MODEL_TIER_MIGRATION_ANNOTATION: RUNTIME_DEFAULT_TIER_MIGRATION_SOURCE
    }


def test_revision_335_creates_only_the_default_tier_check(monkeypatch) -> None:
    """MoonLadderStudios/MoonMind#3793: the applied revision keeps its own checks."""

    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        _seed_pre_migration_profiles(connection)
        revision_335, operations = _bind_migration(
            _REVISION_335, connection, monkeypatch
        )

        revision_335.upgrade()

        assert operations.created_check_constraints == [
            (
                "ck_provider_profiles_default_model_tier_positive",
                "managed_agent_provider_profiles",
                "default_model_tier >= 1",
            ),
        ]

        revision_335.downgrade()

        # Downgrading a database that only ever applied 335 must not try to drop a
        # constraint that revision never created.
        assert operations.dropped_constraints == [
            (
                "ck_provider_profiles_default_model_tier_positive",
                "managed_agent_provider_profiles",
                "check",
            ),
        ]
        remaining = {
            column["name"]
            for column in sa.inspect(connection).get_columns(
                "managed_agent_provider_profiles"
            )
        }
        assert not ({"model_tiers", "default_model_tier"} & remaining)


def test_revision_365_creates_and_drops_the_model_tiers_array_check(
    monkeypatch,
) -> None:
    """MoonLadderStudios/MoonMind#3793: the forward revision owns the array check."""

    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        _seed_pre_migration_profiles(connection)
        revision_335, _ = _bind_migration(_REVISION_335, connection, monkeypatch)
        revision_335.upgrade()
        revision_365, operations = _bind_migration(
            _REVISION_365, connection, monkeypatch
        )

        revision_365.upgrade()

        assert operations.created_check_constraints == [
            (
                "ck_provider_profiles_model_tiers_array",
                "managed_agent_provider_profiles",
                "jsonb_typeof(model_tiers) = 'array' "
                "AND jsonb_array_length(model_tiers) >= 1",
            ),
        ]

        revision_365.downgrade()

        assert operations.dropped_constraints == [
            (
                "ck_provider_profiles_model_tiers_array",
                "managed_agent_provider_profiles",
                "check",
            ),
        ]
        reverted = _read_migrated_profiles(connection)

    # Downgrading restores the exact un-annotated tier revision 335 wrote.
    for profile_id, default_model, default_effort in _SEEDED_PROFILES:
        label = (
            "Legacy default"
            if (default_model is not None or default_effort is not None)
            else "Runtime default"
        )
        assert reverted[profile_id]["model_tiers"] == [
            {
                "label": label,
                "model": default_model,
                "effort": default_effort,
                "parameters": {},
                "annotations": {},
            }
        ]


def test_orm_table_declares_postgresql_model_tiers_array_check() -> None:
    """MoonLadderStudios/MoonMind#3793: the ORM mirrors the array/length check."""

    postgres_ddl = str(
        CreateTable(ManagedAgentProviderProfile.__table__).compile(
            dialect=postgresql.dialect()
        )
    )
    assert "ck_provider_profiles_model_tiers_array" in postgres_ddl
    assert "jsonb_typeof(model_tiers) = 'array'" in postgres_ddl
    assert "jsonb_array_length(model_tiers) >= 1" in postgres_ddl

    # The check uses PostgreSQL jsonb functions, so it must not be emitted for
    # SQLite-backed schema creation.
    sqlite_ddl = str(
        CreateTable(ManagedAgentProviderProfile.__table__).compile(
            dialect=sqlite.dialect()
        )
    )
    assert "ck_provider_profiles_model_tiers_array" not in sqlite_ddl


def test_orm_model_tiers_column_is_jsonb_on_postgresql() -> None:
    """The array check calls jsonb-only functions, so a PostgreSQL table created
    from ORM metadata (tests/integration/omnigent/conftest.py::_create_tables)
    must declare ``model_tiers`` as JSONB, matching the migrated schema."""

    column = ManagedAgentProviderProfile.__table__.c.model_tiers

    postgres_column_ddl = str(
        CreateColumn(column).compile(dialect=postgresql.dialect())
    )
    assert "JSONB" in postgres_column_ddl

    sqlite_column_ddl = str(CreateColumn(column).compile(dialect=sqlite.dialect()))
    assert "JSONB" not in sqlite_column_ddl
    assert "JSON" in sqlite_column_ddl


def test_migration_provenance_matches_tier_contract() -> None:
    """MoonLadderStudios/MoonMind#3793: migration and contract stay in lockstep."""

    migration = importlib.import_module(_REVISION_365)

    assert migration.MIGRATED_FROM_ANNOTATION == MODEL_TIER_MIGRATION_ANNOTATION
    assert (
        migration.LEGACY_DEFAULT_MIGRATION_SOURCE
        == LEGACY_DEFAULT_TIER_MIGRATION_SOURCE
    )
    assert (
        migration.RUNTIME_DEFAULT_MIGRATION_SOURCE
        == RUNTIME_DEFAULT_TIER_MIGRATION_SOURCE
    )

    orm_check = next(
        constraint
        for constraint in ManagedAgentProviderProfile.__table__.constraints
        if constraint.name == "ck_provider_profiles_model_tiers_array"
    )
    assert str(orm_check.sqltext) == migration.MODEL_TIERS_ARRAY_CHECK
