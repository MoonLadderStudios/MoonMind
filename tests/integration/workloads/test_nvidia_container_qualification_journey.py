"""Real NVIDIA container qualification journey.

Qualification layer 2 for MoonLadderStudios/MoonMind#3777. This journey runs a
caller-supplied NVIDIA GPU container on a deployment-owned GPU host through the
same trusted MoonMind Docker boundary used in production, and publishes a
compact generic qualification record.

It is marked ``requires_gpu`` and is excluded from required CI. A CPU-only
runner skips every test with an explicit environment reason. Run it with::

    ./tools/test_gpu_qualification.sh

or, on the GPU host directly::

    MOONMIND_GPU_QUALIFICATION_IMAGE=<image> \\
      pytest tests/integration/workloads -m requires_gpu

Configuration (test configuration only — none of these are MoonMind product
settings, and no MoonMind module reads them):

``MOONMIND_GPU_QUALIFICATION_IMAGE``
    Required. The container image the caller submits, with an explicit tag or
    digest.
``MOONMIND_GPU_QUALIFICATION_COMMAND``
    Optional JSON array overriding the caller-supplied command.
``MOONMIND_GPU_QUALIFICATION_COMMAND_WRITES_OUTPUT``
    Optional. Set to ``1`` when a command override writes the declared-output
    path, so the journey declares and checksums that output for the override too.
``MOONMIND_GPU_QUALIFICATION_GPU_COUNT``
    Optional ``all`` (default) or a positive integer.
``MOONMIND_GPU_QUALIFICATION_WORKSPACE_ROOT``
    Optional workspace root visible at the same absolute path to both this
    process and the Docker daemon.
``MOONMIND_GPU_QUALIFICATION_CACHE_VOLUME``
    Optional named volume used for the warm-reuse leg.
``MOONMIND_GPU_QUALIFICATION_DOCKER_HOST``
    Optional Docker endpoint. Every workload launch and every helper invocation
    targets it, so all evidence describes one daemon.
``MOONMIND_GPU_QUALIFICATION_DOCKER_BINARY``
    Optional Docker CLI binary. Defaults to ``docker``.
``MOONMIND_GPU_QUALIFICATION_TIMEOUT``
    Optional per-request timeout in seconds.
``MOONMIND_GPU_QUALIFICATION_PROBE_TIMEOUT``
    Optional timeout in seconds for the collection-time device probe.
``MOONMIND_GPU_QUALIFICATION_RECORD_DIR``
    Optional durable directory for published qualification records. Defaults to
    ``var/gpu_qualification`` in the repository, which outlives the ephemeral
    per-run workspace.

A command override receives the declared-output path in
``MOONMIND_GPU_QUALIFICATION_OUTPUT``; the workspace is mounted at its own
absolute path, so that value is valid both inside the container and on the host.
Writing that file and setting
``MOONMIND_GPU_QUALIFICATION_COMMAND_WRITES_OUTPUT=1`` makes an override produce
the same declared-output evidence as the default command. Ignoring it is also
valid: the journey then declares no fixture-specific output instead of failing
the override for a file it never agreed to write.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from moonmind.schemas.workload_models import (
    UnrestrictedContainerRequest,
    WorkloadGpuRequest,
)
from moonmind.workflows.temporal.activity_runtime import TemporalAgentRuntimeActivities
from moonmind.workloads.docker_launcher import DockerWorkloadLauncher
from moonmind.workloads.gpu import (
    GPU_CONTAINER_REQUEST_SCHEMA_VERSION,
    build_gpu_qualification_record,
    classify_gpu_launch_failure,
    parse_image_digest,
)
from moonmind.workloads.registry import RunnerProfileRegistry

pytestmark = [pytest.mark.integration, pytest.mark.requires_gpu]

DECLARED_OUTPUT_CLASS = "output.primary"
DECLARED_OUTPUT_PATH = "gpu/qualification.json"


def _env(name: str) -> str:
    return str(os.environ.get(name) or "").strip()


def _docker_binary() -> str:
    return _env("MOONMIND_GPU_QUALIFICATION_DOCKER_BINARY") or "docker"


def _docker_host() -> str | None:
    return _env("MOONMIND_GPU_QUALIFICATION_DOCKER_HOST") or None


def _run_docker(*args: str, timeout: int = 300) -> tuple[int, str, str]:
    """Run one Docker CLI command against the daemon the journey launched on.

    Every helper — preflight, the device probe, image and volume inspection,
    container checks, and teardown — has to inspect the same Docker authority
    that ran the workload, otherwise the evidence describes a different daemon.
    """

    import subprocess

    host = _docker_host()
    command = [_docker_binary()]
    if host:
        command.extend(["--host", host])
    command.extend(args)
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    return completed.returncode, completed.stdout, completed.stderr


def _gpu_environment_reason() -> str | None:
    """Return why this host cannot run the journey, or ``None`` if it can."""

    if not _env("MOONMIND_GPU_QUALIFICATION_IMAGE"):
        return (
            "MOONMIND_GPU_QUALIFICATION_IMAGE is not set: this journey requires a "
            "caller-supplied NVIDIA container image on a deployment-owned GPU host"
        )
    if shutil.which(_docker_binary()) is None:
        return f"{_docker_binary()!r} is not on PATH: no trusted Docker boundary here"
    code, _stdout, stderr = _run_docker("info", "--format", "{{.ServerVersion}}", timeout=60)
    if code != 0:
        return f"the configured Docker daemon is unreachable: {stderr.strip()[:200]}"
    code, stdout, _stderr = _run_docker(
        "info", "--format", "{{json .Runtimes}}", timeout=60
    )
    if code != 0 or "nvidia" not in stdout.lower():
        return (
            "the Docker daemon exposes no NVIDIA runtime: this host is CPU-only, so "
            "the real-GPU leg cannot produce terminal evidence"
        )
    return _gpu_device_probe_reason()


def _gpu_device_probe_reason() -> str | None:
    """Return why the daemon exposes no usable device, or ``None`` if it does.

    A registered NVIDIA runtime does not establish that a device exists. This
    runs one bounded throwaway container carrying the same device request the
    journey will submit, and classifies a refusal with the production classifier
    so a device-unavailable host skips explicitly instead of failing later inside
    the caller's workload.
    """

    count = _gpu_count()
    device_request = "all" if count == "all" else str(count)
    code, _stdout, stderr = _run_docker(
        "run",
        "--rm",
        "--network",
        "none",
        "--gpus",
        device_request,
        _env("MOONMIND_GPU_QUALIFICATION_IMAGE"),
        "true",
        timeout=int(_env("MOONMIND_GPU_QUALIFICATION_PROBE_TIMEOUT") or 300),
    )
    if code == 0:
        return None
    failure = classify_gpu_launch_failure(
        gpu=WorkloadGpuRequest(vendor="nvidia", count=count),
        exit_code=code,
        stderr=stderr,
    )
    if failure is not None:
        return (
            "the Docker daemon refused the device request "
            f"({failure.reason}): this host exposes no usable NVIDIA device, so "
            "the real-GPU leg cannot produce terminal evidence"
        )
    return (
        "the caller-supplied qualification image could not start on this host: "
        f"{stderr.strip()[:200]}"
    )


def _gpu_count() -> Any:
    raw = _env("MOONMIND_GPU_QUALIFICATION_GPU_COUNT") or "all"
    return raw if raw == "all" else int(raw)


GPU_ENVIRONMENT_REASON = _gpu_environment_reason()

pytestmark.append(
    pytest.mark.skipif(
        GPU_ENVIRONMENT_REASON is not None,
        reason=GPU_ENVIRONMENT_REASON or "",
    )
)


def _command_override() -> tuple[str, ...] | None:
    """Return the operator's command override, or ``None`` for the default."""

    override = _env("MOONMIND_GPU_QUALIFICATION_COMMAND")
    if not override:
        return None
    parsed = json.loads(override)
    if not isinstance(parsed, list) or not parsed:
        raise ValueError(
            "MOONMIND_GPU_QUALIFICATION_COMMAND must be a non-empty JSON array"
        )
    return tuple(str(part) for part in parsed)


