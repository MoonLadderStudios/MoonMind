"""Unit tests for Temporal worker-topology bootstrap helpers."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from temporalio import workflow

from api_service.db.models import Base
from moonmind.config.settings import settings
from moonmind.workflows.skills.skill_dispatcher import SkillActivityDispatcher
from moonmind.workflows.temporal import (
    AGENT_RUNTIME_FLEET,
    ARTIFACTS_FLEET,
    DEPLOYMENT_FLEET,
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
from moonmind.workflows.temporal import activity_runtime
from moonmind.workflows.temporal.activity_runtime import (
    TemporalActivityRuntimeError,
    TemporalProposalActivities,
)
from moonmind.workflows.temporal.activity_runtime import TemporalAgentRuntimeActivities
from moonmind.workflows.temporal.activity_runtime import TemporalManifestActivities
from moonmind.workflows.temporal.publish_auto_evidence import parse_auto_publish_evidence
from moonmind.workflows.agent_skills.agent_skills_activities import AgentSkillsActivities
from moonmind.workflows.temporal.workers import (
    build_all_worker_topologies,
    build_worker_activity_bindings,
    build_worker_spec,
    build_worker_topology,
    describe_configured_worker,
    list_registered_workflow_types,
)
from moonmind.workflows.temporal.workflow_registry import (
    workflow_fleet_activity_handlers,
    workflow_fleet_workflow_classes,
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
        DEPLOYMENT_FLEET,
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
    assert topologies[DEPLOYMENT_FLEET].service_name == (
        "temporal-worker-deployment-control"
    )
    assert topologies[DEPLOYMENT_FLEET].activity_types == ("mm.tool.execute",)


def test_workflow_worker_startup_rejects_unroutable_activity_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(
        activity_runtime._ACTIVITY_HANDLER_ATTRS,
        "agent_runtime.unroutable_test_handler",
        ("agent_runtime", "unroutable_test_handler"),
    )

    with pytest.raises(
        TemporalActivityRuntimeError,
        match="handlers without catalog routes: agent_runtime.unroutable_test_handler",
    ):
        build_worker_topology(fleet=WORKFLOW_FLEET)


def test_registered_workflow_types_include_manifest_ingest():
    assert list_registered_workflow_types() == (
        "MoonMind.UserWorkflow",
        "MoonMind.ContainerJob",
        "MoonMind.ManifestIngest",
        "MoonMind.ProviderProfileManager",
        "MoonMind.AgentSession",
        "MoonMind.ManagedSessionReconcile",
        "MoonMind.ManagedRuntimeWorkspaceCleanup",
        "MoonMind.AgentRun",
        "MoonMind.OAuthSession",
        "MoonMind.OmnigentOAuthHostJanitor",
        "MoonMind.MergeAutomation",
        "MoonMind.PRResolver",
        "MoonMind.PublicationRecoveryV1",
    )


def test_publication_recovery_activity_routes_are_registered_by_authority() -> None:
    expected = {
        "integrations": {
            "publication_recovery.observe",
            "publication_recovery.publish",
            "publication_recovery.verify",
        },
        "agent_runtime": {
            "publication_recovery.restore_candidate",
            "publication_recovery.cleanup",
        },
        "artifacts": {"publication_recovery.persist_result"},
    }

    for fleet, activity_types in expected.items():
        topology = build_worker_topology(fleet=fleet)
        assert activity_types <= set(topology.activity_types)


def test_advertised_workflow_types_match_production_worker_classes():
    registered_class_names = tuple(
        workflow._Definition.must_from_class(workflow_class).name
        for workflow_class in workflow_fleet_workflow_classes()
    )

    assert registered_class_names == list_registered_workflow_types()


def test_production_worker_classes_are_cached():
    assert workflow_fleet_workflow_classes() is workflow_fleet_workflow_classes()


def test_executable_worker_spec_drives_registration_and_stable_identity() -> None:
    topology = build_worker_topology(fleet=WORKFLOW_FLEET)
    kwargs = {
        "topology": topology,
        "workflows": workflow_fleet_workflow_classes(),
        "activities": workflow_fleet_activity_handlers(),
        "environ": {
            "MOONMIND_BUILD_SHA": "abc123",
            "MOONMIND_IMAGE_DIGEST": "sha256:image",
            "TEMPORAL_WORKER_DEPLOYMENT_NAME": "moonmind-test",
            "TEMPORAL_WORKER_VERSIONING_ENABLED": "true",
        },
    }
    first = build_worker_spec(**kwargs)
    second = build_worker_spec(**kwargs)
    assert first.registry_fingerprint == second.registry_fingerprint
    assert first.workflow_types == list_registered_workflow_types()
    assert "MoonMind.PRResolver" in first.readiness_payload()["workflowTypes"]
    assert "MoonMind.PublicationRecoveryV1" in first.readiness_payload()[
        "workflowTypes"
    ]
    assert first.versioning_enabled is True
    assert first.build_id == "abc123"

    alternate_lane = build_worker_spec(
        topology=replace(topology, task_queues=("mm.workflow.merge_automation",)),
        workflows=workflow_fleet_workflow_classes(),
        activities=workflow_fleet_activity_handlers(),
        environ=kwargs["environ"],
    )
    assert alternate_lane.registry_fingerprint == first.registry_fingerprint


def test_production_worker_spec_requires_immutable_release_identity() -> None:
    topology = build_worker_topology(fleet=WORKFLOW_FLEET)
    with pytest.raises(ValueError, match="MOONMIND_BUILD_SHA"):
        build_worker_spec(
            topology=topology,
            workflows=workflow_fleet_workflow_classes(),
            activities=workflow_fleet_activity_handlers(),
            environ={"MOONMIND_DEPLOYMENT_MODE": "production"},
        )


def test_production_worker_spec_requires_temporal_worker_versioning() -> None:
    topology = build_worker_topology(fleet=WORKFLOW_FLEET)
    with pytest.raises(ValueError, match="TEMPORAL_WORKER_VERSIONING_ENABLED"):
        build_worker_spec(
            topology=topology,
            workflows=workflow_fleet_workflow_classes(),
            activities=workflow_fleet_activity_handlers(),
            environ={
                "MOONMIND_DEPLOYMENT_MODE": "production",
                "MOONMIND_BUILD_SHA": "abc123",
            },
        )


def test_pr_resolver_terminal_publication_is_idempotent(tmp_path: Path):
    async def _run() -> None:
        service, session, engine = await _artifact_service(tmp_path)
        try:
            activities = TemporalArtifactActivities(service)
            request = {
                "principal": "resolver-user",
                "idempotencyKey": "resolver:wf:terminal:merged",
                "executionRef": {
                    "namespace": "default",
                    "workflow_id": "resolver-wf",
                    "run_id": "resolver-run",
                },
                "terminalResult": {
                    "status": "merged",
                    "mergeAutomationDisposition": "merged",
                    "repository": "MoonLadderStudios/MoonMind",
                    "prUrl": "https://github.com/MoonLadderStudios/MoonMind/pull/3150",
                    "verifiedHeadSha": "abc",
                    "reasonCode": "merged",
                },
            }

            first = await activities.pr_resolver_write_terminal_result(request)
            second = await activities.pr_resolver_write_terminal_result(request)

            assert second == first
            _artifact, publish_payload = await service.read(
                artifact_id=first["publishEvidenceRef"],
                principal="resolver-user",
            )
            evidence = parse_auto_publish_evidence(publish_payload)
            assert evidence.merged is True
            assert evidence.action == "merge"
            artifacts = await service.list_for_execution(
                namespace="default",
                workflow_id="resolver-wf",
                run_id="resolver-run",
                principal="resolver-user",
            )
            assert len(artifacts) == 2
        finally:
            await session.close()
            await engine.dispose()

    asyncio.run(_run())

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
    assert topology.forbidden_capabilities == (
        "llm",
        "integration:jules",
        "integration:openclaw",
        "integration:omnigent",
        "agent_runtime",
        "docker_workload",
    )

def test_agent_runtime_topology_exposes_docker_workload_capability():
    topology = describe_configured_worker(
        temporal_settings=settings.temporal.model_copy(
            update={"worker_fleet": AGENT_RUNTIME_FLEET}
        )
    )

    assert "docker_workload" in topology.capabilities
    assert "workload.run" in topology.activity_types
    assert "oauth_session.start_auth_runner" in topology.activity_types
    assert "oauth_session.verify_volume" in topology.activity_types


def test_deployment_topology_exposes_deployment_control_capability():
    topology = describe_configured_worker(
        temporal_settings=settings.temporal.model_copy(
            update={"worker_fleet": DEPLOYMENT_FLEET}
        )
    )

    assert topology.task_queues == (settings.temporal.activity_deployment_task_queue,)
    assert topology.concurrency_limit == 1
    assert "deployment_control" in topology.capabilities
    assert "docker_admin" in topology.capabilities
    assert "mm.tool.execute" in topology.activity_types
    assert "workload.run" not in topology.activity_types

def test_build_worker_activity_bindings_only_registers_selected_fleet(tmp_path: Path):
    async def _run() -> None:
        service, session, engine = await _artifact_service(tmp_path)
        try:
            catalog = build_default_activity_catalog()

            bindings = build_worker_activity_bindings(
                fleet=ARTIFACTS_FLEET,
                catalog=catalog,
                artifact_activities=TemporalArtifactActivities(service),
                manifest_activities=TemporalManifestActivities(
                    artifact_service=service,
                ),
                plan_activities=TemporalPlanActivities(artifact_service=service),
                skill_activities=TemporalSkillActivities(
                    dispatcher=SkillActivityDispatcher()
                ),
                sandbox_activities=TemporalSandboxActivities(artifact_service=service),
                integration_activities=TemporalIntegrationActivities(
                    artifact_service=service,
                    client_factory=lambda: None,
                ),
                proposal_activities=TemporalProposalActivities(
                    artifact_service=service,
                ),
                agent_skills_activities=AgentSkillsActivities(),
            )

            assert bindings
            assert {binding.fleet for binding in bindings} == {ARTIFACTS_FLEET}
            activity_types = {binding.activity_type for binding in bindings}
            assert "mm.skill.execute" in activity_types
            assert "oauth_session.update_status" in activity_types
            assert "oauth_session.mark_failed" in activity_types
            assert "oauth_session.cleanup_stale" in activity_types
            assert "oauth_session.start_auth_runner" not in activity_types
            assert "manifest.compile" in activity_types
            assert "manifest.write_summary" in activity_types
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
                proposal_activities=TemporalProposalActivities(
                    artifact_service=service,
                ),
                agent_skills_activities=AgentSkillsActivities(),
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

def test_build_worker_activity_bindings_registers_workload_run_on_agent_runtime_fleet(
    tmp_path: Path,
):
    async def _run() -> None:
        service, session, engine = await _artifact_service(tmp_path)
        try:
            bindings = build_worker_activity_bindings(
                fleet=AGENT_RUNTIME_FLEET,
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
                agent_runtime_activities=TemporalAgentRuntimeActivities(),
                proposal_activities=TemporalProposalActivities(
                    artifact_service=service,
                ),
                agent_skills_activities=AgentSkillsActivities(),
            )

            workload_bindings = [
                binding for binding in bindings if binding.activity_type == "workload.run"
            ]
            reconcile_bindings = [
                binding
                for binding in bindings
                if binding.activity_type == "agent_runtime.reconcile_managed_sessions"
            ]
            cleanup_bindings = [
                binding
                for binding in bindings
                if binding.activity_type == "agent_runtime.cleanup_managed_runtime_files"
            ]
            oauth_runner_bindings = [
                binding
                for binding in bindings
                if binding.activity_type == "oauth_session.start_auth_runner"
            ]
            assert len(workload_bindings) == 1
            assert len(reconcile_bindings) == 1
            assert len(cleanup_bindings) == 1
            assert len(oauth_runner_bindings) == 1
            assert (
                workload_bindings[0].task_queue
                == settings.temporal.activity_agent_runtime_task_queue
            )
            assert (
                reconcile_bindings[0].task_queue
                == settings.temporal.activity_agent_runtime_task_queue
            )
            assert (
                cleanup_bindings[0].task_queue
                == settings.temporal.activity_agent_runtime_task_queue
            )
            assert (
                oauth_runner_bindings[0].task_queue
                == settings.temporal.activity_agent_runtime_task_queue
            )
        finally:
            await session.close()
            await engine.dispose()

    asyncio.run(_run())
