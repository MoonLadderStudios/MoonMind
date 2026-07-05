"""Unit tests for Temporal activity-family runtime helpers."""

from __future__ import annotations

import asyncio
import json
import re
import stat
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from temporalio import activity as temporal_activity
from temporalio import exceptions as temporal_exceptions

from api_service.db.models import Base
from moonmind.config.settings import PentestSettings, settings
from moonmind.integrations.pentest.models import (
    PENTEST_CLAUDE_OAUTH_PROFILE_ID,
    PENTEST_CLAUDE_OAUTH_RUNNER_PROFILE_ID,
)
from moonmind.jules.runtime import JULES_RUNTIME_DISABLED_MESSAGE
from moonmind.schemas.agent_runtime_models import AgentRunResult, ManagedRunRecord
from moonmind.schemas.jules_models import JulesTaskResponse
from moonmind.schemas.workload_models import (
    ValidatedWorkloadRequest,
    WorkloadResult,
)
from moonmind.workflows.skills.artifact_store import InMemoryArtifactStore
from moonmind.workflows.skills.skill_dispatcher import SkillActivityDispatcher
from moonmind.workflows.skills.skill_plan_contracts import SkillResult
from moonmind.workflows.skills.skill_registry import (
    create_registry_snapshot,
    parse_skill_registry,
)
from moonmind.workflows.skills.tool_plan_contracts import ToolFailure
from moonmind.workflows.skills.deployment_tools import (
    DEPLOYMENT_UPDATE_TOOL_NAME,
    DEPLOYMENT_UPDATE_TOOL_VERSION,
)
from moonmind.workflows.temporal import activity_runtime as activity_runtime_module
from moonmind.workflows.temporal.activity_catalog import (
    AGENT_RUNTIME_FLEET,
    ARTIFACTS_FLEET,
    ARTIFACTS_TASK_QUEUE,
    DEPLOYMENT_FLEET,
    INTEGRATIONS_FLEET,
    SANDBOX_FLEET,
    SANDBOX_TASK_QUEUE,
    TemporalActivityCatalog,
    TemporalActivityDefinition,
    TemporalActivityRetries,
    TemporalActivityTimeouts,
    TemporalWorkerFleet,
    build_default_activity_catalog,
)
from moonmind.workflows.temporal.activity_runtime import (
    SandboxCommandResult,
    TemporalActivityRuntimeError,
    TemporalAgentRuntimeActivities,
    TemporalCheckpointActivities,
    TemporalIntegrationActivities,
    TemporalManifestActivities,
    TemporalPlanActivities,
    TemporalProposalActivities,
    TemporalSandboxActivities,
    TemporalSkillActivities,
    TemporalPentestProviderLeaseManager,
    _default_registry_skill_payload,
    _default_skill_registry_payload,
    build_activity_bindings,
    build_activity_execution_context,
    build_activity_invocation_envelope,
    build_compact_activity_result,
    build_observability_summary,
    cleanup_pentest_orphan_containers,
    emit_pentest_activity_heartbeat,
)
from moonmind.workflows.agent_skills.agent_skills_activities import AgentSkillsActivities
from moonmind.workflows.temporal.artifacts import (
    ExecutionRef,
    LocalTemporalArtifactStore,
    TemporalArtifactActivities,
    TemporalArtifactNotFoundError,
    TemporalArtifactRepository,
    TemporalArtifactService,
    TemporalArtifactValidationError,
    build_artifact_ref,
)
from moonmind.workflows.temporal.report_artifacts import validate_report_bundle_result
from moonmind.workflows.temporal.runtime.store import ManagedRunStore
from moonmind.workloads.registry import RunnerProfileRegistry

PENTEST_RUNNER_IMAGE = "ghcr.io/moonladderstudios/moonmind-pentestgpt:1.0"

pytestmark = [pytest.mark.asyncio]


async def test_prepare_managed_codex_turn_adds_moonspec_verify_artifact_hint() -> None:
    prepared = TemporalAgentRuntimeActivities._prepare_managed_codex_turn_text(
        "Run moonspec-verify.",
        parameters={
            "metadata": {"moonmind": {"selectedSkill": "moonspec-verify"}},
            "verify_artifact_path": "var/artifacts/moonspec-verify/verify-final.json",
        },
    )

    assert "MoonSpec verification output contract:" in prepared
    assert "var/artifacts/moonspec-verify/verify-final.json" in prepared
    assert "complete structured verifier JSON" in prepared
    # The hint must enumerate the enforced vocabularies so the verifier agent
    # cannot drift into non-canonical values the gate would fail closed on.
    assert '"FULLY_IMPLEMENTED"' in prepared
    assert '"BLOCKED"' in prepared
    assert '"advance"' in prepared
    assert '"reattempt_current_step"' in prepared
    assert "create_pull_request" in prepared


async def test_prepare_managed_codex_turn_appends_vocab_when_path_already_present() -> None:
    path = "var/artifacts/moonspec-verify/verify-final.json"
    prepared = TemporalAgentRuntimeActivities._prepare_managed_codex_turn_text(
        f"Run moonspec-verify and write JSON to `{path}`.",
        parameters={
            "metadata": {"moonmind": {"selectedSkill": "moonspec-verify"}},
            "verify_artifact_path": path,
        },
    )

    assert "MoonSpec verification output contract:" in prepared
    assert prepared.count("complete structured verifier JSON") == 0
    assert '"FULLY_IMPLEMENTED"' in prepared
    assert '"advance"' in prepared
    assert "create_pull_request" in prepared


async def test_codex_skill_payload_rejects_auto_publish_mode() -> None:
    from moonmind.agents.codex_worker.handlers import (
        CodexSkillPayload,
        CodexWorkerHandlerError,
    )

    with pytest.raises(CodexWorkerHandlerError, match="codex_skill publishMode"):
        CodexSkillPayload.from_payload(
            {
                "skillId": "fix-ci",
                "inputs": {
                    "repo": "MoonLadderStudios/MoonMind",
                    "publishMode": "auto",
                },
            }
        )


async def test_checkpoint_activity_runtime_bindings_are_registered() -> None:
    catalog = TemporalActivityCatalog(
        activities=(
            TemporalActivityDefinition(
                "step_checkpoint.create",
                "step_checkpoint",
                "artifacts",
                ARTIFACTS_TASK_QUEUE,
                ARTIFACTS_FLEET,
                TemporalActivityTimeouts(10, 20),
                TemporalActivityRetries(1, 10),
            ),
            TemporalActivityDefinition(
                "step_checkpoint.validate",
                "step_checkpoint",
                "artifacts",
                ARTIFACTS_TASK_QUEUE,
                ARTIFACTS_FLEET,
                TemporalActivityTimeouts(10, 20),
                TemporalActivityRetries(1, 10),
            ),
            TemporalActivityDefinition(
                "workspace.capture_checkpoint",
                "workspace",
                "sandbox",
                SANDBOX_TASK_QUEUE,
                SANDBOX_FLEET,
                TemporalActivityTimeouts(10, 20),
                TemporalActivityRetries(1, 10),
            ),
            TemporalActivityDefinition(
                "workspace.apply_policy",
                "workspace",
                "sandbox",
                SANDBOX_TASK_QUEUE,
                SANDBOX_FLEET,
                TemporalActivityTimeouts(10, 20),
                TemporalActivityRetries(1, 10),
            ),
            TemporalActivityDefinition(
                "workspace.classify_git_effect",
                "workspace",
                "sandbox",
                SANDBOX_TASK_QUEUE,
                SANDBOX_FLEET,
                TemporalActivityTimeouts(10, 20),
                TemporalActivityRetries(1, 10),
            ),
        ),
        fleets=(
            TemporalWorkerFleet(
                ARTIFACTS_FLEET,
                (ARTIFACTS_TASK_QUEUE,),
                ("artifacts",),
                ("artifact_store",),
                "test",
                ("step_checkpoint.create", "step_checkpoint.validate"),
            ),
            TemporalWorkerFleet(
                SANDBOX_FLEET,
                (SANDBOX_TASK_QUEUE,),
                ("sandbox",),
                ("workspace",),
                "test",
                (
                    "workspace.capture_checkpoint",
                    "workspace.apply_policy",
                    "workspace.classify_git_effect",
                ),
            ),
        ),
    )
    class _ArtifactImplementation:
        async def __getattr__(self, _name: str):
            raise AttributeError(f"unexpected dynamic lookup: {_name}")

        async def artifact_create(self):
            pass

        async def artifact_write_complete(self):
            pass

        async def artifact_publish_report_bundle(self):
            pass

        async def artifact_read(self):
            pass

        async def execution_dependency_status_snapshot(self):
            pass

        async def execution_record_terminal_state(self):
            pass

        async def artifact_list_for_execution(self):
            pass

        async def artifact_compute_preview(self):
            pass

        async def artifact_link(self):
            pass

        async def artifact_pin(self):
            pass

        async def artifact_unpin(self):
            pass

        async def artifact_lifecycle_sweep(self):
            pass

        async def step_checkpoint_create(self):
            pass

        async def step_checkpoint_validate(self):
            pass

    artifacts = _ArtifactImplementation()
    sandbox = TemporalSandboxActivities(
        artifact_store=InMemoryArtifactStore(),
        workspace_root=Path("/tmp/moonmind-test-workspaces"),
    )

    bindings = {
        binding.activity_type: binding
        for binding in build_activity_bindings(
            catalog,
            artifact_activities=artifacts,
            sandbox_activities=sandbox,
            fleets=(ARTIFACTS_FLEET, SANDBOX_FLEET),
        )
    }

    assert bindings["step_checkpoint.create"].fleet == ARTIFACTS_FLEET
    assert bindings["step_checkpoint.validate"].fleet == ARTIFACTS_FLEET
    assert bindings["workspace.capture_checkpoint"].fleet == SANDBOX_FLEET
    assert bindings["workspace.apply_policy"].fleet == SANDBOX_FLEET
    assert bindings["workspace.classify_git_effect"].fleet == SANDBOX_FLEET

@asynccontextmanager
async def temporal_db(tmp_path: Path):
    db_url = f"sqlite+aiosqlite:///{tmp_path}/temporal_activity_runtime.db"
    engine = create_async_engine(db_url, future=True)
    session_maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    try:
        yield session_maker
    finally:
        await engine.dispose()

def _registry_payload() -> dict:
    return {
        "skills": [
            {
                "name": "repo.run_tests",
                "description": "Run tests",
                "inputs": {
                    "schema": {
                        "type": "object",
                        "required": ["repo_ref"],
                        "properties": {"repo_ref": {"type": "string"}},
                    }
                },
                "outputs": {
                    "schema": {
                        "type": "object",
                        "required": ["ok"],
                        "properties": {"ok": {"type": "boolean"}},
                    }
                },
                "executor": {
                    "activity_type": "mm.skill.execute",
                    "selector": {"mode": "by_capability"},
                },
                "requirements": {"capabilities": ["sandbox"]},
                "policies": {
                    "timeouts": {
                        "start_to_close_seconds": 30,
                        "schedule_to_close_seconds": 120,
                    },
                    "retries": {"max_attempts": 2},
                },
            }
        ]
    }

def _plan_payload(*, registry_artifact_id: str, registry_digest: str) -> dict:
    return {
        "plan_version": "1.0",
        "metadata": {
            "title": "Fix tests",
            "created_at": "2026-03-05T00:00:00Z",
            "registry_snapshot": {
                "digest": registry_digest,
                "artifact_ref": registry_artifact_id,
            },
        },
        "policy": {"failure_mode": "FAIL_FAST", "max_concurrency": 1},
        "nodes": [
            {
                "id": "n1",
                "skill": {"name": "repo.run_tests"},
                "inputs": {"repo_ref": "git:org/repo#main"},
            }
        ],
        "edges": [],
    }

class _FakeJulesClient:
    def __init__(
        self,
        *,
        create_status: str = "pending",
        get_status: str = "completed",
        get_pull_request_url: str | None = None,
    ) -> None:
        self.created: list[object] = []
        self.lookups: list[object] = []
        self.closed = False
        self._create_status = create_status
        self._get_status = get_status
        self._get_pull_request_url = get_pull_request_url

    async def create_task(self, request):
        self.created.append(request)
        return JulesTaskResponse(
            task_id="task-001",
            status=self._create_status,
            url="https://jules.test/task-001",
        )

    async def get_task(self, request):
        self.lookups.append(request)
        outputs = []
        if self._get_pull_request_url is not None:
            outputs = [{"pullRequest": {"url": self._get_pull_request_url}}]
        return JulesTaskResponse(
            task_id=request.task_id,
            status=self._get_status,
            url="https://jules.test/task-001",
            outputs=outputs,
        )

    async def aclose(self) -> None:
        self.closed = True

async def test_artifact_activity_create_returns_ref_and_upload_descriptor(
    tmp_path: Path,
):
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            activities = TemporalArtifactActivities(service)

            artifact_ref, upload = await activities.artifact_create(
                principal="user-1",
                content_type="text/plain",
            )

            assert artifact_ref.artifact_id.startswith("art_")
            assert upload.mode == "single_put"

async def test_artifact_activity_create_maps_legacy_name_to_metadata(
    tmp_path: Path,
):
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            activities = TemporalArtifactActivities(service)

            artifact_ref, _upload = await activities.artifact_create(
                principal="user-1",
                content_type="application/json",
                name="reports/run_summary.json",
                metadata_json={"artifact_kind": "summary"},
            )
            artifact, _links, _pinned, _policy = await service.get_metadata(
                artifact_id=artifact_ref.artifact_id,
                principal="user-1",
            )

            assert artifact.metadata_json["name"] == "reports/run_summary.json"
            assert artifact.metadata_json["artifact_kind"] == "summary"

async def test_artifact_create_binding_accepts_legacy_name_payload(
    tmp_path: Path,
):
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            activities = TemporalArtifactActivities(service)
            catalog = build_default_activity_catalog()
            bindings = {
                binding.activity_type: binding
                for binding in build_activity_bindings(
                    catalog,
                    artifact_activities=activities,
                    manifest_activities=TemporalManifestActivities(
                        artifact_service=service,
                    ),
                    proposal_activities=TemporalProposalActivities(artifact_service=service),
                    agent_skills_activities=AgentSkillsActivities(),
                    fleets=(ARTIFACTS_FLEET,),
                )
            }

            artifact_ref, _upload = await bindings["artifact.create"].handler(
                {
                    "principal": "user-1",
                    "content_type": "application/json",
                    "name": "reports/run_summary.json",
                    "metadata_json": {"artifact_kind": "summary"},
                }
            )
            artifact, _links, _pinned, _policy = await service.get_metadata(
                artifact_id=artifact_ref.artifact_id,
                principal="user-1",
            )

            assert artifact.metadata_json["name"] == "reports/run_summary.json"
            assert artifact.metadata_json["artifact_kind"] == "summary"

async def test_artifact_publish_report_bundle_binding_routes_to_artifacts_queue(
    tmp_path: Path,
):
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            activities = TemporalArtifactActivities(service)
            catalog = build_default_activity_catalog()
            bindings = {
                binding.activity_type: binding
                for binding in build_activity_bindings(
                    catalog,
                    artifact_activities=activities,
                    manifest_activities=TemporalManifestActivities(
                        artifact_service=service,
                    ),
                    proposal_activities=TemporalProposalActivities(
                        artifact_service=service
                    ),
                    agent_skills_activities=AgentSkillsActivities(),
                    fleets=(ARTIFACTS_FLEET,),
                )
            }

            binding = bindings["artifact.publish_report_bundle"]
            assert (
                catalog.resolve_activity(
                    "artifact.publish_report_bundle"
                ).retries.max_attempts
                == 1
            )
            result = await binding.handler(
                {
                    "principal": "workflow-producer",
                    "namespace": "moonmind",
                    "workflow_id": "wf-report",
                    "run_id": "run-report",
                    "report_type": "unit_test_report",
                    "report_scope": "final",
                    "primary": {
                        "payload": "# Final report",
                        "content_type": "text/markdown",
                    },
                }
            )

            assert binding.task_queue == "mm.activity.artifacts"
            assert result["report_bundle_v"] == 1
            assert result["primary_report_ref"]["artifact_id"].startswith("art_")

async def test_plan_validate_accepts_temporal_registry_artifact_ids(tmp_path: Path):
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            registry_ref = await service.create(
                principal="user-1",
                content_type="application/json",
            )
            registry_artifact, _upload = registry_ref
            registry_completed = await service.write_complete(
                artifact_id=registry_artifact.artifact_id,
                principal="user-1",
                payload=(json.dumps(_registry_payload()) + "\n").encode("utf-8"),
                content_type="application/json",
            )

            planner = TemporalPlanActivities(artifact_service=service)
            registry_payload = _registry_payload()

            snapshot = create_registry_snapshot(
                skills=parse_skill_registry(registry_payload),
                artifact_store=InMemoryArtifactStore(),
            )
            plan_payload = _plan_payload(
                registry_artifact_id=registry_completed.artifact_id,
                registry_digest=snapshot.digest,
            )
            plan_artifact, _upload = await service.create(
                principal="user-1",
                content_type="application/json",
            )
            await service.write_complete(
                artifact_id=plan_artifact.artifact_id,
                principal="user-1",
                payload=(json.dumps(plan_payload) + "\n").encode("utf-8"),
                content_type="application/json",
            )

            validated_ref = await planner.plan_validate(
                plan_ref=plan_artifact.artifact_id,
                registry_snapshot_ref=registry_completed.artifact_id,
                principal="user-1",
            )
            _artifact, payload = await service.read(
                artifact_id=validated_ref.artifact_id,
                principal="user-1",
            )

            assert b'"plan_version": "1.0"' in payload

async def test_plan_generate_rejects_placeholder_registry_refs(tmp_path: Path):
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )

            def _placeholder_planner(_inputs, _parameters, _snapshot):
                return {
                    "plan_version": "1.0",
                    "metadata": {
                        "title": "Bad placeholder plan",
                        "created_at": "2026-03-12T00:00:00Z",
                        "registry_snapshot": {
                            "digest": "reg:sha256:dummy",
                            "artifact_ref": "art:sha256:dummy",
                        },
                    },
                    "policy": {"failure_mode": "FAIL_FAST", "max_concurrency": 1},
                    "nodes": [
                        {
                            "id": "n1",
                            "skill": {"name": "code"},
                            "inputs": {
                                "instructions": "Do work",
                                "runtime": {"mode": "codex"},
                            },
                        }
                    ],
                    "edges": [],
                }

            planner = TemporalPlanActivities(
                artifact_service=service,
                planner=_placeholder_planner,
            )
            with pytest.raises(
                TemporalActivityRuntimeError,
                match="placeholder ref\\(s\\) matching '\\*:sha256:dummy'",
            ):
                await planner.plan_generate(
                    principal="user-1",
                    parameters={
                        "repository": "moonladder/moonmind",
                        "task": {
                            "tool": {"type": "skill", "name": "code"},
                        },
                    },
                )

async def test_plan_generate_legacy_payload_replay(tmp_path: Path):
    """
    Simulates a workflow replay where an older dict-based payload arrives
    at the plan.generate activity, ensuring dual-read parses to PlanGenerateInput.
    """
    from unittest.mock import AsyncMock, patch
    from moonmind.schemas.temporal_activity_models import ArtifactRefModel
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )

            def _dummy_planner(_inputs, _parameters, _snapshot):
                return {
                    "plan_version": "1.0",
                    "metadata": {
                        "title": "Dual-Read Replay Plan",
                        "created_at": "2026-03-12T00:00:00Z",
                    },
                    "policy": {"failure_mode": "FAIL_FAST", "max_concurrency": 1},
                    "nodes": [
                        {
                            "id": "n1",
                            "skill": {"name": "dummy"},
                            "inputs": {"arg": "val"}
                        }
                    ],
                    "edges": [],
                }
            
            # Setup mock registry wrapper
            with patch("moonmind.workflows.temporal.activity_runtime._temporal_snapshot_from_payload") as mock_snapshot:
                from moonmind.workflows.skills.tool_registry import ToolRegistrySnapshot
                mock_snapshot.return_value = ToolRegistrySnapshot(
                    digest="reg:sha256:test1234",
                    artifact_ref="art:sha256:test1234",
                    skills=()
                )

                planner = TemporalPlanActivities(
                    artifact_service=service,
                    planner=_dummy_planner,
                )
                
                # The exact dict layout historically emitted by workflow.execute_activity
                legacy_payload = {
                    "principal": "user-replay",
                    "inputs_ref": None,
                    "parameters": {"strategy": "default"},
                    "idempotency_key": "replay-test"
                }
                
                # Should deserialize correctly matching `PlanGenerateInput` fallback
                result = await planner.plan_generate(legacy_payload) # type: ignore
                assert result.plan_ref.artifact_ref_v == 1

