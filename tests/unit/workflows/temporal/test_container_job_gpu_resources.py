"""Canonical container-job GPU resource qualification.

Qualification for MoonLadderStudios/MoonMind#3779: a caller-supplied generic
GPU resource request must travel the canonical container-job contracts, be
realized by the trusted Docker backend as the vendor's device request, be
refused before the caller's workload with stable generic classifications, and be
observable as bounded evidence -- while CPU-only jobs stay byte-identical.

Every image, command, and capability value here is fixture data. MoonMind never
selects them and never interprets what the caller's workload does.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from moonmind.config.container_backend_settings import (
    resolve_container_backend_settings,
)
from moonmind.schemas.container_job_models import (
    GPU_FAILURE_CLASSES,
    ContainerJobActivityRequest,
    ContainerJobActivityResult,
    ContainerJobFailureClass,
    ContainerJobSubmitRequest,
    ContainerJobWorkflowInput,
    GpuObservation,
    ResolvedContainerLaunchPlan,
    failure_class_from_exception,
    gpu_failure_class_from_validation_error,
    terminal_gpu_observation,
)
from moonmind.schemas.workload_models import (
    WORKLOAD_GPU_CAPABILITIES,
    UnrestrictedContainerRequest,
    WorkloadGpuRequest,
)
from moonmind.workflows.temporal.container_job_backend import (
    DockerContainerJobBackend,
)
from moonmind.workflows.temporal.workflows.container_job import (
    MoonMindContainerJobWorkflow,
)
from moonmind.workloads.gpu import gpu_device_request_args

JOB_ID = "container-job:0123456789abcdef0123456789abcdef"

# Fixture data only: MoonMind never selects this image or command.
FIXTURE_IMAGE = "docker.io/library/qualification-fixture:1.0.0"
FIXTURE_COMMAND = ("sh", "-lc", "probe --emit report.json")

# Generic vendor/runtime diagnostics a daemon emits when it refuses a device
# request. Each is host evidence, never a MoonMind condition.
RUNTIME_UNAVAILABLE_STDERR = (
    b"docker: Error response from daemon: failed to create task: "
    b"nvidia-container-cli: initialization error\n"
)
DEVICE_UNAVAILABLE_STDERR = (
    b"docker: Error response from daemon: failed to initialize NVML: "
    b"Unknown Error\n"
)
# An installed runtime with no usable device reports the device failure through
# the vendor's own CLI, so this diagnostic names both the generic CLI prefix and
# the specific device condition.
RUNTIME_PRESENT_DEVICE_UNAVAILABLE_STDERR = (
    b"docker: Error response from daemon: failed to create task: "
    b"nvidia-container-cli: device error: failed to initialize NVML: "
    b"no devices were found\n"
)
DRIVER_UNSELECTABLE_STDERR = (
    b'docker: Error response from daemon: could not select device driver "" '
    b"with capabilities: [[gpu]]\n"
)
DEVICE_REQUEST_UNSUPPORTED_STDERR = b"unknown flag: --gpus\n"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _spec(**overrides: Any) -> dict[str, Any]:
    spec: dict[str, Any] = {
        "image": FIXTURE_IMAGE,
        "workspaceRef": {"kind": "sandbox", "workspaceId": "art_workspace"},
        "command": list(FIXTURE_COMMAND),
        "resources": {"cpuMillis": 1000, "memoryMiB": 512},
        "timeoutSeconds": 60,
    }
    spec.update(overrides)
    return spec


def _submission(*, gpu: dict[str, Any] | None = None) -> ContainerJobSubmitRequest:
    resources: dict[str, Any] = {"cpuMillis": 1000, "memoryMiB": 512}
    if gpu is not None:
        resources["gpu"] = gpu
    return ContainerJobSubmitRequest.model_validate(
        {
            "idempotencyKey": "issue-3779",
            "source": {"source": "workflow", "workflowId": "mm:3779"},
            "spec": _spec(resources=resources),
        }
    )


def _activity_request(
    tmp_path, *, gpu: dict[str, Any] | None = None
) -> ContainerJobActivityRequest:
    resources: dict[str, Any] = {"cpuMillis": 1000, "memoryMiB": 512}
    if gpu is not None:
        resources["gpu"] = gpu
    return ContainerJobActivityRequest.model_validate(
        {
            "jobId": JOB_ID,
            "ownershipToken": f"{JOB_ID}:v1",
            "request": _submission(gpu=gpu).model_dump(
                mode="json", by_alias=True, exclude_none=True
            ),
            "resolvedWorkspaceRef": str(tmp_path / "art_workspace"),
            "resolvedImageRef": "sha256:" + "a" * 64,
        }
    )


def _workflow_input(*, gpu: dict[str, Any] | None = None) -> dict[str, Any]:
    return ContainerJobWorkflowInput.model_validate(
        {
            "jobId": JOB_ID,
            "observeIntervalSeconds": 1,
            "request": _submission(gpu=gpu).model_dump(
                mode="json", by_alias=True, exclude_none=True
            ),
        }
    ).model_dump(mode="json", by_alias=True, exclude_none=True)


def _runner(
    commands: list[tuple[str, ...]],
    *,
    server_version: bytes = b"27.0.0",
    failures: dict[str, tuple[int, bytes]] | None = None,
):
    """Return a recording Docker runner with optional per-command failures."""

    failures = failures or {}

    async def runner(args):
        args = tuple(args)
        commands.append(args)
        if args[:3] == ("inspect", "--format", "{{json .Config.Labels}}"):
            return 1, b"", b"no such container"
        if args[0] == "version":
            return 0, server_version, b""
        if args[0] in failures:
            code, stderr = failures[args[0]]
            return code, b"", stderr
        if args[0] == "info":
            return 0, str(64 * 1024**3).encode(), b""
        return 0, b"", b""

    return runner


def _backend(tmp_path, runner, **kwargs) -> DockerContainerJobBackend:
    (tmp_path / "art_workspace").mkdir(exist_ok=True)
    return DockerContainerJobBackend(
        workspace_root=tmp_path, command_runner=runner, **kwargs
    )


def _device_request(create: tuple[str, ...]) -> str:
    return create[create.index("--gpus") + 1]


# ---------------------------------------------------------------------------
# Request contract
# ---------------------------------------------------------------------------


def test_canonical_spec_accepts_the_generic_vendor_count_and_capabilities() -> None:
    submission = _submission(
        gpu={
            "vendor": "nvidia",
            "count": "all",
            "capabilities": ["graphics", "compute", "utility"],
        }
    )

    gpu = submission.spec.resources.gpu
    assert gpu is not None
    assert gpu.vendor == "nvidia"
    assert gpu.count == "all"
    # Deduplicated and canonically ordered, so one semantic request always
    # serializes to the same durable bytes.
    assert gpu.capabilities == ("compute", "graphics", "utility")


def test_capability_request_serialization_is_deterministic() -> None:
    first = _submission(gpu={"capabilities": ["utility", "compute", "utility"]})
    second = _submission(gpu={"capabilities": ["compute", "utility"]})

    assert first.model_dump(mode="json", by_alias=True, exclude_none=True) == (
        second.model_dump(mode="json", by_alias=True, exclude_none=True)
    )
    assert first.model_dump(mode="json", by_alias=True, exclude_none=True)["spec"][
        "resources"
    ]["gpu"] == {"vendor": "nvidia", "count": "all", "capabilities": ["compute", "utility"]}


def test_omitted_capabilities_stay_absent_from_the_durable_request() -> None:
    """An omitted optional field must not change already-recorded bytes."""

    payload = _submission(gpu={"count": 2}).model_dump(
        mode="json", by_alias=True, exclude_none=True
    )

    assert payload["spec"]["resources"]["gpu"] == {"vendor": "nvidia", "count": 2}


def test_cpu_only_request_serialization_is_unchanged() -> None:
    payload = _submission().model_dump(mode="json", by_alias=True, exclude_none=True)

    assert "gpu" not in payload["spec"]["resources"]


@pytest.mark.parametrize(
    "gpu",
    [
        {"capabilities": []},
        {"capabilities": "compute"},
        {"capabilities": ["render"]},
        {"capabilities": [1]},
        {"capabilities": ["compute"], "deviceIds": [0]},
    ],
)
def test_malformed_capability_request_is_refused_as_an_invalid_gpu_request(
    gpu: dict[str, Any],
) -> None:
    with pytest.raises(Exception) as exc_info:
        _submission(gpu=gpu)

    assert (
        gpu_failure_class_from_validation_error(exc_info.value)
        == ContainerJobFailureClass.GPU_REQUEST_INVALID
    )


@pytest.mark.parametrize(
    ("gpu", "expected"),
    [
        ({"vendor": "amd"}, ContainerJobFailureClass.GPU_VENDOR_UNSUPPORTED),
        ({"vendor": "gpu"}, ContainerJobFailureClass.GPU_VENDOR_UNSUPPORTED),
        ({"count": 0}, ContainerJobFailureClass.GPU_COUNT_UNSUPPORTED),
        ({"count": -1}, ContainerJobFailureClass.GPU_COUNT_UNSUPPORTED),
        ({"count": "2"}, ContainerJobFailureClass.GPU_COUNT_UNSUPPORTED),
        ({"count": True}, ContainerJobFailureClass.GPU_COUNT_UNSUPPORTED),
        ({"count": "most"}, ContainerJobFailureClass.GPU_COUNT_UNSUPPORTED),
    ],
)
def test_unsupported_vendor_and_count_carry_their_stable_class(
    gpu: dict[str, Any], expected: ContainerJobFailureClass
) -> None:
    with pytest.raises(Exception) as exc_info:
        _submission(gpu=gpu)

    assert gpu_failure_class_from_validation_error(exc_info.value) == expected


def test_public_contract_still_forbids_device_and_authority_fields() -> None:
    for forbidden in ({"devices": ["/dev/nvidia0"]}, {"privileged": True}):
        with pytest.raises(Exception):
            ContainerJobSubmitRequest.model_validate(
                {
                    "idempotencyKey": "issue-3779",
                    "source": {"source": "workflow"},
                    "spec": _spec(
                        resources={
                            "cpuMillis": 1000,
                            "memoryMiB": 512,
                            "gpu": {"count": "all", **forbidden},
                        }
                    ),
                }
            )


def test_gpu_request_survives_temporal_payloads_and_the_launch_plan() -> None:
    gpu = {"vendor": "nvidia", "count": 2, "capabilities": ["compute", "utility"]}
    workflow_input = _workflow_input(gpu=gpu)

    replayed = ContainerJobWorkflowInput.model_validate(
        json.loads(json.dumps(workflow_input))
    )
    plan = ResolvedContainerLaunchPlan(
        jobId=JOB_ID,
        backendKind="docker-engine",
        backendRef="system",
        resolvedWorkspaceRef="/workspaces/art_workspace",
        spec=replayed.request.spec,
    )
    round_tripped = ResolvedContainerLaunchPlan.model_validate(
        json.loads(json.dumps(plan.model_dump(mode="json", by_alias=True)))
    )

    assert replayed.request.spec.resources.gpu == WorkloadGpuRequest.model_validate(gpu)
    assert round_tripped.spec.resources.gpu == replayed.request.spec.resources.gpu


def test_gpu_observation_crosses_the_activity_boundary_unchanged() -> None:
    observation = GpuObservation(
        vendor="nvidia",
        count="all",
        capabilities=("compute", "utility"),
        backendSupported=True,
        launched=True,
    )
    result = ContainerJobActivityResult(gpuObservation=observation)

    replayed = ContainerJobActivityResult.model_validate(
        json.loads(json.dumps(result.model_dump(mode="json", by_alias=True)))
    )

    assert replayed.gpu_observation == observation


# ---------------------------------------------------------------------------
# Trusted Docker backend
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("gpu", "expected"),
    [
        ({"count": "all"}, "all"),
        ({"count": 2}, "2"),
        (
            {"count": "all", "capabilities": ["compute", "utility"]},
            'count=all,"capabilities=compute,utility"',
        ),
        (
            {"count": 3, "capabilities": ["utility", "compute", "graphics"]},
            'count=3,"capabilities=compute,graphics,utility"',
        ),
    ],
)
async def test_backend_realizes_the_requested_device_request(
    tmp_path, gpu: dict[str, Any], expected: str
) -> None:
    commands: list[tuple[str, ...]] = []
    backend = _backend(tmp_path, _runner(commands))

    result = await backend.create_container(_activity_request(tmp_path, gpu=gpu))

    create = next(command for command in commands if command[0] == "create")
    assert _device_request(create) == expected
    assert result.gpu_observation is not None
    assert result.gpu_observation.backend_supported is True
    assert result.gpu_observation.launched is False


@pytest.mark.asyncio
async def test_device_request_needs_no_privileged_mode_or_device_mount(
    tmp_path,
) -> None:
    commands: list[tuple[str, ...]] = []
    backend = _backend(tmp_path, _runner(commands))

    await backend.create_container(
        _activity_request(tmp_path, gpu={"count": "all", "capabilities": ["compute"]})
    )

    create = next(command for command in commands if command[0] == "create")
    assert "--device" not in create
    assert "--privileged" not in create
    assert "--privileged=false" in create
    assert not any(token.startswith("/dev/") for token in create)


@pytest.mark.asyncio
async def test_backend_reports_support_before_creating_the_container(
    tmp_path,
) -> None:
    commands: list[tuple[str, ...]] = []
    backend = _backend(tmp_path, _runner(commands))

    await backend.create_container(_activity_request(tmp_path, gpu={"count": "all"}))

    families = [command[0] for command in commands]
    assert families.index("version") < families.index("create")


@pytest.mark.asyncio
async def test_cpu_only_job_reports_no_gpu_support_and_requests_no_device(
    tmp_path,
) -> None:
    commands: list[tuple[str, ...]] = []
    backend = _backend(tmp_path, _runner(commands))

    result = await backend.create_container(_activity_request(tmp_path))

    create = next(command for command in commands if command[0] == "create")
    assert "--gpus" not in create
    assert result.gpu_observation is None
    assert not any(command[0] == "version" for command in commands)


@pytest.mark.asyncio
async def test_daemon_without_device_request_support_is_refused_before_create(
    tmp_path,
) -> None:
    commands: list[tuple[str, ...]] = []
    backend = _backend(tmp_path, _runner(commands, server_version=b"18.09.9"))

    with pytest.raises(Exception) as exc_info:
        await backend.create_container(_activity_request(tmp_path, gpu={"count": 1}))

    assert (
        failure_class_from_exception(exc_info.value)
        == ContainerJobFailureClass.GPU_BACKEND_UNSUPPORTED
    )
    assert not any(command[0] == "create" for command in commands)


@pytest.mark.asyncio
async def test_unreachable_endpoint_never_echoes_the_daemon_diagnostic(
    tmp_path,
) -> None:
    """The support probe's caller-visible message is a fixed string.

    ``docker version`` stderr can name the deployment-owned daemon URI, its
    certificate paths, or credential-bearing connection detail, and this message
    becomes the durable terminal outcome the status API projects.
    """

    commands: list[tuple[str, ...]] = []
    endpoint_detail = (
        b"Cannot connect to the Docker daemon at "
        b"tcp://gpu-host.internal:2376 (client cert "
        b"/etc/docker/certs.d/client.pem)\n"
    )

    async def runner(args):
        args = tuple(args)
        commands.append(args)
        if args[0] == "version":
            return 1, b"", endpoint_detail
        return 0, b"", b""

    backend = _backend(tmp_path, runner)

    with pytest.raises(Exception) as exc_info:
        await backend.create_container(_activity_request(tmp_path, gpu={"count": 1}))

    assert (
        failure_class_from_exception(exc_info.value)
        == ContainerJobFailureClass.INFRASTRUCTURE
    )
    message = str(exc_info.value)
    for secret in ("gpu-host.internal", "2376", "certs.d", "client.pem", "tcp://"):
        assert secret not in message
    assert not any(command[0] == "create" for command in commands)


@pytest.mark.asyncio
async def test_device_count_above_the_deployment_ceiling_is_refused(tmp_path) -> None:
    commands: list[tuple[str, ...]] = []
    bounded = resolve_container_backend_settings(
        {"MOONMIND_CONTAINER_BACKEND_MAX_GPU_COUNT": "1"}
    )
    backend = _backend(tmp_path, _runner(commands), settings=bounded)

    for requested in (2, "all"):
        with pytest.raises(Exception) as exc_info:
            await backend.create_container(
                _activity_request(tmp_path, gpu={"count": requested})
            )
        assert (
            failure_class_from_exception(exc_info.value)
            == ContainerJobFailureClass.GPU_COUNT_UNSUPPORTED
        )
    assert not any(command[0] == "create" for command in commands)


@pytest.mark.asyncio
async def test_backend_refuses_a_vendor_it_cannot_realize(tmp_path) -> None:
    """The backend is its own authority, not only the request contract."""

    commands: list[tuple[str, ...]] = []
    backend = _backend(tmp_path, _runner(commands))
    request = _activity_request(tmp_path, gpu={"count": 1})
    object.__setattr__(request.request.spec.resources.gpu, "vendor", "amd")

    with pytest.raises(Exception) as exc_info:
        await backend.create_container(request)

    assert (
        failure_class_from_exception(exc_info.value)
        == ContainerJobFailureClass.GPU_VENDOR_UNSUPPORTED
    )
    assert not any(command[0] == "create" for command in commands)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stderr", "expected"),
    [
        (
            RUNTIME_UNAVAILABLE_STDERR,
            ContainerJobFailureClass.GPU_RUNTIME_UNAVAILABLE,
        ),
        (
            DEVICE_UNAVAILABLE_STDERR,
            ContainerJobFailureClass.GPU_RESOURCE_UNAVAILABLE,
        ),
        (
            # A working runtime with no usable device must direct the operator
            # at device capacity, not at repairing the runtime.
            RUNTIME_PRESENT_DEVICE_UNAVAILABLE_STDERR,
            ContainerJobFailureClass.GPU_RESOURCE_UNAVAILABLE,
        ),
        (
            DRIVER_UNSELECTABLE_STDERR,
            ContainerJobFailureClass.GPU_BACKEND_UNSUPPORTED,
        ),
        (
            DEVICE_REQUEST_UNSUPPORTED_STDERR,
            ContainerJobFailureClass.GPU_BACKEND_UNSUPPORTED,
        ),
    ],
)
async def test_start_refusal_is_classified_before_caller_execution(
    tmp_path, stderr: bytes, expected: ContainerJobFailureClass
) -> None:
    commands: list[tuple[str, ...]] = []
    backend = _backend(
        tmp_path, _runner(commands, failures={"start": (125, stderr)})
    )
    request = _activity_request(tmp_path, gpu={"count": "all"})
    request.container_ref = "moonmind-container-job-owned"

    with pytest.raises(Exception) as exc_info:
        await backend.start_container(request)

    assert failure_class_from_exception(exc_info.value) == expected
    assert failure_class_from_exception(exc_info.value) in GPU_FAILURE_CLASSES
    # The daemon diagnostic can name trusted host paths; it never leaves here.
    assert "Error response from daemon" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_create_refusal_of_the_device_request_is_classified(tmp_path) -> None:
    commands: list[tuple[str, ...]] = []
    backend = _backend(
        tmp_path,
        _runner(commands, failures={"create": (125, DRIVER_UNSELECTABLE_STDERR)}),
    )

    with pytest.raises(Exception) as exc_info:
        await backend.create_container(_activity_request(tmp_path, gpu={"count": 1}))

    assert (
        failure_class_from_exception(exc_info.value)
        == ContainerJobFailureClass.GPU_BACKEND_UNSUPPORTED
    )


@pytest.mark.asyncio
async def test_non_gpu_launch_failure_stays_a_generic_launch_failure(
    tmp_path,
) -> None:
    commands: list[tuple[str, ...]] = []
    backend = _backend(
        tmp_path,
        _runner(
            commands,
            failures={
                "create": (
                    125,
                    b"docker: Error response from daemon: invalid mount config\n",
                )
            },
        ),
    )

    with pytest.raises(RuntimeError, match="docker create failed") as exc_info:
        await backend.create_container(_activity_request(tmp_path, gpu={"count": 1}))

    assert failure_class_from_exception(exc_info.value) is None


@pytest.mark.asyncio
async def test_started_gpu_container_records_launch_evidence(tmp_path) -> None:
    commands: list[tuple[str, ...]] = []
    backend = _backend(tmp_path, _runner(commands))
    request = _activity_request(
        tmp_path, gpu={"count": 2, "capabilities": ["compute"]}
    )
    request.container_ref = "moonmind-container-job-owned"

    result = await backend.start_container(request)

    assert result.gpu_observation is not None
    assert result.gpu_observation.launched is True
    assert result.gpu_observation.count == 2
    assert result.gpu_observation.capabilities == ("compute",)


@pytest.mark.asyncio
async def test_cleanup_of_a_gpu_job_removes_only_the_run_owned_container(
    tmp_path,
) -> None:
    commands: list[tuple[str, ...]] = []
    backend = _backend(tmp_path, _runner(commands))
    request = _activity_request(
        tmp_path, gpu={"count": "all", "capabilities": ["compute"]}
    )
    request.container_ref = "moonmind-container-job-owned"

    await backend.cleanup(request)

    assert not any(command[0] in {"rmi", "image"} for command in commands)
    assert not any(
        command[:2] == ("volume", "rm") for command in commands
    )


# ---------------------------------------------------------------------------
# Durable workflow boundary
# ---------------------------------------------------------------------------


def _result_for(name: str) -> ContainerJobActivityResult:
    if name == "container_job.resolve_workspace":
        return ContainerJobActivityResult(
            resolvedWorkspaceRef="/workspaces/art_workspace",
            workspaceProbe="visible",
        )
    if name == "container_job.acquire_image":
        return ContainerJobActivityResult(resolvedImageRef="sha256:" + "a" * 64)
    if name == "container_job.reconcile_container":
        return ContainerJobActivityResult(containerRef=None, running=False)
    if name == "container_job.create_container":
        return ContainerJobActivityResult(containerRef="owned:3779")
    if name == "container_job.observe_container":
        return ContainerJobActivityResult(terminalState="succeeded", exitCode=0)
    return ContainerJobActivityResult()


@pytest.mark.asyncio
async def test_terminal_projection_carries_the_resolved_gpu_observation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = MoonMindContainerJobWorkflow()
    projections: list[ContainerJobActivityRequest] = []

    async def activity(name, request):
        if name == "container_job.project_status":
            projections.append(request.model_copy(deep=True))
        if name == "container_job.create_container":
            return ContainerJobActivityResult(
                containerRef="owned:3779",
                gpuObservation=GpuObservation(
                    vendor="nvidia",
                    count="all",
                    capabilities=("compute", "utility"),
                    backendSupported=True,
                ),
            )
        if name == "container_job.start_container":
            return ContainerJobActivityResult(
                containerRef="owned:3779",
                running=True,
                gpuObservation=GpuObservation(
                    vendor="nvidia",
                    count="all",
                    capabilities=("compute", "utility"),
                    backendSupported=True,
                    launched=True,
                ),
            )
        return _result_for(name)

    monkeypatch.setattr(job, "_activity", activity)
    result = await job.run(
        _workflow_input(gpu={"count": "all", "capabilities": ["compute", "utility"]})
    )

    assert result["state"] == "succeeded"
    terminal = projections[-1]
    assert terminal.gpu_observation is not None
    assert terminal.gpu_observation.launched is True
    assert terminal.gpu_observation.backend_supported is True
    assert terminal.gpu_observation.capabilities == ("compute", "utility")
    assert terminal.gpu_observation.failure_class is None


@pytest.mark.asyncio
async def test_refusal_before_create_still_projects_the_requested_resource(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from moonmind.schemas.container_job_models import ContainerJobBackendError

    job = MoonMindContainerJobWorkflow()
    projections: list[ContainerJobActivityRequest] = []

    async def activity(name, request):
        if name == "container_job.project_status":
            projections.append(request.model_copy(deep=True))
            return ContainerJobActivityResult()
        if name == "container_job.create_container":
            raise ContainerJobBackendError(
                ContainerJobFailureClass.GPU_RUNTIME_UNAVAILABLE,
                "selected container backend refused the requested GPU resource",
            )
        return _result_for(name)

    monkeypatch.setattr(job, "_activity", activity)
    result = await job.run(_workflow_input(gpu={"count": 4}))

    assert result["state"] == "failed"
    assert result["terminal"]["failureClass"] == "gpu_runtime_unavailable"
    terminal = projections[-1]
    assert terminal.gpu_observation is not None
    assert terminal.gpu_observation.count == 4
    assert terminal.gpu_observation.launched is False
    # The refusal is only reachable after the backend reported support, so the
    # durable status must not contradict evidence obtained at that boundary even
    # though the activity raised before it could return an observation.
    assert terminal.gpu_observation.backend_supported is True
    assert (
        terminal.gpu_observation.failure_class
        == ContainerJobFailureClass.GPU_RUNTIME_UNAVAILABLE
    )


@pytest.mark.asyncio
async def test_cpu_only_job_projects_no_gpu_observation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = MoonMindContainerJobWorkflow()
    projections: list[ContainerJobActivityRequest] = []

    async def activity(name, request):
        if name == "container_job.project_status":
            projections.append(request.model_copy(deep=True))
        return _result_for(name)

    monkeypatch.setattr(job, "_activity", activity)
    result = await job.run(_workflow_input())

    assert result["state"] == "succeeded"
    assert all(
        projection.gpu_observation is None for projection in projections
    )


@pytest.mark.parametrize(
    ("failure_class", "expected_support"),
    [
        (ContainerJobFailureClass.GPU_RUNTIME_UNAVAILABLE, True),
        (ContainerJobFailureClass.GPU_RESOURCE_UNAVAILABLE, True),
        # Reachable from the pre-create support report itself, so support was
        # never established and must stay unknown.
        (ContainerJobFailureClass.GPU_BACKEND_UNSUPPORTED, None),
        (ContainerJobFailureClass.GPU_VENDOR_UNSUPPORTED, None),
        (ContainerJobFailureClass.GPU_COUNT_UNSUPPORTED, None),
    ],
)
def test_a_refusal_reports_support_only_when_the_backend_established_it(
    failure_class: ContainerJobFailureClass, expected_support: bool | None
) -> None:
    """A refused job's durable status agrees with the evidence it obtained."""

    observation = terminal_gpu_observation(
        gpu=WorkloadGpuRequest(count=2),
        observed=None,
        failure_class=failure_class,
    )

    assert observation is not None
    assert observation.backend_supported is expected_support
    assert observation.launched is False
    assert observation.failure_class == failure_class


