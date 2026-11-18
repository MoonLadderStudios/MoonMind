"""Account-usage coordination on production PostgreSQL semantics (#5362)."""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest
import pytest_asyncio
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api_service.db.models import (
    Base,
    ManagedAgentProviderProfile,
    ProviderAccountUsageObservation,
)
from api_service.services.provider_account_usage import ProviderAccountUsageService
from moonmind.provider_profiles.account_usage import (
    ObservationRejection,
    RefreshOutcome,
    RefreshTrigger,
)
from tests.unit.services.account_usage_support import (
    T0,
    Clock,
    FakeDispatcher,
    make_profile,
    observation,
    usage,
    window,
)

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]

REPO_ROOT = Path(__file__).resolve().parents[3]
TABLES = [
    ManagedAgentProviderProfile.__table__,
    ProviderAccountUsageObservation.__table__,
]


@pytest_asyncio.fixture
async def pg(control_plane_postgres_url):
    engine = create_async_engine(control_plane_postgres_url)
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync: Base.metadata.create_all(sync, tables=TABLES)
        )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        session.add_all([make_profile("alpha"), make_profile("beta")])
        await session.commit()
    try:
        yield control_plane_postgres_url, engine, sessions
    finally:
        await engine.dispose()


def _workers(url: str, count: int):
    """Independent engines stand in for separate API worker processes."""

    return [create_async_engine(url, pool_size=2) for _ in range(count)]


async def _call(engine, clock, dispatcher, method, *args, **kwargs):
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        service = ProviderAccountUsageService(
            session, dispatcher=dispatcher, clock=clock
        )
        return await getattr(service, method)(*args, **kwargs)


@pytest.mark.asyncio
async def test_concurrent_requests_across_workers_coalesce_to_one_collection(
    pg,
) -> None:
    url, _, _ = pg
    clock, dispatcher = Clock(), FakeDispatcher()
    workers = _workers(url, 4)
    try:
        results = await asyncio.gather(
            *(
                _call(
                    workers[i % 4],
                    clock,
                    dispatcher,
                    "request_refresh",
                    ["beta", "alpha"] if i % 2 else ["alpha", "beta"],
                    trigger=RefreshTrigger.VISIBILITY,
                )
                for i in range(16)
            )
        )
    finally:
        for worker in workers:
            await worker.dispose()
    assert sorted(r.profile_id for r in dispatcher.requests) == ["alpha", "beta"]
    for profile_id in ("alpha", "beta"):
        attempts = {
            r.request_id
            for batch in results
            for r in batch
            if r.profile_id == profile_id
        }
        assert len(attempts) == 1
        assert all(
            r.outcome is RefreshOutcome.ACCEPTED
            for batch in results
            for r in batch
            if r.profile_id == profile_id
        )


@pytest.mark.asyncio
async def test_separate_processes_share_single_flight_ownership(pg) -> None:
    url, _, sessions = pg
    script = textwrap.dedent(
        """
        import asyncio, json, sys
        from datetime import datetime
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from api_service.services.provider_account_usage import ProviderAccountUsageService
        from moonmind.provider_profiles.account_usage import RefreshTrigger
        from tests.unit.services.account_usage_support import FakeDispatcher

        async def main():
            engine = create_async_engine(sys.argv[1])
            dispatcher = FakeDispatcher()
            now = datetime.fromisoformat(sys.argv[2])
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                service = ProviderAccountUsageService(
                    session, dispatcher=dispatcher, clock=lambda: now
                )
                [result] = await service.request_refresh(
                    ["alpha"], trigger=RefreshTrigger.MANUAL
                )
            await engine.dispose()
            print(json.dumps({"dispatched": len(dispatcher.requests),
                              "request_id": result.request_id,
                              "outcome": result.outcome.value}))

        asyncio.run(main())
        """
    )
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", script, url, T0.isoformat()],
            cwd=REPO_ROOT,
            env={**os.environ, "PYTHONPATH": str(REPO_ROOT)},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(3)
    ]
    outputs = []
    for process in processes:
        stdout, stderr = process.communicate(timeout=120)
        assert process.returncode == 0, stderr[-2000:]
        outputs.append(json.loads(stdout.strip().splitlines()[-1]))
    assert sum(o["dispatched"] for o in outputs) == 1
    assert len({o["request_id"] for o in outputs}) == 1
    assert {o["outcome"] for o in outputs} == {"accepted"}