async def test_default_skill_registry_payload_excludes_auto_when_explicit_skill_selected():
    """When an explicit skill is selected, 'auto' (the placeholder) must not appear in the registry."""
    payload = _default_skill_registry_payload(
        parameters={
            "workflow": {
                "tool": {
                    "type": "skill",
                    "name": "pr-resolver",
                }
            }
        }
    )
    skills = payload.get("skills")
    assert isinstance(skills, list)
    keyset = {
        str(item.get("name"))
        for item in skills
        if isinstance(item, dict)
    }
    # 'auto' is a placeholder and must not be in the registry when explicit skills are present.
    assert "auto" not in keyset
    assert "pr-resolver" in keyset
    assert all("version" not in item for item in skills if isinstance(item, dict))

async def test_default_skill_registry_payload_auto_placeholder_filtered():
    """When 'auto' is the only (placeholder) skill, it must not appear in the registry."""
    payload = _default_skill_registry_payload(
        parameters={
            "workflow": {
                "skill": {
                    "name": "auto",
                }
            }
        }
    )
    skills = payload.get("skills")
    assert isinstance(skills, list)
    keyset = {
        str(item.get("name"))
        for item in skills
        if isinstance(item, dict)
    }
    # 'auto' is a placeholder and must not appear in the registry at all.
    assert "auto" not in keyset
    assert all("version" not in item for item in skills if isinstance(item, dict))

@pytest.mark.parametrize(
    "skill_name",
    ["jira-issue-creator", "jira-issue-updater", "jira-pr-verify", "jira-verify"],
)
async def test_default_skill_registry_payload_excludes_agent_only_jira_skill(
    skill_name: str,
):
    payload = _default_skill_registry_payload(
        parameters={
            "workflow": {
                "tool": {
                    "type": "skill",
                    "name": skill_name,
                }
            }
        }
    )
    skills = payload.get("skills")
    assert skills == []

async def test_default_skill_registry_payload_uses_dood_tool_definitions():
    payload = _default_skill_registry_payload(
        parameters={
            "workflow": {
                "steps": [
                    {
                        "tool": {
                            "type": "skill",
                            "name": "container.run_workload",
                        }
                    },
                    {
                        "tool": {
                            "type": "skill",
                            "name": "unreal.run_tests",
                        }
                    },
                ]
            }
        }
    )

    skills = payload.get("skills")
    assert isinstance(skills, list)
    tools = {item["name"]: item for item in skills}
    assert tools["container.run_workload"]["requirements"]["capabilities"] == [
        "docker_workload"
    ]
    assert tools["unreal.run_tests"]["requirements"]["capabilities"] == [
        "docker_workload"
    ]
    assert (
        tools["container.run_workload"]["executor"]["activity_type"]
        == "mm.tool.execute"
    )

async def test_default_skill_registry_payload_includes_input_sourced_tool_steps():
    payload = _default_skill_registry_payload(
        parameters={"workflow": {"tool": {"name": "auto"}}},
        inputs={
            "workflow": {
                "steps": [
                    {
                        "type": "tool",
                        "tool": {
                            "id": "jira.get_issue",
                            "inputs": {"issueKey": "MM-579"},
                        },
                    }
                ]
            }
        },
    )

    skills = payload.get("skills")
    assert isinstance(skills, list)
    assert [item["name"] for item in skills] == ["jira.get_issue"]
    assert all("version" not in item for item in skills if isinstance(item, dict))

async def test_default_skill_registry_payload_uses_curated_pentest_tool_definition():
    payload = _default_skill_registry_payload(
        parameters={
            "workflow": {
                "steps": [
                    {
                        "tool": {
                            "type": "skill",
                            "name": "security.pentest.run",
                        }
                    }
                ]
            }
        }
    )

    skills = payload.get("skills")
    assert isinstance(skills, list)
    assert len(skills) == 1
    definition = skills[0]

    assert definition["name"] == "security.pentest.run"
    assert "version" not in definition
    assert definition["type"] == "skill"
    assert definition["executor"] == {
        "activity_type": "security.pentest.execute",
        "selector": {"mode": "by_capability"},
        "binding_reason": "stronger_isolation",
    }
    assert definition["requirements"]["capabilities"] == ["agent_runtime"]
    assert definition["security"]["allowed_roles"] == [
        "admin",
        "security_operator",
    ]

    input_schema = definition["inputs"]["schema"]
    assert input_schema["required"] == ["target"]
    assert input_schema["properties"]["operation_mode"]["enum"] == [
        "recon_only",
        "validate_hypothesis",
        "full_authorized",
    ]
    assert input_schema["properties"]["operation_mode"]["default"] == "recon_only"
    assert (
        input_schema["properties"]["runner_profile_id"]["default"]
        == "pentestgpt-claude-oauth"
    )
    assert input_schema["properties"]["provider_selector"][
        "additionalProperties"
    ] is False
    assert set(input_schema["properties"]["provider_selector"]["properties"]) == {
        "provider_id",
        "tags_any",
        "tags_all",
    }
    assert (
        input_schema["properties"]["provider_runtime_state"][
            "additionalProperties"
        ]["properties"]["available_slots"]["minimum"]
        == 0
    )
    assert input_schema["properties"]["time_budget_minutes"] == {
        "type": "integer",
        "minimum": 1,
        "maximum": 480,
        "default": 60,
    }
    assert input_schema["properties"]["artifacts_dir"]["type"] == "string"
    assert input_schema["properties"]["evidence_level"]["enum"] == [
        "minimal",
        "standard",
        "full",
    ]

    output_schema = definition["outputs"]["schema"]
    assert output_schema["required"] == [
        "status",
        "target",
        "runner_profile_id",
        "launch_plan",
    ]
    assert output_schema["properties"]["status"] == {
        "type": "string",
        "enum": ["launch_plan_ready", "provider_cooldown"],
    }
    assert "provider_profile" in output_schema["properties"]
    assert "provider_lease" in output_schema["properties"]
    assert output_schema["properties"]["provider_cooldown"]["properties"][
        "cooldown_seconds"
    ] == {"type": "integer", "minimum": 0}
    launch_plan_schema = output_schema["properties"]["launch_plan"]
    assert launch_plan_schema["required"] == [
        "profile_id",
        "container_name",
        "image",
        "entrypoint",
        "workdir",
        "network_policy",
        "linux_capabilities",
        "devices",
        "labels",
        "cleanup_selector",
    ]
    assert {
        "mounts",
        "env_keys",
        "resources",
        "timeout_seconds",
        "cleanup",
    }.issubset(launch_plan_schema["properties"])

    assert definition["policies"] == {
        "timeouts": {
            "start_to_close_seconds": 28800,
            "schedule_to_close_seconds": 32400,
        },
        "retries": {
            "max_attempts": 1,
            "backoff": "none",
            "non_retryable_error_codes": [
                "INVALID_SCOPE",
                "PERMISSION_DENIED",
                "UNAPPROVED_TARGET",
                "UNSUPPORTED_PROFILE",
                "NON_IDEMPOTENT_OPERATION",
            ],
        },
    }

    parsed = parse_skill_registry(payload)
    assert [tool.name for tool in parsed] == [
        "security.pentest.run"
    ]
    assert parsed[0].executor.activity_type == "security.pentest.execute"

async def test_default_skill_registry_payload_uses_curated_deployment_tool_definition():
    payload = _default_skill_registry_payload(
        parameters={
            "workflow": {
                "steps": [
                    {
                        "tool": {
                            "type": "skill",
                            "name": DEPLOYMENT_UPDATE_TOOL_NAME,
                        }
                    }
                ]
            }
        }
    )

    skills = payload.get("skills")
    assert isinstance(skills, list)
    assert len(skills) == 1
    definition = skills[0]

    assert definition["name"] == DEPLOYMENT_UPDATE_TOOL_NAME
    assert "version" not in definition
    assert definition["type"] == "skill"
    assert definition["executor"] == {
        "activity_type": "mm.tool.execute",
        "selector": {"mode": "by_capability"},
    }
    assert definition["requirements"]["capabilities"] == [
        "deployment_control",
        "docker_admin",
    ]
    assert definition["security"]["allowed_roles"] == ["admin"]
    assert definition["security"]["opsRuntime"]["kind"] == "MoonMindOpsRuntime"
    assert definition["security"]["opsRuntime"]["exposedToManagedAgents"] is False

    input_schema = definition["inputs"]["schema"]
    assert input_schema["required"] == ["stack", "image"]
    assert input_schema["additionalProperties"] is False
    assert input_schema["properties"]["image"]["additionalProperties"] is False

    parsed = parse_skill_registry(payload)
    assert [tool.name for tool in parsed] == [
        DEPLOYMENT_UPDATE_TOOL_NAME
    ]
    assert parsed[0].required_capabilities == (
        "deployment_control",
        "docker_admin",
    )
    route = build_default_activity_catalog().resolve_skill(parsed[0])
    assert route.fleet == DEPLOYMENT_FLEET
    assert route.task_queue == "mm.activity.deployment"

async def test_default_skill_registry_payload_routes_jira_preset_brief_to_integrations():
    payload = _default_skill_registry_payload(
        parameters={
            "workflow": {
                "steps": [
                    {
                        "tool": {
                            "type": "skill",
                            "name": "jira.load_preset_brief",
                        }
                    }
                ]
            }
        }
    )

    skills = payload.get("skills")
    assert isinstance(skills, list)
    assert len(skills) == 1
    definition = skills[0]

    assert definition["name"] == "jira.load_preset_brief"
    assert definition["requirements"]["capabilities"] == ["integration:jira"]
    assert definition["policies"]["timeouts"] == {
        "start_to_close_seconds": 60,
        "schedule_to_close_seconds": 120,
    }

    parsed = parse_skill_registry(payload)
    assert parsed[0].required_capabilities == ("integration:jira",)
    route = build_default_activity_catalog().resolve_skill(parsed[0])
    assert route.fleet == INTEGRATIONS_FLEET
    assert route.task_queue == "mm.activity.integrations"

async def test_curated_pentest_activity_binding_is_registered_on_agent_runtime_fleet():
    bindings = build_activity_bindings(
        build_default_activity_catalog(),
        agent_runtime_activities=TemporalAgentRuntimeActivities(),
        agent_skills_activities=AgentSkillsActivities(),
        fleets=[AGENT_RUNTIME_FLEET],
    )

    binding = next(
        item for item in bindings if item.activity_type == "security.pentest.execute"
    )
    assert binding.fleet == AGENT_RUNTIME_FLEET
    assert binding.task_queue == "mm.activity.agent_runtime"
    assert binding.handler.__temporal_activity_definition.name == (
        "security.pentest.execute"
    )

async def test_managed_runtime_cleanup_binding_is_registered_on_agent_runtime_fleet():
    bindings = build_activity_bindings(
        build_default_activity_catalog(),
        agent_runtime_activities=TemporalAgentRuntimeActivities(),
        agent_skills_activities=AgentSkillsActivities(),
        fleets=[AGENT_RUNTIME_FLEET],
    )

    binding = next(
        item
        for item in bindings
        if item.activity_type == "agent_runtime.cleanup_managed_runtime_files"
    )

    assert binding.fleet == AGENT_RUNTIME_FLEET
    assert binding.task_queue == "mm.activity.agent_runtime"
    assert binding.handler.__temporal_activity_definition.name == (
        "agent_runtime.cleanup_managed_runtime_files"
    )

