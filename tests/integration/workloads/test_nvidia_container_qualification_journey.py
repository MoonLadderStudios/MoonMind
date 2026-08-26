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
``MOONMIND_GPU_QUALIFICATION_GPU_COUNT``
    Optional ``all`` (default) or a positive integer.
``MOONMIND_GPU_QUALIFICATION_WORKSPACE_ROOT``
    Optional workspace root visible at the same absolute path to both this
    process and the Docker daemon.
``MOONMIND_GPU_QUALIFICATION_CACHE_VOLUME``
    Optional named volume used for the warm-reuse leg.
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

from moonmind.schemas.workload_models import UnrestrictedContainerRequest
from moonmind.workflows.temporal.activity_runtime import TemporalAgentRuntimeActivities
from moonmind.workloads.docker_launcher import DockerWorkloadLauncher
from moonmind.workloads.gpu import (
    GPU_CONTAINER_REQUEST_SCHEMA_VERSION,
    build_gpu_qualification_record,
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


def _run_docker(*args: str, timeout: int = 300) -> tuple[int, str, str]:
    import subprocess

    completed = subprocess.run(
        [_docker_binary(), *args],
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
    return None


GPU_ENVIRONMENT_REASON = _gpu_environment_reason()

pytestmark.append(
    pytest.mark.skipif(
        GPU_ENVIRONMENT_REASON is not None,
        reason=GPU_ENVIRONMENT_REASON or "",
    )
)


def _caller_command(report_path: Path) -> tuple[str, ...]:
    """Return the caller-supplied command. Fixture data, never a MoonMind default."""

    override = _env("MOONMIND_GPU_QUALIFICATION_COMMAND")
    if override:
        parsed = json.loads(override)
        if not isinstance(parsed, list) or not parsed:
            raise ValueError(
                "MOONMIND_GPU_QUALIFICATION_COMMAND must be a non-empty JSON array"
            )
        return tuple(str(part) for part in parsed)
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


def _gpu_count() -> Any:
    raw = _env("MOONMIND_GPU_QUALIFICATION_GPU_COUNT") or "all"
    return raw if raw == "all" else int(raw)


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
    payload: dict[str, Any] = {
        "toolName": "container.run_container",
        "agentRunId": agent_run_id,
        "stepId": "gpu-step",
        "attempt": 1,
        "repoDir": str(paths["repo"]),
        "artifactsDir": str(paths["artifacts"]),
        "scratchDir": str(paths["scratch"]),
        "image": _env("MOONMIND_GPU_QUALIFICATION_IMAGE"),
        "command": list(_caller_command(paths["artifacts"] / DECLARED_OUTPUT_PATH)),
        "networkMode": "none",
        "timeoutSeconds": int(_env("MOONMIND_GPU_QUALIFICATION_TIMEOUT") or 600),
        "resources": {"gpu": {"vendor": "nvidia", "count": _gpu_count()}},
        "declaredOutputs": {DECLARED_OUTPUT_CLASS: DECLARED_OUTPUT_PATH},
    }
    if cache_volume:
        payload["cacheMounts"] = [{"source": cache_volume, "target": "/work/cache"}]
    return payload


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
    return parse_image_digest(stdout) if code == 0 else None


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


def _publish_record(artifacts_dir: Path, record: Any) -> Path:
    path = artifacts_dir / "gpu" / "qualification-record.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(record.model_dump(mode="json", by_alias=True), indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    return path


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
    declared = Path(result_payload["outputRefs"][DECLARED_OUTPUT_CLASS])
    assert declared.is_file()
    assert declared == Path(payload["artifactsDir"]) / DECLARED_OUTPUT_PATH

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
    assert published["declaredOutputChecksums"][DECLARED_OUTPUT_CLASS].startswith(
        "sha256:"
    )
    assert published["recordedAt"]


@pytest.mark.asyncio
async def test_second_request_reuses_image_and_shared_cache_with_new_identity(
    workspace_root: Path,
    cache_volume: str,
) -> None:
    activities = _activities(workspace_root)
    image = _env("MOONMIND_GPU_QUALIFICATION_IMAGE")

    first_payload = _request_payload(
        workspace_root,
        agent_run_id=f"gpu-qual-{uuid.uuid4().hex[:8]}",
        cache_volume=cache_volume,
    )
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
    second = await activities.workload_run({"request": second_payload})

    assert second["status"] == "succeeded", second["metadata"]["stderr"]
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
    listing_payload.pop("declaredOutputs")
    listing = await activities.workload_run({"request": listing_payload})

    assert listing["status"] == "succeeded", listing["metadata"]["stderr"]
    entries = [
        entry.strip()
        for entry in listing["metadata"]["stdout"].splitlines()
        if entry.strip()
    ]
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
    payload.pop("declaredOutputs")

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
    payload.pop("declaredOutputs")
    request = UnrestrictedContainerRequest.model_validate(payload)
    image = request.image

    result = await _activities(workspace_root).workload_run({"request": payload})

    assert result["status"] == "timed_out"
    assert result["timeoutReason"] == "workload exceeded timeoutSeconds"
    await asyncio.sleep(1)
    assert not _container_present(request.container_name)
    assert _image_present(image)