def _caller_command(report_path: Path) -> tuple[str, ...]:
    """Return the caller-supplied command. Fixture data, never a MoonMind default."""

    override = _command_override()
    if override is not None:
        return override
    script = (
        "set -eu\n"
        'identity="$(nvidia-smi --query-gpu=name,uuid --format=csv,noheader | head -n 4)"\n'
        'printf "%s\\n" "$identity"\n'
        'devices="$(nvidia-smi --query-gpu=uuid --format=csv,noheader | wc -l | tr -d " ")"\n'
        'checksum="$(printf "%s" "$identity" | cksum | cut -d" " -f1)"\n'
        f'mkdir -p "$(dirname {report_path})"\n'
        'printf \'{"visibleDevices": %s, "identityChecksum": "%s"}\\n\' '
        f'"$devices" "$checksum" > {report_path}\n'
        'test "$devices" -ge 1\n'
    )
    return ("sh", "-lc", script)


@pytest.fixture
def workspace_root(tmp_path: Path) -> Iterator[Path]:
    configured = _env("MOONMIND_GPU_QUALIFICATION_WORKSPACE_ROOT")
    if not configured:
        yield tmp_path
        return
    root = Path(configured) / f"gpu-qualification-{uuid.uuid4().hex[:8]}"
    root.mkdir(parents=True, exist_ok=True)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


