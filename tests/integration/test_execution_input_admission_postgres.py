"""Canonical create identity and its delegated inputs share one transaction."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import (
    Base,
    TemporalArtifact,
    TemporalArtifactLink,
    TemporalArtifactUseClaim,
    TemporalExecutionCanonicalRecord,
)
from api_service.services.omnigent_execution_plan_service import (
    PersistedOmnigentExecutionPlan,
)
from moonmind.config.settings import settings
from moonmind.omnigent.harness_platform.stores import DbExecutionPlanStore
from moonmind.workflows.temporal.artifacts import (
    TemporalArtifactAuthorizationError,
    TemporalArtifactRepository,
    TemporalArtifactService,
)
from moonmind.workflows.temporal.service import (
    TemporalExecutionService,
    TemporalExecutionValidationError,
)
from tests.integration import (
    test_single_user_conversion_postgres_4346 as postgres_fixtures,
)
from tests.unit.omnigent.test_scheduled_planning_input_authority import _schedule

conversion_postgres_url = postgres_fixtures.conversion_postgres_url
pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]


@pytest_asyncio.fixture
async def sessions(conversion_postgres_url, tmp_path, monkeypatch):
    schema = f"admission_{uuid4().hex}"
    admin = create_async_engine(conversion_postgres_url)
    async with admin.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(
        conversion_postgres_url,
        connect_args={"server_settings": {"search_path": schema}},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    monkeypatch.setattr(settings.workflow, "temporal_artifact_backend", "local_fs")
    monkeypatch.setattr(
        settings.workflow, "temporal_artifact_root", str(tmp_path / "blobs")
    )
    try:
        yield sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    finally:
        await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


async def _create(service, sessions, saved, *, key="same-request"):
    envelope = await DbExecutionPlanStore(sessions).load(saved.binding.plan_ref)
    compiled = PersistedOmnigentExecutionPlan(
        envelope=envelope,
        binding=saved.binding,
        artifact_refs=(),
        resolved_skillset_ref=saved.skills.manifest_id,
        resolved_skillset_digest="sha256:" + "a" * 64,
    )
    return await service.create_execution(
        workflow_type="MoonMind.UserWorkflow",
        owner_id=None,
        title="Saved request",
        input_artifact_ref=saved.input_id,
        plan_artifact_ref=saved.plan_id,
        manifest_artifact_ref=None,
        failure_policy=None,
        initial_parameters={
            **saved.action["initial_parameters"],
            "resolvedSkillsetRef": saved.skills.manifest_id,
        },
        idempotency_key=key,
        _workflow_id="mm:deterministic-api-create",
        _admitted_execution_plan=compiled,
    )


def _service(session, adapter):
    service = TemporalExecutionService(session, client_adapter=adapter)
    service.check_system_paused = AsyncMock(return_value=False)
    service._enforce_worker_code_freshness = AsyncMock()
    service._sync_projection_best_effort = AsyncMock(side_effect=lambda record: record)
    return service


async def test_concurrent_same_key_different_inputs_only_grants_the_create_winner(
    sessions,
):
    candidates = [await _schedule(sessions), await _schedule(sessions)]
    starts = []

    async def start(**kwargs):
        starts.append(kwargs)
        return SimpleNamespace(run_id="actual-postgres-run")

    adapter = SimpleNamespace(start_workflow=AsyncMock(side_effect=start))
    ready = asyncio.Event()
    initial_lookups = 0

    async def writer(saved):
        nonlocal initial_lookups
        async with sessions() as session:
            service = _service(session, adapter)
            find_existing = service.find_create_execution

            async def overlapping_lookup(**kwargs):
                nonlocal initial_lookups
                result = await find_existing(**kwargs)
                initial_lookups += 1
                if initial_lookups <= 2:
                    if initial_lookups == 2:
                        ready.set()
                    await ready.wait()
                return result

            service.find_create_execution = overlapping_lookup
            return await _create(service, sessions, saved)

    results = await asyncio.wait_for(
        asyncio.gather(*(writer(saved) for saved in candidates)), 60
    )
    assert len(starts) == 1
    assert {record.workflow_id for record in results} == {"mm:deterministic-api-create"}
    winner_ref = starts[0]["input_args"]["initial_parameters"]["omnigentExecutionPlan"][
        "planRef"
    ]
    winner = next(saved for saved in candidates if saved.binding.plan_ref == winner_ref)
    loser = next(saved for saved in candidates if saved is not winner)
    async with sessions() as session:
        service = TemporalArtifactService(TemporalArtifactRepository(session))
        _, body = await service.read(
            artifact_id=winner.input_id,
            principal="workflow:mm:deterministic-api-create",
        )
        assert body
        for artifact_id in (
            loser.input_id,
            loser.plan_id,
            loser.snapshot_id,
            loser.skills.manifest_id,
            loser.skills.body_id,
        ):
            with pytest.raises(TemporalArtifactAuthorizationError):
                await service.read(
                    artifact_id=artifact_id,
                    principal="workflow:mm:deterministic-api-create",
                )
        links = (
            (
                await session.execute(
                    select(TemporalArtifactLink).where(
                        TemporalArtifactLink.workflow_id
                        == "mm:deterministic-api-create"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert {link.run_id for link in links} == {"actual-postgres-run"}


async def test_late_input_closure_failure_rolls_back_create_and_every_grant(sessions):
    saved = await _schedule(sessions)
    async with sessions() as session:
        artifact = await session.get(TemporalArtifact, saved.skills.manifest_id)
        TemporalArtifactService(TemporalArtifactRepository(session))._store.read_path(
            artifact.storage_key
        ).write_bytes(b"invalid")
    adapter = SimpleNamespace(start_workflow=AsyncMock())
    async with sessions() as session:
        service = _service(session, adapter)
        with pytest.raises(TemporalExecutionValidationError, match="Skill manifest"):
            await _create(service, sessions, saved)
        adapter.start_workflow.assert_not_awaited()
    async with sessions() as session:
        assert (
            await session.execute(select(TemporalExecutionCanonicalRecord))
        ).scalars().all() == []
        assert (
            await session.execute(select(TemporalArtifactLink))
        ).scalars().all() == []
        assert (
            await session.execute(select(TemporalArtifactUseClaim))
        ).scalars().all() == []


async def test_consumer_claim_failure_rolls_back_its_claim_with_the_create(
    sessions, monkeypatch
):
    saved = await _schedule(sessions)

    async def fail_release(*_args, **_kwargs):
        raise RuntimeError("injected consumer release failure")

    monkeypatch.setattr(TemporalArtifactService, "_release_observed_use", fail_release)
    adapter = SimpleNamespace(start_workflow=AsyncMock())
    async with sessions() as session:
        service = _service(session, adapter)
        with pytest.raises(RuntimeError, match="injected consumer release failure"):
            await _create(service, sessions, saved)
        adapter.start_workflow.assert_not_awaited()
    async with sessions() as session:
        assert (
            await session.execute(select(TemporalExecutionCanonicalRecord))
        ).scalars().all() == []
        assert (
            await session.execute(select(TemporalArtifactLink))
        ).scalars().all() == []
        assert (
            await session.execute(select(TemporalArtifactUseClaim))
        ).scalars().all() == []