def test_an_ordinary_execution_failure_is_not_reported_as_a_gpu_failure() -> None:
    observation = terminal_gpu_observation(
        gpu=WorkloadGpuRequest(count=1),
        observed=GpuObservation(
            vendor="nvidia", count=1, backendSupported=True, launched=True
        ),
        failure_class=ContainerJobFailureClass.EXECUTION,
    )

    assert observation is not None
    assert observation.failure_class is None
    assert observation.launched is True


# ---------------------------------------------------------------------------
# One request contract across both container boundaries
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_canonical_and_unrestricted_paths_realize_one_device_request(
    tmp_path,
) -> None:
    """A repository skill can move boundaries without changing GPU semantics."""

    gpu = {"vendor": "nvidia", "count": 2, "capabilities": ["compute", "utility"]}
    paths = {
        name: tmp_path / name for name in ("repo", "artifacts", "scratch")
    }
    for path in paths.values():
        path.mkdir(exist_ok=True)
    unrestricted = UnrestrictedContainerRequest.model_validate(
        {
            "toolName": "container.run_container",
            "agentRunId": "task-3779",
            "stepId": "gpu-step",
            "attempt": 1,
            "repoDir": str(paths["repo"]),
            "artifactsDir": str(paths["artifacts"]),
            "scratchDir": str(paths["scratch"]),
            "image": FIXTURE_IMAGE,
            "command": list(FIXTURE_COMMAND),
            "networkMode": "none",
            "timeoutSeconds": 60,
            "resources": {"gpu": gpu},
        }
    )
    canonical = _submission(gpu=gpu)

    # One semantic request, one realization, on both boundaries.
    assert unrestricted.resources.gpu == canonical.spec.resources.gpu
    expected = gpu_device_request_args(canonical.spec.resources.gpu)

    commands: list[tuple[str, ...]] = []
    backend = _backend(tmp_path, _runner(commands))
    await backend.create_container(_activity_request(tmp_path, gpu=gpu))
    create = next(command for command in commands if command[0] == "create")
    assert [
        "--gpus",
        _device_request(create),
    ] == expected

    # The image and command the caller supplied are unchanged on both paths.
    assert canonical.spec.image == unrestricted.image == FIXTURE_IMAGE
    assert list(canonical.spec.command) == list(unrestricted.command)
    assert create[-len(FIXTURE_COMMAND) :] == FIXTURE_COMMAND