@pytest.fixture
def cache_volume() -> Iterator[str]:
    configured = _env("MOONMIND_GPU_QUALIFICATION_CACHE_VOLUME")
    name = configured or f"mm_gpu_qualification_cache_{uuid.uuid4().hex[:8]}"
    _run_docker("volume", "create", name, timeout=60)
    try:
        yield name
    finally:
        if not configured:
            _run_docker("volume", "rm", "-f", name, timeout=60)


def _paths(workspace_root: Path, agent_run_id: str) -> dict[str, Path]:
    repo = workspace_root / agent_run_id / "repo"
    artifacts = workspace_root / agent_run_id / "artifacts" / "gpu-step"
    scratch = workspace_root / agent_run_id / "scratch" / "gpu-step"
    for path in (repo, artifacts, scratch):
        path.mkdir(parents=True, exist_ok=True)
    return {"repo": repo, "artifacts": artifacts, "scratch": scratch}


def _request_payload(
    workspace_root: Path,
    *,
    agent_run_id: str,
    cache_volume: str | None = None,
) -> dict[str, Any]:
    paths = _paths(workspace_root, agent_run_id)
    output_path = paths["artifacts"] / DECLARED_OUTPUT_PATH
    payload: dict[str, Any] = {
        "toolName": "container.run_container",
        "agentRunId": agent_run_id,
        "stepId": "gpu-step",
        "attempt": 1,
        "repoDir": str(paths["repo"]),
        "artifactsDir": str(paths["artifacts"]),
        "scratchDir": str(paths["scratch"]),
        "image": _env("MOONMIND_GPU_QUALIFICATION_IMAGE"),
        "command": list(_caller_command(output_path)),
        "networkMode": "none",
        "timeoutSeconds": int(_env("MOONMIND_GPU_QUALIFICATION_TIMEOUT") or 600),
        "resources": {"gpu": {"vendor": "nvidia", "count": _gpu_count()}},
        # The dynamically generated declared-output path is exposed to the
        # command, so an override can satisfy the same output contract as the
        # default command instead of being required to guess the path.
        "envOverrides": {"MOONMIND_GPU_QUALIFICATION_OUTPUT": str(output_path)},
    }
    if _declares_fixture_output():
        payload["declaredOutputs"] = {DECLARED_OUTPUT_CLASS: DECLARED_OUTPUT_PATH}
    if cache_volume:
        payload["cacheMounts"] = [{"source": cache_volume, "target": "/work/cache"}]
    return payload


