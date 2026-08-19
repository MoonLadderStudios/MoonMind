"""PostgreSQL coverage for the decisive Omnigent control-plane invariants.

Source: MoonLadderStudios/MoonMind#3703 ([Omnigent control plane 2/11]).

The uniqueness and concurrency cases that decide whether a second canonical
chat authority can be created must be proven on PostgreSQL, not only SQLite,
because the "fail closed rather than pick the newest row" guarantee depends on
the real unique-index concurrency behaviour. Runs an ephemeral local cluster
with no manually managed test service (mirrors the checkpoint-branch migration
integration test).
"""

from __future__ import annotations

import asyncio
import os
import shutil
import socket
import subprocess
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import (
    OmnigentChatBindingAlias,
    OmnigentCleanupAuthority,
    OmnigentCommand,
    OmnigentObservation,
    OmnigentReconciliationDecision,
    OmnigentSession,
    OmnigentTurnAttempt,
)
from moonmind.omnigent.control_plane import (
    ConflictingSessionAuthorityError,
    ControlPlaneOutcome,
    FencingConflictError,
    FencingScope,
    OmnigentControlPlaneStore,
    RevisionConflictError,
)
from tests.helpers.omnigent_port_contracts import (
    run_decision_repository_contract,
    run_observation_repository_contract,
)

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]

_CONTROL_PLANE_TABLES = [
    OmnigentSession.__table__,
    OmnigentTurnAttempt.__table__,
    OmnigentObservation.__table__,
    OmnigentCommand.__table__,
    OmnigentReconciliationDecision.__table__,
    OmnigentChatBindingAlias.__table__,
    OmnigentCleanupAuthority.__table__,
]


@pytest.fixture
def control_plane_postgres_url():
    """Provide isolated PostgreSQL without a manually managed test service."""

    configured = os.getenv("MOONMIND_TEST_POSTGRES_URL", "").strip()
    if configured:
        if configured.startswith("postgresql://"):
            configured = configured.replace("postgresql://", "postgresql+asyncpg://", 1)
        if not configured.startswith("postgresql+asyncpg://"):
            pytest.fail("MOONMIND_TEST_POSTGRES_URL must use PostgreSQL")
        yield configured
        return

    initdb_path = shutil.which("initdb")
    if initdb_path is None:
        candidates = sorted(Path("/usr/lib/postgresql").glob("*/bin/initdb"))
        initdb_path = str(candidates[-1]) if candidates else None
    if initdb_path is None:
        pytest.fail("PostgreSQL test binaries are unavailable in the Python test image")
    initdb = str(Path(initdb_path))
    pg_ctl = str(Path(initdb).with_name("pg_ctl"))
    data_root = Path(tempfile.mkdtemp(prefix="moonmind-control-plane-postgres-"))
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
        yield f"postgresql+asyncpg://postgres@127.0.0.1:{port}/postgres"
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


@pytest_asyncio.fixture()
async def pg_store(control_plane_postgres_url):
    engine = create_async_engine(control_plane_postgres_url)
    async with engine.begin() as conn:
        await conn.run_sync(_create_tables)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield OmnigentControlPlaneStore(maker)
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(_drop_tables)
        await engine.dispose()


def _create_tables(sync_conn) -> None:
    for table in _CONTROL_PLANE_TABLES:
        table.create(sync_conn, checkfirst=True)


def _drop_tables(sync_conn) -> None:
    for table in reversed(_CONTROL_PLANE_TABLES):
        table.drop(sync_conn, checkfirst=True)


@pytest.mark.asyncio
async def test_postgres_scope_uniqueness_fails_closed(pg_store) -> None:
    async with pg_store.transaction() as repos:
        await repos.sessions.create(
            session_id="s1",
            moonmind_workflow_id="wf-1",
            provider="codex",
            provider_session_ref="psess-1",
        )
    with pytest.raises(ConflictingSessionAuthorityError):
        async with pg_store.transaction() as repos:
            await repos.sessions.create(
                session_id="s2",
                moonmind_workflow_id="wf-1",
                provider="codex",
                provider_session_ref="psess-1",
            )


