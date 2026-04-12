from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from moonmind.schemas.workload_models import (
    WorkloadRequest,
    WorkloadResult,
    parse_size_bytes,
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


def test_size_parser_uses_docker_binary_units() -> None:
    assert parse_size_bytes("512m") == 512 * 1024**2
    assert parse_size_bytes("2gb") == 2 * 1024**3
    assert parse_size_bytes("1ti") == 1024**4

    with pytest.raises(ValueError, match="invalid size"):
        parse_size_bytes("1p")


def test_request_rejects_empty_command() -> None:
    with pytest.raises(ValidationError, match="command"):
        _request(command=[])


def test_registry_rejects_unknown_profile(tmp_path: Path) -> None:
    registry = _registry(tmp_path)

    with pytest.raises(WorkloadPolicyError, match="unknown runner profile") as exc_info:
        registry.validate_request(_request(profileId="missing-profile"))
    assert exc_info.value.reason == "unknown_profile"
    assert exc_info.value.details == {"profileId": "missing-profile"}


def test_registry_rejects_env_key_outside_profile_allowlist(tmp_path: Path) -> None:
    registry = _registry(tmp_path)

    with pytest.raises(WorkloadPolicyError, match="environment override") as exc_info:
        registry.validate_request(_request(envOverrides={"SECRET_TOKEN": "raw"}))
    assert exc_info.value.reason == "disallowed_env_key"
    assert exc_info.value.details == {
        "envKey": "SECRET_TOKEN",
        "profileId": "local-python",
    }


def test_registry_rejects_workspace_paths_outside_workspace_root(tmp_path: Path) -> None:
    registry = _registry(tmp_path)

    with pytest.raises(WorkloadPolicyError, match="repoDir") as exc_info:
        registry.validate_request(_request(repoDir="/tmp/repo"))
    assert exc_info.value.reason == "invalid_request"
    assert exc_info.value.details == {
        "field": "repoDir",
        "workspaceRoot": str(WORKSPACE_ROOT),
    }

    with pytest.raises(WorkloadPolicyError, match="artifactsDir") as exc_info:
        registry.validate_request(
            _request(artifactsDir="/work/agent_jobs/../outside/artifacts")
        )
    assert exc_info.value.reason == "invalid_request"
    assert exc_info.value.details == {
        "field": "artifactsDir",
        "workspaceRoot": str(WORKSPACE_ROOT),
    }


def test_registry_rejects_resource_overrides_above_profile_maximum(
    tmp_path: Path,
) -> None:
    registry = _registry(tmp_path)

    with pytest.raises(WorkloadPolicyError, match="memory") as exc_info:
        registry.validate_request(_request(resources={"memory": "8g"}))
    assert exc_info.value.reason == "resource_request_too_large"
    assert exc_info.value.details == {"resource": "memory", "profileId": "local-python"}

    with pytest.raises(WorkloadPolicyError, match="cpu"):
        registry.validate_request(_request(resources={"cpu": "8"}))


def test_registry_enforces_image_registry_allowlist(tmp_path: Path) -> None:
    registry_path = tmp_path / "profiles.json"
    registry_path.write_text(
        json.dumps({"profiles": [_profile_payload(image="python:3.12-slim")]}),
        encoding="utf-8",
    )

    with pytest.raises(WorkloadPolicyError, match="image registry") as exc_info:
        RunnerProfileRegistry.load_file(
            registry_path,
            workspace_root=WORKSPACE_ROOT,
            allowed_image_registries=("ghcr.io",),
        )

    assert exc_info.value.reason == "disallowed_image_registry"
    assert exc_info.value.details == {
        "profileId": "local-python",
        "imageRegistry": "docker.io",
    }


def test_registry_allows_profiles_from_approved_registry(tmp_path: Path) -> None:
    registry_path = tmp_path / "profiles.json"
    registry_path.write_text(
        json.dumps(
            {"profiles": [_profile_payload(image="ghcr.io/moonmind/python:3.12")]}
        ),
        encoding="utf-8",
    )

    registry = RunnerProfileRegistry.load_file(
        registry_path,
        workspace_root=WORKSPACE_ROOT,
        allowed_image_registries=("ghcr.io",),
    )

    assert registry.profile_ids == ("local-python",)


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
        (
            {
                "required_mounts": [
                    {
                        "type": "volume",
                        "source": "relative/cache",
                        "target": "/work/cache",
                    }
                ]
            },
            "mount source",
        ),
        (
            {
                "required_mounts": [
                    {
                        "type": "volume",
                        "source": "agent_workspaces",
                        "target": "/work/../etc",
                    }
                ]
            },
            "mount target",
        ),
        (
            {
                "required_mounts": [
                    {
                        "type": "volume",
                        "source": "codex_auth_volume",
                        "target": "/home/codex/.codex",
                    }
                ]
            },
            "auth volumes",
        ),
        ({"privileged": True}, "privileged"),
        ({"workdir_template": "/work/../tmp"}, "workdirTemplate"),
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


def test_registry_rejects_unsupported_registry_extension(tmp_path: Path) -> None:
    registry_path = tmp_path / "profiles.toml"
    registry_path.write_text("profiles = []", encoding="utf-8")

    with pytest.raises(WorkloadPolicyError, match=".json, .yaml, or .yml"):
        RunnerProfileRegistry.load_file(
            registry_path,
            workspace_root=WORKSPACE_ROOT,
        )


def test_registry_wraps_yaml_parse_errors(tmp_path: Path) -> None:
    registry_path = tmp_path / "profiles.yaml"
    registry_path.write_text("profiles: [", encoding="utf-8")

    with pytest.raises(
        WorkloadPolicyError,
        match="invalid runner profile registry YAML",
    ):
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


def test_workload_request_rejects_declared_output_paths_outside_artifacts_dir() -> None:
    with pytest.raises(ValueError, match="declaredOutputs"):
        _request(declaredOutputs={"output.primary": "../outside.txt"})

    with pytest.raises(ValueError, match="declaredOutputs"):
        _request(declaredOutputs={"output.primary": "/tmp/outside.txt"})


def test_workload_request_rejects_session_continuity_declared_outputs() -> None:
    with pytest.raises(ValueError, match="session continuity"):
        _request(declaredOutputs={"session.summary": "summary.json"})


@pytest.mark.parametrize("artifact_class", ["runtime.stdout", "output.logs"])
def test_workload_request_rejects_runtime_reserved_declared_outputs(
    artifact_class: str,
) -> None:
    with pytest.raises(ValueError, match="runtime artifact classes"):
        _request(declaredOutputs={artifact_class: "logs.txt"})