def _declares_fixture_output() -> bool:
    """Return whether this run declares the fixture-specific output file.

    Only the default command promises to write ``gpu/qualification.json``. An
    ordinary override such as ``["nvidia-smi"]`` must not be failed for a
    missing fixture output it never agreed to produce; it may still write the
    path exported in ``MOONMIND_GPU_QUALIFICATION_OUTPUT`` to opt back in.
    """

    if _command_override() is None:
        return True
    return _env("MOONMIND_GPU_QUALIFICATION_COMMAND_WRITES_OUTPUT").lower() in {
        "1",
        "true",
        "yes",
    }


def _activities(workspace_root: Path) -> TemporalAgentRuntimeActivities:
    """Build the normal dispatch boundary with unrestricted workflow Docker mode."""

    return TemporalAgentRuntimeActivities(
        workload_registry=RunnerProfileRegistry.empty(workspace_root=workspace_root),
        workload_launcher=DockerWorkloadLauncher(
            docker_binary=_docker_binary(),
            docker_host=_env("MOONMIND_GPU_QUALIFICATION_DOCKER_HOST") or None,
        ),
        workflow_docker_mode="unrestricted",
        workspace_root=workspace_root,
    )


def _image_digest(image: str) -> str | None:
    code, stdout, _stderr = _run_docker(
        "image", "inspect", image, "--format", "{{json .RepoDigests}}", timeout=120
    )
    return parse_image_digest(stdout, image=image) if code == 0 else None


def _image_present(image: str) -> bool:
    code, _stdout, _stderr = _run_docker("image", "inspect", image, timeout=120)
    return code == 0


def _volume_present(name: str) -> bool:
    code, _stdout, _stderr = _run_docker("volume", "inspect", name, timeout=60)
    return code == 0


def _container_present(name: str) -> bool:
    code, stdout, _stderr = _run_docker(
        "ps", "-a", "--filter", f"name=^/{name}$", "--format", "{{.Names}}", timeout=60
    )
    return code == 0 and bool(stdout.strip())


def _record_root() -> Path:
    """Return the durable directory qualification records are published to.

    The per-run workspace is ephemeral — the configured workspace fixture
    deletes its root on teardown — so the record cannot be the only copy inside
    it. This root outlives the run, and a successful qualification always leaves
    a citable record behind.
    """

    configured = _env("MOONMIND_GPU_QUALIFICATION_RECORD_DIR")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[3] / "var" / "gpu_qualification"


def _publish_record(artifacts_dir: Path, record: Any) -> Path:
    """Publish the record to the run artifacts and to the durable record root."""

    serialized = (
        json.dumps(record.model_dump(mode="json", by_alias=True), indent=2, sort_keys=True)
        + "\n"
    )
    path = artifacts_dir / "gpu" / "qualification-record.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(serialized, encoding="utf-8")

    durable_root = _record_root()
    durable_root.mkdir(parents=True, exist_ok=True)
    durable = durable_root / f"{record.container_name}.json"
    durable.write_text(serialized, encoding="utf-8")
    # Printed so the quiet operator command still names the durable evidence.
    print(f"published GPU qualification record: {durable}")
    return durable


