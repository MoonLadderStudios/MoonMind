from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from moonmind.schemas.workload_models import RunnerProfile, WorkloadResult
from moonmind.workflows.skills.artifact_store import InMemoryArtifactStore
from moonmind.workflows.skills.skill_dispatcher import (
    SkillActivityDispatcher as ToolActivityDispatcher,
    execute_skill_activity as execute_tool_activity,
)
from moonmind.workflows.skills.skill_plan_contracts import (
    SkillFailure as ToolFailure,
    parse_skill_definition as parse_tool_definition,
)
from moonmind.workflows.skills.skill_registry import (
    SkillRegistrySnapshot,
    create_registry_snapshot,
)
from moonmind.workloads.registry import RunnerProfileRegistry
from moonmind.workloads.tool_bridge import (
    CONTAINER_RUN_WORKLOAD_TOOL,
    CONTAINER_START_HELPER_TOOL,
    CONTAINER_STOP_HELPER_TOOL,
    build_dood_tool_definition_payload,
    register_workload_tool_handlers,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]

WORKSPACE_ROOT = Path("/work/agent_jobs")

def _profile_payload() -> dict[str, object]:
    return {
        "id": "local-python",
        "kind": "one_shot",
        "image": "python:3.12-slim",
        "entrypoint": ["/bin/bash", "-lc"],
        "workdirTemplate": "/work/agent_jobs/${task_run_id}/repo",
        "requiredMounts": [
            {
                "type": "volume",
                "source": "agent_workspaces",
                "target": "/work/agent_jobs",
            }
        ],
        "envAllowlist": ["CI"],
        "networkPolicy": "none",
        "timeoutSeconds": 300,
        "maxTimeoutSeconds": 600,
        "cleanup": {
            "removeContainerOnExit": True,
            "killGraceSeconds": 30,
        },
    }

def _helper_profile_payload() -> dict[str, object]:
    return {
        "id": "redis-helper",
        "kind": "bounded_service",
        "image": "redis:7.2-alpine",
        "workdirTemplate": "/work/agent_jobs/${task_run_id}/repo",
        "requiredMounts": [
            {
                "type": "volume",
                "source": "agent_workspaces",
                "target": "/work/agent_jobs",
            }
        ],
        "envAllowlist": ["CI"],
        "networkPolicy": "bridge",
        "timeoutSeconds": 60,
        "maxTimeoutSeconds": 120,
        "helperTtlSeconds": 300,
        "maxHelperTtlSeconds": 900,
        "readinessProbe": {
            "type": "exec",
            "command": ["redis-cli", "ping"],
            "intervalSeconds": 0,
            "timeoutSeconds": 1,
            "retries": 3,
        },
        "cleanup": {
            "removeContainerOnExit": True,
            "killGraceSeconds": 3,
        },
    }

class _FakeLauncher:
    def __init__(self) -> None:
        self.validated: Any | None = None
        self.reason: str | None = None

    @staticmethod
    def _session_context(validated: Any) -> dict[str, object] | None:
        if validated.request.session_id is None:
            return None
        context: dict[str, object] = {"sessionId": validated.request.session_id}
        if validated.request.session_epoch is not None:
            context["sessionEpoch"] = validated.request.session_epoch
        if validated.request.source_turn_id is not None:
            context["sourceTurnId"] = validated.request.source_turn_id
        return context

    async def run(self, validated: Any) -> WorkloadResult:
        self.validated = validated
        profile_id = validated.profile.id if validated.profile is not None else validated.request.tool_name
        return WorkloadResult(
            requestId=validated.container_name,
            profileId=profile_id,
            status="succeeded",
            labels=validated.ownership.labels,
            exitCode=0,
            stdoutRef="art:sha256:stdout",
            stderrRef="art:sha256:stderr",
            diagnosticsRef="art:sha256:diagnostics",
            metadata={
                "containerName": validated.container_name,
                "workload": {
                    "toolName": validated.request.tool_name,
                    "profileId": profile_id,
                    "workflowDockerMode": getattr(validated.ownership, "workflow_docker_mode", None),
                    "sessionContext": self._session_context(validated),
                },
                "artifactPublication": {"status": "complete"},
            },
        )

    async def start_helper(self, validated: Any) -> WorkloadResult:
        self.validated = validated
        profile_id = validated.profile.id if validated.profile is not None else validated.request.tool_name
        return WorkloadResult(
            requestId=validated.container_name,
            profileId=profile_id,
            status="ready",
            labels=validated.ownership.labels,
            metadata={
                "helper": {
                    "containerName": validated.container_name,
                    "status": "ready",
                    "readiness": {"status": "ready", "attempts": 1},
                    "ttlSeconds": validated.request.ttl_seconds,
                },
                "artifactPublication": {"status": "complete"},
            },
        )

    async def stop_helper(
        self,
        validated: Any,
        *,
        reason: str = "bounded_window_complete",
    ) -> WorkloadResult:
        self.validated = validated
        self.reason = reason
        profile_id = validated.profile.id if validated.profile is not None else validated.request.tool_name
        return WorkloadResult(
            requestId=validated.container_name,
            profileId=profile_id,
            status="stopped",
            labels=validated.ownership.labels,
            metadata={
                "helper": {
                    "containerName": validated.container_name,
                    "status": "stopped",
                    "teardown": {"status": "complete", "reason": reason},
                },
                "artifactPublication": {"status": "complete"},
            },
        )

