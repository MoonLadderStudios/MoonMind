from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from moonmind.schemas.workload_models import RunnerProfile, WorkloadResult
from moonmind.workflows.skills.skill_plan_contracts import SkillFailure, SkillResult
from moonmind.workloads.registry import RunnerProfileRegistry
from moonmind.workloads.tool_bridge import (
    CONTAINER_RUN_CONTAINER_TOOL,
    CONTAINER_RUN_DOCKER_TOOL,
    DOOD_TOOL_NAMES,
    INTEGRATION_CI_PROFILE_ID,
    INTEGRATION_CI_TOOL,
    build_dood_tool_definition_payload,
    build_workload_tool_handler,
    register_workload_tool_handlers,
)

WORKSPACE_ROOT = Path("/work/agent_jobs")

def _profile_payload(profile_id: str = "local-python") -> dict[str, object]:
    return {
        "id": profile_id,
        "kind": "one_shot",
        "image": "python:3.12-slim",
        "workdirTemplate": "/work/agent_jobs/${task_run_id}/repo",
        "requiredMounts": [
            {
                "type": "volume",
                "source": "agent_workspaces",
                "target": "/work/agent_jobs",
            }
        ],
        "envAllowlist": [
            "CI",
            "UE_PROJECT_PATH",
            "UE_TARGET",
            "UE_TEST_SELECTOR",
            "UE_REPORT_PATH",
            "UE_SUMMARY_PATH",
            "UE_JUNIT_PATH",
        ],
        "networkPolicy": "none",
        "timeoutSeconds": 300,
        "maxTimeoutSeconds": 600,
    }

