from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from moonmind.schemas.workload_models import WorkloadRequest
from moonmind.workloads.docker_launcher import (
    DockerContainerJanitor,
    DockerWorkloadConcurrencyLimiter,
    DockerWorkloadLauncher,
    DockerWorkloadLauncherError,
)
from moonmind.workloads.registry import RunnerProfileRegistry


WORKSPACE_ROOT = Path("/work/agent_jobs")


def _profile_payload(
    *,
    workspace_root: Path = WORKSPACE_ROOT,
    **overrides: object,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": "local-python",
        "kind": "one_shot",
        "image": "python:3.12-slim",
        "entrypoint": ["/bin/bash"],
        "command_wrapper": ["-lc"],
        "workdir_template": f"{workspace_root}/${{task_run_id}}/repo",
        "required_mounts": [
            {
                "type": "volume",
                "source": "agent_workspaces",
                "target": str(workspace_root),
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


def _registry(
    tmp_path: Path,
    *,
    workspace_root: Path = WORKSPACE_ROOT,
) -> RunnerProfileRegistry:
    registry_path = tmp_path / "profiles.json"
    registry_path.write_text(
        json.dumps({"profiles": [_profile_payload(workspace_root=workspace_root)]}),
        encoding="utf-8",
    )
    return RunnerProfileRegistry.load_file(
        registry_path,
        workspace_root=workspace_root,
    )


def _validated_request(
    tmp_path: Path,
    *,
    workspace_root: Path = WORKSPACE_ROOT,
    profiles: list[dict[str, object]] | None = None,
    **overrides: object,
):
    payload: dict[str, object] = {
        "profileId": "local-python",
        "taskRunId": "task-1",
        "stepId": "step-test",
        "attempt": 2,
        "toolName": "container.run_workload",
        "repoDir": f"{workspace_root}/task-1/repo",
        "artifactsDir": f"{workspace_root}/task-1/artifacts/step-test",
        "command": ["pytest -q"],
        "envOverrides": {"CI": "1"},
        "timeoutSeconds": 120,
        "resources": {"cpu": "3", "memory": "3g", "shmSize": "768m"},
    }
    payload.update(overrides)
    if profiles is None:
        registry = _registry(tmp_path, workspace_root=workspace_root)
    else:
        registry_path = tmp_path / "profiles.json"
        registry_path.write_text(
            json.dumps({"profiles": profiles}),
            encoding="utf-8",
        )
        registry = RunnerProfileRegistry.load_file(
            registry_path,
            workspace_root=workspace_root,
        )
    return registry.validate_request(WorkloadRequest.model_validate(payload))


def _helper_profile_payload(
    *,
    workspace_root: Path = WORKSPACE_ROOT,
    **overrides: object,
) -> dict[str, object]:
    payload = _profile_payload(
        workspace_root=workspace_root,
        id="redis-helper",
        kind="bounded_service",
        image="redis:7.2-alpine",
        entrypoint=["redis-server"],
        command_wrapper=[],
        env_allowlist=[],
        timeout_seconds=60,
        max_timeout_seconds=60,
        helper_ttl_seconds=300,
        max_helper_ttl_seconds=900,
        readiness_probe={
            "type": "exec",
            "command": ["redis-cli", "ping"],
            "interval_seconds": 1,
            "timeout_seconds": 2,
            "retries": 3,
        },
    )
    payload.update(overrides)
    return payload


def _validated_helper_request(
    tmp_path: Path,
    *,
    workspace_root: Path = WORKSPACE_ROOT,
    **overrides: object,
):
    payload: dict[str, object] = {
        "profileId": "redis-helper",
        "taskRunId": "task-helper",
        "stepId": "step-service",
        "attempt": 1,
        "toolName": "container.run_workload",
        "repoDir": f"{workspace_root}/task-helper/repo",
        "artifactsDir": f"{workspace_root}/task-helper/artifacts/step-service",
        "command": ["--appendonly", "no"],
        "envOverrides": {},
        "timeoutSeconds": 60,
        "resources": {"cpu": "2", "memory": "2g"},
        "ttlSeconds": 300,
    }
    payload.update(overrides)
    return _validated_request(
        tmp_path,
        workspace_root=workspace_root,
        profiles=[_helper_profile_payload(workspace_root=workspace_root)],
        **payload,
    )


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
    assert "--privileged=false" in run_args
    assert "--cap-drop" in run_args
    assert run_args[run_args.index("--cap-drop") + 1] == "ALL"
    assert "--security-opt" in run_args
    assert "no-new-privileges" in run_args
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
    assert any(label.startswith("moonmind.expires_at=") for label in run_args)
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


def test_unreal_profile_launch_args_include_cache_volumes_and_safe_posture() -> None:
    registry = RunnerProfileRegistry.load_file(
        Path("config/workloads/default-runner-profiles.yaml"),
        workspace_root=WORKSPACE_ROOT,
        allowed_image_registries=("ghcr.io",),
    )
    validated = registry.validate_request(
        WorkloadRequest.model_validate(
            {
                "profileId": "unreal-5_3-linux",
                "taskRunId": "task-1",
                "stepId": "unreal-tests",
                "attempt": 1,
                "toolName": "unreal.run_tests",
                "repoDir": "/work/agent_jobs/task-1/repo",
                "artifactsDir": "/work/agent_jobs/task-1/artifacts/unreal",
                "command": [
                    "unreal-run-tests",
                    "--project",
                    "Game/Game.uproject",
                    "--report",
                    "unreal/reports/results.json",
                ],
                "envOverrides": {
                    "UE_PROJECT_PATH": "Game/Game.uproject",
                    "UE_REPORT_PATH": "unreal/reports/results.json",
                    "CCACHE_DIR": "/work/.ccache",
                    "UBT_CACHE_DIR": "/work/ubt-cache",
                },
                "declaredOutputs": {
                    "output.primary": "unreal/reports/results.json",
                },
            }
        )
    )

    run_args = DockerWorkloadLauncher().build_run_args(validated)

    assert "--network" in run_args
    assert run_args[run_args.index("--network") + 1] == "none"
    assert "--privileged=false" in run_args
    assert "--cap-drop" in run_args
    assert "--security-opt" in run_args
    assert "type=volume,source=agent_workspaces,target=/work/agent_jobs" in run_args
    assert "type=volume,source=unreal_ccache_volume,target=/work/.ccache" in run_args
    assert "type=volume,source=unreal_ubt_volume,target=/work/ubt-cache" in run_args
    assert "ghcr.io/moonladderstudios/moonmind-unreal-runner:5.3" in run_args
    assert "/var/run/docker.sock" not in " ".join(run_args)


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
    workspace_root = tmp_path / "workspace"
    artifact_dir = workspace_root / "task-phase4" / "artifacts" / "step-test"
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
            workspace_root=workspace_root,
            taskRunId="task-phase4",
            repoDir=str(workspace_root / "task-phase4" / "repo"),
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
async def test_launcher_diagnostics_omit_env_values_and_auth_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    artifact_dir = workspace_root / "task-redaction" / "artifacts" / "step-test"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    secret_value = "inline_secret_value_for_redaction_test"
    auth_path = "/home/codex/.codex/auth.json"

    async def _fake_create_subprocess_exec(*args: str, **_kwargs: Any) -> _Process:
        if args[1] == "run":
            return _Process(returncode=0, stdout=b"runtime stdout\n")
        return _Process(returncode=0)

    monkeypatch.setattr(
        "moonmind.workloads.docker_launcher.asyncio.create_subprocess_exec",
        _fake_create_subprocess_exec,
    )

    result = await DockerWorkloadLauncher().run(
        _validated_request(
            tmp_path,
            workspace_root=workspace_root,
            taskRunId="task-redaction",
            repoDir=str(workspace_root / "task-redaction" / "repo"),
            artifactsDir=str(artifact_dir),
            envOverrides={
                "CI": secret_value,
                "UE_PROJECT_PATH": auth_path,
            },
        )
    )

    diagnostics_text = Path(result.diagnostics_ref or "").read_text("utf-8")
    diagnostics = json.loads(diagnostics_text)
    workload_metadata_text = json.dumps(
        result.metadata["workload"],
        sort_keys=True,
    )

    assert diagnostics["envOverrideKeys"] == ["CI", "UE_PROJECT_PATH"]
    assert secret_value not in diagnostics_text
    assert secret_value not in workload_metadata_text
    assert auth_path not in diagnostics_text
    assert auth_path not in workload_metadata_text
    assert "envOverrides" not in diagnostics


@pytest.mark.asyncio
async def test_launcher_publishes_failure_artifacts_with_session_association(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    artifact_dir = workspace_root / "task-phase4-session" / "artifacts" / "step-test"
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
            workspace_root=workspace_root,
            taskRunId="task-phase4-session",
            repoDir=str(workspace_root / "task-phase4-session" / "repo"),
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
async def test_launcher_reports_artifact_publication_failure_in_result_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_create_subprocess_exec(*args: str, **_kwargs: Any) -> _Process:
        if args[1] == "run":
            return _Process(returncode=0, stdout=b"before publish failure\n")
        return _Process(returncode=0)

    def _fail_write(_path: Path, _payload: str) -> str:
        raise OSError("artifact store unavailable")

    monkeypatch.setattr(
        "moonmind.workloads.docker_launcher.asyncio.create_subprocess_exec",
        _fake_create_subprocess_exec,
    )
    monkeypatch.setattr(
        "moonmind.workloads.docker_launcher._write_text_artifact",
        _fail_write,
    )

    result = await DockerWorkloadLauncher().run(_validated_request(tmp_path))

    assert result.status == "succeeded"
    assert result.stdout_ref is None
    assert result.stderr_ref is None
    assert result.diagnostics_ref is None
    assert result.output_refs == {}
    assert result.metadata["artifactPublication"]["status"] == "failed"
    assert result.metadata["artifactPublication"]["error"] == "artifact store unavailable"
    assert result.metadata["artifactPublication"]["errors"] == {
        "runtime.stdout": "artifact store unavailable",
        "runtime.stderr": "artifact store unavailable",
        "runtime.diagnostics": "artifact store unavailable",
    }
    assert result.metadata["stdout"] == "before publish failure\n"


@pytest.mark.asyncio
async def test_launcher_preserves_refs_when_artifact_publication_partly_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    artifact_dir = workspace_root / "task-partial-artifacts" / "artifacts" / "step-test"
    artifact_dir.mkdir(parents=True, exist_ok=True)

    async def _fake_create_subprocess_exec(*args: str, **_kwargs: Any) -> _Process:
        if args[1] == "run":
            return _Process(returncode=0, stdout=b"saved stdout\n", stderr=b"lost\n")
        return _Process(returncode=0)

    def _write_maybe_fail(path: Path, payload: str) -> str:
        if path.name == "runtime.stderr.log":
            raise OSError("stderr store unavailable")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload, encoding="utf-8")
        return str(path)

    monkeypatch.setattr(
        "moonmind.workloads.docker_launcher.asyncio.create_subprocess_exec",
        _fake_create_subprocess_exec,
    )
    monkeypatch.setattr(
        "moonmind.workloads.docker_launcher._write_text_artifact",
        _write_maybe_fail,
    )

    result = await DockerWorkloadLauncher().run(
        _validated_request(
            tmp_path,
            workspace_root=workspace_root,
            taskRunId="task-partial-artifacts",
            repoDir=str(workspace_root / "task-partial-artifacts" / "repo"),
            artifactsDir=str(artifact_dir),
        )
    )

    assert result.stdout_ref is not None
    assert result.stderr_ref is None
    assert result.diagnostics_ref is not None
    assert result.output_refs["runtime.stdout"] == result.stdout_ref
    assert result.output_refs["runtime.diagnostics"] == result.diagnostics_ref
    assert "runtime.stderr" not in result.output_refs
    diagnostics = json.loads(Path(result.diagnostics_ref or "").read_text("utf-8"))
    assert diagnostics["artifactPublication"]["errors"] == {
        "runtime.stderr": "stderr store unavailable"
    }
    assert result.metadata["artifactPublication"]["status"] == "failed"
    assert result.metadata["artifactPublication"]["errors"] == {
        "runtime.stderr": "stderr store unavailable"
    }


@pytest.mark.asyncio
async def test_launcher_links_declared_output_artifacts_under_artifacts_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    artifact_dir = workspace_root / "task-phase4-outputs" / "artifacts" / "step-test"
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
            workspace_root=workspace_root,
            taskRunId="task-phase4-outputs",
            repoDir=str(workspace_root / "task-phase4-outputs" / "repo"),
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
async def test_launcher_enforces_profile_concurrency_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    class _ReleasePipe:
        async def read(self, _size: int = -1) -> bytes:
            await release.wait()
            return b""

    class _BlockingProcess(_Process):
        def __init__(self) -> None:
            super().__init__(never_complete=True)
            self.stdout = _ReleasePipe()
            self.stderr = _ReleasePipe()

        async def wait(self) -> int:
            await release.wait()
            self.returncode = 0
            self._closed.set()
            return 0

    async def _fake_create_subprocess_exec(*args: str, **_kwargs: Any) -> _Process:
        if args[1] == "run":
            started.set()
            return _BlockingProcess()
        return _Process(returncode=0)

    monkeypatch.setattr(
        "moonmind.workloads.docker_launcher.asyncio.create_subprocess_exec",
        _fake_create_subprocess_exec,
    )
    limiter = DockerWorkloadConcurrencyLimiter(fleet_limit=4)
    launcher = DockerWorkloadLauncher(concurrency_limiter=limiter)
    first = asyncio.create_task(
        launcher.run(
            _validated_request(
                tmp_path,
                taskRunId="task-concurrency-a",
                repoDir="/work/agent_jobs/task-concurrency-a/repo",
                artifactsDir="/work/agent_jobs/task-concurrency-a/artifacts/step-test",
            )
        )
    )
    await started.wait()

    with pytest.raises(DockerWorkloadLauncherError, match="concurrency limit"):
        await launcher.run(
            _validated_request(
                tmp_path,
                taskRunId="task-concurrency-b",
                repoDir="/work/agent_jobs/task-concurrency-b/repo",
                artifactsDir="/work/agent_jobs/task-concurrency-b/artifacts/step-test",
            )
        )

    release.set()
    result = await first
    assert result.status == "succeeded"


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


@pytest.mark.asyncio
async def test_container_janitor_sweeps_expired_workload_orphans(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[list[str]] = []

    async def _fake_create_subprocess_exec(*args: str, **_kwargs: Any) -> _Process:
        created.append(list(args))
        if args[1:3] == ("ps", "-a"):
            return _Process(
                returncode=0,
                stdout=(
                    b"expired123\tmm-workload-expired\t2026-04-12T00:00:00Z\n"
                    b"fresh456\tmm-workload-fresh\t2026-04-13T00:00:00Z\n"
                    b"missing789\tmm-workload-missing\t\n"
                ),
            )
        return _Process(returncode=0)

    monkeypatch.setattr(
        "moonmind.workloads.docker_launcher.asyncio.create_subprocess_exec",
        _fake_create_subprocess_exec,
    )

    janitor = DockerContainerJanitor()
    swept = await janitor.sweep_expired_workloads(
        now_iso="2026-04-12T12:00:00Z",
    )

    assert swept == ("expired123",)
    assert ["docker", "rm", "-f", "expired123"] in created
    assert ["docker", "rm", "-f", "fresh456"] not in created
    assert ["docker", "rm", "-f", "missing789"] not in created


@pytest.mark.asyncio
async def test_launcher_starts_bounded_helper_detached_and_waits_for_readiness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[list[str]] = []

    async def _fake_create_subprocess_exec(*args: str, **_kwargs: Any) -> _Process:
        created.append(list(args))
        if args[1] == "run":
            return _Process(returncode=0, stdout=b"helper123\n")
        if args[1] == "exec":
            return _Process(returncode=0, stdout=b"PONG\n")
        return _Process(returncode=0)

    monkeypatch.setattr(
        "moonmind.workloads.docker_launcher.asyncio.create_subprocess_exec",
        _fake_create_subprocess_exec,
    )

    result = await DockerWorkloadLauncher().start_helper(
        _validated_helper_request(tmp_path)
    )

    run_args = created[0]
    assert run_args[:4] == [
        "docker",
        "run",
        "--detach",
        "--name",
    ]
    assert run_args[4] == "mm-helper-task-helper-step-service-1"
    assert "moonmind.kind=bounded_service" in run_args
    assert "moonmind.helper_ttl_seconds=300" in run_args
    assert any(label.startswith("moonmind.expires_at=") for label in run_args)
    assert run_args[-3:] == ["redis:7.2-alpine", "--appendonly", "no"]
    assert created[1] == [
        "docker",
        "exec",
        "mm-helper-task-helper-step-service-1",
        "redis-cli",
        "ping",
    ]
    assert result.status == "ready"
    assert result.exit_code is None
    assert result.metadata["helper"]["containerName"] == (
        "mm-helper-task-helper-step-service-1"
    )
    assert result.metadata["helper"]["readiness"]["status"] == "ready"
    assert result.metadata["helper"]["ttlSeconds"] == 300
    assert result.metadata["helper"]["sessionContext"] is None


@pytest.mark.asyncio
async def test_launcher_reports_unhealthy_helper_after_bounded_readiness_retries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[list[str]] = []

    async def _fake_create_subprocess_exec(*args: str, **_kwargs: Any) -> _Process:
        created.append(list(args))
        if args[1] == "run":
            return _Process(returncode=0, stdout=b"helper123\n")
        if args[1] == "exec":
            return _Process(returncode=1, stderr=b"not ready\n")
        return _Process(returncode=0)

    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(
        "moonmind.workloads.docker_launcher.asyncio.create_subprocess_exec",
        _fake_create_subprocess_exec,
    )
    monkeypatch.setattr("moonmind.workloads.docker_launcher.asyncio.sleep", _no_sleep)

    result = await DockerWorkloadLauncher().start_helper(
        _validated_helper_request(tmp_path)
    )

    exec_calls = [args for args in created if args[1] == "exec"]
    assert len(exec_calls) == 3
    assert result.status == "unhealthy"
    assert result.metadata["helper"]["readiness"]["status"] == "unhealthy"
    assert result.metadata["helper"]["readiness"]["attempts"] == 3


@pytest.mark.asyncio
async def test_launcher_tears_down_bounded_helper_after_multiple_sub_steps(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[list[str]] = []

    async def _fake_create_subprocess_exec(*args: str, **_kwargs: Any) -> _Process:
        created.append(list(args))
        if args[1] == "run":
            return _Process(returncode=0, stdout=b"helper123\n")
        if args[1] == "exec":
            return _Process(returncode=0, stdout=b"PONG\n")
        return _Process(returncode=0)

    monkeypatch.setattr(
        "moonmind.workloads.docker_launcher.asyncio.create_subprocess_exec",
        _fake_create_subprocess_exec,
    )
    launcher = DockerWorkloadLauncher()
    validated = _validated_helper_request(tmp_path)

    start_result = await launcher.start_helper(validated)
    sub_step_observations = [
        start_result.metadata["helper"]["containerName"],
        start_result.metadata["helper"]["containerName"],
    ]
    stop_result = await launcher.stop_helper(validated, reason="bounded_window_complete")

    assert sub_step_observations == [
        "mm-helper-task-helper-step-service-1",
        "mm-helper-task-helper-step-service-1",
    ]
    assert [
        "docker",
        "stop",
        "-t",
        "3",
        "mm-helper-task-helper-step-service-1",
    ] in created
    assert ["docker", "kill", "mm-helper-task-helper-step-service-1"] in created
    assert ["docker", "rm", "-f", "mm-helper-task-helper-step-service-1"] in created
    assert stop_result.status == "stopped"
    assert stop_result.metadata["helper"]["teardown"]["reason"] == (
        "bounded_window_complete"
    )


@pytest.mark.asyncio
async def test_container_janitor_sweeps_expired_bounded_helpers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[list[str]] = []

    async def _fake_create_subprocess_exec(*args: str, **_kwargs: Any) -> _Process:
        created.append(list(args))
        if args[1:3] == ("ps", "-a"):
            return _Process(
                returncode=0,
                stdout=(
                    b"helper-expired\tmm-helper-expired\t2026-04-12T00:00:00Z\n"
                    b"helper-fresh\tmm-helper-fresh\t2026-04-13T00:00:00Z\n"
                ),
            )
        return _Process(returncode=0)

    monkeypatch.setattr(
        "moonmind.workloads.docker_launcher.asyncio.create_subprocess_exec",
        _fake_create_subprocess_exec,
    )

    janitor = DockerContainerJanitor()
    swept = await janitor.sweep_expired_helpers(
        now_iso="2026-04-12T12:00:00Z",
    )

    assert swept == ("helper-expired",)
    assert ["docker", "rm", "-f", "helper-expired"] in created
    assert ["docker", "rm", "-f", "helper-fresh"] not in created
    assert created[0] == [
        "docker",
        "ps",
        "-a",
        "--filter",
        "label=moonmind.kind=bounded_service",
        "--format",
        '{{.ID}}\t{{.Names}}\t{{.Label "moonmind.expires_at"}}',
    ]
