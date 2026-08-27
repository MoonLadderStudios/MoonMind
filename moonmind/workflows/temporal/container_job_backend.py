"""Production Docker Engine backend for durable container-job activities.

This module defines the narrow, deployment-selected backend boundary for
``kind=docker-engine`` (MoonLadderStudios/MoonMind#3254):

* ``ContainerJobBackend`` is the small adapter Protocol the trusted Temporal
  container-job Activities depend on. It covers readiness, image observation,
  the create/start/observe/wait/log-attach primitives, stop/kill with bounded
  grace, remove, and label-based reconciliation — and nothing else.
* ``DockerContainerJobBackend`` is the one production implementation. It accepts
  only a resolved, authorized launch plan, enforces non-overridable
  security/resource policy at the final launch boundary, creates or reattaches
  to an owned container idempotently, observes execution, and stops/removes only
  that container.

Endpoint and daemon-reachability configuration lives in
``ContainerBackendSettings`` and is only ever read by trusted worker
construction; it never crosses a public contract.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import mimetypes
import os
import re
import shutil
import stat
import tarfile
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from io import BytesIO
from logging import getLogger
from pathlib import Path
from typing import Awaitable, Callable, Protocol, Sequence, runtime_checkable

from moonmind.config.container_backend_settings import (
    ContainerBackendReadinessError,
    ContainerBackendSettings,
    LocalImageRecipe,
    RegistryImageSource,
    resolve_container_backend_settings,
)
from moonmind.observability.transport import SpoolLogPublisher
from moonmind.schemas.agent_runtime_models import RunObservabilityEvent
from moonmind.schemas.container_job_models import (
    ArtifactCollectionStatus,
    AuxiliaryOutcome,
    ContainerJobActivityRequest,
    ContainerJobActivityResult,
    ContainerJobArtifact,
    ContainerJobArtifactPage,
    ContainerJobBackendError,
    ContainerJobState,
    ContainerJobLogEntry,
    MAX_LOG_PAGE_ENTRIES,
    RegistryAuthorization,
    ContainerJobFailureClass,
    GpuObservation,
    ImageObservation,
    gpu_observation,
)
from moonmind.utils.logging import redact_sensitive_text
from moonmind.workflows.temporal.container_image_acquisition import (
    FilesystemImageAcquisitionLock,
    ImageAcquisitionError,
    ImageAcquisitionLock,
    classify_pull_failure,
    image_lock_key,
    normalize_image_reference,
    parse_resolved_digest,
)
from moonmind.workflows.temporal.runtime.command_runner import run_runtime_command
from moonmind.workflows.temporal.runtime.registry_auth_resolve import (
    RegistryAuthResolutionError,
    RegistryCredential,
    resolve_registry_pull_credentials,
)
from moonmind.workloads.docker_launcher import structured_container_security_args
from moonmind.workloads.gpu import (
    GpuLaunchFailureReason,
    gpu_device_request_args,
    gpu_launch_refusal,
)
from moonmind.security.egress import (
    DEFAULT_EGRESS_PROFILE,
    attest_docker_workload_egress,
    bounded_denial_diagnostics,
    denied_connection_count,
    attest_docker_egress,
    restricted_proxy_env,
)
from moonmind.security.egress_conformance_evidence import (
    serialize_conformance_evidence,
)
from moonmind.schemas.workload_models import (
    WORKLOAD_GPU_VENDORS,
    WorkloadGpuRequest,
)
from moonmind.schemas.workspace_locator_models import (
    ExternalStateLocator,
    ManagedWorkspaceLocator,
    SandboxWorkspaceLocator,
)
from moonmind.workflows.temporal.runtime.workspace_locators import (
    ManagedRunRecordStore,
    resolve_managed_workspace_locator,
)

CommandRunner = Callable[[Sequence[str]], Awaitable[tuple[int, bytes, bytes]]]
EvidencePublisher = Callable[[ContainerJobActivityRequest, str, bytes], Awaitable[str]]
ProjectionWriter = Callable[[ContainerJobActivityRequest], Awaitable[None]]
RegistryAuthResolver = Callable[[str], Awaitable[RegistryCredential]]
SecretResolver = Callable[[str], Awaitable[str]]

logger = getLogger(__name__)


def _redact(text: str, secrets: Sequence[str]) -> str:
    """Remove any resolved credential material from an observable string."""

    redacted = text
    for secret in secrets:
        token = str(secret or "").strip()
        if token:
            redacted = redacted.replace(token, "[redacted]")
    return redacted
# Ownership/correlation/expiry label keys. These are applied unconditionally by
# the backend and can never be supplied or overridden through the public spec
# (the contract layer rejects ``label``/``labels`` keys outright).
LABEL_CONTAINER_JOB = "moonmind.container_job"
LABEL_OWNERSHIP = "moonmind.ownership"
LABEL_CORRELATION = "moonmind.correlation"
LABEL_EXPIRES_AT = "moonmind.expires_at"
LABEL_OBJECT_KIND = "moonmind.object_kind"
LABEL_BACKEND_REF = "moonmind.backend_ref"
LABEL_OWNERSHIP_SCHEMA = "moonmind.ownership_schema"
OWNERSHIP_SCHEMA_VERSION = "container-job/v1"

# Grace added to the job timeout when computing the reaper expiry label so a
# container that is still being torn down is not swept mid-cleanup.
_EXPIRY_GRACE_SECONDS = 300

# Forbidden tokens that must never reach ``docker create`` for an owned job
# container. This is a defense-in-depth re-check at the final launch boundary;
# the public contract already rejects these, but the adapter refuses to launch
# if construction ever produced one.
# ``--privileged`` is handled separately because the hardened baseline emits the
# explicit, safe ``--privileged=false``. Every other flag here must never appear.
_FORBIDDEN_LAUNCH_FLAGS = frozenset(
    {
        "--add-host",
        "--device",
        "--device-cgroup-rule",
        "--dns",
        "--dns-option",
        "--dns-search",
        "--pid",
        "--ipc",
        "--uts",
        "--userns",
        "--cgroupns",
        "--cap-add",
        "--sysctl",
    }
)
_TRUTHY_PRIVILEGED = frozenset({"--privileged", "--privileged=true", "--privileged=1"})
_FORBIDDEN_MOUNT_SOURCES = (
    "/var/run/docker.sock",
    "/run/docker.sock",
    "/var/lib/docker",
)
_MIN_VOLUME_SUBPATH_DOCKER_MAJOR = 26
# Docker Engine 19.03 introduced the device-request API that realizes a typed
# GPU resource. An older daemon accepts the ordinary container request but has
# no way to attach the requested devices, so a GPU job is refused before start
# rather than silently executed on a less-capable substrate.
_MIN_DEVICE_REQUEST_DOCKER_MAJOR = 19
# Generic mapping from the shared launch-refusal classifier's vendor/runtime
# reason to the canonical container-job failure class. The classifier owns the
# diagnostic evidence; this table only names the stable service-level outcome.
_GPU_LAUNCH_FAILURE_CLASSES: dict[
    GpuLaunchFailureReason, ContainerJobFailureClass
] = {
    "nvidia_runtime_unavailable": ContainerJobFailureClass.GPU_RUNTIME_UNAVAILABLE,
    "gpu_device_unavailable": ContainerJobFailureClass.GPU_RESOURCE_UNAVAILABLE,
    "device_request_unsupported": ContainerJobFailureClass.GPU_BACKEND_UNSUPPORTED,
    "gpu_device_request_rejected": ContainerJobFailureClass.GPU_BACKEND_UNSUPPORTED,
}
_MIB = 1024 * 1024
_AUTO_ACTIVE_MEMORY_FRACTION = 0.70
_CAPACITY_LOCK_WAIT_SECONDS = 45.0
_CAPACITY_LOCK_POLL_SECONDS = 0.1


@dataclass(frozen=True)
class _CapacityAdmissionLease:
    file_descriptor: int


class CapacityAdmissionLock(Protocol):
    """Mutually exclusive cross-worker lock for one capacity snapshot."""

    async def acquire(
        self,
        key: str,
        *,
        wait_seconds: float,
        poll_seconds: float,
    ) -> _CapacityAdmissionLease:
        """Wait for and return exclusive ownership of ``key``."""

    async def release(self, lease: _CapacityAdmissionLease) -> None:
        """Release a lease returned by ``acquire``."""


class FilesystemCapacityAdmissionLock:
    """OS-held advisory lock that is released automatically on worker death."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)
        self._local_lock = asyncio.Lock()

    def _path(self, key: str) -> Path:
        return self._root / f"{key}.lock"

    @staticmethod
    def _try_lock(file_descriptor: int) -> None:
        # Docker Engine workers run on Linux. Keeping the import local avoids
        # making this production adapter unimportable on non-POSIX dev hosts.
        import fcntl

        fcntl.flock(file_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)

    @staticmethod
    def _unlock(file_descriptor: int) -> None:
        import fcntl

        try:
            fcntl.flock(file_descriptor, fcntl.LOCK_UN)
        finally:
            os.close(file_descriptor)

    async def acquire(
        self,
        key: str,
        *,
        wait_seconds: float,
        poll_seconds: float,
    ) -> _CapacityAdmissionLease:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + wait_seconds
        local_acquired = False
        file_descriptor: int | None = None
        try:
            await asyncio.wait_for(
                self._local_lock.acquire(), timeout=wait_seconds
            )
            local_acquired = True
            self._root.mkdir(parents=True, exist_ok=True)
            file_descriptor = os.open(
                self._path(key), os.O_CREAT | os.O_RDWR, 0o600
            )
            while True:
                try:
                    self._try_lock(file_descriptor)
                    return _CapacityAdmissionLease(file_descriptor)
                except BlockingIOError:
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        raise TimeoutError(
                            "container-job capacity admission remained busy"
                        ) from None
                    await asyncio.sleep(min(poll_seconds, remaining))
        except BaseException:
            if file_descriptor is not None:
                os.close(file_descriptor)
            if local_acquired:
                self._local_lock.release()
            raise

    async def release(self, lease: _CapacityAdmissionLease) -> None:
        try:
            self._unlock(lease.file_descriptor)
        finally:
            self._local_lock.release()


def _docker_major_version(server_version: str) -> int | None:
    match = re.match(r"\s*(\d+)(?:\.\d+)?", str(server_version or ""))
    return int(match.group(1)) if match is not None else None


@runtime_checkable
class ContainerJobBackend(Protocol):
    """Narrow adapter the container-job Activities depend on."""

    async def check_readiness(self) -> ContainerJobActivityResult: pass

    async def resolve_workspace(
        self, request: ContainerJobActivityRequest
    ) -> ContainerJobActivityResult: pass

    async def acquire_image(
        self, request: ContainerJobActivityRequest
    ) -> ContainerJobActivityResult: pass

    async def reconcile_container(
        self, request: ContainerJobActivityRequest
    ) -> ContainerJobActivityResult: pass

    async def create_container(
        self, request: ContainerJobActivityRequest
    ) -> ContainerJobActivityResult: pass

    async def start_container(
        self, request: ContainerJobActivityRequest
    ) -> ContainerJobActivityResult: pass

    async def observe_container(
        self, request: ContainerJobActivityRequest
    ) -> ContainerJobActivityResult: pass

    async def stop_container(
        self, request: ContainerJobActivityRequest
    ) -> ContainerJobActivityResult: pass

    async def remove_container(
        self, request: ContainerJobActivityRequest
    ) -> ContainerJobActivityResult: pass

    async def publish_evidence(
        self, request: ContainerJobActivityRequest
    ) -> ContainerJobActivityResult: pass

    async def project_status(
        self, request: ContainerJobActivityRequest
    ) -> ContainerJobActivityResult: pass

    async def repair_projection(
        self, request: ContainerJobActivityRequest
    ) -> ContainerJobActivityResult: pass

    async def cleanup(
        self, request: ContainerJobActivityRequest
    ) -> ContainerJobActivityResult: pass

# Only the exact image id and registry repo digests are read; never the full
# manifest, so unbounded inspect output cannot reach the observation payload.
_INSPECT_FORMAT = "{{.Id}}\t{{join .RepoDigests \",\"}}"
_LOCAL_INSPECT_FORMAT = (
    '{"id":{{json .Id}},"repoDigests":{{json .RepoDigests}},'
    '"created":{{json .Created}},"os":{{json .Os}},'
    '"architecture":{{json .Architecture}},"labels":{{json .Config.Labels}}}'
)
# Bytes of bounded pull progress retained as diagnostics evidence. The full,
# unbounded pull output never crosses the activity/Temporal boundary.
_PULL_DIAGNOSTICS_MAX_BYTES = 8192
_BUILD_DIAGNOSTICS_MAX_BYTES = 16_384

LABEL_IMAGE_SOURCE = "io.moonmind.image-source"
LABEL_IMAGE_BUILD_KEY = "io.moonmind.build-key"
LABEL_IMAGE_BUILT_AT = "io.moonmind.built-at"
LABEL_IMAGE_RECIPE_VERSION = "io.moonmind.recipe-version"


@dataclass(frozen=True)
class _LocalImageObservation:
    present: bool
    resolved_ref: str
    digest: str | None
    fresh: bool

