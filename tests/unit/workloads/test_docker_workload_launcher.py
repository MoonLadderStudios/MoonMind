from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from moonmind.schemas.workload_models import WorkloadRequest
from moonmind.workloads.docker_launcher import (
    DockerContainerJanitor,
    DockerWorkloadLauncher,
)
from moonmind.workloads.registry import RunnerProfileRegistry


WORKSPACE_ROOT = Path("/work/agent_jobs")


def _profile_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": "local-python",
        "kind": "one_shot",
        "image": "python:3.12-slim",
        "entrypoint": ["/bin/bash"],
        "command_wrapper": ["-lc"],
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
                "source": "unreal_ccache_volume",
                "target": "/work/cache/ccache",
            },
            {
                "type": "volume",
                "source": "unreal_ubt_volume",
                "target": "/work/cache/ubt",
                "read_only": True,
            },
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
            "kill_grace_seconds": 3,
        },
        "device_policy": {"mode": "none"},
    }
    payload.update(overrides)
    return payload


def _registry(tmp_path: Path) -> RunnerProfileRegistry:
    registry_path = tmp_path / "profiles.json"
    registry_path.write_text(
        json.dumps({"profiles": [_profile_payload()]}),
        encoding="utf-8",
    )
    return RunnerProfileRegistry.load_file(
        registry_path,
        workspace_root=WORKSPACE_ROOT,
    )


def _validated_request(tmp_path: Path, **overrides: object):
    payload: dict[str, object] = {
        "profileId": "local-python",
        "taskRunId": "task-1",
        "stepId": "step-test",
        "attempt": 2,
        "toolName": "container.run_workload",
        "repoDir": "/work/agent_jobs/task-1/repo",
        "artifactsDir": "/work/agent_jobs/task-1/artifacts/step-test",
        "command": ["pytest -q"],
        "envOverrides": {"CI": "1"},
        "timeoutSeconds": 120,
        "resources": {"cpu": "3", "memory": "3g", "shmSize": "768m"},
    }
    payload.update(overrides)
    return _registry(tmp_path).validate_request(WorkloadRequest.model_validate(payload))


class _Process:
    def __init__(
        self,
        *,
        returncode: int = 0,
        stdout: bytes = b"ok\n",
        stderr: bytes = b"",
        never_complete: bool = False,
    ) -> None:
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr
        self._never_complete = never_complete
        self.killed = False

    async def communicate(self) -> tuple[bytes, bytes]:
        if self._never_complete and not self.killed:
            await asyncio.sleep(3600)
        return self._stdout, self._stderr

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


@pytest.mark.asyncio
async def test_launcher_builds_deterministic_docker_run_and_cleans_up(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[list[str]] = []

    async def _fake_create_subprocess_exec(*args: str, **_kwargs: Any) -> _Process:
        created.append(list(args))
        if args[1] == "run":
            return _Process(returncode=0, stdout=b"tests passed\n")
        return _Process(returncode=0)

    monkeypatch.setattr(
        "moonmind.workloads.docker_launcher.asyncio.create_subprocess_exec",
        _fake_create_subprocess_exec,
    )
    launcher = DockerWorkloadLauncher(docker_host="tcp://docker-proxy:2375")

    result = await launcher.run(_validated_request(tmp_path))

    run_args = created[0]
    assert run_args[:4] == ["docker", "run", "--name", "mm-workload-task-1-step-test-2"]
    assert "--workdir" in run_args
    assert run_args[run_args.index("--workdir") + 1] == "/work/agent_jobs/task-1/repo"
    assert "--network" in run_args
    assert run_args[run_args.index("--network") + 1] == "none"
    assert "--cpus" in run_args
    assert run_args[run_args.index("--cpus") + 1] == "3"
    assert "--memory" in run_args
    assert run_args[run_args.index("--memory") + 1] == "3g"
    assert "--shm-size" in run_args
    assert run_args[run_args.index("--shm-size") + 1] == "768m"
    assert "--env" in run_args
    assert "CI=1" in run_args
    assert "--entrypoint" in run_args
    assert run_args[run_args.index("--entrypoint") + 1] == "/bin/bash"
    assert "type=volume,source=agent_workspaces,target=/work/agent_jobs" in run_args
    assert "type=volume,source=unreal_ccache_volume,target=/work/cache/ccache" in run_args
    assert (
        "type=volume,source=unreal_ubt_volume,target=/work/cache/ubt,readonly"
        in run_args
    )
    assert "moonmind.kind=workload" in run_args
    assert "moonmind.workload_profile=local-python" in run_args
    assert run_args[-3:] == ["python:3.12-slim", "-lc", "pytest -q"]
    assert created[-1] == ["docker", "rm", "-f", "mm-workload-task-1-step-test-2"]
    assert result.status == "succeeded"
    assert result.exit_code == 0
    assert result.metadata["containerName"] == "mm-workload-task-1-step-test-2"
    assert result.metadata["stdout"] == "tests passed\n"


@pytest.mark.asyncio
async def test_launcher_timeout_stops_kills_and_removes_container(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[list[str]] = []

    async def _fake_create_subprocess_exec(*args: str, **_kwargs: Any) -> _Process:
        created.append(list(args))
        if args[1] == "run":
            return _Process(never_complete=True)
        return _Process(returncode=0)

    monkeypatch.setattr(
        "moonmind.workloads.docker_launcher.asyncio.create_subprocess_exec",
        _fake_create_subprocess_exec,
    )
    launcher = DockerWorkloadLauncher()

    result = await launcher.run(
        _validated_request(tmp_path, timeoutSeconds=1),
        timeout_seconds=0.01,
    )

    assert result.status == "timed_out"
    assert result.timeout_reason == "workload exceeded timeoutSeconds"
    assert ["docker", "stop", "-t", "3", "mm-workload-task-1-step-test-2"] in created
    assert ["docker", "kill", "mm-workload-task-1-step-test-2"] in created
    assert ["docker", "rm", "-f", "mm-workload-task-1-step-test-2"] in created


@pytest.mark.asyncio
async def test_container_janitor_lists_orphans_by_labels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[list[str]] = []

    async def _fake_create_subprocess_exec(*args: str, **_kwargs: Any) -> _Process:
        created.append(list(args))
        return _Process(returncode=0, stdout=b"abc123\nxyz789\n")

    monkeypatch.setattr(
        "moonmind.workloads.docker_launcher.asyncio.create_subprocess_exec",
        _fake_create_subprocess_exec,
    )

    janitor = DockerContainerJanitor()
    orphans = await janitor.find_by_labels(
        {
            "moonmind.kind": "workload",
            "moonmind.task_run_id": "task-1",
        }
    )

    assert orphans == ("abc123", "xyz789")
    assert created[0] == [
        "docker",
        "ps",
        "-a",
        "--filter",
        "label=moonmind.kind=workload",
        "--filter",
        "label=moonmind.task_run_id=task-1",
        "--format",
        "{{.ID}}",
    ]