def _approved_pentest_scope() -> dict[str, object]:
    now = datetime.now(timezone.utc)
    return {
        "scope_id": "scope-123",
        "title": "Lab validation",
        "owner_user_id": "user-security",
        "created_at": (now - timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
        "expires_at": (now + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
        "target_class": "lab",
        "targets": [{"kind": "url", "value": "https://lab.example.test"}],
        "allowed_actions": [
            "auth_testing",
            "vuln_validation",
            "exploit_validation",
        ],
        "prohibited_actions": [],
        "requires_manual_approval": False,
        "approval_recorded": False,
        "allowed_runner_profiles": [PENTEST_CLAUDE_OAUTH_RUNNER_PROFILE_ID],
        "required_network_attachment_type": None,
        "authorized_principals": ["user-security"],
        "metadata": {"jira": "MM-470"},
    }

def _pentest_activity_payload(**overrides: object) -> dict[str, object]:
    request: dict[str, object] = {
        "pentest_enabled": True,
        "agent_run_id": "run-123",
        "step_id": "step-pentest",
        "attempt": 1,
        "target": "https://lab.example.test",
        "operation_mode": "validate_hypothesis",
        "objective": "Validate auth bypass hypothesis.",
        "scope_artifact_ref": "art:sha256:scope",
        "runner_profile_id": PENTEST_CLAUDE_OAUTH_RUNNER_PROFILE_ID,
        "execution_profile_ref": PENTEST_CLAUDE_OAUTH_PROFILE_ID,
        "time_budget_minutes": 60,
        "evidence_level": "standard",
        "artifacts_dir": "/tmp/artifacts",
        "principal_id": "user-security",
        "approved_scope": _approved_pentest_scope(),
        "trusted_internal_execution": True,
    }
    request.update(overrides)
    return {"request": request}

def _pentest_artifact_activity_payload(**overrides: object) -> dict[str, object]:
    request: dict[str, object] = {
        "pentest_enabled": True,
        "agent_run_id": "run-123",
        "step_id": "step-pentest",
        "attempt": 1,
        "target": "https://lab.example.test",
        "operation_mode": "validate_hypothesis",
        "objective": "Validate auth bypass hypothesis.",
        "scope_artifact_ref": "art_scope_valid",
        "runner_profile_id": PENTEST_CLAUDE_OAUTH_RUNNER_PROFILE_ID,
        "execution_profile_ref": PENTEST_CLAUDE_OAUTH_PROFILE_ID,
        "time_budget_minutes": 60,
        "evidence_level": "standard",
        "artifacts_dir": "/tmp/artifacts",
        "principal_id": "user-security",
    }
    request.update(overrides)
    return {"request": request}

class _FakePentestArtifactService:
    def __init__(self, artifacts: dict[str, bytes]) -> None:
        self.artifacts = artifacts
        self.reads: list[tuple[str, str]] = []

    async def read(
        self,
        *,
        artifact_id: str,
        principal: str,
        allow_restricted_raw: bool = False,
    ) -> tuple[object, bytes]:
        self.reads.append((artifact_id, principal))
        if artifact_id not in self.artifacts:
            raise TemporalArtifactNotFoundError(artifact_id)
        return SimpleNamespace(artifact_id=artifact_id), self.artifacts[artifact_id]

def _scope_artifact_bytes(**overrides: object) -> bytes:
    return json.dumps(_approved_pentest_scope() | overrides).encode("utf-8")

class _FakePentestLauncher:
    def __init__(
        self,
        *,
        status: str = "succeeded",
        findings_payload: dict[str, Any] | None = None,
    ) -> None:
        self.status = status
        self.findings_payload = findings_payload
        self.requests: list[object] = []

    async def run(self, request: object) -> WorkloadResult:
        self.requests.append(request)
        workload_request = getattr(request, "request", request)
        artifacts_dir = getattr(workload_request, "artifacts_dir", None)
        if self.findings_payload is not None and artifacts_dir:
            findings_path = (
                Path(str(artifacts_dir))
                / "pentest"
                / "findings"
                / "findings.normalizer-input.json"
            )
            findings_path.parent.mkdir(parents=True, exist_ok=True)
            findings_path.write_text(
                json.dumps(self.findings_payload, sort_keys=True, indent=2) + "\n",
                encoding="utf-8",
            )
        return WorkloadResult.model_validate(
            {
                "requestId": "workload-run-123",
                "profileId": PENTEST_CLAUDE_OAUTH_RUNNER_PROFILE_ID,
                "status": self.status,
                "exitCode": 0 if self.status == "succeeded" else 2,
                "stdoutRef": "file:/tmp/artifacts/pentest/runtime/stdout.log",
                "stderrRef": "file:/tmp/artifacts/pentest/runtime/stderr.log",
                "diagnosticsRef": "file:/tmp/artifacts/pentest/runtime/diagnostics.json",
                "outputRefs": {
                    "report.primary": "file:/tmp/artifacts/pentest/findings/findings.report.md",
                    "report.summary": "file:/tmp/artifacts/pentest/findings/findings.summary.md",
                    "report.structured": "file:/tmp/artifacts/pentest/findings/findings.normalizer-input.json",
                    "report.evidence": "file:/tmp/artifacts/pentest/evidence/bundle.tar.zst",
                },
                "metadata": {"fake": True},
            }
        )

class _FileWritingPentestLauncher(_FakePentestLauncher):
    async def run(self, request: object) -> WorkloadResult:
        workload_request = getattr(request, "request", request)
        artifacts_dir = Path(str(getattr(workload_request, "artifacts_dir")))
        base = artifacts_dir / "pentest"
        report_profile_fields = {
            "runner_" + "profile" + "_id": "report-runner",
            "execution_" + "profile" + "_ref": "report-execution",
        }
        files = {
            "runtime/stdout.log": "stdout raw-output-marker-should-not-leak\n",
            "runtime/stderr.log": "stderr raw-error-marker-should-not-leak\n",
            "runtime/diagnostics.json": json.dumps(
                {"status": "ok", "raw_log": "must stay in artifact body"}
            ),
            "findings/findings.summary.md": "Summary without secrets.\n",
            "findings/findings.report.md": "# Pentest report\n\nPrimary report.\n",
            "findings/findings.normalizer-input.json": json.dumps(
                {
                    "tool_name": "security.pentest.run",
                    "target": "https://lab.example.test",
                    "scope_artifact_ref": "art:sha256:scope",
                    "operation_mode": "validate_hypothesis",
                    **report_profile_fields,
                    "produced_at": "2026-06-14T00:00:00Z",
                    "findings": [
                        {
                            "finding_id": "finding-1",
                            "title": "Supported issue",
                            "severity": "high",
                            "confidence": "confirmed",
                            "target": "https://lab.example.test",
                            "summary": "Evidence supports issue",
                        }
                    ],
                    "summary": {
                        "findings_count": 1,
                        "confirmed_findings_count": 1,
                        "high_or_critical_count": 1,
                    },
                }
            ),
            "evidence/bundle.tar.zst": (
                "fake evidence body evidence-marker-should-not-leak\n"
            ),
        }
        for relative, body in files.items():
            path = base / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
        return await super().run(request)


class _MalformedFindingsFileLauncher(_FileWritingPentestLauncher):
    async def run(self, request: object) -> WorkloadResult:
        result = await super().run(request)
        workload_request = getattr(request, "request", request)
        artifacts_dir = Path(str(getattr(workload_request, "artifacts_dir")))
        (
            artifacts_dir / "pentest" / "findings" / "findings.normalizer-input.json"
        ).write_text("{malformed-json", encoding="utf-8")
        return result


class _BlockingPentestLauncher(_FakePentestLauncher):
    def __init__(self, *, status: str = "succeeded") -> None:
        super().__init__(
            status=status,
            findings_payload={
                "tool_name": "security.pentest.run",
                "target": "https://lab.example.test",
                "scope_artifact_ref": "art:sha256:scope",
                "operation_mode": "validate_hypothesis",
                "runner_profile_id": PENTEST_CLAUDE_OAUTH_RUNNER_PROFILE_ID,
                "execution_profile_ref": PENTEST_CLAUDE_OAUTH_PROFILE_ID,
                "produced_at": "2026-06-14T00:00:00Z",
                "findings": [
                    {
                        "finding_id": "blocking-finding",
                        "title": "Blocking launcher finding",
                        "severity": "low",
                        "confidence": "supported",
                        "target": "https://lab.example.test",
                        "summary": "Valid structured finding for heartbeat test.",
                    }
                ],
            },
        )
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def run(self, request: object) -> WorkloadResult:
        self.requests.append(request)
        self.started.set()
        await self.release.wait()
        return await super().run(request)


class _RecordingPentestRegistry:
    """Registry stand-in that validates requests into the launcher contract.

    The real ``DockerWorkloadLauncher`` dereferences ``request.profile`` and
    ``request.request``, so the activity must hand the launcher a
    ``ValidatedWorkloadRequest`` rather than the plain ``WorkloadRequest``
    returned by ``parse_workload_request``. This records every validated
    request so tests can assert the registry was consulted before launch.
    """

    def __init__(self) -> None:
        self.requests: list[object] = []

    def validate_request(self, request, *, workflow_docker_mode=None):
        self.requests.append(request)
        return ValidatedWorkloadRequest(
            request=request,
            profile=None,
            ownership=request.ownership_metadata(
                workflow_docker_mode=workflow_docker_mode or "profiles"
            ),
            containerName=request.container_name,
        )

class _FakePentestLeaseManager:
    def __init__(self, client_adapter: object | None = None) -> None:
        self.client_adapter = client_adapter
        self.events: list[tuple[str, dict[str, Any]]] = []

    async def acquire(
        self,
        *,
        runtime_id: str,
        profile_id: str,
        owner: str,
        metadata: dict[str, Any],
    ) -> str:
        self.events.append(
            (
                "acquire",
                {
                    "runtime_id": runtime_id,
                    "profile_id": profile_id,
                    "owner": owner,
                    "metadata": dict(metadata),
                },
            )
        )
        return f"lease:{owner}"

    async def release(
        self,
        *,
        runtime_id: str,
        profile_id: str,
        owner: str,
        lease_id: str,
    ) -> None:
        self.events.append(
            (
                "release",
                {
                    "runtime_id": runtime_id,
                    "profile_id": profile_id,
                    "owner": owner,
                    "lease_id": lease_id,
                },
            )
        )

    async def record_cooldown(
        self,
        *,
        runtime_id: str,
        profile_id: str,
        owner: str,
        cooldown_seconds: int,
        reason: str,
    ) -> None:
        self.events.append(
            (
                "cooldown",
                {
                    "runtime_id": runtime_id,
                    "profile_id": profile_id,
                    "owner": owner,
                    "cooldown_seconds": cooldown_seconds,
                    "reason": reason,
                },
            )
        )


class _SupervisedPentestHandle:
    def __init__(
        self,
        *,
        result_after_polls: int | None = 2,
        result_status: str = "succeeded",
        poll_error: Exception | None = None,
    ) -> None:
        self.result_after_polls = result_after_polls
        self.result_status = result_status
        self.poll_error = poll_error
        self.polls = 0
        self.stops: list[int] = []
        self.removed = False

    async def poll(self) -> WorkloadResult | None:
        self.polls += 1
        if self.poll_error is not None:
            raise self.poll_error
        if self.result_after_polls is None or self.polls < self.result_after_polls:
            return None
        return WorkloadResult.model_validate(
            {
                "requestId": "supervised-run-123",
                "profileId": PENTEST_CLAUDE_OAUTH_RUNNER_PROFILE_ID,
                "status": self.result_status,
                "exitCode": 0 if self.result_status == "succeeded" else 2,
                "stdoutRef": "file:/tmp/artifacts/pentest/runtime/stdout.log",
                "stderrRef": "file:/tmp/artifacts/pentest/runtime/stderr.log",
                "diagnosticsRef": "file:/tmp/artifacts/pentest/runtime/diagnostics.json",
                "outputRefs": {
                    "report.primary": "file:/tmp/artifacts/pentest/findings/findings.report.md",
                    "report.summary": "file:/tmp/artifacts/pentest/findings/findings.summary.md",
                    "report.structured": "file:/tmp/artifacts/pentest/findings/findings.normalizer-input.json",
                    "report.evidence": "file:/tmp/artifacts/pentest/evidence/bundle.tar.zst",
                },
            }
        )

    async def stop(self, *, grace_seconds: int) -> dict[str, object]:
        self.stops.append(grace_seconds)
        return {
            "gracefulTerminationAttempted": True,
            "killEscalated": True,
        }

    async def remove(self) -> dict[str, object]:
        self.removed = True
        return {"containerRemoved": True}


class _SupervisedPentestLauncher:
    def __init__(self, handle: _SupervisedPentestHandle) -> None:
        self.handle = handle
        self.requests: list[object] = []

    async def start(self, request: object) -> _SupervisedPentestHandle:
        self.requests.append(request)
        return self.handle


@pytest.fixture(autouse=True)
def _use_fake_pentest_lease_manager(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        (
            "moonmind.workflows.temporal.activity_runtime."
            "TemporalPentestProviderLeaseManager"
        ),
        _FakePentestLeaseManager,
    )

async def test_temporal_pentest_provider_lease_manager_acquires_slot_with_update():
    class _ClientAdapter:
        def __init__(self) -> None:
            self.updates: list[tuple[str, str, dict[str, object]]] = []
            self.signals: list[tuple[str, str, dict[str, object]]] = []

        async def update_workflow(
            self, workflow_id: str, update_name: str, arg: dict[str, object]
        ) -> dict[str, object]:
            self.updates.append((workflow_id, update_name, arg))
            return {
                "profile_id": "claude_anthropic",
                "lease_id": "owner-1",
            }

        async def signal_workflow(
            self, workflow_id: str, signal_name: str, arg: dict[str, object]
        ) -> None:
            self.signals.append((workflow_id, signal_name, arg))

    client = _ClientAdapter()
    manager = TemporalPentestProviderLeaseManager(client)

    lease_id = await manager.acquire(
        runtime_id="claude_code",
        profile_id="claude_anthropic",
        owner="owner-1",
        metadata={"target_hash": "hash-1"},
    )

    assert lease_id == "owner-1"
    assert client.signals == []
    assert client.updates == [
        (
            "provider-profile-manager:claude_code",
            "AcquireSlot",
            {
                "requester_workflow_id": "owner-1",
                "runtime_id": "claude_code",
                "execution_profile_ref": "claude_anthropic",
                "metadata": {"target_hash": "hash-1"},
            },
        )
    ]

async def test_temporal_pentest_provider_lease_manager_waits_for_profile_readiness(
    monkeypatch: pytest.MonkeyPatch,
):
    sleeps: list[float] = []

    async def _sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(activity_runtime_module.asyncio, "sleep", _sleep)

    class _WorkflowHandle:
        def __init__(self) -> None:
            self.queries = 0

        async def query(self, query_name: str) -> dict[str, object]:
            assert query_name == "get_state"
            self.queries += 1
            if self.queries < 3:
                return {"profiles": {}}
            return {"profiles": {"claude_anthropic": {"enabled": True}}}

    class _TemporalClient:
        def __init__(self) -> None:
            self.handle = _WorkflowHandle()
            self.started: list[tuple[str, dict[str, object], str]] = []

        async def start_workflow(
            self,
            workflow_name: str,
            arg: dict[str, object],
            *,
            id: str,
            task_queue: str,
        ) -> None:
            self.started.append((workflow_name, arg, id))

        def get_workflow_handle(self, workflow_id: str) -> _WorkflowHandle:
            assert workflow_id == "provider-profile-manager:claude_code"
            return self.handle

    class _ClientAdapter:
        def __init__(self) -> None:
            self.client = _TemporalClient()
            self.updates: list[tuple[str, str, dict[str, object]]] = []

        async def get_client(self) -> _TemporalClient:
            return self.client

        async def update_workflow(
            self, workflow_id: str, update_name: str, arg: dict[str, object]
        ) -> dict[str, object]:
            self.updates.append((workflow_id, update_name, arg))
            return {
                "profile_id": "claude_anthropic",
                "lease_id": "owner-1",
            }

    client = _ClientAdapter()
    manager = TemporalPentestProviderLeaseManager(client)

    lease_id = await manager.acquire(
        runtime_id="claude_code",
        profile_id="claude_anthropic",
        owner="owner-1",
        metadata={},
    )

    assert lease_id == "owner-1"
    assert client.client.handle.queries == 3
    assert sleeps == [1.0, 1.0]
    assert client.client.started == [
        (
            "MoonMind.ProviderProfileManager",
            {"runtime_id": "claude_code"},
            "provider-profile-manager:claude_code",
        )
    ]
    assert client.updates == [
        (
            "provider-profile-manager:claude_code",
            "AcquireSlot",
            {
                "requester_workflow_id": "owner-1",
                "runtime_id": "claude_code",
                "execution_profile_ref": "claude_anthropic",
                "metadata": {},
            },
        )
    ]

async def test_temporal_pentest_provider_lease_manager_fails_closed_without_updates():
    class _ClientAdapter:
        async def signal_workflow(
            self, workflow_id: str, signal_name: str, arg: dict[str, object]
        ) -> None:
            raise AssertionError("acquire must not use asynchronous request_slot signals")

    manager = TemporalPentestProviderLeaseManager(_ClientAdapter())

    with pytest.raises(RuntimeError, match="workflow updates"):
        await manager.acquire(
            runtime_id="claude_code",
            profile_id="claude_anthropic",
            owner="owner-1",
            metadata={},
        )

async def test_pentest_heartbeat_helper_emits_compact_redacted_payload(
    monkeypatch: pytest.MonkeyPatch,
):
    emitted: list[dict[str, object]] = []

    monkeypatch.setattr(
        activity_runtime_module.temporal_activity,
        "heartbeat",
        lambda payload: emitted.append(payload),
    )

    payload = activity_runtime_module.emit_pentest_activity_heartbeat(
        phase="running",
        agent_run_id="run-123",
        step_id="step-pentest",
        attempt=1,
        message="running with token=secret-value",
        metadata={
            "stdout": "raw stdout body must be dropped",
            "safe": "password=hunter2",
        },
    )

    assert emitted == [payload]
    assert payload["phase"] == "running"
    assert payload["message"] == "running with token=[REDACTED]"
    assert payload["metadata"] == {"safe": "password=[REDACTED]"}
    assert "secret-value" not in str(payload)
    assert "raw stdout body" not in str(payload)

async def test_security_pentest_execute_fails_closed_before_runner_when_disabled_by_operator_override(
    monkeypatch: pytest.MonkeyPatch,
):
    # Discovery/execution defaults to enabled (MM-845); the operator disable
    # override (MOONMIND_PENTEST_ENABLED=false) must still fail closed before
    # the runner when the policy flag is omitted and falls back to settings.
    monkeypatch.setattr(settings.pentest, "enabled", False)
    launcher = _FakePentestLauncher()
    activities = TemporalAgentRuntimeActivities(workload_launcher=launcher)

    payload = _pentest_activity_payload()["request"]
    payload.pop("pentest_enabled")

    result = await activities.security_pentest_execute({"request": payload})

    assert result["status"] == "policy_denied"
    assert result["execution_policy"]["max_attempts"] == 1
    assert result["failure_classification"]["failure_kind"] == "policy_denied"
    assert result["failure_classification"]["interaction_state"] == "pre_interaction"
    assert result["terminal_cleanup"]["terminal_reason"] == "failure"
    assert launcher.requests == []


async def test_security_pentest_execute_honors_explicit_disabled_payload_flag(
    monkeypatch: pytest.MonkeyPatch,
):
    # In-flight payloads that recorded an explicit pentest_enabled=False must
    # keep failing closed even though discovery now defaults to enabled.
    monkeypatch.setattr(settings.pentest, "enabled", True)
    launcher = _FakePentestLauncher()
    activities = TemporalAgentRuntimeActivities(workload_launcher=launcher)

    payload = _pentest_activity_payload(pentest_enabled=False)["request"]

    result = await activities.security_pentest_execute({"request": payload})

    assert result["status"] == "policy_denied"
    assert result["failure_classification"]["failure_kind"] == "policy_denied"
    assert result["failure_classification"]["interaction_state"] == "pre_interaction"
    assert launcher.requests == []

async def test_security_pentest_execute_malformed_attempt_returns_validation_failure():
    launcher = _FakePentestLauncher()
    activities = TemporalAgentRuntimeActivities(workload_launcher=launcher)
    payload = dict(_pentest_activity_payload()["request"])
    payload["attempt"] = "not-an-int"

    result = await activities._security_pentest_execute_trusted_internal(
        {"request": payload}
    )

    assert result["status"] == "validation_failed"
    assert result["failure_classification"]["failure_kind"] == "policy_denied"
    assert result["failure_classification"]["interaction_state"] == "pre_interaction"
    assert launcher.requests == []

async def test_security_pentest_execute_emits_all_phase_heartbeats(
    monkeypatch: pytest.MonkeyPatch,
):
    emitted: list[dict[str, object]] = []
    monkeypatch.setattr(
        activity_runtime_module.temporal_activity,
        "heartbeat",
        lambda payload: emitted.append(payload),
    )
    monkeypatch.setattr(
        activity_runtime_module,
        "_PENTEST_RUNNING_HEARTBEAT_INTERVAL_SECONDS",
        0.01,
    )
    activities = TemporalAgentRuntimeActivities(
        workload_launcher=_FileWritingPentestLauncher(),
        workload_registry=_RecordingPentestRegistry(),
    )

    result = await activities._security_pentest_execute_trusted_internal(
        _pentest_activity_payload()
    )

    assert result["status"] == "completed"
    phases = [str(item["phase"]) for item in emitted]
    for phase in (
        "validating_scope",
        "waiting_for_profile_slot",
        "materializing_inputs",
        "launching_container",
        "running",
        "publishing_artifacts",
        "normalizing_findings",
        "cleanup",
    ):
        assert phase in phases

async def test_security_pentest_execute_repeats_running_heartbeats(
    monkeypatch: pytest.MonkeyPatch,
):
    emitted: list[dict[str, object]] = []
    monkeypatch.setattr(
        activity_runtime_module.temporal_activity,
        "heartbeat",
        lambda payload: emitted.append(payload),
    )
    monkeypatch.setattr(
        activity_runtime_module,
        "_PENTEST_RUNNING_HEARTBEAT_INTERVAL_SECONDS",
        0.01,
    )
    launcher = _BlockingPentestLauncher()
    activities = TemporalAgentRuntimeActivities(
        workload_launcher=launcher,
        workload_registry=_RecordingPentestRegistry(),
    )

    task = asyncio.create_task(
        activities._security_pentest_execute_trusted_internal(
            _pentest_activity_payload()
        )
    )
    await asyncio.wait_for(launcher.started.wait(), timeout=1)
    await asyncio.sleep(0.035)
    launcher.release.set()
    result = await task

    assert result["status"] == "completed"
    running = [item for item in emitted if item["phase"] == "running"]
    assert len(running) >= 2

async def test_security_pentest_execute_fails_closed_before_runner_without_scope():
    launcher = _FakePentestLauncher()
    activities = TemporalAgentRuntimeActivities(workload_launcher=launcher)

    result = await activities._security_pentest_execute_trusted_internal(
        _pentest_activity_payload(approved_scope=None)
    )

    assert result["status"] == "validation_failed"
    assert result["failure_classification"]["failure_kind"] == "policy_denied"
    assert "missing_approved_scope" in str(result["diagnostics"])
    assert launcher.requests == []

async def test_security_pentest_execute_loads_scope_artifact_before_launch_plan():
    artifact_service = _FakePentestArtifactService(
        {"art_scope_valid": _scope_artifact_bytes()}
    )
    activities = TemporalAgentRuntimeActivities(
        artifact_service=artifact_service,
        workload_launcher=_FileWritingPentestLauncher(),
        workload_registry=_RecordingPentestRegistry(),
    )

    result = await activities.security_pentest_execute(
        _pentest_artifact_activity_payload()
    )

    assert artifact_service.reads == [("art_scope_valid", "user-security")]
    assert result["status"] == "completed"
    assert result["launch_plan"]["profile_id"] == PENTEST_CLAUDE_OAUTH_RUNNER_PROFILE_ID


async def test_security_pentest_execute_applies_url_first_defaults_before_validation():
    artifact_service = _FakePentestArtifactService(
        {
            "art_scope_valid": _scope_artifact_bytes(
                allowed_actions=["recon", "scan", "content_discovery"],
            )
        }
    )
    registry = _RecordingPentestRegistry()
    activities = TemporalAgentRuntimeActivities(
        artifact_service=artifact_service,
        workload_launcher=_FileWritingPentestLauncher(),
        workload_registry=registry,
    )
    payload = _pentest_artifact_activity_payload()
    request = payload["request"]
    assert isinstance(request, dict)
    for key in (
        "operation_mode",
        "runner_profile_id",
        "execution_profile_ref",
        "time_budget_minutes",
        "evidence_level",
    ):
        request.pop(key)

    result = await activities.security_pentest_execute(payload)

    assert result["status"] == "completed"
    assert registry.requests[0].env_overrides["MM_PENTEST_MODE"] == "recon_only"
    assert registry.requests[0].profile_id == PENTEST_CLAUDE_OAUTH_RUNNER_PROFILE_ID
    assert result["runner_profile_id"] == PENTEST_CLAUDE_OAUTH_RUNNER_PROFILE_ID
    assert result["execution_profile_ref"] == PENTEST_CLAUDE_OAUTH_PROFILE_ID


async def test_security_pentest_execute_accepts_workflow_invocation_envelope(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("MOONMIND_AGENT_RUNTIME_STORE", "/custom/agent_jobs")
    artifact_service = _FakePentestArtifactService(
        {"art_scope_valid": _scope_artifact_bytes()}
    )
    launcher = _FakePentestLauncher()
    materialized_requests: list[object] = []
    activities = TemporalAgentRuntimeActivities(
        artifact_service=artifact_service,
        workload_launcher=launcher,
        workload_registry=_RecordingPentestRegistry(),
    )

    def _record_materialization(**kwargs):
        materialized_requests.append(kwargs["request"])
        return {}

    monkeypatch.setattr(
        activities._pentest_activities,
        "_materialize_pentest_input_files",
        _record_materialization,
    )

    result = await activities.security_pentest_execute(
        {
            "registry_snapshot_ref": "art_registry",
            "principal": "user-security",
            "pentest_enabled": True,
            "invocation_payload": {
                "id": "step-pentest",
                "tool": {
                    "type": "skill",
                    "name": "security.pentest.run",
                },
                "inputs": {
                    "pentest_enabled": False,
                    "target": "https://lab.example.test",
                    "operation_mode": "validate_hypothesis",
                    "scope_artifact_ref": "art_scope_valid",
                    "runner_profile_id": PENTEST_CLAUDE_OAUTH_RUNNER_PROFILE_ID,
                    "execution_profile_ref": PENTEST_CLAUDE_OAUTH_PROFILE_ID,
                    "time_budget_minutes": 60,
                    "evidence_level": "standard",
                    "agent_run_id": "caller-supplied-run",
                    "step_id": "caller-supplied-step",
                    "attempt": 99,
                    "principal_id": "caller-supplied-principal",
                    "artifacts_dir": "/tmp/caller-supplied-artifacts",
                },
                "options": {},
            },
            "context": {
                "namespace": "default",
                "workflow_id": "mm:workflow-123",
                "run_id": "run-123",
                "node_id": "step-pentest",
            },
            "idempotency_key": (
                "mm:workflow-123:run-123:step-pentest:execution:1:execute"
            ),
        }
    )

    assert artifact_service.reads == [("art_scope_valid", "user-security")]
    assert result["status"] == "failed"
    assert result["normalization_status"] == "normalizer_error"
    assert result["launch_plan"]["profile_id"] == PENTEST_CLAUDE_OAUTH_RUNNER_PROFILE_ID
    assert len(launcher.requests) == 1
    assert len(materialized_requests) == 1
    request = materialized_requests[0]
    assert request.agent_run_id == "mm:workflow-123"
    assert request.step_id == "step-pentest"
    assert request.attempt == 1
    assert request.principal_id == "user-security"
    assert (
        request.artifacts_dir
        == "/custom/agent_jobs/mm:workflow-123/artifacts/step-pentest"
    )

@pytest.mark.parametrize(
    ("artifact_id", "artifact_payload", "message"),
    [
        ("art_scope_missing", None, "scope_artifact_unreadable"),
        ("art_scope_malformed", b"{not-json", "scope_artifact_malformed"),
        (
            "art_scope_structurally_invalid",
            json.dumps({"scope_id": "scope-123"}).encode("utf-8"),
            "scope_artifact_invalid",
        ),
    ],
)
async def test_security_pentest_execute_maps_unreadable_scope_artifacts(
    artifact_id: str,
    artifact_payload: bytes | None,
    message: str,
):
    artifacts = {} if artifact_payload is None else {artifact_id: artifact_payload}
    launcher = _FakePentestLauncher()
    activities = TemporalAgentRuntimeActivities(
        artifact_service=_FakePentestArtifactService(artifacts),
        workload_launcher=launcher,
        workload_registry=_RecordingPentestRegistry(),
    )

    result = await activities.security_pentest_execute(
        _pentest_artifact_activity_payload(scope_artifact_ref=artifact_id)
    )

    assert result["status"] == "validation_failed"
    assert "INVALID_SCOPE" in str(result["diagnostics"])
    assert message in str(result["diagnostics"])
    assert launcher.requests == []

async def test_security_pentest_execute_rejects_ordinary_inline_scope_without_artifact():
    launcher = _FakePentestLauncher()
    activities = TemporalAgentRuntimeActivities(workload_launcher=launcher)

    result = await activities.security_pentest_execute(
        _pentest_activity_payload(
            scope_artifact_ref="",
            approved_scope=_approved_pentest_scope(),
            trusted_internal_execution=False,
        )
    )

    assert result["status"] == "validation_failed"
    diagnostics = str(result["diagnostics"])
    assert "INVALID_SCOPE" in diagnostics
    assert (
        "scope_artifact_ref" in diagnostics
        or "inline_scope_requires_trusted_internal_execution" in diagnostics
    )
    assert launcher.requests == []

async def test_security_pentest_execute_allows_trusted_internal_inline_scope():
    activities = TemporalAgentRuntimeActivities(
        workload_launcher=_FakePentestLauncher(),
        workload_registry=_RecordingPentestRegistry(),
    )

    result = await activities._security_pentest_execute_trusted_internal(
        _pentest_activity_payload(
            scope_artifact_ref=None,
            trusted_internal_execution=True,
        )
    )

    assert result["status"] == "completed"
    assert result["launch_plan"]["profile_id"] == PENTEST_CLAUDE_OAUTH_RUNNER_PROFILE_ID


async def test_security_pentest_execute_ignores_caller_supplied_trust_flag():
    """A registry-dispatched submission cannot self-authorize an inline scope."""

    launcher = _FakePentestLauncher()
    activities = TemporalAgentRuntimeActivities(workload_launcher=launcher)

    result = await activities.security_pentest_execute(
        _pentest_activity_payload(
            scope_artifact_ref=None,
            trusted_internal_execution=True,
        )
    )

    assert result["status"] == "validation_failed"
    diagnostics = str(result["diagnostics"])
    assert "INVALID_SCOPE" in diagnostics
    assert (
        "inline_scope_requires_trusted_internal_execution" in diagnostics
        or "scope_artifact_ref" in diagnostics
    )
    assert launcher.requests == []

@pytest.mark.parametrize(
    ("scope_overrides", "request_overrides", "error_code", "reason"),
    [
        ({"expires_at": "2020-01-01T00:00:00Z"}, {}, "INVALID_SCOPE", "scope_expired"),
        ({}, {"principal_id": "user-other"}, "PERMISSION_DENIED", "principal_not_authorized"),
        (
            {"targets": [{"kind": "url", "value": "https://other.example.test"}]},
            {},
            "UNAPPROVED_TARGET",
            "target_outside_scope",
        ),
        (
            {"allowed_runner_profiles": ["missing-profile"]},
            {},
            "UNSUPPORTED_PROFILE",
            "runner_profile_not_allowed",
        ),
        (
            {"allowed_actions": ["auth_testing"]},
            {},
            "UNSUPPORTED_PROFILE",
            "operation_mode_not_allowed",
        ),
        (
            {"required_network_attachment_type": "vpn"},
            {},
            "UNSUPPORTED_PROFILE",
            "network_attachment_required",
        ),
        (
            {"metadata": {"idempotency_safety": "ambiguous"}},
            {},
            "NON_IDEMPOTENT_OPERATION",
            "idempotency_safety_ambiguous",
        ),
    ],
)
async def test_security_pentest_execute_returns_validation_failure_before_side_effects(
    scope_overrides: dict[str, object],
    request_overrides: dict[str, object],
    error_code: str,
    reason: str,
):
    launcher = _FakePentestLauncher()
    activities = TemporalAgentRuntimeActivities(
        artifact_service=_FakePentestArtifactService(
            {"art_scope_valid": _scope_artifact_bytes(**scope_overrides)}
        ),
        workload_launcher=launcher,
        workload_registry=_RecordingPentestRegistry(),
    )

    result = await activities.security_pentest_execute(
        _pentest_artifact_activity_payload(**request_overrides)
    )

    assert result["status"] == "validation_failed"
    diagnostics = str(result["diagnostics"])
    assert error_code in diagnostics
    assert reason in diagnostics
    assert launcher.requests == []

async def test_security_pentest_execute_denies_without_retry_when_workflow_docker_disabled():
    activities = TemporalAgentRuntimeActivities(workflow_docker_mode="disabled")

    with pytest.raises(temporal_exceptions.ApplicationError) as exc_info:
        await activities.security_pentest_execute(_pentest_activity_payload())

    message = str(exc_info.value)
    assert "docker_workflows_disabled" in message
    assert "policy_denied" in message
    assert exc_info.value.type == "docker_workflows_disabled"
    assert exc_info.value.non_retryable is True

async def test_security_pentest_execute_reaches_launch_plan_after_scope_validation():
    launcher = _FakePentestLauncher()
    registry = _RecordingPentestRegistry()
    activities = TemporalAgentRuntimeActivities(
        workload_launcher=launcher,
        workload_registry=registry,
    )

    result = await activities._security_pentest_execute_trusted_internal(
        _pentest_activity_payload()
    )

    assert result["status"] == "completed"
    assert result["launch_plan"]["profile_id"] == PENTEST_CLAUDE_OAUTH_RUNNER_PROFILE_ID
    assert len(launcher.requests) == 1
    # The registry must validate the parsed request before the launcher runs it,
    # and the launcher must receive the validated request (not the raw one).
    assert len(registry.requests) == 1
    assert isinstance(launcher.requests[0], ValidatedWorkloadRequest)

async def test_security_pentest_execute_returns_safe_launch_plan_after_scope_validation():
    launcher = _FakePentestLauncher()
    activities = TemporalAgentRuntimeActivities(
        workload_launcher=launcher,
        workload_registry=_RecordingPentestRegistry(),
    )

    result = await activities._security_pentest_execute_trusted_internal(
        _pentest_activity_payload()
    )

    assert result["status"] == "completed"
    assert result["target"] == "https://lab.example.test"
    assert result["runner_profile_id"] == PENTEST_CLAUDE_OAUTH_RUNNER_PROFILE_ID
    launch_plan = result["launch_plan"]
    assert launch_plan["profile_id"] == PENTEST_CLAUDE_OAUTH_RUNNER_PROFILE_ID
    assert launch_plan["container_name"] == "mm-pentest-run-123-step-pentest-1"
    assert launch_plan["network_policy"] == "bridge_approved_lab"
    assert launch_plan["linux_capabilities"] == []
    assert launch_plan["devices"] == []
    assert launch_plan["labels"]["moonmind.tool_name"] == "security.pentest.run"
    assert launch_plan["labels"]["moonmind.runtime_id"] == "pentestgpt"
    assert launch_plan["labels"]["moonmind.operation_mode"] == "validate_hypothesis"
    validated_request = launcher.requests[0]
    assert validated_request.request.runtime_id == "pentestgpt"
    assert validated_request.ownership.labels["moonmind.runtime_id"] == "pentestgpt"

async def test_security_pentest_execute_includes_secret_safe_provider_preparation():
    activities = TemporalAgentRuntimeActivities(
        workload_launcher=_FakePentestLauncher(),
        workload_registry=_RecordingPentestRegistry(),
    )

    result = await activities._security_pentest_execute_trusted_internal(
        _pentest_activity_payload()
    )

    provider_profile = result["provider_profile"]
    assert provider_profile["profile_id"] == PENTEST_CLAUDE_OAUTH_PROFILE_ID
    assert provider_profile["runtime_id"] == "pentestgpt"
    assert provider_profile["provider_id"] == "anthropic"
    assert provider_profile["env"]["PENTESTGPT_AUTH_MODE"] == "manual"
    assert provider_profile["env"]["LANGFUSE_ENABLED"] == "false"
    assert provider_profile["env"]["CLAUDE_HOME"] == "/home/pentester/.claude"
    assert provider_profile["secret_env"] == {}
    assert provider_profile["secret_refs"] == {}
    assert set(provider_profile["clear_env_keys"]) == {
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "CLAUDE_API_KEY",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "OPENAI_API_KEY",
    }
    assert provider_profile["force_non_interactive"] is True
    assert result["provider_lease"]["runtime_id"] == "claude_code"
    assert result["provider_lease"]["profile_id"] == "claude_anthropic"
    assert result["provider_lease"]["lease_required"] is True
    assert "sk-" not in str(result)

async def test_security_pentest_execute_does_not_resolve_secret_refs_for_oauth(
    monkeypatch: pytest.MonkeyPatch,
):
    resolved_refs: list[object] = []

    async def _fake_resolver(ref: object, *, field_name: str) -> str:
        resolved_refs.append(ref)
        raise AssertionError("OAuth Pentest path must not resolve API key secrets")

    monkeypatch.setattr(
        "moonmind.workflows.temporal.activity_runtime.resolve_managed_api_key_reference",
        _fake_resolver,
    )
    launcher = _FakePentestLauncher()
    activities = TemporalAgentRuntimeActivities(
        workload_launcher=launcher,
        workload_registry=_RecordingPentestRegistry(),
    )

    result = await activities._security_pentest_execute_trusted_internal(
        _pentest_activity_payload()
    )

    assert result["status"] == "completed"
    assert resolved_refs == []
    request = launcher.requests[0].request
    assert "ANTHROPIC_API_KEY" not in request.env_overrides
    assert request.env_overrides["PENTESTGPT_AUTH_MODE"] == "manual"

async def test_security_pentest_execute_honors_oauth_provider_runtime_state():
    activities = TemporalAgentRuntimeActivities(
        workload_launcher=_FakePentestLauncher(),
        workload_registry=_RecordingPentestRegistry(),
    )

    result = await activities._security_pentest_execute_trusted_internal(
        _pentest_activity_payload(
            execution_profile_ref=None,
            provider_selector={"tags_all": ["oauth"]},
            provider_runtime_state={
                PENTEST_CLAUDE_OAUTH_PROFILE_ID: {
                    "profile_id": PENTEST_CLAUDE_OAUTH_PROFILE_ID,
                    "auth_state": "connected",
                    "oauth_volume_present": True,
                }
            },
        )
    )

    assert result["provider_profile"]["profile_id"] == PENTEST_CLAUDE_OAUTH_PROFILE_ID
    assert result["provider_lease"]["profile_id"] == "claude_anthropic"

async def test_security_pentest_execute_acquires_lease_after_scope_validation_without_secret_resolution(
    monkeypatch: pytest.MonkeyPatch,
):
    order: list[str] = []
    lease_manager = _FakePentestLeaseManager()
    artifact_service = _FakePentestArtifactService(
        {"art_scope_valid": _scope_artifact_bytes()}
    )

    original_read = artifact_service.read

    async def _recording_read(**kwargs):
        order.append("scope_read")
        return await original_read(**kwargs)

    async def _fake_resolver(ref: object, *, field_name: str) -> str:
        order.append("secret_resolve")
        return "sk-resolved-provider-value"

    async def _recording_acquire(**kwargs):
        order.append("lease_acquire")
        return await _FakePentestLeaseManager.acquire(lease_manager, **kwargs)

    artifact_service.read = _recording_read  # type: ignore[method-assign]
    lease_manager.acquire = _recording_acquire  # type: ignore[method-assign]
    monkeypatch.setattr(
        "moonmind.workflows.temporal.activity_runtime.resolve_managed_api_key_reference",
        _fake_resolver,
    )
    activities = TemporalAgentRuntimeActivities(
        artifact_service=artifact_service,
        workload_launcher=_FakePentestLauncher(),
        workload_registry=_RecordingPentestRegistry(),
        pentest_provider_lease_manager=lease_manager,
    )

    result = await activities.security_pentest_execute(
        _pentest_artifact_activity_payload()
    )

    assert result["status"] == "completed"
    assert order == ["scope_read", "lease_acquire"]
    acquire_event = lease_manager.events[0]
    assert acquire_event[0] == "acquire"
    payload = acquire_event[1]
    assert payload["owner"] == "pentest:run-123:step-pentest:1"
    assert payload["metadata"] == {
        "tool": "security.pentest.run",
        "runtime_id": "claude_code",
        "profile_id": "claude_anthropic",
        "agent_run_id": "run-123",
        "step_id": "step-pentest",
        "attempt": 1,
        "target_hash": "af009b45becaa02be2c95bfdd8a055c52fc3c0ec440b24d73fb14783c9b7786e",
        "mode": "validate_hypothesis",
        "runner_profile": PENTEST_CLAUDE_OAUTH_RUNNER_PROFILE_ID,
    }
    assert "https://lab.example.test" not in str(payload["metadata"])
    assert "sk-resolved-provider-value" not in str(result)

async def test_security_pentest_execute_does_not_acquire_lease_for_invalid_scope():
    lease_manager = _FakePentestLeaseManager()
    launcher = _FakePentestLauncher()
    activities = TemporalAgentRuntimeActivities(
        artifact_service=_FakePentestArtifactService(
            {"art_scope_valid": _scope_artifact_bytes(expires_at="2020-01-01T00:00:00Z")}
        ),
        workload_launcher=launcher,
        workload_registry=_RecordingPentestRegistry(),
        pentest_provider_lease_manager=lease_manager,
    )

    result = await activities.security_pentest_execute(
        _pentest_artifact_activity_payload()
    )

    assert result["status"] == "validation_failed"
    assert lease_manager.events == []
    assert launcher.requests == []

async def test_security_pentest_execute_releases_acquired_lease_on_success():
    lease_manager = _FakePentestLeaseManager()
    activities = TemporalAgentRuntimeActivities(
        workload_launcher=_FakePentestLauncher(),
        workload_registry=_RecordingPentestRegistry(),
        pentest_provider_lease_manager=lease_manager,
    )

    result = await activities._security_pentest_execute_trusted_internal(
        _pentest_activity_payload()
    )

    assert result["status"] == "completed"
    assert [event[0] for event in lease_manager.events] == ["acquire", "release"]
    assert result["terminal_cleanup"]["provider_lease_released"] is True
    assert result["provider_lease"]["lease_id"] == "lease:pentest:run-123:step-pentest:1"

async def test_security_pentest_execute_releases_acquired_lease_on_workload_failure():
    lease_manager = _FakePentestLeaseManager()
    activities = TemporalAgentRuntimeActivities(
        workload_launcher=_FakePentestLauncher(status="failed"),
        workload_registry=_RecordingPentestRegistry(),
        pentest_provider_lease_manager=lease_manager,
    )

    result = await activities._security_pentest_execute_trusted_internal(
        _pentest_activity_payload()
    )

    assert result["status"] == "failed"
    assert [event[0] for event in lease_manager.events] == ["acquire", "release"]
    assert result["terminal_cleanup"]["provider_lease_released"] is True

async def test_security_pentest_execute_reports_secret_safe_provider_cooldown():
    lease_manager = _FakePentestLeaseManager()
    activities = TemporalAgentRuntimeActivities(
        workload_launcher=_FakePentestLauncher(),
        workload_registry=_RecordingPentestRegistry(),
        pentest_provider_lease_manager=lease_manager,
    )

    result = await activities._security_pentest_execute_trusted_internal(
        _pentest_activity_payload(
            provider_failure={"category": "provider_429"},
        )
    )

    assert result["status"] == "provider_cooldown"
    assert result["provider_cooldown"] == {
        "profile_id": "claude_anthropic",
        "cooldown_seconds": 300,
        "failure_category": "provider_429",
        "retry_allowed": False,
    }
    assert result["provider_lease"]["release_required"] is True
    assert [event[0] for event in lease_manager.events] == [
        "acquire",
        "cooldown",
        "release",
    ]
    assert lease_manager.events[1][1]["reason"] == "provider_429"
    assert result["terminal_cleanup"]["provider_lease_released"] is True
    assert "OPENROUTER_API_KEY" not in str(result["provider_cooldown"])
    assert "sk-" not in str(result)

async def test_security_pentest_execute_classifies_lease_manager_failure_as_provider_capacity():
    class _FailingLeaseManager(_FakePentestLeaseManager):
        async def acquire(self, **kwargs) -> str:
            self.events.append(("acquire", dict(kwargs)))
            raise RuntimeError("slot service unavailable")

    lease_manager = _FailingLeaseManager()
    launcher = _FakePentestLauncher()
    activities = TemporalAgentRuntimeActivities(
        workload_launcher=launcher,
        workload_registry=_RecordingPentestRegistry(),
        pentest_provider_lease_manager=lease_manager,
    )

    result = await activities._security_pentest_execute_trusted_internal(
        _pentest_activity_payload()
    )

    assert result["status"] == "provider_capacity_failed"
    assert result["failure_classification"]["failure_kind"] == "provider_capacity"
    assert result["failure_classification"]["interaction_state"] == "pre_interaction"
    assert [event[0] for event in lease_manager.events] == ["acquire"]
    assert launcher.requests == []

async def test_security_pentest_execute_includes_instruction_materialization_metadata():
    activities = TemporalAgentRuntimeActivities(
        workload_launcher=_FakePentestLauncher(),
        workload_registry=_RecordingPentestRegistry(),
    )

    result = await activities._security_pentest_execute_trusted_internal(
        _pentest_activity_payload()
    )

    bundle = result["instruction_bundle"]
    paths = result["runtime_paths"]
    invocation = result["wrapper_invocation"]

    assert bundle["target"] == "https://lab.example.test"
    assert "objective" not in bundle
    assert bundle["operation_mode"] == "validate_hypothesis"
    assert "content" not in bundle
    assert len(bundle["sha256"]) == 64
    assert paths["instruction_file"] == "/tmp/artifacts/pentest/inputs/instruction.txt"
    assert paths["input_manifest_file"] == "/tmp/artifacts/pentest/inputs/request.json"
    assert paths["approved_scope_file"] == (
        "/tmp/artifacts/pentest/inputs/approved-scope.json"
    )
    assert paths["provider_snapshot_file"] == (
        "/tmp/artifacts/pentest/inputs/provider-snapshot.json"
    )
    assert paths["stdout_file"] == "/tmp/artifacts/pentest/runtime/stdout.log"
    assert paths["stderr_file"] == "/tmp/artifacts/pentest/runtime/stderr.log"
    assert paths["diagnostics_file"] == "/tmp/artifacts/pentest/runtime/diagnostics.json"
    assert paths["raw_evidence_file"] == (
        "/tmp/artifacts/pentest/evidence/pentestgpt-session-export.json"
    )
    assert paths["normalizer_input_file"] == (
        "/tmp/artifacts/pentest/findings/findings.normalizer-input.json"
    )
    assert invocation["command"][0] == "--target"
    assert "--non-interactive" in invocation["command"]
    assert "--instruction-file" in invocation["command"]
    assert invocation["env"] == {
        "MM_PENTEST_TARGET": "https://lab.example.test",
        "MM_PENTEST_MODE": "validate_hypothesis",
        "MM_PENTEST_INSTRUCTION_FILE": paths["instruction_file"],
        "LANGFUSE_ENABLED": "false",
    }
    assert "Validate auth bypass hypothesis" not in str(result)
    assert "Objective:" not in str(invocation["command"])
    assert "Objective:" not in str(invocation["env"])
    assert "Objective:" not in str(result["launch_plan"]["labels"])
    assert "make connect" not in str(result).lower()
    assert "docker attach" not in str(result).lower()

async def test_security_pentest_execute_includes_publication_metadata_without_session_artifacts(
    tmp_path: Path,
):
    # The runner's structured findings file is authoritative for the published
    # normalized findings, so the launcher writes it rather than relying on
    # caller-supplied publication-time findings.
    activities = TemporalAgentRuntimeActivities(
        workload_launcher=_FakePentestLauncher(
            findings_payload={
                "target": "https://lab.example.test",
                "operation_mode": "validate_hypothesis",
                "runner_profile_id": PENTEST_CLAUDE_OAUTH_RUNNER_PROFILE_ID,
                "findings": [
                    {
                        "finding_id": "finding-1",
                        "title": "Supported issue",
                        "severity": "high",
                        "confidence": "supported",
                        "target": "https://lab.example.test",
                        "summary": "Evidence supports issue",
                    }
                ],
                "summary": {
                    "findings_count": 1,
                    "confirmed_findings_count": 0,
                    "high_or_critical_count": 1,
                },
            }
        ),
        workload_registry=_RecordingPentestRegistry(),
    )

    result = await activities._security_pentest_execute_trusted_internal(
        _pentest_activity_payload(
            artifacts_dir=str(tmp_path / "artifacts"),
            summary_text="Pentest finished with password=hunter2",
            findings=[],
            provider_snapshot_available=True,
            wrapper_log_available=True,
        )
    )

    publication = result["artifact_publication"]
    finding_set = result["normalized_findings"]
    live_logs = result["live_log_events"]
    artifact_names = {item["name"] for item in publication["artifacts"]}

    assert publication["status"] == "complete"
    assert {
        "input.manifest",
        "input.instructions",
        "runtime.stdout",
        "runtime.stderr",
        "runtime.diagnostics",
        "report.summary",
        "report.primary",
        "report.structured",
        "output.provider_snapshot",
        "output.logs",
        "report.evidence",
    }.issubset(artifact_names)
    assert result["report_bundle_v"] == 1
    assert result["primary_report_ref"]
    assert result["summary_ref"]
    assert result["structured_ref"]
    assert result["evidence_refs"]
    assert result["report_bundle"]["report_type"] == "security_pentest_report"
    assert result["report_bundle"]["report_scope"] == "final"
    assert result["report_bundle"]["sensitivity"] == "security_restricted"
    assert result["report_bundle"]["counts"]["high_or_critical_count"] == 1
    assert result["severity_counts"] == {"high": 1}
    assert "session.summary" not in artifact_names
    assert "session.step_checkpoint" not in artifact_names
    assert "session.control_event" not in artifact_names
    assert "session.reset_boundary" not in artifact_names
    assert publication["restricted_evidence_refs"]
    assert finding_set["summary"] == {
        "findings_count": 1,
        "confirmed_findings_count": 0,
        "high_or_critical_count": 1,
    }
    assert finding_set["findings"][0]["confidence"] == "supported"
    assert finding_set["normalization_status"] == "ok"
    assert result["normalization_status"] == "ok"
    assert result["quarantined_findings_count"] == 0
    assert result["heartbeat_phases"] == [
        "validating_scope",
        "waiting_for_profile_slot",
        "materializing_inputs",
        "launching_container",
        "running",
        "publishing_artifacts",
        "normalizing_findings",
        "cleanup",
    ]
    assert any(event["event_type"] == "artifact" for event in live_logs)
    assert any(event["event_type"] == "annotation" for event in live_logs)
    assert "hunter2" not in str(result)
    assert "terminal_control" not in str(live_logs)
    assert "docker attach" not in str(result).lower()

async def test_security_pentest_execute_publishes_runner_outputs_through_artifact_service(
    tmp_path: Path,
):
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifact-store"),
            )
            activities = TemporalAgentRuntimeActivities(
                artifact_service=service,
                workload_launcher=_FileWritingPentestLauncher(),
                workload_registry=_RecordingPentestRegistry(),
            )

            result = await activities._security_pentest_execute_trusted_internal(
                _pentest_activity_payload(
                    artifacts_dir=str(tmp_path / "runner-artifacts"),
                    findings=[],
                    provider_snapshot_available=True,
                )
            )

            assert result["status"] == "completed"
            artifact_ref_fields = {
                "stdout_artifact_ref",
                "stderr_artifact_ref",
                "diagnostics_artifact_ref",
                "provider_snapshot_artifact_ref",
                "primary_report_ref",
                "summary_ref",
                "structured_ref",
                "evidence_bundle_artifact_ref",
            }
            for field in artifact_ref_fields:
                assert str(result[field]).startswith("art_"), field

            validate_report_bundle_result(result["report_bundle"])
            assert result["report_bundle"]["counts"] == {
                "findings_count": 1,
                "confirmed_findings_count": 1,
                "high_or_critical_count": 1,
                "severity_counts": {"high": 1},
            }
            assert "findings_count" not in result["report_bundle"]
            assert "high_or_critical_count" not in result["report_bundle"]

            expected_link_types = {
                "runtime.stdout",
                "runtime.stderr",
                "runtime.diagnostics",
                "output.provider_snapshot",
                "report.primary",
                "report.summary",
                "report.structured",
                "report.evidence",
            }
            by_link_type = {}
            for link_type in expected_link_types:
                artifacts = await service.list_for_execution(
                    namespace="default",
                    workflow_id="run-123",
                    run_id="step-pentest-1",
                    principal="user-security",
                    link_type=link_type,
                    latest_only=True,
                )
                assert artifacts, link_type
                by_link_type[link_type] = artifacts[0]
            assert by_link_type["report.primary"].metadata_json["is_final_report"] is True
            assert (
                by_link_type["report.primary"].metadata_json["sensitivity"]
                == "security_restricted"
            )
            _structured_artifact, structured_payload = await service.read(
                artifact_id=result["report_bundle"]["structured_ref"]["artifact_id"],
                principal="user-security",
                allow_restricted_raw=True,
            )
            assert isinstance(structured_payload, bytes)
            assert json.loads(structured_payload.decode("utf-8")) == result[
                "normalized_findings"
            ]


async def test_security_pentest_execute_preserves_report_refs_for_non_clean_runner_status(
    tmp_path: Path,
):
    target_url = "https://lab.example.test/app"

    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifact-store"),
            )
            activities = TemporalAgentRuntimeActivities(
                artifact_service=service,
                workload_launcher=_FileWritingPentestLauncher(status="failed"),
                workload_registry=_RecordingPentestRegistry(),
            )

            result = await activities._security_pentest_execute_trusted_internal(
                _pentest_activity_payload(
                    artifacts_dir=str(tmp_path / "runner-artifacts"),
                    target=target_url,
                    approved_scope={
                        **_approved_pentest_scope(),
                        "targets": [
                            {
                                "kind": "url",
                                "value": target_url,
                            }
                        ],
                    },
                )
            )

            assert result["status"] == "failed"
            assert result["normalization_status"] == "runner_failed"
            assert result["primary_report_ref"].startswith("art_")
            assert result["summary_ref"].startswith("art_")
            assert result["structured_ref"].startswith("art_")
            assert result["evidence_refs"]
            assert result["report_bundle"]["counts"]["findings_count"] == 0
            assert result["terminal_cleanup"]["terminal_reason"] == "failure"

            artifacts = await service.list_for_execution(
                namespace="default",
                workflow_id="run-123",
                run_id="step-pentest-1",
                principal="user-security",
                link_type="report.primary",
                latest_only=True,
            )
            assert artifacts


async def test_security_pentest_execute_publishes_parsed_structured_findings_when_file_is_malformed(
    tmp_path: Path,
):
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifact-store"),
            )
            activities = TemporalAgentRuntimeActivities(
                artifact_service=service,
                workload_launcher=_MalformedFindingsFileLauncher(),
                workload_registry=_RecordingPentestRegistry(),
            )

            result = await activities._security_pentest_execute_trusted_internal(
                _pentest_activity_payload(
                    artifacts_dir=str(tmp_path / "runner-artifacts"),
                    findings=[],
                    provider_snapshot_available=True,
                )
            )

            assert result["status"] == "failed"
            assert result["normalization_status"] == "normalizer_error"
            _structured_artifact, structured_payload = await service.read(
                artifact_id=result["report_bundle"]["structured_ref"]["artifact_id"],
                principal="user-security",
                allow_restricted_raw=True,
            )
            assert json.loads(structured_payload.decode("utf-8")) == result[
                "normalized_findings"
            ]
            assert structured_payload != b"{malformed-json"


