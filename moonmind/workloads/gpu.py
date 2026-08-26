"""Generic GPU device requests for caller-supplied containers.

This module is application-neutral. It realizes a caller-supplied
:class:`~moonmind.schemas.workload_models.WorkloadGpuRequest` as the vendor's
Docker device request, classifies a Docker refusal of that device request
distinctly from an ordinary container process exit, and describes the compact
qualification record published as evidence for one generic GPU container run.

It contains no image constants, no workload-type branching, and no
repository-, engine-, or product-specific interpretation: the image, the
command, and the GPU resource are ordinary request data supplied by the caller.
"""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from moonmind.schemas._validation import NonBlankStr
from moonmind.schemas.workload_models import (
    UnrestrictedContainerRequest,
    WorkloadGpuRequest,
    WorkloadResult,
    WorkloadStatus,
)

#: Docker flag carrying an NVIDIA device request (``--gpus all`` / ``--gpus 2``).
GPU_DEVICE_REQUEST_FLAG = "--gpus"

#: Stable failure class reported when Docker refused the GPU device request
#: itself. It is deliberately distinct from a container that started and then
#: exited nonzero, which carries no GPU failure class at all.
GPU_DEVICE_REQUEST_REJECTED = "gpu_device_request_rejected"

GpuLaunchFailureReason = Literal[
    "nvidia_runtime_unavailable",
    "gpu_device_unavailable",
    "device_request_unsupported",
    "gpu_device_request_rejected",
]

#: Schema version of the generic container request shape a qualification record
#: was produced against.
GPU_CONTAINER_REQUEST_SCHEMA_VERSION = "v1"

# Docker/daemon failures that are explicitly *not* GPU device-request refusals.
# They are ordinary generic container launch failures and must not be reported
# as a GPU rejection, otherwise an unreachable daemon would masquerade as a
# missing GPU.
_NON_GPU_LAUNCH_FAILURE_PATTERNS: tuple[str, ...] = (
    "cannot connect to the docker daemon",
    "is the docker daemon running",
)

# Ordered generic signatures of a refused device request. Each entry is a
# vendor/runtime diagnostic string, never a product or repository condition.
# Every pattern is specific enough that a container which started and then wrote
# GPU-shaped text to stderr cannot be mistaken for a refused device request.
_GPU_LAUNCH_FAILURE_PATTERNS: tuple[tuple[GpuLaunchFailureReason, tuple[str, ...]], ...] = (
    (
        "nvidia_runtime_unavailable",
        (
            "nvidia-container-cli",
            "nvidia-container-runtime",
            "unknown or invalid runtime name: nvidia",
            "unknown runtime specified nvidia",
        ),
    ),
    (
        "gpu_device_unavailable",
        (
            "failed to initialize nvml",
            "no devices were found",
            "detected 0 devices",
        ),
    ),
    (
        "device_request_unsupported",
        (
            "unknown flag: --gpus",
            "flag provided but not defined: -gpus",
            "deviceRequests",
            "device requests are not supported",
        ),
    ),
    (
        "gpu_device_request_rejected",
        (
            "could not select device driver",
            "capabilities: [[gpu]]",
        ),
    ),
)

_DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")


def gpu_device_request_args(gpu: WorkloadGpuRequest) -> list[str]:
    """Return the Docker device-request arguments for a generic GPU request.

    ``count: "all"`` yields ``--gpus all``; a numeric count yields
    ``--gpus <count>``. The vendor is validated by the request contract, so no
    vendor branching happens here.
    """

    return [GPU_DEVICE_REQUEST_FLAG, gpu.device_request_value]


