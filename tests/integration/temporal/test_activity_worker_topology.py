"""Integration-style verification for the Temporal activity worker topology."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import Base
from moonmind.workflows.agent_skills.agent_skills_activities import AgentSkillsActivities
from moonmind.workflows.skills.skill_dispatcher import SkillActivityDispatcher
from moonmind.workflows.skills.skill_plan_contracts import SkillResult
from moonmind.workflows.temporal import (
    AGENT_RUNTIME_FLEET,
    ARTIFACTS_FLEET,
    LLM_FLEET,
    SANDBOX_FLEET,
    ExecutionRef,
    LocalTemporalArtifactStore,
    TemporalArtifactActivities,
    TemporalArtifactRepository,
    TemporalArtifactService,
    TemporalIntegrationActivities,
    TemporalManifestActivities,
    TemporalPlanActivities,
    TemporalSandboxActivities,
    TemporalSkillActivities,
    build_default_activity_catalog,
)
from moonmind.workflows.temporal.activity_runtime import (
    TemporalAgentRuntimeActivities,
    TemporalProposalActivities,
    TemporalReviewActivities,
)
from moonmind.workflows.temporal.workers import build_worker_activity_bindings

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]

@asynccontextmanager
async def _db(tmp_path: Path):
    db_url = f"sqlite+aiosqlite:///{tmp_path}/temporal_worker_topology.db"
    engine = create_async_engine(db_url, future=True)
    session_maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield session_maker
    finally:
        await engine.dispose()

class _UnusedIntegrationClient:
    async def aclose(self) -> None:
        return None

def _registry_payload() -> dict[str, object]:
    return {
        "skills": [
            {
                "name": "repo.run_tests",
                "version": "1.0.0",
                "description": "Run tests",
                "inputs": {"schema": {"type": "object", "properties": {}}},
                "outputs": {"schema": {"type": "object", "properties": {}}},
                "executor": {
                    "activity_type": "mm.skill.execute",
                    "selector": {"mode": "by_capability"},
                },
                "requirements": {"capabilities": ["sandbox"]},
                "policies": {
                    "timeouts": {
                        "start_to_close_seconds": 30,
                        "schedule_to_close_seconds": 60,
                    },
                    "retries": {"max_attempts": 1},
                },
            }
        ]
    }

def _planner(_inputs, _parameters, _snapshot):
    return {
        "plan_version": "1.0",
        "metadata": {
            "title": "Generated Plan",
            "created_at": "2026-03-06T00:00:00Z",
            "registry_snapshot": {
                "digest": "reg:sha256:" + ("a" * 64),
                "artifact_ref": "art_01HJ4M3Y7RM4C5S2P3Q8G6T7V8",
            },
        },
        "policy": {"failure_mode": "FAIL_FAST", "max_concurrency": 1},
        "nodes": [
            {
                "id": "n1",
                "skill": {"name": "repo.run_tests", "version": "1.0.0"},
                "inputs": {"repo_ref": "git:org/repo#main"},
            }
        ],
        "edges": [],
    }

async def test_activity_worker_topology_routes_one_activity_per_family(
    tmp_path: Path,
) -> None:
    async with _db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            catalog = build_default_activity_catalog()
            dispatcher = SkillActivityDispatcher()
            dispatcher.register_skill(
                skill_name="repo.run_tests",
                version="1.0.0",
                handler=lambda inputs, _context: SkillResult(
                    status="COMPLETED",
                    outputs={"repo_ref": inputs["repo_ref"]},
                ),
            )

            artifact_bindings = {
                binding.activity_type: binding
                for binding in build_worker_activity_bindings(
                    fleet=ARTIFACTS_FLEET,
                    catalog=catalog,
                    artifact_activities=TemporalArtifactActivities(service),
                    manifest_activities=TemporalManifestActivities(
                        artifact_service=service,
                    ),
                    plan_activities=TemporalPlanActivities(
                        artifact_service=service,
                        planner=_planner,
                    ),
                    skill_activities=TemporalSkillActivities(dispatcher=dispatcher),
                    sandbox_activities=TemporalSandboxActivities(
                        artifact_service=service,
                        workspace_root=tmp_path / "workspaces",
                    ),
                    integration_activities=TemporalIntegrationActivities(
                        artifact_service=service,
                        client_factory=_UnusedIntegrationClient,
                    ),
                    proposal_activities=TemporalProposalActivities(
                        artifact_service=service,
                    ),
                    review_activities=TemporalReviewActivities(),
                )
            }
            artifact_ref, _upload = await artifact_bindings["artifact.create"].handler(
                {"principal": "user-1", "content_type": "text/plain"}
            )
            assert (
                artifact_bindings["artifact.create"].task_queue
                == "mm.activity.artifacts"
            )
            assert "oauth_session.start_auth_runner" not in artifact_bindings
            assert artifact_ref.artifact_id.startswith("art_")

            llm_bindings = {
                binding.activity_type: binding
                for binding in build_worker_activity_bindings(
                    fleet=LLM_FLEET,
                    catalog=catalog,
                    artifact_activities=TemporalArtifactActivities(service),
                    plan_activities=TemporalPlanActivities(
                        artifact_service=service,
                        planner=_planner,
                    ),
                    skill_activities=TemporalSkillActivities(dispatcher=dispatcher),
                    sandbox_activities=TemporalSandboxActivities(
                        artifact_service=service,
                        workspace_root=tmp_path / "workspaces",
                    ),
                    integration_activities=TemporalIntegrationActivities(
                        artifact_service=service,
                        client_factory=_UnusedIntegrationClient,
                    ),
                    proposal_activities=TemporalProposalActivities(
                        artifact_service=service,
                    ),
                    review_activities=TemporalReviewActivities(),
                )
            }
            registry_artifact, _upload = await service.create(
                principal="user-1",
                content_type="application/json",
            )
            await service.write_complete(
                artifact_id=registry_artifact.artifact_id,
                principal="user-1",
                payload=(json.dumps(_registry_payload()) + "\n").encode("utf-8"),
                content_type="application/json",
            )
            inputs_artifact, _upload = await service.create(
                principal="user-1",
                content_type="application/json",
            )
            await service.write_complete(
                artifact_id=inputs_artifact.artifact_id,
                principal="user-1",
                payload=(json.dumps({"goal": "run tests"}) + "\n").encode("utf-8"),
                content_type="application/json",
            )
            plan_result = await llm_bindings["plan.generate"].handler(
                {
                    "principal": "user-1",
                    "inputs_ref": inputs_artifact.artifact_id,
                    "registry_snapshot_ref": registry_artifact.artifact_id,
                    "execution_ref": ExecutionRef(
                        namespace="moonmind",
                        workflow_id="wf-plan",
                        run_id="run-plan",
                        link_type="output.primary",
                    ),
                }
            )
            assert llm_bindings["plan.generate"].task_queue == "mm.activity.llm"
            assert plan_result.plan_ref.artifact_id.startswith("art_")

            sandbox_bindings = {
                binding.activity_type: binding
                for binding in build_worker_activity_bindings(
                    fleet=SANDBOX_FLEET,
                    catalog=catalog,
                    artifact_activities=TemporalArtifactActivities(service),
                    plan_activities=TemporalPlanActivities(
                        artifact_service=service,
                        planner=_planner,
                    ),
                    skill_activities=TemporalSkillActivities(dispatcher=dispatcher),
                    sandbox_activities=TemporalSandboxActivities(
                        artifact_service=service,
                        workspace_root=tmp_path / "workspaces",
                    ),
                    integration_activities=TemporalIntegrationActivities(
                        artifact_service=service,
                        client_factory=_UnusedIntegrationClient,
                    ),
                    proposal_activities=TemporalProposalActivities(
                        artifact_service=service,
                    ),
                    review_activities=TemporalReviewActivities(),
                )
            }
            workspace = tmp_path / "workspaces" / "temporal_sandbox" / "workspace"
            workspace.mkdir(parents=True, exist_ok=True)
            sandbox_result = await sandbox_bindings["sandbox.run_command"].handler(
                {
                    "workspace_ref": workspace,
                    "cmd": "printf 'sandbox ok'",
                    "principal": "user-1",
                }
            )
            assert (
                sandbox_bindings["sandbox.run_command"].task_queue
                == "mm.activity.sandbox"
            )
            assert (
                sandbox_bindings["mm.skill.execute"].task_queue == "mm.activity.sandbox"
            )
            assert sandbox_result.exit_code == 0

            agent_runtime_bindings = {
                binding.activity_type: binding
                for binding in build_worker_activity_bindings(
                    fleet=AGENT_RUNTIME_FLEET,
                    catalog=catalog,
                    artifact_activities=TemporalArtifactActivities(service),
                    plan_activities=TemporalPlanActivities(
                        artifact_service=service,
                        planner=_planner,
                    ),
                    skill_activities=TemporalSkillActivities(dispatcher=dispatcher),
                    sandbox_activities=TemporalSandboxActivities(
                        artifact_service=service,
                        workspace_root=tmp_path / "workspaces",
                    ),
                    integration_activities=TemporalIntegrationActivities(
                        artifact_service=service,
                        client_factory=_UnusedIntegrationClient,
                    ),
                    proposal_activities=TemporalProposalActivities(
                        artifact_service=service,
                    ),
                    review_activities=TemporalReviewActivities(),
                    agent_runtime_activities=TemporalAgentRuntimeActivities(),
                    agent_skills_activities=AgentSkillsActivities(),
                )
            }
            assert (
                agent_runtime_bindings["oauth_session.start_auth_runner"].task_queue
                == "mm.activity.agent_runtime"
            )
            assert (
                agent_runtime_bindings["oauth_session.ensure_volume"].task_queue
                == "mm.activity.agent_runtime"
            )