@pytest.mark.asyncio
async def test_postgres_chat_binding_uniqueness_fails_closed(pg_store) -> None:
    async with pg_store.transaction() as repos:
        await repos.sessions.create(
            session_id="s1",
            moonmind_workflow_id="wf-1",
            provider="codex",
            provider_session_ref="psess-1",
            chat_binding_id="cb-shared",
        )
    with pytest.raises(ConflictingSessionAuthorityError):
        async with pg_store.transaction() as repos:
            await repos.sessions.create(
                session_id="s2",
                moonmind_workflow_id="wf-2",
                provider="codex",
                provider_session_ref="psess-2",
                chat_binding_id="cb-shared",
            )


@pytest.mark.asyncio
async def test_postgres_concurrent_scope_creation_admits_one_authority(pg_store) -> None:
    async def _create(session_id: str):
        async with pg_store.transaction() as repos:
            return await repos.sessions.create(
                session_id=session_id,
                moonmind_workflow_id="wf-1",
                provider="codex",
                provider_session_ref="psess-1",
            )

    results = await asyncio.gather(
        _create("s-a"), _create("s-b"), return_exceptions=True
    )
    successes = [r for r in results if not isinstance(r, Exception)]
    conflicts = [r for r in results if isinstance(r, ConflictingSessionAuthorityError)]
    # Exactly one canonical authority is admitted; the loser fails closed rather
    # than superseding the winner.
    assert len(successes) == 1
    assert len(conflicts) == 1

    async with pg_store.transaction() as repos:
        winner = successes[0]
        stored = await repos.sessions.get_by_scope("wf-1", "psess-1")
    assert stored is not None
    assert stored.session_id == winner.session_id


@pytest.mark.asyncio
async def test_postgres_concurrent_fenced_update_admits_one_writer(pg_store) -> None:
    # Two reconcilers loading the same revision must not both commit: the
    # revision fence is enforced by a real row lock, so exactly one write lands
    # and the loser fails closed rather than overwriting the winner's cursor.
    async with pg_store.transaction() as repos:
        created = await repos.sessions.create(
            session_id="s1",
            moonmind_workflow_id="wf-1",
            provider="codex",
            provider_session_ref="psess-1",
        )
    base_revision = created.revision

    async def _advance(cursor: str):
        async with pg_store.transaction() as repos:
            return await repos.sessions.update_lifecycle(
                "s1",
                provider_event_cursor=cursor,
                expected_revision=base_revision,
                expected_fencing_generation=0,
            )

    results = await asyncio.gather(
        _advance("cursor-a"), _advance("cursor-b"), return_exceptions=True
    )
    successes = [r for r in results if not isinstance(r, Exception)]
    conflicts = [r for r in results if isinstance(r, RevisionConflictError)]
    assert len(successes) == 1
    assert len(conflicts) == 1

    async with pg_store.transaction() as repos:
        stored = await repos.sessions.get("s1")
    assert stored.revision == base_revision + 1
    assert stored.provider_event_cursor == successes[0].provider_event_cursor


@pytest.mark.asyncio
async def test_postgres_superseded_fencing_generation_is_fenced(pg_store) -> None:
    # A replacement supervisor acquires a strictly newer session-supervisor
    # generation. The former owner, still presenting the old generation, is
    # fenced out even though it presents a matching revision at load time.
    async with pg_store.transaction() as repos:
        await repos.sessions.create(
            session_id="s1",
            moonmind_workflow_id="wf-1",
            provider="codex",
            provider_session_ref="psess-1",
        )
    async with pg_store.transaction() as repos:
        current = await repos.sessions.get("s1")
        superseded = await repos.sessions.acquire_fencing_generation(
            "s1",
            FencingScope.SESSION_SUPERVISOR,
            expected_revision=current.revision,
        )
    assert superseded.fencing_generation == 1

    # The former owner presents generation 0 and is fenced (not a lost update).
    with pytest.raises(FencingConflictError):
        async with pg_store.transaction() as repos:
            await repos.sessions.update_lifecycle(
                "s1",
                expected_revision=superseded.revision,
                expected_fencing_generation=0,
                observed_state="running",
            )
    # The current owner (generation 1) still writes.
    async with pg_store.transaction() as repos:
        applied = await repos.sessions.update_lifecycle(
            "s1",
            expected_revision=superseded.revision,
            expected_fencing_generation=1,
            observed_state="running",
        )
    assert applied.observed_state == "running"