def _snapshot(*tool_names: str) -> SkillRegistrySnapshot:
    return create_registry_snapshot(
        skills=tuple(
            parse_tool_definition(
                build_dood_tool_definition_payload(name=tool_name, version="1.0")
            )
            for tool_name in tool_names
        ),
        artifact_store=InMemoryArtifactStore(),
    )

async def test_profile_backed_run_workload_routes_through_runner_profile() -> None:
    registry = RunnerProfileRegistry(
        [RunnerProfile.model_validate(_profile_payload())],
        workspace_root=WORKSPACE_ROOT,
    )
    launcher = _FakeLauncher()
    dispatcher = ToolActivityDispatcher()
    register_workload_tool_handlers(
        dispatcher,
        registry=registry,
        launcher=launcher,
        workflow_docker_mode="profiles",
    )

    result = await execute_tool_activity(
        invocation_payload={
            "id": "step-run-workload",
            "tool": {
                "type": "skill",
                "name": CONTAINER_RUN_WORKLOAD_TOOL,
                "version": "1.0",
            },
            "inputs": {
                "profileId": "local-python",
                "repoDir": "/work/agent_jobs/wf-1/repo",
                "artifactsDir": "/work/agent_jobs/wf-1/artifacts/workload",
                "command": ["pytest", "-q"],
                "envOverrides": {"CI": "1"},
            },
        },
        registry_snapshot=_snapshot(CONTAINER_RUN_WORKLOAD_TOOL),
        dispatcher=dispatcher,
        context={"workflow_id": "wf-1", "node_id": "step-run-workload"},
    )

    assert launcher.validated is not None
    assert launcher.validated.profile.id == "local-python"
    assert launcher.validated.profile.required_mounts[0].source == "agent_workspaces"
    assert launcher.validated.profile.cleanup.remove_container_on_exit is True
    assert launcher.validated.request.tool_name == CONTAINER_RUN_WORKLOAD_TOOL
    assert result.status == "COMPLETED"
    assert result.outputs["profileId"] == "local-python"

