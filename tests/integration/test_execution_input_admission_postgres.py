"""Canonical create identity and its delegated inputs share one transaction."""

import asyncio
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from temporalio.service import RPCError, RPCStatusCode

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

    async def describe(workflow_id):
        if starts:
            return SimpleNamespace(run_id="actual-postgres-run")
        raise RPCError("not started", RPCStatusCode.NOT_FOUND, b"")

    adapter = SimpleNamespace(
        start_workflow=AsyncMock(side_effect=start),
        describe_workflow=AsyncMock(side_effect=describe),
        namespace_retention_seconds=AsyncMock(return_value=86_400),
    )
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
        admitted = await session.get(
            TemporalExecutionCanonicalRecord, "mm:deterministic-api-create"
        )
        assert admitted.memo["task_input_snapshot_ref"] == (
            winner.binding.task_input_snapshot_ref
        )
        assert {loser.snapshot_id, loser.binding.task_input_snapshot_ref}.isdisjoint(
            admitted.artifact_refs
        )
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


async def test_frozen_compile_resolves_deployment_skill_in_callers_transaction(
    sessions, monkeypatch, tmp_path
):
    from api_service.db.models import AgentSkillDefinition, OmnigentExecutionPlanRecord
    from api_service.services import omnigent_execution_plan_service as plans
    from api_service.services.agent_skills_service import AgentSkillsService
    from moonmind.omnigent.harness_platform.catalog import (
        TrustState,
        classify_harness_trust,
        create_catalog_snapshot,
    )
    from moonmind.omnigent.harness_platform.catalog_service import (
        DbHarnessCatalogRepository,
        HarnessCatalogSyncResult,
    )
    from tests.unit.services.test_omnigent_execution_plan_service import (
        _compile_opencode_plan,
        _configure_ready_host_image_pair,
        _protected_support_evidence,
    )
    from tests.unit.workflows.temporal.test_saved_work_retention_4017 import (
        _service as local_artifacts,
    )

    provenance = _configure_ready_host_image_pair(monkeypatch)
    provenance["hostVersion"] = "0.11.0"
    monkeypatch.setattr(
        plans,
        "resolve_execution_evidence",
        lambda payload, **_kwargs: (_protected_support_evidence(payload), "supported"),
    )
    catalog = create_catalog_snapshot(
        endpointRef="default",
        omnigentVersion="0.11.0",
        omnigentBuildDigest="sha256:" + "b" * 64,
        sourceDigest="sha256:" + "d" * 64,
        observedAt=datetime.now(UTC),
        harnesses=[
            {
                "id": "opencode-native",
                "aliases": [],
                "label": "OpenCode",
                "implementation": {
                    "sourceKind": "core",
                    "package": "omnigent",
                    "version": "0.11.0",
                    "digest": "sha256:" + "c" * 64,
                    "pluginEntryPoint": None,
                },
                "runtimeRequirements": {},
                "capabilities": {
                    "integrationMode": "native-server",
                    "authModel": "own-auth",
                    "interrupt": True,
                    "streaming": True,
                },
                "setupSteps": [],
            }
        ],
    )
    harness = catalog.harnesses[0]
    await DbHarnessCatalogRepository(sessions).persist(
        HarnessCatalogSyncResult(
            snapshot=catalog,
            trust_records=(
                classify_harness_trust(
                    harnessId=harness.id,
                    implementation=harness.implementation,
                    trustState=TrustState.core_trusted,
                ),
            ),
            diagnostics={},
        )
    )
    parameters = {
        "targetRuntime": "omnigent",
        "model": "example/model",
        "publishMode": "none",
        "maxAttempts": 2,
        "workflow": {
            "instructions": "Keep this admitted Skill on re-admission.",
            "skill": {"id": "saved-database-skill"},
        },
    }
    content = (
        "---\nname: saved-database-skill\n"
        "metadata:\n  required-skills: saved-database-helper\n---\n"
        "# Saved database Skill\nUse these admitted bytes.\n"
    )
    helper_content = "# Saved helper\nKeep the required Skill too.\n"
    async with sessions() as writer:
        artifacts = local_artifacts(writer, tmp_path)
        skill_service = AgentSkillsService(writer, artifact_service=artifacts)
        await skill_service.create_skill(
            slug="saved-database-helper", title="Saved helper"
        )
        helper = await skill_service.update_skill_content(
            skill_slug="saved-database-helper",
            content=helper_content,
            principal="user-1",
        )
        helper_ref = helper.artifact_ref
        skill = await skill_service.create_skill(
            slug="saved-database-skill", title="Saved database Skill"
        )
        skill = await skill_service.update_skill_content(
            skill_slug=skill.slug, content=content, principal="user-1"
        )
        skill_id, content_ref = skill.id, skill.artifact_ref
        task_ref, task_digest = await plans.persist_json_artifact(
            artifact_service=artifacts,
            principal="user-1",
            artifact_class="original_task_input_snapshot",
            payload={"initialParameters": parameters},
        )
        original = await _compile_opencode_plan(
            monkeypatch,
            artifacts=artifacts,
            launch_policy_ref="opencode-on-demand@1",
            plan_store=DbExecutionPlanStore(sessions),
            session_factory=sessions,
            workflow_id="mm:database-skill-original",
            task_input_snapshot_ref=task_ref,
            task_input_snapshot_digest=task_digest,
            extra_parameters=parameters,
        )
        _, profile_body = await artifacts.read(
            artifact_id=original.envelope.payload.agentProfileSnapshotRef.removeprefix(
                "artifact:"
            ),
            principal="user-1",
        )
        profile_snapshot = json.loads(profile_body)

    async with sessions() as caller:
        transaction = await caller.begin()
        current_skill = await caller.get(AgentSkillDefinition, skill_id)
        current_skill.description = "uncommitted caller-owned marker"
        await caller.flush()
        # Artifact persistence owns savepoints, just as in canonical create
        # admission; neither its commits nor Skill reads own this transaction.
        async with AsyncSession(
            bind=await caller.connection(),
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        ) as artifact_writer:
            artifacts = local_artifacts(artifact_writer, tmp_path)
            admitted = await plans.compile_frozen_execution_plan(
                db_session=caller,
                artifact_service=artifacts,
                workflow_id="mm:database-skill-readmission",
                principal="user-1",
                snapshot=profile_snapshot,
                provider_profile=SimpleNamespace(
                    profile_id="provider-opencode-native",
                    runtime_id="opencode",
                    provider_id="opencode-go",
                ),
                initial_parameters=parameters,
                binding=original.binding,
            )
            _, manifest = await artifacts.read(
                artifact_id=admitted.resolved_skillset_ref, principal="user-1"
            )
            assert {
                entry["skill_name"]: entry["content_ref"]
                for entry in json.loads(manifest)["skills"]
            } == {
                "saved-database-skill": content_ref,
                "saved-database-helper": helper_ref,
            }
            assert (await artifacts.read(artifact_id=content_ref, principal="user-1"))[
                1
            ] == content.encode()
            assert (await artifacts.read(artifact_id=helper_ref, principal="user-1"))[
                1
            ] == helper_content.encode()
            assert transaction.is_active
            assert caller.get_transaction() is transaction
            assert (
                await caller.scalar(
                    select(AgentSkillDefinition.description).where(
                        AgentSkillDefinition.id == skill_id
                    )
                )
                == "uncommitted caller-owned marker"
            )
        await transaction.rollback()
        # The same caller session remains usable, and no child commit leaked.
        assert (
            await caller.scalar(
                select(AgentSkillDefinition.description).where(
                    AgentSkillDefinition.id == skill_id
                )
            )
            is None
        )
    async with sessions() as verifier:
        assert (
            await verifier.get(OmnigentExecutionPlanRecord, admitted.binding.plan_ref)
            is None
        )
        assert (
            await verifier.get(AgentSkillDefinition, skill_id)
        ).artifact_ref == content_ref
