"""Unit tests for Temporal worker-topology bootstrap helpers."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import Base
from moonmind.config.settings import settings
from moonmind.workflows.skills.skill_dispatcher import SkillActivityDispatcher
from moonmind.workflows.temporal import (
    AGENT_RUNTIME_FLEET,
    ARTIFACTS_FLEET,
    INTEGRATIONS_FLEET,
    LLM_FLEET,
    SANDBOX_FLEET,
    WORKFLOW_FLEET,
    TemporalIntegrationActivities,
    TemporalPlanActivities,
    TemporalSandboxActivities,
    TemporalSkillActivities,
    build_default_activity_catalog,
)
from moonmind.workflows.temporal.artifacts import (
    LocalTemporalArtifactStore,
    TemporalArtifactActivities,
    TemporalArtifactRepository,
    TemporalArtifactService,
)
from moonmind.workflows.temporal.workers import (
    build_all_worker_topologies,
    build_worker_activity_bindings,
    describe_configured_worker,
    list_registered_workflow_types,
)


async def _artifact_service(
    tmp_path: Path,
) -> tuple[TemporalArtifactService, AsyncSession, Any]:
    db_url = f"sqlite+aiosqlite:///{tmp_path}/temporal_workers.db"
    engine = create_async_engine(db_url, future=True)
    session_maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session = session_maker()
    return (
        TemporalArtifactService(
            TemporalArtifactRepository(session),
            store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
        ),
        session,
        engine,
    )


def test_build_all_worker_topologies_covers_canonical_fleets():
    topologies = {
        topology.fleet: topology for topology in build_all_worker_topologies()
    }

    assert set(topologies) == {
        WORKFLOW_FLEET,
        ARTIFACTS_FLEET,
        LLM_FLEET,
        SANDBOX_FLEET,
        INTEGRATIONS_FLEET,
        AGENT_RUNTIME_FLEET,
    }
    assert topologies[WORKFLOW_FLEET].service_name == "temporal-worker-workflow"
    assert topologies[ARTIFACTS_FLEET].required_secrets == (
        "TEMPORAL_ARTIFACT_S3_ENDPOINT",
        "TEMPORAL_ARTIFACT_S3_BUCKET",
        "TEMPORAL_ARTIFACT_S3_ACCESS_KEY_ID",
        "TEMPORAL_ARTIFACT_S3_SECRET_ACCESS_KEY",
    )
    assert "OPENAI_API_KEY" in topologies[LLM_FLEET].required_secrets
    assert "JULES_API_KEY" in topologies[INTEGRATIONS_FLEET].required_secrets
    assert topologies[SANDBOX_FLEET].concurrency_limit == 2
    assert "mm.skill.execute" in topologies[ARTIFACTS_FLEET].activity_types
    assert "mm.skill.execute" in topologies[SANDBOX_FLEET].activity_types
    assert "mm.skill.execute" in topologies[INTEGRATIONS_FLEET].activity_types


def test_registered_workflow_types_include_manifest_ingest():
    assert list_registered_workflow_types() == (
        "MoonMind.Run",
        "MoonMind.ManifestIngest",
        "MoonMind.AuthProfileManager",
        "MoonMind.AgentRun",
    )


def test_describe_configured_worker_uses_temporal_worker_fleet_override():
    temporal_settings = settings.temporal.model_copy(
        update={
            "worker_fleet": SANDBOX_FLEET,
            "sandbox_worker_concurrency": 3,
        }
    )

    topology = describe_configured_worker(temporal_settings=temporal_settings)

    assert topology.fleet == SANDBOX_FLEET
    assert topology.task_queues == (settings.temporal.activity_sandbox_task_queue,)
    assert topology.concurrency_limit == 3
    assert topology.forbidden_capabilities == ("llm", "integration:jules", "agent_runtime")


def test_build_worker_activity_bindings_only_registers_selected_fleet(tmp_path: Path):
    async def _run() -> None:
        service, session, engine = await _artifact_service(tmp_path)
        try:
            catalog = build_default_activity_catalog()

            bindings = build_worker_activity_bindings(
                fleet=ARTIFACTS_FLEET,
                catalog=catalog,
                artifact_activities=TemporalArtifactActivities(service),
                plan_activities=TemporalPlanActivities(artifact_service=service),
                skill_activities=TemporalSkillActivities(
                    dispatcher=SkillActivityDispatcher()
                ),
                sandbox_activities=TemporalSandboxActivities(artifact_service=service),
                integration_activities=TemporalIntegrationActivities(
                    artifact_service=service,
                    client_factory=lambda: None,
                ),
            )

            assert bindings
            assert {binding.fleet for binding in bindings} == {ARTIFACTS_FLEET}
            assert "mm.skill.execute" in {binding.activity_type for binding in bindings}
            assert {binding.task_queue for binding in bindings} == {
                settings.temporal.activity_artifacts_task_queue
            }
        finally:
            await session.close()
            await engine.dispose()

    asyncio.run(_run())


def test_build_worker_activity_bindings_registers_mm_skill_execute_on_sandbox_fleet(
    tmp_path: Path,
):
    async def _run() -> None:
        service, session, engine = await _artifact_service(tmp_path)
        try:
            bindings = build_worker_activity_bindings(
                fleet=SANDBOX_FLEET,
                catalog=build_default_activity_catalog(),
                artifact_activities=TemporalArtifactActivities(service),
                plan_activities=TemporalPlanActivities(artifact_service=service),
                skill_activities=TemporalSkillActivities(
                    dispatcher=SkillActivityDispatcher()
                ),
                sandbox_activities=TemporalSandboxActivities(artifact_service=service),
                integration_activities=TemporalIntegrationActivities(
                    artifact_service=service,
                    client_factory=lambda: None,
                ),
            )

            mm_skill_bindings = [
                binding
                for binding in bindings
                if binding.activity_type == "mm.skill.execute"
            ]
            assert len(mm_skill_bindings) == 1
            assert (
                mm_skill_bindings[0].task_queue
                == settings.temporal.activity_sandbox_task_queue
            )
        finally:
            await session.close()
            await engine.dispose()

    asyncio.run(_run())