@pytest.mark.asyncio
async def test_real_nvidia_container_completes_through_the_trusted_boundary(
    workspace_root: Path,
) -> None:
    agent_run_id = f"gpu-qual-{uuid.uuid4().hex[:8]}"
    payload = _request_payload(workspace_root, agent_run_id=agent_run_id)
    request = UnrestrictedContainerRequest.model_validate(payload)

    # The request crosses the ordinary workload.run Activity boundary; this test
    # never invokes Docker for the workload itself.
    result_payload = await _activities(workspace_root).workload_run(
        {"request": payload}
    )

    assert result_payload["status"] == "succeeded", result_payload["metadata"]["stderr"]
    assert result_payload["exitCode"] == 0
    workload = result_payload["metadata"]["workload"]
    observations = workload["gpu"]
    assert observations["deviceRequestArgs"][0] == "--gpus"
    assert observations["deviceRequestArgs"][1] == (
        "all" if _gpu_count() == "all" else str(_gpu_count())
    )
    assert observations["launchFailure"] is None

    # Bounded stdout/stderr and the declared output are collected generically.
    assert result_payload["metadata"]["stdout"].strip()
    assert Path(result_payload["outputRefs"]["runtime.stdout"]).is_file()
    assert Path(result_payload["outputRefs"]["runtime.stderr"]).is_file()
    if _declares_fixture_output():
        declared = Path(result_payload["outputRefs"][DECLARED_OUTPUT_CLASS])
        assert declared.is_file()
        assert declared == Path(payload["artifactsDir"]) / DECLARED_OUTPUT_PATH
    else:
        assert DECLARED_OUTPUT_CLASS not in result_payload["outputRefs"]

    # Only the run-owned container is removed; the image survives cleanup.
    assert not _container_present(request.container_name)
    assert _image_present(request.image)

    # Published evidence carries no raw Docker endpoint or host environment.
    diagnostics = Path(result_payload["outputRefs"]["runtime.diagnostics"]).read_text(
        encoding="utf-8"
    )
    assert "/var/run/docker.sock" not in diagnostics
    assert "DOCKER_HOST" not in diagnostics

    from moonmind.schemas.workload_models import WorkloadResult

    record = build_gpu_qualification_record(
        request=request,
        result=WorkloadResult.model_validate(result_payload),
        recorded_at=datetime.now(UTC),
        image_digest=_image_digest(request.image),
    )
    record_path = _publish_record(Path(payload["artifactsDir"]), record)

    published = json.loads(record_path.read_text(encoding="utf-8"))
    assert published["requestSchemaVersion"] == GPU_CONTAINER_REQUEST_SCHEMA_VERSION
    assert published["imageRef"] == request.image
    assert published["status"] == "succeeded"
    assert published["moonmindRevision"]
    assert published["deviceRequestArgs"] == observations["deviceRequestArgs"]
    if _declares_fixture_output():
        assert published["declaredOutputChecksums"][DECLARED_OUTPUT_CLASS].startswith(
            "sha256:"
        )
    assert published["recordedAt"]
    # The durable record outlives the ephemeral per-run workspace.
    assert record_path.parent == _record_root()


@pytest.mark.asyncio
async def test_second_request_reuses_image_and_shared_cache_with_new_identity(
    workspace_root: Path,
    cache_volume: str,
) -> None:
    activities = _activities(workspace_root)
    image = _env("MOONMIND_GPU_QUALIFICATION_IMAGE")
    marker = f"reuse-{uuid.uuid4().hex}"
    marker_path = "/work/cache/reuse-marker"

    first_payload = _request_payload(
        workspace_root,
        agent_run_id=f"gpu-qual-{uuid.uuid4().hex[:8]}",
        cache_volume=cache_volume,
    )
    # The first GPU request writes a unique marker into the shared cache, so the
    # second request's read is terminal evidence that the same mounted volume was
    # reused — volume existence alone would also pass with the mount omitted.
    first_payload["command"] = [
        "sh",
        "-lc",
        f'printf "%s" "{marker}" > {marker_path}',
    ]
    first_payload.pop("declaredOutputs", None)
    first = await activities.workload_run({"request": first_payload})
    assert first["status"] == "succeeded", first["metadata"]["stderr"]

    # Cleanup from the first request must leave the image and cache intact.
    assert _image_present(image)
    assert _volume_present(cache_volume)
    digest_after_first = _image_digest(image)

    second_payload = _request_payload(
        workspace_root,
        agent_run_id=f"gpu-qual-{uuid.uuid4().hex[:8]}",
        cache_volume=cache_volume,
    )
    second_payload["command"] = ["sh", "-lc", f"cat {marker_path}"]
    second_payload.pop("declaredOutputs", None)
    second = await activities.workload_run({"request": second_payload})

    assert second["status"] == "succeeded", second["metadata"]["stderr"]
    assert second["metadata"]["stdout"].strip() == marker
    # Distinct execution identity, identical image and shared cache.
    assert first["requestId"] != second["requestId"]
    assert first["labels"]["moonmind.agent_run_id"] != (
        second["labels"]["moonmind.agent_run_id"]
    )
    assert _image_present(image)
    assert _image_digest(image) == digest_after_first
    assert _volume_present(cache_volume)
    assert not _container_present(first["requestId"])
    assert not _container_present(second["requestId"])

    # The shared cache holds only what the caller's workload wrote: MoonMind
    # never places job authority, ownership, or lifecycle state there.
    listing_payload = _request_payload(
        workspace_root,
        agent_run_id=f"gpu-qual-cache-{uuid.uuid4().hex[:8]}",
        cache_volume=cache_volume,
    )
    listing_payload["resources"] = {}
    listing_payload["command"] = ["sh", "-lc", "ls -A /work/cache || true"]
    listing_payload.pop("declaredOutputs", None)
    listing = await activities.workload_run({"request": listing_payload})

    assert listing["status"] == "succeeded", listing["metadata"]["stderr"]
    entries = [
        entry.strip()
        for entry in listing["metadata"]["stdout"].splitlines()
        if entry.strip()
    ]
    assert "reuse-marker" in entries
    assert not [entry for entry in entries if entry.lower().startswith("moonmind")]
    assert not [entry for entry in entries if entry.lower().startswith("mm-workload")]


