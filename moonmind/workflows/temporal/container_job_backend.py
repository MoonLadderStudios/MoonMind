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
    ImageObservation,
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
        "--device",
        "--device-cgroup-rule",
        "--pid",
        "--ipc",
        "--uts",
        "--userns",
        "--cgroupns",
        "--cap-add",
    }
)
_TRUTHY_PRIVILEGED = frozenset({"--privileged", "--privileged=true", "--privileged=1"})
_FORBIDDEN_MOUNT_SOURCES = (
    "/var/run/docker.sock",
    "/run/docker.sock",
    "/var/lib/docker",
)
_MIN_VOLUME_SUBPATH_DOCKER_MAJOR = 26


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
                raise RuntimeError(
                    f"{name}={requested} exceeds the deployment ceiling {ceiling} "
                    "and cannot be raised by a caller"
                )

    @staticmethod
    def _reject_forbidden_launch_args(args: Sequence[str]) -> None:
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
            raise ImageAcquisitionError(
                "requested image is absent and pullPolicy=never",
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
        return ContainerJobActivityResult(
            containerRef=name, running=stdout.strip() == b"true"
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
        name = self._name(request)
        await self._reject_ownership_collision(request, name)
        if spec.network_mode not in {"none", "bridge"}:
            raise RuntimeError("network mode must be 'none' or policy-approved 'bridge'")
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
            spec.network_mode,
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
        if spec.caches:
            raise RuntimeError(
                "cacheRef is unsupported until container-job authority can "
                "resolve it to an approved volume"
            )
        args.extend(await self._materialized_env(request))
        if spec.entrypoint:
            args.extend(("--entrypoint", spec.entrypoint[0]))
        self._reject_forbidden_launch_args(args)
        args.append(request.resolved_image_ref)
        args.extend(spec.entrypoint[1:])
        args.extend(spec.command)
        code, _, _ = await self._runner(args)
        if code:
            # Docker mount errors echo the trusted host source. Keep it out of
            # workflow history and caller-visible terminal diagnostics.
            raise RuntimeError("docker create failed for the resolved workspace")
        return ContainerJobActivityResult(containerRef=name)

    async def start_container(self, request: ContainerJobActivityRequest):
        await self._checked("start", request.container_ref or self._name(request))
        return ContainerJobActivityResult(
            containerRef=request.container_ref or self._name(request), running=True
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

    async def publish_evidence(self, request: ContainerJobActivityRequest):
        ref = request.container_ref or self._name(request)
        code, stdout, stderr = await self._runner(("logs", ref))
        # Redact container-emitted secrets before anything is persisted. For a
        # non-TTY container ``docker logs`` writes container stdout/stderr to the
        # matching streams, so each is captured as a deterministic artifact.
        limit = self._settings.max_output_bytes
        stdout_raw = redact_sensitive_text(
            stdout.decode("utf-8", errors="replace")
        ).encode("utf-8")
        stderr_raw = redact_sensitive_text(
            stderr.decode("utf-8", errors="replace")
        ).encode("utf-8")
        combined_raw = stdout_raw + (
            b"\n[stderr]\n" + stderr_raw if stderr_raw else b""
        )
        combined = self._bound_tail(combined_raw, limit)
        stdout_bytes = self._bound_tail(stdout_raw, limit)
        stderr_bytes = self._bound_tail(stderr_raw, limit)
        if code and not (stdout_raw or stderr_raw):
            raise RuntimeError("container evidence is unavailable")

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
            "logsRef": logs_ref,
            "stdoutRef": stdout_ref,
            "stderrRef": stderr_ref,
            "outputs": [
                item.model_dump(mode="json", by_alias=True, exclude_none=True)
                for item in manifest
            ],
        }
        data = redact_sensitive_text(
            json.dumps(diagnostics, sort_keys=True, separators=(",", ":"))
        ).encode("utf-8")
        return await self._publish(
            request, f"{request.job_id}-diagnostics.json", data
        )

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
        try:
            self._remove_auth_dir(auth_dir, best_effort=False)
        except OSError as exc:
            raise ContainerJobBackendError(
                ContainerJobFailureClass.CREDENTIAL_CLEANUP_FAILED,
                "ephemeral registry credential directory could not be removed",
            ) from exc
        return ContainerJobActivityResult()
