"""Generic NVIDIA GPU resources on unrestricted container execution.

Source: MoonLadderStudios/MoonMind#3775 (epic #3774).

MoonMind owns the generic request shape, the workspace, the Docker dispatch, the
lifecycle, and the declared-output collection. The caller owns the image, the
command, the requested GPU resources, and the interpretation of the result.
These tests pin that boundary end to end for the unrestricted container path:
validation, serialization, Docker realization, bounded observations, lifecycle,
and the absence of profile-mode device-policy involvement.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

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
    _launch_outcome,
)
from moonmind.workloads.registry import RunnerProfileRegistry

WORKSPACE_ROOT = Path("/work/agent_jobs")
CALLER_IMAGE = "ghcr.io/example/caller-owned:1.4.2"
CALLER_COMMAND = ("caller-owned-doctor", "--report", "gpu.json")

REPO_ROOT = Path(__file__).resolve().parents[3]
# The generic GPU implementation surface. #3774's genericity guard forbids
# project, engine, or application coupling in these files; project names stay in
# external consumer fixtures and documentation.
GENERIC_GPU_IMPLEMENTATION_FILES = (
    REPO_ROOT / "moonmind" / "schemas" / "workload_models.py",
    REPO_ROOT / "moonmind" / "workloads" / "docker_launcher.py",
    REPO_ROOT / "moonmind" / "workloads" / "registry.py",
    REPO_ROOT / "moonmind" / "workloads" / "unrestricted_container_tool.py",
)


def _container_payload(
    *,
    workspace_root: Path = WORKSPACE_ROOT,
    agent_run_id: str = "task-gpu",
    step_id: str = "step-render",
    attempt: int = 1,
    **overrides: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "agentRunId": agent_run_id,
        "stepId": step_id,
        "attempt": attempt,
        "toolName": "container.run_container",
        "repoDir": f"{workspace_root}/{agent_run_id}/repo",
        "artifactsDir": f"{workspace_root}/{agent_run_id}/artifacts/{step_id}",
        "scratchDir": f"{workspace_root}/{agent_run_id}/scratch/{step_id}",
        "image": CALLER_IMAGE,
        "command": list(CALLER_COMMAND),
        "resources": {"gpu": {"vendor": "nvidia", "count": "all"}},
    }
    payload.update(overrides)
    return payload


def _profile_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": "local-python",
        "kind": "one_shot",
        "image": "python:3.12-slim",
        "workdirTemplate": f"{WORKSPACE_ROOT}/${{agent_run_id}}/repo",
        "requiredMounts": [
            {
                "type": "volume",
                "source": "agent_workspaces",
                "target": str(WORKSPACE_ROOT),
            }
        ],
        "envAllowlist": ["CI"],
        "networkPolicy": "none",
        "timeoutSeconds": 300,
        "devicePolicy": {"mode": "none"},
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
        json.dumps({"profiles": [_profile_payload()]}),
        encoding="utf-8",
    )
    return RunnerProfileRegistry.load_file(
        registry_path,
        workspace_root=workspace_root,
    )


def _validated(
    tmp_path: Path,
    *,
    workspace_root: Path = WORKSPACE_ROOT,
    **overrides: Any,
):
    return _registry(tmp_path, workspace_root=workspace_root).validate_request(
        _container_payload(workspace_root=workspace_root, **overrides),
        workflow_docker_mode="unrestricted",
    )


class _Pipe:
    def __init__(self, process: "_Process", data: bytes) -> None:
        self._process = process
        self._data = bytearray(data)

    async def read(self, size: int = -1) -> bytes:
        if not self._data:
            await self._process.closed.wait()
            return b""
        if size is None or size < 0:
            size = len(self._data)
        chunk = bytes(self._data[:size])
        del self._data[:size]
        return chunk


class _Process:
    """Minimal ``asyncio`` subprocess double for the Docker CLI."""

    def __init__(
        self,
        *,
        returncode: int = 0,
        stdout: bytes = b"",
        stderr: bytes = b"",
        never_complete: bool = False,
    ) -> None:
        self.returncode = None if never_complete else returncode
        self.killed = False
        self.terminated = False
        self.closed = asyncio.Event()
        self._stdout = stdout
        self._stderr = stderr
        if not never_complete:
            self.closed.set()
        self.stdout = _Pipe(self, stdout)
        self.stderr = _Pipe(self, stderr)

    async def communicate(self) -> tuple[bytes, bytes]:
        if self.returncode is None and not self.killed:
            await self.closed.wait()
        return self._stdout, self._stderr

    async def wait(self) -> int:
        if self.returncode is None:
            await self.closed.wait()
        return int(self.returncode or 0)

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15
        self.closed.set()

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9
        self.closed.set()


def _fake_docker(
    monkeypatch: pytest.MonkeyPatch,
    created: list[list[str]],
    *,
    run_exit_code: int = 0,
    run_stdout: bytes = b"",
    run_stderr: bytes = b"",
    run_never_completes: bool = False,
) -> list[_Process]:
    run_processes: list[_Process] = []

    async def _create(*args: str, **_kwargs: Any) -> _Process:
        created.append(list(args))
        if len(args) > 1 and args[1] == "run":
            process = _Process(
                returncode=run_exit_code,
                stdout=run_stdout,
                stderr=run_stderr,
                never_complete=run_never_completes,
            )
            run_processes.append(process)
            return process
        return _Process(returncode=0)

    monkeypatch.setattr(
        "moonmind.workloads.docker_launcher.asyncio.create_subprocess_exec",
        _create,
    )
    return run_processes


def _flag_values(args: list[str], flag: str) -> list[str]:
    return [args[index + 1] for index, item in enumerate(args) if item == flag]


# --------------------------------------------------------------------------- #
# 1. Request and schema support
# --------------------------------------------------------------------------- #


def test_unrestricted_container_request_accepts_generic_nvidia_gpu_resource() -> None:
    request = UnrestrictedContainerRequest.model_validate(_container_payload())

    gpu = request.resources.gpu
    assert gpu is not None
    assert (gpu.contract_version, gpu.vendor, gpu.count) == ("v1", "nvidia", "all")
    # The caller's image and command are preserved verbatim after validation.
    assert request.image == CALLER_IMAGE
    assert request.command == CALLER_COMMAND


def test_generic_gpu_resource_survives_a_serialization_round_trip() -> None:
    request = UnrestrictedContainerRequest.model_validate(
        _container_payload(
            resources={
                "shmSize": "2g",
                "gpu": {
                    "vendor": "nvidia",
                    "count": 2,
                    "capabilities": ["compute", "utility"],
                },
            }
        )
    )

    # The serialized activity payload is what crosses the Temporal boundary.
    wire = json.loads(request.model_dump_json(by_alias=True, exclude_none=True))
    assert wire["resources"] == {
        "shmSize": "2g",
        "gpu": {
            "contractVersion": "v1",
            "vendor": "nvidia",
            "count": 2,
            "capabilities": ["compute", "utility"],
        },
    }
    assert UnrestrictedContainerRequest.model_validate(wire) == request


def test_generic_gpu_request_deduplicates_capabilities_in_order() -> None:
    gpu = WorkloadGpuRequest.model_validate(
        {"vendor": "nvidia", "capabilities": ["utility", "compute", "utility"]}
    )

    assert gpu.capabilities == ("utility", "compute")


@pytest.mark.parametrize(
    ("gpu_payload", "match"),
    [
        ({"vendor": "amd", "count": "all"}, "vendor"),
        ({"vendor": "nvidia", "count": 0}, "positive integer"),
        ({"vendor": "nvidia", "count": -2}, "positive integer"),
        # The count is a JSON integer or "all"; a boolean (``bool`` subclasses
        # ``int``), a numeric string, and a float are all malformed counts.
        ({"vendor": "nvidia", "count": True}, "valid integer"),
        ({"vendor": "nvidia", "count": "2"}, "valid integer"),
        ({"vendor": "nvidia", "count": 2.0}, "valid integer"),
        ({"vendor": "nvidia", "count": "most"}, "count"),
        ({"vendor": "nvidia", "capabilities": ["render"]}, "capabilities"),
        ({"count": "all"}, "vendor"),
        ({"vendor": "nvidia", "contractVersion": "v2"}, "contractVersion"),
        # Raw daemon authority must not arrive through the GPU field.
        ({"vendor": "nvidia", "devices": ["/dev/nvidia0"]}, "devices"),
        ({"vendor": "nvidia", "privileged": True}, "privileged"),
        ({"vendor": "nvidia", "runtime": "nvidia"}, "runtime"),
    ],
)
def test_malformed_gpu_requests_fail_before_launch(
    gpu_payload: dict[str, Any],
    match: str,
) -> None:
    with pytest.raises(ValidationError, match=match):
        UnrestrictedContainerRequest.model_validate(
            _container_payload(resources={"gpu": gpu_payload})
        )


def test_profile_and_raw_docker_requests_reject_structured_gpu_resources() -> None:
    """GPU resources are only meaningful on the structured unrestricted request.

    Profile mode keeps its own device policy and gains no GPU support in v1, and
    a raw ``container.run_docker`` command is caller-composed: MoonMind must not
    append ``--gpus`` to it.
    """

    with pytest.raises(ValidationError, match="resources.gpu"):
        WorkloadRequest.model_validate(
            {
                "profileId": "local-python",
                "agentRunId": "task-gpu",
                "stepId": "step-render",
                "attempt": 1,
                "toolName": "container.run_workload",
                "repoDir": f"{WORKSPACE_ROOT}/task-gpu/repo",
                "artifactsDir": f"{WORKSPACE_ROOT}/task-gpu/artifacts/step-render",
                "command": ["python", "-V"],
                "resources": {"gpu": {"vendor": "nvidia", "count": "all"}},
            }
        )

    with pytest.raises(ValidationError, match="resources.gpu"):
        UnrestrictedDockerRequest.model_validate(
            {
                "agentRunId": "task-gpu",
                "stepId": "step-render",
                "attempt": 1,
                "toolName": "container.run_docker",
                "repoDir": f"{WORKSPACE_ROOT}/task-gpu/repo",
                "artifactsDir": f"{WORKSPACE_ROOT}/task-gpu/artifacts/step-render",
                "command": ["docker", "run", "--gpus", "all", CALLER_IMAGE],
                "resources": {"gpu": {"vendor": "nvidia", "count": "all"}},
            }
        )


def test_raw_docker_argv_is_never_rewritten_for_gpu_resources(tmp_path: Path) -> None:
    """A caller-composed Docker command keeps its own ``--gpus``, untouched.

    ``container.run_docker`` is not an available caller route: the retained
    ``workload.run`` Activity refuses the name in every mode (see
    docs/Workflows/GpuContainerResourcesContract.md section 3.1). This pins the
    launcher behavior below that gate — MoonMind never appends, rewrites, or
    strips GPU arguments in a caller-composed command.
    """

    validated = _registry(tmp_path).validate_request(
        {
            "agentRunId": "task-gpu",
            "stepId": "step-render",
            "attempt": 1,
            "toolName": "container.run_docker",
            "repoDir": f"{WORKSPACE_ROOT}/task-gpu/repo",
            "artifactsDir": f"{WORKSPACE_ROOT}/task-gpu/artifacts/step-render",
            "command": ["docker", "run", "--gpus", "all", CALLER_IMAGE, "doctor"],
        },
        workflow_docker_mode="unrestricted",
    )

    args = DockerWorkloadLauncher().build_run_args(validated)

    assert args == ["docker", "run", "--gpus", "all", CALLER_IMAGE, "doctor"]


# --------------------------------------------------------------------------- #
# 2. Docker realization
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("gpu_payload", "expected_flag_value"),
    [
        # ``--gpus all`` is what makes Docker build the NVIDIA DeviceRequest
        # {"Driver": "nvidia", "Count": -1, "Capabilities": [["gpu"]]}; the
        # equivalences are tabulated in
        # docs/Workflows/GpuContainerResourcesContract.md section 4.
        ({"vendor": "nvidia", "count": "all"}, "all"),
        ({"vendor": "nvidia"}, "all"),
        ({"vendor": "nvidia", "count": 2}, "2"),
        (
            {"vendor": "nvidia", "count": "all", "capabilities": ["compute", "utility"]},
            'driver=nvidia,count=all,"capabilities=compute,utility"',
        ),
    ],
)
def test_gpu_request_maps_to_the_nvidia_gpus_launch_value(
    gpu_payload: dict[str, Any],
    expected_flag_value: str,
) -> None:
    gpu = WorkloadGpuRequest.model_validate(gpu_payload)

    assert gpu.docker_gpus_value() == expected_flag_value


def test_unrestricted_gpu_launch_receives_gpus_all_and_caller_owned_argv(
    tmp_path: Path,
) -> None:
    args = DockerWorkloadLauncher().build_run_args(_validated(tmp_path))

    assert _flag_values(args, "--gpus") == ["all"]
    # MoonMind neither selects nor rewrites the caller's image and command.
    assert args[-len(CALLER_COMMAND) - 1 :] == [CALLER_IMAGE, *CALLER_COMMAND]
    # GPU support adds no daemon, device, or namespace authority.
    assert "--privileged=false" in args
    assert args[args.index("--cap-drop") + 1] == "ALL"
    for forbidden in (
        "--privileged",
        "--device",
        "--runtime",
        "--pid",
        "--ipc",
        "--userns",
        "--volume",
        "-H",
        "--host",
    ):
        assert forbidden not in args


def test_unrestricted_gpu_launch_preserves_numeric_count_and_capabilities(
    tmp_path: Path,
) -> None:
    args = DockerWorkloadLauncher().build_run_args(
        _validated(
            tmp_path,
            resources={
                "shmSize": "4g",
                "gpu": {
                    "vendor": "nvidia",
                    "count": 4,
                    "capabilities": ["compute", "graphics"],
                },
            },
        )
    )

    assert _flag_values(args, "--gpus") == [
        'driver=nvidia,count=4,"capabilities=compute,graphics"'
    ]
    assert _flag_values(args, "--shm-size") == ["4g"]


def test_cpu_only_unrestricted_and_profile_launches_have_no_gpu_arguments(
    tmp_path: Path,
) -> None:
    registry = _registry(tmp_path)

    unrestricted = DockerWorkloadLauncher().build_run_args(
        registry.validate_request(
            _container_payload(resources={"cpu": "2", "memory": "2g"}),
            workflow_docker_mode="unrestricted",
        )
    )
    assert "--gpus" not in unrestricted
    assert _flag_values(unrestricted, "--cpus") == ["2"]

    profile_backed = DockerWorkloadLauncher().build_run_args(
        registry.validate_request(
            {
                "profileId": "local-python",
                "agentRunId": "task-cpu",
                "stepId": "step-test",
                "attempt": 1,
                "toolName": "container.run_workload",
                "repoDir": f"{WORKSPACE_ROOT}/task-cpu/repo",
                "artifactsDir": f"{WORKSPACE_ROOT}/task-cpu/artifacts/step-test",
                "command": ["python", "-V"],
                "envOverrides": {"CI": "1"},
            }
        )
    )
    assert "--gpus" not in profile_backed


def test_profile_device_policy_is_not_consulted_for_unrestricted_gpu_requests(
    tmp_path: Path,
) -> None:
    """No GPU runner profile, device policy, or approval is required."""

    registry = _registry(tmp_path)
    assert registry.get("local-python").device_policy.mode == "none"

    validated = registry.validate_request(
        _container_payload(), workflow_docker_mode="unrestricted"
    )

    assert validated.profile is None
    assert validated.ownership.workload_access == "unrestricted_container"
    assert validated.ownership.labels["moonmind.workflow_docker_mode"] == "unrestricted"
    assert validated.request.resources.gpu is not None


# --------------------------------------------------------------------------- #
# 3. Bounded observations and outcome classification
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("status", "exit_code", "expected"),
    [
        ("succeeded", 0, "succeeded"),
        ("failed", 125, "docker_request_rejected"),
        ("failed", 126, "container_start_failed"),
        ("failed", 127, "container_command_not_found"),
        ("failed", 3, "process_failed"),
        ("timed_out", None, "timed_out"),
        ("timed_out", -9, "timed_out"),
        ("failed", None, "unknown"),
    ],
)
def test_generic_launch_outcomes_stay_distinguishable(
    status: str,
    exit_code: int | None,
    expected: str,
) -> None:
    assert _launch_outcome(status=status, exit_code=exit_code) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("exit_code", "expected_outcome", "docker_accepted"),
    [
        (0, "succeeded", True),
        # Docker refused the device request: unsupported DeviceRequests, an
        # unavailable NVIDIA runtime, or an absent GPU all surface as 125.
        (125, "docker_request_rejected", False),
        (126, "container_start_failed", True),
        (7, "process_failed", True),
    ],
)
async def test_gpu_run_records_bounded_generic_observations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    exit_code: int,
    expected_outcome: str,
    docker_accepted: bool,
) -> None:
    created: list[list[str]] = []
    _fake_docker(monkeypatch, created, run_exit_code=exit_code)

    result = await DockerWorkloadLauncher().run(
        _validated(
            tmp_path,
            resources={
                "gpu": {
                    "vendor": "nvidia",
                    "count": 2,
                    "capabilities": ["compute"],
                }
            },
        )
    )

    workload = result.metadata["workload"]
    assert workload["launchOutcome"] == expected_outcome
    assert workload["gpu"] == {
        "requested": True,
        "contractVersion": "v1",
        "vendor": "nvidia",
        "count": 2,
        "capabilities": ["compute"],
        "dockerAccepted": docker_accepted,
    }
    assert result.status == ("succeeded" if exit_code == 0 else "failed")
    assert result.exit_code == exit_code
    # Raw driver details and daemon configuration stay out of the observation.
    serialized = json.dumps(workload["gpu"])
    assert "libnvidia" not in serialized and "/dev/" not in serialized


@pytest.mark.asyncio
async def test_cpu_only_run_reports_no_gpu_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[list[str]] = []
    _fake_docker(monkeypatch, created, run_exit_code=0)

    result = await DockerWorkloadLauncher().run(
        _validated(tmp_path, resources={"cpu": "2"})
    )

    assert result.metadata["workload"]["gpu"] == {"requested": False}
    assert "--gpus" not in created[0]


# --------------------------------------------------------------------------- #
# 4. Workspace, declared outputs, and lifecycle
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_gpu_container_collects_declared_outputs_and_uninterpreted_logs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_root = tmp_path / "agent_jobs"
    repo_dir = workspace_root / "task-gpu" / "repo"
    artifacts_dir = workspace_root / "task-gpu" / "artifacts" / "step-render"
    scratch_dir = workspace_root / "task-gpu" / "scratch" / "step-render"
    for directory in (repo_dir, artifacts_dir, scratch_dir):
        directory.mkdir(parents=True, exist_ok=True)
    (artifacts_dir / "gpu-doctor.json").write_text(
        json.dumps({"callerOwnedVerdict": "pass"}),
        encoding="utf-8",
    )
    (repo_dir / "render.log").write_text("caller log\n", encoding="utf-8")

    created: list[list[str]] = []
    doctor_output = b'{"callerOwnedVerdict": "pass", "devices": 1}\n'
    _fake_docker(monkeypatch, created, run_exit_code=0, run_stdout=doctor_output)

    validated = _validated(
        tmp_path,
        workspace_root=workspace_root,
        declaredOutputs={"output.primary": "gpu-doctor.json"},
        collectGlobs=["render.log"],
        cacheMounts=[{"source": "caller_gpu_cache", "target": "/caller/cache"}],
    )

    result = await DockerWorkloadLauncher().run(validated)

    run_args = created[0]
    assert _flag_values(run_args, "--gpus") == ["all"]
    assert (
        "type=volume,source=caller_gpu_cache,target=/caller/cache" in run_args
    )
    assert f"type=bind,source={repo_dir},target={repo_dir}" in run_args
    assert result.status == "succeeded"
    # The image-owned doctor output is collected verbatim, not interpreted.
    assert result.metadata["stdout"] == doctor_output.decode()
    assert result.output_refs["output.primary"] == str(
        artifacts_dir / "gpu-doctor.json"
    )
    assert result.output_refs["collected:render.log"] == str(repo_dir / "render.log")
    # Only the run-owned container is cleaned up: no image or cache removal.
    control_verbs = {args[1] for args in created[1:] if len(args) > 1}
    assert control_verbs.isdisjoint({"rmi", "image", "volume", "system"})


@pytest.mark.asyncio
async def test_gpu_container_timeout_stops_the_run_owned_container_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[list[str]] = []
    _fake_docker(monkeypatch, created, run_never_completes=True)
    # Keep the post-stop reap bounded so the test does not wait a real grace
    # period for a container that never exits on its own.
    monkeypatch.setattr(
        "moonmind.workloads.docker_launcher._DEFAULT_KILL_GRACE_SECONDS",
        1,
    )
    validated = _validated(tmp_path)

    result = await DockerWorkloadLauncher().run(validated, timeout_seconds=0.01)

    assert result.status == "timed_out"
    assert result.metadata["workload"]["launchOutcome"] == "timed_out"
    assert result.metadata["workload"]["gpu"]["requested"] is True
    container = "mm-workload-task-gpu-step-render-1"
    assert validated.container_name == container
    assert ["docker", "stop", "-t", "30", container] in created
    assert ["docker", "kill", container] in created
    assert not any(
        args[1:2] in (["rmi"], ["volume"]) for args in created if len(args) > 1
    )


@pytest.mark.asyncio
async def test_gpu_container_cancellation_keeps_the_run_owned_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[list[str]] = []
    run_processes = _fake_docker(monkeypatch, created, run_never_completes=True)
    validated = _validated(tmp_path)

    task = asyncio.create_task(DockerWorkloadLauncher().run(validated))
    await asyncio.sleep(0)
    task.cancel()
    done, pending = await asyncio.wait({task})

    assert done == {task} and pending == set()
    assert task.cancelled()
    container = "mm-workload-task-gpu-step-render-1"
    assert ["docker", "stop", "-t", "30", container] in created
    assert ["docker", "kill", container] in created
    assert run_processes and run_processes[0].terminated


def test_retry_identity_is_attempt_owned_and_independent_of_gpu_resources() -> None:
    first = UnrestrictedContainerRequest.model_validate(_container_payload())
    retry = UnrestrictedContainerRequest.model_validate(
        _container_payload(attempt=2, resources={"gpu": {"vendor": "nvidia", "count": 8}})
    )
    cpu_only = UnrestrictedContainerRequest.model_validate(
        _container_payload(resources={"cpu": "2"})
    )

    assert first.container_name == "mm-workload-task-gpu-step-render-1"
    assert retry.container_name == "mm-workload-task-gpu-step-render-2"
    # Resource content never participates in the run-owned container identity.
    assert cpu_only.container_name == first.container_name


# --------------------------------------------------------------------------- #
# 5. Genericity guard
# --------------------------------------------------------------------------- #


# Word-boundary tokens plus literal substrings for the file-extension and
# repository-path forms.
FORBIDDEN_COUPLING_WORDS = (
    "tactics",
    "unreal",
    "ue4",
    "ue5",
    "cuda",
    "vulkan",
    "blender",
    "ubt",
    "ddc",
)
FORBIDDEN_COUPLING_SUBSTRINGS = (
    ".uproject",
    ".umap",
    "proof bundle",
    "moonladderstudios/",
)

@pytest.mark.parametrize("path", GENERIC_GPU_IMPLEMENTATION_FILES, ids=lambda p: p.name)
def test_generic_gpu_implementation_has_no_project_or_application_coupling(
    path: Path,
) -> None:
    text = path.read_text(encoding="utf-8").lower()

    for word in FORBIDDEN_COUPLING_WORDS:
        assert re.search(rf"\b{re.escape(word)}\b", text) is None, (
            f"{path.name} must stay application-neutral: {word!r}"
        )
    for substring in FORBIDDEN_COUPLING_SUBSTRINGS:
        assert substring not in text, (
            f"{path.name} must stay application-neutral: {substring!r}"
        )
