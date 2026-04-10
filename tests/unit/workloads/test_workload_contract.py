from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from moonmind.schemas.workload_models import (
    WorkloadRequest,
    WorkloadResult,
)
from moonmind.workloads.registry import (
    RunnerProfileRegistry,
    WorkloadPolicyError,
)


WORKSPACE_ROOT = Path("/work/agent_jobs")


def _profile_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": "local-python",
        "kind": "one_shot",
        "image": "python:3.12-slim",
        "entrypoint": ["/bin/bash", "-lc"],
        "workdir_template": "/work/agent_jobs/${task_run_id}/repo",
        "required_mounts": [
            {
                "type": "volume",
                "source": "agent_workspaces",
                "target": "/work/agent_jobs",
            }
        ],
        "optional_mounts": [
            {
                "type": "volume",
                "source": "build_cache",
                "target": "/work/cache",
            }
        ],
        "env_allowlist": ["CI", "UE_PROJECT_PATH"],
        "network_policy": "none",
        "resources": {
            "cpu": "2",
            "memory": "2g",
            "shm_size": "512m",
            "max_cpu": "4",
            "max_memory": "4g",
            "max_shm_size": "1g",
        },
        "timeout_seconds": 300,
        "max_timeout_seconds": 600,
        "cleanup": {
            "remove_container_on_exit": True,
            "kill_grace_seconds": 30,
        },
        "device_policy": {"mode": "none"},
    }
    payload.update(overrides)
    return payload


def _registry(tmp_path: Path, *profiles: dict[str, object]) -> RunnerProfileRegistry:
    registry_path = tmp_path / "profiles.json"
    registry_path.write_text(
        json.dumps({"profiles": list(profiles or [_profile_payload()])}),
        encoding="utf-8",
    )
    return RunnerProfileRegistry.load_file(
        registry_path,
        workspace_root=WORKSPACE_ROOT,
    )


def _request(**overrides: object) -> WorkloadRequest:
    payload: dict[str, object] = {
        "profileId": "local-python",
        "taskRunId": "task-1",
        "stepId": "step-test",
        "attempt": 1,
        "toolName": "container.run_workload",
        "repoDir": "/work/agent_jobs/task-1/repo",
        "artifactsDir": "/work/agent_jobs/task-1/artifacts/step-test",
        "command": ["pytest", "-q"],
        "envOverrides": {"CI": "1"},
        "timeoutSeconds": 300,
        "resources": {"cpu": "2", "memory": "2g"},
        "sessionId": "session-1",
        "sessionEpoch": 2,
        "sourceTurnId": "turn-1",
    }
    payload.update(overrides)
    return WorkloadRequest.model_validate(payload)


def test_registry_validates_request_and_derives_required_labels(tmp_path: Path) -> None:
    registry = _registry(tmp_path)

    validated = registry.validate_request(_request())

    assert validated.profile.id == "local-python"
    assert validated.ownership.labels == {
        "moonmind.kind": "workload",
        "moonmind.task_run_id": "task-1",
        "moonmind.step_id": "step-test",
        "moonmind.attempt": "1",
        "moonmind.tool_name": "container.run_workload",
        "moonmind.workload_profile": "local-python",
        "moonmind.session_id": "session-1",
        "moonmind.session_epoch": "2",
    }
    assert validated.container_name == "mm-workload-task-1-step-test-1"


def test_registry_loads_yaml_profiles(tmp_path: Path) -> None:
    registry_path = tmp_path / "profiles.yaml"
    registry_path.write_text(
        """
profiles:
  - id: local-python
    kind: one_shot
    image: python:3.12-slim
    entrypoint:
      - /bin/bash
      - -lc
    workdir_template: /work/agent_jobs/${task_run_id}/repo
    required_mounts:
      - type: volume
        source: agent_workspaces
        target: /work/agent_jobs
    env_allowlist:
      - CI
    network_policy: none
    resources:
      cpu: "2"
      memory: 2g
      max_cpu: "4"
      max_memory: 4g
    timeout_seconds: 300
    max_timeout_seconds: 600
""",
        encoding="utf-8",
    )

    registry = RunnerProfileRegistry.load_file(
        registry_path,
        workspace_root=WORKSPACE_ROOT,
    )

    assert registry.profile_ids == ("local-python",)


def test_registry_loads_profile_mapping_keyed_by_profile_id(tmp_path: Path) -> None:
    profile = _profile_payload()
    profile.pop("id")
    registry_path = tmp_path / "profiles.json"
    registry_path.write_text(
        json.dumps({"local-python": profile}),
        encoding="utf-8",
    )

    registry = RunnerProfileRegistry.load_file(
        registry_path,
        workspace_root=WORKSPACE_ROOT,
    )

    assert registry.profile_ids == ("local-python",)