# Live incremental-log plane bounds (MoonLadderStudios/MoonMind#3258). Live
# events are a bounded, best-effort projection published to the shared Live Logs
# spool while a job runs; the durable terminal artifacts remain authoritative.
# ``_MAX_LIVE_LOG_EVENTS`` is the total retention ceiling per job, enforced via
# the monotonic sequence carried in the resumable cursor.
_MAX_LIVE_LOG_EVENTS = 5000
_LIVE_LOG_ENTRY_MAX_CHARS = 8192
_LIVE_EVENTS_JOURNAL_NAME = "observability.events.jsonl"
# Bounded in-Activity retry for transient credential-directory removal failures.
# The removal is idempotent, so retrying a transient OSError cannot leak state.
_CREDENTIAL_CLEANUP_ATTEMPTS = 3
_CREDENTIAL_CLEANUP_BACKOFF_SECONDS = 0.1
# ``docker logs --timestamps`` prefixes every line with an RFC3339Nano instant.
_DOCKER_TS_LINE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2}))(?:\s(?P<text>.*))?$"
)
# File types accepted by declared-output collection. Anything else (device,
# fifo, socket, symlink target escaping the workspace) is rejected, not copied.
_ALLOWED_OUTPUT_MEDIA_FALLBACK = "application/octet-stream"


def _parse_rfc3339(value: str) -> datetime | None:
    """Parse an RFC3339 (Docker) timestamp into an aware datetime, or ``None``.

    Docker emits nanosecond precision, which ``fromisoformat`` cannot parse; the
    fractional part is truncated to microseconds and a trailing ``Z`` is
    normalized to an explicit UTC offset.
    """

    text = str(value or "").strip()
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    match = re.match(r"^(.*?\.\d{6})\d*(.*)$", text)
    if match:
        text = match.group(1) + match.group(2)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _parse_container_timing(
    state: dict,
) -> tuple[datetime | None, datetime | None, int | None]:
    """Extract start/finish/duration from a ``docker inspect .State`` payload."""

    started = _parse_rfc3339(str(state.get("StartedAt") or ""))
    finished = _parse_rfc3339(str(state.get("FinishedAt") or ""))
    # Docker reports the zero instant for a boundary that never occurred.
    if started is not None and started.year <= 1:
        started = None
    if finished is not None and finished.year <= 1:
        finished = None
    duration_ms: int | None = None
    if started is not None and finished is not None and finished >= started:
        duration_ms = int((finished - started).total_seconds() * 1000)
    return started, finished, duration_ms


def _parse_log_cursor(cursor: str | None) -> tuple[datetime | None, int, int]:
    """Decode ``timestamp|sequence|timestamp-offset`` (legacy cursors allowed)."""

    if not cursor:
        return None, 0, 0
    parts = str(cursor).split("|")
    raw_ts = parts[0]
    raw_seq = parts[1] if len(parts) > 1 else "0"
    raw_offset = parts[2] if len(parts) > 2 else "0"
    since = _parse_rfc3339(raw_ts) if raw_ts else None
    try:
        sequence = max(0, int(raw_seq))
    except (TypeError, ValueError):
        sequence = 0
    try:
        timestamp_offset = max(0, int(raw_offset))
    except (TypeError, ValueError):
        timestamp_offset = 0
    return since, sequence, timestamp_offset


class _OutputRejected(RuntimeError):
    """Internal signal that a declared output breaches a collection policy."""