@pytest.mark.asyncio
async def test_postgres_two_janitors_cannot_both_claim_cleanup(pg_store) -> None:
    async with pg_store.transaction() as repos:
        await repos.sessions.create(
            session_id="s1",
            moonmind_workflow_id="wf-1",
            provider="codex",
            provider_session_ref="psess-1",
        )

    async def _claim(token):
        async with pg_store.transaction() as repos:
            return await repos.cleanup.claim_cleanup(
                "s1", owner_class="janitor", claim_token=token
            )

    results = await asyncio.gather(
        _claim("janitor-a"), _claim("janitor-b"), return_exceptions=True
    )
    outcomes = [r.outcome for r in results if not isinstance(r, Exception)]
    # Exactly one janitor claims; the other (distinct claim token) observes the
    # live claim and is refused.
    assert outcomes.count(ControlPlaneOutcome.APPLIED) == 1
    assert outcomes.count(ControlPlaneOutcome.NOT_OWNER) == 1


@pytest.mark.asyncio
async def test_postgres_former_janitor_cannot_release_replacement_lease(pg_store) -> None:
    # A janitor claims cleanup fenced against host lease generation 1. The host
    # lease is then renewed to generation 2. The former janitor's completion is
    # fenced: it cannot release a resource that now belongs to a newer lease.
    async with pg_store.transaction() as repos:
        await repos.sessions.create(
            session_id="s1",
            moonmind_workflow_id="wf-1",
            provider="codex",
            provider_session_ref="psess-1",
        )
        current = await repos.sessions.get("s1")
        await repos.sessions.acquire_fencing_generation(
            "s1", FencingScope.HOST_LEASE, expected_revision=current.revision
        )
        # The claim derives its host fence from the live session (generation 1).
        claim = await repos.cleanup.claim_cleanup(
            "s1", owner_class="janitor", claim_token="janitor-a"
        )
    assert claim.outcome is ControlPlaneOutcome.APPLIED
    assert claim.record.fenced_host_generation == 1

    # Host lease is renewed (a replacement generation).
    async with pg_store.transaction() as repos:
        current = await repos.sessions.get("s1")
        await repos.sessions.acquire_fencing_generation(
            "s1", FencingScope.HOST_LEASE, expected_revision=current.revision
        )

    async with pg_store.transaction() as repos:
        completion = await repos.cleanup.complete_cleanup(
            "s1",
            generation=claim.record.generation,
            owner_class="janitor",
            claim_token="janitor-a",
            session_repository=repos.sessions,
        )
    assert completion.outcome is ControlPlaneOutcome.FENCING_CONFLICT

    # The fenced-out claim is not stuck forever: a janitor fenced against the
    # current lease takes it over (advancing the generation) and completes.
    async with pg_store.transaction() as repos:
        takeover = await repos.cleanup.claim_cleanup(
            "s1", owner_class="janitor", claim_token="janitor-b"
        )
        assert takeover.outcome is ControlPlaneOutcome.APPLIED
        assert takeover.record.generation == claim.record.generation + 1
        completed = await repos.cleanup.complete_cleanup(
            "s1",
            generation=takeover.record.generation,
            owner_class="janitor",
            claim_token="janitor-b",
            session_repository=repos.sessions,
        )
    assert completed.outcome is ControlPlaneOutcome.APPLIED
    assert completed.record.state == "complete"


