"""CPU-capable qualification fixtures for generic NVIDIA GPU containers.

Qualification layer 1 for MoonLadderStudios/MoonMind#3777. Every test here runs
on an ordinary CPU-only runner against a synthetic Docker launcher, and proves
the generic contract: caller-owned image and command, GPU request validation
independent of profile device policy, device-request construction, workspace and
output mounts, logs, timeout, cancellation, declared outputs, cleanup, and the
negative matrix. The real-NVIDIA leg lives in
``tests/integration/workloads/test_nvidia_container_qualification_journey.py``.

No test in this module interprets the workload: the image and command are opaque
caller data.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

import pytest
from pydantic import ValidationError

from moonmind.schemas.workload_models import (
    UnrestrictedContainerRequest,
    UnrestrictedDockerRequest,
    WorkloadGpuRequest,
    WorkloadRequest,
)
from moonmind.workloads.docker_launcher import (
    DockerWorkloadLauncher,
    DockerWorkloadLauncherError,
)
from moonmind.workloads.gpu import (
    GPU_CONTAINER_REQUEST_SCHEMA_VERSION,
    GPU_DEVICE_REQUEST_REJECTED,
    build_gpu_qualification_record,
    classify_gpu_launch_failure,
    gpu_device_request_args,
    moonmind_revision,
    parse_image_digest,
)
from moonmind.workloads.registry import RunnerProfileRegistry, WorkloadPolicyError
from tests.unit.workloads.test_docker_workload_launcher import _Process

# Fixture data only. These are caller-supplied request values, never MoonMind
# defaults: nothing in ``moonmind/`` references them.
QUALIFICATION_IMAGE = "docker.io/library/qualification-fixture:1.0.0"
QUALIFICATION_COMMAND = ("sh", "-lc", "probe --emit report.json")
SHARED_CACHE_VOLUME = "gpu_qualification_cache"


def _workspace(tmp_path: Path, agent_run_id: str) -> dict[str, Path]:
    root = tmp_path / "agent-workspaces"
    repo = root / agent_run_id / "repo"
    artifacts = root / agent_run_id / "artifacts" / "gpu-step"
    scratch = root / agent_run_id / "scratch" / "gpu-step"
    for path in (repo, artifacts, scratch):
        path.mkdir(parents=True, exist_ok=True)
    return {"root": root, "repo": repo, "artifacts": artifacts, "scratch": scratch}


def _container_request_payload(
    paths: dict[str, Path],
    *,
    agent_run_id: str = "task-gpu",
    step_id: str = "gpu-step",
    attempt: int = 1,
    gpu: dict[str, Any] | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    resources: dict[str, Any] = {"cpu": "2", "memory": "2g"}
    if gpu is not None:
        resources["gpu"] = gpu
    payload: dict[str, Any] = {
        "toolName": "container.run_container",
        "agentRunId": agent_run_id,
        "stepId": step_id,
        "attempt": attempt,
        "repoDir": str(paths["repo"]),
        "artifactsDir": str(paths["artifacts"]),
        "scratchDir": str(paths["scratch"]),
        "image": QUALIFICATION_IMAGE,
        "command": list(QUALIFICATION_COMMAND),
        "networkMode": "none",
        "timeoutSeconds": 120,
        "resources": resources,
        "declaredOutputs": {"output.primary": "gpu/report.json"},
    }
    payload.update(overrides)
    return payload


def _validated(paths: dict[str, Path], **kwargs: Any):
    registry = RunnerProfileRegistry.empty(workspace_root=paths["root"])
    return registry.validate_request(
        _container_request_payload(paths, **kwargs),
        workflow_docker_mode="unrestricted",
    )


def _fake_docker(
    recorded: list[list[str]],
    *,
    run_process: Any = None,
) -> Any:
    async def _create(*args: str, **_kwargs: Any) -> _Process:
        recorded.append(list(args))
        if len(args) > 1 and args[1] == "run":
            return run_process() if callable(run_process) else _Process(returncode=0)
        return _Process(returncode=0)

    return _create


def _install_fake_docker(
    monkeypatch: pytest.MonkeyPatch,
    recorded: list[list[str]],
    *,
    run_process: Any = None,
) -> None:
    monkeypatch.setattr(
        "moonmind.workloads.docker_launcher.asyncio.create_subprocess_exec",
        _fake_docker(recorded, run_process=run_process),
    )


def _flag_value(args: list[str], flag: str) -> str:
    return args[args.index(flag) + 1]


# ---------------------------------------------------------------------------
# Generic request ownership
# ---------------------------------------------------------------------------


def test_gpu_request_defaults_to_all_devices_for_the_supported_vendor() -> None:
    gpu = WorkloadGpuRequest()

    assert gpu.vendor == "nvidia"
    assert gpu.count == "all"
    assert gpu_device_request_args(gpu) == ["--gpus", "all"]


def test_gpu_request_realizes_numeric_count_as_the_device_request() -> None:
    assert gpu_device_request_args(WorkloadGpuRequest(count=2)) == ["--gpus", "2"]


def test_caller_owns_the_exact_image_and_command_for_a_gpu_request(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path, "task-gpu")

    args = DockerWorkloadLauncher().build_run_args(
        _validated(paths, gpu={"count": "all"})
    )

    # MoonMind selects no image and appends no application command: the image is
    # the last argument before the caller's own argv.
    assert args[-4:] == [QUALIFICATION_IMAGE, *QUALIFICATION_COMMAND]
    assert args.count(QUALIFICATION_IMAGE) == 1


def test_gpu_request_survives_serialization_and_dispatch(tmp_path: Path) -> None:
    paths = _workspace(tmp_path, "task-gpu")
    request = UnrestrictedContainerRequest.model_validate(
        _container_request_payload(paths, gpu={"vendor": "nvidia", "count": 4})
    )

    wire = json.loads(request.model_dump_json(by_alias=True, exclude_none=True))
    round_tripped = UnrestrictedContainerRequest.model_validate(wire)

    assert wire["resources"]["gpu"] == {"vendor": "nvidia", "count": 4}
    assert round_tripped.resources.gpu == request.resources.gpu
    assert round_tripped.image == QUALIFICATION_IMAGE
    assert round_tripped.command == QUALIFICATION_COMMAND


def test_any_repository_skill_can_produce_the_gpu_request_shape(
    tmp_path: Path,
) -> None:
    """The request is plain JSON: no MoonMind-private field is required."""

    from moonmind.schemas.workload_models import parse_workload_request

    paths = _workspace(tmp_path, "task-gpu")
    skill_emitted = json.loads(
        json.dumps(_container_request_payload(paths, gpu={"count": "all"}))
    )

    parsed = parse_workload_request(skill_emitted)

    assert isinstance(parsed, UnrestrictedContainerRequest)
    assert parsed.resources.gpu == WorkloadGpuRequest(count="all")
    assert parsed.image == QUALIFICATION_IMAGE


def test_gpu_request_validation_does_not_consult_profile_device_policy(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path, "task-gpu")
    registry = RunnerProfileRegistry.empty(workspace_root=paths["root"])

    validated = registry.validate_request(
        _container_request_payload(paths, gpu={"count": "all"}),
        workflow_docker_mode="unrestricted",
    )

    assert validated.profile is None
    assert validated.request.resources.gpu == WorkloadGpuRequest(count="all")
    assert validated.ownership.labels["moonmind.workload_access"] == (
        "unrestricted_container"
    )


# ---------------------------------------------------------------------------
# Docker command construction
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("count", "expected"),
    [("all", "all"), (1, "1"), (8, "8")],
)
def test_docker_run_carries_the_requested_device_request(
    tmp_path: Path,
    count: Any,
    expected: str,
) -> None:
    paths = _workspace(tmp_path, "task-gpu")

    args = DockerWorkloadLauncher().build_run_args(
        _validated(paths, gpu={"count": count})
    )

    assert _flag_value(args, "--gpus") == expected


def test_gpu_run_keeps_workspace_output_and_cache_mounts(tmp_path: Path) -> None:
    paths = _workspace(tmp_path, "task-gpu")

    args = DockerWorkloadLauncher().build_run_args(
        _validated(
            paths,
            gpu={"count": "all"},
            cacheMounts=[{"source": SHARED_CACHE_VOLUME, "target": "/work/cache"}],
        )
    )

    mounts = {args[index + 1] for index, value in enumerate(args) if value == "--mount"}
    assert f"type=bind,source={paths['repo']},target={paths['repo']}" in mounts
    assert f"type=bind,source={paths['artifacts']},target={paths['artifacts']}" in mounts
    assert f"type=bind,source={paths['scratch']},target={paths['scratch']}" in mounts
    assert f"type=volume,source={SHARED_CACHE_VOLUME},target=/work/cache" in mounts
    assert _flag_value(args, "--workdir") == str(paths["repo"])
    assert "--privileged=false" in args


def test_cpu_only_request_emits_no_device_request(tmp_path: Path) -> None:
    paths = _workspace(tmp_path, "task-cpu")

    args = DockerWorkloadLauncher().build_run_args(_validated(paths))

    assert "--gpus" not in args
    assert _flag_value(args, "--cpus") == "2"
    assert _flag_value(args, "--memory") == "2g"


# ---------------------------------------------------------------------------
# Lifecycle, artifacts, and evidence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gpu_container_run_publishes_logs_declared_output_and_observations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _workspace(tmp_path, "task-gpu")
    report = paths["artifacts"] / "gpu" / "report.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps({"devices": 1, "checksum": "ok"}), encoding="utf-8")
    recorded: list[list[str]] = []
    _install_fake_docker(
        monkeypatch,
        recorded,
        run_process=lambda: _Process(returncode=0, stdout=b"device 0 ready\n"),
    )
    validated = _validated(paths, gpu={"count": "all"})

    result = await DockerWorkloadLauncher().run(validated)

    assert result.status == "succeeded"
    assert result.exit_code == 0
    assert result.metadata["stdout"] == "device 0 ready\n"
    assert result.output_refs["output.primary"] == str(report)
    assert Path(result.output_refs["runtime.stdout"]).is_file()
    assert Path(result.output_refs["runtime.stderr"]).is_file()
    observations = result.metadata["workload"]["gpu"]
    assert observations["request"] == {"vendor": "nvidia", "count": "all"}
    assert observations["deviceRequestArgs"] == ["--gpus", "all"]
    assert observations["launchFailure"] is None
    diagnostics = json.loads(
        Path(result.output_refs["runtime.diagnostics"]).read_text(encoding="utf-8")
    )
    assert diagnostics["gpu"]["deviceRequestArgs"] == ["--gpus", "all"]
    assert diagnostics["declaredOutputRefs"] == {"output.primary": str(report)}


@pytest.mark.asyncio
async def test_gpu_container_cleanup_removes_only_the_run_owned_container(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _workspace(tmp_path, "task-gpu")
    recorded: list[list[str]] = []
    _install_fake_docker(monkeypatch, recorded)
    validated = _validated(
        paths,
        gpu={"count": "all"},
        cacheMounts=[{"source": SHARED_CACHE_VOLUME, "target": "/work/cache"}],
    )

    result = await DockerWorkloadLauncher().run(validated)

    container = "mm-workload-task-gpu-gpu-step-1"
    assert ["docker", "rm", "-f", container] in recorded
    assert result.metadata["workload"]["containerName"] == container
    verbs = {args[1] for args in recorded if len(args) > 1}
    assert "rmi" not in verbs
    assert "image" not in verbs
    assert "volume" not in verbs
    assert "system" not in verbs
    flattened = [value for args in recorded for value in args]
    assert QUALIFICATION_IMAGE not in flattened[1:] or all(
        args[1] == "run" for args in recorded if QUALIFICATION_IMAGE in args
    )
    assert SHARED_CACHE_VOLUME not in [
        value for args in recorded if args[1] != "run" for value in args
    ]


@pytest.mark.asyncio
async def test_cpu_only_unrestricted_cleanup_semantics_are_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A CPU-only unrestricted run keeps the cleanup semantics it was launched with.

    The ``workload.run`` binding is retained so already recorded Temporal
    commands can replay. An in-flight CPU-only attempt that retries or resumes on
    this version must not start deleting a container the earlier version retained,
    and must not report different cleanup metadata.
    """

    paths = _workspace(tmp_path, "task-cpu")
    recorded: list[list[str]] = []
    _install_fake_docker(monkeypatch, recorded)

    result = await DockerWorkloadLauncher().run(
        _validated(paths, agent_run_id="task-cpu")
    )

    container = "mm-workload-task-cpu-gpu-step-1"
    assert result.metadata["workload"]["containerName"] == container
    assert result.metadata["workload"]["gpu"] is None
    assert ["docker", "rm", "-f", container] not in recorded
    diagnostics = json.loads(
        Path(result.output_refs["runtime.diagnostics"]).read_text(encoding="utf-8")
    )
    assert diagnostics["cleanup"]["removeContainerOnExit"] is False


