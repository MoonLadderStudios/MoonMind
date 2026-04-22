"""Unit tests for Temporal activity-family runtime helpers."""

from __future__ import annotations

import json
import re
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import Base
from moonmind.config.settings import settings
from moonmind.jules.runtime import JULES_RUNTIME_DISABLED_MESSAGE
from moonmind.schemas.jules_models import JulesTaskResponse
from moonmind.workflows.skills.artifact_store import InMemoryArtifactStore
from moonmind.workflows.skills.skill_dispatcher import SkillActivityDispatcher
from moonmind.workflows.skills.skill_plan_contracts import SkillResult
from moonmind.workflows.skills.skill_registry import (
    create_registry_snapshot,
    parse_skill_registry,
)
from moonmind.workflows.temporal.activity_catalog import (
    AGENT_RUNTIME_FLEET,
    ARTIFACTS_FLEET,
    SANDBOX_FLEET,
    build_default_activity_catalog,
)
from moonmind.workflows.temporal.activity_runtime import (
    SandboxCommandResult,
    TemporalActivityRuntimeError,
    TemporalAgentRuntimeActivities,
    TemporalIntegrationActivities,
    TemporalManifestActivities,
    TemporalPlanActivities,
    TemporalProposalActivities,
    TemporalSandboxActivities,
    TemporalSkillActivities,
    _default_registry_skill_payload,
    _default_skill_registry_payload,
    build_activity_bindings,
    build_activity_execution_context,
    build_activity_invocation_envelope,
    build_compact_activity_result,
    build_observability_summary,
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

pytestmark = [pytest.mark.asyncio]


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
                "version": "1.0.0",
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
                "skill": {"name": "repo.run_tests", "version": "1.0.0"},
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
                            "skill": {"name": "code", "version": "1.0"},
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
                            "tool": {"type": "skill", "name": "code", "version": "1.0"},
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
                            "skill": {"name": "dummy", "version": "1.0"},
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
            "task": {
                "tool": {
                    "type": "skill",
                    "name": "pr-resolver",
                    "version": "1.0",
                }
            }
        }
    )
    skills = payload.get("skills")
    assert isinstance(skills, list)
    keyset = {
        (str(item.get("name")), str(item.get("version")))
        for item in skills
        if isinstance(item, dict)
    }
    # 'auto' is a placeholder and must not be in the registry when explicit skills are present
    assert ("auto", "1.0") not in keyset
    assert ("pr-resolver", "1.0") in keyset


async def test_default_skill_registry_payload_auto_placeholder_filtered():
    """When 'auto' is the only (placeholder) skill, it must not appear in the registry."""
    payload = _default_skill_registry_payload(
        parameters={
            "task": {
                "skill": {
                    "name": "auto",
                    "version": "1.0",
                }
            }
        }
    )
    skills = payload.get("skills")
    assert isinstance(skills, list)
    keyset = {
        (str(item.get("name")), str(item.get("version")))
        for item in skills
        if isinstance(item, dict)
    }
    # 'auto' is a placeholder and must not appear in the registry at all
    assert ("auto", "1.0") not in keyset


@pytest.mark.parametrize(
    "skill_name", ["jira-issue-creator", "jira-pr-verify", "jira-verify"]
)
async def test_default_skill_registry_payload_excludes_agent_only_jira_skill(
    skill_name: str,
):
    payload = _default_skill_registry_payload(
        parameters={
            "task": {
                "tool": {
                    "type": "skill",
                    "name": skill_name,
                    "version": "1.0",
                }
            }
        }
    )
    skills = payload.get("skills")
    assert skills == []


async def test_default_skill_registry_payload_uses_dood_tool_definitions():
    payload = _default_skill_registry_payload(
        parameters={
            "task": {
                "steps": [
                    {
                        "tool": {
                            "type": "skill",
                            "name": "container.run_workload",
                            "version": "1.0",
                        }
                    },
                    {
                        "tool": {
                            "type": "skill",
                            "name": "unreal.run_tests",
                            "version": "1.0",
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


async def test_default_skill_registry_payload_uses_curated_pentest_tool_definition():
    payload = _default_skill_registry_payload(
        parameters={
            "task": {
                "steps": [
                    {
                        "tool": {
                            "type": "skill",
                            "name": "security.pentest.run",
                            "version": "1.0.0",
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
    assert definition["version"] == "1.0.0"
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
    assert input_schema["required"] == [
        "target",
        "scope_artifact_ref",
        "operation_mode",
        "runner_profile_id",
    ]
    assert input_schema["properties"]["operation_mode"]["enum"] == [
        "recon_only",
        "validate_hypothesis",
        "full_authorized",
    ]
    assert input_schema["properties"]["provider_selector"][
        "additionalProperties"
    ] is False
    assert set(input_schema["properties"]["provider_selector"]["properties"]) == {
        "provider_id",
        "tags_any",
        "tags_all",
    }
    assert input_schema["properties"]["time_budget_minutes"] == {
        "type": "integer",
        "minimum": 1,
        "maximum": 480,
        "default": 60,
    }
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
        "stdout_artifact_ref",
        "stderr_artifact_ref",
        "diagnostics_artifact_ref",
        "summary_artifact_ref",
        "findings_artifact_ref",
    ]
    assert {
        "evidence_bundle_artifact_ref",
        "provider_snapshot_artifact_ref",
        "findings_count",
        "confirmed_findings_count",
    }.issubset(output_schema["properties"])

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
    assert [(tool.name, tool.version) for tool in parsed] == [
        ("security.pentest.run", "1.0.0")
    ]
    assert parsed[0].executor.activity_type == "security.pentest.execute"


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
                        "tool": {"type": "skill", "name": "auto", "version": "1.0"},
                        "skill": {"name": "auto", "version": "1.0"},
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


async def test_default_registry_payload_uses_extended_timeouts_for_pr_resolver():
    payload = _default_registry_skill_payload(name="pr-resolver", version="1.0")
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
                version="1.0.0",
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
                    "skill": {"name": "repo.run_tests", "version": "1.0.0"},
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
                version="1.0.0",
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
                    "skill": {"name": "repo.run_tests", "version": "1.0.0"},
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
            assert any(
                binding.handler.__name__ == "artifact_lifecycle_sweep"
                for binding in bindings
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
        version="1.0.0",
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
                        "skill": {"name": "repo.run_tests", "version": "1.0.0"},
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
            assert "agent_runtime.status" in bound_types
            assert "agent_runtime.fetch_result" in bound_types
            assert "agent_runtime.cancel" in bound_types
            assert "agent_skill.resolve" in bound_types
            assert "agent_skill.materialize" in bound_types
            assert "agent_skill.build_prompt_index" in bound_types


async def test_agent_runtime_send_turn_disables_catalog_retries(
    tmp_path: Path,
) -> None:
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker():
            catalog = build_default_activity_catalog()

            send_turn = catalog.resolve_activity("agent_runtime.send_turn")

            assert send_turn.retries.max_attempts == 1
            assert send_turn.timeouts.start_to_close_seconds == 3600
            assert send_turn.timeouts.schedule_to_close_seconds == 3900
            assert send_turn.timeouts.heartbeat_timeout_seconds == 30