async def test_profile_backed_run_workload_keeps_session_metadata_as_association_only() -> None:
    registry = RunnerProfileRegistry(
        [RunnerProfile.model_validate(_profile_payload())],
        workspace_root=WORKSPACE_ROOT,
    )
    launcher = _FakeLauncher()
    dispatcher = ToolActivityDispatcher()
    register_workload_tool_handlers(
        dispatcher,
        registry=registry,
        launcher=launcher,
        workflow_docker_mode="profiles",
    )

    result = await execute_tool_activity(
        invocation_payload={
            "id": "step-run-workload-session",
            "tool": {
                "type": "skill",
                "name": CONTAINER_RUN_WORKLOAD_TOOL,
                "version": "1.0",
            },
            "inputs": {
                "profileId": "local-python",
                "repoDir": "/work/agent_jobs/wf-1/repo",
                "artifactsDir": "/work/agent_jobs/wf-1/artifacts/workload",
                "command": ["pytest", "-q"],
                "envOverrides": {"CI": "1"},
            },
        },
        registry_snapshot=_snapshot(CONTAINER_RUN_WORKLOAD_TOOL),
        dispatcher=dispatcher,
        context={
            "workflow_id": "wf-1",
            "node_id": "step-run-workload-session",
            "session_id": "session-1",
            "session_epoch": 2,
            "source_turn_id": "turn-5",
        },
    )

    assert launcher.validated is not None
    assert launcher.validated.request.session_id == "session-1"
    assert launcher.validated.request.session_epoch == 2
    assert launcher.validated.request.source_turn_id == "turn-5"
    assert result.outputs["workloadMetadata"]["sessionContext"] == {
        "sessionId": "session-1",
        "sessionEpoch": 2,
        "sourceTurnId": "turn-5",
    }
    assert "session.summary" not in result.outputs["outputRefs"]

@pytest.mark.parametrize(
    ("raw_field", "raw_value"),
    (
        ("image", "ghcr.io/example/runtime:1.2.3"),
        ("mounts", [{"source": "/tmp/host", "target": "/work/host"}]),
        ("privileged", True),
    ),
)
async def test_profile_backed_run_workload_rejects_raw_container_fields(
    raw_field: str,
    raw_value: object,
) -> None:
    registry = RunnerProfileRegistry(
        [RunnerProfile.model_validate(_profile_payload())],
        workspace_root=WORKSPACE_ROOT,
    )
    launcher = _FakeLauncher()
    dispatcher = ToolActivityDispatcher()
    register_workload_tool_handlers(
        dispatcher,
        registry=registry,
        launcher=launcher,
        workflow_docker_mode="profiles",
    )

    with pytest.raises(ToolFailure) as exc_info:
        await execute_tool_activity(
            invocation_payload={
                "id": "step-run-workload-invalid",
                "tool": {
                    "type": "skill",
                    "name": CONTAINER_RUN_WORKLOAD_TOOL,
                    "version": "1.0",
                },
                "inputs": {
                    "profileId": "local-python",
                    "repoDir": "/work/agent_jobs/wf-1/repo",
                    "artifactsDir": "/work/agent_jobs/wf-1/artifacts/workload",
                    "command": ["pytest", "-q"],
                    raw_field: raw_value,
                },
            },
            registry_snapshot=_snapshot(CONTAINER_RUN_WORKLOAD_TOOL),
            dispatcher=dispatcher,
            context={"workflow_id": "wf-1", "node_id": "step-run-workload-invalid"},
        )

    assert exc_info.value.error_code == "INVALID_INPUT"
    assert launcher.validated is None