@pytest.mark.asyncio
async def test_abandoned_attempt_is_reclaimed_once_after_restart(pg) -> None:
    url, _, sessions = pg
    clock, dispatcher = Clock(), FakeDispatcher()
    first_process = create_async_engine(url)
    [first] = await _call(
        first_process,
        clock,
        dispatcher,
        "request_refresh",
        ["alpha"],
        trigger=RefreshTrigger.MANUAL,
    )
    # The API process dies before the executor acknowledges the attempt.
    await first_process.dispose()
    clock.advance(minutes=3)

    workers = _workers(url, 3)
    try:
        reclaimed = await asyncio.gather(
            *(
                _call(
                    w,
                    clock,
                    dispatcher,
                    "request_refresh",
                    ["alpha"],
                    trigger=RefreshTrigger.VISIBILITY,
                )
                for w in workers * 2
            )
        )
        attempts = {batch[0].request_id for batch in reclaimed}
        assert len(attempts) == 1 and first.request_id not in attempts
        assert len(dispatcher.requests) == 2
        late = await _call(
            workers[0],
            clock,
            dispatcher,
            "ingest_observation",
            observation(
                "alpha",
                attempt_id=first.request_id,
                usage=usage("full", clock.now, window("weekly")),
            ),
        )
        assert late.rejection is ObservationRejection.OWNER_EXPIRED
    finally:
        for worker in workers:
            await worker.dispose()


@pytest.mark.asyncio
async def test_identity_and_quota_reads_for_different_accounts_cannot_both_land(
    pg,
) -> None:
    url, _, _ = pg
    clock, dispatcher = Clock(), FakeDispatcher()
    identity_read = observation(
        "alpha",
        identity={
            "email": "a@example.com",
            "account_id": "acct-a",
            "verified": True,
            "source": "fake_identity",
            "observed_at": T0,
        },
    )
    quota_read = observation(
        "alpha", account_id="acct-b", usage=usage("full", T0, window("weekly", used=70))
    )
    workers = _workers(url, 2)
    try:
        results = await asyncio.gather(
            _call(workers[0], clock, dispatcher, "ingest_observation", identity_read),
            _call(workers[1], clock, dispatcher, "ingest_observation", quota_read),
        )
    finally:
        for worker in workers:
            await worker.dispose()
    assert sorted(r.accepted for r in results) == [False, True]
    [rejected] = [r for r in results if not r.accepted]
    assert rejected.rejection is ObservationRejection.ACCOUNT_MISMATCH


@pytest.mark.asyncio
async def test_rebinding_race_fences_results_for_the_old_generation(pg) -> None:
    url, engine, sessions = pg
    clock, dispatcher = Clock(), FakeDispatcher()
    [claimed] = await _call(
        engine,
        clock,
        dispatcher,
        "request_refresh",
        ["alpha"],
        trigger=RefreshTrigger.MANUAL,
    )

    async def rebind():
        async with sessions() as session:
            profile = await session.get(ManagedAgentProviderProfile, "alpha")
            profile.credential_generation += 1
            await session.commit()

    class RacingService(ProviderAccountUsageService):
        raced = False

        async def _rows(self, profile_ids):
            rows = await super()._rows(profile_ids)
            if rows and not RacingService.raced:
                # The account switch commits after the generation check but
                # before the result's compare-and-set.
                RacingService.raced = True
                await rebind()
            return rows

    worker = create_async_engine(url)
    try:
        async with async_sessionmaker(worker, expire_on_commit=False)() as session:
            late = await RacingService(
                session, dispatcher=dispatcher, clock=clock
            ).ingest_observation(
                observation(
                    "alpha",
                    attempt_id=claimed.request_id,
                    account_id="acct-old",
                    usage=usage("full", clock.now, window("weekly", used=90)),
                )
            )
    finally:
        await worker.dispose()
    assert RacingService.raced
    assert late.rejection is ObservationRejection.STALE_GENERATION
    async with sessions() as session:
        row = (
            (
                await session.execute(
                    select(ProviderAccountUsageObservation.__table__).where(
                        ProviderAccountUsageObservation.profile_id == "alpha"
                    )
                )
            )
            .mappings()
            .one()
        )
    assert row["usage_json"] is None and row["bound_account_id"] is None


