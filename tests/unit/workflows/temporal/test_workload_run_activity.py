from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

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

    async def run(self, validated: Any) -> WorkloadResult:
        self.validated = validated
        return WorkloadResult(
            requestId=validated.container_name,
            profileId=validated.profile.id,
            status="succeeded",
            labels=validated.ownership.labels,
            exitCode=0,
            metadata={"containerName": validated.container_name},
        )


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
async def test_workload_run_activity_requires_runtime_dependencies() -> None:
    activities = TemporalAgentRuntimeActivities()

    with pytest.raises(TemporalActivityRuntimeError, match="workload registry"):
        await activities.workload_run(_request_payload())
