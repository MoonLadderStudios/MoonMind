from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from temporalio import exceptions as temporal_exceptions

from moonmind.schemas.workload_models import RunnerProfile, WorkloadResult
from moonmind.workflows.temporal.activity_runtime import (
    TemporalActivityRuntimeError,
    TemporalAgentRuntimeActivities,
)
from moonmind.workloads.registry import RunnerProfileRegistry


WORKSPACE_ROOT = Path("/work/agent_jobs")


def _profile_payload() -> dict[str, object]:
    return {
        "id": "local-python",
        "kind": "one_shot",
        "image": "python:3.12-slim",
        "workdir_template": "/work/agent_jobs/${task_run_id}/repo",
        "required_mounts": [
            {
                "type": "volume",
                "source": "agent_workspaces",
                "target": "/work/agent_jobs",
            }
        ],
        "env_allowlist": ["CI"],
        "network_policy": "none",
        "timeout_seconds": 300,
        "max_timeout_seconds": 600,
    }


def _helper_profile_payload() -> dict[str, object]:
    return {
        "id": "redis-helper",
        "kind": "bounded_service",
        "image": "redis:7.2-alpine",
        "workdir_template": "/work/agent_jobs/${task_run_id}/repo",
        "required_mounts": [
            {
                "type": "volume",
                "source": "agent_workspaces",
                "target": "/work/agent_jobs",
            }
        ],
        "env_allowlist": ["CI"],
        "network_policy": "bridge",
        "timeout_seconds": 60,
        "max_timeout_seconds": 120,
        "helper_ttl_seconds": 300,
        "max_helper_ttl_seconds": 900,
        "readiness_probe": {
            "type": "exec",
            "command": ["redis-cli", "ping"],
            "interval_seconds": 0,
            "timeout_seconds": 1,
            "retries": 3,
        },
        "cleanup": {
            "remove_container_on_exit": True,
            "kill_grace_seconds": 3,
        },
    }