@pytest.mark.asyncio
async def test_cache_survives_restart_loads_in_one_query_and_cascades(pg) -> None:
    url, engine, sessions = pg
    clock, dispatcher = Clock(), FakeDispatcher()
    await _call(
        engine,
        clock,
        dispatcher,
        "ingest_observation",
        observation(
            "alpha",
            identity={"email": "a@example.com", "source": "fake", "observed_at": T0},
            usage=usage(
                "full", T0, window("weekly", used=25, reset_at=T0.replace(day=20))
            ),
        ),
    )
    restarted = create_async_engine(url)
    statements: list[str] = []
    event.listen(
        restarted.sync_engine,
        "before_cursor_execute",
        lambda _c, _cur, statement, *_a: statements.append(statement),
    )
    # A slow, failing collector is never consulted for cached reads.
    dispatcher.delay, dispatcher.error = 30, RuntimeError("down")
    try:
        async with async_sessionmaker(restarted, expire_on_commit=False)() as session:
            profiles = (
                (await session.execute(select(ManagedAgentProviderProfile)))
                .scalars()
                .all()
            )
            statements.clear()
            projections = await ProviderAccountUsageService(
                session, dispatcher=dispatcher, clock=clock
            ).load_projections(profiles)
    finally:
        await restarted.dispose()
    assert (
        len([s for s in statements if "provider_account_usage_observations" in s]) == 1
    )
    assert dispatcher.requests == []
    alpha = projections["alpha"]
    assert alpha["status"] == "fresh"
    assert alpha["usage"]["windows"][0]["observed_at"] == T0
    assert alpha["account"]["email"] == "a@example.com"
    assert projections["beta"]["status"] == "unknown"

    async with sessions() as session:
        await session.delete(await session.get(ManagedAgentProviderProfile, "alpha"))
        await session.commit()
        remaining = (
            (await session.execute(select(ProviderAccountUsageObservation.profile_id)))
            .scalars()
            .all()
        )
    assert remaining == []


def test_forward_migration_on_postgres_creates_no_observations(
    control_plane_postgres_url,
):
    sync_url = control_plane_postgres_url.replace("+asyncpg", "+psycopg2")
    engine = sa.create_engine(sync_url)
    migration = importlib.import_module(
        "api_service.migrations.versions.415_provider_account_usage"
    )
    try:
        with engine.begin() as connection:
            ManagedAgentProviderProfile.__table__.create(connection)
            connection.execute(
                ManagedAgentProviderProfile.__table__.insert().values(
                    profile_id="existing",
                    capacity_scope_ref="provider-profile:existing",
                    runtime_id="codex_cli",
                    provider_id="openai",
                    account_label="person@example.com",
                    model_tiers=[{"label": "Runtime default", "model": None}],
                )
            )
            before = connection.execute(
                sa.text("SELECT * FROM managed_agent_provider_profiles")
            ).all()
            with patch.object(
                migration, "op", Operations(MigrationContext.configure(connection))
            ):
                migration.upgrade()
            assert (
                connection.execute(
                    sa.text("SELECT COUNT(*) FROM provider_account_usage_observations")
                ).scalar_one()
                == 0
            )
            assert (
                connection.execute(
                    sa.text("SELECT * FROM managed_agent_provider_profiles")
                ).all()
                == before
            )
            connection.execute(
                sa.text(
                    "INSERT INTO provider_account_usage_observations "
                    "(profile_id, credential_generation, provider_id, runtime_id) "
                    "VALUES ('existing', 1, 'openai', 'codex_cli')"
                )
            )
            connection.execute(sa.text("DELETE FROM managed_agent_provider_profiles"))
            assert (
                connection.execute(
                    sa.text("SELECT COUNT(*) FROM provider_account_usage_observations")
                ).scalar_one()
                == 0
            )
            with patch.object(
                migration, "op", Operations(MigrationContext.configure(connection))
            ):
                migration.downgrade()
    finally:
        engine.dispose()