class DockerContainerJobBackend:
    """Thin, deployment-selected Docker CLI adapter with owned identities."""

    def __init__(
        self,
        *,
        workspace_root: str | Path,
        settings: ContainerBackendSettings | None = None,
        docker_binary: str = "docker",
        docker_host: str | None = None,
        backend_ref: str = "system",
        command_runner: CommandRunner | None = None,
        evidence_publisher: EvidencePublisher | None = None,
        projection_writer: ProjectionWriter | None = None,
        registry_auth_resolver: RegistryAuthResolver | None = None,
        auth_root: str | Path | None = None,
        image_lock: ImageAcquisitionLock | None = None,
        capacity_lock: CapacityAdmissionLock | None = None,
        image_lock_root: str | Path | None = None,
        pull_lease_ttl_seconds: float = 240.0,
        pull_lock_poll_seconds: float = 2.0,
        pull_lock_max_wait_seconds: float = 280.0,
        secret_resolver: SecretResolver | None = None,
        managed_run_store: ManagedRunRecordStore | None = None,
        workspace_volume_name: str | None = None,
        log_spool_root: str | Path | None = None,
        live_log_max_events: int = _MAX_LIVE_LOG_EVENTS,
    ) -> None:
        self._workspace_root = Path(workspace_root).resolve()
        # Deployment-owned policy. Default to env-independent defaults so unit
        # construction is deterministic; trusted worker code passes resolved
        # settings sourced from deployment configuration.
        self._settings = settings or resolve_container_backend_settings({})
        self._docker_binary = docker_binary
        self._docker_host = docker_host or self._settings.endpoint
        self._backend_ref = backend_ref
        self._runner = command_runner or self._run
        self._publish = evidence_publisher
        self._write_projection = projection_writer
        self._resolve_registry_auth = (
            registry_auth_resolver or resolve_registry_pull_credentials
        )
        # Per-job ephemeral Docker auth material lives under a dedicated,
        # deployment-writable root, never inside the mounted job workspace.
        self._auth_root = (
            Path(auth_root).resolve()
            if auth_root is not None
            else self._workspace_root.parent / ".mm-container-job-auth"
        )
        lock_root = (
            Path(image_lock_root)
            if image_lock_root is not None
            else self._workspace_root.parent / ".moonmind-image-acquisition-locks"
        )
        self._image_lock = image_lock or FilesystemImageAcquisitionLock(lock_root)
        self._capacity_lock = capacity_lock or FilesystemCapacityAdmissionLock(
            lock_root / "capacity"
        )
        self._pull_lease_ttl_seconds = pull_lease_ttl_seconds
        self._pull_lock_poll_seconds = pull_lock_poll_seconds
        self._pull_lock_max_wait_seconds = pull_lock_max_wait_seconds
        self._resolve_secret = secret_resolver
        self._managed_run_store = managed_run_store
        self._workspace_volume_name = str(workspace_volume_name or "").strip() or None
        if self._workspace_volume_name is not None and not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.-]*", self._workspace_volume_name
        ):
            raise ValueError("workspace_volume_name has invalid format")
        # Live incremental logs are published to a MoonMind-controlled spool
        # root, never into the caller's mounted job workspace (which the
        # container itself sees at /workspace). When unset, live logging is a
        # no-op and only the durable terminal artifacts are produced.
        self._log_spool_root = (
            Path(log_spool_root).resolve()
            if log_spool_root is not None
            else None
        )
        self._live_log_max_events = max(0, int(live_log_max_events))

    # ------------------------------------------------------------------ helpers

    async def _run(self, args: Sequence[str]) -> tuple[int, bytes, bytes]:
        env = os.environ.copy()
        if self._docker_host:
            env["DOCKER_HOST"] = self._docker_host
        return await run_runtime_command(
            (self._docker_binary, *args),
            env=env,
        )

    async def _checked(self, *args: str) -> str:
        code, stdout, stderr = await self._runner(args)
        if code:
            detail = stderr.decode(errors="replace").strip()[:1000]
            raise RuntimeError(f"docker {' '.join(args[:2])} failed: {detail}")
        return stdout.decode(errors="replace").strip()

    @staticmethod
    def _name(request: ContainerJobActivityRequest) -> str:
        suffix = hashlib.sha256(request.ownership_token.encode()).hexdigest()[:20]
        return f"moonmind-container-job-{suffix}"

    @staticmethod
    def _owner_scoped_cache_volume_name(
        request: ContainerJobActivityRequest,
        base_volume_name: str,
    ) -> tuple[str, str]:
        owner_identity = (
            f"{request.owner.principal_type}:{request.owner.principal_id}"
        )
        owner_digest = hashlib.sha256(owner_identity.encode("utf-8")).hexdigest()[:20]
        prefix = base_volume_name[: 255 - len(owner_digest) - 1]
        return f"{prefix}-{owner_digest}", owner_digest

    async def _ensure_owner_scoped_cache_volume(
        self,
        request: ContainerJobActivityRequest,
        *,
        base_volume_name: str,
        cache_ref: str,
    ) -> str:
        volume_name, owner_digest = self._owner_scoped_cache_volume_name(
            request, base_volume_name
        )
        code, _stdout, _stderr = await self._runner(
            (
                "volume",
                "create",
                "--label",
                "moonmind.kind=container-job-cache",
                "--label",
                f"moonmind.cache_ref={cache_ref}",
                "--label",
                f"moonmind.cache_owner={owner_digest}",
                volume_name,
            )
        )
        if code:
            raise RuntimeError("container cache volume could not be materialized")
        return volume_name

    async def _owned_ownership_label(self, name: str) -> str | None:
        """Return the ownership label of an existing container, or ``None``.

        A missing container yields ``None``. A container that exists but carries
        no MoonMind ownership label yields an empty string so callers can treat
        it as a foreign collision.
        """

        code, stdout, _ = await self._runner(
            ("inspect", "--format", "{{json .Config.Labels}}", name)
        )
        if code:
            return None
        try:
            labels = json.loads(stdout.decode(errors="replace").strip())
        except (json.JSONDecodeError, TypeError):
            return ""
        return str(labels.get(LABEL_OWNERSHIP, "")) if isinstance(labels, dict) else ""

    async def _reject_ownership_collision(
        self, request: ContainerJobActivityRequest, name: str
    ) -> str | None:
        existing = await self._owned_ownership_label(name)
        if existing is not None and existing != request.ownership_token:
            raise RuntimeError(
                "container name collision: an existing container is owned by a "
                "different job and will not be reused"
            )
        return existing

    def _correlation_label(self, request: ContainerJobActivityRequest) -> str:
        source = request.request.source
        for candidate in (
            source.workflow_id,
            source.caller_request_id,
            source.managed_session_id,
            source.agent_run_id,
            source.omnigent_session_id,
        ):
            if candidate:
                return str(candidate)[:255]
        return str(source.source)

    def _expiry_label(self, request: ContainerJobActivityRequest) -> str:
        ttl = request.request.spec.timeout_seconds + _EXPIRY_GRACE_SECONDS
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl)
        return expires_at.isoformat().replace("+00:00", "Z")

    def _enforce_resource_ceilings(
        self, request: ContainerJobActivityRequest
    ) -> None:
        spec = request.request.spec
        ceilings = self._settings
        checks = (
            (spec.resources.cpu_millis, ceilings.max_cpu_millis, "cpuMillis"),
            (spec.resources.memory_mib, ceilings.max_memory_mib, "memoryMiB"),
            (spec.resources.pids, ceilings.max_pids, "pids"),
            (spec.timeout_seconds, ceilings.max_timeout_seconds, "timeoutSeconds"),
        )
        for requested, ceiling, name in checks:
            if requested > ceiling:
                raise ContainerJobBackendError(
                    ContainerJobFailureClass.RESOURCE_LIMIT_EXCEEDED,
                    f"{name}={requested} exceeds the deployment ceiling {ceiling} "
                    "and cannot be raised by a caller",
                )
        gpu = spec.resources.gpu
        if gpu is not None and ceilings.max_gpu_count is not None:
            # ``all`` is unbounded by definition, so a deployment that publishes
            # a finite device ceiling rejects it rather than silently clamping a
            # billing-relevant resource value.
            requested_devices = gpu.count
            if requested_devices == "all" or requested_devices > ceilings.max_gpu_count:
                raise ContainerJobBackendError(
                    ContainerJobFailureClass.GPU_COUNT_UNSUPPORTED,
                    f"gpu.count={requested_devices} exceeds the deployment ceiling "
                    f"{ceilings.max_gpu_count} and cannot be raised by a caller",
                )

    async def _report_gpu_support(
        self, gpu: WorkloadGpuRequest
    ) -> GpuObservation:
        """Return the selected daemon's support report for one GPU request.

        Reports before the container starts, so an unsupported vendor or a
        daemon without the device-request API is refused with a stable generic
        class instead of running the caller's workload without the resource it
        asked for. Runtime and device availability are not observable without a
        launch; the daemon's own refusal is classified at create/start instead.
        """

        if gpu.vendor not in WORKLOAD_GPU_VENDORS:
            raise ContainerJobBackendError(
                ContainerJobFailureClass.GPU_VENDOR_UNSUPPORTED,
                f"gpu.vendor={gpu.vendor} is not realizable by the selected "
                "container backend",
            )
        code, stdout, stderr = await self._runner(
            ("version", "--format", "{{.Server.Version}}")
        )
        if code:
            # This message becomes the caller-visible terminal outcome, and the
            # daemon's own connection diagnostic can name the deployment-owned
            # endpoint, its TLS material, or credential-bearing connection
            # detail. The caller contract stays a fixed string; the redacted
            # diagnostic goes only to trusted backend logs.
            logger.warning(
                "Container-job GPU support probe could not reach the container "
                "backend endpoint: %s",
                redact_sensitive_text(
                    stderr.decode(errors="replace").strip()[:500]
                ),
            )
            raise ContainerJobBackendError(
                ContainerJobFailureClass.INFRASTRUCTURE,
                "container backend endpoint is unreachable",
            )
        server_version = stdout.decode(errors="replace").strip()
        major_version = _docker_major_version(server_version)
        if major_version is None or major_version < _MIN_DEVICE_REQUEST_DOCKER_MAJOR:
            raise ContainerJobBackendError(
                ContainerJobFailureClass.GPU_BACKEND_UNSUPPORTED,
                "selected container backend does not support device requests; "
                f"daemon reported {server_version or 'unknown'}",
            )
        return gpu_observation(gpu, backend_supported=True)

    def _reject_gpu_launch_refusal(
        self, gpu: WorkloadGpuRequest | None, *, stderr: bytes, exit_code: int
    ) -> None:
        """Raise the stable GPU class when the daemon refused the device request.

        The shared launch classifier is the single authority for what counts as a
        refused device request, so the canonical backend and the unrestricted
        launcher agree on the evidence. Container create and start are their own
        commands here, so such a failure is never an application exit. The daemon
        diagnostic itself is never echoed: it can name trusted host paths and
        endpoints.
        """

        failure = gpu_launch_refusal(
            gpu=gpu,
            stderr=stderr.decode(errors="replace"),
            exit_code=exit_code,
        )
        if failure is None:
            return
        raise ContainerJobBackendError(
            _GPU_LAUNCH_FAILURE_CLASSES[failure.reason],
            "selected container backend refused the requested GPU resource "
            f"({failure.reason})",
        )

    def _capacity_lock_key(self) -> str:
        raw = f"{self._backend_ref}\ncontainer-job-active-memory".encode()
        return hashlib.sha256(raw).hexdigest()

    async def _acquire_capacity_lock(self) -> _CapacityAdmissionLease:
        key = self._capacity_lock_key()
        try:
            return await self._capacity_lock.acquire(
                key,
                wait_seconds=_CAPACITY_LOCK_WAIT_SECONDS,
                poll_seconds=_CAPACITY_LOCK_POLL_SECONDS,
            )
        except TimeoutError as exc:
            raise ContainerJobBackendError(
                ContainerJobFailureClass.INFRASTRUCTURE,
                "container-job capacity admission remained busy",
            ) from exc

    async def _active_memory_budget_mib(self) -> int:
        code, stdout, _ = await self._runner(
            ("info", "--format", "{{.MemTotal}}")
        )
        try:
            daemon_memory_bytes = int(stdout.decode(errors="replace").strip())
        except ValueError as exc:
            raise ContainerJobBackendError(
                ContainerJobFailureClass.INFRASTRUCTURE,
                "container backend did not report a valid memory capacity",
            ) from exc
        daemon_memory_mib = daemon_memory_bytes // _MIB
        if code or daemon_memory_mib < 16:
            raise ContainerJobBackendError(
                ContainerJobFailureClass.INFRASTRUCTURE,
                "container backend memory capacity is unavailable",
            )
        configured = self._settings.max_active_memory_mib
        automatic = max(
            16, int(daemon_memory_mib * _AUTO_ACTIVE_MEMORY_FRACTION)
        )
        if configured is None:
            return automatic
        if configured > daemon_memory_mib:
            raise ContainerJobBackendError(
                ContainerJobFailureClass.RESOURCE_LIMIT_EXCEEDED,
                "configured active container-job memory exceeds daemon capacity",
            )
        return min(configured, automatic)

    async def _active_container_memory_mib(self, *, exclude: str) -> int:
        code, stdout, _ = await self._runner(
            (
                "ps",
                "--all",
                "--filter",
                f"label={LABEL_CONTAINER_JOB}",
                "--filter",
                "status=running",
                "--format",
                "{{.Names}}",
            )
        )
        if code:
            raise ContainerJobBackendError(
                ContainerJobFailureClass.INFRASTRUCTURE,
                "active container-job inventory is unavailable",
            )
        names = tuple(
            name
            for name in stdout.decode(errors="replace").splitlines()
            if name and name != exclude
        )
        if not names:
            return 0
        code, stdout, _ = await self._runner(
            (
                "inspect",
                "--format",
                "{{.HostConfig.Memory}}",
                *names,
            )
        )
        if code:
            raise ContainerJobBackendError(
                ContainerJobFailureClass.INFRASTRUCTURE,
                "active container-job memory limits are unavailable",
            )
        total_bytes = 0
        try:
            for raw_limit in stdout.decode(errors="replace").splitlines():
                memory_bytes = int(raw_limit.strip())
                if memory_bytes <= 0:
                    raise ValueError("unbounded memory limit")
                total_bytes += memory_bytes
        except ValueError as exc:
            raise ContainerJobBackendError(
                ContainerJobFailureClass.INFRASTRUCTURE,
                "active container-job memory limits are invalid",
            ) from exc
        return (total_bytes + _MIB - 1) // _MIB

    async def _enforce_active_memory_budget(
        self, request: ContainerJobActivityRequest, *, container_name: str
    ) -> None:
        budget_mib = await self._active_memory_budget_mib()
        active_mib = await self._active_container_memory_mib(
            exclude=container_name
        )
        requested_mib = request.request.spec.resources.memory_mib
        if active_mib + requested_mib > budget_mib:
            raise ContainerJobBackendError(
                ContainerJobFailureClass.RESOURCE_LIMIT_EXCEEDED,
                "container-job active memory budget is exhausted; retry after "
                "another container job finishes or request less memory",
            )

    @staticmethod
    def _reject_forbidden_launch_args(
        args: Sequence[str], *, expected_network: str | None = None
    ) -> None:
        network_values: list[str] = []
        for index, token in enumerate(args):
            if token == "--network":
                if index + 1 >= len(args):
                    raise RuntimeError("refusing to launch without a network value")
                network_values.append(args[index + 1])
            elif token.startswith("--network="):
                network_values.append(token.split("=", 1)[1])
        if expected_network is not None and network_values != [expected_network]:
            raise RuntimeError(
                "refusing to launch owned container with an unapproved network"
            )
        for token in args:
            flag = token.split("=", 1)[0]
            if flag in _FORBIDDEN_LAUNCH_FLAGS:
                raise RuntimeError(
                    f"refusing to launch owned container with forbidden flag: {flag}"
                )
            if token.lower() in _TRUTHY_PRIVILEGED:
                raise RuntimeError(
                    "refusing to launch owned container in privileged mode"
                )
            if any(source in token for source in _FORBIDDEN_MOUNT_SOURCES):
                raise RuntimeError(
                    "refusing to launch owned container with a forbidden mount source"
                )

    # -------------------------------------------------------------- operations

    async def check_readiness(self) -> ContainerJobActivityResult:
        """Fail fast when the deployment-selected endpoint is missing/unreachable."""

        if not self._settings.enabled:
            raise ContainerBackendReadinessError(
                "container-job backend is disabled by deployment configuration"
            )
        self._settings.require_endpoint()
        code, stdout, stderr = await self._runner(
            ("version", "--format", "{{.Server.Version}}")
        )
        if code:
            detail = stderr.decode(errors="replace").strip()[:500]
            raise ContainerBackendReadinessError(
                f"container-job backend endpoint is unreachable: {detail}"
            )
        if self._workspace_volume_name is not None:
            server_version = stdout.decode(errors="replace").strip()
            major_version = _docker_major_version(server_version)
            if (
                major_version is None
                or major_version < _MIN_VOLUME_SUBPATH_DOCKER_MAJOR
            ):
                observed = server_version or "unknown"
                raise ContainerBackendReadinessError(
                    "container-job backend requires Docker Engine 26 or newer "
                    "for workspace volume subpath mounts; selected daemon "
                    f"reported {observed}"
                )
        return ContainerJobActivityResult()

    async def network_ready(self, network_ref: str) -> bool:
        """Return live Docker authority for one deployment-owned network ref."""

        code, _, _ = await self._runner(("network", "inspect", network_ref))
        return code == 0

    @property
    def command_runner(self) -> CommandRunner:
        """Expose only the normalized trusted runner for backend attestation."""

        return self._runner

    async def resolve_workspace(self, request: ContainerJobActivityRequest):
        locator = request.request.spec.workspace_ref
        if isinstance(locator, ManagedWorkspaceLocator):
            if self._managed_run_store is None:
                raise RuntimeError("managed run store is unavailable")
            workspace = resolve_managed_workspace_locator(
                locator,
                store=self._managed_run_store,
                current_agent_run_id=locator.agent_run_id,
                current_runtime_id=locator.runtime_id,
            )
        elif isinstance(locator, SandboxWorkspaceLocator):
            sandbox_root = (self._workspace_root / "temporal_sandbox").resolve()
            workspace_root = (sandbox_root / locator.workspace_id).resolve()
            if workspace_root.parent != sandbox_root:
                raise RuntimeError("container-job sandbox identity escapes its authority")
            workspace = (workspace_root / locator.relative_path).resolve()
            if not workspace.is_relative_to(workspace_root):
                raise RuntimeError("authorized container-job workspace escapes its authority")
        elif isinstance(locator, ExternalStateLocator):
            safe = re.sub(r"[^A-Za-z0-9_.-]", "_", locator.artifact_ref)
            workspace = (self._workspace_root / safe).resolve()
        else:  # pragma: no cover - discriminated schema prevents this
            raise RuntimeError("unsupported container-job workspace locator")
        if (
            not workspace.is_relative_to(self._workspace_root)
            or not workspace.is_dir()
        ):
            raise RuntimeError("authorized container-job workspace is unavailable")
        # Report a non-sensitive probe result only; the resolved host path is
        # returned for the trusted launch boundary but never recorded as an
        # observation.
        result = ContainerJobActivityResult(
            resolvedWorkspaceRef=str(workspace), workspaceProbe="visible"
        )
        if self._workspace_volume_name is not None:
            relative = workspace.relative_to(self._workspace_root).as_posix()
            if not relative or relative == "." or "," in relative:
                raise RuntimeError(
                    "authorized container-job workspace has an invalid volume subpath"
                )
            result.resolved_workspace_volume_name = self._workspace_volume_name
            result.resolved_workspace_volume_subpath = relative
        return result

    async def _inspect_image(
        self, image: str
    ) -> tuple[bool, str, str | None]:
        """Return ``(present, resolved_launch_ref, resolved_digest)``.

        Presence is probed on the selected daemon, never in the caller
        container. The resolved launch reference is the exact image id so the
        container launches the observed content, not a mutable tag.
        """

        code, stdout, stderr = await self._runner(
            ("image", "inspect", "--format", _INSPECT_FORMAT, image)
        )
        if code:
            detail = stderr.decode(errors="replace")
            if "no such image" not in detail.lower() and "not found" not in detail.lower():
                failure = classify_pull_failure(detail)
                if failure == ContainerJobFailureClass.IMAGE:
                    failure = ContainerJobFailureClass.IMAGE_BACKEND_UNAVAILABLE
                raise ImageAcquisitionError(
                    "docker image inspection failed",
                    failure_class=failure,
                )
            return False, image, None
        text = stdout.decode(errors="replace").strip()
        image_id, _, repo_digests = text.partition("\t")
        image_id = image_id.strip()
        digest = parse_resolved_digest(repo_digests, image_id)
        return True, (image_id or image), digest

    async def _publish_pull_diagnostics(
        self,
        request: ContainerJobActivityRequest,
        stdout: bytes,
        stderr: bytes,
    ) -> str | None:
        """Publish a bounded tail of pull output as durable diagnostics.

        The bounded tail is the only pull output that leaves this activity; the
        raw, potentially multi-gigabyte progress stream never reaches Temporal
        history.
        """

        if self._publish is None:
            return None
        combined = stdout + (b"\n[stderr]\n" + stderr if stderr else b"")
        bounded = combined[-_PULL_DIAGNOSTICS_MAX_BYTES:]
        try:
            return await self._publish(
                request, f"{request.job_id}-image-pull.txt", bounded
            )
        except Exception:
            # Diagnostics are auxiliary evidence and must never mask the pull
            # outcome; a failure to persist them is tolerated.
            return None

    async def _pull_image(
        self, request: ContainerJobActivityRequest, image: str
    ) -> tuple[int, str | None]:
        """Pull ``image``, returning ``(duration_ms, diagnostics_ref)``.

        Raises :class:`ImageAcquisitionError` with a granular failure class when
        the pull fails, attaching the bounded diagnostics reference.
        """

        started = time.monotonic()
        code, stdout, stderr = await self._runner(("pull", image))
        duration_ms = int((time.monotonic() - started) * 1000)
        diagnostics_ref = await self._publish_pull_diagnostics(
            request, stdout, stderr
        )
        if code:
            failure = classify_pull_failure(stderr.decode(errors="replace"))
            raise ImageAcquisitionError(
                f"docker pull failed for the requested image ({failure.value})",
                failure_class=failure,
                diagnostics_ref=diagnostics_ref,
            )
        return duration_ms, diagnostics_ref

    def _local_build_key(self, recipe: LocalImageRecipe, platform: str) -> str:
        """Hash the normalized recipe and only its declared effective inputs."""

        root = recipe.context_root.resolve()
        digest = hashlib.sha256()
        normalized_recipe = {
            "sourceRef": recipe.source_ref,
            "image": recipe.image,
            "dockerfile": recipe.dockerfile,
            "target": recipe.target,
            "buildArgs": list(recipe.build_args),
            "fingerprintInputs": list(recipe.fingerprint_inputs),
            "recipeVersion": recipe.recipe_version,
            "platform": platform,
        }
        digest.update(
            json.dumps(
                normalized_recipe, sort_keys=True, separators=(",", ":")
            ).encode()
        )
        for pattern in recipe.fingerprint_inputs:
            matches: list[Path] = []
            for candidate in root.glob(pattern):
                resolved = candidate.resolve()
                if not resolved.is_relative_to(root):
                    raise ImageAcquisitionError(
                        "local image build input escapes the deployment root",
                        failure_class=ContainerJobFailureClass.IMAGE_BUILD_INPUTS_UNAVAILABLE,
                    )
                if resolved.is_file():
                    matches.append(resolved)
            if not matches:
                raise ImageAcquisitionError(
                    f"local image build input is unavailable: {pattern}",
                    failure_class=ContainerJobFailureClass.IMAGE_BUILD_INPUTS_UNAVAILABLE,
                )
            for path in sorted(set(matches)):
                relative = path.relative_to(root).as_posix()
                digest.update(b"\0path\0")
                digest.update(relative.encode())
                with path.open("rb") as handle:
                    while chunk := handle.read(1024 * 1024):
                        digest.update(chunk)
        return f"sha256:{digest.hexdigest()}"

    async def _daemon_platform(self) -> str:
        code, stdout, stderr = await self._runner(
            ("version", "--format", "{{.Server.Os}}/{{.Server.Arch}}")
        )
        platform = stdout.decode(errors="replace").strip()
        if code or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", platform):
            detail = stderr.decode(errors="replace").strip()[:500]
            raise ImageAcquisitionError(
                f"container backend platform is unavailable: {detail}",
                failure_class=ContainerJobFailureClass.IMAGE_BACKEND_UNAVAILABLE,
            )
        return platform.lower()

    async def _inspect_local_image(
        self,
        recipe: LocalImageRecipe,
        *,
        build_key: str,
        platform: str,
    ) -> _LocalImageObservation:
        code, stdout, stderr = await self._runner(
            ("image", "inspect", "--format", _LOCAL_INSPECT_FORMAT, recipe.image)
        )
        if code:
            detail = stderr.decode(errors="replace").lower()
            if "no such image" in detail or "not found" in detail:
                return _LocalImageObservation(False, recipe.image, None, False)
            raise ImageAcquisitionError(
                "docker image inspection failed",
                failure_class=ContainerJobFailureClass.IMAGE_BACKEND_UNAVAILABLE,
            )
        try:
            observed = json.loads(stdout.decode(errors="replace"))
        except (json.JSONDecodeError, TypeError) as exc:
            raise ImageAcquisitionError(
                "docker returned an invalid local image observation",
                failure_class=ContainerJobFailureClass.IMAGE_BACKEND_UNAVAILABLE,
            ) from exc
        image_id = str(observed.get("id") or "").strip()
        repo_digests = ",".join(observed.get("repoDigests") or [])
        digest = parse_resolved_digest(repo_digests, image_id)
        labels = observed.get("labels") or {}
        observed_platform = (
            f"{str(observed.get('os') or '').lower()}/"
            f"{str(observed.get('architecture') or '').lower()}"
        )
        fresh = (
            isinstance(labels, dict)
            and labels.get(LABEL_IMAGE_SOURCE) == recipe.source_ref
            and labels.get(LABEL_IMAGE_BUILD_KEY) == build_key
            and labels.get(LABEL_IMAGE_RECIPE_VERSION) == recipe.recipe_version
            and observed_platform == platform
        )
        if fresh and recipe.max_age_seconds is not None:
            built_at = _parse_rfc3339(str(labels.get(LABEL_IMAGE_BUILT_AT) or ""))
            if built_at is None:
                fresh = False
            else:
                age = datetime.now(timezone.utc) - built_at
                fresh = timedelta(0) <= age <= timedelta(
                    seconds=recipe.max_age_seconds
                )
        return _LocalImageObservation(
            True, image_id or recipe.image, digest, fresh
        )

    async def _publish_build_diagnostics(
        self,
        request: ContainerJobActivityRequest,
        stdout: bytes,
        stderr: bytes,
    ) -> str | None:
        if self._publish is None:
            return None
        combined = stdout + (b"\n[stderr]\n" + stderr if stderr else b"")
        try:
            return await self._publish(
                request,
                f"{request.job_id}-image-build.txt",
                combined[-_BUILD_DIAGNOSTICS_MAX_BYTES:],
            )
        except Exception:
            return None

    async def _build_local_image(
        self,
        request: ContainerJobActivityRequest,
        recipe: LocalImageRecipe,
        *,
        build_key: str,
        platform: str,
    ) -> tuple[int, str | None]:
        root = recipe.context_root.resolve()
        dockerfile = (root / recipe.dockerfile).resolve()
        if not dockerfile.is_relative_to(root) or not dockerfile.is_file():
            raise ImageAcquisitionError(
                "configured local image Dockerfile is unavailable",
                failure_class=ContainerJobFailureClass.IMAGE_BUILD_INPUTS_UNAVAILABLE,
            )
        if self._write_projection is not None:
            request.state = ContainerJobState.BUILDING_IMAGE
            try:
                await self._write_projection(request)
            except Exception:
                # The durable workflow remains authoritative. A failed status
                # projection must not turn a successful provision into failure.
                pass
        built_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        args: list[str] = [
            "build",
            # The deployment uses a Buildx docker-container builder. Without an
            # explicit output mode that builder can report a successful build
            # while leaving the daemon's existing tag unchanged, so the
            # authoritative post-build inspection correctly sees a stale image.
            # Load the result into the selected Docker Engine before validating
            # the freshness labels.
            "--load",
            "--file",
            str(dockerfile),
            "--target",
            recipe.target,
            "--tag",
            recipe.image,
            "--platform",
            platform,
            "--pull",
            "--label",
            f"{LABEL_IMAGE_SOURCE}={recipe.source_ref}",
            "--label",
            f"{LABEL_IMAGE_BUILD_KEY}={build_key}",
            "--label",
            f"{LABEL_IMAGE_BUILT_AT}={built_at}",
            "--label",
            f"{LABEL_IMAGE_RECIPE_VERSION}={recipe.recipe_version}",
        ]
        for name, value in recipe.build_args:
            args.extend(("--build-arg", f"{name}={value}"))
        args.append(str(root))
        started = time.monotonic()
        code, stdout, stderr = await self._runner(tuple(args))
        duration_ms = int((time.monotonic() - started) * 1000)
        diagnostics_ref = await self._publish_build_diagnostics(
            request, stdout, stderr
        )
        if code:
            detail = stderr.decode(errors="replace").lower()
            failure = (
                ContainerJobFailureClass.IMAGE_BUILD_TIMEOUT
                if "timed out" in detail or "deadline exceeded" in detail
                else ContainerJobFailureClass.IMAGE_BUILD_FAILED
            )
            raise ImageAcquisitionError(
                f"local image build failed for source {recipe.source_ref!r}",
                failure_class=failure,
                diagnostics_ref=diagnostics_ref,
            )
        return duration_ms, diagnostics_ref

    async def _validate_local_image(
        self, recipe: LocalImageRecipe, resolved_ref: str
    ) -> None:
        args = (
            "run",
            "--rm",
            "--network",
            recipe.validation_network_mode,
            resolved_ref,
            *recipe.validation_command,
        )
        code, _, _ = await self._runner(args)
        if code:
            raise ImageAcquisitionError(
                f"local image validation failed for source {recipe.source_ref!r}",
                failure_class=ContainerJobFailureClass.IMAGE_VALIDATION_FAILED,
            )

    async def _acquire_local_image(
        self, request: ContainerJobActivityRequest, recipe: LocalImageRecipe
    ) -> ContainerJobActivityResult:
        platform = await self._daemon_platform()
        build_key = await asyncio.to_thread(
            self._local_build_key, recipe, platform
        )
        observed = await self._inspect_local_image(
            recipe, build_key=build_key, platform=platform
        )
        present_at_start = observed.present
        fresh_at_start = observed.fresh
        if observed.fresh:
            await self._validate_local_image(recipe, observed.resolved_ref)
            return self._local_image_result(
                recipe,
                observed,
                build_key=build_key,
                cache_present=True,
                cache_hit=True,
                fresh_at_start=True,
                lock_wait_ms=0,
                action="reuse",
            )

        key = hashlib.sha256(
            f"{self._backend_ref}\n{recipe.source_ref}\n{platform}\n{build_key}".encode()
        ).hexdigest()
        owner_id = f"{os.getpid()}:{request.job_id}:{uuid.uuid4().hex}"
        waited_started = time.monotonic()
        lease_ttl = max(
            self._pull_lease_ttl_seconds,
            float(request.request.spec.timeout_seconds + 60),
        )
        max_wait = max(
            self._pull_lock_max_wait_seconds,
            float(request.request.spec.timeout_seconds + 60),
        )
        while True:
            lock_wait_ms = int((time.monotonic() - waited_started) * 1000)
            acquired = await self._image_lock.try_acquire(
                key, ttl_seconds=lease_ttl, owner_id=owner_id
            )
            if acquired:
                try:
                    observed = await self._inspect_local_image(
                        recipe, build_key=build_key, platform=platform
                    )
                    if observed.fresh:
                        await self._validate_local_image(
                            recipe, observed.resolved_ref
                        )
                        return self._local_image_result(
                            recipe,
                            observed,
                            build_key=build_key,
                            cache_present=present_at_start,
                            cache_hit=True,
                            fresh_at_start=fresh_at_start,
                            lock_wait_ms=lock_wait_ms,
                            action="reuse",
                        )
                    build_ms, diagnostics_ref = await self._build_local_image(
                        request,
                        recipe,
                        build_key=build_key,
                        platform=platform,
                    )
                    observed = await self._inspect_local_image(
                        recipe, build_key=build_key, platform=platform
                    )
                    if not observed.fresh:
                        raise ImageAcquisitionError(
                            "local image is not fresh after a completed build",
                            failure_class=ContainerJobFailureClass.IMAGE_BUILD_FAILED,
                            diagnostics_ref=diagnostics_ref,
                        )
                    await self._validate_local_image(recipe, observed.resolved_ref)
                    return self._local_image_result(
                        recipe,
                        observed,
                        build_key=build_key,
                        cache_present=present_at_start,
                        cache_hit=False,
                        fresh_at_start=fresh_at_start,
                        lock_wait_ms=lock_wait_ms,
                        action="build",
                        build_duration_ms=build_ms,
                        diagnostics_ref=diagnostics_ref,
                    )
                finally:
                    await self._image_lock.release(key, owner_id)

            await asyncio.sleep(self._pull_lock_poll_seconds)
            observed = await self._inspect_local_image(
                recipe, build_key=build_key, platform=platform
            )
            if observed.fresh:
                await self._validate_local_image(recipe, observed.resolved_ref)
                return self._local_image_result(
                    recipe,
                    observed,
                    build_key=build_key,
                    cache_present=present_at_start,
                    cache_hit=True,
                    fresh_at_start=fresh_at_start,
                    lock_wait_ms=int((time.monotonic() - waited_started) * 1000),
                    action="reuse",
                )
            if time.monotonic() - waited_started > max_wait:
                raise ImageAcquisitionError(
                    "timed out waiting for concurrent local image provisioning",
                    failure_class=ContainerJobFailureClass.IMAGE_BUILD_TIMEOUT,
                )

    def _local_image_result(
        self,
        recipe: LocalImageRecipe,
        observed: _LocalImageObservation,
        *,
        build_key: str,
        cache_present: bool,
        cache_hit: bool,
        fresh_at_start: bool,
        lock_wait_ms: int,
        action: str,
        build_duration_ms: int | None = None,
        diagnostics_ref: str | None = None,
    ) -> ContainerJobActivityResult:
        return ContainerJobActivityResult(
            resolvedImageRef=observed.resolved_ref,
            imageObservation=ImageObservation(
                requestedReference=recipe.image,
                sourceKind="local-build",
                imageSourceRef=recipe.source_ref,
                resolvedDigest=observed.digest,
                cachePresent=cache_present,
                cacheHit=cache_hit,
                buildKey=build_key,
                freshAtStart=fresh_at_start,
                provisionAction=action,
                provisionWaitedOnLock=lock_wait_ms > 0,
                pullLockWaitMs=max(0, lock_wait_ms),
                buildDurationMs=build_duration_ms,
            ),
            diagnosticsRef=diagnostics_ref,
        )

    async def acquire_image(self, request: ContainerJobActivityRequest):
        spec = request.request.spec
        # Workspace visibility is authorized before any expensive acquisition,
        # so a missing-image pull can never precede workspace resolution.
        if not request.resolved_workspace_ref:
            raise ImageAcquisitionError(
                "workspace must be resolved before image acquisition",
                failure_class=ContainerJobFailureClass.WORKSPACE,
            )

        if spec.image_source_ref is not None:
            try:
                source = self._settings.image_source(spec.image_source_ref)
            except Exception as exc:
                raise ImageAcquisitionError(
                    "requested deployment image source is not configured",
                    failure_class=ContainerJobFailureClass.IMAGE_BUILD_NOT_CONFIGURED,
                ) from exc
            if isinstance(source, LocalImageRecipe):
                return await self._acquire_local_image(request, source)
            if not isinstance(source, RegistryImageSource):
                raise ImageAcquisitionError(
                    "requested deployment image source kind is unsupported",
                    failure_class=ContainerJobFailureClass.IMAGE_BUILD_NOT_CONFIGURED,
                )
            image = source.image
            policy = source.pull_policy
            image_source_ref = source.source_ref
        else:
            if spec.image is None:  # schema validation is the public guard
                raise ImageAcquisitionError(
                    "container image is not configured",
                    failure_class=ContainerJobFailureClass.IMAGE_NOT_FOUND,
                )
            image = spec.image
            policy = spec.pull_policy
            image_source_ref = None

        credential_ref = spec.registry_credential_ref
        if credential_ref is not None:
            return await self._acquire_private_image(
                request, image, policy, credential_ref
            )

        normalized = normalize_image_reference(image)
        key = image_lock_key(self._backend_ref, normalized)

        present, resolved_ref, digest = await self._inspect_image(image)
        present_at_start = present

        # Cache reuse: a present image under a reuse policy pulls nothing.
        if present and policy != "always":
            return self._image_result(
                resolved_ref,
                requested=image,
                digest=digest,
                cache_present=True,
                cache_hit=True,
                fresh_at_start=True,
                lock_wait_ms=0,
                image_source_ref=image_source_ref,
                action="reuse",
            )

        if policy == "never":
            if image_source_ref is not None:
                detail = (
                    f"deployment image source {image_source_ref!r} is absent on "
                    "the selected backend and pullPolicy=never; provision its "
                    "configured image on that backend or change the "
                    "deployment-owned pull policy"
                )
            else:
                detail = "requested image is absent and pullPolicy=never"
            raise ImageAcquisitionError(
                detail,
                failure_class=ContainerJobFailureClass.IMAGE_NOT_FOUND,
            )

        # A missing image (or an authorized `always` refresh) is acquired once
        # per normalized identity on this backend while concurrent jobs wait and
        # re-inspect. Unrelated images use distinct keys and are not serialized.
        owner_id = f"{os.getpid()}:{request.job_id}:{uuid.uuid4().hex}"
        waited_started = time.monotonic()
        while True:
            lock_wait_ms = int((time.monotonic() - waited_started) * 1000)
            acquired = await self._image_lock.try_acquire(
                key,
                ttl_seconds=self._pull_lease_ttl_seconds,
                owner_id=owner_id,
            )
            if acquired:
                try:
                    # Re-inspect after winning the lease: a concurrent owner may
                    # have completed the pull, and the lease alone is never
                    # treated as proof the image is present.
                    present, resolved_ref, digest = await self._inspect_image(image)
                    if present and policy != "always":
                        return self._image_result(
                            resolved_ref,
                            requested=image,
                            digest=digest,
                            cache_present=present_at_start,
                            cache_hit=True,
                            fresh_at_start=present_at_start,
                            lock_wait_ms=lock_wait_ms,
                            image_source_ref=image_source_ref,
                            action="reuse",
                        )
                    pull_ms, diagnostics_ref = await self._pull_image(request, image)
                    present, resolved_ref, digest = await self._inspect_image(image)
                    if not present:
                        raise ImageAcquisitionError(
                            "image is still absent after a completed pull",
                            failure_class=ContainerJobFailureClass.IMAGE,
                            diagnostics_ref=diagnostics_ref,
                        )
                    return self._image_result(
                        resolved_ref,
                        requested=image,
                        digest=digest,
                        cache_present=present_at_start,
                        cache_hit=False,
                        fresh_at_start=present_at_start,
                        lock_wait_ms=lock_wait_ms,
                        pull_duration_ms=pull_ms,
                        diagnostics_ref=diagnostics_ref,
                        image_source_ref=image_source_ref,
                        action="pull",
                    )
                finally:
                    await self._image_lock.release(key, owner_id)

            # Another worker owns the pull; wait, then re-inspect for reuse.
            await asyncio.sleep(self._pull_lock_poll_seconds)
            present, resolved_ref, digest = await self._inspect_image(image)
            if present and policy != "always":
                return self._image_result(
                    resolved_ref,
                    requested=image,
                    digest=digest,
                    cache_present=False,
                    cache_hit=True,
                    fresh_at_start=False,
                    lock_wait_ms=int((time.monotonic() - waited_started) * 1000),
                    image_source_ref=image_source_ref,
                    action="reuse",
                )
            if time.monotonic() - waited_started > self._pull_lock_max_wait_seconds:
                raise ImageAcquisitionError(
                    "timed out waiting for a concurrent image pull to complete",
                    failure_class=ContainerJobFailureClass.IMAGE_PULL_TIMEOUT,
                )

    def _image_result(
        self,
        resolved_ref: str,
        *,
        requested: str,
        digest: str | None,
        cache_present: bool,
        cache_hit: bool,
        fresh_at_start: bool,
        lock_wait_ms: int,
        pull_duration_ms: int | None = None,
        diagnostics_ref: str | None = None,
        image_source_ref: str | None = None,
        action: str = "none",
    ) -> ContainerJobActivityResult:
        observation = ImageObservation(
            requestedReference=requested,
            sourceKind="registry",
            imageSourceRef=image_source_ref,
            resolvedDigest=digest,
            cachePresent=cache_present,
            cacheHit=cache_hit,
            freshAtStart=fresh_at_start,
            provisionAction=action,
            provisionWaitedOnLock=lock_wait_ms > 0,
            pullLockWaitMs=max(0, lock_wait_ms),
            pullDurationMs=pull_duration_ms,
        )
        return ContainerJobActivityResult(
            resolvedImageRef=resolved_ref,
            imageObservation=observation,
            diagnosticsRef=diagnostics_ref,
        )

    async def _acquire_private_image(
        self,
        request: ContainerJobActivityRequest,
        image: str,
        policy: str,
        credential_ref: str,
    ):
        # Authorization is re-checked on every run before either a cache hit or
        # a pull is accepted: image presence in the shared daemon never bypasses
        # policy. The API-owned decision travels with the request; the worker
        # only enforces and resolves the credential, it does not re-decide.
        authorization = request.registry_authorization
        if authorization is None or not authorization.authorized:
            raise ContainerJobBackendError(
                ContainerJobFailureClass.IMAGE_USE_DENIED,
                "private image use was not authorized for this job",
            )
        normalized = normalize_image_reference(image)
        self._enforce_authorized_scope(normalized, authorization)

        # A local inspect needs no registry authentication.
        inspect_code, stdout, _ = await self._runner(
            ("image", "inspect", "--format", "{{.Id}}", image)
        )
        cache_present = inspect_code == 0
        need_pull = policy == "always" or (not cache_present and policy == "if-missing")
        if policy == "never" and not cache_present:
            raise ContainerJobBackendError(
                ContainerJobFailureClass.IMAGE,
                "private image is absent under the 'never' pull policy",
            )

        if need_pull:
            credential = await self._resolve_credential(credential_ref)
            secrets = (credential.username, credential.secret)
            auth_dir = self._auth_dir(request)
            try:
                self._materialize_registry_auth(
                    auth_dir, normalized.registry, credential
                )
                await self._authorized_pull(image, auth_dir, secrets)
            finally:
                # Remove ephemeral auth immediately after the pull. The terminal
                # ``cleanup`` activity re-removes it deterministically so a crash
                # between here and cleanup cannot leak credentials.
                self._remove_auth_dir(auth_dir, best_effort=True)
            inspect_code, stdout, _ = await self._runner(
                ("image", "inspect", "--format", "{{.Id}}", image)
            )

        if inspect_code:
            raise ContainerJobBackendError(
                ContainerJobFailureClass.IMAGE,
                "private image is unavailable under the selected pull policy",
            )
        resolved = stdout.decode(errors="replace").strip() or image
        return ContainerJobActivityResult(resolvedImageRef=resolved)

    @staticmethod
    def _enforce_authorized_scope(
        normalized, authorization: RegistryAuthorization
    ) -> None:
        if normalized.registry.lower() != authorization.registry.lower():
            raise ContainerJobBackendError(
                ContainerJobFailureClass.REPOSITORY_SCOPE_MISMATCH,
                "resolved registry is outside the authorized scope",
            )
        if normalized.repository != authorization.repository:
            raise ContainerJobBackendError(
                ContainerJobFailureClass.REPOSITORY_SCOPE_MISMATCH,
                "resolved repository is outside the authorized scope",
            )
        if authorization.digest is not None and normalized.digest != authorization.digest:
            raise ContainerJobBackendError(
                ContainerJobFailureClass.REPOSITORY_SCOPE_MISMATCH,
                "resolved digest does not match the authorized digest",
            )

    async def _resolve_credential(self, credential_ref: str) -> RegistryCredential:
        try:
            return await self._resolve_registry_auth(credential_ref)
        except RegistryAuthResolutionError as exc:
            raise ContainerJobBackendError(
                ContainerJobFailureClass.CREDENTIAL_UNRESOLVED,
                "registry credential reference could not be resolved",
            ) from exc

    def _auth_dir(self, request: ContainerJobActivityRequest) -> Path:
        suffix = hashlib.sha256(request.ownership_token.encode()).hexdigest()[:20]
        return self._auth_root / f"job-{suffix}"

    def _materialize_registry_auth(
        self, auth_dir: Path, registry: str, credential: RegistryCredential
    ) -> None:
        """Write a per-job Docker config with restrictive ownership and mode."""

        self._remove_auth_dir(auth_dir, best_effort=True)
        auth_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(auth_dir, stat.S_IRWXU)  # 0o700
        auth_key = (
            "https://index.docker.io/v1/" if registry == "docker.io" else registry
        )
        config = {"auths": {auth_key: credential.docker_auth_entry()}}
        config_path = auth_dir / "config.json"
        fd = os.open(
            config_path,
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
            stat.S_IRUSR | stat.S_IWUSR,  # 0o600
        )
        try:
            os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)
            handle = os.fdopen(fd, "w", encoding="utf-8")
        except Exception:
            os.close(fd)
            raise
        with handle:
            json.dump(config, handle)

    def _remove_auth_dir(self, auth_dir: Path, *, best_effort: bool) -> None:
        if best_effort:
            shutil.rmtree(auth_dir, ignore_errors=True)
            return
        if auth_dir.exists():
            shutil.rmtree(auth_dir)

    async def _authorized_pull(
        self, image: str, auth_dir: Path, secrets: tuple[str, ...]
    ) -> None:
        code, _, stderr = await self._runner(
            ("--config", str(auth_dir), "pull", image)
        )
        if code:
            detail = _redact(stderr.decode(errors="replace").strip()[:1000], secrets)
            raise ContainerJobBackendError(
                ContainerJobFailureClass.REGISTRY_AUTH_FAILED,
                f"authorized registry pull failed: {detail}",
            )

    async def reconcile_container(self, request: ContainerJobActivityRequest):
        name = self._name(request)
        ownership = await self._reject_ownership_collision(request, name)
        if ownership is None:
            # Absence must remain absence across the workflow boundary.  A
            # synthetic ref here makes the workflow treat the container as a
            # reconciled prior attempt and skip the authoritative create step.
            return ContainerJobActivityResult(running=False)
        code, stdout, _ = await self._runner(
            ("inspect", "--format", "{{.State.Running}}", name)
        )
        if code:
            return ContainerJobActivityResult()
        # A reconciled container skips the authoritative create step, so its
        # durable launch-attestation reference would otherwise be lost and both
        # the runtime diagnostics and post-cleanup lifecycle artifacts would
        # publish launchAttestationRef=null. Re-attest the live restricted-egress
        # state and republish the attestation evidence so the recovery path can
        # correlate the running workload with its enforced launch policy.
        running = stdout.strip() == b"true"
        diagnostics_ref = await self._recover_launch_attestation(
            request,
            name,
            running=running,
        )
        return ContainerJobActivityResult(
            containerRef=name,
            running=running,
            diagnosticsRef=diagnostics_ref,
            # An owned container that exists is stronger evidence than a
            # capability report: the daemon already accepted this job's device
            # request, and a running container already carries it.
            gpuObservation=gpu_observation(
                request.request.spec.resources.gpu,
                backend_supported=True,
                launched=running,
            ),
        )

    async def _recover_launch_attestation(
        self,
        request: ContainerJobActivityRequest,
        name: str,
        *,
        running: bool,
    ) -> str | None:
        """Re-attest and republish launch evidence for a reconciled bridge job."""

        if request.request.spec.network_mode != "bridge" or self._publish is None:
            return None
        egress_attestation = await attest_docker_egress(
            runner=self._runner,
            profile=DEFAULT_EGRESS_PROFILE,
            backend_ref=self._backend_ref,
        )
        workload_evidence = None
        if running:
            workload_evidence = await attest_docker_workload_egress(
                runner=self._runner,
                profile=DEFAULT_EGRESS_PROFILE,
                attestation=egress_attestation,
                attachment_identity=name,
                expected_image_ref=str(request.resolved_image_ref or ""),
                started_at=request.started_at,
                finished_at=request.finished_at,
            )
        return await self._publish_container_job_egress_launch(
            request,
            attestation=egress_attestation,
            attachment_identity=name,
            workload_evidence=workload_evidence,
            reconciliation_result="recovered",
        )

    async def _publish_container_job_egress_launch(
        self,
        request: ContainerJobActivityRequest,
        *,
        attestation,
        attachment_identity: str,
        workload_evidence: dict[str, object] | None,
        reconciliation_result: str,
    ) -> str | None:
        """Publish one immutable launch-authority row through the real adapter."""

        if self._publish is None:
            return None
        if workload_evidence is not None:
            expected_image = str(request.resolved_image_ref or "").strip()
            observed_image = str(
                workload_evidence.get("workloadImageDigest") or ""
            ).strip()
            # The production image-acquisition owner returns an exact daemon
            # image id. Preserve in-flight compatibility for older tag-shaped
            # payloads, but never accept a different id for a current launch.
            if (
                expected_image.startswith("sha256:")
                and observed_image != expected_image
            ):
                raise RuntimeError(
                    "restricted-egress workload image does not match resolved authority"
                )
        evidence = {
            "schemaVersion": 1,
            "kind": "restricted-egress-launch-attestation",
            "conformanceRow": "generic_container_job",
            "evidenceStage": "running" if workload_evidence else "created_unstarted",
            "containerJobContractVersion": request.contract_version,
            "agentProfileRef": "container_job",
            "agentProfileVersion": request.request.contract_version,
            "securityPolicyRef": DEFAULT_EGRESS_PROFILE.security_review_ref,
            "securityPolicyVersion": DEFAULT_EGRESS_PROFILE.version,
            "egressProfileVersion": DEFAULT_EGRESS_PROFILE.version,
            "backendRef": self._backend_ref,
            "runtimeProvenance": "container_job/docker-engine",
            "requestedImageRef": request.request.spec.image,
            "resolvedImageRef": request.resolved_image_ref,
            "imageObservation": (
                request.image_observation.model_dump(
                    mode="json", by_alias=True, exclude_none=True
                )
                if request.image_observation is not None
                else None
            ),
            "attachmentIdentity": attachment_identity,
            "attestation": attestation.model_dump(by_alias=True, mode="json"),
            "deniedConnectionCount": 0,
            "denialDiagnostics": [],
            "cleanupResult": "pending",
            "reconciliationResult": reconciliation_result,
        }
        if workload_evidence is not None:
            evidence.update(workload_evidence)
        return await self._publish(
            request,
            f"{request.job_id}-egress-attestation.json",
            serialize_conformance_evidence(
                evidence, location="egress-attestation"
            ),
        )

    async def _materialized_env(
        self, request: ContainerJobActivityRequest
    ) -> list[str]:
        """Return ``--env`` argv pairs with execution-time secrets materialized.

        Secret values are injected only into the container launch arguments and
        are never returned to the workflow, persisted, or rendered into any
        diagnostics or evidence artifact.
        """

        args: list[str] = []
        for item in request.request.spec.environment:
            if item.secret_ref is not None:
                raise RuntimeError(
                    "secretRef is unsupported until container-job authority can "
                    "authorize each requested secret"
                )
            else:
                args.extend(("--env", f"{item.name}={item.value}"))
        return args

    async def create_container(self, request: ContainerJobActivityRequest):
        if not request.resolved_workspace_ref or not request.resolved_image_ref:
            raise RuntimeError("resolved workspace and image are required")
        self._enforce_resource_ceilings(request)
        spec = request.request.spec
        # Report the selected daemon's support for a caller-requested GPU
        # resource before anything is created, so an unsupported request never
        # reaches the caller's workload.
        resolved_gpu = (
            await self._report_gpu_support(spec.resources.gpu)
            if spec.resources.gpu is not None
            else None
        )
        name = self._name(request)
        if spec.network_mode not in {"none", "bridge"}:
            raise RuntimeError("network mode must be 'none' or policy-approved 'bridge'")
        # Prove network-layer egress enforcement before probing container state
        # or reserving a name; an unattested launch must fail closed first.
        egress_attestation = None
        network_mode = spec.network_mode
        if spec.network_mode == "bridge":
            egress_attestation = await attest_docker_egress(
                runner=self._runner,
                profile=DEFAULT_EGRESS_PROFILE,
                backend_ref=self._backend_ref,
            )
            network_mode = egress_attestation.network_ref
        await self._reject_ownership_collision(request, name)
        volume_name = request.resolved_workspace_volume_name
        volume_subpath = request.resolved_workspace_volume_subpath
        if (
            not volume_name
            and not volume_subpath
            and self._workspace_volume_name is not None
        ):
            # Already-running workflows may carry the pre-volume Activity shape.
            # Reconstruct only deployment-owned metadata at the trusted launch
            # boundary so those histories use the same daemon-visible mount.
            workspace = Path(request.resolved_workspace_ref).resolve()
            if not workspace.is_relative_to(self._workspace_root):
                raise RuntimeError("resolved workspace escapes its authority")
            derived_subpath = workspace.relative_to(self._workspace_root).as_posix()
            if not derived_subpath or derived_subpath == "." or "," in derived_subpath:
                raise RuntimeError(
                    "resolved workspace has an invalid volume subpath"
                )
            volume_name = self._workspace_volume_name
            volume_subpath = derived_subpath
        if bool(volume_name) != bool(volume_subpath):
            raise RuntimeError("resolved workspace volume metadata is incomplete")
        if volume_name and volume_subpath:
            if volume_name != self._workspace_volume_name:
                raise RuntimeError(
                    "resolved workspace volume is not deployment-authorized"
                )
            workspace = Path(request.resolved_workspace_ref).resolve()
            if not workspace.is_relative_to(self._workspace_root):
                raise RuntimeError("resolved workspace escapes its authority")
            expected_subpath = workspace.relative_to(self._workspace_root).as_posix()
            if volume_subpath != expected_subpath:
                raise RuntimeError(
                    "resolved workspace volume subpath does not match workspace"
                )
            workspace_mount = (
                f"type=volume,src={volume_name},dst=/workspace,"
                f"volume-subpath={volume_subpath}"
            )
        else:
            # Preserve the pre-volume activity shape for already-started runs.
            workspace_mount = (
                f"type=bind,src={request.resolved_workspace_ref},dst=/workspace"
            )
        args = [
            "create",
            "--name",
            name,
            "--label",
            f"{LABEL_CONTAINER_JOB}={request.job_id}",
            "--label",
            f"{LABEL_OWNERSHIP}={request.ownership_token}",
            "--label",
            f"{LABEL_CORRELATION}={self._correlation_label(request)}",
            "--label",
            f"{LABEL_EXPIRES_AT}={self._expiry_label(request)}",
            "--label",
            f"{LABEL_OBJECT_KIND}=container",
            "--label",
            f"{LABEL_BACKEND_REF}={self._backend_ref}",
            "--label",
            f"{LABEL_OWNERSHIP_SCHEMA}={OWNERSHIP_SCHEMA_VERSION}",
            "--network",
            network_mode,
            *structured_container_security_args(),
            "--cpus",
            str(spec.resources.cpu_millis / 1000),
            "--memory",
            f"{spec.resources.memory_mib}m",
            "--shm-size",
            f"{self._settings.shm_size_mib}m",
            "--pids-limit",
            str(spec.resources.pids),
            "--workdir",
            spec.workdir,
            "--mount",
            workspace_mount,
        ]
        if spec.resources.gpu is not None:
            # The caller owns the device request; the backend only realizes it as
            # the vendor's Docker device request after the deployment ceiling has
            # already admitted the count.
            args.extend(gpu_device_request_args(spec.resources.gpu))
        resolved_cache_refs: list[str] = []
        for requested_cache in spec.caches:
            try:
                cache = self._settings.cache_source(requested_cache.cache_ref)
            except Exception as exc:
                raise RuntimeError(
                    f"container cache source {requested_cache.cache_ref!r} is not "
                    "deployment-authorized"
                ) from exc
            if requested_cache.target != cache.target:
                raise RuntimeError(
                    f"container cache source {cache.cache_ref!r} requires target "
                    f"{cache.target!r}"
                )
            if requested_cache.read_only != cache.read_only:
                access = "read-only" if cache.read_only else "read-write"
                raise RuntimeError(
                    f"container cache source {cache.cache_ref!r} requires "
                    f"{access} access"
                )
            volume_name = await self._ensure_owner_scoped_cache_volume(
                request,
                base_volume_name=cache.volume_name,
                cache_ref=cache.cache_ref,
            )
            mount = (
                f"type=volume,src={volume_name},dst={cache.target}"
            )
            if cache.read_only:
                mount += ",readonly"
            args.extend(("--mount", mount))
            resolved_cache_refs.append(cache.cache_ref)
        args.extend(await self._materialized_env(request))
        if egress_attestation is not None:
            args.extend(
                (
                    "--label",
                    f"moonmind.egress.profile={egress_attestation.profile_ref}",
                    "--label",
                    "moonmind.egress.profile_digest="
                    f"{egress_attestation.profile_digest}",
                    "--label",
                    "moonmind.egress.applied_rule_digest="
                    f"{egress_attestation.applied_rule_digest}",
                )
            )
            for proxy_env in restricted_proxy_env():
                args.extend(("--env", proxy_env))
        if spec.entrypoint:
            args.extend(("--entrypoint", spec.entrypoint[0]))
        self._reject_forbidden_launch_args(args, expected_network=network_mode)
        args.append(request.resolved_image_ref)
        args.extend(spec.entrypoint[1:])
        args.extend(spec.command)
        code, _, create_stderr = await self._runner(args)
        if code:
            # A refused device request is reported with its stable generic class
            # before the ordinary launch failure, so a caller can tell an
            # unavailable GPU resource from an unusable workspace.
            self._reject_gpu_launch_refusal(
                spec.resources.gpu, stderr=create_stderr, exit_code=code
            )
            # Docker mount errors echo the trusted host source. Keep it out of
            # workflow history and caller-visible terminal diagnostics.
            raise RuntimeError("docker create failed for the resolved workspace")
        egress_evidence_ref = None
        if egress_attestation is not None and self._publish is not None:
            try:
                egress_evidence_ref = await self._publish_container_job_egress_launch(
                    request,
                    attestation=egress_attestation,
                    attachment_identity=name,
                    workload_evidence=None,
                    reconciliation_result="not_required",
                )
            except Exception as exc:
                # Evidence is part of readiness at this authority boundary. A
                # container that cannot publish it must never be started.
                await self._runner(("rm", "--force", name))
                raise RuntimeError(
                    "restricted-egress launch evidence could not be persisted"
                ) from exc
        return ContainerJobActivityResult(
            containerRef=name,
            diagnosticsRef=egress_evidence_ref,
            resolvedCacheRefs=tuple(resolved_cache_refs),
            gpuObservation=resolved_gpu,
        )

    async def start_container(self, request: ContainerJobActivityRequest):
        container_name = request.container_ref or self._name(request)
        requested_gpu = request.request.spec.resources.gpu
        started_at = datetime.now(timezone.utc)
        capacity_lease = await self._acquire_capacity_lock()
        try:
            await self._enforce_active_memory_budget(
                request, container_name=container_name
            )
            code, _, start_stderr = await self._runner(("start", container_name))
            if code:
                # The daemon resolves a device request when the container
                # starts, so this is where an unavailable GPU runtime or device
                # is refused. Classify it before the ordinary launch failure.
                self._reject_gpu_launch_refusal(
                    requested_gpu, stderr=start_stderr, exit_code=code
                )
                detail = start_stderr.decode(errors="replace").strip()[:1000]
                raise RuntimeError(f"docker start failed: {detail}")
        finally:
            try:
                await self._capacity_lock.release(capacity_lease)
            except Exception:  # noqa: BLE001 - OS releases locks on worker exit
                logger.warning(
                    "Container-job capacity lock release failed; process exit "
                    "will release the OS-held lock",
                    exc_info=True,
                )
        diagnostics_ref = request.egress_attestation_ref
        if request.request.spec.network_mode == "bridge":
            try:
                attestation = await attest_docker_egress(
                    runner=self._runner,
                    profile=DEFAULT_EGRESS_PROFILE,
                    backend_ref=self._backend_ref,
                )
                workload_evidence = await attest_docker_workload_egress(
                    runner=self._runner,
                    profile=DEFAULT_EGRESS_PROFILE,
                    attestation=attestation,
                    attachment_identity=container_name,
                    expected_image_ref=str(request.resolved_image_ref or ""),
                    started_at=started_at,
                )
                diagnostics_ref = await self._publish_container_job_egress_launch(
                    request,
                    attestation=attestation,
                    attachment_identity=container_name,
                    workload_evidence=workload_evidence,
                    reconciliation_result="not_required",
                )
                if not diagnostics_ref:
                    raise RuntimeError(
                        "restricted-egress evidence publisher is unavailable"
                    )
            except Exception as exc:
                # A running restricted workload without its immutable evidence
                # chain is not ready. Remove only this owned container and fail
                # before caller execution can proceed.
                await self._runner(("rm", "--force", container_name))
                raise RuntimeError(
                    "restricted-egress running launch evidence could not be persisted"
                ) from exc
        return ContainerJobActivityResult(
            containerRef=container_name,
            running=True,
            diagnosticsRef=diagnostics_ref,
            gpuObservation=gpu_observation(
                requested_gpu, backend_supported=True, launched=True
            ),
        )

    async def observe_container(self, request: ContainerJobActivityRequest):
        ref = request.container_ref or self._name(request)
        raw = await self._checked("inspect", "--format", "{{json .State}}", ref)
        state = json.loads(raw)
        # Publish any incremental log delta produced since the last poll to the
        # shared Live Logs spool. This is bounded and best-effort; a failure
        # here never fails the observation of container liveness/terminality.
        log_cursor = await self._collect_live_logs(request, ref)
        if state.get("Running"):
            return ContainerJobActivityResult(
                containerRef=ref, running=True, logCursor=log_cursor
            )
        exit_code = int(state.get("ExitCode", 1))
        started_at, finished_at, duration_ms = _parse_container_timing(state)
        return ContainerJobActivityResult(
            containerRef=ref,
            running=False,
            terminalState="succeeded" if exit_code == 0 else "failed",
            exitCode=exit_code,
            logCursor=log_cursor,
            startedAt=started_at,
            finishedAt=finished_at,
            durationMs=duration_ms,
        )

    # ------------------------------------------------------- live log producer

    def _live_spool_dir(self, request: ContainerJobActivityRequest) -> Path | None:
        if self._log_spool_root is None:
            return None
        suffix = hashlib.sha256(request.ownership_token.encode()).hexdigest()[:20]
        return self._log_spool_root / f"job-{suffix}"

    async def _collect_live_logs(
        self, request: ContainerJobActivityRequest, ref: str
    ) -> str | None:
        """Publish the bounded incremental log delta to the shared Live spool.

        Returns the resumable cursor (``"<rfc3339>|<sequence>"``) so the next
        poll fetches only newer lines. Live logging is opt-in (a spool root must
        be configured), bounded by a total-retention ceiling, redacted before
        persistence, and strictly non-authoritative: any error is swallowed and
        the previous cursor is preserved.
        """

        spool_dir = self._live_spool_dir(request)
        if spool_dir is None:
            return request.log_cursor
        try:
            since_dt, base_seq, timestamp_offset = _parse_log_cursor(
                request.log_cursor
            )
            if base_seq >= self._live_log_max_events:
                return request.log_cursor
            entries = await self._read_incremental_log_entries(
                ref, since_dt, base_seq, timestamp_offset
            )
            if not entries:
                return request.log_cursor
            spool_dir.mkdir(parents=True, exist_ok=True)
            publisher = SpoolLogPublisher(workspace_path=str(spool_dir))
            last = entries[-1]
            for entry in entries:
                publisher.publish(
                    RunObservabilityEvent(
                        runId=request.job_id,
                        sequence=entry.sequence,
                        stream=entry.stream if entry.stream != "system" else "system",
                        timestamp=entry.timestamp.isoformat(),
                        text=entry.text,
                        kind="stdout_chunk"
                        if entry.stream == "stdout"
                        else "stderr_chunk",
                    )
                )
            prior_at_last = timestamp_offset if last.timestamp == since_dt else 0
            emitted_at_last = sum(
                entry.timestamp == last.timestamp for entry in entries
            )
            return (
                f"{last.timestamp.isoformat()}|{last.sequence}|"
                f"{prior_at_last + emitted_at_last}"
            )
        except Exception:
            return request.log_cursor

    async def _read_incremental_log_entries(
        self,
        ref: str,
        since_dt: datetime | None,
        base_seq: int,
        timestamp_offset: int = 0,
    ) -> list[ContainerJobLogEntry]:
        """Read and merge the new stdout/stderr delta as bounded log entries."""

        # Bound the daemon response itself; slicing after the subprocess exits
        # still permits a noisy container to exhaust worker memory.
        args: list[str] = ["logs", "--timestamps", "--tail", str(MAX_LOG_PAGE_ENTRIES)]
        if since_dt is not None:
            args.extend(("--since", since_dt.isoformat()))
        args.append(ref)
        code, stdout, stderr = await self._runner(tuple(args))
        # ``docker logs`` writes container stdout/stderr to the corresponding
        # streams for a non-TTY container, so they can be attributed exactly.
        merged: list[tuple[datetime, str, str]] = []
        seen_at_cursor = 0
        for stream, blob in (("stdout", stdout), ("stderr", stderr)):
            for line in blob.decode("utf-8", errors="replace").splitlines():
                match = _DOCKER_TS_LINE.match(line)
                if not match:
                    continue
                ts = _parse_rfc3339(match.group("ts"))
                if ts is None:
                    continue
                if since_dt is not None and ts < since_dt:
                    continue
                if since_dt is not None and ts == since_dt:
                    seen_at_cursor += 1
                    if seen_at_cursor <= timestamp_offset:
                        continue
                text = redact_sensitive_text(match.group("text") or "")[
                    :_LIVE_LOG_ENTRY_MAX_CHARS
                ]
                merged.append((ts, stream, text))
        merged.sort(key=lambda item: item[0])
        remaining = min(
            MAX_LOG_PAGE_ENTRIES, self._live_log_max_events - base_seq
        )
        entries: list[ContainerJobLogEntry] = []
        for index, (ts, stream, text) in enumerate(merged[:remaining]):
            entries.append(
                ContainerJobLogEntry(
                    sequence=base_seq + index + 1,
                    timestamp=ts,
                    stream=stream,
                    text=text,
                )
            )
        return entries

    async def stop_container(self, request: ContainerJobActivityRequest):
        ref = request.container_ref or self._name(request)
        ownership = await self._owned_ownership_label(ref)
        if ownership is None:
            return ContainerJobActivityResult(containerRef=ref, running=False)
        if ownership != request.ownership_token:
            raise RuntimeError("container job ownership mismatch; refusing stop")
        await self._checked(
            "stop", "--time", "10", ref
        )
        return ContainerJobActivityResult(
            containerRef=ref, running=False
        )

    async def remove_container(self, request: ContainerJobActivityRequest):
        # Re-read immutable ownership immediately before deletion. A prior
        # observe/reconcile result is not deletion authority: the expected name
        # may have been removed and replaced between Activities. Missing is an
        # idempotent success; a replacement with different ownership fails
        # closed.
        ref = request.container_ref or self._name(request)
        ownership = await self._owned_ownership_label(ref)
        if ownership is None:
            return ContainerJobActivityResult()
        if ownership != request.ownership_token:
            raise RuntimeError("container job ownership mismatch; refusing removal")
        await self._checked("rm", "--force", ref)
        return ContainerJobActivityResult()

    @staticmethod
    def _bound_tail(data: bytes, limit: int) -> bytes:
        """Bound captured output to ``limit`` bytes, keeping the failure tail."""

        if len(data) > limit:
            return b"[truncated]\n" + data[-limit:]
        return data

    @staticmethod
    def _pre_container_evidence_message(
        request: ContainerJobActivityRequest,
    ) -> str:
        """Describe a terminal outcome reached before a container existed.

        The terminal state and failure class own the outcome, so the fallback
        never contradicts them by publishing "failed" for an operator
        cancellation or a timeout.
        """

        if request.message:
            return request.message
        if (
            request.terminal_state == ContainerJobState.CANCELED
            or request.failure_class == ContainerJobFailureClass.CANCELED
        ):
            return "container job was canceled before a container was created"
        if (
            request.terminal_state == ContainerJobState.TIMED_OUT
            or request.failure_class == ContainerJobFailureClass.TIMEOUT
        ):
            return "container job timed out before a container was created"
        if request.terminal_state == ContainerJobState.SUCCEEDED:
            return "container job completed before a container was created"
        return "container job failed before a container was created"

    async def publish_evidence(self, request: ContainerJobActivityRequest):
        limit = self._settings.max_output_bytes
        if request.container_ref is None:
            # ``container_ref`` is the only authority that a reconcile or create
            # Activity actually produced a container. A deterministic container
            # name is not that evidence, so querying it here would replace the
            # authoritative terminal cause with Docker's derived "No such
            # container" response.
            message = redact_sensitive_text(
                self._pre_container_evidence_message(request)
            )
            stdout_raw = b""
            stderr_raw = b""
            combined_raw = f"[system]\n{message}\n".encode()
        else:
            ref = request.container_ref or self._name(request)
            code, stdout, stderr = await self._runner(("logs", ref))
            # Redact container-emitted secrets before anything is persisted. For
            # a non-TTY container ``docker logs`` writes container stdout/stderr
            # to the matching streams, so each is captured deterministically.
            stdout_raw = redact_sensitive_text(
                stdout.decode("utf-8", errors="replace")
            ).encode("utf-8")
            stderr_raw = redact_sensitive_text(
                stderr.decode("utf-8", errors="replace")
            ).encode("utf-8")
            combined_raw = stdout_raw + (
                b"\n[stderr]\n" + stderr_raw if stderr_raw else b""
            )
            if code and not (stdout_raw or stderr_raw):
                raise RuntimeError("container evidence is unavailable")
        combined = self._bound_tail(combined_raw, limit)
        stdout_bytes = self._bound_tail(stdout_raw, limit)
        stderr_bytes = self._bound_tail(stderr_raw, limit)

        if self._publish is None:
            return ContainerJobActivityResult()

        job = request.job_id
        # A combined logs artifact preserves the terminal reconstruction
        # fallback; separate stdout/stderr artifacts satisfy the deterministic
        # per-stream requirement.
        logs_ref = await self._publish(request, f"{job}-logs.txt", combined)
        stdout_ref = await self._publish(request, f"{job}-stdout.txt", stdout_bytes)
        stderr_ref = await self._publish(request, f"{job}-stderr.txt", stderr_bytes)

        manifest = await self._collect_declared_outputs(request)
        artifacts_ref = await self._publish_output_manifest(request, manifest)
        diagnostics_ref = await self._publish_runtime_diagnostics(
            request,
            logs_ref=logs_ref,
            stdout_ref=stdout_ref,
            stderr_ref=stderr_ref,
            manifest=manifest,
        )
        events_ref = await self._persist_live_events_journal(request)
        return ContainerJobActivityResult(
            logsRef=logs_ref,
            artifactsRef=artifacts_ref,
            diagnosticsRef=diagnostics_ref,
            eventsRef=events_ref,
        )

    # ----------------------------------------------- declared-output collection

    def _build_output_entries(
        self, workspace: Path, outputs: Sequence
    ) -> list[dict]:
        """Validate and read declared outputs under the approved workspace root.

        Rejects traversal/symlink escape, unsupported file types, and outputs
        that would breach the deployment file-count or total-size ceilings.
        Missing declared outputs are preserved as partial evidence rather than
        aborting the whole collection (so cancellation/timeout still publishes
        what exists).
        """

        workspace_real = Path(os.path.realpath(workspace))
        files_budget = self._settings.max_output_files
        bytes_budget = self._settings.max_output_total_bytes
        results: list[dict] = []
        for decl in outputs:
            entry: dict = {
                "name": decl.name,
                "relative_path": decl.relative_path,
                "status": ArtifactCollectionStatus.COLLECTED,
                "detail": None,
                "media_type": None,
                "payload": None,
            }
            candidate = workspace / decl.relative_path
            if not os.path.lexists(candidate):
                entry["status"] = ArtifactCollectionStatus.MISSING
                entry["detail"] = "declared output was not produced"
                results.append(entry)
                continue
            real = Path(os.path.realpath(candidate))
            if real != workspace_real and workspace_real not in real.parents:
                entry["status"] = ArtifactCollectionStatus.REJECTED
                entry["detail"] = "declared output escapes the approved workspace root"
                results.append(entry)
                continue
            if candidate.is_symlink():
                entry["status"] = ArtifactCollectionStatus.REJECTED
                entry["detail"] = "declared output is a symlink"
                results.append(entry)
                continue
            try:
                info = candidate.stat()
            except OSError:
                entry["status"] = ArtifactCollectionStatus.MISSING
                entry["detail"] = "declared output could not be read"
                results.append(entry)
                continue
            if stat.S_ISREG(info.st_mode):
                if files_budget < 1 or info.st_size > bytes_budget:
                    entry["status"] = ArtifactCollectionStatus.REJECTED
                    entry["detail"] = "declared output exceeds the collection ceiling"
                    results.append(entry)
                    continue
                entry["payload"] = candidate.read_bytes()
                entry["media_type"] = (
                    mimetypes.guess_type(candidate.name)[0]
                    or _ALLOWED_OUTPUT_MEDIA_FALLBACK
                )
                files_budget -= 1
                bytes_budget -= len(entry["payload"])
                results.append(entry)
                continue
            if stat.S_ISDIR(info.st_mode):
                try:
                    archive, used_files, used_bytes = self._archive_directory(
                        candidate, workspace_real, files_budget, bytes_budget
                    )
                except _OutputRejected as exc:
                    entry["status"] = ArtifactCollectionStatus.REJECTED
                    entry["detail"] = str(exc)
                    results.append(entry)
                    continue
                entry["payload"] = archive
                entry["media_type"] = "application/gzip"
                files_budget -= used_files
                bytes_budget -= used_bytes
                results.append(entry)
                continue
            # Device, fifo, socket, or other special file.
            entry["status"] = ArtifactCollectionStatus.REJECTED
            entry["detail"] = "declared output is an unsupported file type"
            results.append(entry)
        return results

    @staticmethod
    def _archive_directory(
        root: Path, workspace_real: Path, files_budget: int, bytes_budget: int
    ) -> tuple[bytes, int, int]:
        """Archive only regular in-workspace files, never following symlinks."""

        buffer = BytesIO()
        used_files = 0
        used_bytes = 0
        with tarfile.open(fileobj=buffer, mode="w:gz") as bundle:
            for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
                # Never descend into symlinked directories (escape prevention).
                dirnames[:] = sorted(
                    name
                    for name in dirnames
                    if not os.path.islink(os.path.join(dirpath, name))
                )
                for name in sorted(filenames):
                    full = Path(dirpath) / name
                    if full.is_symlink():
                        continue
                    try:
                        member = full.stat()
                    except OSError:
                        continue
                    if not stat.S_ISREG(member.st_mode):
                        continue
                    real = Path(os.path.realpath(full))
                    if real != workspace_real and workspace_real not in real.parents:
                        raise _OutputRejected(
                            "directory output contains an escaping path"
                        )
                    used_files += 1
                    used_bytes += member.st_size
                    if used_files > files_budget or used_bytes > bytes_budget:
                        raise _OutputRejected(
                            "declared output exceeds the collection ceiling"
                        )
                    bundle.add(
                        str(full),
                        arcname=os.path.relpath(full, root),
                        recursive=False,
                    )
        return buffer.getvalue(), used_files, used_bytes

    async def _collect_declared_outputs(
        self, request: ContainerJobActivityRequest
    ) -> list[ContainerJobArtifact]:
        outputs = request.request.spec.outputs
        if not outputs or self._publish is None:
            return []
        workspace = Path(request.resolved_workspace_ref or "").resolve()
        raw = await asyncio.to_thread(
            self._build_output_entries, workspace, list(outputs)
        )
        manifest: list[ContainerJobArtifact] = []
        for entry in raw:
            payload = entry["payload"]
            if payload is None:
                manifest.append(
                    ContainerJobArtifact(
                        name=entry["name"],
                        relativePath=entry["relative_path"],
                        collectionStatus=entry["status"],
                        detail=entry["detail"],
                    )
                )
                continue
            # Preserve the source suffix so the artifact publisher can assign
            # the media type already detected during collection.
            suffixes = "".join(Path(entry["relative_path"]).suffixes)
            name = f"{request.job_id}-output-{entry['name']}{suffixes}"
            if entry["media_type"] == "application/gzip":
                name = name.removesuffix(suffixes) + ".tar.gz"
            ref = await self._publish(request, name, payload)
            manifest.append(
                ContainerJobArtifact(
                    name=entry["name"],
                    artifactRef=ref,
                    sizeBytes=len(payload),
                    sha256=hashlib.sha256(payload).hexdigest(),
                    mediaType=entry["media_type"],
                    relativePath=entry["relative_path"],
                    collectionStatus=entry["status"],
                )
            )
        return manifest

    async def _publish_output_manifest(
        self,
        request: ContainerJobActivityRequest,
        manifest: list[ContainerJobArtifact],
    ) -> str | None:
        if self._publish is None or not manifest:
            return None
        collected = any(
            item.collection_status == ArtifactCollectionStatus.COLLECTED
            for item in manifest
        )
        page = ContainerJobArtifactPage(
            jobId=request.job_id,
            artifacts=manifest,
            publication=AuxiliaryOutcome(
                state="succeeded" if collected else "failed"
            ),
        )
        data = redact_sensitive_text(
            page.model_dump_json(by_alias=True, exclude_none=True)
        ).encode("utf-8")
        return await self._publish(request, f"{request.job_id}-artifacts.json", data)

    async def _publish_runtime_diagnostics(
        self,
        request: ContainerJobActivityRequest,
        *,
        logs_ref: str | None,
        stdout_ref: str | None,
        stderr_ref: str | None,
        manifest: list[ContainerJobArtifact],
    ) -> str | None:
        if self._publish is None:
            return None
        # Egress evidence is auxiliary diagnostic collection performed after the
        # logs and output artifacts have already been uploaded. If the gateway,
        # its access log, or the workload attachment cannot be inspected, isolate
        # that failure into the diagnostics body rather than letting it propagate
        # and discard the already-published primary terminal evidence.
        egress_evidence: dict | None
        egress_error: str | None = None
        try:
            egress_evidence = await self._runtime_egress_evidence(request)
        except Exception as exc:
            egress_evidence = None
            egress_error = str(exc)[:512] or type(exc).__name__
        diagnostics = {
            "jobId": request.job_id,
            "contractVersion": "v1",
            "terminalState": getattr(
                request.terminal_state, "value", request.terminal_state
            ),
            "exitCode": request.exit_code,
            "failureClass": getattr(
                request.failure_class, "value", request.failure_class
            ),
            "message": request.message,
            "startedAt": request.started_at.isoformat()
            if request.started_at
            else None,
            "finishedAt": request.finished_at.isoformat()
            if request.finished_at
            else None,
            "durationMs": request.duration_ms,
            "backendRef": self._backend_ref,
            "imageSourceRef": request.request.spec.image_source_ref,
            "image": (
                request.image_observation.model_dump(
                    mode="json", by_alias=True, exclude_none=True
                )
                if request.image_observation is not None
                else None
            ),
            "resolvedCacheRefs": list(request.resolved_cache_refs),
            "logsRef": logs_ref,
            "stdoutRef": stdout_ref,
            "stderrRef": stderr_ref,
            "outputs": [
                item.model_dump(mode="json", by_alias=True, exclude_none=True)
                for item in manifest
            ],
        }
        if egress_evidence is not None:
            egress_evidence["launchAttestationRef"] = (
                request.egress_attestation_ref
            )
            diagnostics["egressEvidence"] = egress_evidence
        elif egress_error is not None:
            # Record the auxiliary failure without discarding the primary refs.
            diagnostics["egressEvidence"] = {
                "state": "unavailable",
                "error": egress_error,
                "launchAttestationRef": request.egress_attestation_ref,
            }
        data = redact_sensitive_text(
            json.dumps(diagnostics, sort_keys=True, separators=(",", ":"))
        ).encode("utf-8")
        return await self._publish(
            request, f"{request.job_id}-diagnostics.json", data
        )

    async def _runtime_egress_evidence(
        self, request: ContainerJobActivityRequest
    ) -> dict | None:
        """Observe bounded denial and attachment evidence before cleanup."""

        # Only an actual container reference proves a container exists to
        # inspect; a deterministic name would make ``docker inspect`` report a
        # missing container as drifted network evidence.
        if (
            request.request.spec.network_mode != "bridge"
            or request.container_ref is None
        ):
            return None
        ref = request.container_ref
        # Inspect the complete network map, not just the approved network. If
        # daemon state drifted or another trusted client attached a second
        # network, indexing only the approved network would still return a valid
        # IP and let terminal evidence claim restricted egress while the workload
        # holds a direct bypass route. Require the approved network to be the sole
        # attachment before emitting passing evidence.
        code, stdout, _ = await self._runner(
            (
                "inspect",
                "--format",
                "{{json .NetworkSettings.Networks}}",
                ref,
            )
        )
        if code or not stdout.strip():
            raise RuntimeError("restricted-egress attachment evidence is unavailable")
        try:
            networks = json.loads(stdout.decode(errors="replace"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise RuntimeError(
                "restricted-egress attachment evidence is malformed"
            ) from exc
        if not isinstance(networks, dict) or set(networks) != {
            DEFAULT_EGRESS_PROFILE.network_ref
        }:
            raise RuntimeError(
                "restricted-egress attachment is not the sole approved network"
            )
        client_address = str(
            (networks[DEFAULT_EGRESS_PROFILE.network_ref] or {}).get("IPAddress") or ""
        ).strip()
        if not client_address:
            raise RuntimeError("restricted-egress attachment evidence is unavailable")
        code, access_log, _ = await self._runner(
            (
                "exec",
                DEFAULT_EGRESS_PROFILE.gateway_ref,
                "cat",
                "/var/log/squid/access.log",
            )
        )
        if code:
            raise RuntimeError("restricted-egress denial evidence is unavailable")
        # Scope denials to this launch by client address and, when known, the
        # container start/finish interval so a reused bridge IP cannot attribute
        # a prior holder's denials from the gateway log to this job. The complete
        # log is scanned so older in-lifetime denials cannot fall outside a tail;
        # only the bounded normalized sample below enters durable evidence.
        denial_diagnostics = bounded_denial_diagnostics(
            access_log,
            client_address=client_address,
            started_at=request.started_at,
            finished_at=request.finished_at,
        )
        # The retained diagnostic sample is capped; count denials independently so
        # terminal evidence does not underreport traffic above the cap.
        denied_count = denied_connection_count(
            access_log,
            client_address=client_address,
            started_at=request.started_at,
            finished_at=request.finished_at,
        )
        return {
            "profileRef": DEFAULT_EGRESS_PROFILE.ref,
            "profileDigest": DEFAULT_EGRESS_PROFILE.digest,
            "networkRef": DEFAULT_EGRESS_PROFILE.network_ref,
            "gatewayRef": DEFAULT_EGRESS_PROFILE.gateway_ref,
            "attachmentIdentity": ref,
            "attachmentAddressDigest": "sha256:"
            + hashlib.sha256(client_address.encode()).hexdigest(),
            "deniedConnectionCount": denied_count,
            "denialDiagnostics": list(denial_diagnostics),
        }

    async def _persist_live_events_journal(
        self, request: ContainerJobActivityRequest
    ) -> str | None:
        """Persist the live spool as the durable terminal log fallback (#3258).

        Active jobs are followed through the live spool; terminal jobs are
        reconstructed from this durable journal without the container or the
        live stream.
        """

        if self._publish is None:
            return None
        spool_dir = self._live_spool_dir(request)
        if spool_dir is None:
            return None
        spool_path = spool_dir / "live_streams.spool"
        try:
            if not spool_path.is_file() or spool_path.stat().st_size <= 0:
                return None
            data = spool_path.read_bytes()
        except OSError:
            return None
        ref = await self._publish(
            request, f"{request.job_id}-{_LIVE_EVENTS_JOURNAL_NAME}", data
        )
        # The durable journal is authoritative once publication succeeds.
        shutil.rmtree(spool_dir, ignore_errors=True)
        return ref

    async def project_status(self, request: ContainerJobActivityRequest):
        if self._write_projection is None:
            raise RuntimeError("durable container-job projection writer is unavailable")
        await self._write_projection(request)
        return ContainerJobActivityResult(terminalState=request.terminal_state)

    async def repair_projection(self, request: ContainerJobActivityRequest):
        return await self.project_status(request)

    async def cleanup(self, request: ContainerJobActivityRequest):
        # Deterministic, job-owned removal of any ephemeral registry auth. This
        # runs on success, failure, cancellation, timeout, and orphan
        # reconciliation, so credential material never outlives the job. A
        # cleanup failure is reported through the workflow's separate cleanup
        # auxiliary outcome and never rewrites the primary workload result.
        auth_dir = self._auth_dir(request)
        cleanup_succeeded = True
        failure_code: str | None = None
        # Retry the credential removal in-Activity for transient OSErrors before
        # surrendering. Reporting cleanupSucceeded=false returns the Activity
        # normally (to still publish durable lifecycle evidence), which suppresses
        # the workflow's configured cleanup retries; a bounded inner retry keeps a
        # transient filesystem error from leaving registry credentials on disk.
        for attempt in range(_CREDENTIAL_CLEANUP_ATTEMPTS):
            try:
                self._remove_auth_dir(auth_dir, best_effort=False)
                cleanup_succeeded = True
                failure_code = None
                break
            except OSError:
                cleanup_succeeded = False
                failure_code = "credential_cleanup_failed"
                if attempt + 1 < _CREDENTIAL_CLEANUP_ATTEMPTS:
                    await asyncio.sleep(_CREDENTIAL_CLEANUP_BACKOFF_SECONDS)

        diagnostics_ref = None
        if request.request.spec.network_mode == "bridge":
            # remove_container runs immediately before cleanup. Observe that no
            # job-owned attachment remains rather than turning an attempted
            # delete into terminal evidence. Filters are ownership scoped, so
            # this check neither discovers nor mutates another job's resource.
            code, stdout, _ = await self._runner(
                (
                    "ps",
                    "-aq",
                    "--filter",
                    f"label={LABEL_CONTAINER_JOB}={request.job_id}",
                    "--filter",
                    f"label={LABEL_OWNERSHIP}={request.ownership_token}",
                )
            )
            if code or stdout.strip():
                cleanup_succeeded = False
                failure_code = failure_code or "attachment_still_present"
            if self._publish is None:
                raise RuntimeError(
                    "restricted-egress lifecycle evidence publisher is unavailable"
                )
            lifecycle = {
                "schemaVersion": 1,
                "profileRef": DEFAULT_EGRESS_PROFILE.ref,
                "profileDigest": DEFAULT_EGRESS_PROFILE.digest,
                "workloadAttachmentIdentity": request.container_ref
                or self._name(request),
                "runtimeDiagnosticsRef": (
                    request.publication.diagnostics_ref
                    if request.publication is not None
                    else None
                ),
                "launchAttestationRef": request.egress_attestation_ref,
                "reconciliationResult": (
                    "succeeded" if not (code or stdout.strip()) else "failed"
                ),
                "cleanupResult": "succeeded" if cleanup_succeeded else "failed",
            }
            if failure_code is not None:
                lifecycle["failureCode"] = failure_code
            diagnostics_ref = await self._publish(
                request,
                f"{request.job_id}-egress-lifecycle.json",
                serialize_conformance_evidence(
                    lifecycle, location="egress-lifecycle"
                ),
            )
        return ContainerJobActivityResult(
            diagnosticsRef=diagnostics_ref,
            cleanupSucceeded=cleanup_succeeded,
        )