async def test_security_pentest_execute_rejects_missing_report_bundle_files(
    tmp_path: Path,
):
    class _MissingSummaryReportLauncher(_FileWritingPentestLauncher):
        async def run(self, request: object) -> WorkloadResult:
            result = await super().run(request)
            workload_request = getattr(request, "request", request)
            artifacts_dir = Path(str(getattr(workload_request, "artifacts_dir")))
            (
                artifacts_dir / "pentest" / "findings" / "findings.summary.md"
            ).unlink()
            return result

    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifact-store"),
            )
            activities = TemporalAgentRuntimeActivities(
                artifact_service=service,
                workload_launcher=_MissingSummaryReportLauncher(),
                workload_registry=_RecordingPentestRegistry(),
            )

            with pytest.raises(
                TemporalArtifactValidationError,
                match="Pentest summary report file was not found",
            ):
                await activities._security_pentest_execute_trusted_internal(
                    _pentest_activity_payload(
                        artifacts_dir=str(tmp_path / "runner-artifacts"),
                        findings=[],
                        provider_snapshot_available=True,
                    )
                )


async def test_security_pentest_report_bundle_rejects_unsafe_custom_keys():
    unsafe_cases = (
        {"raw_log": "inline log"},
        {"finding_details": {"body": "raw finding"}},
        {"presigned_url": "https://example.invalid/raw"},
        {"evidence_body": "raw evidence"},
    )

    for unsafe in unsafe_cases:
        bundle = {"report_bundle_v": 1, **unsafe}
        with pytest.raises(TemporalArtifactValidationError, match="unsafe report bundle"):
            validate_report_bundle_result(bundle)