def test_status_contract_projects_one_bounded_gpu_observation() -> None:
    from datetime import datetime, timezone

    from moonmind.schemas.container_job_models import ContainerJobStatus

    status = ContainerJobStatus(
        jobId=JOB_ID,
        state="succeeded",
        gpu=GpuObservation(
            vendor="nvidia", count="all", backendSupported=True, launched=True
        ),
        updatedAt=datetime.now(timezone.utc),
    )

    payload = status.model_dump(mode="json", by_alias=True, exclude_none=True)
    assert payload["gpu"] == {
        "vendor": "nvidia",
        "count": "all",
        "backendSupported": True,
        "launched": True,
    }


def test_capability_vocabulary_is_bounded_and_vendor_neutral() -> None:
    """The bounded list is driver capability names, not MoonMind vocabulary."""

    assert WORKLOAD_GPU_CAPABILITIES == (
        "compute",
        "compat32",
        "graphics",
        "utility",
        "video",
        "display",
    )


@pytest.mark.asyncio
async def test_reattached_gpu_container_reports_its_realized_resource(
    tmp_path,
) -> None:
    """A reconciled container skips create, so it reports the evidence itself."""

    commands: list[tuple[str, ...]] = []
    ownership = f"{JOB_ID}:v1".encode()

    async def runner(args):
        args = tuple(args)
        commands.append(args)
        if args[:3] == ("inspect", "--format", "{{json .Config.Labels}}"):
            return 0, b'{"moonmind.ownership":"' + ownership + b'"}', b""
        if args[:3] == ("inspect", "--format", "{{.State.Running}}"):
            return 0, b"true", b""
        return 0, b"", b""

    backend = _backend(tmp_path, runner)

    result = await backend.reconcile_container(
        _activity_request(tmp_path, gpu={"count": 2, "capabilities": ["compute"]})
    )

    assert result.gpu_observation is not None
    assert result.gpu_observation.launched is True
    assert result.gpu_observation.backend_supported is True
    assert result.gpu_observation.count == 2
    assert result.gpu_observation.capabilities == ("compute",)


