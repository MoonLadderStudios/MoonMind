"""Real PostgreSQL coverage for the provider capacity scope migration.

Source: MoonLadderStudios/MoonMind#3882 (AC7).

Migration ``368_provider_capacity_scopes`` seeds one scope per existing
Provider Profile and removes the one-profile uniqueness constraint so several
profiles may intentionally share one upstream allowance. Two properties have to
hold against a real cluster rather than against a fake session:

* upgrading preserves the leases that are already held, because the durable
  lease ledger is what the manager restores its capacity authority from; and
* downgrading fails closed when scopes are shared, because restoring 1:1
  uniqueness would silently discard the shared allowance those profiles were
  admitted under.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import make_url


pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]

_BEFORE_REVISION = "367_remove_workflow_proposals"
_SCOPE_REVISION = "368_provider_capacity_scopes"


@pytest.fixture
def capacity_scope_postgres_url() -> str:
    """Provide an isolated PostgreSQL database for a staged upgrade."""

    configured = os.getenv("MOONMIND_TEST_POSTGRES_URL", "").strip()
    if configured:
        configured = configured.replace(
            "postgresql+asyncpg://", "postgresql+psycopg2://", 1
        )
        configured = configured.replace("postgresql://", "postgresql+psycopg2://", 1)
        if not configured.startswith("postgresql+psycopg2://"):
            pytest.fail("MOONMIND_TEST_POSTGRES_URL must use PostgreSQL")
        yield configured
        return

    initdb_path = shutil.which("initdb")
    if initdb_path is None:
        candidates = sorted(Path("/usr/lib/postgresql").glob("*/bin/initdb"))
        initdb_path = str(candidates[-1]) if candidates else None
    if initdb_path is None:
        pytest.fail("PostgreSQL test binaries are unavailable in the test image")

    initdb = str(Path(initdb_path))
    pg_ctl = str(Path(initdb).with_name("pg_ctl"))
    data_root = Path(tempfile.mkdtemp(prefix="moonmind-capacity-scope-postgres-"))
    data_dir = data_root / "data"
    log_path = data_root / "postgres.log"
    command_prefix: list[str] = []
    if os.geteuid() == 0:
        shutil.chown(data_root, user="postgres", group="postgres")
        command_prefix = ["runuser", "--user", "postgres", "--"]

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]

    subprocess.run(
        [
            *command_prefix,
            initdb,
            "--pgdata",
            str(data_dir),
            "--username",
            "postgres",
            "--auth",
            "trust",
            "--encoding",
            "UTF8",
            "--no-locale",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [
            *command_prefix,
            pg_ctl,
            "--pgdata",
            str(data_dir),
            "--log",
            str(log_path),
            "--options",
            f"-F -p {port} -h 127.0.0.1 -k {data_dir}",
            "--wait",
            "start",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    try:
        yield f"postgresql+psycopg2://postgres@127.0.0.1:{port}/postgres"
    finally:
        subprocess.run(
            [
                *command_prefix,
                pg_ctl,
                "--pgdata",
                str(data_dir),
                "--mode",
                "immediate",
                "--wait",
                "stop",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        shutil.rmtree(data_root, ignore_errors=True)


def _alembic(database_url: str, *args: str) -> subprocess.CompletedProcess[str]:
    repo_root = Path(__file__).resolve().parents[3]
    url = make_url(database_url)
    env = os.environ.copy()
    env.update(
        {
            "POSTGRES_HOST": str(url.host),
            "POSTGRES_PORT": str(url.port),
            "POSTGRES_USER": str(url.username),
            "POSTGRES_PASSWORD": str(url.password or ""),
            "POSTGRES_DB": str(url.database),
        }
    )
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            "api_service/migrations/alembic.ini",
            *args,
        ],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )


def _required_defaults(table: sa.Table) -> dict[str, object]:
    """Placeholder values for every column the schema will not fill itself."""

    values: dict[str, object] = {}
    for column in table.columns:
        if column.nullable or column.server_default is not None:
            continue
        if isinstance(column.type, (sa.Integer, sa.BigInteger, sa.SmallInteger)):
            values[column.name] = 1
        elif isinstance(column.type, sa.Boolean):
            values[column.name] = False
        elif isinstance(column.type, sa.DateTime):
            values[column.name] = sa.func.now()
        else:
            values[column.name] = "placeholder"
    return values


def _insert_profile(
    connection: sa.Connection,
    profiles: sa.Table,
    *,
    profile_id: str,
    runtime_id: str,
    provider_id: str,
    capacity_scope_ref: str,
    max_parallel_runs: int,
) -> None:
    values = _required_defaults(profiles)
    values.update(
        {
            "profile_id": profile_id,
            "runtime_id": runtime_id,
            "provider_id": provider_id,
            "capacity_scope_ref": capacity_scope_ref,
            "max_parallel_runs": max_parallel_runs,
            "enabled": True,
            "is_default": False,
        }
    )
    if "auth_state" in profiles.columns:
        values["auth_state"] = "connected"
    if "model_tiers" in profiles.columns:
        values["model_tiers"] = '[{"tier": 1, "model": "placeholder"}]'
    connection.execute(sa.insert(profiles).values(**values))


def _insert_lease(
    connection: sa.Connection,
    leases: sa.Table,
    *,
    workflow_id: str,
    profile_id: str,
    runtime_id: str,
) -> None:
    values = _required_defaults(leases)
    values.update(
        {
            "workflow_id": workflow_id,
            "profile_id": profile_id,
            "runtime_id": runtime_id,
        }
    )
    if "lease_id" in leases.columns:
        values["lease_id"] = workflow_id
    if "owner_id" in leases.columns:
        values["owner_id"] = workflow_id
    if "purpose" in leases.columns:
        values["purpose"] = "execution_omnigent"
    if "lease_state" in leases.columns:
        values["lease_state"] = "held"
    connection.execute(sa.insert(leases).values(**values))


def _constraint_names(connection: sa.Connection, table_name: str) -> set[str]:
    return set(
        connection.execute(
            sa.text(
                """
                select conname
                from pg_constraint
                where conrelid = cast(:table_name as regclass)
                """
            ),
            {"table_name": table_name},
        ).scalars()
    )


def test_the_capacity_scope_migration_seeds_scopes_and_preserves_leases(
    capacity_scope_postgres_url: str,
) -> None:
    """AC7: upgrading must not disturb the leases already held."""

    staged = _alembic(capacity_scope_postgres_url, "upgrade", _BEFORE_REVISION)
    assert staged.returncode == 0, staged.stderr[-4000:]

    engine = sa.create_engine(capacity_scope_postgres_url)
    try:
        metadata = sa.MetaData()
        profiles = sa.Table(
            "managed_agent_provider_profiles", metadata, autoload_with=engine
        )
        leases = sa.Table(
            "provider_profile_slot_leases", metadata, autoload_with=engine
        )
        with engine.begin() as connection:
            _insert_profile(
                connection,
                profiles,
                profile_id="opencode-zen-free",
                runtime_id="opencode",
                provider_id="opencode",
                capacity_scope_ref="provider-profile:opencode-zen-free",
                max_parallel_runs=8,
            )
            _insert_profile(
                connection,
                profiles,
                profile_id="opencode-zen-paid",
                runtime_id="opencode",
                provider_id="opencode",
                capacity_scope_ref="provider-profile:opencode-zen-paid",
                max_parallel_runs=4,
            )
            _insert_lease(
                connection,
                leases,
                workflow_id="agent-run-held",
                profile_id="opencode-zen-free",
                runtime_id="opencode",
            )

        upgraded = _alembic(capacity_scope_postgres_url, "upgrade", _SCOPE_REVISION)
        assert upgraded.returncode == 0, upgraded.stderr[-4000:]

        with engine.connect() as connection:
            seeded = connection.execute(
                sa.text(
                    """
                    select scope_ref, runtime_id, provider_class, generation,
                           configured_limit, effective_limit, backpressure_state,
                           recovery_policy_ref
                    from provider_capacity_scopes
                    order by scope_ref
                    """
                )
            ).all()
            assert [row.scope_ref for row in seeded] == [
                "provider-profile:opencode-zen-free",
                "provider-profile:opencode-zen-paid",
            ]
            free, paid = seeded
            # An equivalent one-profile scope: effective behavior unchanged.
            assert (free.configured_limit, free.effective_limit) == (8, 8)
            assert (paid.configured_limit, paid.effective_limit) == (4, 4)
            for row in seeded:
                assert row.runtime_id == "opencode"
                assert row.provider_class == "opencode"
                assert row.generation == 1
                assert row.backpressure_state == "healthy"
                assert (
                    row.recovery_policy_ref
                    == "additive-increase-multiplicative-decrease@1"
                )

            # The durable lease ledger is the manager's capacity authority; the
            # migration must not have touched it.
            held = connection.execute(
                sa.text(
                    "select workflow_id, profile_id from provider_profile_slot_leases"
                )
            ).all()
            assert [(row.workflow_id, row.profile_id) for row in held] == [
                ("agent-run-held", "opencode-zen-free")
            ]

            constraints = _constraint_names(
                connection, "managed_agent_provider_profiles"
            )
            assert "uq_provider_profile_capacity_scope" not in constraints
            indexes = {
                index["name"]
                for index in sa.inspect(engine).get_indexes(
                    "managed_agent_provider_profiles"
                )
            }
            assert "ix_provider_profile_capacity_scope" in indexes
    finally:
        engine.dispose()


def test_downgrade_rejects_an_unsafe_shared_scope_rollback(
    capacity_scope_postgres_url: str,
) -> None:
    """AC7: restoring 1:1 uniqueness must not silently discard a shared allowance."""

    staged = _alembic(capacity_scope_postgres_url, "upgrade", _SCOPE_REVISION)
    assert staged.returncode == 0, staged.stderr[-4000:]

    engine = sa.create_engine(capacity_scope_postgres_url)
    try:
        metadata = sa.MetaData()
        profiles = sa.Table(
            "managed_agent_provider_profiles", metadata, autoload_with=engine
        )
        leases = sa.Table(
            "provider_profile_slot_leases", metadata, autoload_with=engine
        )
        with engine.begin() as connection:
            for profile_id in ("opencode-a", "opencode-b"):
                _insert_profile(
                    connection,
                    profiles,
                    profile_id=profile_id,
                    runtime_id="opencode",
                    provider_id="opencode",
                    # Both draw from one real upstream allowance.
                    capacity_scope_ref="opencode-zen-account",
                    max_parallel_runs=8,
                )
            connection.execute(
                sa.text(
                    """
                    insert into provider_capacity_scopes
                        (scope_ref, runtime_id, provider_class, generation,
                         configured_limit, effective_limit, backpressure_state,
                         recovery_policy_ref)
                    values ('opencode-zen-account', 'opencode', 'opencode', 1,
                            10, 10, 'healthy',
                            'additive-increase-multiplicative-decrease@1')
                    """
                )
            )
            _insert_lease(
                connection,
                leases,
                workflow_id="agent-run-shared",
                profile_id="opencode-a",
                runtime_id="opencode",
            )

        rolled_back = _alembic(
            capacity_scope_postgres_url, "downgrade", _BEFORE_REVISION
        )
        assert rolled_back.returncode != 0, rolled_back.stdout[-4000:]

        with engine.connect() as connection:
            # Failing closed means the shared allowance and the leases admitted
            # under it are still there to reconcile against.
            assert (
                connection.execute(
                    sa.text(
                        "select configured_limit from provider_capacity_scopes "
                        "where scope_ref = 'opencode-zen-account'"
                    )
                ).scalar()
                == 10
            )
            assert (
                connection.execute(
                    sa.text(
                        "select count(*) from provider_profile_slot_leases "
                        "where workflow_id = 'agent-run-shared'"
                    )
                ).scalar()
                == 1
            )

        # Once no scope is shared, the same rollback is safe and succeeds.
        with engine.begin() as connection:
            connection.execute(
                sa.text(
                    "update managed_agent_provider_profiles "
                    "set capacity_scope_ref = 'provider-profile:' || profile_id"
                )
            )
        unshared = _alembic(capacity_scope_postgres_url, "downgrade", _BEFORE_REVISION)
        assert unshared.returncode == 0, unshared.stderr[-4000:]

        with engine.connect() as connection:
            assert "provider_capacity_scopes" not in sa.inspect(
                engine
            ).get_table_names()
            assert (
                connection.execute(
                    sa.text(
                        "select count(*) from provider_profile_slot_leases "
                        "where workflow_id = 'agent-run-shared'"
                    )
                ).scalar()
                == 1
            )
            constraints = _constraint_names(
                connection, "managed_agent_provider_profiles"
            )
            assert "uq_provider_profile_capacity_scope" in constraints
    finally:
        engine.dispose()