@pytest.mark.asyncio
async def test_device_bearing_unrestricted_run_owns_its_container_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _workspace(tmp_path, "task-gpu")
    recorded: list[list[str]] = []
    _install_fake_docker(monkeypatch, recorded)

    result = await DockerWorkloadLauncher().run(_validated(paths, gpu={"count": "all"}))

    container = "mm-workload-task-gpu-gpu-step-1"
    assert ["docker", "rm", "-f", container] in recorded
    diagnostics = json.loads(
        Path(result.output_refs["runtime.diagnostics"]).read_text(encoding="utf-8")
    )
    assert diagnostics["cleanup"]["removeContainerOnExit"] is True


@pytest.mark.asyncio
async def test_gpu_container_evidence_excludes_docker_endpoint_and_host_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _workspace(tmp_path, "task-gpu")
    monkeypatch.setenv("MOONMIND_UNRELATED_HOST_SECRET", "should-not-be-published")
    recorded: list[list[str]] = []
    _install_fake_docker(monkeypatch, recorded)
    launcher = DockerWorkloadLauncher(docker_host="tcp://docker-proxy:2375")

    result = await launcher.run(_validated(paths, gpu={"count": "all"}))

    published = json.dumps(result.model_dump(mode="json", by_alias=True))
    # Published evidence is what the launcher writes under the artifacts root
    # and returns as outputRefs.
    evidence_files = [
        Path(result.output_refs[artifact_class]).read_text(encoding="utf-8")
        for artifact_class in ("runtime.diagnostics", "runtime.stdout", "runtime.stderr")
    ]
    for evidence in (published, *evidence_files):
        assert "should-not-be-published" not in evidence
        assert "MOONMIND_UNRELATED_HOST_SECRET" not in evidence
    for evidence in evidence_files:
        assert "tcp://docker-proxy:2375" not in evidence
        assert "/var/run/docker.sock" not in evidence
        assert "DOCKER_HOST" not in evidence