@pytest.mark.asyncio
async def test_cpu_only_request_on_the_gpu_host_requests_no_device(
    workspace_root: Path,
) -> None:
    """CPU-only behavior is unchanged on a GPU-capable host."""

    payload = _request_payload(
        workspace_root, agent_run_id=f"gpu-qual-cpu-{uuid.uuid4().hex[:8]}"
    )
    payload["resources"] = {}
    payload["command"] = ["sh", "-lc", "echo cpu-only-path"]
    payload.pop("declaredOutputs", None)

    result = await _activities(workspace_root).workload_run({"request": payload})

    assert result["status"] == "succeeded", result["metadata"]["stderr"]
    assert result["metadata"]["workload"]["gpu"] is None


@pytest.mark.asyncio
async def test_timeout_targets_only_the_run_owned_container(
    workspace_root: Path,
) -> None:
    agent_run_id = f"gpu-qual-timeout-{uuid.uuid4().hex[:8]}"
    payload = _request_payload(workspace_root, agent_run_id=agent_run_id)
    payload["command"] = ["sh", "-lc", "sleep 600"]
    payload["timeoutSeconds"] = 5
    payload.pop("declaredOutputs", None)
    request = UnrestrictedContainerRequest.model_validate(payload)
    image = request.image

    result = await _activities(workspace_root).workload_run({"request": payload})

    assert result["status"] == "timed_out"
    assert result["timeoutReason"] == "workload exceeded timeoutSeconds"
    await asyncio.sleep(1)
    assert not _container_present(request.container_name)
    assert _image_present(image)


# ---------------------------------------------------------------------------
# Canonical container-job boundary (MoonLadderStudios/MoonMind#3779)
# ---------------------------------------------------------------------------
#
# The same caller-supplied image, command, and generic GPU resource are run
# again through the canonical asynchronous container-job backend on the same
# daemon, so the durable destination is qualified on real hardware rather than
# only the unrestricted route.


CANONICAL_WORKSPACE_REF = "gpu-qualification-workspace"
CANONICAL_CAPABILITIES = ("compute", "utility")


def _canonical_job_id() -> str:
    return f"container-job:{uuid.uuid4().hex}"


def _evidence_publisher(root: Path):
    """Publish bounded evidence to a local root and return its reference.

    The trusted backend only needs an opaque durable reference; the artifact
    store is not part of what this journey qualifies.
    """

    async def publish(request: Any, name: str, payload: bytes) -> str:
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        return str(target)

    return publish


def _container_job_backend(
    workspace_root: Path,
    *,
    evidence_root: Path,
    settings_source: dict[str, str] | None = None,
):
    from moonmind.config.container_backend_settings import (
        resolve_container_backend_settings,
    )
    from moonmind.workflows.temporal.container_job_backend import (
        DockerContainerJobBackend,
    )

    return DockerContainerJobBackend(
        workspace_root=workspace_root,
        settings=resolve_container_backend_settings(settings_source or {}),
        docker_binary=_docker_binary(),
        docker_host=_docker_host(),
        backend_ref="gpu-qualification",
        evidence_publisher=_evidence_publisher(evidence_root),
        image_lock_root=evidence_root / "locks",
    )