def _request_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "profileId": "local-python",
        "taskRunId": "task-1",
        "stepId": "step-test",
        "attempt": 1,
        "toolName": "container.run_workload",
        "repoDir": "/work/agent_jobs/task-1/repo",
        "artifactsDir": "/work/agent_jobs/task-1/artifacts/step-test",
        "command": ["python", "-V"],
        "envOverrides": {"CI": "1"},
    }
    payload.update(overrides)
    return payload


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
        return WorkloadResult(
            requestId=validated.container_name,
            profileId=validated.profile.id,
            status="succeeded",
            labels=validated.ownership.labels,
            exitCode=0,
            metadata={
                "containerName": validated.container_name,
                "workload": {
                    "taskRunId": validated.request.task_run_id,
                    "stepId": validated.request.step_id,
                    "attempt": validated.request.attempt,
                    "toolName": validated.request.tool_name,
                    "profileId": validated.profile.id,
                    "sessionContext": self._session_context(validated),
                },
                "artifactPublication": {"status": "complete"},
            },
        )

    async def start_helper(self, validated: Any) -> WorkloadResult:
        self.validated = validated
        return WorkloadResult(
            requestId=validated.container_name,
            profileId=validated.profile.id,
            status="ready",
            labels=validated.ownership.labels,
            metadata={
                "helper": {
                    "containerName": validated.container_name,
                    "status": "ready",
                    "readiness": {"status": "ready", "attempts": 1},
                },
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
        return WorkloadResult(
            requestId=validated.container_name,
            profileId=validated.profile.id,
            status="stopped",
            labels=validated.ownership.labels,
            metadata={
                "helper": {
                    "containerName": validated.container_name,
                    "status": "stopped",
                    "teardown": {"status": "complete", "reason": reason},
                },
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


@pytest.mark.asyncio
async def test_workload_run_activity_validates_request_and_calls_launcher() -> None:
    registry = RunnerProfileRegistry(
        [RunnerProfile.model_validate(_profile_payload())],
        workspace_root=WORKSPACE_ROOT,
    )
    launcher = _FakeLauncher()
    activities = TemporalAgentRuntimeActivities(
        workload_registry=registry,
        workload_launcher=launcher,
    )

    result = await activities.workload_run({"request": _request_payload()})

    assert launcher.validated is not None
    assert launcher.validated.container_name == "mm-workload-task-1-step-test-1"
    assert result["requestId"] == "mm-workload-task-1-step-test-1"
    assert result["profileId"] == "local-python"
    assert result["status"] == "succeeded"
    assert result["labels"]["moonmind.kind"] == "workload"


@pytest.mark.asyncio
async def test_workload_run_activity_preserves_session_context_as_workload_metadata() -> None:
    registry = RunnerProfileRegistry(
        [RunnerProfile.model_validate(_profile_payload())],
        workspace_root=WORKSPACE_ROOT,
    )
    launcher = _FakeLauncher()
    activities = TemporalAgentRuntimeActivities(
        workload_registry=registry,
        workload_launcher=launcher,
    )

    result = await activities.workload_run(
        {
            "request": _request_payload(
                sessionId="session-1",
                sessionEpoch=3,
                sourceTurnId="turn-7",
            )
        }
    )

    assert result["metadata"]["workload"]["sessionContext"] == {
        "sessionId": "session-1",
        "sessionEpoch": 3,
        "sourceTurnId": "turn-7",
    }
    assert "session.summary" not in (result.get("outputRefs") or {})


@pytest.mark.asyncio
async def test_workload_run_activity_starts_helper_by_tool_name() -> None:
    registry = RunnerProfileRegistry(
        [RunnerProfile.model_validate(_helper_profile_payload())],
        workspace_root=WORKSPACE_ROOT,
    )
    launcher = _FakeLauncher()
    activities = TemporalAgentRuntimeActivities(
        workload_registry=registry,
        workload_launcher=launcher,
    )

    result = await activities.workload_run(
        {
            "request": _request_payload(
                profileId="redis-helper",
                toolName="container.start_helper",
                repoDir="/work/agent_jobs/task-1/repo",
                artifactsDir="/work/agent_jobs/task-1/artifacts/step-service",
                command=["--appendonly", "no"],
                ttlSeconds=300,
            )
        }
    )

    assert launcher.validated is not None
    assert launcher.validated.container_name == "mm-helper-task-1-step-test-1"
    assert result["status"] == "ready"
    assert result["labels"]["moonmind.kind"] == "bounded_service"
    assert result["metadata"]["helper"]["readiness"]["status"] == "ready"


@pytest.mark.asyncio
async def test_workload_run_activity_stops_helper_by_tool_name() -> None:
    registry = RunnerProfileRegistry(
        [RunnerProfile.model_validate(_helper_profile_payload())],
        workspace_root=WORKSPACE_ROOT,
    )
    launcher = _FakeLauncher()
    activities = TemporalAgentRuntimeActivities(
        workload_registry=registry,
        workload_launcher=launcher,
    )

    result = await activities.workload_run(
        {
            "request": _request_payload(
                profileId="redis-helper",
                toolName="container.stop_helper",
                repoDir="/work/agent_jobs/task-1/repo",
                artifactsDir="/work/agent_jobs/task-1/artifacts/step-service",
                command=["stop"],
                ttlSeconds=300,
                reason="owner_task_canceled",
            )
        }
    )

    assert launcher.validated is not None
    assert launcher.reason == "owner_task_canceled"
    assert result["status"] == "stopped"
    assert result["labels"]["moonmind.kind"] == "bounded_service"
    assert result["metadata"]["helper"]["teardown"]["reason"] == "owner_task_canceled"


@pytest.mark.asyncio
async def test_workload_run_activity_requires_runtime_dependencies() -> None:
    activities = TemporalAgentRuntimeActivities()

    with pytest.raises(TemporalActivityRuntimeError, match="workload registry"):
        await activities.workload_run(_request_payload())


@pytest.mark.asyncio
async def test_workload_run_activity_denies_when_workflow_docker_disabled() -> None:
    activities = TemporalAgentRuntimeActivities(
        workload_registry=_FailingRegistry(),
        workload_launcher=_FailingLauncher(),
        workflow_docker_mode="disabled",
    )

    with pytest.raises(temporal_exceptions.ApplicationError) as exc_info:
        await activities.workload_run({"request": _request_payload()})

    message = str(exc_info.value)
    assert "docker_workflows_disabled" in message
    assert "policy_denied" in message
    assert exc_info.value.type == "docker_workflows_disabled"
    assert exc_info.value.non_retryable is True


@pytest.mark.asyncio
async def test_workload_run_activity_denies_unrestricted_tool_when_mode_is_profiles() -> None:
    activities = TemporalAgentRuntimeActivities(
        workload_registry=_FailingRegistry(),
        workload_launcher=_FailingLauncher(),
        workflow_docker_mode="profiles",
    )

    with pytest.raises(temporal_exceptions.ApplicationError) as exc_info:
        await activities.workload_run(
            {
                "request": {
                    "taskRunId": "task-1",
                    "stepId": "step-test",
                    "attempt": 1,
                    "toolName": "container.run_docker",
                    "repoDir": "/work/agent_jobs/task-1/repo",
                    "artifactsDir": "/work/agent_jobs/task-1/artifacts/step-test",
                    "command": ["docker", "ps"],
                }
            }
        )

    assert exc_info.value.type == "docker_workflow_mode_forbidden"
    assert "profiles" in str(exc_info.value)