async def test_security_pentest_serialized_result_and_metadata_stay_compact_and_secret_safe(
    tmp_path: Path,
):
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifact-store"),
            )
            activities = TemporalAgentRuntimeActivities(
                artifact_service=service,
                workload_launcher=_FileWritingPentestLauncher(),
                workload_registry=_RecordingPentestRegistry(),
            )

            result = await activities._security_pentest_execute_trusted_internal(
                _pentest_activity_payload(
                    artifacts_dir=str(tmp_path / "runner-artifacts"),
                    summary_text="Pentest finished with token=ghp_summaryshouldnotleak",
                    provider_snapshot_available=True,
                )
            )

            serialized = json.dumps(result, sort_keys=True)
            assert len(serialized.encode("utf-8")) < 60000
            blocked_patterns = (
                "raw-output-marker-should-not-leak",
                "raw-error-marker-should-not-leak",
                "evidence-marker-should-not-leak",
                "ghp_summaryshouldnotleak",
                "presigned_url",
                "finding_details",
                "evidence_body",
                "session.summary",
                "session.step_checkpoint",
                "session.control_event",
                "session.reset_boundary",
            )
            for pattern in blocked_patterns:
                assert pattern not in serialized

            artifacts = await service.list_for_execution(
                namespace="default",
                workflow_id="run-123",
                run_id="step-pentest-1",
                principal="user-security",
                latest_only=False,
            )
            metadata_payload = json.dumps(
                [artifact.metadata_json for artifact in artifacts],
                sort_keys=True,
            )
            for pattern in blocked_patterns:
                assert pattern not in metadata_payload

async def test_pentest_heartbeat_emitter_returns_redacted_compact_payload(
    monkeypatch: pytest.MonkeyPatch,
):
    heartbeats: list[dict[str, object]] = []
    monkeypatch.setattr(
        activity_runtime_module.temporal_activity,
        "heartbeat",
        lambda payload: heartbeats.append(payload),
    )

    payload = emit_pentest_activity_heartbeat(
        phase="running",
        agent_run_id="run-123",
        step_id="step-pentest",
        attempt=1,
        message="Still running with token=hunter2",
        metadata={
            "container_name": "mm-pentest-run-123-step-pentest-1",
            "env": {"ANTHROPIC_API_KEY": "secret"},
            "stdout": "raw output",
            "api_key": "hunter2",
        },
        elapsed_seconds=1.23456,
    )

    assert heartbeats == [payload]
    assert payload["phase"] == "running"
    assert payload["elapsed_seconds"] == 1.235
    assert payload["metadata"]["container_name"] == "mm-pentest-run-123-step-pentest-1"
    assert "env" not in payload.get("metadata", {})
    assert "stdout" not in payload.get("metadata", {})
    assert "hunter2" not in str(payload)

async def test_security_pentest_execute_supervised_handle_emits_running_heartbeats(
    monkeypatch: pytest.MonkeyPatch,
):
    heartbeats: list[dict[str, object]] = []
    monkeypatch.setattr(
        activity_runtime_module,
        "_PENTEST_RUNNING_HEARTBEAT_INTERVAL_SECONDS",
        0.001,
    )
    monkeypatch.setattr(
        activity_runtime_module.temporal_activity,
        "heartbeat",
        lambda payload: heartbeats.append(payload),
    )
    handle = _SupervisedPentestHandle(result_after_polls=2)
    launcher = _SupervisedPentestLauncher(handle)
    activities = TemporalAgentRuntimeActivities(
        workload_launcher=launcher,
        workload_registry=_RecordingPentestRegistry(),
    )

    result = await activities._security_pentest_execute_trusted_internal(
        _pentest_activity_payload()
    )

    assert result["status"] == "completed"
    phases = [heartbeat["phase"] for heartbeat in heartbeats]
    assert phases == [
        "validating_scope",
        "waiting_for_profile_slot",
        "materializing_inputs",
        "launching_container",
        "running",
        "running",
        "publishing_artifacts",
        "normalizing_findings",
        "cleanup",
    ]
    assert handle.polls >= 2
    for heartbeat in heartbeats:
        assert "ANTHROPIC_API_KEY" not in str(heartbeat)
        assert "hunter2" not in str(heartbeat)

async def test_security_pentest_execute_emits_publication_heartbeat_after_runtime(
    monkeypatch: pytest.MonkeyPatch,
):
    heartbeats: list[dict[str, object]] = []
    monkeypatch.setattr(
        activity_runtime_module,
        "_PENTEST_RUNNING_HEARTBEAT_INTERVAL_SECONDS",
        0.001,
    )
    monkeypatch.setattr(
        activity_runtime_module.temporal_activity,
        "heartbeat",
        lambda payload: heartbeats.append(payload),
    )
    activities = TemporalAgentRuntimeActivities(
        workload_launcher=_FakePentestLauncher(),
        workload_registry=_RecordingPentestRegistry(),
    )

    result = await activities._security_pentest_execute_trusted_internal(
        _pentest_activity_payload()
    )

    assert result["status"] == "completed"
    phases = [heartbeat["phase"] for heartbeat in heartbeats]
    assert phases == [
        "validating_scope",
        "waiting_for_profile_slot",
        "materializing_inputs",
        "launching_container",
        "running",
        "publishing_artifacts",
        "normalizing_findings",
        "cleanup",
    ]
    assert phases.index("publishing_artifacts") > phases.index("running")
    assert phases.index("normalizing_findings") > phases.index(
        "publishing_artifacts"
    )

async def test_supervised_pentest_workload_timeout_stops_removes_and_returns_cleanup():
    handle = _SupervisedPentestHandle(result_after_polls=None)
    launcher = _SupervisedPentestLauncher(handle)
    request_payload = dict(_pentest_activity_payload()["request"])
    request_payload.pop("pentest_enabled", None)
    request = activity_runtime_module.PentestWorkloadRequest.model_validate(
        request_payload
    )
    result = await activity_runtime_module._supervise_pentest_workload_with_activity_heartbeats(
        launcher,
        SimpleNamespace(
            container_name="mm-pentest-run-123-step-pentest-1",
            profile=SimpleNamespace(
                cleanup=SimpleNamespace(kill_grace_seconds=7),
            ),
        ),
        request=request,
        timeout_seconds=0.001,
        heartbeat_interval_seconds=0.001,
    )

    assert result.status == "timed_out"
    assert result.timeout_reason == "workload exceeded timeoutSeconds"
    assert handle.stops == [7]
    assert handle.removed is True
    assert result.metadata["cleanup"]["terminalReason"] == "timeout"
    assert result.metadata["cleanup"]["gracefulTerminationAttempted"] is True
    assert result.metadata["cleanup"]["killEscalated"] is True
    assert result.metadata["cleanup"]["containerRemoved"] is True

async def test_supervised_pentest_workload_cancellation_stops_and_removes():
    handle = _SupervisedPentestHandle(result_after_polls=None)
    launcher = _SupervisedPentestLauncher(handle)
    request_payload = dict(_pentest_activity_payload()["request"])
    request_payload.pop("pentest_enabled", None)
    request = activity_runtime_module.PentestWorkloadRequest.model_validate(
        request_payload
    )

    task = asyncio.create_task(
        activity_runtime_module._supervise_pentest_workload_with_activity_heartbeats(
            launcher,
            SimpleNamespace(container_name="mm-pentest-run-123-step-pentest-1"),
            request=request,
            timeout_seconds=60,
            heartbeat_interval_seconds=0.001,
        )
    )
    await asyncio.sleep(0)
    task.cancel()

    cancellation_result = await asyncio.gather(task, return_exceptions=True)

    assert handle.stops == [30]
    assert handle.removed is True
    assert isinstance(cancellation_result[0], asyncio.CancelledError)

async def test_supervised_pentest_workload_poll_error_stops_and_removes():
    handle = _SupervisedPentestHandle(
        result_after_polls=None,
        poll_error=RuntimeError("docker daemon unavailable"),
    )
    launcher = _SupervisedPentestLauncher(handle)
    request_payload = dict(_pentest_activity_payload()["request"])
    request_payload.pop("pentest_enabled", None)
    request = activity_runtime_module.PentestWorkloadRequest.model_validate(
        request_payload
    )

    with pytest.raises(RuntimeError, match="docker daemon unavailable"):
        await activity_runtime_module._supervise_pentest_workload_with_activity_heartbeats(
            launcher,
            SimpleNamespace(container_name="mm-pentest-run-123-step-pentest-1"),
            request=request,
            timeout_seconds=60,
            heartbeat_interval_seconds=0.001,
        )

    assert handle.stops == [30]
    assert handle.removed is True

async def test_pentest_orphan_cleanup_uses_deterministic_label_selector():
    class _Janitor:
        def __init__(self) -> None:
            self.selector: dict[str, str] | None = None
            self.removed: list[str] = []

        async def find_by_labels(self, labels: dict[str, str]) -> tuple[str, ...]:
            self.selector = dict(labels)
            return ("container-1", "container-2")

        async def remove(self, container_id: str) -> None:
            self.removed.append(container_id)

    janitor = _Janitor()

    result = await cleanup_pentest_orphan_containers(
        janitor,
        agent_run_id="run-123",
        step_id="step-pentest",
        runner_profile_id=PENTEST_CLAUDE_OAUTH_RUNNER_PROFILE_ID,
    )

    assert janitor.selector == {
        "moonmind.kind": "workload",
        "moonmind.tool_name": "security.pentest.run",
        "moonmind.runtime_id": "pentestgpt",
        "moonmind.agent_run_id": "run-123",
        "moonmind.step_id": "step-pentest",
        "moonmind.workload_profile": PENTEST_CLAUDE_OAUTH_RUNNER_PROFILE_ID,
    }
    assert janitor.removed == ["container-1", "container-2"]
    assert result["removed_count"] == 2

async def test_security_pentest_execute_coerces_string_publication_flags():
    activities = TemporalAgentRuntimeActivities(
        workload_launcher=_FakePentestLauncher(),
        workload_registry=_RecordingPentestRegistry(),
    )

    result = await activities._security_pentest_execute_trusted_internal(
        _pentest_activity_payload(
            provider_snapshot_available="false",
            wrapper_log_available="0",
        )
    )

    publication = result["artifact_publication"]
    artifact_names = {item["name"] for item in publication["artifacts"]}
    assert "output.provider_snapshot" in artifact_names
    assert "output.logs" not in artifact_names
    assert publication["omitted_optional_artifacts"] == ["output.logs"]

async def test_pentest_settings_parse_safe_policy_csv_values():
    parsed = PentestSettings(
        MOONMIND_PENTEST_ENABLED="true",
        MOONMIND_PENTEST_ALLOWED_RUNNER_PROFILES="pentestgpt-claude-oauth",
        MOONMIND_PENTEST_ALLOWED_OPERATION_MODES="recon_only,validate_hypothesis",
        MOONMIND_PENTEST_ALLOWED_EVIDENCE_LEVELS="minimal,standard",
        MOONMIND_PENTEST_MAX_TIME_BUDGET_MINUTES="120",
        MOONMIND_PENTEST_PROVIDER_LEASE_SECONDS="",
    )

    assert parsed.enabled is True
    assert parsed.allowed_runner_profiles == (
        PENTEST_CLAUDE_OAUTH_RUNNER_PROFILE_ID,
    )
    assert parsed.allowed_operation_modes == ("recon_only", "validate_hypothesis")
    assert parsed.allowed_evidence_levels == ("minimal", "standard")
    assert parsed.max_time_budget_minutes == 120
    assert parsed.provider_lease_seconds is None
    assert (
        PentestSettings.model_fields["enabled"].json_schema_extra["moonmind"][
            "expose"
        ]
        is True
    )

async def test_pentest_settings_rejects_defaults_outside_allowlists():
    with pytest.raises(ValueError, match="default pentest operation mode"):
        PentestSettings(
            MOONMIND_PENTEST_DEFAULT_OPERATION_MODE="validate_hypothesis",
            MOONMIND_PENTEST_ALLOWED_OPERATION_MODES="recon_only",
        )
    with pytest.raises(ValueError, match="default pentest evidence level"):
        PentestSettings(
            MOONMIND_PENTEST_DEFAULT_EVIDENCE_LEVEL="full",
            MOONMIND_PENTEST_ALLOWED_EVIDENCE_LEVELS="minimal,standard",
        )
    with pytest.raises(ValueError, match="default pentest runner profile"):
        PentestSettings(
            MOONMIND_PENTEST_DEFAULT_RUNNER_PROFILE="missing-profile",
            MOONMIND_PENTEST_ALLOWED_RUNNER_PROFILES="pentestgpt-claude-oauth",
        )

async def test_pentest_workload_profile_registry_includes_claude_oauth_runner():
    registry = RunnerProfileRegistry.load_file(
        Path("config/workloads/default-runner-profiles.yaml"),
        workspace_root="/work/agent_jobs",
    )

    profile = registry.get(PENTEST_CLAUDE_OAUTH_RUNNER_PROFILE_ID)
    assert profile is not None
    assert profile.image == PENTEST_RUNNER_IMAGE
    assert profile.network_policy == "bridge"
    assert "ANTHROPIC_API_KEY" not in profile.env_allowlist
    assert "CLAUDE_HOME" in profile.env_allowlist

async def test_security_pentest_execute_rejects_configured_profiles_missing_from_registry(
    monkeypatch: pytest.MonkeyPatch,
):
    launcher = _FakePentestLauncher()
    registry = RunnerProfileRegistry.load_file(
        Path("config/workloads/default-runner-profiles.yaml"),
        workspace_root="/work/agent_jobs",
    )
    activities = TemporalAgentRuntimeActivities(
        workload_launcher=launcher,
        workload_registry=registry,
    )
    monkeypatch.setattr(
        settings.pentest,
        "allowed_runner_profiles",
        (PENTEST_CLAUDE_OAUTH_RUNNER_PROFILE_ID, "pentestgpt-missing"),
    )

    result = await activities._security_pentest_execute_trusted_internal(
        _pentest_activity_payload()
    )

    assert result["status"] == "validation_failed"
    assert "runner_profile_not_registered" in str(result["diagnostics"])
    assert "pentestgpt-missing" in str(result["diagnostics"])
    assert launcher.requests == []