@pytest.mark.asyncio
async def test_postgres_command_claimed_and_executed_once(pg_store) -> None:
    # One logical command is claimed and executed once under concurrent activity
    # retries: exactly one claimer gets execution authority.
    async with pg_store.transaction() as repos:
        await repos.sessions.create(
            session_id="s1",
            moonmind_workflow_id="wf-1",
            provider="codex",
            provider_session_ref="psess-1",
        )
        await repos.commands.record(
            command_id="c1",
            session_id="s1",
            command_type="ensure_host",
            idempotency_key="cmd-1",
            payload_digest="digest",
        )

    async def _claim(token):
        async with pg_store.transaction() as repos:
            return await repos.commands.claim_command(
                "c1", owner_class="supervisor", claim_token=token
            )

    results = await asyncio.gather(
        _claim("worker-a"), _claim("worker-b"), return_exceptions=True
    )
    outcomes = [r.outcome for r in results if not isinstance(r, Exception)]
    # Exactly one worker wins execution authority; the racing worker (distinct
    # claim token) is refused rather than granted a false success.
    assert outcomes.count(ControlPlaneOutcome.APPLIED) == 1
    assert outcomes.count(ControlPlaneOutcome.NOT_OWNER) == 1


@pytest.mark.asyncio
async def test_postgres_delayed_frontier_after_new_epoch_is_retained(pg_store) -> None:
    # A delayed event/callback from an older provider epoch cannot regress the
    # frontier after a newer supervisor generation is acquired; it is fenced and
    # retained as an observation instead of overwriting current state.
    async with pg_store.transaction() as repos:
        created = await repos.sessions.create(
            session_id="s1",
            moonmind_workflow_id="wf-1",
            provider="codex",
            provider_session_ref="psess-1",
        )
        await repos.sessions.update_lifecycle(
            "s1",
            expected_revision=created.revision,
            expected_fencing_generation=0,
            provider_event_cursor="cursor-2",
        )
    async with pg_store.transaction() as repos:
        current = await repos.sessions.get("s1")
        await repos.sessions.acquire_fencing_generation(
            "s1", FencingScope.SESSION_SUPERVISOR, expected_revision=current.revision
        )

    async with pg_store.transaction() as repos:
        current = await repos.sessions.get("s1")
        # A stale-epoch callback presents the old generation 0.
        result = await repos.sessions.advance_observation_frontier(
            "s1",
            expected_revision=current.revision,
            expected_fencing_generation=0,
            provider_event_cursor="cursor-1",
        )
    assert result.outcome is ControlPlaneOutcome.FENCING_CONFLICT

    async with pg_store.transaction() as repos:
        stored = await repos.sessions.get("s1")
    # The durable frontier was not regressed by the delayed event.
    assert stored.provider_event_cursor == "cursor-2"


@pytest.mark.asyncio
async def test_postgres_null_provider_session_scopes_are_distinct(pg_store) -> None:
    # Two unattached canonical rows in one workflow do not collide (NULLs are
    # distinct under the unique index), so pre-attachment rows stay independent.
    async with pg_store.transaction() as repos:
        await repos.sessions.create(
            session_id="s1", moonmind_workflow_id="wf-1", provider="codex"
        )
        await repos.sessions.create(
            session_id="s2", moonmind_workflow_id="wf-1", provider="codex"
        )
        listed = await repos.turn_attempts.list_for_session("s1")
    assert listed == []


@pytest.mark.asyncio
async def test_postgres_observation_port_contract(pg_store) -> None:
    # The production PostgreSQL adapter satisfies the same shared port contract
    # as the in-memory reference adapter (MoonLadderStudios/MoonMind#3711).
    async with pg_store.transaction() as repos:
        for session_id in ("sa", "sb"):
            await repos.sessions.create(
                session_id=session_id,
                moonmind_workflow_id=f"wf-{session_id}",
                provider="codex",
                provider_session_ref=f"psess-{session_id}",
            )
        await run_observation_repository_contract(
            repos.observations, session_a="sa", session_b="sb"
        )


@pytest.mark.asyncio
async def test_postgres_decision_port_contract(pg_store) -> None:
    async with pg_store.transaction() as repos:
        for session_id in ("sa", "sb"):
            await repos.sessions.create(
                session_id=session_id,
                moonmind_workflow_id=f"wf-{session_id}",
                provider="codex",
                provider_session_ref=f"psess-{session_id}",
            )
        await run_decision_repository_contract(
            repos.decisions, session_a="sa", session_b="sb"
        )
