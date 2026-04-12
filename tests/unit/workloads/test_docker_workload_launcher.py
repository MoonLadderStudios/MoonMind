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
    DockerWorkloadLauncherError,
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
        self.returncode = None if never_complete else returncode
        self._stdout = stdout
        self._stderr = stderr
        self._never_complete = never_complete
        self.killed = False
        self.terminated = False
        self._closed = asyncio.Event()
        if not never_complete:
            self._closed.set()
        self.stdout = _Pipe(self, stdout)
        self.stderr = _Pipe(self, stderr)

    async def communicate(self) -> tuple[bytes, bytes]:
        if self._never_complete and not self.killed:
            await self._closed.wait()
        return self._stdout, self._stderr

    async def wait(self) -> int:
        if self.returncode is None:
            await self._closed.wait()
        return int(self.returncode or 0)

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15
        self._closed.set()

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9
        self._closed.set()


class _Pipe:
    def __init__(self, process: _Process, data: bytes) -> None:
        self._process = process
        self._data = bytearray(data)

    async def read(self, size: int = -1) -> bytes:
        if not self._data:
            await self._process._closed.wait()
            return b""
        if size is None or size < 0:
            size = len(self._data)
        chunk = bytes(self._data[:size])
        del self._data[:size]
        return chunk


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
    assert result.metadata["artifactsDir"] == "/work/agent_jobs/task-1/artifacts/step-test"
    assert result.metadata["stdout"] == "tests passed\n"


def test_launcher_wraps_multi_part_shell_command_as_single_arg(
    tmp_path: Path,
) -> None:
    run_args = DockerWorkloadLauncher().build_run_args(
        _validated_request(tmp_path, command=["python", "-V"])
    )

    assert run_args[-3:] == ["python:3.12-slim", "-lc", "python -V"]


def test_launcher_rejects_artifacts_dir_outside_profile_mount(
    tmp_path: Path,
) -> None:
    registry_path = tmp_path / "profiles.json"
    registry_path.write_text(
        json.dumps(
            {
                "profiles": [
                    _profile_payload(
                        required_mounts=[
                            {
                                "type": "volume",
                                "source": "agent_workspaces",
                                "target": "/work/agent_jobs/task-1/repo",
                            }
                        ],
                        optional_mounts=[],
                    )
                ]
            }
        ),
        encoding="utf-8",
    )
    registry = RunnerProfileRegistry.load_file(
        registry_path,
        workspace_root=WORKSPACE_ROOT,
    )
    validated = registry.validate_request(
        WorkloadRequest.model_validate(
            {
                "profileId": "local-python",
                "taskRunId": "task-1",
                "stepId": "step-test",
                "attempt": 2,
                "toolName": "container.run_workload",
                "repoDir": "/work/agent_jobs/task-1/repo",
                "artifactsDir": "/work/agent_jobs/task-1/artifacts/step-test",
                "command": ["pytest -q"],
            }
        )
    )

    with pytest.raises(DockerWorkloadLauncherError, match="artifactsDir"):
        DockerWorkloadLauncher().build_run_args(validated)


@pytest.mark.asyncio
async def test_launcher_removes_container_after_nonzero_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[list[str]] = []

    async def _fake_create_subprocess_exec(*args: str, **_kwargs: Any) -> _Process:
        created.append(list(args))
        if args[1] == "run":
            return _Process(returncode=7, stdout=b"", stderr=b"failed\n")
        return _Process(returncode=0)

    monkeypatch.setattr(
        "moonmind.workloads.docker_launcher.asyncio.create_subprocess_exec",
        _fake_create_subprocess_exec,
    )

    result = await DockerWorkloadLauncher().run(_validated_request(tmp_path))

    assert result.status == "failed"
    assert result.exit_code == 7
    assert result.metadata["stderr"] == "failed\n"
    assert created[-1] == ["docker", "rm", "-f", "mm-workload-task-1-step-test-2"]


@pytest.mark.asyncio
async def test_launcher_publishes_runtime_artifacts_and_diagnostics_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact_dir = Path("/work/agent_jobs/task-phase4/artifacts/step-test")
    if artifact_dir.exists():
        for child in artifact_dir.rglob("*"):
            if child.is_file():
                child.unlink()
    artifact_dir.mkdir(parents=True, exist_ok=True)

    async def _fake_create_subprocess_exec(*args: str, **_kwargs: Any) -> _Process:
        if args[1] == "run":
            return _Process(
                returncode=0,
                stdout=b"runtime stdout\n",
                stderr=b"runtime stderr\n",
            )
        return _Process(returncode=0)

    monkeypatch.setattr(
        "moonmind.workloads.docker_launcher.asyncio.create_subprocess_exec",
        _fake_create_subprocess_exec,
    )

    result = await DockerWorkloadLauncher().run(
        _validated_request(
            tmp_path,
            taskRunId="task-phase4",
            artifactsDir=str(artifact_dir),
        )
    )

    stdout_path = Path(result.stdout_ref or "")
    stderr_path = Path(result.stderr_ref or "")
    diagnostics_path = Path(result.diagnostics_ref or "")
    assert stdout_path.read_text(encoding="utf-8") == "runtime stdout\n"
    assert stderr_path.read_text(encoding="utf-8") == "runtime stderr\n"
    diagnostics = json.loads(diagnostics_path.read_text(encoding="utf-8"))
    assert diagnostics["status"] == "succeeded"
    assert diagnostics["profileId"] == "local-python"
    assert diagnostics["imageRef"] == "python:3.12-slim"
    assert diagnostics["exitCode"] == 0
    assert diagnostics["durationSeconds"] == result.duration_seconds
    assert diagnostics["stepId"] == "step-test"
    assert diagnostics["taskRunId"] == "task-phase4"
    assert diagnostics["sessionContext"] is None
    assert result.output_refs["runtime.stdout"] == result.stdout_ref
    assert result.output_refs["runtime.stderr"] == result.stderr_ref
    assert result.output_refs["runtime.diagnostics"] == result.diagnostics_ref
    assert result.metadata["workload"]["stepId"] == "step-test"