class GpuLaunchFailure(BaseModel):
    """Docker's refusal of a GPU device request, distinct from a process exit."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    failure_class: Literal["gpu_device_request_rejected"] = Field(
        GPU_DEVICE_REQUEST_REJECTED,
        alias="failureClass",
    )
    reason: GpuLaunchFailureReason = Field(..., alias="reason")
    exit_code: int | None = Field(None, alias="exitCode")


def classify_gpu_launch_failure(
    *,
    gpu: WorkloadGpuRequest | None,
    exit_code: int | None,
    stderr: str,
) -> GpuLaunchFailure | None:
    """Classify a failed run as a GPU device-request refusal, or not.

    A refusal is recognized only by a specific vendor/runtime diagnostic on
    stderr. Returns ``None`` when no GPU was requested, when the run succeeded
    or was cancelled, when the diagnostic names a non-GPU launch failure such as
    an unreachable Docker daemon, and — by construction — when the container
    started and exited nonzero on its own, which stays an ordinary process exit
    failure.
    """

    if gpu is None or not exit_code:
        return None
    haystack = str(stderr or "").lower()
    if not haystack:
        return None
    if any(pattern in haystack for pattern in _NON_GPU_LAUNCH_FAILURE_PATTERNS):
        return None
    for reason, patterns in _GPU_LAUNCH_FAILURE_PATTERNS:
        if any(pattern.lower() in haystack for pattern in patterns):
            return GpuLaunchFailure(reason=reason, exitCode=exit_code)
    return None


def gpu_launch_observations(
    *,
    gpu: WorkloadGpuRequest | None,
    exit_code: int | None,
    stderr: str,
) -> dict[str, Any] | None:
    """Return bounded, non-sensitive GPU observations for one container run."""

    if gpu is None:
        return None
    failure = classify_gpu_launch_failure(gpu=gpu, exit_code=exit_code, stderr=stderr)
    return {
        "request": gpu.model_dump(mode="json", by_alias=True),
        "deviceRequestArgs": gpu_device_request_args(gpu),
        "launchFailure": (
            failure.model_dump(mode="json", by_alias=True)
            if failure is not None
            else None
        ),
    }


class GpuQualificationRecord(BaseModel):
    """Compact generic evidence for one qualified GPU container run."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    record_version: Literal["v1"] = Field("v1", alias="recordVersion")
    moonmind_revision: NonBlankStr = Field(..., alias="moonmindRevision")
    request_schema_version: NonBlankStr = Field(..., alias="requestSchemaVersion")
    image_ref: NonBlankStr = Field(..., alias="imageRef")
    image_digest: str | None = Field(None, alias="imageDigest")
    gpu_request: WorkloadGpuRequest = Field(..., alias="gpuRequest")
    device_request_args: tuple[str, ...] = Field(..., alias="deviceRequestArgs")
    container_name: NonBlankStr = Field(..., alias="containerName")
    status: WorkloadStatus = Field(..., alias="status")
    exit_code: int | None = Field(None, alias="exitCode")
    timeout_reason: str | None = Field(None, alias="timeoutReason")
    gpu_launch_failure: GpuLaunchFailure | None = Field(None, alias="gpuLaunchFailure")
    declared_output_checksums: dict[str, str] = Field(
        default_factory=dict,
        alias="declaredOutputChecksums",
    )
    started_at: datetime | None = Field(None, alias="startedAt")
    completed_at: datetime | None = Field(None, alias="completedAt")
    duration_seconds: float | None = Field(None, alias="durationSeconds", ge=0)
    recorded_at: datetime = Field(..., alias="recordedAt")


def moonmind_revision(env: Mapping[str, str] | None = None) -> str:
    """Return the deployment's immutable release identity, or ``unknown``."""

    source = os.environ if env is None else env
    for key in ("MOONMIND_BUILD_SHA", "MOONMIND_IMAGE_DIGEST"):
        value = str(source.get(key) or "").strip()
        if value:
            return value
    return "unknown"


def parse_image_digest(value: str | None) -> str | None:
    """Return the ``sha256:`` digest embedded in a Docker inspect line."""

    match = _DIGEST_PATTERN.search(str(value or ""))
    return match.group(0) if match else None


def build_gpu_qualification_record(
    *,
    request: UnrestrictedContainerRequest,
    result: WorkloadResult,
    recorded_at: datetime,
    image_digest: str | None = None,
    env: Mapping[str, str] | None = None,
) -> GpuQualificationRecord:
    """Build the compact qualification record for one generic GPU container run."""

    gpu = request.resources.gpu
    if gpu is None:
        raise ValueError("qualification records require a GPU resource request")
    workload = result.metadata.get("workload")
    observations = workload.get("gpu") if isinstance(workload, Mapping) else None
    raw_failure = (
        observations.get("launchFailure") if isinstance(observations, Mapping) else None
    )
    checksums = {
        artifact_class: _sha256_file(Path(ref))
        for artifact_class, ref in sorted(result.output_refs.items())
        if artifact_class in request.declared_outputs
    }
    return GpuQualificationRecord(
        moonmindRevision=moonmind_revision(env),
        requestSchemaVersion=GPU_CONTAINER_REQUEST_SCHEMA_VERSION,
        imageRef=request.image,
        imageDigest=image_digest,
        gpuRequest=gpu,
        deviceRequestArgs=tuple(gpu_device_request_args(gpu)),
        containerName=request.container_name,
        status=result.status,
        exitCode=result.exit_code,
        timeoutReason=result.timeout_reason,
        gpuLaunchFailure=(
            GpuLaunchFailure.model_validate(raw_failure)
            if isinstance(raw_failure, Mapping)
            else None
        ),
        declaredOutputChecksums={
            artifact_class: digest
            for artifact_class, digest in checksums.items()
            if digest is not None
        },
        startedAt=result.started_at,
        completedAt=result.completed_at,
        durationSeconds=result.duration_seconds,
        recordedAt=recorded_at.astimezone(UTC),
    )


def _sha256_file(path: Path) -> str | None:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                digest.update(chunk)
    except OSError:
        return None
    return f"sha256:{digest.hexdigest()}"


__all__ = [
    "GPU_CONTAINER_REQUEST_SCHEMA_VERSION",
    "GPU_DEVICE_REQUEST_FLAG",
    "GPU_DEVICE_REQUEST_REJECTED",
    "GpuLaunchFailure",
    "GpuLaunchFailureReason",
    "GpuQualificationRecord",
    "build_gpu_qualification_record",
    "classify_gpu_launch_failure",
    "gpu_device_request_args",
    "gpu_launch_observations",
    "moonmind_revision",
    "parse_image_digest",
]