async def test_security_pentest_execute_rejects_registered_runner_image_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    launcher = _FakePentestLauncher()
    registry_path = tmp_path / "profiles.json"
    registry_path.write_text(
        json.dumps(
            {
                "profiles": [
                    {
                        "id": PENTEST_CLAUDE_OAUTH_RUNNER_PROFILE_ID,
                        "kind": "one_shot",
                        "image": "ghcr.io/moonladderstudios/moonmind-pentestgpt:1.0-drift",
                        "workdirTemplate": "/work/agent_jobs/${agent_run_id}/repo",
                        "requiredMounts": [
                            {
                                "type": "volume",
                                "source": "agent_workspaces",
                                "target": "/work/agent_jobs",
                            }
                        ],
                        "envAllowlist": ["ANTHROPIC_API_KEY"],
                        "networkPolicy": "bridge",
                        "resources": {
                            "cpu": "4",
                            "memory": "8g",
                            "shmSize": "1g",
                            "maxCpu": "4",
                            "maxMemory": "8g",
                            "maxShmSize": "1g",
                        },
                        "timeoutSeconds": 28800,
                        "maxTimeoutSeconds": 28800,
                        "cleanup": {
                            "removeContainerOnExit": True,
                            "killGraceSeconds": 30,
                        },
                        "devicePolicy": {"mode": "none"},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    registry = RunnerProfileRegistry.load_file(
        registry_path,
        workspace_root="/work/agent_jobs",
    )
    activities = TemporalAgentRuntimeActivities(
        workload_launcher=launcher,
        workload_registry=registry,
    )
    monkeypatch.setattr(
        settings.pentest,
        "runner_image",
        PENTEST_RUNNER_IMAGE,
    )
    monkeypatch.setattr(
        settings.pentest,
        "allowed_runner_profiles",
        (PENTEST_CLAUDE_OAUTH_RUNNER_PROFILE_ID,),
    )
    result = await activities._security_pentest_execute_trusted_internal(
        _pentest_activity_payload()
    )

    assert result["status"] == "validation_failed"
    assert "runner_profile_image_drift" in str(result["diagnostics"])
    assert PENTEST_CLAUDE_OAUTH_RUNNER_PROFILE_ID in str(result["diagnostics"])
    assert launcher.requests == []

async def test_security_pentest_execute_validates_safe_profile_against_registry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    launcher = _FileWritingPentestLauncher()
    registry = RunnerProfileRegistry.load_file(
        Path("config/workloads/default-runner-profiles.yaml"),
        workspace_root=tmp_path,
        profile_image_overrides={
            PENTEST_CLAUDE_OAUTH_RUNNER_PROFILE_ID: PENTEST_RUNNER_IMAGE
        },
    )
    activities = TemporalAgentRuntimeActivities(
        workload_launcher=launcher,
        workload_registry=registry,
    )
    monkeypatch.setattr(
        settings.pentest,
        "runner_image",
        PENTEST_RUNNER_IMAGE,
    )
    monkeypatch.setattr(
        settings.pentest,
        "default_runner_profile",
        PENTEST_CLAUDE_OAUTH_RUNNER_PROFILE_ID,
    )
    monkeypatch.setattr(
        settings.pentest,
        "allowed_runner_profiles",
        (PENTEST_CLAUDE_OAUTH_RUNNER_PROFILE_ID,),
    )
    payload = _pentest_activity_payload()
    payload["request"]["repo_dir"] = str(tmp_path / "run-123" / "repo")
    payload["request"]["artifacts_dir"] = str(
        tmp_path / "run-123" / "artifacts" / "pentest"
    )

    result = await activities._security_pentest_execute_trusted_internal(payload)

    assert result["status"] == "completed"
    assert len(launcher.requests) == 1

async def test_security_pentest_execute_materializes_input_files_without_secrets(
    tmp_path: Path,
):
    artifacts_dir = tmp_path / "artifacts"
    activities = TemporalAgentRuntimeActivities(
        workload_launcher=_FakePentestLauncher(),
        workload_registry=_RecordingPentestRegistry(),
    )

    result = await activities._security_pentest_execute_trusted_internal(
        _pentest_activity_payload(
            artifacts_dir=str(artifacts_dir),
            summary_text="password=hunter2",
        )
    )

    assert result["status"] == "failed"
    assert result["normalization_status"] == "normalizer_error"
    instruction_file = artifacts_dir / "pentest" / "inputs" / "instruction.txt"
    manifest_file = artifacts_dir / "pentest" / "inputs" / "request.json"
    scope_file = artifacts_dir / "pentest" / "inputs" / "approved-scope.json"
    provider_file = artifacts_dir / "pentest" / "inputs" / "provider-snapshot.json"
    assert instruction_file.read_text(encoding="utf-8").startswith("Objective:")
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    provider_snapshot = json.loads(provider_file.read_text(encoding="utf-8"))
    scope_snapshot = json.loads(scope_file.read_text(encoding="utf-8"))
    assert manifest["provider_snapshot_ref"] == f"file:{provider_file}"
    assert provider_snapshot["profile_id"] == PENTEST_CLAUDE_OAUTH_PROFILE_ID
    assert "secret_env_keys" not in provider_snapshot
    assert provider_snapshot["credential_env_key_count"] == 0
    assert provider_snapshot["missing_credential_env_key_count"] == 0
    assert scope_snapshot["scope_id"] == "scope-123"
    assert "hunter2" not in provider_file.read_text(encoding="utf-8")
    assert "hunter2" not in manifest_file.read_text(encoding="utf-8")

async def test_security_pentest_execute_fails_before_launch_when_policy_disallows_mode():
    launcher = _FakePentestLauncher()
    activities = TemporalAgentRuntimeActivities(workload_launcher=launcher)

    result = await activities._security_pentest_execute_trusted_internal(
        _pentest_activity_payload(operation_mode="full_authorized")
    )

    assert result["status"] == "validation_failed"
    assert "operation_mode_disabled" in str(result["diagnostics"])
    assert launcher.requests == []

async def test_security_pentest_execute_sources_publication_payload_from_nested_request():
    activities = TemporalAgentRuntimeActivities(
        workload_launcher=_FakePentestLauncher(),
        workload_registry=_RecordingPentestRegistry(),
    )
    # Publication inputs (here ``summary_text``) must be sourced from the nested
    # ``request`` envelope, not from the top-level payload. ``summary_text``
    # flows into the publication diagnostics/preview, which survive the
    # runner-file overwrite of ``normalized_findings``.
    nested_request = _pentest_activity_payload(
        summary_text="nested-source-marker",
        provider_snapshot_available=False,
    )["request"]
    payload = {
        "request": nested_request,
        "summary_text": "top-source-marker",
        "provider_snapshot_available": True,
    }

    result = await activities._security_pentest_execute_trusted_internal(payload)

    publication = result["artifact_publication"]
    artifact_names = {item["name"] for item in publication["artifacts"]}
    assert "nested-source-marker" in result["diagnostics"]["summary"]
    assert "top-source-marker" not in str(result)
    assert "output.provider_snapshot" in artifact_names

async def test_security_pentest_execute_uses_runner_normalized_findings_after_launch(
    tmp_path: Path,
):
    launcher = _FakePentestLauncher(
        findings_payload={
            "tool_name": "security.pentest.run",
            "tool_version": "1.0.0",
            "target": "https://lab.example.test",
            "scope_artifact_ref": "art:sha256:approved-scope",
            "operation_mode": "validate_hypothesis",
            "runner_profile_id": PENTEST_CLAUDE_OAUTH_RUNNER_PROFILE_ID,
            "execution_profile_ref": PENTEST_CLAUDE_OAUTH_PROFILE_ID,
            "produced_at": "2026-01-01T00:00:00Z",
            "findings": [
                {
                    "finding_id": "runner-finding",
                    "title": "Runner finding",
                    "severity": "critical",
                    "confidence": "confirmed",
                    "summary": "Runner-produced evidence.",
                },
                "ignored-non-object",
            ],
            "summary": {
                "findings_count": 1,
                "confirmed_findings_count": 1,
                "high_or_critical_count": 1,
            },
            "evidence_refs": ["file:/tmp/evidence.json"],
        }
    )
    activities = TemporalAgentRuntimeActivities(
        workload_launcher=launcher,
        workload_registry=_RecordingPentestRegistry(),
    )

    result = await activities._security_pentest_execute_trusted_internal(
        _pentest_activity_payload(artifacts_dir=str(tmp_path / "artifacts"))
    )

    assert result["status"] == "completed"
    assert result["findings_count"] == 1
    assert result["confirmed_findings_count"] == 1
    assert result["report_bundle"]["counts"]["high_or_critical_count"] == 1
    assert result["severity_counts"] == {"critical": 1}
    assert [
        item["finding_id"] for item in result["normalized_findings"]["findings"]
    ] == ["runner-finding"]


async def test_successful_workload_missing_structured_findings_is_not_clean(
    tmp_path: Path,
):
    # The launcher writes no structured findings file. A succeeded workload with
    # no machine-readable findings must NOT be reported as a clean run.
    activities = TemporalAgentRuntimeActivities(
        workload_launcher=_FakePentestLauncher(),
        workload_registry=_RecordingPentestRegistry(),
    )

    result = await activities._security_pentest_execute_trusted_internal(
        _pentest_activity_payload(artifacts_dir=str(tmp_path / "artifacts"))
    )

    assert result["status"] == "failed"
    assert (
        result["normalized_findings"]["normalization_status"] == "normalizer_error"
    )
    assert result["normalization_status"] == "normalizer_error"
    assert not result["normalized_findings"].get("implies_no_vulnerabilities", False)
    assert result["failure_classification"]["failure_kind"] == "runtime_action"
    assert result["terminal_cleanup"]["terminal_reason"] == "failure"


async def test_provider_cooldown_does_not_return_clean_findings():
    activities = TemporalAgentRuntimeActivities(
        workload_launcher=_FakePentestLauncher(),
        workload_registry=_RecordingPentestRegistry(),
        pentest_provider_lease_manager=_FakePentestLeaseManager(),
    )

    result = await activities._security_pentest_execute_trusted_internal(
        _pentest_activity_payload(
            provider_failure={"category": "provider_429"},
        )
    )

    assert result["status"] == "provider_cooldown"
    assert (
        result["normalized_findings"]["normalization_status"] == "provider_failed"
    )
    assert not result["normalized_findings"].get("implies_no_vulnerabilities", False)


async def test_workload_failure_does_not_return_clean_findings():
    activities = TemporalAgentRuntimeActivities(
        workload_launcher=_FakePentestLauncher(status="failed"),
        workload_registry=_RecordingPentestRegistry(),
    )

    result = await activities._security_pentest_execute_trusted_internal(
        _pentest_activity_payload()
    )

    assert result["status"] in {"failed", "timed-out", "canceled"}
    assert result["normalized_findings"]["normalization_status"] == "runner_failed"
    assert not result["normalized_findings"].get("implies_no_vulnerabilities", False)

async def test_security_pentest_execute_fails_closed_before_unknown_runner_launch(
    monkeypatch: pytest.MonkeyPatch,
):
    launcher = _FakePentestLauncher()
    activities = TemporalAgentRuntimeActivities(workload_launcher=launcher)
    monkeypatch.setattr(
        settings.pentest, "allowed_runner_profiles", ("missing-profile",)
    )

    result = await activities._security_pentest_execute_trusted_internal(
        _pentest_activity_payload(
            runner_profile_id="missing-profile",
            approved_scope={
                **_approved_pentest_scope(),
                "allowed_runner_profiles": ["missing-profile"],
            },
        )
    )

    assert result["status"] == "validation_failed"
    assert "unknown Pentest runner profile" in str(result["diagnostics"])
    assert launcher.requests == []

async def test_security_pentest_execute_returns_structured_runtime_failure_with_cleanup():
    launcher = _FakePentestLauncher(status="failed")
    activities = TemporalAgentRuntimeActivities(
        workload_launcher=launcher,
        workload_registry=_RecordingPentestRegistry(),
    )

    result = await activities._security_pentest_execute_trusted_internal(
        _pentest_activity_payload()
    )

    assert result["status"] == "failed"
    assert result["failure_classification"]["failure_kind"] == "runtime_action"
    assert result["failure_classification"]["interaction_state"] == "post_interaction"
    assert result["terminal_cleanup"]["terminal_reason"] == "failure"
    assert result["terminal_cleanup"]["container_removed"] is True
    assert result["execution_policy"]["automatic_retries_enabled"] is False

async def test_security_pentest_execute_requires_registry_to_launch():
    # A launcher is configured but no registry is wired. The real launcher needs
    # a ValidatedWorkloadRequest, so the activity must fail closed rather than
    # hand the raw request to the launcher.
    launcher = _FakePentestLauncher()
    activities = TemporalAgentRuntimeActivities(workload_launcher=launcher)

    result = await activities._security_pentest_execute_trusted_internal(
        _pentest_activity_payload()
    )

    assert result["status"] == "failed"
    assert result["failure_classification"]["failure_kind"] == "runtime_action"
    assert "workload registry is required" in str(result["diagnostics"])
    assert launcher.requests == []

class _NoOutputRefsPentestLauncher:
    """Launcher returning a succeeded result with ``output_refs`` unset (None)."""

    def __init__(self) -> None:
        self.requests: list[object] = []

    async def run(self, request: object) -> WorkloadResult:
        self.requests.append(request)
        return WorkloadResult.model_validate(
            {
                "requestId": "workload-run-123",
                "profileId": PENTEST_CLAUDE_OAUTH_RUNNER_PROFILE_ID,
                "status": "succeeded",
                "exitCode": 0,
            }
        )

async def test_security_pentest_execute_handles_missing_output_refs():
    # When the workload result omits output_refs, the activity must fall back to
    # published artifact refs instead of raising AttributeError on None.get().
    launcher = _NoOutputRefsPentestLauncher()
    activities = TemporalAgentRuntimeActivities(
        workload_launcher=launcher,
        workload_registry=_RecordingPentestRegistry(),
    )

    result = await activities._security_pentest_execute_trusted_internal(
        _pentest_activity_payload()
    )

    assert result["status"] == "completed"
    assert len(launcher.requests) == 1
    # Report refs fall back to the published pentest artifact refs.
    assert result["primary_report_ref"]
    assert result["summary_ref"]

async def test_plan_generate_accepts_auto_placeholder_without_registry_entries(
    tmp_path: Path,
):
    from moonmind.workflows.temporal.worker_runtime import _build_runtime_planner

    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            planner = TemporalPlanActivities(
                artifact_service=service,
                planner=_build_runtime_planner(),
            )

            result = await planner.plan_generate(
                principal="user-1",
                parameters={
                    "repository": "MoonLadderStudios/MoonMind",
                    "targetRuntime": "claude",
                    "model": "MiniMax-M2.7",
                    "instructions": "Move the pagination control next to next/prev buttons.",
                    "task": {
                        "tool": {"type": "skill", "name": "auto"},
                        "skill": {"name": "auto"},
                        "runtime": {"mode": "claude", "model": "MiniMax-M2.7"},
                        "instructions": "Move the pagination control next to next/prev buttons.",
                    },
                },
            )

            _artifact, payload = await service.read(
                artifact_id=result.plan_ref.artifact_id,
                principal="user-1",
            )
            plan_payload = json.loads(payload.decode("utf-8"))
            registry_ref = plan_payload["metadata"]["registry_snapshot"]["artifact_ref"]

            _registry_artifact, registry_payload_raw = await service.read(
                artifact_id=registry_ref,
                principal="user-1",
            )
            registry_payload = json.loads(registry_payload_raw.decode("utf-8"))

            assert plan_payload["nodes"][0]["tool"]["type"] == "agent_runtime"
            assert registry_payload == {"skills": []}

async def test_plan_generate_fallback_registry_includes_input_artifact_tool_steps(
    tmp_path: Path,
):
    from moonmind.workflows.temporal.worker_runtime import _build_runtime_planner

    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            inputs_artifact, _upload = await service.create(
                principal="user-1",
                content_type="application/json",
            )
            await service.write_complete(
                artifact_id=inputs_artifact.artifact_id,
                principal="user-1",
                payload=(
                    json.dumps(
                        {
                            "workflow": {
                                "instructions": "Fetch the Jira issue.",
                                "runtime": {"mode": "codex_cli"},
                                "steps": [
                                    {
                                        "id": "fetch-issue",
                                        "type": "tool",
                                        "instructions": "Fetch MM-579.",
                                        "tool": {
                                            "id": "jira.get_issue",
                                            "inputs": {"issueKey": "MM-579"},
                                        },
                                    }
                                ],
                            }
                        }
                    )
                    + "\n"
                ).encode("utf-8"),
                content_type="application/json",
            )

            planner = TemporalPlanActivities(
                artifact_service=service,
                planner=_build_runtime_planner(),
            )
            result = await planner.plan_generate(
                principal="user-1",
                inputs_ref=inputs_artifact.artifact_id,
                parameters={
                    "repository": "MoonLadderStudios/MoonMind",
                    "targetRuntime": "codex_cli",
                    "workflow": {
                        "tool": {"type": "skill", "name": "auto"}
                    },
                },
            )

            _artifact, plan_payload_raw = await service.read(
                artifact_id=result.plan_ref.artifact_id,
                principal="user-1",
            )
            plan_payload = json.loads(plan_payload_raw.decode("utf-8"))
            registry_ref = plan_payload["metadata"]["registry_snapshot"]["artifact_ref"]
            _registry_artifact, registry_payload_raw = await service.read(
                artifact_id=registry_ref,
                principal="user-1",
            )
            registry_payload = json.loads(registry_payload_raw.decode("utf-8"))

            assert plan_payload["nodes"][0]["tool"] == {
                "type": "skill",
                "name": "jira.get_issue",
            }
            assert [item["name"] for item in registry_payload["skills"]] == [
                "jira.get_issue"
            ]

async def test_plan_generate_direct_skill_step_uses_only_authored_tool_inputs(
    tmp_path: Path,
):
    from moonmind.workflows.temporal.worker_runtime import _build_runtime_planner

    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            planner = TemporalPlanActivities(
                artifact_service=service,
                planner=_build_runtime_planner(),
            )

            result = await planner.plan_generate(
                principal="user-1",
                parameters={
                    "repository": "g3-qrtr/crash_server_main",
                    "targetRuntime": "claude_code",
                    "model": "claude-opus-4-8",
                    "profileId": "claude_anthropic",
                    "effort": "xhigh",
                    "publishMode": "none",
                    "maxAttempts": 3,
                    "stepCount": 1,
                    "workflow": {
                        "tool": {"type": "skill", "name": "auto"},
                        "runtime": {
                            "mode": "claude_code",
                            "model": "claude-opus-4-8",
                            "profileId": "claude_anthropic",
                            "effort": "xhigh",
                        },
                        "steps": [
                            {
                                "id": "step-1",
                                "title": "Run approved PentestGPT assessment",
                                "type": "tool",
                                "instructions": "Run the curated PentestGPT tool.",
                                "tool": {
                                    "id": "security.pentest.run",
                                    "inputs": {
                                        "target": "https://lab.example.test",
                                        "scope_artifact_ref": "art_scope_valid",
                                        "operation_mode": "recon_only",
                                        "runner_profile_id": PENTEST_CLAUDE_OAUTH_RUNNER_PROFILE_ID,
                                        "time_budget_minutes": 60,
                                        "evidence_level": "standard",
                                    },
                                },
                            }
                        ],
                    },
                },
            )

            _artifact, payload = await service.read(
                artifact_id=result.plan_ref.artifact_id,
                principal="user-1",
            )
            plan_payload = json.loads(payload.decode("utf-8"))
            node = plan_payload["nodes"][0]

            assert node["tool"] == {
                "type": "skill",
                "name": "security.pentest.run",
            }
            assert node["inputs"] == {
                "target": "https://lab.example.test",
                "scope_artifact_ref": "art_scope_valid",
                "operation_mode": "recon_only",
                "runner_profile_id": PENTEST_CLAUDE_OAUTH_RUNNER_PROFILE_ID,
                "time_budget_minutes": 60,
                "evidence_level": "standard",
            }

async def test_default_registry_payload_uses_extended_timeouts_for_pr_resolver():
    payload = _default_registry_skill_payload(name="pr-resolver")
    policies = payload.get("policies", {})
    timeouts = policies.get("timeouts", {})
    assert timeouts.get("start_to_close_seconds") == 7200
    assert timeouts.get("schedule_to_close_seconds") == 7500

async def test_skill_execute_loads_registry_snapshot_from_temporal_artifact(
    tmp_path: Path,
):
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
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

            dispatcher = SkillActivityDispatcher()
            dispatcher.register_skill(
                skill_name="repo.run_tests",
                handler=lambda inputs, _context: SkillResult(
                    status="COMPLETED",
                    outputs={"ok": inputs["repo_ref"].endswith("#main")},
                    progress={"percent": 100},
                ),
            )

            activities = TemporalSkillActivities(dispatcher=dispatcher)
            result = await activities.mm_skill_execute(
                invocation_payload={
                    "id": "n1",
                    "skill": {"name": "repo.run_tests"},
                    "inputs": {"repo_ref": "git:org/repo#main"},
                },
                registry_snapshot_ref=registry_artifact.artifact_id,
                artifact_service=service,
                principal="user-1",
            )

            assert result.status == "COMPLETED"
            assert result.outputs["ok"] is True

async def test_skill_execute_uses_bound_artifact_service_when_not_passed(
    tmp_path: Path,
):
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
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

            dispatcher = SkillActivityDispatcher()
            dispatcher.register_skill(
                skill_name="repo.run_tests",
                handler=lambda inputs, _context: SkillResult(
                    status="COMPLETED",
                    outputs={"ok": inputs["repo_ref"].endswith("#main")},
                ),
            )

            activities = TemporalSkillActivities(
                dispatcher=dispatcher,
                artifact_service=service,
            )
            result = await activities.mm_skill_execute(
                invocation_payload={
                    "id": "n1",
                    "skill": {"name": "repo.run_tests"},
                    "inputs": {"repo_ref": "git:org/repo#main"},
                },
                registry_snapshot_ref=registry_artifact.artifact_id,
                principal="user-1",
            )

            assert result.status == "COMPLETED"
            assert result.outputs["ok"] is True

async def test_artifact_read_invalid_ref_failures_surface_cleanly(tmp_path: Path):
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            activities = TemporalArtifactActivities(service)

            with pytest.raises(
                TemporalArtifactValidationError,
                match="artifact_id is required",
            ):
                await activities.artifact_read(
                    {"artifact_ref": {"artifactId": "  "}, "principal": "user-1"}
                )

            with pytest.raises(TemporalArtifactNotFoundError):
                await activities.artifact_read(
                    {"artifact_ref": "art:sha256:dummy", "principal": "user-1"}
                )

async def test_sandbox_run_command_writes_diagnostics_artifact(tmp_path: Path):
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            workspace_root = tmp_path / "workspaces"
            workspace = workspace_root / "temporal_sandbox" / "unit-run-command"
            workspace.mkdir(parents=True)
            activities = TemporalSandboxActivities(
                artifact_service=service,
                workspace_root=workspace_root,
            )

            result = await activities.sandbox_run_command(
                workspace_ref=workspace,
                cmd="printf 'hello sandbox'",
                principal="user-1",
                execution_ref=ExecutionRef(
                    namespace="moonmind",
                    workflow_id="wf-1",
                    run_id="run-1",
                    link_type="output.logs",
                ),
            )
            assert result.exit_code == 0
            assert result.diagnostics_ref is not None

            _artifact, payload = await service.read(
                artifact_id=result.diagnostics_ref.artifact_id,
                principal="user-1",
            )
            assert b"hello sandbox" in payload

async def test_sandbox_rejects_workspace_outside_sandbox_root(tmp_path: Path):
    activities = TemporalSandboxActivities(workspace_root=tmp_path / "workspaces")
    outside_workspace = tmp_path / "outside"
    outside_workspace.mkdir()

    with pytest.raises(TemporalActivityRuntimeError, match="escapes sandbox root"):
        await activities.sandbox_run_command(
            workspace_ref=outside_workspace,
            cmd=("pwd",),
        )

async def test_sandbox_checkout_rejects_local_path_outside_workspace_root(
    tmp_path: Path,
):
    activities = TemporalSandboxActivities(workspace_root=tmp_path / "workspaces")
    source = tmp_path / "repo"
    source.mkdir()

    with pytest.raises(
        TemporalActivityRuntimeError,
        match="must be under workspace_root",
    ):
        await activities.sandbox_checkout_repo(
            repo_ref=source,
            idempotency_key="checkout-outside",
        )

async def test_sandbox_run_command_allows_allowlisted_file_change(tmp_path: Path):
    activities = TemporalSandboxActivities(workspace_root=tmp_path / "workspaces")
    workspace = tmp_path / "workspaces" / "temporal_sandbox" / "allowlisted"
    workspace.mkdir(parents=True)
    target = workspace / "allowed.txt"
    target.write_text("before\n", encoding="utf-8")

    result = await activities.sandbox_run_command(
        workspace_ref=workspace,
        cmd=(
            sys.executable,
            "-c",
            "from pathlib import Path; Path('allowed.txt').write_text('after\\n')",
        ),
        allowed_file_paths=("allowed.txt",),
    )

    assert result.exit_code == 0
    assert target.read_text(encoding="utf-8") == "after\n"

async def test_sandbox_run_command_allows_directory_allowlisted_file_change(
    tmp_path: Path,
):
    activities = TemporalSandboxActivities(workspace_root=tmp_path / "workspaces")
    workspace = tmp_path / "workspaces" / "temporal_sandbox" / "allowlisted-dir"
    workspace.mkdir(parents=True)
    target = workspace / "allowed" / "nested.txt"
    target.parent.mkdir()
    target.write_text("before\n", encoding="utf-8")

    result = await activities.sandbox_run_command(
        workspace_ref=workspace,
        cmd=(
            sys.executable,
            "-c",
            "from pathlib import Path; Path('allowed/nested.txt').write_text('after\\n')",
        ),
        allowed_file_paths=("allowed",),
    )

    assert result.exit_code == 0
    assert target.read_text(encoding="utf-8") == "after\n"

async def test_sandbox_run_command_rejects_file_change_outside_allowlist(
    tmp_path: Path,
):
    activities = TemporalSandboxActivities(workspace_root=tmp_path / "workspaces")
    workspace = tmp_path / "workspaces" / "temporal_sandbox" / "blocked"
    workspace.mkdir(parents=True)
    allowed = workspace / "allowed.txt"
    blocked = workspace / "blocked.txt"
    allowed.write_text("allowed\n", encoding="utf-8")
    blocked.write_text("before\n", encoding="utf-8")

    with pytest.raises(
        TemporalActivityRuntimeError,
        match="modified files outside the allowlist: blocked.txt",
    ):
        await activities.sandbox_run_command(
            workspace_ref=workspace,
            cmd=(
                sys.executable,
                "-c",
                "from pathlib import Path; Path('blocked.txt').write_text('after\\n')",
            ),
            allowed_file_paths=("allowed.txt",),
        )

    assert blocked.read_text(encoding="utf-8") == "before\n"

async def test_sandbox_run_command_rejects_permission_change_outside_allowlist(
    tmp_path: Path,
):
    activities = TemporalSandboxActivities(workspace_root=tmp_path / "workspaces")
    workspace = tmp_path / "workspaces" / "temporal_sandbox" / "blocked-mode"
    workspace.mkdir(parents=True)
    allowed = workspace / "allowed.txt"
    blocked = workspace / "blocked.sh"
    allowed.write_text("allowed\n", encoding="utf-8")
    blocked.write_text("#!/bin/sh\n", encoding="utf-8")
    blocked.chmod(0o644)

    with pytest.raises(
        TemporalActivityRuntimeError,
        match="modified files outside the allowlist: blocked.sh",
    ):
        await activities.sandbox_run_command(
            workspace_ref=workspace,
            cmd=(
                sys.executable,
                "-c",
                "from pathlib import Path; Path('blocked.sh').chmod(0o755)",
            ),
            allowed_file_paths=("allowed.txt",),
        )

    assert stat.S_IMODE(blocked.stat().st_mode) == 0o644

async def test_sandbox_apply_patch_enforces_file_allowlist(tmp_path: Path):
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            workspace = tmp_path / "workspaces" / "temporal_sandbox" / "patch-blocked"
            workspace.mkdir(parents=True)
            (workspace / "blocked.txt").write_text("before\n", encoding="utf-8")

            patch_artifact, _upload = await service.create(
                principal="user-1",
                content_type="text/plain",
            )
            await service.write_complete(
                artifact_id=patch_artifact.artifact_id,
                principal="user-1",
                payload=(
                    "--- blocked.txt\n+++ blocked.txt\n@@ -1 +1 @@\n-before\n+after\n"
                ).encode("utf-8"),
                content_type="text/plain",
            )

            activities = TemporalSandboxActivities(
                artifact_service=service,
                workspace_root=tmp_path / "workspaces",
            )

            with pytest.raises(
                TemporalActivityRuntimeError,
                match="modified files outside the allowlist: blocked.txt",
            ):
                await activities.sandbox_apply_patch(
                    workspace_ref=workspace,
                    patch_ref=patch_artifact.artifact_id,
                    principal="user-1",
                    allowed_file_paths=("allowed.txt",),
                )

            assert (workspace / "blocked.txt").read_text(encoding="utf-8") == "before\n"

async def test_sandbox_checkout_repo_clones_github_slug_and_revision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    activities = TemporalSandboxActivities(workspace_root=tmp_path / "workspaces")
    recorded_commands: list[list[str]] = []

    async def _fake_run_command(request, /, **_kwargs):
        cmd = [str(token) for token in request.get("cmd", [])]
        recorded_commands.append(cmd)
        if cmd[:2] == ["git", "clone"] and len(cmd) >= 4:
            Path(cmd[3]).mkdir(parents=True, exist_ok=True)
        return SandboxCommandResult(
            exit_code=0,
            command=tuple(cmd),
            duration_ms=1,
            stdout_tail="ok",
            stderr_tail="",
            diagnostics_ref=None,
        )

    monkeypatch.setattr(activities, "sandbox_run_command", _fake_run_command)

    workspace = await activities.sandbox_checkout_repo(
        repo_ref="MoonLadderStudios/MoonMind",
        idempotency_key="checkout-remote",
        checkout_revision="main",
    )

    assert Path(workspace).exists()
    assert recorded_commands[0][:3] == [
        "git",
        "clone",
        "https://github.com/MoonLadderStudios/MoonMind.git",
    ]
    assert recorded_commands[1] == ["git", "checkout", "main"]

async def test_shared_envelope_helpers_build_compact_runtime_contracts():
    invocation = build_activity_invocation_envelope(
        correlation_id="corr-1",
        idempotency_key="idem-1",
        input_refs=["art_01HJ4M3Y7RM4C5S2P3Q8G6T7V8"],
        parameters={"phase": "run"},
    )
    result = build_compact_activity_result(
        output_refs=["art_01HJ4M3Y7RM4C5S2P3Q8G6T7V9"],
        summary={"status": "ok"},
        metrics={"tokens": 12},
        diagnostics_ref="art_01HJ4M3Y7RM4C5S2P3Q8G6T7VA",
    )
    context = build_activity_execution_context(
        workflow_id="wf-1",
        run_id="run-1",
        activity_id="act-1",
        attempt=2,
        task_queue="mm.activity.sandbox",
    )
    summary = build_observability_summary(
        context=context,
        activity_type="sandbox.run_command",
        correlation_id=invocation.correlation_id,
        idempotency_key="idem-1",
        outcome="completed",
        diagnostics_ref=result.diagnostics_ref,
        metrics_dimensions={"fleet": "sandbox"},
    )

    assert invocation.to_payload()["idempotency_key"] == "idem-1"
    assert result.to_payload()["output_refs"] == ["art_01HJ4M3Y7RM4C5S2P3Q8G6T7V9"]
    assert summary.activity_type == "sandbox.run_command"
    assert summary.idempotency_key_hash != "idem-1"

async def test_sandbox_checkout_apply_patch_and_run_tests(tmp_path: Path):
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            repo = tmp_path / "workspaces" / "repo"
            repo.mkdir(parents=True)
            (repo / "sample.txt").write_text("hello\n", encoding="utf-8")
            (repo / "tools").mkdir()
            (repo / "tools" / "test_unit.sh").write_text(
                "#!/usr/bin/env sh\nprintf 'tests ok'\n",
                encoding="utf-8",
            )
            (repo / "tools" / "test_unit.sh").chmod(0o755)

            patch_artifact, _upload = await service.create(
                principal="user-1",
                content_type="text/plain",
            )
            await service.write_complete(
                artifact_id=patch_artifact.artifact_id,
                principal="user-1",
                payload=(
                    "--- sample.txt\n+++ sample.txt\n@@ -1 +1 @@\n-hello\n+patched\n"
                ).encode("utf-8"),
                content_type="text/plain",
            )

            activities = TemporalSandboxActivities(
                artifact_service=service,
                workspace_root=tmp_path / "workspaces",
            )
            workspace = await activities.sandbox_checkout_repo(
                repo_ref=repo,
                idempotency_key="checkout-1",
            )
            assert Path(workspace).exists()

            patched_workspace = await activities.sandbox_apply_patch(
                workspace_ref=workspace,
                patch_ref=patch_artifact.artifact_id,
                principal="user-1",
            )
            assert (
                Path(patched_workspace, "sample.txt").read_text(encoding="utf-8")
                == "patched\n"
            )

            report_ref = await activities.sandbox_run_tests(
                workspace_ref=patched_workspace,
                principal="user-1",
            )
            _artifact, payload = await service.read(
                artifact_id=report_ref.artifact_id,
                principal="user-1",
            )
            assert b'"exit_code": 0' in payload

async def test_build_activity_bindings_filters_to_requested_fleet(tmp_path: Path):
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            catalog = build_default_activity_catalog()

            bindings = build_activity_bindings(
                catalog,
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
                    client_factory=_FakeJulesClient,
                ),
                proposal_activities=TemporalProposalActivities(artifact_service=service),
                agent_skills_activities=AgentSkillsActivities(),
                fleets=(ARTIFACTS_FLEET,),
            )

            assert bindings
            assert {binding.fleet for binding in bindings} == {ARTIFACTS_FLEET}
            assert "mm.skill.execute" in {binding.activity_type for binding in bindings}
            assert "artifact.lifecycle_sweep" in {
                binding.activity_type for binding in bindings
            }
            assert "execution.record_terminal_state" in {
                binding.activity_type for binding in bindings
            }
            assert any(
                binding.handler.__name__ == "artifact_lifecycle_sweep"
                for binding in bindings
            )
            assert any(
                binding.handler.__name__ == "execution_record_terminal_state"
                for binding in bindings
            )

async def test_build_activity_bindings_resolves_memory_integration_handlers(
    tmp_path: Path,
):
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            catalog = build_default_activity_catalog()
            bindings = {
                binding.activity_type: binding
                for binding in build_activity_bindings(
                    catalog,
                    integration_activities=TemporalIntegrationActivities(
                        artifact_service=service,
                        client_factory=_FakeJulesClient,
                    ),
                    fleets=(INTEGRATIONS_FLEET,),
                )
            }

            source = {
                "workflowId": "wf-1",
                "runId": "run-1",
                "logicalStepId": "implement",
                "executionOrdinal": 1,
            }
            decision_result = await bindings["memory.evaluate_proposals"].handler(
                {
                    "proposal_refs": ["artifact://memory/proposal-1"],
                    "source": source,
                    "terminal_disposition": "accepted",
                    "publication_gate": {"passed": True},
                    "requested_target": "memory://run",
                    "policy_decision": "accept_for_run_context",
                }
            )
            application_result = await bindings["memory.apply_policy"].handler(
                {
                    "proposal_ref": "artifact://memory/proposal-1",
                    "decision_ref": "artifact://memory/decision-1",
                    "source": source,
                    "target": "repo://AGENTS.md",
                    "decision": "approve_repo_application",
                    "gate_status": {"terminalDisposition": "accepted"},
                }
            )

            assert decision_result["decisionRefs"] == ["artifact://memory/decision-1"]
            assert application_result["outcome"] == "blocked"
            assert (
                application_result["failureReason"]
                == "applied_repo_memory_result_requires_accepted_gates"
            )


async def test_build_activity_bindings_resolves_omnigent_execute_handler(
    tmp_path: Path,
):
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            catalog = build_default_activity_catalog()
            bindings = {
                binding.activity_type: binding
                for binding in build_activity_bindings(
                    catalog,
                    integration_activities=TemporalIntegrationActivities(
                        artifact_service=service,
                        client_factory=_FakeJulesClient,
                    ),
                    fleets=(INTEGRATIONS_FLEET,),
                )
            }

            assert "integration.omnigent.execute" in bindings
            assert (
                bindings["integration.omnigent.execute"].handler.__name__
                == "integration_omnigent_execute"
            )

async def test_build_activity_bindings_artifact_read_accepts_request_mapping(
    tmp_path: Path,
):
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            artifact, _upload = await service.create(
                principal="user-1",
                content_type="application/json",
            )
            stored = await service.write_complete(
                artifact_id=artifact.artifact_id,
                principal="user-1",
                payload=b'{"ok": true}',
                content_type="application/json",
            )
            catalog = build_default_activity_catalog()

            bindings = build_activity_bindings(
                catalog,
                artifact_activities=TemporalArtifactActivities(service),
                manifest_activities=TemporalManifestActivities(
                    artifact_service=service,
                ),
                proposal_activities=TemporalProposalActivities(artifact_service=service),
                agent_skills_activities=AgentSkillsActivities(),
                fleets=(ARTIFACTS_FLEET,),
            )
            artifact_read_handler = next(
                binding.handler
                for binding in bindings
                if binding.activity_type == "artifact.read"
            )

            payload = await artifact_read_handler(
                {
                    "artifact_ref": build_artifact_ref(stored),
                    "principal": "user-1",
                }
            )

            assert payload == b'{"ok": true}'

async def test_build_activity_bindings_artifact_handlers_preserve_typed_request_signature(
    tmp_path: Path,
):
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            catalog = build_default_activity_catalog()
            bindings = {
                binding.activity_type: binding
                for binding in build_activity_bindings(
                    catalog,
                    artifact_activities=TemporalArtifactActivities(service),
                    manifest_activities=TemporalManifestActivities(
                        artifact_service=service,
                    ),
                    proposal_activities=TemporalProposalActivities(artifact_service=service),
                    agent_skills_activities=AgentSkillsActivities(),
                    fleets=(ARTIFACTS_FLEET,),
                )
            }

            from typing import get_type_hints
            from moonmind.schemas.temporal_activity_models import (
                ArtifactReadInput,
                ArtifactWriteCompleteInput,
            )

            read_handler_hints = get_type_hints(bindings["artifact.read"].handler)
            write_handler_hints = get_type_hints(
                bindings["artifact.write_complete"].handler
            )

            annotation_globals = dict(TemporalArtifactActivities.artifact_read.__globals__)
            annotation_globals.update({
                "ArtifactReadInput": ArtifactReadInput,
                "ArtifactWriteCompleteInput": ArtifactWriteCompleteInput,
            })

            read_method_hints = get_type_hints(
                TemporalArtifactActivities.artifact_read, globalns=annotation_globals
            )
            write_method_hints = get_type_hints(
                TemporalArtifactActivities.artifact_write_complete, globalns=annotation_globals
            )

            assert read_handler_hints["request"] == read_method_hints["request"]
            assert read_handler_hints.get("return") == read_method_hints.get("return")

            assert write_handler_hints["request"] == write_method_hints["request"]
            assert write_handler_hints.get("return") == write_method_hints.get("return")

async def test_build_activity_bindings_artifact_read_accepts_serialized_ref_mapping(
    tmp_path: Path,
):
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            artifact, _upload = await service.create(
                principal="user-1",
                content_type="application/json",
            )
            stored = await service.write_complete(
                artifact_id=artifact.artifact_id,
                principal="user-1",
                payload=b'{"ok": true}',
                content_type="application/json",
            )
            catalog = build_default_activity_catalog()
            bindings = build_activity_bindings(
                catalog,
                artifact_activities=TemporalArtifactActivities(service),
                manifest_activities=TemporalManifestActivities(
                    artifact_service=service,
                ),
                proposal_activities=TemporalProposalActivities(artifact_service=service),
                agent_skills_activities=AgentSkillsActivities(),
                fleets=(ARTIFACTS_FLEET,),
            )
            artifact_read_handler = next(
                binding.handler
                for binding in bindings
                if binding.activity_type == "artifact.read"
            )
            serialized_ref = {
                "artifact_id": stored.artifact_id,
                "artifact_ref_v": 1,
                "sha256": stored.sha256,
                "size_bytes": stored.size_bytes,
                "content_type": stored.content_type,
                "encryption": stored.encryption,
            }

            payload = await artifact_read_handler(
                {
                    "artifact_ref": serialized_ref,
                    "principal": "user-1",
                }
            )

            assert payload == b'{"ok": true}'

async def test_build_activity_bindings_artifact_write_complete_accepts_legacy_payload_mapping(
    tmp_path: Path,
):
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            artifact, _upload = await service.create(
                principal="user-1",
                content_type="application/octet-stream",
            )
            catalog = build_default_activity_catalog()
            bindings = build_activity_bindings(
                catalog,
                artifact_activities=TemporalArtifactActivities(service),
                manifest_activities=TemporalManifestActivities(
                    artifact_service=service,
                ),
                proposal_activities=TemporalProposalActivities(artifact_service=service),
                agent_skills_activities=AgentSkillsActivities(),
                fleets=(ARTIFACTS_FLEET,),
            )
            artifact_write_handler = next(
                binding.handler
                for binding in bindings
                if binding.activity_type == "artifact.write_complete"
            )

            stored_ref = await artifact_write_handler(
                {
                    "artifact_id": artifact.artifact_id,
                    "principal": "user-1",
                    "payload": list(b'{"ok": true}'),
                    "content_type": "application/json",
                }
            )
            _stored_artifact, payload = await service.read(
                artifact_id=artifact.artifact_id,
                principal="user-1",
            )

            assert stored_ref.artifact_id == artifact.artifact_id
            assert payload == b'{"ok": true}'

async def test_build_activity_bindings_injected_skill_handler_uses_request_mapping(
    tmp_path: Path,
):
    class _KeywordOnlySkillActivities:
        async def mm_skill_execute(
            self,
            *,
            invocation_payload: Mapping[str, object],
            principal: str,
        ) -> dict[str, object]:
            return {
                "invocationId": invocation_payload.get("id"),
                "principal": principal,
            }

    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            catalog = build_default_activity_catalog()
            bindings = build_activity_bindings(
                catalog,
                artifact_activities=TemporalArtifactActivities(service),
                manifest_activities=TemporalManifestActivities(
                    artifact_service=service,
                ),
                skill_activities=_KeywordOnlySkillActivities(),
                proposal_activities=TemporalProposalActivities(artifact_service=service),
                agent_skills_activities=AgentSkillsActivities(),
                fleets=(ARTIFACTS_FLEET,),
            )
            skill_handler = next(
                binding.handler
                for binding in bindings
                if binding.activity_type == "mm.skill.execute"
            )

            result = await skill_handler(
                {
                    "invocation_payload": {"id": "node-1"},
                    "principal": "user-1",
                }
            )

            assert result["invocationId"] == "node-1"
            assert result["principal"] == "user-1"

async def test_build_activity_bindings_mm_tool_execute_handler_supports_keyword_payload(
    tmp_path: Path,
):
    dispatcher = SkillActivityDispatcher()
    captured_context: dict[str, object] = {}

    def _run_tests_handler(
        inputs: Mapping[str, object],
        context: Mapping[str, object] | None,
    ) -> SkillResult:
        captured_context.update(dict(context or {}))
        return SkillResult(
            status="COMPLETED",
            outputs={"ok": str(inputs["repo_ref"]).endswith("#main")},
        )

    dispatcher.register_skill(
        skill_name="repo.run_tests",
        handler=_run_tests_handler,
    )
    snapshot = create_registry_snapshot(
        skills=parse_skill_registry(_registry_payload()),
        artifact_store=InMemoryArtifactStore(),
    )

    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            catalog = build_default_activity_catalog()
            bindings = build_activity_bindings(
                catalog,
                artifact_activities=TemporalArtifactActivities(service),
                skill_activities=TemporalSkillActivities(
                    dispatcher=dispatcher,
                    artifact_service=service,
                ),
                sandbox_activities=TemporalSandboxActivities(
                    artifact_service=service,
                    workspace_root=tmp_path,
                ),
                fleets=(SANDBOX_FLEET,),
            )
            tool_handler = next(
                binding.handler
                for binding in bindings
                if binding.activity_type == "mm.tool.execute"
            )

            result = await tool_handler(
                {
                    "invocation_payload": {
                        "id": "n1",
                        "skill": {"name": "repo.run_tests"},
                        "inputs": {"repo_ref": "git:org/repo#main"},
                    },
                    "registry_snapshot": snapshot,
                    "context": {"workflow_id": "wf-1"},
                    "idempotency_key": "wf-1_n1_execute",
                }
            )

            assert result.status == "COMPLETED"
            assert result.outputs["ok"] is True
            assert captured_context["workflow_id"] == "wf-1"
            assert captured_context["idempotency_key"] == "wf-1_n1_execute"


async def test_mm_tool_execute_preserves_tool_failure_envelope() -> None:
    dispatcher = SkillActivityDispatcher()

    def _failing_handler(
        _inputs: dict[str, object],
        _context: dict[str, object] | None,
    ) -> SkillResult:
        leaked_token = "ghp_toolfailuretoken1234567890abcd"
        raise ToolFailure(
            error_code="DEPLOYMENT_RUNNER_UNSAFE",
            message=(
                "Deployment update would recreate the worker container "
                f"with token {leaked_token}."
            ),
            retryable=False,
            details={
                "failureClass": "runner_self_recreation_unsafe",
                "service": "temporal-worker-agent-runtime",
                "token": leaked_token,
            },
        )

    dispatcher.register_skill(
        skill_name="repo.run_tests",
        handler=_failing_handler,
    )
    snapshot = create_registry_snapshot(
        skills=parse_skill_registry(_registry_payload()),
        artifact_store=InMemoryArtifactStore(),
    )
    activities = TemporalSkillActivities(dispatcher=dispatcher)

    with pytest.raises(temporal_exceptions.ApplicationError) as exc_info:
        await activities.mm_tool_execute(
            invocation_payload={
                "id": "deploy",
                "tool": {
                    "type": "skill",
                    "name": "repo.run_tests",
                },
                "inputs": {"repo_ref": "git:org/repo#main"},
            },
            registry_snapshot=snapshot,
        )

    assert exc_info.value.type == "DEPLOYMENT_RUNNER_UNSAFE"
    assert exc_info.value.non_retryable is True
    assert "Deployment update would recreate" in str(exc_info.value)
    assert "ghp_toolfailuretoken1234567890abcd" not in str(exc_info.value)
    assert "[REDACTED]" in str(exc_info.value)
    assert exc_info.value.details[0] == {
        "error_code": "DEPLOYMENT_RUNNER_UNSAFE",
        "message": (
            "Deployment update would recreate the worker container "
            "with token [REDACTED]."
        ),
        "retryable": False,
        "details": {
            "failureClass": "runner_self_recreation_unsafe",
            "service": "temporal-worker-agent-runtime",
            "token": "[REDACTED]",
        },
    }


async def test_build_activity_bindings_does_not_mutate_sandbox_method_signatures(
    tmp_path: Path,
):
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            dispatcher = SkillActivityDispatcher()
            sandbox_activities = TemporalSandboxActivities(
                artifact_service=service,
                workspace_root=tmp_path,
            )
            build_activity_bindings(
                build_default_activity_catalog(),
                artifact_activities=TemporalArtifactActivities(service),
                skill_activities=TemporalSkillActivities(
                    dispatcher=dispatcher,
                    artifact_service=service,
                ),
                sandbox_activities=sandbox_activities,
                fleets=(SANDBOX_FLEET,),
            )

            workspace = tmp_path / "temporal_sandbox" / "workspace"
            workspace.mkdir(parents=True, exist_ok=True)
            result = await sandbox_activities.sandbox_run_command(
                workspace_ref=workspace,
                cmd=("bash", "-lc", "true"),
                principal="user-1",
            )

            assert result.exit_code == 0

async def test_sandbox_run_command_env_allows_unsetting_parent_values(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("MM_TEMP_ENV_UNSET_TEST", "present")
    sandbox_activities = TemporalSandboxActivities(workspace_root=tmp_path)
    workspace = tmp_path / "temporal_sandbox" / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    result = await sandbox_activities.sandbox_run_command(
        workspace_ref=workspace,
        cmd=("bash", "-lc", '[ -z "${MM_TEMP_ENV_UNSET_TEST+x}" ]'),
        principal="user-1",
        env={"MM_TEMP_ENV_UNSET_TEST": None},
    )

    assert result.exit_code == 0

async def test_build_activity_bindings_requires_selected_family_implementation(
    tmp_path: Path,
):
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            catalog = build_default_activity_catalog()

            with pytest.raises(
                TemporalActivityRuntimeError,
                match="sandbox implementation",
            ):
                build_activity_bindings(
                    catalog,
                    artifact_activities=TemporalArtifactActivities(service),
                    fleets=(SANDBOX_FLEET,),
                )

async def test_build_activity_bindings_resolves_agent_runtime_fleet(
    tmp_path: Path,
):
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            catalog = build_default_activity_catalog()

            bindings = build_activity_bindings(
                catalog,
                agent_runtime_activities=TemporalAgentRuntimeActivities(
                    artifact_service=service,
                ),
                agent_skills_activities=AgentSkillsActivities(),
                fleets=(AGENT_RUNTIME_FLEET,),
            )

            assert bindings
            assert {binding.fleet for binding in bindings} == {AGENT_RUNTIME_FLEET}
            bound_types = {binding.activity_type for binding in bindings}
            assert "agent_runtime.build_launch_context" in bound_types
            assert "agent_runtime.launch_session" in bound_types
            assert "agent_runtime.publish_artifacts" in bound_types
            assert "agent_runtime.session_status" in bound_types
            assert "agent_runtime.prepare_turn_instructions" in bound_types
            assert "agent_runtime.send_turn" in bound_types
            assert "agent_runtime.steer_turn" in bound_types
            assert "agent_runtime.interrupt_turn" in bound_types
            assert "agent_runtime.clear_session" in bound_types
            assert "agent_runtime.terminate_session" in bound_types
            assert "agent_runtime.fetch_session_summary" in bound_types
            assert "agent_runtime.publish_session_artifacts" in bound_types
            assert "agent_runtime.cleanup_managed_runtime_files" in bound_types
            assert "agent_runtime.status" in bound_types
            assert "agent_runtime.fetch_result" in bound_types
            assert "agent_runtime.cancel" in bound_types
            assert "agent_skill.resolve" in bound_types
            assert "agent_skill.materialize" in bound_types
            assert "agent_skill.build_prompt_index" in bound_types
            assert "agent_skill.query_on_demand" in bound_types
            assert "agent_skill.request_on_demand" in bound_types


async def test_prepare_managed_codex_turn_text_hides_on_demand_command_names_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.workflow, "skills_on_demand_enabled", False)

    result = TemporalAgentRuntimeActivities._prepare_managed_codex_turn_text(
        "Use the selected skill.",
        parameters={
            "metadata": {
                "moonmind": {
                    "selectedSkill": "moonspec-implement",
                }
            }
        },
        skill_materialization_metadata=None,
    )

    assert "Skills On Demand is disabled for this run." in result
    assert "moonmind.skills.query" not in result
    assert "moonmind.skills.request" not in result


async def test_agent_runtime_publish_artifacts_publishes_explicit_report_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            activities = TemporalAgentRuntimeActivities(artifact_service=service)

            monkeypatch.setattr(
                temporal_activity,
                "info",
                lambda: SimpleNamespace(
                    namespace="default",
                    workflow_id="parent-wf:agent:node-1",
                    workflow_run_id="child-run-1",
                ),
            )

            result = await activities.agent_runtime_publish_artifacts(
                AgentRunResult(
                    summary="Completed.",
                    metadata={
                        "operator_summary": "# Integration test report\n\nAll tests passed.",
                        "moonmind": {
                            "reportOutput": {
                                "enabled": True,
                                "required": True,
                                "reportType": "integration_test_report",
                                "primaryPath": "reports/final-report",
                                "executionRef": {
                                    "namespace": "default",
                                    "workflow_id": "parent-wf",
                                    "run_id": "parent-run-1",
                                },
                            }
                        },
                    },
                )
            )

            assert result is not None
            assert result.metadata["primaryReportRef"].startswith("art_")
            assert result.metadata["reportBundle"]["report_bundle_v"] == 1

            reports = await service.list_for_execution(
                namespace="default",
                workflow_id="parent-wf",
                run_id="parent-run-1",
                principal="system:agent_runtime",
                link_type="report.primary",
                latest_only=True,
            )
            assert len(reports) == 1
            assert reports[0].metadata_json["report_type"] == "integration_test_report"
            assert reports[0].metadata_json["report_scope"] == "final"
            assert reports[0].metadata_json["is_final_report"] is True
            assert reports[0].metadata_json["name"] == "final-report.md"
            _artifact, path = await service.read_path(
                artifact_id=reports[0].artifact_id,
                principal="system:agent_runtime",
            )
            body = path.read_bytes()
            assert body.decode("utf-8") == "# Integration test report\n\nAll tests passed.\n"


async def test_agent_runtime_publish_artifacts_publishes_moonspec_verify_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            workspace = tmp_path / "workspace"
            verify_path = workspace / "var/artifacts/moonspec-verify/final.json"
            verify_path.parent.mkdir(parents=True)
            large_evidence = "verified evidence " * 2000
            verify_path.write_text(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "verdict": "FULLY_IMPLEMENTED",
                        "recommendedNextAction": "advance",
                        "recoverableInCurrentRuntime": True,
                        "remainingWork": [],
                        "requirementCoverage": [
                            {
                                "requirement": "large verification evidence",
                                "evidence": large_evidence,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            run_store = ManagedRunStore(tmp_path / "runs")
            run_store.save(
                ManagedRunRecord(
                    runId="verify-run-1",
                    agentId="codex_cli",
                    runtimeId="codex_cli",
                    status="completed",
                    startedAt=datetime.now(timezone.utc),
                    workspacePath=workspace.as_posix(),
                )
            )
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            activities = TemporalAgentRuntimeActivities(
                artifact_service=service,
                run_store=run_store,
            )

            async def _skip_notify(*_args: Any, **_kwargs: Any) -> dict[str, str]:
                return {"status": "skipped"}

            monkeypatch.setattr(
                activities,
                "execution_notify_completion",
                _skip_notify,
            )
            monkeypatch.setattr(
                temporal_activity,
                "info",
                lambda: SimpleNamespace(
                    namespace="default",
                    workflow_id="parent-wf:agent:verify",
                    workflow_run_id="child-run-verify",
                ),
            )

            result = await activities.agent_runtime_publish_artifacts(
                AgentRunResult(
                    summary="Completed.",
                    metadata={
                        "agentRunId": "verify-run-1",
                        "verify_artifact_path": (
                            "var/artifacts/moonspec-verify/final.json"
                        ),
                    },
                )
            )

            assert isinstance(result, AgentRunResult)
            assert result.metadata["gateResultRef"].startswith("art_")
            assert result.metadata["moonSpecVerifyArtifactRef"] == (
                result.metadata["gateResultRef"]
            )
            assert result.metadata["moonSpecVerify"]["verdict"] == "FULLY_IMPLEMENTED"
            assert result.metadata["moonSpecVerify"]["gateResultRef"] == (
                result.metadata["gateResultRef"]
            )
            assert "contractViolations" not in result.metadata["moonSpecVerify"]
            assert "requirementCoverage" not in result.metadata["moonSpecVerify"]
            AgentRunResult(**result.model_dump(mode="json", by_alias=True))

            _artifact, artifact_path = await service.read_path(
                artifact_id=result.metadata["gateResultRef"],
                principal="system:agent_runtime",
            )
            persisted_payload = json.loads(artifact_path.read_text(encoding="utf-8"))
            assert persisted_payload["requirementCoverage"][0]["evidence"] == (
                large_evidence
            )


async def test_agent_runtime_publish_artifacts_flags_moonspec_contract_violations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            workspace = tmp_path / "workspace"
            verify_path = workspace / "var/artifacts/moonspec-verify/final.json"
            verify_path.parent.mkdir(parents=True)
            # Regression fixture mirroring the observed drift: an approving
            # verdict paired with a non-canonical recommendedNextAction.
            verify_path.write_text(
                json.dumps(
                    {
                        "schemaVersion": "moonspec-verify.issue_brief.v1",
                        "verdict": "FULLY_IMPLEMENTED",
                        "recommendedNextAction": "create_pull_request",
                        "recoverableInCurrentRuntime": True,
                        "remainingWork": [],
                    }
                ),
                encoding="utf-8",
            )
            run_store = ManagedRunStore(tmp_path / "runs")
            run_store.save(
                ManagedRunRecord(
                    runId="verify-run-2",
                    agentId="codex_cli",
                    runtimeId="codex_cli",
                    status="completed",
                    startedAt=datetime.now(timezone.utc),
                    workspacePath=workspace.as_posix(),
                )
            )
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            activities = TemporalAgentRuntimeActivities(
                artifact_service=service,
                run_store=run_store,
            )

            async def _skip_notify(*_args: Any, **_kwargs: Any) -> dict[str, str]:
                return {"status": "skipped"}

            monkeypatch.setattr(
                activities,
                "execution_notify_completion",
                _skip_notify,
            )
            monkeypatch.setattr(
                temporal_activity,
                "info",
                lambda: SimpleNamespace(
                    namespace="default",
                    workflow_id="parent-wf:agent:verify",
                    workflow_run_id="child-run-verify-2",
                ),
            )

            result = await activities.agent_runtime_publish_artifacts(
                AgentRunResult(
                    summary="Completed.",
                    metadata={
                        "agentRunId": "verify-run-2",
                        "verify_artifact_path": (
                            "var/artifacts/moonspec-verify/final.json"
                        ),
                    },
                )
            )

            violations = result.metadata["moonSpecVerify"]["contractViolations"]
            assert any("create_pull_request" in item for item in violations)
            AgentRunResult(**result.model_dump(mode="json", by_alias=True))


async def test_agent_runtime_publish_artifacts_uses_last_assistant_text_for_report_body(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            activities = TemporalAgentRuntimeActivities(artifact_service=service)

            monkeypatch.setattr(
                temporal_activity,
                "info",
                lambda: SimpleNamespace(
                    namespace="default",
                    workflow_id="parent-wf:agent:node-1",
                    workflow_run_id="child-run-1",
                ),
            )

            await activities.agent_runtime_publish_artifacts(
                AgentRunResult(
                    summary="Completed with status completed",
                    metadata={
                        "lastAssistantText": (
                            "# Docker Compose Update System Report\n\n"
                            "The implementation is missing report handoff coverage."
                        ),
                        "moonmind": {
                            "reportOutput": {
                                "enabled": True,
                                "required": True,
                                "reportType": "agent_run_report",
                                "primaryPath": "exports/final-answer.txt",
                                "executionRef": {
                                    "namespace": "default",
                                    "workflow_id": "parent-wf",
                                    "run_id": "parent-run-1",
                                },
                            }
                        },
                    },
                )
            )

            reports = await service.list_for_execution(
                namespace="default",
                workflow_id="parent-wf",
                run_id="parent-run-1",
                principal="system:agent_runtime",
                link_type="report.primary",
                latest_only=True,
            )

            assert len(reports) == 1
            assert reports[0].metadata_json["name"] == "final-answer.txt"
            _artifact, path = await service.read_path(
                artifact_id=reports[0].artifact_id,
                principal="system:agent_runtime",
            )
            rendered = path.read_text(encoding="utf-8")
            assert rendered.startswith("# Docker Compose Update System Report")
            assert "missing report handoff coverage" in rendered
            assert "Completed with status completed" not in rendered

async def test_agent_runtime_publish_artifacts_fails_required_report_on_publish_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FailingReportService:
        def __init__(self, wrapped: TemporalArtifactService) -> None:
            self._wrapped = wrapped

        async def create(self, **kwargs: Any) -> Any:
            return await self._wrapped.create(**kwargs)

        async def write_complete(self, **kwargs: Any) -> Any:
            return await self._wrapped.write_complete(**kwargs)

        async def publish_report_bundle(self, **_kwargs: Any) -> dict[str, Any]:
            raise RuntimeError("report publication failed")

    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            activities = TemporalAgentRuntimeActivities(
                artifact_service=_FailingReportService(service)  # type: ignore[arg-type]
            )

            monkeypatch.setattr(
                temporal_activity,
                "info",
                lambda: SimpleNamespace(
                    namespace="default",
                    workflow_id="parent-wf:agent:node-1",
                    workflow_run_id="child-run-1",
                ),
            )

            with pytest.raises(RuntimeError, match="report publication failed"):
                await activities.agent_runtime_publish_artifacts(
                    AgentRunResult(
                        summary="Completed.",
                        metadata={
                            "moonmind": {
                                "reportOutput": {
                                    "enabled": True,
                                    "required": True,
                                    "reportType": "integration_test_report",
                                    "executionRef": {
                                        "namespace": "default",
                                        "workflow_id": "parent-wf",
                                        "run_id": "parent-run-1",
                                    },
                                }
                            }
                        },
                    )
                )

async def test_agent_runtime_send_turn_retries_transient_failures(
    tmp_path: Path,
) -> None:
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker():
            catalog = build_default_activity_catalog()

            send_turn = catalog.resolve_activity("agent_runtime.send_turn")

            assert send_turn.retries.max_attempts == 5
            assert send_turn.retries.max_interval_seconds == 600
            assert (
                "CodexPermanentTurnError"
                in send_turn.retries.non_retryable_error_codes
            )
            assert send_turn.timeouts.start_to_close_seconds == 3600
            assert send_turn.timeouts.schedule_to_close_seconds == 3900
            assert send_turn.timeouts.heartbeat_timeout_seconds == 30
