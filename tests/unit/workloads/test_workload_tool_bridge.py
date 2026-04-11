from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from moonmind.schemas.workload_models import RunnerProfile, WorkloadResult
from moonmind.workflows.skills.skill_plan_contracts import SkillFailure, SkillResult
from moonmind.workloads.registry import RunnerProfileRegistry
from moonmind.workloads.tool_bridge import (
    build_dood_tool_definition_payload,
    build_workload_tool_handler,
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
        "envAllowlist": ["CI"],
        "networkPolicy": "none",
        "timeoutSeconds": 300,
        "maxTimeoutSeconds": 600,
    }


class _FakeLauncher:
    def __init__(self, *, status: str = "succeeded") -> None:
        self.validated: Any | None = None
        self._status = status

    async def run(self, validated: Any) -> WorkloadResult:
        self.validated = validated
        return WorkloadResult(
            requestId=validated.container_name,
            profileId=validated.profile.id,
            status=self._status,
            labels=validated.ownership.labels,
            exitCode=0 if self._status == "succeeded" else 1,
            metadata={"containerName": validated.container_name},
        )


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
    )


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
