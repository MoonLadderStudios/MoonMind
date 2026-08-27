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
import json
import os
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from moonmind.schemas._validation import NonBlankStr
from moonmind.schemas.container_job_models import normalize_image_reference
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

#: ``docker run`` reports its own client/daemon errors with this exit status.
#: A container that started reports its application's exit status instead, so
#: this code is the objective boundary between a refused device request and an
#: ordinary container process exit.
DOCKER_LAUNCH_FAILURE_EXIT_CODE = 125

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
#
# Precedence is most-specific-first, because the vendor stack reports a device
# failure *through* its own tooling: a host with the runtime installed but no
# usable device emits both the generic ``nvidia-container-cli`` prefix and a
# specific device diagnostic. Matching the generic prefix first would direct an
# operator toward repairing a runtime that is already working instead of toward
# device capacity, so the bare vendor-stack prefixes are the last resort.
_GPU_LAUNCH_FAILURE_PATTERNS: tuple[tuple[GpuLaunchFailureReason, tuple[str, ...]], ...] = (
    (
        "nvidia_runtime_unavailable",
        (
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
    (
        # Last resort: the installed vendor stack reported something no specific
        # signature above recognized, so the runtime itself is the only evidence
        # available.
        "nvidia_runtime_unavailable",
        (
            "nvidia-container-cli",
            "nvidia-container-runtime",
        ),
    ),
)

_DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")


def gpu_device_request_args(gpu: WorkloadGpuRequest) -> list[str]:
    """Return the Docker device-request arguments for a generic GPU request.

    ``count: "all"`` yields ``--gpus all``; a numeric count yields
    ``--gpus <count>``. Declared driver capabilities are appended as Docker's
    own quoted ``capabilities=`` device-request field, whose value is itself
    comma-separated and therefore must stay quoted inside the option's CSV
    syntax. The vendor is validated by the request contract, so no vendor
    branching happens here.
    """

    value = gpu.device_request_value
    if gpu.capabilities:
        capabilities = ",".join(gpu.capabilities)
        value = f'count={value},"capabilities={capabilities}"'
    return [GPU_DEVICE_REQUEST_FLAG, value]


class GpuLaunchFailure(BaseModel):
    """Docker's refusal of a GPU device request, distinct from a process exit."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    failure_class: Literal["gpu_device_request_rejected"] = Field(
        GPU_DEVICE_REQUEST_REJECTED,
        alias="failureClass",
    )
    reason: GpuLaunchFailureReason = Field(..., alias="reason")
    exit_code: int | None = Field(None, alias="exitCode")


def gpu_launch_refusal(
    *,
    gpu: WorkloadGpuRequest | None,
    stderr: str,
    exit_code: int | None = None,
) -> GpuLaunchFailure | None:
    """Classify a launch-phase failure as a GPU device-request refusal, or not.

    This is the single authority for recognizing a refused device request:
    stderr must carry a specific vendor/runtime diagnostic. Callers whose launch
    phase is already its own command — an explicit container create or start —
    use it directly, because such a failure cannot be an application exit.
    Returns ``None`` when no GPU was requested, when there is no diagnostic, and
    when the diagnostic names a non-GPU launch failure such as an unreachable
    Docker daemon.
    """

    if gpu is None:
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


def classify_gpu_launch_failure(
    *,
    gpu: WorkloadGpuRequest | None,
    exit_code: int | None,
    stderr: str,
) -> GpuLaunchFailure | None:
    """Classify a failed one-shot run as a GPU device-request refusal, or not.

    One ``docker run`` merges the launch phase with the application's own exit,
    so a refusal is recognized only when Docker itself refused to run the
    container — ``exit_code`` equals :data:`DOCKER_LAUNCH_FAILURE_EXIT_CODE` —
    and :func:`gpu_launch_refusal` then recognizes the diagnostic. A container
    that started and exited nonzero on its own stays an ordinary process exit
    failure even if the workload wrote GPU-shaped text to stderr.
    """

    if exit_code != DOCKER_LAUNCH_FAILURE_EXIT_CODE:
        return None
    return gpu_launch_refusal(gpu=gpu, stderr=stderr, exit_code=exit_code)


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
        # ``exclude_none`` keeps the observation compact and keeps an omitted
        # optional request field absent, so evidence recorded for one semantic
        # request stays byte-identical as the request contract grows.
        "request": gpu.model_dump(mode="json", by_alias=True, exclude_none=True),
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


#: Environment keys carrying an immutable build identity, in precedence order.
MOONMIND_REVISION_ENV_KEYS: tuple[str, ...] = (
    "MOONMIND_BUILD_SHA",
    "MOONMIND_IMAGE_DIGEST",
)


def moonmind_revision(env: Mapping[str, str] | None = None) -> str | None:
    """Return the immutable revision identity, or ``None`` when unavailable.

    Published evidence must name the MoonMind implementation it qualified, so
    there is no placeholder value: a caller that cannot resolve an immutable
    identity has to fail before publishing rather than record an unattributable
    revision.
    """

    source = os.environ if env is None else env
    for key in MOONMIND_REVISION_ENV_KEYS:
        value = str(source.get(key) or "").strip()
        if value:
            return value
    return None


def parse_image_digest(
    value: str | Sequence[str] | None, *, image: str
) -> str | None:
    """Return the digest whose repository matches ``image``.

    ``docker image inspect --format '{{json .RepoDigests}}'`` reports one entry
    per repository the local image is tagged in, so the repository names decide
    which digest belongs to the requested reference. A digest from an alias
    repository is never attributed to ``image``.
    """

    target = normalize_image_reference(image)
    for entry in _repo_digest_entries(value):
        repository, separator, digest = entry.rpartition("@")
        if not separator or not _DIGEST_PATTERN.fullmatch(digest):
            continue
        try:
            candidate = normalize_image_reference(repository)
        except ValueError:
            continue
        if (candidate.registry, candidate.repository) == (
            target.registry,
            target.repository,
        ):
            return digest
    return None


def _repo_digest_entries(value: str | Sequence[str] | None) -> tuple[str, ...]:
    """Return the ``repository@digest`` entries reported by Docker inspect."""

    if value is None:
        return ()
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return ()
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError:
            return ()
    else:
        decoded = list(value)
    if decoded is None:
        return ()
    if not isinstance(decoded, list):
        return ()
    return tuple(str(entry).strip() for entry in decoded if str(entry).strip())


def verified_gpu_observations(
    *,
    request: UnrestrictedContainerRequest,
    result: WorkloadResult,
) -> Mapping[str, Any]:
    """Return the executed GPU observations proven to belong to ``request``.

    Terminal evidence, not the submitted request, is the authority for what a
    host actually realized. This rejects a result produced for a different
    container, image, or GPU request, and a result that carries no realized
    device-request evidence at all, so a record can never combine one request's
    identity with another run's outcome.
    """

    gpu = request.resources.gpu
    if gpu is None:
        raise ValueError("qualification records require a GPU resource request")
    if result.request_id != request.container_name:
        raise ValueError(
            "qualification result belongs to a different container: "
            f"{result.request_id!r} is not {request.container_name!r}"
        )
    workload = result.metadata.get("workload")
    workload = workload if isinstance(workload, Mapping) else None
    if workload is None:
        raise ValueError(
            "qualification records require executed workload evidence on the result"
        )
    recorded_container = workload.get("containerName")
    if recorded_container != request.container_name:
        raise ValueError(
            "qualification evidence names a different container: "
            f"{recorded_container!r} is not {request.container_name!r}"
        )
    recorded_image = workload.get("imageRef")
    if recorded_image != request.image:
        raise ValueError(
            "qualification evidence names a different image: "
            f"{recorded_image!r} is not {request.image!r}"
        )
    observations = workload.get("gpu")
    observations = observations if isinstance(observations, Mapping) else None
    if observations is None:
        raise ValueError(
            "qualification records require realized GPU device-request evidence"
        )
    realized_request = observations.get("request")
    expected_request = gpu.model_dump(mode="json", by_alias=True, exclude_none=True)
    if realized_request != expected_request:
        raise ValueError(
            "qualification evidence realized a different GPU request than the "
            "one submitted"
        )
    realized_args = observations.get("deviceRequestArgs")
    realized_args = (
        realized_args
        if isinstance(realized_args, Sequence) and not isinstance(realized_args, str)
        else None
    )
    if realized_args is None:
        raise ValueError(
            "qualification records require realized GPU device-request evidence"
        )
    device_request_args = tuple(str(part) for part in realized_args)
    if not device_request_args or device_request_args[0] != GPU_DEVICE_REQUEST_FLAG:
        raise ValueError(
            "qualification evidence carries no realized device request: "
            f"{device_request_args!r}"
        )
    return observations


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
    revision = moonmind_revision(env)
    if revision is None:
        keys = " or ".join(MOONMIND_REVISION_ENV_KEYS)
        raise ValueError(
            "qualification records require an immutable MoonMind revision; set "
            f"{keys} before publishing"
        )
    observations = verified_gpu_observations(request=request, result=result)
    raw_failure = observations.get("launchFailure")
    device_request_args = tuple(
        str(part) for part in observations["deviceRequestArgs"]
    )
    checksums = {
        artifact_class: _sha256_file(Path(ref))
        for artifact_class, ref in sorted(result.output_refs.items())
        if artifact_class in request.declared_outputs
    }
    return GpuQualificationRecord(
        moonmindRevision=revision,
        requestSchemaVersion=GPU_CONTAINER_REQUEST_SCHEMA_VERSION,
        imageRef=request.image,
        imageDigest=image_digest,
        gpuRequest=gpu,
        deviceRequestArgs=device_request_args,
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
    "DOCKER_LAUNCH_FAILURE_EXIT_CODE",
    "GPU_CONTAINER_REQUEST_SCHEMA_VERSION",
    "GPU_DEVICE_REQUEST_FLAG",
    "GPU_DEVICE_REQUEST_REJECTED",
    "MOONMIND_REVISION_ENV_KEYS",
    "GpuLaunchFailure",
    "GpuLaunchFailureReason",
    "GpuQualificationRecord",
    "build_gpu_qualification_record",
    "classify_gpu_launch_failure",
    "gpu_device_request_args",
    "gpu_launch_observations",
    "gpu_launch_refusal",
    "moonmind_revision",
    "parse_image_digest",
    "verified_gpu_observations",
]