def _canonical_workspace(workspace_root: Path) -> Path:
    workspace = workspace_root / CANONICAL_WORKSPACE_REF
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


def _canonical_request(
    *,
    job_id: str,
    gpu: dict[str, Any] | None,
    command: tuple[str, ...],
    declared_output: bool,
    timeout_seconds: int | None = None,
):
    from moonmind.schemas.container_job_models import ContainerJobActivityRequest

    resources: dict[str, Any] = {"cpuMillis": 2000, "memoryMiB": 2048}
    if gpu is not None:
        resources["gpu"] = gpu
    spec: dict[str, Any] = {
        "image": _env("MOONMIND_GPU_QUALIFICATION_IMAGE"),
        "workspaceRef": {
            "kind": "external_state",
            "artifactRef": CANONICAL_WORKSPACE_REF,
        },
        "command": list(command),
        "networkMode": "none",
        "resources": resources,
        "timeoutSeconds": timeout_seconds
        or int(_env("MOONMIND_GPU_QUALIFICATION_TIMEOUT") or 600),
        "environment": [
            {
                "name": "MOONMIND_GPU_QUALIFICATION_OUTPUT",
                "value": f"/workspace/{DECLARED_OUTPUT_PATH}",
            }
        ],
    }
    if declared_output:
        spec["outputs"] = [
            {"name": DECLARED_OUTPUT_CLASS, "relativePath": DECLARED_OUTPUT_PATH}
        ]
    return ContainerJobActivityRequest.model_validate(
        {
            "jobId": job_id,
            "ownershipToken": f"{job_id}:v1",
            "request": {
                "idempotencyKey": job_id,
                "source": {"source": "workflow", "workflowId": job_id},
                "spec": spec,
            },
        }
    )


async def _drive_canonical_job(backend: Any, request: Any) -> dict[str, Any]:
    """Run one canonical job through the same Activity sequence the workflow uses."""

    resolved = await backend.resolve_workspace(request)
    request.resolved_workspace_ref = resolved.resolved_workspace_ref
    request.resolved_workspace_volume_name = resolved.resolved_workspace_volume_name
    request.resolved_workspace_volume_subpath = (
        resolved.resolved_workspace_volume_subpath
    )
    image = await backend.acquire_image(request)
    request.resolved_image_ref = image.resolved_image_ref

    created = await backend.create_container(request)
    request.container_ref = created.container_ref
    request.gpu_observation = created.gpu_observation
    started = await backend.start_container(request)
    if started.gpu_observation is not None:
        request.gpu_observation = started.gpu_observation

    deadline = request.request.spec.timeout_seconds
    observed = None
    for _ in range(deadline * 2):
        observed = await backend.observe_container(request)
        if observed.terminal_state is not None:
            break
        await asyncio.sleep(0.5)
    assert observed is not None and observed.terminal_state is not None, (
        "canonical container job did not reach a terminal state"
    )
    request.terminal_state = observed.terminal_state
    request.exit_code = observed.exit_code

    published = await backend.publish_evidence(request)
    await backend.remove_container(request)
    cleaned = await backend.cleanup(request)
    return {
        "created": created,
        "started": started,
        "observed": observed,
        "published": published,
        "cleaned": cleaned,
    }