def test_request_rejects_empty_command() -> None:
    with pytest.raises(ValidationError, match="command"):
        _request(command=[])


def test_registry_rejects_unknown_profile(tmp_path: Path) -> None:
    registry = _registry(tmp_path)

    with pytest.raises(WorkloadPolicyError, match="unknown runner profile"):
        registry.validate_request(_request(profileId="missing-profile"))


def test_registry_rejects_env_key_outside_profile_allowlist(tmp_path: Path) -> None:
    registry = _registry(tmp_path)

    with pytest.raises(WorkloadPolicyError, match="environment override"):
        registry.validate_request(_request(envOverrides={"SECRET_TOKEN": "raw"}))


def test_registry_rejects_workspace_paths_outside_workspace_root(tmp_path: Path) -> None:
    registry = _registry(tmp_path)

    with pytest.raises(WorkloadPolicyError, match="repoDir"):
        registry.validate_request(_request(repoDir="/tmp/repo"))

    with pytest.raises(WorkloadPolicyError, match="artifactsDir"):
        registry.validate_request(
            _request(artifactsDir="/work/agent_jobs/../outside/artifacts")
        )


def test_registry_rejects_resource_overrides_above_profile_maximum(
    tmp_path: Path,
) -> None:
    registry = _registry(tmp_path)

    with pytest.raises(WorkloadPolicyError, match="memory"):
        registry.validate_request(_request(resources={"memory": "8g"}))

    with pytest.raises(WorkloadPolicyError, match="cpu"):
        registry.validate_request(_request(resources={"cpu": "8"}))


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"image": "python:latest"}, "latest"),
        ({"image": "python"}, "tag or digest"),
        ({"network_policy": "host"}, "network"),
        ({"device_policy": {"mode": "gpu"}}, "device"),
        (
            {
                "required_mounts": [
                    {
                        "type": "bind",
                        "source": "/var/run/docker.sock",
                        "target": "/var/run/docker.sock",
                    }
                ]
            },
            "mount",
        ),
    ],
)
def test_registry_rejects_unsafe_profile_policy(
    tmp_path: Path,
    overrides: dict[str, object],
    message: str,
) -> None:
    registry_path = tmp_path / "profiles.json"
    registry_path.write_text(
        json.dumps({"profiles": [_profile_payload(**overrides)]}),
        encoding="utf-8",
    )

    with pytest.raises((ValidationError, WorkloadPolicyError), match=message):
        RunnerProfileRegistry.load_file(
            registry_path,
            workspace_root=WORKSPACE_ROOT,
        )


def test_registry_rejects_duplicate_profile_ids(tmp_path: Path) -> None:
    registry_path = tmp_path / "profiles.json"
    registry_path.write_text(
        json.dumps({"profiles": [_profile_payload(), _profile_payload()]}),
        encoding="utf-8",
    )

    with pytest.raises(WorkloadPolicyError, match="duplicate runner profile"):
        RunnerProfileRegistry.load_file(
            registry_path,
            workspace_root=WORKSPACE_ROOT,
        )


def test_missing_registry_returns_empty_fail_closed_registry(tmp_path: Path) -> None:
    registry = RunnerProfileRegistry.load_optional_file(
        tmp_path / "missing.json",
        workspace_root=WORKSPACE_ROOT,
    )

    assert registry.profile_ids == ()
    with pytest.raises(WorkloadPolicyError, match="unknown runner profile"):
        registry.validate_request(_request())


def test_workload_result_serializes_bounded_metadata() -> None:
    result = WorkloadResult.model_validate(
        {
            "requestId": "mm-workload-task-1-step-test-1",
            "profileId": "local-python",
            "status": "failed",
            "labels": {
                "moonmind.kind": "workload",
                "moonmind.task_run_id": "task-1",
            },
            "exitCode": 2,
            "durationSeconds": 12.5,
            "stdoutRef": "artifact:stdout",
            "stderrRef": "artifact:stderr",
            "diagnosticsRef": "artifact:diagnostics",
            "outputRefs": {"report": "artifact:report"},
            "metadata": {"summary": "pytest failed"},
        }
    )

    dumped = result.model_dump(by_alias=True)
    assert dumped["stdoutRef"] == "artifact:stdout"
    assert dumped["stderrRef"] == "artifact:stderr"
    assert dumped["metadata"] == {"summary": "pytest failed"}