async def test_profile_backed_helper_lifecycle_stays_bounded() -> None:
    registry = RunnerProfileRegistry(
        [RunnerProfile.model_validate(_helper_profile_payload())],
        workspace_root=WORKSPACE_ROOT,
    )
    launcher = _FakeLauncher()
    dispatcher = ToolActivityDispatcher()
    register_workload_tool_handlers(
        dispatcher,
        registry=registry,
        launcher=launcher,
        workflow_docker_mode="profiles",
    )
    snapshot = _snapshot(CONTAINER_START_HELPER_TOOL, CONTAINER_STOP_HELPER_TOOL)

    start_result = await execute_tool_activity(
        invocation_payload={
            "id": "step-start-helper",
            "tool": {
                "type": "skill",
                "name": CONTAINER_START_HELPER_TOOL,
                "version": "1.0",
            },
            "inputs": {
                "profileId": "redis-helper",
                "stepId": "helper-lifecycle",
                "repoDir": "/work/agent_jobs/wf-1/repo",
                "artifactsDir": "/work/agent_jobs/wf-1/artifacts/helper",
                "command": ["--appendonly", "no"],
                "ttlSeconds": 300,
            },
        },
        registry_snapshot=snapshot,
        dispatcher=dispatcher,
        context={"workflow_id": "wf-1", "node_id": "step-start-helper"},
    )

    assert launcher.validated is not None
    assert launcher.validated.profile.kind == "bounded_service"
    assert launcher.validated.container_name == "mm-helper-wf-1-helper-lifecycle-1"
    assert start_result.status == "COMPLETED"
    assert start_result.outputs["workloadMetadata"]["helper"]["readiness"]["status"] == "ready"

    stop_result = await execute_tool_activity(
        invocation_payload={
            "id": "step-stop-helper",
            "tool": {
                "type": "skill",
                "name": CONTAINER_STOP_HELPER_TOOL,
                "version": "1.0",
            },
            "inputs": {
                "profileId": "redis-helper",
                "stepId": "helper-lifecycle",
                "repoDir": "/work/agent_jobs/wf-1/repo",
                "artifactsDir": "/work/agent_jobs/wf-1/artifacts/helper",
                "ttlSeconds": 300,
                "reason": "owner_task_canceled",
            },
        },
        registry_snapshot=snapshot,
        dispatcher=dispatcher,
        context={"workflow_id": "wf-1", "node_id": "step-stop-helper"},
    )

    assert launcher.reason == "owner_task_canceled"
    assert stop_result.status == "COMPLETED"
    assert stop_result.outputs["workloadMetadata"]["helper"]["teardown"]["reason"] == (
        "owner_task_canceled"
    )

async def test_unrestricted_run_docker_preserves_shared_workload_metadata() -> None:
    launcher = _FakeLauncher()
    dispatcher = ToolActivityDispatcher()
    register_workload_tool_handlers(
        dispatcher,
        registry=RunnerProfileRegistry.empty(workspace_root=WORKSPACE_ROOT),
        launcher=launcher,
        workflow_docker_mode="unrestricted",
    )

    result = await execute_tool_activity(
        invocation_payload={
            "id": "step-run-docker",
            "tool": {
                "type": "skill",
                "name": "container.run_docker",
                "version": "1.0",
            },
            "inputs": {
                "repoDir": "/work/agent_jobs/wf-1/repo",
                "artifactsDir": "/work/agent_jobs/wf-1/artifacts/docker",
                "command": ["docker", "ps"],
            },
        },
        registry_snapshot=_snapshot("container.run_docker"),
        dispatcher=dispatcher,
        context={"workflow_id": "wf-1", "node_id": "step-run-docker"},
    )

    assert result.status == "COMPLETED"
    assert launcher.validated is not None
    assert launcher.validated.request.tool_name == "container.run_docker"
    assert result.outputs["workloadMetadata"]["workflowDockerMode"] == "unrestricted"
    assert result.outputs["workloadMetadata"]["toolName"] == "container.run_docker"

async def test_disabled_mode_denies_profile_backed_workload_tools() -> None:
    registry = RunnerProfileRegistry(
        [RunnerProfile.model_validate(_profile_payload())],
        workspace_root=WORKSPACE_ROOT,
    )
    dispatcher = ToolActivityDispatcher()
    register_workload_tool_handlers(
        dispatcher,
        registry=registry,
        launcher=_FakeLauncher(),
        workflow_docker_mode="disabled",
    )

    assert dispatcher._skill_handlers == {}

    with pytest.raises(ToolFailure) as exc_info:
        await execute_tool_activity(
            invocation_payload={
                "id": "step-run-workload-disabled",
                "tool": {
                    "type": "skill",
                    "name": CONTAINER_RUN_WORKLOAD_TOOL,
                    "version": "1.0",
                },
                "inputs": {
                    "profileId": "local-python",
                    "repoDir": "/work/agent_jobs/wf-1/repo",
                    "artifactsDir": "/work/agent_jobs/wf-1/artifacts/workload",
                    "command": ["pytest", "-q"],
                },
            },
            registry_snapshot=_snapshot(CONTAINER_RUN_WORKLOAD_TOOL),
            dispatcher=dispatcher,
            context={"workflow_id": "wf-1", "node_id": "step-run-workload-disabled"},
        )

    assert exc_info.value.error_code == "INVALID_INPUT"
    assert "No mm.tool.execute handler registered" in exc_info.value.message