def _helper_profile_payload(profile_id: str = "redis-helper") -> dict[str, object]:
    return {
        "id": profile_id,
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
    def __init__(self, *, status: str = "succeeded") -> None:
        self.validated: Any | None = None
        self._status = status

    async def run(self, validated: Any) -> WorkloadResult:
        self.validated = validated
        return WorkloadResult(
            requestId=validated.container_name,
            profileId=(validated.profile.id if validated.profile is not None else validated.request.tool_name),
            status=self._status,
            labels=validated.ownership.labels,
            exitCode=0 if self._status == "succeeded" else 1,
            stdoutRef="/work/agent_jobs/task-1/artifacts/step-test/workload/stdout.log",
            stderrRef="/work/agent_jobs/task-1/artifacts/step-test/workload/stderr.log",
            diagnosticsRef=(
                "/work/agent_jobs/task-1/artifacts/step-test/workload/"
                "diagnostics.json"
            ),
            outputRefs={
                "runtime.stdout": (
                    "/work/agent_jobs/task-1/artifacts/step-test/workload/stdout.log"
                ),
                "runtime.stderr": (
                    "/work/agent_jobs/task-1/artifacts/step-test/workload/stderr.log"
                ),
                "runtime.diagnostics": (
                    "/work/agent_jobs/task-1/artifacts/step-test/workload/"
                    "diagnostics.json"
                ),
            },
            metadata={
                "containerName": validated.container_name,
                "workload": {
                    "taskRunId": validated.request.task_run_id,
                    "stepId": validated.request.step_id,
                    "attempt": validated.request.attempt,
                    "toolName": validated.request.tool_name,
                    "profileId": (validated.profile.id if validated.profile is not None else validated.request.tool_name),
                    "workflowDockerMode": validated.ownership.workflow_docker_mode,
                    "sessionContext": (
                        {
                            "sessionId": validated.request.session_id,
                            "sessionEpoch": validated.request.session_epoch,
                            "sourceTurnId": validated.request.source_turn_id,
                        }
                        if validated.request.session_id is not None
                        else None
                    ),
                },
                "artifactPublication": {"status": "complete"},
            },
        )

    async def start_helper(self, validated: Any) -> WorkloadResult:
        self.validated = validated
        return WorkloadResult(
            requestId=validated.container_name,
            profileId=(validated.profile.id if validated.profile is not None else validated.request.tool_name),
            status="ready",
            labels=validated.ownership.labels,
            exitCode=None,
            metadata={
                "helper": {
                    "containerName": validated.container_name,
                    "status": "ready",
                    "ttlSeconds": validated.request.ttl_seconds,
                    "readiness": {"status": "ready", "attempts": 1},
                    "sessionContext": None,
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
        return WorkloadResult(
            requestId=validated.container_name,
            profileId=(validated.profile.id if validated.profile is not None else validated.request.tool_name),
            status="stopped",
            labels=validated.ownership.labels,
            exitCode=None,
            metadata={
                "helper": {
                    "containerName": validated.container_name,
                    "status": "stopped",
                    "teardown": {
                        "status": "complete",
                        "reason": reason,
                        "removeContainerOnExit": True,
                    },
                },
                "artifactPublication": {"status": "complete"},
            },
        )

class _FailingRegistry:
    def validate_request(self, _request: object) -> object:
        raise AssertionError("registry validation should not run")

class _FailingLauncher:
    async def run(self, _validated: object) -> object:
        raise AssertionError("launcher should not run")

    async def start_helper(self, _validated: object) -> object:
        raise AssertionError("launcher should not run")

    async def stop_helper(self, _validated: object, *, reason: str) -> object:
        raise AssertionError("launcher should not run")

def test_container_run_workload_tool_definition_routes_to_docker_workload() -> None:
    definition = build_dood_tool_definition_payload(
        name="container.run_workload",
        version="1.0",
    )

    assert definition["type"] == "skill"
    assert definition["executor"]["activity_type"] == "mm.tool.execute"
    assert definition["requirements"]["capabilities"] == ["docker_workload"]
    assert "image" not in definition["inputs"]["schema"]["properties"]
    assert "mounts" not in definition["inputs"]["schema"]["properties"]
    assert "devices" not in definition["inputs"]["schema"]["properties"]
    resources_schema = definition["inputs"]["schema"]["properties"]["resources"]
    assert resources_schema == {
        "type": "object",
        "properties": {
            "cpu": {"type": "string", "minLength": 1},
            "memory": {"type": "string", "minLength": 1},
            "shmSize": {"type": "string", "minLength": 1},
        },
        "additionalProperties": False,
    }
    output_properties = definition["outputs"]["schema"]["properties"]
    assert output_properties["outputRefs"] == {"type": "object"}

def test_unreal_run_tests_tool_definition_routes_to_docker_workload() -> None:
    definition = build_dood_tool_definition_payload(
        name="unreal.run_tests",
        version="1.0",
    )

    assert definition["type"] == "skill"
    assert definition["executor"]["activity_type"] == "mm.tool.execute"
    assert definition["requirements"]["capabilities"] == ["docker_workload"]
    assert definition["inputs"]["schema"]["required"] == [
        "repoDir",
        "artifactsDir",
        "projectPath",
    ]
    assert "image" not in definition["inputs"]["schema"]["properties"]
    assert "mounts" not in definition["inputs"]["schema"]["properties"]
    assert "devices" not in definition["inputs"]["schema"]["properties"]
    assert "reportPaths" in definition["inputs"]["schema"]["properties"]
    resources_schema = definition["inputs"]["schema"]["properties"]["resources"]
    assert resources_schema == {
        "type": "object",
        "properties": {
            "cpu": {"type": "string", "minLength": 1},
            "memory": {"type": "string", "minLength": 1},
            "shmSize": {"type": "string", "minLength": 1},
        },
        "additionalProperties": False,
    }

@pytest.mark.parametrize(
    ("tool_name", "required"),
    [
        (
            "container.start_helper",
            ["profileId", "repoDir", "artifactsDir", "command", "ttlSeconds"],
        ),
        (
            "container.stop_helper",
            ["profileId", "repoDir", "artifactsDir", "ttlSeconds"],
        ),
    ],
)
def test_helper_tool_definitions_route_to_docker_workload(
    tool_name: str,
    required: list[str],
) -> None:
    definition = build_dood_tool_definition_payload(
        name=tool_name,
        version="1.0",
    )

    assert definition["type"] == "skill"
    assert definition["executor"]["activity_type"] == "mm.tool.execute"
    assert definition["requirements"]["capabilities"] == ["docker_workload"]
    assert definition["inputs"]["schema"]["required"] == required
    assert "image" not in definition["inputs"]["schema"]["properties"]
    assert "mounts" not in definition["inputs"]["schema"]["properties"]
    assert "devices" not in definition["inputs"]["schema"]["properties"]
    assert "ttlSeconds" in definition["inputs"]["schema"]["properties"]

@pytest.mark.asyncio
async def test_container_run_workload_handler_validates_and_calls_launcher() -> None:
    registry = RunnerProfileRegistry(
        [RunnerProfile.model_validate(_profile_payload())],
        workspace_root=WORKSPACE_ROOT,
    )
    launcher = _FakeLauncher()
    handler = build_workload_tool_handler(
        tool_name="container.run_workload",
        registry=registry,
        launcher=launcher,
    )

    result = await handler(
        {
            "profileId": "local-python",
            "repoDir": "/work/agent_jobs/task-1/repo",
            "artifactsDir": "/work/agent_jobs/task-1/artifacts/step-test",
            "command": ["python", "-V"],
            "envOverrides": {"CI": "1"},
        },
        {
            "workflow_id": "task-1",
            "node_id": "step-test",
            "attempt": 2,
            "session_id": "session-1",
            "session_epoch": 3,
            "source_turn_id": "turn-1",
        },
    )

    assert isinstance(result, SkillResult)
    assert result.status == "COMPLETED"
    assert launcher.validated is not None
    assert launcher.validated.request.tool_name == "container.run_workload"
    assert launcher.validated.request.task_run_id == "task-1"
    assert launcher.validated.request.step_id == "step-test"
    assert launcher.validated.request.attempt == 2
    assert launcher.validated.request.session_id == "session-1"
    assert launcher.validated.request.source_turn_id == "turn-1"
    assert result.outputs["workloadResult"]["status"] == "succeeded"
    assert result.outputs["stdoutRef"].endswith("stdout.log")
    assert result.outputs["stderrRef"].endswith("stderr.log")
    assert result.outputs["diagnosticsRef"].endswith("diagnostics.json")
    assert result.outputs["outputRefs"]["runtime.stdout"].endswith("stdout.log")
    assert result.progress["outputRefs"]["runtime.stdout"].endswith("stdout.log")
    workload_metadata = result.outputs["workloadMetadata"]
    assert workload_metadata["stepId"] == "step-test"
    assert workload_metadata["sessionContext"] == {
        "sessionId": "session-1",
        "sessionEpoch": 3,
        "sourceTurnId": "turn-1",
    }
    assert workload_metadata["workflowDockerMode"] == "profiles"
    assert workload_metadata["artifactPublication"] == {"status": "complete"}
    assert result.progress["workloadMetadata"]["artifactPublication"] == {
        "status": "complete"
    }
    assert "session.summary" not in result.outputs["outputRefs"]

def test_integration_ci_tool_definition_routes_to_docker_workload() -> None:
    definition = build_dood_tool_definition_payload(
        name=INTEGRATION_CI_TOOL,
        version="1.0",
    )

    assert definition["name"] == INTEGRATION_CI_TOOL
    assert definition["type"] == "skill"
    assert definition["executor"]["activity_type"] == "mm.tool.execute"
    assert definition["requirements"]["capabilities"] == ["docker_workload"]
    assert definition["inputs"]["schema"]["required"] == ["repoDir", "artifactsDir"]
    properties = definition["inputs"]["schema"]["properties"]
    assert "profileId" not in properties
    assert "command" not in properties
    assert "image" not in properties
    assert "mounts" not in properties
    assert "devices" not in properties

@pytest.mark.asyncio
async def test_workload_tool_handler_denies_when_workflow_docker_disabled() -> None:
    handler = build_workload_tool_handler(
        tool_name="container.run_workload",
        registry=_FailingRegistry(),
        launcher=_FailingLauncher(),
        workflow_docker_mode="disabled",
    )

    with pytest.raises(SkillFailure) as exc_info:
        await handler(
            {
                "profileId": "local-python",
                "repoDir": "/work/agent_jobs/task-1/repo",
                "artifactsDir": "/work/agent_jobs/task-1/artifacts/step-test",
                "command": ["python", "-V"],
            },
            {"workflow_id": "task-1", "node_id": "step-test"},
        )

    assert exc_info.value.error_code == "PERMISSION_DENIED"
    assert exc_info.value.details["reason"] == "docker_workflows_disabled"
    assert "docker_workflows_disabled" in exc_info.value.message

@pytest.mark.asyncio
async def test_integration_ci_tool_denies_when_workflow_docker_disabled() -> None:
    handler = build_workload_tool_handler(
        tool_name=INTEGRATION_CI_TOOL,
        registry=_FailingRegistry(),
        launcher=_FailingLauncher(),
        workflow_docker_mode="disabled",
    )

    with pytest.raises(SkillFailure) as exc_info:
        await handler(
            {
                "repoDir": "/work/agent_jobs/task-1/repo",
                "artifactsDir": "/work/agent_jobs/task-1/artifacts/integration-ci",
            },
            {"workflow_id": "task-1", "node_id": "integration-ci"},
        )

    assert exc_info.value.error_code == "PERMISSION_DENIED"
    assert exc_info.value.details["reason"] == "docker_workflows_disabled"

@pytest.mark.asyncio
async def test_integration_ci_tool_maps_to_curated_profile_and_script() -> None:
    registry = RunnerProfileRegistry(
        [RunnerProfile.model_validate(_profile_payload(INTEGRATION_CI_PROFILE_ID))],
        workspace_root=WORKSPACE_ROOT,
    )
    launcher = _FakeLauncher()
    handler = build_workload_tool_handler(
        tool_name=INTEGRATION_CI_TOOL,
        registry=registry,
        launcher=launcher,
    )

    result = await handler(
        {
            "repoDir": "/work/agent_jobs/task-1/repo",
            "artifactsDir": "/work/agent_jobs/task-1/artifacts/integration-ci",
            "timeoutSeconds": 300,
            "envOverrides": {"CI": "1"},
        },
        {"workflow_id": "task-1", "node_id": "integration-ci"},
    )

    assert launcher.validated is not None
    assert launcher.validated.request.tool_name == INTEGRATION_CI_TOOL
    assert launcher.validated.request.profile_id == INTEGRATION_CI_PROFILE_ID
    assert launcher.validated.request.command == ("./tools/test_integration.sh",)
    assert result.status == "COMPLETED"
    assert result.outputs["profileId"] == INTEGRATION_CI_PROFILE_ID
    assert result.outputs["stdoutRef"].endswith("stdout.log")
    assert result.outputs["stderrRef"].endswith("stderr.log")
    assert result.outputs["diagnosticsRef"].endswith("diagnostics.json")
    assert result.outputs["workloadMetadata"]["toolName"] == INTEGRATION_CI_TOOL

@pytest.mark.asyncio
async def test_container_run_workload_handler_accepts_explicit_ids_without_context() -> None:
    registry = RunnerProfileRegistry(
        [RunnerProfile.model_validate(_profile_payload())],
        workspace_root=WORKSPACE_ROOT,
    )
    launcher = _FakeLauncher()
    handler = build_workload_tool_handler(
        tool_name="container.run_workload",
        registry=registry,
        launcher=launcher,
    )

    result = await handler(
        {
            "profileId": "local-python",
            "taskRunId": "task-explicit",
            "stepId": "step-explicit",
            "attempt": 3,
            "repoDir": "/work/agent_jobs/task-explicit/repo",
            "artifactsDir": "/work/agent_jobs/task-explicit/artifacts/step-explicit",
            "command": ["python", "-V"],
        },
        None,
    )

    assert result.status == "COMPLETED"
    assert launcher.validated is not None
    assert launcher.validated.request.task_run_id == "task-explicit"
    assert launcher.validated.request.step_id == "step-explicit"
    assert launcher.validated.request.attempt == 3

@pytest.mark.asyncio
@pytest.mark.parametrize("raw_field", ["image", "mounts", "devices", "privileged"])
async def test_container_run_workload_handler_rejects_raw_docker_fields(
    raw_field: str,
) -> None:
    registry = RunnerProfileRegistry(
        [RunnerProfile.model_validate(_profile_payload())],
        workspace_root=WORKSPACE_ROOT,
    )
    launcher = _FakeLauncher()
    handler = build_workload_tool_handler(
        tool_name="container.run_workload",
        registry=registry,
        launcher=launcher,
    )

    with pytest.raises(SkillFailure) as exc_info:
        await handler(
            {
                "profileId": "local-python",
                "repoDir": "/work/agent_jobs/task-1/repo",
                "artifactsDir": "/work/agent_jobs/task-1/artifacts/step-test",
                "command": ["python", "-V"],
                raw_field: "not-allowed",
            },
            {"workflow_id": "task-1", "node_id": "step-test"},
        )

    assert exc_info.value.error_code == "INVALID_INPUT"
    assert launcher.validated is None

@pytest.mark.asyncio
async def test_container_run_workload_policy_failure_omits_env_values() -> None:
    registry = RunnerProfileRegistry(
        [RunnerProfile.model_validate(_profile_payload())],
        workspace_root=WORKSPACE_ROOT,
    )
    launcher = _FakeLauncher()
    handler = build_workload_tool_handler(
        tool_name="container.run_workload",
        registry=registry,
        launcher=launcher,
    )
    secret_value = "inline_secret_value_for_redaction_test"

    with pytest.raises(SkillFailure) as exc_info:
        await handler(
            {
                "profileId": "local-python",
                "repoDir": "/work/agent_jobs/task-1/repo",
                "artifactsDir": "/work/agent_jobs/task-1/artifacts/step-test",
                "command": ["python", "-V"],
                "envOverrides": {"SECRET_TOKEN": secret_value},
            },
            {"workflow_id": "task-1", "node_id": "step-test"},
        )

    failure = exc_info.value
    failure_text = f"{failure.message} {failure.details!r}"
    assert failure.error_code == "PERMISSION_DENIED"
    assert failure.details == {
        "reason": "disallowed_env_key",
        "envKey": "SECRET_TOKEN",
        "profileId": "local-python",
    }
    assert secret_value not in failure_text
    assert launcher.validated is None

@pytest.mark.asyncio
async def test_container_run_workload_handler_maps_failed_result_to_tool_failure_status() -> None:
    registry = RunnerProfileRegistry(
        [RunnerProfile.model_validate(_profile_payload())],
        workspace_root=WORKSPACE_ROOT,
    )
    launcher = _FakeLauncher(status="failed")
    handler = build_workload_tool_handler(
        tool_name="container.run_workload",
        registry=registry,
        launcher=launcher,
    )

    result = await handler(
        {
            "profileId": "local-python",
            "repoDir": "/work/agent_jobs/task-1/repo",
            "artifactsDir": "/work/agent_jobs/task-1/artifacts/step-test",
            "command": ["python", "-V"],
        },
        {"workflow_id": "task-1", "node_id": "step-test"},
    )

    assert result.status == "FAILED"
    assert result.outputs["workloadStatus"] == "failed"
    assert result.outputs["exitCode"] == 1

@pytest.mark.asyncio
async def test_container_start_helper_handler_uses_bounded_helper_lifecycle() -> None:
    registry = RunnerProfileRegistry(
        [RunnerProfile.model_validate(_helper_profile_payload())],
        workspace_root=WORKSPACE_ROOT,
    )
    launcher = _FakeLauncher()
    handler = build_workload_tool_handler(
        tool_name="container.start_helper",
        registry=registry,
        launcher=launcher,
    )

    result = await handler(
        {
            "profileId": "redis-helper",
            "repoDir": "/work/agent_jobs/task-1/repo",
            "artifactsDir": "/work/agent_jobs/task-1/artifacts/step-service",
            "command": ["--appendonly", "no"],
            "ttlSeconds": 300,
            "envOverrides": {"CI": "1"},
        },
        {"workflow_id": "task-1", "node_id": "step-service"},
    )

    assert result.status == "COMPLETED"
    assert launcher.validated is not None
    assert launcher.validated.request.tool_name == "container.start_helper"
    assert launcher.validated.request.ttl_seconds == 300
    assert launcher.validated.container_name == "mm-helper-task-1-step-service-1"
    assert result.outputs["workloadStatus"] == "ready"
    helper_metadata = result.outputs["workloadMetadata"]["helper"]
    assert helper_metadata["status"] == "ready"
    assert helper_metadata["readiness"] == {"status": "ready", "attempts": 1}

@pytest.mark.asyncio
async def test_container_stop_helper_handler_maps_reason_and_default_command() -> None:
    registry = RunnerProfileRegistry(
        [RunnerProfile.model_validate(_helper_profile_payload())],
        workspace_root=WORKSPACE_ROOT,
    )
    launcher = _FakeLauncher()
    handler = build_workload_tool_handler(
        tool_name="container.stop_helper",
        registry=registry,
        launcher=launcher,
    )

    result = await handler(
        {
            "profileId": "redis-helper",
            "repoDir": "/work/agent_jobs/task-1/repo",
            "artifactsDir": "/work/agent_jobs/task-1/artifacts/step-service",
            "ttlSeconds": 300,
            "reason": "owner_task_canceled",
        },
        {"workflow_id": "task-1", "node_id": "step-service"},
    )

    assert result.status == "COMPLETED"
    assert launcher.validated is not None
    assert launcher.validated.request.tool_name == "container.stop_helper"
    assert launcher.validated.request.command == ("stop",)
    assert result.outputs["workloadStatus"] == "stopped"
    helper_metadata = result.outputs["workloadMetadata"]["helper"]
    assert helper_metadata["teardown"]["reason"] == "owner_task_canceled"

@pytest.mark.asyncio
async def test_unreal_run_tests_handler_builds_curated_command() -> None:
    registry = RunnerProfileRegistry(
        [RunnerProfile.model_validate(_profile_payload("unreal-5_3-linux"))],
        workspace_root=WORKSPACE_ROOT,
    )
    launcher = _FakeLauncher()
    handler = build_workload_tool_handler(
        tool_name="unreal.run_tests",
        registry=registry,
        launcher=launcher,
    )

    result = await handler(
        {
            "repoDir": "/work/agent_jobs/task-1/repo",
            "artifactsDir": "/work/agent_jobs/task-1/artifacts/unreal",
            "projectPath": "Game/Game.uproject",
            "target": "Editor",
            "testSelector": "Project.Functional",
            "reportPaths": {
                "primary": "reports/unreal/results.json",
                "summary": "reports/unreal/summary.json",
                "junit": "reports/unreal/junit.xml",
            },
        },
        {"workflow_id": "task-1", "node_id": "unreal-tests"},
    )

    assert result.status == "COMPLETED"
    assert launcher.validated is not None
    assert launcher.validated.request.profile_id == "unreal-5_3-linux"
    assert launcher.validated.request.tool_name == "unreal.run_tests"
    assert launcher.validated.request.command == (
        "unreal-run-tests",
        "--project",
        "Game/Game.uproject",
        "--target",
        "Editor",
        "--test",
        "Project.Functional",
        "--report",
        "reports/unreal/results.json",
        "--summary",
        "reports/unreal/summary.json",
        "--junit",
        "reports/unreal/junit.xml",
    )
    assert launcher.validated.request.env_overrides == {
        "UE_PROJECT_PATH": "Game/Game.uproject",
        "UE_TARGET": "Editor",
        "UE_TEST_SELECTOR": "Project.Functional",
        "UE_REPORT_PATH": "reports/unreal/results.json",
        "UE_SUMMARY_PATH": "reports/unreal/summary.json",
        "UE_JUNIT_PATH": "reports/unreal/junit.xml",
    }
    assert launcher.validated.request.declared_outputs == {
        "output.primary": "reports/unreal/results.json",
        "output.summary": "reports/unreal/summary.json",
        "output.logs.junit": "reports/unreal/junit.xml",
    }

@pytest.mark.asyncio
async def test_unreal_run_tests_handler_enforces_curated_profile() -> None:
    registry = RunnerProfileRegistry(
        [RunnerProfile.model_validate(_profile_payload("unreal-5_3-linux"))],
        workspace_root=WORKSPACE_ROOT,
    )
    launcher = _FakeLauncher()
    handler = build_workload_tool_handler(
        tool_name="unreal.run_tests",
        registry=registry,
        launcher=launcher,
    )

    await handler(
        {
            "profileId": "unapproved-profile",
            "repoDir": "/work/agent_jobs/task-1/repo",
            "artifactsDir": "/work/agent_jobs/task-1/artifacts/unreal",
            "projectPath": "Game/Game.uproject",
        },
        {"workflow_id": "task-1", "node_id": "unreal-tests"},
    )

    assert launcher.validated is not None
    assert launcher.validated.request.profile_id == "unreal-5_3-linux"

@pytest.mark.asyncio
async def test_unreal_run_tests_handler_normalizes_report_paths() -> None:
    registry = RunnerProfileRegistry(
        [RunnerProfile.model_validate(_profile_payload("unreal-5_3-linux"))],
        workspace_root=WORKSPACE_ROOT,
    )
    launcher = _FakeLauncher()
    handler = build_workload_tool_handler(
        tool_name="unreal.run_tests",
        registry=registry,
        launcher=launcher,
    )

    await handler(
        {
            "repoDir": "/work/agent_jobs/task-1/repo",
            "artifactsDir": "/work/agent_jobs/task-1/artifacts/unreal",
            "projectPath": "Game/Game.uproject",
            "reportPaths": {
                "primary": r"reports\unreal\.\results.json",
                "summary": "reports/unreal/../unreal/summary.json",
                "junit": r"reports\unreal\junit.xml",
            },
        },
        {"workflow_id": "task-1", "node_id": "unreal-tests"},
    )

    assert launcher.validated is not None
    assert launcher.validated.request.command[-6:] == (
        "--report",
        "reports/unreal/results.json",
        "--summary",
        "reports/unreal/summary.json",
        "--junit",
        "reports/unreal/junit.xml",
    )
    assert launcher.validated.request.env_overrides["UE_JUNIT_PATH"] == (
        "reports/unreal/junit.xml"
    )
    assert launcher.validated.request.declared_outputs == {
        "output.primary": "reports/unreal/results.json",
        "output.summary": "reports/unreal/summary.json",
        "output.logs.junit": "reports/unreal/junit.xml",
    }

@pytest.mark.asyncio
async def test_unreal_run_tests_handler_uses_default_report_outputs() -> None:
    registry = RunnerProfileRegistry(
        [RunnerProfile.model_validate(_profile_payload("unreal-5_3-linux"))],
        workspace_root=WORKSPACE_ROOT,
    )
    launcher = _FakeLauncher()
    handler = build_workload_tool_handler(
        tool_name="unreal.run_tests",
        registry=registry,
        launcher=launcher,
    )

    await handler(
        {
            "repoDir": "/work/agent_jobs/task-1/repo",
            "artifactsDir": "/work/agent_jobs/task-1/artifacts/unreal",
            "projectPath": "Game/Game.uproject",
        },
        {"workflow_id": "task-1", "node_id": "unreal-tests"},
    )

    assert launcher.validated is not None
    assert launcher.validated.request.declared_outputs == {
        "output.primary": "unreal/reports/results.json",
        "output.summary": "unreal/reports/summary.json",
        "output.logs.junit": "unreal/reports/junit.xml",
    }
    assert "--report" in launcher.validated.request.command

@pytest.mark.asyncio
async def test_unreal_run_tests_handler_rejects_report_paths_outside_artifacts() -> None:
    registry = RunnerProfileRegistry(
        [RunnerProfile.model_validate(_profile_payload("unreal-5_3-linux"))],
        workspace_root=WORKSPACE_ROOT,
    )
    launcher = _FakeLauncher()
    handler = build_workload_tool_handler(
        tool_name="unreal.run_tests",
        registry=registry,
        launcher=launcher,
    )

    with pytest.raises(SkillFailure) as exc_info:
        await handler(
            {
                "repoDir": "/work/agent_jobs/task-1/repo",
                "artifactsDir": "/work/agent_jobs/task-1/artifacts/unreal",
                "projectPath": "Game/Game.uproject",
                "reportPaths": {"primary": "../escape.json"},
            },
            {"workflow_id": "task-1", "node_id": "unreal-tests"},
        )

    assert exc_info.value.error_code == "INVALID_INPUT"
    assert "reportPaths" in exc_info.value.message
    assert launcher.validated is None

@pytest.mark.asyncio
async def test_unreal_run_tests_handler_rejects_non_mapping_env_overrides() -> None:
    registry = RunnerProfileRegistry(
        [RunnerProfile.model_validate(_profile_payload("unreal-5_3-linux"))],
        workspace_root=WORKSPACE_ROOT,
    )
    launcher = _FakeLauncher()
    handler = build_workload_tool_handler(
        tool_name="unreal.run_tests",
        registry=registry,
        launcher=launcher,
    )

    with pytest.raises(SkillFailure) as exc_info:
        await handler(
            {
                "repoDir": "/work/agent_jobs/task-1/repo",
                "artifactsDir": "/work/agent_jobs/task-1/artifacts/unreal",
                "projectPath": "Game/Game.uproject",
                "envOverrides": 1,
            },
            {"workflow_id": "task-1", "node_id": "unreal-tests"},
        )

    assert exc_info.value.error_code == "INVALID_INPUT"
    assert "envOverrides" in exc_info.value.message
    assert launcher.validated is None

@pytest.mark.asyncio
async def test_unreal_run_tests_handler_requires_project_path_before_launch() -> None:
    registry = RunnerProfileRegistry(
        [RunnerProfile.model_validate(_profile_payload("unreal-5_3-linux"))],
        workspace_root=WORKSPACE_ROOT,
    )
    launcher = _FakeLauncher()
    handler = build_workload_tool_handler(
        tool_name="unreal.run_tests",
        registry=registry,
        launcher=launcher,
    )

    with pytest.raises(SkillFailure) as exc_info:
        await handler(
            {
                "repoDir": "/work/agent_jobs/task-1/repo",
                "artifactsDir": "/work/agent_jobs/task-1/artifacts/unreal",
            },
            {"workflow_id": "task-1", "node_id": "unreal-tests"},
        )

    assert exc_info.value.error_code == "INVALID_INPUT"
    assert "projectPath" in exc_info.value.message
    assert launcher.validated is None

class _RecordingDispatcher:
    def __init__(self) -> None:
        self.skills: list[tuple[str, str]] = []

    def register_skill(self, *, skill_name: str, version: str, handler: Any) -> None:
        self.skills.append((skill_name, version))

def test_unrestricted_tool_definitions_are_registered_as_docker_workloads() -> None:
    for tool_name in (CONTAINER_RUN_CONTAINER_TOOL, CONTAINER_RUN_DOCKER_TOOL):
        definition = build_dood_tool_definition_payload(name=tool_name, version="1.0")

        assert definition["type"] == "skill"
        assert definition["executor"]["activity_type"] == "mm.tool.execute"
        assert definition["requirements"]["capabilities"] == ["docker_workload"]

def test_register_workload_tool_handlers_omits_all_dood_tools_when_disabled() -> None:
    dispatcher = _RecordingDispatcher()

    register_workload_tool_handlers(
        dispatcher,
        registry=RunnerProfileRegistry.empty(workspace_root=WORKSPACE_ROOT),
        launcher=_FailingLauncher(),
        workflow_docker_mode="disabled",
    )

    assert dispatcher.skills == []

def test_register_workload_tool_handlers_exposes_only_curated_tools_in_profiles_mode() -> None:
    dispatcher = _RecordingDispatcher()

    register_workload_tool_handlers(
        dispatcher,
        registry=RunnerProfileRegistry.empty(workspace_root=WORKSPACE_ROOT),
        launcher=_FailingLauncher(),
        workflow_docker_mode="profiles",
    )

    registered = {name for name, _version in dispatcher.skills}
    assert CONTAINER_RUN_CONTAINER_TOOL not in registered
    assert CONTAINER_RUN_DOCKER_TOOL not in registered
    assert INTEGRATION_CI_TOOL in registered

def test_register_workload_tool_handlers_exposes_unrestricted_tools_only_in_unrestricted_mode() -> None:
    dispatcher = _RecordingDispatcher()

    register_workload_tool_handlers(
        dispatcher,
        registry=RunnerProfileRegistry.empty(workspace_root=WORKSPACE_ROOT),
        launcher=_FailingLauncher(),
        workflow_docker_mode="unrestricted",
    )

    registered = {name for name, _version in dispatcher.skills}
    assert registered == DOOD_TOOL_NAMES

@pytest.mark.asyncio
async def test_profiles_mode_denies_direct_unrestricted_container_invocation() -> None:
    handler = build_workload_tool_handler(
        tool_name=CONTAINER_RUN_CONTAINER_TOOL,
        registry=_FailingRegistry(),
        launcher=_FailingLauncher(),
        workflow_docker_mode="profiles",
    )

    with pytest.raises(SkillFailure) as exc_info:
        await handler(
            {
                "image": "ghcr.io/example/runtime:1.2.3",
                "repoDir": "/work/agent_jobs/task-1/repo",
                "artifactsDir": "/work/agent_jobs/task-1/artifacts/step-test",
                "scratchDir": "/work/agent_jobs/task-1/scratch",
                "command": ["pytest", "-q"],
            },
            {"workflow_id": "task-1", "node_id": "step-test"},
        )

    assert exc_info.value.error_code == "PERMISSION_DENIED"
    assert exc_info.value.details["reason"] == "docker_workflow_mode_forbidden"
    assert exc_info.value.details["workflowDockerMode"] == "profiles"

@pytest.mark.asyncio
async def test_unrestricted_mode_allows_unrestricted_docker_handler() -> None:
    launcher = _FakeLauncher()
    handler = build_workload_tool_handler(
        tool_name=CONTAINER_RUN_DOCKER_TOOL,
        registry=RunnerProfileRegistry.empty(workspace_root=WORKSPACE_ROOT),
        launcher=launcher,
        workflow_docker_mode="unrestricted",
    )

    result = await handler(
        {
            "repoDir": "/work/agent_jobs/task-1/repo",
            "artifactsDir": "/work/agent_jobs/task-1/artifacts/step-test",
            "command": ["docker", "ps"],
        },
        {"workflow_id": "task-1", "node_id": "step-test"},
    )

    assert result.status == "COMPLETED"
    assert launcher.validated.profile is None
    assert launcher.validated.request.tool_name == CONTAINER_RUN_DOCKER_TOOL
    assert result.outputs["workloadMetadata"]["workflowDockerMode"] == "unrestricted"