@pytest.mark.asyncio
async def test_gpu_container_timeout_targets_only_the_run_owned_container(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _workspace(tmp_path, "task-gpu")
    recorded: list[list[str]] = []
    _install_fake_docker(
        monkeypatch,
        recorded,
        run_process=lambda: _Process(never_complete=True),
    )

    result = await DockerWorkloadLauncher().run(
        _validated(paths, gpu={"count": "all"}), timeout_seconds=0.01
    )

    container = "mm-workload-task-gpu-gpu-step-1"
    assert result.status == "timed_out"
    assert result.timeout_reason == "workload exceeded timeoutSeconds"
    assert ["docker", "stop", "-t", "30", container] in recorded
    assert ["docker", "kill", container] in recorded
    assert all(
        container in args for args in recorded if args[1] in {"stop", "kill", "rm"}
    )


@pytest.mark.asyncio
async def test_gpu_container_timeout_preserves_partial_declared_outputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _workspace(tmp_path, "task-gpu")
    report = paths["artifacts"] / "gpu" / "report.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text('{"partial": true}', encoding="utf-8")
    recorded: list[list[str]] = []
    _install_fake_docker(
        monkeypatch,
        recorded,
        run_process=lambda: _Process(never_complete=True),
    )

    result = await DockerWorkloadLauncher().run(
        _validated(paths, gpu={"count": "all"}), timeout_seconds=0.01
    )

    assert result.status == "timed_out"
    assert result.output_refs["output.primary"] == str(report)


@pytest.mark.asyncio
async def test_gpu_container_cancellation_before_launch_starts_no_container(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _workspace(tmp_path, "task-gpu")
    recorded: list[list[str]] = []

    async def _create(*args: str, **_kwargs: Any) -> _Process:
        recorded.append(list(args))
        return _Process(returncode=0)

    monkeypatch.setattr(
        "moonmind.workloads.docker_launcher.asyncio.create_subprocess_exec", _create
    )
    validated = _validated(paths, gpu={"count": "all"})
    task = asyncio.create_task(DockerWorkloadLauncher().run(validated))
    task.cancel()
    await asyncio.wait({task})

    assert task.cancelled()
    assert recorded == []


@pytest.mark.asyncio
async def test_gpu_container_cancellation_during_execution_stops_the_container(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _workspace(tmp_path, "task-gpu")
    recorded: list[list[str]] = []
    launched: list[_Process] = []

    def _run_process() -> _Process:
        process = _Process(never_complete=True)
        launched.append(process)
        return process

    _install_fake_docker(monkeypatch, recorded, run_process=_run_process)
    validated = _validated(paths, gpu={"count": "all"})
    task = asyncio.create_task(DockerWorkloadLauncher().run(validated))
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.wait({task})

    container = "mm-workload-task-gpu-gpu-step-1"
    assert task.cancelled()
    assert ["docker", "stop", "-t", "30", container] in recorded
    assert ["docker", "kill", container] in recorded
    assert launched and launched[0].terminated


# ---------------------------------------------------------------------------
# Negative matrix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "gpu",
    [
        {"vendor": "amd"},
        {"vendor": "intel", "count": 1},
        {"vendor": ""},
    ],
)
def test_unsupported_gpu_vendor_is_rejected(tmp_path: Path, gpu: dict[str, Any]) -> None:
    paths = _workspace(tmp_path, "task-gpu")

    with pytest.raises(ValidationError, match="nvidia"):
        UnrestrictedContainerRequest.model_validate(
            _container_request_payload(paths, gpu=gpu)
        )


@pytest.mark.parametrize(
    "gpu",
    [
        {"count": 0},
        {"count": -1},
        {"count": "many"},
        {"count": "all", "devices": ["0"]},
        {"count": 1.5},
        # Values that Pydantic's integer coercion would otherwise realize as a
        # device count the caller never declared.
        {"count": True},
        {"count": False},
        {"count": "2"},
        {"count": " 2 "},
        {"count": 2.0},
        {"count": None},
        {"count": []},
    ],
)
def test_malformed_gpu_request_is_rejected(tmp_path: Path, gpu: dict[str, Any]) -> None:
    paths = _workspace(tmp_path, "task-gpu")

    with pytest.raises(ValidationError):
        UnrestrictedContainerRequest.model_validate(
            _container_request_payload(paths, gpu=gpu)
        )


def test_profile_backed_request_rejects_gpu_without_device_policy_support(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "agent-workspaces"
    (workspace_root / "task-gpu" / "repo").mkdir(parents=True)
    registry_path = tmp_path / "profiles.json"
    registry_path.write_text(
        json.dumps(
            {
                "profiles": [
                    {
                        "id": "local-python",
                        "kind": "one_shot",
                        "image": "python:3.12-slim",
                        "workdirTemplate": "/work",
                        "requiredMounts": [
                            {
                                "type": "volume",
                                "source": "agent_workspaces",
                                "target": str(workspace_root),
                            }
                        ],
                        "networkPolicy": "none",
                        "timeoutSeconds": 60,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    registry = RunnerProfileRegistry.load_file(
        registry_path, workspace_root=workspace_root
    )

    with pytest.raises(WorkloadPolicyError) as exc_info:
        registry.validate_request(
            WorkloadRequest.model_validate(
                {
                    "profileId": "local-python",
                    "agentRunId": "task-gpu",
                    "stepId": "gpu-step",
                    "attempt": 1,
                    "toolName": "container.run_workload",
                    "repoDir": str(workspace_root / "task-gpu" / "repo"),
                    "artifactsDir": str(workspace_root / "task-gpu" / "repo"),
                    "command": ["python", "-V"],
                    "resources": {"gpu": {"count": "all"}},
                }
            )
        )

    assert exc_info.value.reason == "unsupported_gpu_request"
    assert exc_info.value.details["devicePolicyMode"] == "none"


def test_raw_docker_cli_request_rejects_a_gpu_resource(tmp_path: Path) -> None:
    paths = _workspace(tmp_path, "task-gpu")

    with pytest.raises(ValidationError, match="resources.gpu"):
        UnrestrictedDockerRequest.model_validate(
            {
                "toolName": "container.run_docker",
                "agentRunId": "task-gpu",
                "stepId": "gpu-step",
                "attempt": 1,
                "repoDir": str(paths["repo"]),
                "artifactsDir": str(paths["artifacts"]),
                "command": ["docker", "ps"],
                "resources": {"gpu": {"count": "all"}},
            }
        )


def test_gpu_request_outside_workspace_root_is_rejected(tmp_path: Path) -> None:
    paths = _workspace(tmp_path, "task-gpu")
    registry = RunnerProfileRegistry.empty(workspace_root=paths["root"])

    with pytest.raises(WorkloadPolicyError) as exc_info:
        registry.validate_request(
            _container_request_payload(
                paths, gpu={"count": "all"}, repoDir="/tmp/elsewhere"
            ),
            workflow_docker_mode="unrestricted",
        )

    assert exc_info.value.reason == "disallowed_mount"


@pytest.mark.parametrize(
    "declared",
    [
        {"output.primary": "../escape.json"},
        {"output.primary": "/etc/passwd"},
    ],
)
def test_unsafe_declared_output_path_is_rejected(
    tmp_path: Path, declared: dict[str, str]
) -> None:
    paths = _workspace(tmp_path, "task-gpu")

    with pytest.raises(ValidationError, match="artifactsDir"):
        UnrestrictedContainerRequest.model_validate(
            _container_request_payload(
                paths, gpu={"count": "all"}, declaredOutputs=declared
            )
        )


@pytest.mark.asyncio
async def test_missing_declared_output_is_reported_without_failing_the_container(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _workspace(tmp_path, "task-gpu")
    recorded: list[list[str]] = []
    _install_fake_docker(monkeypatch, recorded)

    result = await DockerWorkloadLauncher().run(_validated(paths, gpu={"count": "all"}))

    assert result.status == "succeeded"
    assert "output.primary" not in result.output_refs
    diagnostics = json.loads(
        Path(result.output_refs["runtime.diagnostics"]).read_text(encoding="utf-8")
    )
    assert diagnostics["missingDeclaredOutputs"] == {
        "output.primary": "gpu/report.json"
    }
    assert diagnostics["reportPublication"]["missingOutputs"] == {
        "output.primary": "gpu/report.json"
    }
    assert "publishedRefs" not in diagnostics["reportPublication"]


@pytest.mark.asyncio
async def test_nonzero_process_exit_is_not_reported_as_a_gpu_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _workspace(tmp_path, "task-gpu")
    recorded: list[list[str]] = []
    _install_fake_docker(
        monkeypatch,
        recorded,
        run_process=lambda: _Process(returncode=3, stderr=b"workload assertion failed\n"),
    )

    result = await DockerWorkloadLauncher().run(_validated(paths, gpu={"count": "all"}))

    assert result.status == "failed"
    assert result.exit_code == 3
    assert result.metadata["workload"]["gpu"]["launchFailure"] is None


@pytest.mark.parametrize(
    "stderr",
    [
        "Failed to initialize NVML: driver/library version mismatch",
        "nvidia-container-cli mode is unsupported by this workload",
        'could not select device driver "" with capabilities: [[gpu]]',
        "detected 0 devices",
    ],
)
@pytest.mark.asyncio
async def test_started_container_writing_gpu_text_is_not_a_device_refusal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stderr: str,
) -> None:
    """A started container's own diagnostic never becomes a launch refusal.

    Docker forwards the application's stderr and its own exit status. Only
    Docker's launch-failure status is objective evidence that the device request
    itself was refused.
    """

    paths = _workspace(tmp_path, "task-gpu")
    recorded: list[list[str]] = []
    _install_fake_docker(
        monkeypatch,
        recorded,
        run_process=lambda: _Process(returncode=1, stderr=stderr.encode() + b"\n"),
    )

    result = await DockerWorkloadLauncher().run(_validated(paths, gpu={"count": "all"}))

    assert result.status == "failed"
    assert result.exit_code == 1
    assert result.metadata["workload"]["gpu"]["launchFailure"] is None


@pytest.mark.parametrize(
    ("stderr", "reason"),
    [
        (
            (
                'docker: Error response from daemon: could not select device '
                'driver "" with capabilities: [[gpu]].'
            ),
            "gpu_device_request_rejected",
        ),
        (
            (
                "docker: Error response from daemon: failed to create task for "
                "container: nvidia-container-cli: initialization error: load "
                "library failed."
            ),
            "nvidia_runtime_unavailable",
        ),
        (
            "docker: Error response from daemon: unknown or invalid runtime name: nvidia.",
            "nvidia_runtime_unavailable",
        ),
        (
            (
                "nvidia-container-cli: device error: Failed to initialize NVML: "
                "no devices were found."
            ),
            "nvidia_runtime_unavailable",
        ),
        (
            "docker: Error response from daemon: detected 0 devices for the request.",
            "gpu_device_unavailable",
        ),
        (
            "unknown flag: --gpus\nSee 'docker run --help'.",
            "device_request_unsupported",
        ),
    ],
)
def test_docker_rejection_of_the_device_request_is_classified(
    stderr: str, reason: str
) -> None:
    failure = classify_gpu_launch_failure(
        gpu=WorkloadGpuRequest(count="all"), exit_code=125, stderr=stderr
    )

    assert failure is not None
    assert failure.failure_class == GPU_DEVICE_REQUEST_REJECTED
    assert failure.reason == reason
    assert failure.exit_code == 125


@pytest.mark.parametrize(
    "stderr",
    [
        (
            "Cannot connect to the Docker daemon at unix:///var/run/docker.sock. "
            "Is the docker daemon running?"
        ),
        "docker: Error response from daemon: pull access denied for private/image.",
        (
            "docker: Error response from daemon: invalid mount config: bind source "
            "path does not exist."
        ),
        "workload exited with status 1",
        # A container that started and echoed GPU-shaped text is a process exit.
        "usage: probe --gpus <count>\nerror: missing operand",
        "no such device: /dev/fuse",
        "",
    ],
)
def test_non_gpu_launch_failures_are_not_classified_as_gpu_rejections(
    stderr: str,
) -> None:
    assert (
        classify_gpu_launch_failure(
            gpu=WorkloadGpuRequest(count="all"), exit_code=125, stderr=stderr
        )
        is None
    )


@pytest.mark.parametrize("exit_code", [1, 2, 3, 126, 127, 137, None])
def test_device_refusal_requires_dockers_own_launch_failure_status(
    exit_code: int | None,
) -> None:
    assert (
        classify_gpu_launch_failure(
            gpu=WorkloadGpuRequest(count="all"),
            exit_code=exit_code,
            stderr='could not select device driver "" with capabilities: [[gpu]]',
        )
        is None
    )


def test_cpu_only_runs_never_produce_a_gpu_failure_classification() -> None:
    assert (
        classify_gpu_launch_failure(
            gpu=None,
            exit_code=125,
            stderr='could not select device driver "" with capabilities: [[gpu]]',
        )
        is None
    )


@pytest.mark.asyncio
async def test_docker_daemon_unavailable_stays_a_generic_container_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _workspace(tmp_path, "task-gpu")
    recorded: list[list[str]] = []
    _install_fake_docker(
        monkeypatch,
        recorded,
        run_process=lambda: _Process(
            returncode=125,
            stderr=b"Cannot connect to the Docker daemon. Is the docker daemon running?\n",
        ),
    )

    result = await DockerWorkloadLauncher().run(_validated(paths, gpu={"count": "all"}))

    assert result.status == "failed"
    assert result.exit_code == 125
    assert result.metadata["workload"]["gpu"]["launchFailure"] is None


@pytest.mark.asyncio
async def test_gpu_device_request_rejection_is_reported_on_the_generic_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _workspace(tmp_path, "task-gpu")
    recorded: list[list[str]] = []
    _install_fake_docker(
        monkeypatch,
        recorded,
        run_process=lambda: _Process(
            returncode=125,
            stderr=(
                b'docker: Error response from daemon: could not select device driver ""'
                b" with capabilities: [[gpu]].\n"
            ),
        ),
    )

    result = await DockerWorkloadLauncher().run(_validated(paths, gpu={"count": "all"}))

    assert result.status == "failed"
    failure = result.metadata["workload"]["gpu"]["launchFailure"]
    assert failure["failureClass"] == GPU_DEVICE_REQUEST_REJECTED
    assert failure["reason"] == "gpu_device_request_rejected"
    assert ["docker", "rm", "-f", "mm-workload-task-gpu-gpu-step-1"] in recorded


@pytest.mark.asyncio
async def test_cleanup_tolerates_an_already_absent_container(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _workspace(tmp_path, "task-gpu")
    recorded: list[list[str]] = []

    async def _create(*args: str, **_kwargs: Any) -> _Process:
        recorded.append(list(args))
        if args[1] == "rm":
            return _Process(returncode=1, stderr=b"Error: No such container\n")
        return _Process(returncode=0)

    monkeypatch.setattr(
        "moonmind.workloads.docker_launcher.asyncio.create_subprocess_exec", _create
    )

    result = await DockerWorkloadLauncher().run(_validated(paths, gpu={"count": "all"}))

    assert result.status == "succeeded"
    assert ["docker", "rm", "-f", "mm-workload-task-gpu-gpu-step-1"] in recorded


@pytest.mark.asyncio
async def test_workspace_mount_unavailable_stays_a_generic_container_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _workspace(tmp_path, "task-gpu")
    recorded: list[list[str]] = []
    _install_fake_docker(
        monkeypatch,
        recorded,
        run_process=lambda: _Process(
            returncode=125,
            stderr=(
                b"docker: Error response from daemon: invalid mount config for type "
                b'"bind": bind source path does not exist.\n'
            ),
        ),
    )

    result = await DockerWorkloadLauncher().run(_validated(paths, gpu={"count": "all"}))

    assert result.status == "failed"
    assert result.exit_code == 125
    # A missing workspace bind is a generic launch failure, not a GPU refusal.
    assert result.metadata["workload"]["gpu"]["launchFailure"] is None
    assert ["docker", "rm", "-f", "mm-workload-task-gpu-gpu-step-1"] in recorded


@pytest.mark.asyncio
async def test_image_unavailable_stays_a_generic_container_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _workspace(tmp_path, "task-gpu")
    recorded: list[list[str]] = []
    _install_fake_docker(
        monkeypatch,
        recorded,
        run_process=lambda: _Process(
            returncode=125,
            stderr=(
                b"docker: Error response from daemon: pull access denied for "
                b"private/image, repository does not exist or may require "
                b"'docker login'.\n"
            ),
        ),
    )

    result = await DockerWorkloadLauncher().run(_validated(paths, gpu={"count": "all"}))

    assert result.status == "failed"
    assert result.exit_code == 125
    assert result.metadata["workload"]["gpu"]["launchFailure"] is None
    assert result.metadata["image"] == QUALIFICATION_IMAGE


def test_unsupported_unrestricted_request_shape_fails_before_launch(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path, "task-gpu")
    validated = _validated(paths, gpu={"count": "all"})

    with pytest.raises(DockerWorkloadLauncherError, match="unsupported unrestricted"):
        DockerWorkloadLauncher().build_run_args(
            validated.model_copy(update={"request": _unsupported_request(paths)})
        )


def _unsupported_request(paths: dict[str, Path]) -> Any:
    class _Unsupported:
        tool_name = "container.run_container"
        command = ("noop",)
        env_overrides: ClassVar[dict[str, str]] = {}
        declared_outputs: ClassVar[dict[str, str]] = {}
        collect_globs: tuple[str, ...] = ()
        repo_dir = str(paths["repo"])
        artifacts_dir = str(paths["artifacts"])

    return _Unsupported()


# ---------------------------------------------------------------------------
# Warm reuse
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_second_gpu_request_reuses_image_and_cache_with_new_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths_first = _workspace(tmp_path, "task-gpu-a")
    paths_second = _workspace(tmp_path, "task-gpu-b")
    paths_second["root"] = paths_first["root"]
    recorded: list[list[str]] = []
    _install_fake_docker(monkeypatch, recorded)
    launcher = DockerWorkloadLauncher()
    cache = [{"source": SHARED_CACHE_VOLUME, "target": "/work/cache"}]

    first = await launcher.run(
        _validated(
            paths_first,
            agent_run_id="task-gpu-a",
            gpu={"count": "all"},
            cacheMounts=cache,
        )
    )
    second = await launcher.run(
        _validated(
            paths_second,
            agent_run_id="task-gpu-b",
            gpu={"count": "all"},
            cacheMounts=cache,
        )
    )

    # Distinct execution identity, identical caller-supplied image and cache.
    assert first.request_id != second.request_id
    assert first.labels["moonmind.agent_run_id"] != second.labels["moonmind.agent_run_id"]
    assert first.metadata["image"] == second.metadata["image"] == QUALIFICATION_IMAGE

    runs = [args for args in recorded if args[1] == "run"]
    assert len(runs) == 2
    for args in runs:
        # ``docker run`` uses if-missing image behavior by default: no pull is
        # forced and the image is never rebuilt between requests.
        assert "--pull" not in args
        assert f"type=volume,source={SHARED_CACHE_VOLUME},target=/work/cache" in args

    # Cleanup after the first request removed its container only, so the image
    # and the shared cache volume survive for the second request.
    removals = [args for args in recorded if args[1] == "rm"]
    assert [args[-1] for args in removals] == [
        first.request_id,
        second.request_id,
    ]
    assert not [args for args in recorded if args[1] in {"rmi", "image", "volume"}]


# ---------------------------------------------------------------------------
# Qualification record
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_qualification_record_captures_generic_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _workspace(tmp_path, "task-gpu")
    report = paths["artifacts"] / "gpu" / "report.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text('{"devices": 1}', encoding="utf-8")
    recorded: list[list[str]] = []
    _install_fake_docker(monkeypatch, recorded)
    validated = _validated(paths, gpu={"count": 2})

    result = await DockerWorkloadLauncher().run(validated)
    record = build_gpu_qualification_record(
        request=validated.request,
        result=result,
        recorded_at=datetime(2026, 8, 26, 12, 0, tzinfo=UTC),
        image_digest="sha256:" + "a" * 64,
        env={"MOONMIND_BUILD_SHA": "abc1234"},
    )

    payload = record.model_dump(mode="json", by_alias=True)
    assert payload["recordVersion"] == "v1"
    assert payload["moonmindRevision"] == "abc1234"
    assert payload["requestSchemaVersion"] == GPU_CONTAINER_REQUEST_SCHEMA_VERSION
    assert payload["imageRef"] == QUALIFICATION_IMAGE
    assert payload["imageDigest"] == "sha256:" + "a" * 64
    # The published record keeps null optional fields, so an omitted
    # capability request is recorded as explicitly absent.
    assert payload["gpuRequest"] == {
        "vendor": "nvidia",
        "count": 2,
        "capabilities": None,
    }
    assert payload["deviceRequestArgs"] == ["--gpus", "2"]
    assert payload["status"] == "succeeded"
    assert payload["exitCode"] == 0
    assert payload["gpuLaunchFailure"] is None
    assert payload["declaredOutputChecksums"]["output.primary"].startswith("sha256:")
    assert payload["startedAt"] and payload["completedAt"] and payload["recordedAt"]
    # Only declared outputs are checksummed; runtime log classes are not.
    assert set(payload["declaredOutputChecksums"]) == {"output.primary"}


def test_qualification_record_requires_a_gpu_request(tmp_path: Path) -> None:
    paths = _workspace(tmp_path, "task-gpu")
    request = UnrestrictedContainerRequest.model_validate(
        _container_request_payload(paths)
    )
    from moonmind.schemas.workload_models import WorkloadResult

    with pytest.raises(ValueError, match="GPU resource request"):
        build_gpu_qualification_record(
            request=request,
            result=WorkloadResult(
                requestId="mm-workload-task-gpu-gpu-step-1",
                profileId="container.run_container",
                status="succeeded",
            ),
            recorded_at=datetime(2026, 8, 26, 12, 0, tzinfo=UTC),
        )


def _executed_workload_metadata(
    request: UnrestrictedContainerRequest,
    *,
    device_request_args: list[str] | None = None,
    image_ref: str | None = None,
    container_name: str | None = None,
    gpu_request: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the generic executed-workload evidence any trusted host reports."""

    gpu = request.resources.gpu
    assert gpu is not None
    return {
        "workload": {
            "containerName": container_name or request.container_name,
            "imageRef": image_ref or request.image,
            "gpu": {
                "request": (
                    gpu_request
                    if gpu_request is not None
                    else gpu.model_dump(
                        mode="json", by_alias=True, exclude_none=True
                    )
                ),
                "deviceRequestArgs": (
                    device_request_args
                    if device_request_args is not None
                    else list(gpu_device_request_args(gpu))
                ),
                "launchFailure": None,
            },
        }
    }


def _foreign_result(
    request: UnrestrictedContainerRequest,
    **metadata_kwargs: Any,
) -> Any:
    """Return a result produced by some other trusted host, not this launcher."""

    from moonmind.schemas.workload_models import WorkloadResult

    return WorkloadResult(
        requestId=request.container_name,
        profileId="container.run_container",
        status="succeeded",
        exitCode=0,
        startedAt=datetime(2026, 8, 26, 11, 59, tzinfo=UTC),
        completedAt=datetime(2026, 8, 26, 12, 0, tzinfo=UTC),
        durationSeconds=60.0,
        metadata=_executed_workload_metadata(request, **metadata_kwargs),
    )


def test_qualification_record_is_host_independent(tmp_path: Path) -> None:
    """The record depends only on the generic request and result contracts.

    Nothing in it names the launcher, the Activity, or the dispatch path, so the
    same qualification can be rerun against a future canonical container host
    that reports the same generic executed-workload evidence.
    """

    paths = _workspace(tmp_path, "task-gpu")
    request = UnrestrictedContainerRequest.model_validate(
        _container_request_payload(paths, gpu={"count": "all"})
    )

    record = build_gpu_qualification_record(
        request=request,
        result=_foreign_result(request),
        recorded_at=datetime(2026, 8, 26, 12, 1, tzinfo=UTC),
        env={"MOONMIND_BUILD_SHA": "abc1234"},
    )

    payload = record.model_dump(mode="json", by_alias=True)
    assert payload["deviceRequestArgs"] == ["--gpus", "all"]
    assert payload["status"] == "succeeded"
    assert payload["gpuLaunchFailure"] is None
    serialized = json.dumps(payload).lower()
    for host_detail in ("launcher", "activity", "dockerhost", "workflow"):
        assert host_detail not in serialized


def test_qualification_record_requires_realized_device_request_evidence(
    tmp_path: Path,
) -> None:
    """A host that reported no device request cannot be recorded as realizing one."""

    from moonmind.schemas.workload_models import WorkloadResult

    paths = _workspace(tmp_path, "task-gpu")
    request = UnrestrictedContainerRequest.model_validate(
        _container_request_payload(paths, gpu={"count": "all"})
    )

    for metadata in (
        {},
        {"workload": {"containerName": request.container_name, "imageRef": request.image}},
        {
            "workload": {
                "containerName": request.container_name,
                "imageRef": request.image,
                "gpu": None,
            }
        },
    ):
        with pytest.raises(ValueError, match="evidence"):
            build_gpu_qualification_record(
                request=request,
                result=WorkloadResult(
                    requestId=request.container_name,
                    profileId="container.run_container",
                    status="succeeded",
                    exitCode=0,
                    metadata=metadata,
                ),
                recorded_at=datetime(2026, 8, 26, 12, 0, tzinfo=UTC),
                env={"MOONMIND_BUILD_SHA": "abc1234"},
            )


def test_qualification_record_rejects_a_substrate_that_omitted_the_device_request(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path, "task-gpu")
    request = UnrestrictedContainerRequest.model_validate(
        _container_request_payload(paths, gpu={"count": "all"})
    )

    for realized in ([], ["--device", "/dev/nvidia0"]):
        with pytest.raises(ValueError, match="realized device request"):
            build_gpu_qualification_record(
                request=request,
                result=_foreign_result(request, device_request_args=realized),
                recorded_at=datetime(2026, 8, 26, 12, 0, tzinfo=UTC),
                env={"MOONMIND_BUILD_SHA": "abc1234"},
            )


def test_qualification_record_binds_the_result_to_the_requested_container(
    tmp_path: Path,
) -> None:
    """A result from a concurrent or retried run cannot be recorded as this one."""

    paths = _workspace(tmp_path, "task-gpu")
    request = UnrestrictedContainerRequest.model_validate(
        _container_request_payload(paths, gpu={"count": "all"})
    )
    other = UnrestrictedContainerRequest.model_validate(
        _container_request_payload(paths, agent_run_id="task-gpu-other", gpu={"count": 2})
    )

    with pytest.raises(ValueError, match="different container"):
        build_gpu_qualification_record(
            request=request,
            result=_foreign_result(other),
            recorded_at=datetime(2026, 8, 26, 12, 0, tzinfo=UTC),
            env={"MOONMIND_BUILD_SHA": "abc1234"},
        )

    with pytest.raises(ValueError, match="different container"):
        build_gpu_qualification_record(
            request=request,
            result=_foreign_result(request, container_name=other.container_name),
            recorded_at=datetime(2026, 8, 26, 12, 0, tzinfo=UTC),
            env={"MOONMIND_BUILD_SHA": "abc1234"},
        )

    with pytest.raises(ValueError, match="different image"):
        build_gpu_qualification_record(
            request=request,
            result=_foreign_result(request, image_ref="docker.io/library/other:2.0.0"),
            recorded_at=datetime(2026, 8, 26, 12, 0, tzinfo=UTC),
            env={"MOONMIND_BUILD_SHA": "abc1234"},
        )

    with pytest.raises(ValueError, match="different GPU request"):
        build_gpu_qualification_record(
            request=request,
            result=_foreign_result(
                request,
                gpu_request={"vendor": "nvidia", "count": 2},
                device_request_args=["--gpus", "2"],
            ),
            recorded_at=datetime(2026, 8, 26, 12, 0, tzinfo=UTC),
            env={"MOONMIND_BUILD_SHA": "abc1234"},
        )


def test_qualification_record_requires_an_immutable_moonmind_revision(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path, "task-gpu")
    request = UnrestrictedContainerRequest.model_validate(
        _container_request_payload(paths, gpu={"count": "all"})
    )

    with pytest.raises(ValueError, match="immutable MoonMind revision"):
        build_gpu_qualification_record(
            request=request,
            result=_foreign_result(request),
            recorded_at=datetime(2026, 8, 26, 12, 0, tzinfo=UTC),
            env={},
        )


def test_moonmind_revision_prefers_build_sha_then_image_digest() -> None:
    assert moonmind_revision({"MOONMIND_BUILD_SHA": "sha-1"}) == "sha-1"
    assert moonmind_revision({"MOONMIND_IMAGE_DIGEST": "sha256:beef"}) == "sha256:beef"
    assert moonmind_revision({"MOONMIND_BUILD_SHA": "  "}) is None
    assert moonmind_revision({}) is None


def test_image_digest_is_selected_by_repository_not_by_position() -> None:
    digest = "sha256:" + "b" * 64
    alias_digest = "sha256:" + "c" * 64
    inspected = json.dumps(
        [f"other/alias@{alias_digest}", f"{QUALIFICATION_IMAGE.split(':')[0]}@{digest}"]
    )

    assert parse_image_digest(inspected, image=QUALIFICATION_IMAGE) == digest
    # An alias repository's digest is never attributed to the requested image.
    assert (
        parse_image_digest(
            json.dumps([f"other/alias@{alias_digest}"]), image=QUALIFICATION_IMAGE
        )
        is None
    )
    assert parse_image_digest("[]", image=QUALIFICATION_IMAGE) is None
    assert parse_image_digest("null", image=QUALIFICATION_IMAGE) is None
    assert parse_image_digest(None, image=QUALIFICATION_IMAGE) is None
    assert parse_image_digest("not json", image=QUALIFICATION_IMAGE) is None