@pytest.mark.asyncio
async def test_launcher_publishes_failure_artifacts_with_session_association(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact_dir = Path("/work/agent_jobs/task-phase4-session/artifacts/step-test")
    artifact_dir.mkdir(parents=True, exist_ok=True)

    async def _fake_create_subprocess_exec(*args: str, **_kwargs: Any) -> _Process:
        if args[1] == "run":
            return _Process(returncode=9, stdout=b"before failure\n", stderr=b"boom\n")
        return _Process(returncode=0)

    monkeypatch.setattr(
        "moonmind.workloads.docker_launcher.asyncio.create_subprocess_exec",
        _fake_create_subprocess_exec,
    )

    result = await DockerWorkloadLauncher().run(
        _validated_request(
            tmp_path,
            taskRunId="task-phase4-session",
            artifactsDir=str(artifact_dir),
            sessionId="session-1",
            sessionEpoch=4,
            sourceTurnId="turn-9",
        )
    )

    diagnostics = json.loads(Path(result.diagnostics_ref or "").read_text("utf-8"))
    assert result.status == "failed"
    assert Path(result.stdout_ref or "").read_text("utf-8") == "before failure\n"
    assert Path(result.stderr_ref or "").read_text("utf-8") == "boom\n"
    assert diagnostics["status"] == "failed"
    assert diagnostics["sessionContext"] == {
        "sessionId": "session-1",
        "sessionEpoch": 4,
        "sourceTurnId": "turn-9",
    }
    assert "session.summary" not in result.output_refs
    assert "session.step_checkpoint" not in result.output_refs


@pytest.mark.asyncio
async def test_launcher_links_declared_output_artifacts_under_artifacts_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact_dir = Path("/work/agent_jobs/task-phase4-outputs/artifacts/step-test")
    report_path = artifact_dir / "reports" / "result.xml"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("<testsuite />\n", encoding="utf-8")

    async def _fake_create_subprocess_exec(*args: str, **_kwargs: Any) -> _Process:
        if args[1] == "run":
            return _Process(returncode=0, stdout=b"done\n")
        return _Process(returncode=0)

    monkeypatch.setattr(
        "moonmind.workloads.docker_launcher.asyncio.create_subprocess_exec",
        _fake_create_subprocess_exec,
    )

    result = await DockerWorkloadLauncher().run(
        _validated_request(
            tmp_path,
            taskRunId="task-phase4-outputs",
            artifactsDir=str(artifact_dir),
            declaredOutputs={
                "test.report": "reports/result.xml",
                "output.summary": "summary.json",
            },
        )
    )

    diagnostics = json.loads(Path(result.diagnostics_ref or "").read_text("utf-8"))
    assert result.output_refs["test.report"] == str(report_path.resolve())
    assert "output.summary" not in result.output_refs
    assert diagnostics["declaredOutputRefs"] == {
        "test.report": str(report_path.resolve())
    }
    assert diagnostics["missingDeclaredOutputs"] == {
        "output.summary": "summary.json"
    }


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
async def test_launcher_cancel_stops_kills_removes_and_propagates_cancel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[list[str]] = []
    run_process: _Process | None = None

    async def _fake_create_subprocess_exec(*args: str, **_kwargs: Any) -> _Process:
        nonlocal run_process
        created.append(list(args))
        if args[1] == "run":
            run_process = _Process(never_complete=True)
            return run_process
        return _Process(returncode=0)

    monkeypatch.setattr(
        "moonmind.workloads.docker_launcher.asyncio.create_subprocess_exec",
        _fake_create_subprocess_exec,
    )
    task = asyncio.create_task(DockerWorkloadLauncher().run(_validated_request(tmp_path)))
    await asyncio.sleep(0)

    task.cancel()
    done, pending = await asyncio.wait({task})

    assert done == {task}
    assert pending == set()
    assert task.cancelled()
    assert ["docker", "stop", "-t", "3", "mm-workload-task-1-step-test-2"] in created
    assert ["docker", "kill", "mm-workload-task-1-step-test-2"] in created
    assert ["docker", "rm", "-f", "mm-workload-task-1-step-test-2"] in created
    assert run_process is not None
    assert run_process.terminated


@pytest.mark.asyncio
async def test_launcher_captures_bounded_process_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    noisy_stdout = b"a" * 70_000 + b"tail\n"

    async def _fake_create_subprocess_exec(*args: str, **_kwargs: Any) -> _Process:
        if args[1] == "run":
            return _Process(returncode=0, stdout=noisy_stdout)
        return _Process(returncode=0)

    monkeypatch.setattr(
        "moonmind.workloads.docker_launcher.asyncio.create_subprocess_exec",
        _fake_create_subprocess_exec,
    )

    result = await DockerWorkloadLauncher().run(_validated_request(tmp_path))

    assert len(result.metadata["stdout"]) == 64_000
    assert result.metadata["stdout"].endswith("tail\n")


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