@pytest.mark.asyncio
async def test_reattached_container_projection_survives_a_skipped_create(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = MoonMindContainerJobWorkflow()
    projections: list[ContainerJobActivityRequest] = []
    created = 0

    async def activity(name, request):
        nonlocal created
        if name == "container_job.project_status":
            projections.append(request.model_copy(deep=True))
        if name == "container_job.create_container":
            created += 1
        if name == "container_job.reconcile_container":
            return ContainerJobActivityResult(
                containerRef="owned:3779",
                running=True,
                gpuObservation=GpuObservation(
                    vendor="nvidia",
                    count="all",
                    backendSupported=True,
                    launched=True,
                ),
            )
        return _result_for(name)

    monkeypatch.setattr(job, "_activity", activity)
    result = await job.run(_workflow_input(gpu={"count": "all"}))

    assert result["state"] == "succeeded"
    assert created == 0
    assert projections[-1].gpu_observation is not None
    assert projections[-1].gpu_observation.launched is True


@pytest.mark.asyncio
async def test_reconcile_reports_no_gpu_evidence_for_a_cpu_only_job(tmp_path) -> None:
    commands: list[tuple[str, ...]] = []
    ownership = f"{JOB_ID}:v1".encode()

    async def runner(args):
        args = tuple(args)
        commands.append(args)
        if args[:3] == ("inspect", "--format", "{{json .Config.Labels}}"):
            return 0, b'{"moonmind.ownership":"' + ownership + b'"}', b""
        if args[:3] == ("inspect", "--format", "{{.State.Running}}"):
            return 0, b"true", b""
        return 0, b"", b""

    backend = _backend(tmp_path, runner)

    result = await backend.reconcile_container(_activity_request(tmp_path))

    assert result.gpu_observation is None


# ---------------------------------------------------------------------------
# Parent workflow tool dispatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_job_dispatch_submits_and_reports_the_gpu_resource(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The parent workflow's container tool carries the request and evidence."""

    from datetime import datetime, timezone

    from moonmind.workflows.temporal.workflows import run as run_workflow_module
    from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow

    submitted: list[dict[str, Any]] = []
    job_id = JOB_ID

    async def fake_execute_activity(activity_type, payload, **kwargs):
        if activity_type == "container_job.submit":
            submitted.append(payload)
            return {"jobId": job_id, "state": "queued"}
        if activity_type == "container_job.status":
            return {
                "jobId": job_id,
                "state": "failed",
                "terminal": {
                    "exitCode": None,
                    "failureClass": "gpu_resource_unavailable",
                    "message": "selected container backend refused the requested "
                    "GPU resource (gpu_device_unavailable)",
                },
                "gpu": {
                    "vendor": "nvidia",
                    "count": 2,
                    "capabilities": ["compute", "utility"],
                    "backendSupported": True,
                    "launched": False,
                    "failureClass": "gpu_resource_unavailable",
                },
            }
        raise AssertionError(f"unexpected activity: {activity_type}")

    workflow = MoonMindRunWorkflow()
    workflow._owner_id = "owner-1"
    monkeypatch.setattr(
        run_workflow_module.workflow, "execute_activity", fake_execute_activity
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "info",
        type(
            "WorkflowInfo",
            (),
            {
                "namespace": "default",
                "workflow_id": "wf-3779",
                "run_id": "run-1",
                "search_attributes": {},
            },
        ),
    )
    monkeypatch.setattr(
        run_workflow_module.workflow, "now", lambda: datetime.now(timezone.utc)
    )

    result = await workflow._execute_container_job_tool(
        node_inputs={
            "idempotencyKey": "wf-3779:gpu-step:1",
            "spec": {
                "image": FIXTURE_IMAGE,
                "command": list(FIXTURE_COMMAND),
                "workspaceRef": {"kind": "sandbox", "workspaceId": "run"},
                "resources": {
                    "cpuMillis": 1000,
                    "memoryMiB": 512,
                    "gpu": {
                        "vendor": "nvidia",
                        "count": 2,
                        "capabilities": ["utility", "compute"],
                    },
                },
            },
        },
        node_id="gpu-step",
        execution_ordinal=1,
    )

    assert len(submitted) == 1
    # The submitted request carries the caller's normalized GPU resource, and
    # the caller's image and command are untouched.
    spec = submitted[0]["request"]["spec"]
    assert spec["resources"]["gpu"] == {
        "vendor": "nvidia",
        "count": 2,
        "capabilities": ["compute", "utility"],
    }
    assert spec["image"] == FIXTURE_IMAGE
    assert spec["command"] == list(FIXTURE_COMMAND)

    assert result["status"] == "FAILED"
    assert result["outputs"]["failureClass"] == "gpu_resource_unavailable"
    assert result["outputs"]["gpu"]["launched"] is False
    assert result["outputs"]["gpu"]["capabilities"] == ["compute", "utility"]


@pytest.mark.asyncio
async def test_run_job_dispatch_reports_no_gpu_output_for_a_cpu_only_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import datetime, timezone

    from moonmind.workflows.temporal.workflows import run as run_workflow_module
    from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow

    async def fake_execute_activity(activity_type, payload, **kwargs):
        if activity_type == "container_job.submit":
            return {"jobId": JOB_ID, "state": "queued"}
        if activity_type == "container_job.status":
            return {
                "jobId": JOB_ID,
                "state": "succeeded",
                "terminal": {"exitCode": 0},
            }
        raise AssertionError(f"unexpected activity: {activity_type}")

    workflow = MoonMindRunWorkflow()
    workflow._owner_id = "owner-1"
    monkeypatch.setattr(
        run_workflow_module.workflow, "execute_activity", fake_execute_activity
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "info",
        type(
            "WorkflowInfo",
            (),
            {
                "namespace": "default",
                "workflow_id": "wf-3779",
                "run_id": "run-1",
                "search_attributes": {},
            },
        ),
    )
    monkeypatch.setattr(
        run_workflow_module.workflow, "now", lambda: datetime.now(timezone.utc)
    )

    result = await workflow._execute_container_job_tool(
        node_inputs={
            "idempotencyKey": "wf-3779:cpu-step:1",
            "spec": {
                "image": FIXTURE_IMAGE,
                "command": list(FIXTURE_COMMAND),
                "workspaceRef": {"kind": "sandbox", "workspaceId": "run"},
                "resources": {"cpuMillis": 1000, "memoryMiB": 512},
            },
        },
        node_id="cpu-step",
        execution_ordinal=1,
    )

    assert result["status"] == "COMPLETED"
    assert "gpu" not in result["outputs"]


def test_run_job_tool_schema_publishes_the_capability_and_observation_fields() -> None:
    from moonmind.workloads.tool_bridge import (
        build_container_job_tool_definition_payload,
    )

    payload = build_container_job_tool_definition_payload(name="container.run_job")
    gpu_input = payload["inputs"]["schema"]["properties"]["spec"]["properties"][
        "resources"
    ]["properties"]["gpu"]
    gpu_output = payload["outputs"]["schema"]["properties"]["gpu"]

    assert gpu_input["properties"]["capabilities"]["items"]["enum"] == list(
        WORKLOAD_GPU_CAPABILITIES
    )
    assert gpu_input["additionalProperties"] is False
    assert set(gpu_output["properties"]) == {
        "vendor",
        "count",
        "capabilities",
        "backendSupported",
        "launched",
        "failureClass",
    }
    # The workflow-facing contract exposes no Docker flag or device authority.
    for schema in (gpu_input, gpu_output):
        assert "devices" not in schema["properties"]
        assert "deviceRequestArgs" not in schema["properties"]