@pytest.mark.asyncio
async def test_real_nvidia_container_job_completes_through_the_trusted_backend(
    workspace_root: Path,
    tmp_path: Path,
) -> None:
    """The canonical container-job backend realizes the same GPU request."""

    workspace = _canonical_workspace(workspace_root)
    job_id = _canonical_job_id()
    backend = _container_job_backend(workspace_root, evidence_root=tmp_path / "evidence")
    request = _canonical_request(
        job_id=job_id,
        gpu={
            "vendor": "nvidia",
            "count": _gpu_count(),
            "capabilities": list(CANONICAL_CAPABILITIES),
        },
        command=_caller_command(Path("/workspace") / DECLARED_OUTPUT_PATH),
        declared_output=_declares_fixture_output(),
    )

    outcome = await _drive_canonical_job(backend, request)

    assert outcome["observed"].terminal_state == "succeeded"
    assert outcome["observed"].exit_code == 0

    # The resolved GPU resource is reported as supported and launched, and the
    # observation carries the caller's own semantic request.
    observation = request.gpu_observation
    assert observation is not None
    assert observation.backend_supported is True
    assert observation.launched is True
    assert observation.count == _gpu_count()
    assert observation.capabilities == CANONICAL_CAPABILITIES
    assert observation.failure_class is None

    # Bounded logs and the declared output are collected through the ordinary
    # evidence plane, with no GPU-specific lifecycle branch.
    assert outcome["published"].logs_ref
    logs = Path(outcome["published"].logs_ref).read_text(encoding="utf-8")
    assert logs.strip()
    assert "/var/run/docker.sock" not in logs
    assert "DOCKER_HOST" not in logs
    if _declares_fixture_output():
        assert (workspace / DECLARED_OUTPUT_PATH).is_file()
        assert outcome["published"].artifacts_ref
        manifest = json.loads(
            Path(outcome["published"].artifacts_ref).read_text(encoding="utf-8")
        )
        collected = {item["name"]: item for item in manifest["artifacts"]}
        assert collected[DECLARED_OUTPUT_CLASS]["collectionStatus"] == "collected"
        assert collected[DECLARED_OUTPUT_CLASS]["sha256"]

    # Cleanup removed only the run-owned container; the image survives.
    assert outcome["cleaned"].cleanup_succeeded is not False
    assert not _container_present(str(request.container_ref))
    assert _image_present(_env("MOONMIND_GPU_QUALIFICATION_IMAGE"))


@pytest.mark.asyncio
async def test_canonical_cpu_only_job_on_the_gpu_host_requests_no_device(
    workspace_root: Path,
    tmp_path: Path,
) -> None:
    """CPU-only canonical behavior is unchanged on a GPU-capable host."""

    _canonical_workspace(workspace_root)
    backend = _container_job_backend(
        workspace_root, evidence_root=tmp_path / "evidence-cpu"
    )
    request = _canonical_request(
        job_id=_canonical_job_id(),
        gpu=None,
        command=("sh", "-lc", "echo cpu-only-path"),
        declared_output=False,
    )

    outcome = await _drive_canonical_job(backend, request)

    assert outcome["observed"].terminal_state == "succeeded"
    assert outcome["created"].gpu_observation is None
    assert outcome["started"].gpu_observation is None
    assert request.gpu_observation is None


@pytest.mark.asyncio
async def test_canonical_device_count_above_the_ceiling_is_refused_before_execution(
    workspace_root: Path,
    tmp_path: Path,
) -> None:
    """An unsupported device count fails with its stable class, not a workload."""

    from moonmind.schemas.container_job_models import (
        ContainerJobFailureClass,
        failure_class_from_exception,
    )

    _canonical_workspace(workspace_root)
    backend = _container_job_backend(
        workspace_root,
        evidence_root=tmp_path / "evidence-refused",
        settings_source={"MOONMIND_CONTAINER_BACKEND_MAX_GPU_COUNT": "0"},
    )
    request = _canonical_request(
        job_id=_canonical_job_id(),
        gpu={"vendor": "nvidia", "count": 1},
        command=("sh", "-lc", "echo should-not-run"),
        declared_output=False,
    )
    resolved = await backend.resolve_workspace(request)
    request.resolved_workspace_ref = resolved.resolved_workspace_ref
    image = await backend.acquire_image(request)
    request.resolved_image_ref = image.resolved_image_ref

    with pytest.raises(Exception) as exc_info:
        await backend.create_container(request)

    assert (
        failure_class_from_exception(exc_info.value)
        == ContainerJobFailureClass.GPU_COUNT_UNSUPPORTED
    )
