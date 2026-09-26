"""Execution lifecycle for the deployment update tool."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import re
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Protocol, Sequence

from moonmind.deployment_access import DeploymentAccessError, check_compose_access

from .deployment_tools import (
    DEPLOYMENT_UPDATE_TOOL_NAME,
    RELEASE_RUNNER_COMMAND_TIMEOUT_SECONDS,
)
from .tool_plan_contracts import ToolFailure, ToolResult

DEPLOYMENT_RUNNER_MODES = frozenset(
    {"privileged_worker", "ephemeral_updater_container"}
)
DEPLOYMENT_UPDATE_MODES = frozenset({"changed_services", "force_recreate"})
DEPLOYMENT_UPDATE_STACKS = frozenset({"moonmind"})
DEPLOYMENT_FINAL_STATUSES = frozenset({"SUCCEEDED", "FAILED", "PARTIALLY_VERIFIED"})
DEPLOYMENT_ONE_SHOT_SERVICES = frozenset({"init-db"})
DEPLOYMENT_CONTROL_SERVICE = "temporal-worker-deployment-control"
_REDACTED = "[REDACTED]"
_STACK_PATH_COMPONENT_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")
_DOCKER_DESKTOP_HOST_MOUNT_ROOT = PurePosixPath("/run/desktop/mnt/host")
_SENSITIVE_KEY_PATTERN = re.compile(
    r"("
    r"token|secret|password|passwd|credential|authorization|"
    r"auth[_-]?header|api[_-]?key|registry[_-]?password"
    r")",
    re.IGNORECASE,
)
_SENSITIVE_VALUE_PATTERN = re.compile(
    r"(bearer\s+[A-Za-z0-9._~+/=-]+|"
    r"(?:token|password|passwd|secret)=[^ \t\n\r,;&\"']+)",
    re.IGNORECASE,
)
_COMPOSE_OPTION_FLAGS_WITH_VALUES = frozenset(
    {
        "--env-file",
        "--exit-code-from",
        "--file",
        "--format",
        "--policy",
        "--project-directory",
        "--project-name",
        "--pull",
        "--wait-timeout",
        "-f",
        "-p",
    }
)


class DesiredStateStore(Protocol):
    async def persist(self, payload: Mapping[str, Any]) -> str:
        """Persist desired deployment state and return a store reference."""


class EvidenceWriter(Protocol):
    async def write(self, kind: str, payload: Mapping[str, Any]) -> str:
        """Write structured deployment evidence and return an artifact ref."""


@dataclass(frozen=True, slots=True)
class ComposeCommandPlan:
    runner_mode: str
    pull_args: tuple[str, ...]
    up_args: tuple[str, ...]
    # Acquires reconciled infrastructure images that are absent locally
    # without refreshing present ones, so `up --pull never` never fails on an
    # infrastructure image the release newly pins.
    missing_pull_args: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ComposeVerification:
    succeeded: bool
    updated_services: tuple[str, ...]
    running_services: tuple[Mapping[str, Any], ...]
    details: Mapping[str, Any]
    status: str | None = None


class ComposeRunner(Protocol):
    async def capture_state(self, *, stack: str, phase: str) -> Mapping[str, Any]:
        """Capture before/after state for a deployment stack."""

    async def pull(
        self,
        *,
        stack: str,
        command: tuple[str, ...],
        requested_image: str,
    ) -> Mapping[str, Any]:
        """Run the pull command."""

    async def up(
        self,
        *,
        stack: str,
        command: tuple[str, ...],
        requested_image: str,
    ) -> Mapping[str, Any]:
        """Run the up command."""

    async def inspect_image(self, requested_image: str) -> Mapping[str, Any]:
        """Inspect the pulled target image."""

    async def verify(
        self,
        *,
        stack: str,
        requested_image: str,
        resolved_digest: str | None,
    ) -> ComposeVerification:
        """Verify the requested desired state is running."""


# Bounded holds taken on the stack lock by owners that are not updates. The
# availability supervisor sweeps every deployment worker on a short cycle and
# the maintenance pass reconciles retained release jobs; both share the lock
# that serializes updates. An update must outlast the longer of them, because
# reporting "already running" for a routine observation sweep fails a release
# while nothing is being deployed.
DEPLOYMENT_AVAILABILITY_SWEEP_TIMEOUT_SECONDS = 300
DEPLOYMENT_MAINTENANCE_PASS_TIMEOUT_SECONDS = 840
DEPLOYMENT_UPDATE_LOCK_WAIT_SECONDS = (
    max(
        DEPLOYMENT_AVAILABILITY_SWEEP_TIMEOUT_SECONDS,
        DEPLOYMENT_MAINTENANCE_PASS_TIMEOUT_SECONDS,
    )
    + 60
)
_DEPLOYMENT_LOCK_POLL_SECONDS = 0.5


def _lock_unavailable(stack: str, *, retryable: bool) -> ToolFailure:
    return ToolFailure(
        error_code="DEPLOYMENT_LOCKED",
        message=f"Deployment update for stack '{stack}' is already running.",
        retryable=retryable,
        details={"stack": stack, "failureClass": "deployment_lock_unavailable"},
    )


class DeploymentUpdateLockManager:
    """Per-stack lock manager for deployment updates.

    ``wait_seconds`` is the caller's budget for waiting out the current owner.
    It defaults to zero so background sweeps keep yielding to a running update
    immediately instead of queueing behind it.
    """

    def __init__(self) -> None:
        self._guard = asyncio.Lock()
        self._held: set[str] = set()

    async def acquire(
        self, stack: str, *, wait_seconds: float = 0.0
    ) -> "DeploymentUpdateLockLease":
        normalized = _required_string(stack, "stack")
        deadline = asyncio.get_running_loop().time() + max(0.0, wait_seconds)
        while True:
            async with self._guard:
                if normalized not in self._held:
                    self._held.add(normalized)
                    return DeploymentUpdateLockLease(self, normalized)
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise _lock_unavailable(normalized, retryable=False)
            await asyncio.sleep(min(_DEPLOYMENT_LOCK_POLL_SECONDS, remaining))

    async def _release(self, stack: str) -> None:
        async with self._guard:
            self._held.discard(stack)


@dataclass(slots=True)
class DeploymentUpdateLockLease:
    _manager: DeploymentUpdateLockManager
    stack: str
    _released: bool = False

    async def release(self) -> None:
        if self._released:
            return
        self._released = True
        await self._manager._release(self.stack)

    async def __aenter__(self) -> "DeploymentUpdateLockLease":
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.release()


@dataclass(frozen=True, slots=True)
class FileDeploymentUpdateLockManager:
    """Kernel-owned lock shared by every container controlling this stack.

    Process IDs and elapsed time are not ownership evidence across containers.
    The stable inode remains in place; process exit releases the kernel lease.
    """

    lock_dir: str

    async def acquire(
        self, stack: str, *, wait_seconds: float = 0.0
    ) -> "FileDeploymentUpdateLockLease":
        import fcntl
        normalized = _validate_stack_path_component(stack)
        lock_path = Path(self.lock_dir).expanduser() / f"{normalized}.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        # Contention with a bounded background owner is routine, not a running
        # update. A caller that declares a wait budget waits that owner out;
        # the default keeps the nonblocking acquire background sweeps need.
        deadline = asyncio.get_running_loop().time() + max(0.0, wait_seconds)
        while True:
            handle = lock_path.open("a+")
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                handle.close()
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise _lock_unavailable(normalized, retryable=True) from exc
                await asyncio.sleep(min(_DEPLOYMENT_LOCK_POLL_SECONDS, remaining))
                continue
            break
        handle.seek(0)
        previous = handle.read()
        if previous:
            try:
                compatible = json.loads(previous).get("contract") == "moonmind.deployment-kernel-lock.v1"
            except (ValueError, AttributeError):
                compatible = False
            if not compatible:
                handle.close()
                raise ToolFailure(
                    error_code="DEPLOYMENT_LOCKED",
                    message="A legacy deployment lock remains; its original controller must release ownership before cutover.",
                    retryable=True,
                    details={"failureClass": "legacy_deployment_owner"},
                )
        handle.seek(0)
        handle.truncate()
        handle.write(json.dumps({"contract": "moonmind.deployment-kernel-lock.v1", "stack": normalized}))
        handle.flush()
        os.fsync(handle.fileno())
        return FileDeploymentUpdateLockLease(handle)


@dataclass(frozen=True, slots=True)
class FileDeploymentUpdateLockLease:
    handle: Any

    async def release(self) -> None:
        self.handle.close()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.release()


class InMemoryDesiredStateStore:
    """Deterministic desired-state store for hermetic execution tests."""

    def __init__(self) -> None:
        self.records: list[Mapping[str, Any]] = []

    async def persist(self, payload: Mapping[str, Any]) -> str:
        record = dict(payload)
        self.records.append(record)
        return _stable_ref("desired-state", record)


class InMemoryEvidenceWriter:
    """Deterministic evidence writer for hermetic execution tests."""

    def __init__(self) -> None:
        self.records: list[tuple[str, Mapping[str, Any]]] = []

    async def write(self, kind: str, payload: Mapping[str, Any]) -> str:
        record = dict(payload)
        self.records.append((kind, record))
        return _stable_ref(kind, record)


@dataclass(frozen=True, slots=True)
class FileDesiredStateStore:
    """Atomic desired-state store backed by an allowlisted deployment env file.

    The env file is the Compose-consumed desired state. A JSON sidecar preserves
    the audit fields that do not belong in process environment variables.
    """

    env_file_path: str
    json_file_path: str | None = None
    image_env_var: str = "MOONMIND_IMAGE"

    async def persist(self, payload: Mapping[str, Any]) -> str:
        record = dict(payload)
        env_path = Path(self.env_file_path).expanduser()
        json_path = (
            Path(self.json_file_path).expanduser()
            if self.json_file_path
            else env_path.with_suffix(env_path.suffix + ".json")
        )
        # The singular Omnigent release owns `OMNIGENT_*` env refs and the
        # `omnigentRelease` sidecar document independently of MoonMind image
        # authority. A plain rewrite would delete them on every MoonMind
        # update and lose the revision chain, so preserve them here; the
        # release migration remains the sole writer of those keys.
        try:
            existing_env, existing_json = _read_desired_state_files(
                env_path, json_path
            )
        except OSError:
            existing_env, existing_json = {}, {}
        preserved_env = {
            k: v
            for k, v in existing_env.items()
            if k.startswith("OMNIGENT_") and str(v or "").strip()
        }
        if isinstance(existing_json, dict) and "omnigentRelease" in existing_json:
            if "omnigentRelease" not in record:
                record["omnigentRelease"] = existing_json["omnigentRelease"]
        desired_image = _desired_deployed_image(record)
        requested_image = _desired_requested_image(record)
        run_id = str(record.get("sourceRunId") or "").strip()
        env_payload = {
            **preserved_env,
            self.image_env_var: desired_image,
            f"{self.image_env_var}_REQUESTED": requested_image,
            "MOONMIND_DEPLOYMENT_RUN_ID": run_id,
        }
        await asyncio.to_thread(
            _write_desired_state_files,
            env_path,
            json_path,
            env_payload,
            record,
        )
        return f"file:{env_path}"

    async def merge(
        self,
        *,
        env_updates: Mapping[str, Any] | None = None,
        json_updates: Mapping[str, Any] | None = None,
    ) -> str:
        """Merge keys into the desired-state files, preserving other entries.

        Unlike :meth:`persist`, which rewrites the MoonMind image authority
        from scratch, merge keeps every existing entry (including entries this
        release did not author) and only adds or replaces the supplied keys.
        Unparseable env lines are preserved verbatim so a merge never drops
        operator content it cannot understand.
        """
        env_path = Path(self.env_file_path).expanduser()
        json_path = (
            Path(self.json_file_path).expanduser()
            if self.json_file_path
            else env_path.with_suffix(env_path.suffix + ".json")
        )
        await asyncio.to_thread(
            _merge_desired_state_files,
            env_path,
            json_path,
            dict(env_updates or {}),
            dict(json_updates or {}),
        )
        return f"file:{env_path}"

    def read(self) -> tuple[Mapping[str, str], Mapping[str, Any]]:
        """Return the current desired-state env entries and JSON record."""
        env_path = Path(self.env_file_path).expanduser()
        json_path = (
            Path(self.json_file_path).expanduser()
            if self.json_file_path
            else env_path.with_suffix(env_path.suffix + ".json")
        )
        return _read_desired_state_files(env_path, json_path)


@dataclass(frozen=True, slots=True)
class TemporalDeploymentEvidenceWriter:
    """Deployment evidence writer backed by Temporal artifacts."""

    artifact_service: Any
    principal: str = "system:deployment"
    execution_ref: Mapping[str, Any] | None = None

    async def write(self, kind: str, payload: Mapping[str, Any]) -> str:
        encoded = (
            json.dumps(payload, sort_keys=True, default=str, indent=2) + "\n"
        ).encode("utf-8")
        artifact, _upload = await self.artifact_service.create(
            principal=self.principal,
            content_type="application/json",
            link=self.execution_ref,
            metadata_json={
                "artifactClass": "deployment.evidence",
                "deploymentEvidenceKind": kind,
            },
        )
        completed = await self.artifact_service.write_complete(
            artifact_id=artifact.artifact_id,
            principal=self.principal,
            payload=encoded,
            content_type="application/json",
        )
        return str(getattr(completed, "artifact_id", artifact.artifact_id))


class DisabledComposeRunner:
    """Fail-closed runner used when deployment-control infrastructure is absent."""

    async def capture_state(self, *, stack: str, phase: str) -> Mapping[str, Any]:
        raise ToolFailure(
            error_code="POLICY_VIOLATION",
            message="Deployment update runner is not configured for this worker.",
            retryable=False,
            details={
                "stack": stack,
                "phase": phase,
                "failureClass": "policy_violation",
            },
        )

    async def pull(
        self,
        *,
        stack: str,
        command: tuple[str, ...],
        requested_image: str,
    ) -> Mapping[str, Any]:
        raise ToolFailure(
            error_code="POLICY_VIOLATION",
            message="Deployment update runner is not configured for this worker.",
            retryable=False,
            details={
                "stack": stack,
                "command": list(command),
                "failureClass": "policy_violation",
            },
        )

    async def up(
        self,
        *,
        stack: str,
        command: tuple[str, ...],
        requested_image: str,
    ) -> Mapping[str, Any]:
        raise ToolFailure(
            error_code="POLICY_VIOLATION",
            message="Deployment update runner is not configured for this worker.",
            retryable=False,
            details={
                "stack": stack,
                "command": list(command),
                "failureClass": "policy_violation",
            },
        )

    async def inspect_image(self, requested_image: str) -> Mapping[str, Any]:
        raise ToolFailure(
            error_code="DEPLOYMENT_RUNNER_UNAVAILABLE",
            message="Deployment update runner is not configured for this worker.",
            retryable=False,
            details={
                "requestedImage": requested_image,
                "failureClass": "runner_unavailable",
            },
        )

    async def verify(
        self,
        *,
        stack: str,
        requested_image: str,
        resolved_digest: str | None,
    ) -> ComposeVerification:
        raise ToolFailure(
            error_code="POLICY_VIOLATION",
            message="Deployment update runner is not configured for this worker.",
            retryable=False,
            details={
                "stack": stack,
                "requested_image": requested_image,
                "resolved_digest": resolved_digest,
                "failureClass": "policy_violation",
            },
        )


def _is_host_absolute_path(path: Path | str) -> bool:
    """Return True for paths that are absolute on either POSIX or Windows.

    A worker running on Linux still receives Windows host paths (e.g.
    ``C:\\repo``) when the operator runs Docker Desktop on Windows. ``Path``
    parses those as relative on POSIX, so we additionally accept the
    ``<drive>:`` prefix and UNC-style ``\\\\server\\share`` forms.
    """

    text = str(path)
    if not text:
        return False
    if Path(text).is_absolute():
        return True
    if len(text) >= 2 and text[1] == ":" and text[0].isalpha():
        return True
    if text.startswith("\\\\") or text.startswith("//"):
        return True
    return False


def _is_wsl_distro_path(path: str) -> bool:
    """Return True for WSL user-distro ``/mnt/<drive>`` paths.

    Only single-letter drives qualify; longer ``/mnt/<name>`` mounts (for
    example ``/mnt/data``) are genuine Linux mounts and keep their POSIX
    namespace.
    """

    return (
        re.match(r"^/mnt/[A-Za-z](?:/.*)?$", path.strip().replace("\\", "/"))
        is not None
    )


def _read_self_container_id() -> str | None:
    """Return the container id of the running worker, if it is containerized."""

    identity = os.environ.get("HOSTNAME") or os.environ.get("CONTAINER_ID")
    if identity and identity.strip():
        return identity.strip()
    try:
        return Path("/etc/hostname").read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def _normalized_bind_path(path: str) -> str:
    """Compare host paths by what they address, not by their spelling."""

    text = str(path or "").strip().replace("\\", "/")
    while "//" in text:
        text = text.replace("//", "/")
    return text.rstrip("/") or "/"


def _docker_output(
    args: Sequence[str], *, attempts: int = 1, backoff_seconds: float = 0.0
) -> str | None:
    """Run a read-only Docker query and return its stdout, or None."""

    import subprocess  # local import — only needed when evidence is consulted.
    import time

    for attempt in range(max(1, attempts)):
        try:
            result = subprocess.run(
                list(args),
                capture_output=True,
                text=True,
                check=True,
                timeout=10,
            )
        except (
            subprocess.TimeoutExpired,
            subprocess.CalledProcessError,
            FileNotFoundError,
            OSError,
        ):
            if attempt + 1 < max(1, attempts):
                time.sleep(backoff_seconds * (attempt + 1))
            continue
        return result.stdout
    return None


def _docker_json_lines(
    args: Sequence[str], *, attempts: int = 1, backoff_seconds: float = 0.0
) -> list[Any]:
    """Decode a Docker query that answers with one JSON document per line."""

    output = _docker_output(args, attempts=attempts, backoff_seconds=backoff_seconds)
    decoded: list[Any] = []
    for line in (output or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            decoded.append(json.loads(line))
        except ValueError:
            continue
    return decoded


def _mount_sources(mounts: Any, local_mount: str) -> list[str]:
    """Host sources a container's mount table records for ``local_mount``."""

    target = _normalized_bind_path(local_mount)
    found: list[str] = []
    for mount in mounts if isinstance(mounts, list) else []:
        if not isinstance(mount, Mapping):
            continue
        if _normalized_bind_path(str(mount.get("Destination") or "")) != target:
            continue
        source = str(mount.get("Source") or "").strip()
        if source:
            found.append(source)
    return found


def daemon_bind_source(
    local_mount: str,
    *,
    attempts: int = 1,
    backoff_seconds: float = 0.0,
) -> str | None:
    """Return the daemon-recorded host source of our own ``local_mount``.

    The worker reads the deployment checkout and its durable state through bind
    mounts the same daemon created, so that daemon's mount table is evidence of
    which host path resolves to them. Reusing the recorded source keeps new
    Compose containers on a path the daemon has already resolved, instead of
    inferring a Desktop/WSL namespace from the path's shape: the namespace that
    serves a checkout differs between Docker Desktop backends and versions, and
    a wrong guess mounts empty directories rather than failing.

    Retries with linear backoff so a transient ``docker inspect`` failure (for
    example the deployment socket proxy still starting) does not permanently
    discard the evidence. Returns ``None`` when the container identity or the
    daemon stays unreadable, leaving the caller's configured path in place.
    """

    container_id = _read_self_container_id()
    if not container_id:
        return None
    for mounts in _docker_json_lines(
        ["docker", "inspect", "--format", "{{json .Mounts}}", container_id],
        attempts=attempts,
        backoff_seconds=backoff_seconds,
    ):
        sources = _mount_sources(mounts, local_mount)
        if sources:
            return sources[0]
    return None


def installed_bind_sources(local_mount: str, *, project_name: str) -> tuple[str, ...]:
    """Return host sources this deployment's own containers use, common first.

    Docker Desktop records more than one host path for the same WSL checkout —
    the ``/mnt/<drive>`` path and a managed ``docker-desktop-bind-mounts``
    share — depending on which client created the container. Both resolve, but
    Compose hashes the bind source *string* into each container's config, so
    switching spelling mid-life recreates every service, including the socket
    proxy the updater itself talks to. The installed containers therefore
    decide: their source is proven resolvable and keeps Compose idempotent.
    """

    identifiers = (
        _docker_output(
            [
                "docker",
                "ps",
                "-q",
                "--filter",
                f"label=com.docker.compose.project={project_name}",
                # One-off containers (`compose run`, release cohorts) are
                # transient; only the installed services define the deployment.
                "--filter",
                "label=com.docker.compose.oneoff=False",
            ]
        )
        or ""
    ).split()
    if not identifiers:
        return ()
    counted: dict[str, int] = {}
    for mounts in _docker_json_lines(
        ["docker", "inspect", "--format", "{{json .Mounts}}", *identifiers]
    ):
        for source in _mount_sources(mounts, local_mount):
            counted[source] = counted.get(source, 0) + 1
    return tuple(sorted(counted, key=lambda source: (-counted[source], source)))


_host_dir_evidence_cache: dict[tuple[str, str, str], str | None] = {}


async def _resolve_host_dir_evidence(
    *, local_mount: str, project_name: str, configured: str
) -> str | None:
    """Decide, once per daemon and project, which host path Compose receives."""

    key = (
        os.environ.get("DOCKER_HOST", ""),
        _normalized_bind_path(local_mount),
        project_name,
    )
    if key not in _host_dir_evidence_cache:
        _host_dir_evidence_cache[key] = await asyncio.to_thread(
            _host_dir_evidence, local_mount, project_name, configured
        )
    return _host_dir_evidence_cache[key]


def _host_dir_evidence(
    local_mount: str, project_name: str, configured: str
) -> str | None:
    installed = installed_bind_sources(local_mount, project_name=project_name)
    if installed:
        # The configured path wins whenever the deployment proves it works, so
        # an operator's declared value is never quietly replaced by an
        # equivalent spelling.
        for source in installed:
            if _normalized_bind_path(source) == _normalized_bind_path(configured):
                return source
        return installed[0]
    return daemon_bind_source(local_mount)


def _observed_host_dir_evidence(local_mount: str, project_name: str) -> str | None:
    """Read an already-resolved decision without blocking on the daemon."""

    return _host_dir_evidence_cache.get(
        (
            os.environ.get("DOCKER_HOST", ""),
            _normalized_bind_path(local_mount),
            project_name,
        )
    )


_desktop_daemon_probe_cache: dict[str, bool | None] = {}


async def _probe_docker_desktop_daemon() -> bool | None:
    """Report whether the reachable daemon is Docker Desktop.

    Returns True for Docker Desktop, False for another daemon, and None when
    the platform cannot be established. Results are cached per ``DOCKER_HOST``
    so every Compose invocation does not re-probe. ``None`` callers keep the
    Desktop rewrite: the daemon is unreachable either way, and rewritten binds
    disable automatic host-directory creation so a misclassified source fails
    loudly instead of mounting an empty directory.
    """

    key = os.environ.get("DOCKER_HOST", "")
    if key in _desktop_daemon_probe_cache:
        return _desktop_daemon_probe_cache[key]
    result: bool | None = None
    try:
        process = await asyncio.create_subprocess_exec(
            "docker",
            "info",
            "--format",
            "{{.OperatingSystem}}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError:
        result = None
    else:
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=10)
        except TimeoutError:
            if process.returncode is None:
                process.kill()
                await process.wait()
            result = None
        except OSError:
            result = None
        else:
            if process.returncode == 0:
                result = (
                    stdout.decode("utf-8", errors="replace").strip()
                    == "Docker Desktop"
                )
    _desktop_daemon_probe_cache[key] = result
    return result


def _docker_desktop_host_path(path: str) -> str | None:
    """Translate Windows and WSL paths into Docker Desktop's daemon namespace.

    The Linux deployment worker talks directly to the Desktop daemon. Both
    Windows drive paths (``C:\\repo``) and WSL user-distro ``/mnt/<drive>``
    paths are not the daemon's host-file mounts; they resolve to
    ``/run/desktop/mnt/host/<drive>/...``. Longer ``/mnt/<name>`` mounts
    (for example ``/mnt/data``) are genuine Linux mounts and pass through.
    """

    normalized = path.strip()
    if len(normalized) >= 2 and normalized[1] == ":" and normalized[0].isalpha():
        tail = normalized[2:].replace("\\", "/").lstrip("/")
        drive = normalized[0].lower()
        if tail:
            return str(_DOCKER_DESKTOP_HOST_MOUNT_ROOT / drive / tail)
        return str(_DOCKER_DESKTOP_HOST_MOUNT_ROOT / drive)
    unified = normalized.replace("\\", "/")
    if _is_wsl_distro_path(normalized):
        drive = unified.split("/")[2].lower()
        tail = "/".join(part for part in unified.split("/")[3:] if part)
        if tail:
            return str(_DOCKER_DESKTOP_HOST_MOUNT_ROOT / drive / tail)
        return str(_DOCKER_DESKTOP_HOST_MOUNT_ROOT / drive)
    return None


def _remap_host_compose_path(
    compose_file: Path, host_dir: Path, local_dir: Path
) -> Path | None:
    """Map an absolute host-side ``compose_file`` into the local mount.

    Preserves the subpath beneath ``host_dir`` (so
    ``/host/repo/deploy/foo.yaml`` becomes ``<local_dir>/deploy/foo.yaml``).
    Falls back to the basename when no shared prefix is present, which keeps
    the previous flat-path behavior for cases the host path is unrelated to
    the project directory.
    """

    try:
        relative = compose_file.relative_to(host_dir)
        return local_dir / relative
    except ValueError:
        pass
    host_text = str(host_dir).replace("\\", "/").rstrip("/")
    compose_text = str(compose_file).replace("\\", "/")
    if host_text and compose_text.lower().startswith(host_text.lower() + "/"):
        suffix = compose_text[len(host_text) + 1 :]
        if suffix:
            return local_dir.joinpath(*suffix.split("/"))
    return local_dir / compose_file.name


def _tail_text(payload: bytes, *, max_chars: int | None = 512) -> str:
    text = payload.decode("utf-8", errors="replace")
    if max_chars is None:
        return text
    if max_chars <= 0:
        return ""
    return text[-max_chars:]


@dataclass(frozen=True, slots=True)
class HostDockerComposeRunner:
    """Docker Compose runner for a trusted deployment-control worker.

    ``project_dir`` is the **host** filesystem path of the checkout, used as
    ``--project-directory`` so Compose resolves relative bind mounts to paths
    the host Docker daemon can see. ``local_project_dir`` is where the same
    checkout is mounted **inside** the worker container; the worker reads the
    compose file and runs the subprocess from there. When unset they collapse
    to a single value (legacy behavior used by tests on the host).
    """

    project_dir: str
    compose_file: str | None = None
    project_name: str = "moonmind"
    # The release supervision window is sized from this same number, so the
    # Activity watching a release can never be shorter than one command.
    command_timeout_seconds: int = RELEASE_RUNNER_COMMAND_TIMEOUT_SECONDS
    local_project_dir: str | None = None
    env_file: str | None = None
    excluded_services: tuple[str, ...] = ()

    # Resolved deployment-owned overrides accompany the immutable base even
    # when its path changes for a candidate or retained release.
    override_files: tuple[str, ...] = ()

    async def capture_state(self, *, stack: str, phase: str) -> Mapping[str, Any]:
        services = await self._run_compose_json(("ps", "--format", "json"))
        images = await self._run_compose_json(("images", "--format", "json"))
        configured_services = await self._run_compose_services()
        configured_service_images = await self._run_compose_config_service_images()
        return {
            "stack": stack,
            "phase": phase,
            "projectName": self.project_name,
            "configuredServices": configured_services,
            "configuredServiceImages": configured_service_images,
            "services": services,
            "images": images,
            "capturedAt": _utc_now(),
        }

    async def pull(
        self,
        *,
        stack: str,
        command: tuple[str, ...],
        requested_image: str,
    ) -> Mapping[str, Any]:
        return await self._run_compose_command(command, requested_image=requested_image)

    async def up(
        self,
        *,
        stack: str,
        command: tuple[str, ...],
        requested_image: str,
    ) -> Mapping[str, Any]:
        # Validate at the side-effect owner, before any Compose recreation.
        # Config rendering uses worker-visible paths; it never creates mounts.
        await self._record_daemon_host_dir()
        try:
            await asyncio.to_thread(
                check_compose_access,
                self._compose_base_command(project_dir=self._local_dir()),
                cwd=str(self._local_dir()),
                env={**os.environ, "MOONMIND_IMAGE": requested_image},
                services=_compose_up_target_services(command),
                include_dependencies="--no-deps" not in command,
                remove_orphans="--remove-orphans" in command,
            )
        except DeploymentAccessError as exc:
            raise ToolFailure(
                error_code="DEPLOYMENT_ACCESS_CHANGED",
                message=str(exc),
                retryable=False,
                details={"failureClass": "deployment_access_changed"},
            ) from exc
        return await self._run_compose_command(command, requested_image=requested_image)

    async def inspect_image(self, requested_image: str) -> Mapping[str, Any]:
        return await self._inspect_image(requested_image)

    async def verify(
        self,
        *,
        stack: str,
        requested_image: str,
        resolved_digest: str | None,
    ) -> ComposeVerification:
        target = await self._inspect_image(requested_image)
        target_id = str(target.get("Id") or "").strip()
        images = await self._run_compose_json(("images", "--format", "json"))
        services = await self._run_compose_json(("ps", "--format", "json"))
        repository, reference = _split_requested_image(requested_image)
        excluded_services = _normalized_service_names(self.excluded_services)
        # `compose images` includes stopped and one-off containers, including
        # the deliberately retained old release. Join its actual image IDs to
        # the normal running service inventory before deciding convergence.
        live_containers = {
            str(service.get("Name") or ""): str(service.get("Service") or "")
            for service in services
            if isinstance(service, Mapping) and service.get("State") == "running"
        }
        matched_images = []
        for image in images:
            if not isinstance(image, Mapping):
                continue
            image_repository = str(
                image.get("Repository") or image.get("repository") or ""
            ).strip()
            if image_repository != repository:
                continue
            service_name = live_containers.get(str(image.get("ContainerName") or ""))
            if not service_name:
                continue
            if _service_is_excluded(service_name, excluded_services):
                continue
            matched_images.append({**image, "Service": service_name})
        mismatches: list[dict[str, Any]] = []
        updated_services: list[str] = []
        for image in matched_images:
            image_id = str(
                image.get("ID")
                or image.get("Id")
                or image.get("ImageID")
                or image.get("image_id")
                or ""
            ).strip()
            service_name = str(
                image.get("Service") or image.get("Name") or image.get("Container")
                or ""
            ).strip()
            tag = str(image.get("Tag") or image.get("tag") or "").strip()
            if service_name:
                updated_services.append(service_name)
            if not target_id or not image_id or image_id != target_id:
                mismatches.append(
                    {
                        "service": service_name or None,
                        "repository": repository,
                        "tag": tag or None,
                        "imageId": image_id,
                        "expectedImageId": target_id,
                    }
                )
        succeeded = bool(matched_images) and not mismatches
        details = {
            "requestedImage": requested_image,
            "resolvedDigest": resolved_digest,
            "targetImageId": target_id or None,
            "targetRepoDigests": target.get("RepoDigests") or [],
            "matchedImageCount": len(matched_images),
            "failedChecks": mismatches,
            "requestedReference": reference,
        }
        if not matched_images:
            details["failureReason"] = (
                "No running Compose services were found for the requested image "
                f"repository {repository}."
            )
        elif mismatches:
            details["failureReason"] = (
                "One or more Compose services are not running the pulled target image."
            )
        return ComposeVerification(
            succeeded=succeeded,
            updated_services=tuple(sorted(set(updated_services))),
            running_services=tuple(
                service for service in services if isinstance(service, Mapping)
            ),
            details=details,
        )

    def _local_dir(self) -> Path:
        return Path(self.local_project_dir or self.project_dir).expanduser()

    async def _record_daemon_host_dir(self) -> str | None:
        """Resolve, once per daemon, the host path serving this checkout."""

        if not self.local_project_dir:
            return None
        return await _resolve_host_dir_evidence(
            local_mount=str(self._local_dir()),
            project_name=self.project_name,
            configured=str(self.project_dir),
        )

    def _host_dir(self) -> Path:
        # A host path this deployment demonstrably resolves outranks one
        # inferred from the configured path's shape.
        observed = (
            _observed_host_dir_evidence(str(self._local_dir()), self.project_name)
            if self.local_project_dir
            else None
        )
        if observed:
            return Path(observed)
        return Path(self.project_dir).expanduser()

    def _compose_file_path(self) -> Path:
        host_dir = self._host_dir()
        local_dir = self._local_dir()
        if self.compose_file:
            compose_file = Path(self.compose_file).expanduser()
            is_abs = _is_host_absolute_path(compose_file)
            if is_abs and not compose_file.exists():
                # Treat absolute host paths that don't exist locally as
                # host-side paths and remap them into the local mount,
                # preserving any subpath beneath ``host_dir`` so configs
                # like ``/host/repo/deploy/docker-compose.yaml`` resolve to
                # ``<local_dir>/deploy/docker-compose.yaml`` rather than
                # collapsing to the basename.
                candidate = _remap_host_compose_path(compose_file, host_dir, local_dir)
                if candidate is not None and candidate.exists():
                    compose_file = candidate
            elif not is_abs:
                compose_file = local_dir / compose_file
        else:
            compose_file = local_dir / "docker-compose.yaml"
        return compose_file

    def _compose_base_command(
        self,
        *,
        project_dir: Path | None = None,
        compose_file: Path | None = None,
        include_env_file: bool = True,
    ) -> list[str]:
        host_dir = project_dir or self._host_dir()
        if not _is_host_absolute_path(host_dir):
            raise ToolFailure(
                error_code="POLICY_VIOLATION",
                message="Deployment compose project directory must be absolute.",
                retryable=False,
                details={
                    "project_dir": str(host_dir),
                    "failureClass": "policy_violation",
                },
            )
        local_dir = self._local_dir()
        if not local_dir.exists():
            raise ToolFailure(
                error_code="POLICY_VIOLATION",
                message="Deployment compose project directory is not mounted.",
                retryable=False,
                details={
                    "project_dir": str(local_dir),
                    "failureClass": "policy_violation",
                },
            )
        compose_file = compose_file or self._compose_file_path()
        if not compose_file.exists():
            raise ToolFailure(
                error_code="POLICY_VIOLATION",
                message="Deployment compose file is not mounted.",
                retryable=False,
                details={
                    "compose_file": str(compose_file),
                    "failureClass": "policy_violation",
                },
            )
        command = [
            "docker",
            "compose",
            "--project-name",
            self.project_name,
            "--project-directory",
            str(host_dir),
            "-f",
            str(compose_file),
        ]
        if compose_file == self._compose_file_path():
            # A Windows rendered JSON file already includes these overrides.
            for override in self.override_files:
                command.extend(["-f", override])
        if include_env_file:
            # --env-file replaces Compose's implicit .env loading. Include the
            # deployment-owned configuration explicitly before the image-only
            # desired-state overlay, or unrelated auth/network values disappear.
            environment_files = [local_dir / ".env"]
            if self.env_file:
                environment_files.append(Path(self.env_file).expanduser())
            for env_file in dict.fromkeys(environment_files):
                if env_file.exists():
                    command.extend(["--env-file", str(env_file)])
        return command

    def _compose_command(
        self,
        command: Sequence[str],
        *,
        project_dir: Path | None = None,
        compose_file: Path | None = None,
        include_env_file: bool = True,
    ) -> list[str]:
        parts = list(command)
        if len(parts) < 3 or parts[0:2] != ["docker", "compose"]:
            raise ToolFailure(
                error_code="POLICY_VIOLATION",
                message="Deployment command must use docker compose.",
                retryable=False,
                details={
                    "command": parts,
                    "failureClass": "policy_violation",
                },
            )
        return [
            *self._compose_base_command(
                project_dir=project_dir,
                compose_file=compose_file,
                include_env_file=include_env_file,
            ),
            *parts[2:],
        ]

    def _requires_desktop_host_rewrite(self) -> bool:
        """Return True when the host project dir is not daemon-visible.

        Docker Desktop on Windows serves host files from
        ``/run/desktop/mnt/host/<drive>/...``. Both Windows drive-letter
        paths (``C:\\repo``) and WSL distro mounts (``/mnt/<drive>/...``)
        must be rewritten to that namespace before the daemon can mount
        them. Native Linux host paths pass through unchanged.
        """
        text = str(self.project_dir).strip()
        if len(text) >= 2 and text[1] == ":" and text[0].isalpha():
            return True
        return _is_wsl_distro_path(text)

    async def _use_desktop_host_rewrite(self) -> bool:
        """Decide whether daemon-bound Compose input needs the host rewrite.

        The daemon's recorded source for this worker's own checkout bind
        settles the question without guessing whenever it is readable. Only
        when that evidence is unavailable does the path's shape decide:
        Windows drive-letter paths are unambiguous Desktop signals. A bare
        ``/mnt/<drive>`` shape may instead be a native Linux mount, so it is
        rewritten only when the reachable daemon confirms Docker Desktop (or
        when the platform cannot be established, where rewritten binds still
        fail loudly instead of mounting empty directories). A confirmed
        non-Desktop daemon keeps the POSIX namespace untouched.

        A Windows drive-letter host directory is never usable as a Linux
        Compose ``--project-directory``: the client treats ``D:\\...`` as a
        relative path, resolving binds to
        ``/workspace/host_project/D:\\...`` and failing with ``too many
        colons``. Even when the daemon previously resolved that spelling,
        the Linux worker must render through its local checkout and map
        binds into the daemon's ``/run/desktop/mnt/host`` namespace.
        """
        if not self.local_project_dir:
            return False
        observed = await self._record_daemon_host_dir()
        effective = str(self._host_dir())
        if len(effective.strip()) >= 2 and effective.strip()[1] == ":" and effective.strip()[0].isalpha():
            # Windows effective host: Linux Compose cannot use it directly.
            return True
        if _is_wsl_distro_path(effective):
            if observed is not None:
                # The deployment proves this WSL spelling resolves (both
                # spellings resolve on Desktop, and Compose hashes the source
                # string). Keep it to avoid recreating every service,
                # including the socket proxy this updater talks to.
                return False
            if await _probe_docker_desktop_daemon() is False:
                return False
            return True
        if self._requires_desktop_host_rewrite():
            # Configured Windows/WSL with an already daemon-visible POSIX
            # effective (e.g. installed Desktop namespace): keep installed.
            if observed is not None:
                return False
            if _is_wsl_distro_path(str(self.project_dir)):
                if await _probe_docker_desktop_daemon() is False:
                    return False
            return True
        return False

    def _host_bind_source_for_local_path(self, local_source: str) -> str:
        local_dir = str(self._local_dir()).replace("\\", "/").rstrip("/")
        normalized = local_source.replace("\\", "/")
        if normalized == local_dir:
            suffix = ""
        elif normalized.startswith(local_dir + "/"):
            suffix = normalized[len(local_dir) + 1 :]
        else:
            return local_source
        # Prefer the daemon-proven host directory when available so the
        # rewritten source keeps the deployment's existing spelling family
        # (same drive/tail); fall back to the configured path for the
        # no-evidence guess. Both Windows and WSL spellings translate to
        # the same Desktop namespace for the same checkout.
        try:
            effective = str(self._host_dir())
        except Exception:
            effective = str(self.project_dir)
        host_dir = (_docker_desktop_host_path(effective) or effective).rstrip("\\/")
        if not suffix:
            return host_dir
        separator = "/" if "/" in host_dir else "\\"
        return host_dir + separator + suffix.replace("/", separator)

    def _rewrite_local_bind_sources_to_host(
        self, compose_config: Mapping[str, Any]
    ) -> dict[str, Any]:
        rewritten = dict(compose_config)
        services = rewritten.get("services")
        if not isinstance(services, Mapping):
            return rewritten
        rewritten_services: dict[str, Any] = {}
        for service_name, raw_service in services.items():
            if not isinstance(raw_service, Mapping):
                rewritten_services[str(service_name)] = raw_service
                continue
            service = dict(raw_service)
            volumes = service.get("volumes")
            if isinstance(volumes, list):
                rewritten_volumes: list[Any] = []
                for raw_volume in volumes:
                    if isinstance(raw_volume, Mapping):
                        volume = dict(raw_volume)
                        source = volume.get("source")
                        if volume.get("type") == "bind" and isinstance(source, str):
                            host_source = self._host_bind_source_for_local_path(source)
                            if host_source != source:
                                volume["source"] = host_source
                                # A missing checkout must fail before Docker can
                                # create an empty directory over image contents.
                                volume["bind"] = {
                                    **volume.get("bind", {}),
                                    "create_host_path": False,
                                }
                        rewritten_volumes.append(volume)
                    else:
                        rewritten_volumes.append(raw_volume)
                service["volumes"] = rewritten_volumes
            rewritten_services[str(service_name)] = service
        rewritten["services"] = rewritten_services
        return rewritten

    async def _write_desktop_host_resolved_compose_file(
        self, env: Mapping[str, str]
    ) -> Path:
        resolved = [
            *self._compose_base_command(project_dir=self._local_dir()),
            "config",
            "--format",
            "json",
        ]
        process = await asyncio.create_subprocess_exec(
            *resolved,
            cwd=str(self._local_dir()),
            env=dict(env),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=self.command_timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            await process.wait()
            raise ToolFailure(
                error_code="DEPLOYMENT_COMMAND_FAILED",
                message="Deployment compose config command timed out.",
                retryable=False,
                details={
                    "command": resolved,
                    "timeoutSeconds": self.command_timeout_seconds,
                    "failureClass": "compose_config_validation_failure",
                },
            ) from exc
        if process.returncode != 0:
            raise ToolFailure(
                error_code="DEPLOYMENT_COMMAND_FAILED",
                message=(
                    "Deployment compose config command failed while preparing "
                    "host-safe compose input."
                ),
                retryable=False,
                details={
                    "command": resolved,
                    "exit_code": process.returncode,
                    "stderr": _tail_text(stderr, max_chars=2000),
                    "failureClass": "compose_config_validation_failure",
                },
            )
        try:
            parsed = json.loads(stdout.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ToolFailure(
                error_code="DEPLOYMENT_COMMAND_FAILED",
                message="Deployment compose config returned invalid JSON.",
                retryable=False,
                details={
                    "command": resolved,
                    "stdout": _tail_text(stdout, max_chars=2000),
                    "stderr": _tail_text(stderr, max_chars=2000),
                    "failureClass": "compose_config_validation_failure",
                },
            ) from exc
        if not isinstance(parsed, Mapping):
            raise ToolFailure(
                error_code="DEPLOYMENT_COMMAND_FAILED",
                message="Deployment compose config returned an invalid payload.",
                retryable=False,
                details={
                    "command": resolved,
                    "stderr": _tail_text(stderr, max_chars=2000),
                    "failureClass": "compose_config_validation_failure",
                },
            )
        rewritten = self._rewrite_local_bind_sources_to_host(parsed)
        handle = tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            prefix="moonmind-compose-",
            suffix=".json",
            delete=False,
        )
        with handle:
            json.dump(rewritten, handle, separators=(",", ":"))
            handle.write("\n")
        return Path(handle.name)

    def _ensure_host_project_read_alias(self) -> None:
        """Expose the local checkout at the host path for Compose-side file reads."""

        host_dir = self._host_dir()
        local_dir = self._local_dir()
        if host_dir == local_dir:
            return
        if not host_dir.is_absolute():
            # Windows host paths are not representable as Linux filesystem
            # aliases inside the worker. They continue to rely on explicit
            # local compose-file remapping.
            return
        if host_dir.is_symlink() and not host_dir.exists():
            # Broken symlink at the alias path: clear it so the create path can
            # reinstall a valid link below.
            with contextlib.suppress(OSError):
                host_dir.unlink()
        if host_dir.exists():
            try:
                already_aliased = host_dir.resolve() == local_dir.resolve()
            except OSError:
                already_aliased = False
            if already_aliased:
                return
            raise ToolFailure(
                error_code="POLICY_VIOLATION",
                message=(
                    "Deployment compose project path already exists at the worker's "
                    "host path but does not resolve to the active checkout."
                ),
                retryable=False,
                details={
                    "project_dir": str(host_dir),
                    "local_project_dir": str(local_dir),
                    "failureClass": "policy_violation",
                },
            )
        parent = host_dir.parent
        if parent.exists() and not parent.is_dir():
            raise ToolFailure(
                error_code="POLICY_VIOLATION",
                message="Deployment compose project path parent is not a directory.",
                retryable=False,
                details={
                    "project_dir": str(host_dir),
                    "parent": str(parent),
                    "failureClass": "policy_violation",
                },
            )
        try:
            parent.mkdir(parents=True, exist_ok=True)
            host_dir.symlink_to(local_dir, target_is_directory=True)
        except FileExistsError:
            # Race: another worker installed the alias between our checks.
            return
        except OSError as exc:
            raise ToolFailure(
                error_code="POLICY_VIOLATION",
                message=(
                    "Deployment compose project directory is not readable at its "
                    "host path inside the worker."
                ),
                retryable=False,
                details={
                    "project_dir": str(host_dir),
                    "local_project_dir": str(local_dir),
                    "failureClass": "policy_violation",
                },
            ) from exc

    async def _run_compose_command(
        self,
        command: Sequence[str],
        *,
        requested_image: str | None = None,
        max_stdout_chars: int | None = 512,
        max_stderr_chars: int | None = 512,
    ) -> Mapping[str, Any]:
        await self._record_daemon_host_dir()
        self._ensure_host_project_read_alias()
        env = os.environ.copy()
        if requested_image:
            env["MOONMIND_IMAGE"] = requested_image
        temp_compose_file: Path | None = None
        if await self._use_desktop_host_rewrite():
            temp_compose_file = await self._write_desktop_host_resolved_compose_file(env)
            resolved = self._compose_command(
                command,
                project_dir=self._local_dir(),
                compose_file=temp_compose_file,
                include_env_file=False,
            )
        else:
            resolved = self._compose_command(command)
        process = await asyncio.create_subprocess_exec(
            *resolved,
            cwd=str(self._local_dir()),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=self.command_timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            await process.wait()
            raise ToolFailure(
                error_code="DEPLOYMENT_COMMAND_FAILED",
                message="Deployment compose command timed out.",
                retryable=False,
                details={
                    "command": resolved,
                    "timeoutSeconds": self.command_timeout_seconds,
                    "failureClass": "compose_config_validation_failure",
                },
            ) from exc
        finally:
            if temp_compose_file is not None:
                with contextlib.suppress(OSError):
                    temp_compose_file.unlink()
        return {
            "command": resolved,
            "exitCode": process.returncode,
            "stdout": _tail_text(stdout, max_chars=max_stdout_chars),
            "stderr": _tail_text(stderr, max_chars=max_stderr_chars),
        }

    async def _run_compose_json(self, args: Sequence[str]) -> list[Mapping[str, Any]]:
        result = await self._run_compose_command(
            ("docker", "compose", *args),
            max_stdout_chars=None,
            max_stderr_chars=2000,
        )
        _ensure_command_succeeded("config", result)
        stdout = str(result.get("stdout") or "")
        if stdout.strip():
            try:
                return _parse_json_records(stdout)
            except json.JSONDecodeError as exc:
                raise ToolFailure(
                    error_code="DEPLOYMENT_COMMAND_FAILED",
                    message="Deployment compose command returned invalid JSON.",
                    retryable=False,
                    details={
                        "phase": "config",
                        "command": result.get("command"),
                        "stdout": _tail_text(stdout.encode("utf-8"), max_chars=2000),
                        "stderr": result.get("stderr"),
                        "failureClass": "compose_config_validation_failure",
                    },
                ) from exc
        raise ToolFailure(
            error_code="DEPLOYMENT_COMMAND_FAILED",
            message="Deployment compose command returned no JSON output.",
            retryable=False,
            details={
                "phase": "config",
                "command": result.get("command"),
                "stderr": result.get("stderr"),
                "failureClass": "compose_config_validation_failure",
            },
        )

    async def _run_compose_services(self) -> tuple[str, ...]:
        result = await self._run_compose_command(
            ("docker", "compose", "config", "--services"),
            max_stdout_chars=None,
            max_stderr_chars=2000,
        )
        _ensure_command_succeeded("config", result)
        return tuple(
            line.strip()
            for line in str(result.get("stdout") or "").splitlines()
            if line.strip()
        )

    async def _run_compose_config_service_images(self) -> Mapping[str, str]:
        result = await self._run_compose_command(
            ("docker", "compose", "config", "--format", "json"),
            max_stdout_chars=None,
            max_stderr_chars=2000,
        )
        _ensure_command_succeeded("config", result)
        stdout = str(result.get("stdout") or "")
        try:
            parsed = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise ToolFailure(
                error_code="DEPLOYMENT_COMMAND_FAILED",
                message="Deployment compose config returned invalid JSON.",
                retryable=False,
                details={
                    "phase": "config",
                    "command": result.get("command"),
                    "stdout": _tail_text(stdout.encode("utf-8"), max_chars=2000),
                    "stderr": result.get("stderr"),
                    "failureClass": "compose_config_validation_failure",
                },
            ) from exc
        if not isinstance(parsed, Mapping):
            raise ToolFailure(
                error_code="DEPLOYMENT_COMMAND_FAILED",
                message="Deployment compose config returned an invalid payload.",
                retryable=False,
                details={
                    "phase": "config",
                    "command": result.get("command"),
                    "stderr": result.get("stderr"),
                    "failureClass": "compose_config_validation_failure",
                },
            )
        services = parsed.get("services")
        if not isinstance(services, Mapping):
            return {}
        configured_images: dict[str, str] = {}
        for service_name, service_config in services.items():
            if not isinstance(service_config, Mapping):
                continue
            image = str(service_config.get("image") or "").strip()
            if image:
                configured_images[str(service_name)] = image
        return configured_images

    async def _inspect_image(self, requested_image: str) -> Mapping[str, Any]:
        process = await asyncio.create_subprocess_exec(
            "docker",
            "image",
            "inspect",
            requested_image,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            raise ToolFailure(
                error_code="DEPLOYMENT_COMMAND_FAILED",
                message="Pulled deployment image could not be inspected.",
                retryable=False,
                details={
                    "requestedImage": requested_image,
                    "stderr": _tail_text(stderr),
                    "failureClass": "image_pull_failure",
                },
            )
        parsed = json.loads(stdout.decode("utf-8"))
        if not isinstance(parsed, list) or not parsed or not isinstance(parsed[0], Mapping):
            raise ToolFailure(
                error_code="DEPLOYMENT_COMMAND_FAILED",
                message="Docker image inspect returned an invalid payload.",
                retryable=False,
                details={
                    "requestedImage": requested_image,
                    "failureClass": "image_pull_failure",
                },
            )
        return dict(parsed[0])


@dataclass(slots=True)
class DeploymentUpdateExecutor:
    lock_manager: DeploymentUpdateLockManager
    desired_state_store: DesiredStateStore
    evidence_writer: EvidenceWriter
    runner: ComposeRunner
    excluded_services: tuple[str, ...] = ()
    # How long an update waits for the stack lock before reporting contention.
    # The lock is shared with bounded background owners, so a zero wait makes a
    # routine sweep fail an operator's release.
    lock_wait_seconds: float = DEPLOYMENT_UPDATE_LOCK_WAIT_SECONDS
    # Stale-code recovery (MoonLadderStudios/MoonMind#4224): after services are
    # recreated, bind-mounted workers that were not recreated may still run
    # stale modules. The checker reports stale workers
    # ([{worker, startupRevision, currentRevision, busy}]) and the restarter
    # restarts idle workers immediately while draining busy ones (never
    # killing in-flight work). Both default to None to preserve the
    # image-only update behavior for callers that do not opt in; when a check
    # reports stale workers but no restarter can fix them, the update fails
    # loudly instead of admitting new work on stale workers.
    stale_worker_checker: (
        Callable[[], Awaitable[Sequence[Mapping[str, Any]]]] | None
    ) = None
    stale_worker_restarter: (
        Callable[
            [Sequence[Mapping[str, Any]]], Awaitable[Mapping[str, Any]]
        ]
        | None
    ) = None

    async def _reconcile_stale_workers(
        self,
        *,
        stack: str,
        command_log: dict[str, Any],
        progress_events: list[dict[str, str]],
        write_evidence: Callable[[str, Mapping[str, Any]], Awaitable[str]],
    ) -> str | None:
        """Restart workers still running stale modules after recreation.

        Idle stale workers restart immediately; busy ones are drained rather
        than killed (MoonLadderStudios/MoonMind#4224). When stale workers
        remain and no restarter can fix them, the update fails loudly with
        ``reasonCode=stale_code`` instead of admitting new work on stale
        workers. Callers that do not configure a checker keep the previous
        image-only behavior.
        """

        if self.stale_worker_checker is None:
            return None
        from moonmind.workflows.temporal.worker_code_identity import (
            format_stale_code_message,
            plan_stale_worker_recovery,
        )
        from moonmind.workflows.temporal.worker_code_identity import (
            WorkerCodeFreshness,
        )

        stale = [dict(item) for item in await self.stale_worker_checker()]
        if not stale:
            command_log["workerCodeFreshness"] = {"status": "healthy", "stale": []}
            return None
        actionable = [
            item for item in stale if str(item.get("status") or "stale") == "stale"
        ]
        if not actionable:
            # Fail-open for unidentifiable workers (unreachable endpoints or
            # envelopes without identities): an unknown worker must not wedge
            # the update, but it is recorded as unknown — never healthy.
            command_log["workerCodeFreshness"] = {
                "status": "unknown",
                "stale": [],
                "unknown": [
                    {
                        "worker": str(item.get("worker") or item.get("name") or "unknown"),
                        "startupRevision": str(item.get("startupRevision") or "unknown"),
                        "currentRevision": str(item.get("currentRevision") or "unknown"),
                    }
                    for item in stale
                ],
            }
            return None
        freshness = [
            WorkerCodeFreshness(
                name=str(item.get("worker") or item.get("name") or "unknown"),
                status="stale",
                startup_revision=str(item.get("startupRevision") or "unknown"),
                current_revision=str(item.get("currentRevision") or "unknown"),
            )
            for item in actionable
        ]
        busy = {
            str(item.get("worker") or item.get("name") or "").strip()
            for item in actionable
            if item.get("busy") is True
        }
        plan = plan_stale_worker_recovery(
            [item.name for item in freshness], busy=sorted(busy)
        )
        recovery: dict[str, Any] = {
            "status": "stale",
            "reasonCode": "stale_code",
            "stale": [item.to_payload() for item in freshness],
            "plan": plan.to_payload(),
        }
        _add_progress(
            progress_events,
            "RESTARTING_STALE_WORKERS",
            "Restarting workers running stale code: "
            + ", ".join(item.name for item in freshness)
            + ".",
        )
        if self.stale_worker_restarter is None:
            recovery["recovery"] = {"status": "no_restarter"}
            command_log["workerCodeFreshness"] = recovery
            recovery_ref = await write_evidence("worker-code-freshness", recovery)
            recovery["recoveryArtifactRef"] = recovery_ref
            raise ToolFailure(
                error_code="DEPLOYMENT_STALE_WORKER_CODE",
                message=format_stale_code_message(freshness)
                + " No stale-worker restarter is configured for this stack.",
                retryable=False,
                details={"stack": stack, "reasonCode": "stale_code",
                         "staleWorkers": [item.to_payload() for item in freshness],
                         "recoveryArtifactRef": recovery_ref},
            )
        outcome = dict(await self.stale_worker_restarter(actionable))
        recovery["recovery"] = outcome
        # Post-restart verification requires known freshness: an unreachable
        # or not-yet-ready endpoint reports ``unknown``, which is absence of
        # evidence — not proof the replacement is fresh. Retry to a bounded
        # terminal state instead of recording success on an empty list.
        actionable_names = {item.name for item in freshness}
        remaining: list[dict[str, Any]] = []
        for attempt in range(3):
            rechecked = [dict(item) for item in await self.stale_worker_checker()]
            remaining = [
                item
                for item in rechecked
                if str(item.get("worker") or item.get("name") or "") in actionable_names
                and str(item.get("status") or "stale") in ("stale", "unknown")
            ]
            if not remaining:
                break
            if attempt < 2:
                await asyncio.sleep(5)
        recovery["remaining"] = remaining
        command_log["workerCodeFreshness"] = recovery
        recovery_ref = await write_evidence("worker-code-freshness", recovery)
        recovery["recoveryArtifactRef"] = recovery_ref
        failed = list(outcome.get("failed") or [])
        if remaining or failed:
            raise ToolFailure(
                error_code="DEPLOYMENT_STALE_WORKER_CODE",
                message=format_stale_code_message(freshness)
                + " Stale workers could not be restarted.",
                retryable=False,
                details={"stack": stack, "reasonCode": "stale_code",
                         "staleWorkers": [item.to_payload() for item in freshness],
                         "recovery": outcome,
                         "recoveryArtifactRef": recovery_ref},
            )
        return recovery_ref

    async def _reconcile_excluded_substrate(
        self,
        *,
        stack: str,
        parsed: Mapping[str, Any],
        command_plan: ComposeCommandPlan,
        before_state: Mapping[str, Any],
        execution_image: str,
        progress_events: list[dict[str, str]],
        command_log: dict[str, Any],
        verified: bool,
    ) -> dict[str, Any] | None:
        """Reconcile release-owned substrate excluded from the main update.

        Returns the substrate evidence report, or None when the main update
        excluded nothing the selected release configures. The staged pass
        runs only after the main stack verifies, so the controller never
        recreates its own transport mid-update; substrate that is already
        converged is left running, and substrate that does not converge
        fails the release instead of reporting success on stale definitions.
        """
        # A gateway aligned before the workers is already on the incoming
        # release. Selecting it again here would force-recreate it a second
        # time after the stack verified, bouncing egress underneath workers
        # that are live and may already be running egress-dependent work.
        aligned = {
            service.strip().lower()
            for service in _attested_gateway_services(before_state)
        }
        targets = tuple(
            service
            for service in _substrate_reconciliation_targets(
                before_state=before_state,
                excluded_services=self.excluded_services,
            )
            if service.strip().lower() not in aligned
        )
        if not targets:
            return None
        configured_images = before_state.get("configuredServiceImages")
        expected_images = (
            configured_images if isinstance(configured_images, Mapping) else {}
        )
        pending = _substrate_service_mismatches(
            state=before_state,
            targets=targets,
            expected_images=expected_images,
        )
        pending_services = [str(item["service"]) for item in pending]
        report: dict[str, Any] = {
            "targets": list(targets),
            "pendingBefore": list(pending_services),
            "reconciled": [],
            "remaining": [],
        }
        if not pending_services or not verified:
            command_log["substrate"] = report
            return report
        _add_progress(
            progress_events,
            "RECONCILING_SUBSTRATE",
            "Reconciling excluded release substrate.",
        )
        substrate_plan = build_compose_command_plan(
            mode=str(parsed["mode"]),
            remove_orphans=bool(parsed["removeOrphans"]),
            wait=bool(parsed["wait"]),
            runner_mode=command_plan.runner_mode,
        )
        pull_command = (*substrate_plan.pull_args, *pending_services)
        pull_result = await self.runner.pull(
            stack=stack,
            command=pull_command,
            requested_image=execution_image,
        )
        command_log["substratePull"] = {
            "command": list(pull_command),
            "result": dict(pull_result) if isinstance(pull_result, Mapping) else pull_result,
        }
        _ensure_command_succeeded("substrate-pull", pull_result)
        up_command = (*substrate_plan.up_args, "--no-deps", *pending_services)
        up_result = await self.runner.up(
            stack=stack,
            command=up_command,
            requested_image=execution_image,
        )
        command_log["substrateUp"] = {
            "command": list(up_command),
            "result": dict(up_result) if isinstance(up_result, Mapping) else up_result,
        }
        _ensure_command_succeeded("substrate-up", up_result)
        substrate_state = await self.runner.capture_state(
            stack=stack, phase="substrate"
        )
        remaining = _substrate_service_mismatches(
            state=substrate_state,
            targets=tuple(pending_services),
            expected_images=expected_images,
        )
        remaining_services = {str(item["service"]) for item in remaining}
        report["reconciled"] = [
            service
            for service in pending_services
            if service not in remaining_services
        ]
        report["remaining"] = remaining
        command_log["substrate"] = report
        return report

    async def _align_attested_substrate(
        self,
        *,
        stack: str,
        parsed: Mapping[str, Any],
        command_plan: ComposeCommandPlan,
        before_state: Mapping[str, Any],
        execution_image: str,
        progress_events: list[dict[str, str]],
        command_log: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Align the egress gateway before workers are recreated against it.

        Workers attest the restricted-egress gateway at startup, and the
        gateway is excluded from the main update, so a release that changes
        its policy would recreate workers against the old gateway: their
        attestation fails, ``compose up --wait`` fails, and the staged
        substrate pass that would have fixed the gateway never runs because
        it is gated on the main stack verifying. The blue/green path avoided
        this by aligning the candidate gateway before starting candidate
        workers; recreate-in-place needs the same ordering.

        Convergence is not decided here. ``before_state`` renders the
        *installed* image, so a gateway correctly running the old release
        reads as converged and would be skipped -- and the gateway is itself
        parameterized by the release image, so it does need to move. Compose
        already performs that comparison against the incoming configuration
        and leaves a converged service untouched, so this issues the pull and
        up unconditionally and lets Compose decide whether to recreate.

        Only the egress gateway moves here. The controller's own transport
        (docker-proxy) and stateful substrate stay with the staged pass so
        the updater never recreates its own transport mid-update.
        """
        targets = _attested_gateway_services(before_state)
        if not targets:
            return None
        report: dict[str, Any] = {"targets": list(targets)}
        _add_progress(
            progress_events,
            "ALIGNING_EGRESS_GATEWAY",
            "Aligning the restricted-egress gateway before recreation.",
        )
        gateway_plan = build_compose_command_plan(
            mode=str(parsed["mode"]),
            remove_orphans=bool(parsed["removeOrphans"]),
            wait=bool(parsed["wait"]),
            runner_mode=command_plan.runner_mode,
        )
        # No separate pull. The main pull targets the requested repository,
        # and the gateway may be pinned to a different one -- the
        # deployment-update-infrastructure-reconciliation replay records
        # exactly that shape. This recreates the gateway the same way the main
        # up always did, only earlier, and Compose fetches a missing image.
        up_command = (*gateway_plan.up_args, "--no-deps", *targets)
        up_result = await self.runner.up(
            stack=stack, command=up_command, requested_image=execution_image
        )
        command_log["attestedSubstrateUp"] = {
            "command": list(up_command),
            "result": dict(up_result) if isinstance(up_result, Mapping) else up_result,
        }
        # A gateway that cannot come up on the incoming release must fail the
        # update here, not after workers fail an attestation against it.
        _ensure_command_succeeded("egress-gateway-up", up_result)
        command_log["attestedSubstrate"] = report
        return report

    async def execute(
        self,
        inputs: Mapping[str, Any],
        context: Mapping[str, Any] | None = None,
    ) -> ToolResult:
        context = dict(context or {})
        if (isinstance(self.runner, HostDockerComposeRunner)
                and os.environ.get("MOONMIND_DEPLOYMENT_DESIRED_STATE_JSON_FILE")
                and context.get("deployment_runner_mode") != "ephemeral_updater_container"):
            from moonmind.workflows.skills.deployment_release import execute_detached
            return await execute_detached(self, inputs, context)
        progress_events: list[dict[str, str]] = []
        _add_progress(progress_events, "QUEUED", "Deployment update queued.")
        _add_progress(
            progress_events, "VALIDATING", "Validating deployment update input."
        )
        parsed = _parse_inputs(inputs)
        command_plan = build_compose_command_plan(
            mode=parsed["mode"],
            remove_orphans=parsed["removeOrphans"],
            wait=parsed["wait"],
            runner_mode=str(
                context.get("deployment_runner_mode") or "privileged_worker"
            ),
        )
        source_run_id = str(
            context.get("source_run_id")
            or context.get("idempotency_key")
            or context.get("workflow_id")
            or ""
        ).strip() or None
        operator = str(
            context.get("operator") or context.get("principal") or ""
        ).strip()
        operator_role = str(
            context.get("operator_role") or context.get("principal_role") or ""
        ).strip()
        workflow_id = str(context.get("workflow_id") or "").strip() or None
        task_id = (
            str(context.get("task_id") or context.get("workflow_task_id") or "").strip()
            or None
        )
        requested_image = _requested_image(parsed)
        resolved_digest = parsed["image"].get("resolvedDigest")
        started_at = _utc_now()
        before_ref: str | None = None
        after_ref: str | None = None
        command_ref: str | None = None
        verification_ref: str | None = None
        worker_code_recovery_ref: str | None = None
        verification: ComposeVerification | None = None
        final_status: str | None = None
        failure_reason: str | None = None
        after_build_id: str | None = None
        command_log: dict[str, Any] = {
            "runnerMode": command_plan.runner_mode,
            "pull": {"command": list(command_plan.pull_args)},
            "oneShot": [],
            "up": {"command": list(command_plan.up_args)},
        }

        def audit_snapshot(*, completed: bool = False) -> dict[str, Any]:
            return _compact_mapping(
                {
                    "runId": source_run_id,
                    "workflowId": workflow_id,
                    "taskId": task_id,
                    "stack": parsed["stack"],
                    "operator": operator or None,
                    "operatorRole": operator_role or None,
                    "reason": parsed["reason"],
                    "requestedImage": requested_image,
                    "resolvedDigest": resolved_digest,
                    "mode": parsed["mode"],
                    "options": {
                        "removeOrphans": parsed["removeOrphans"],
                        "wait": parsed["wait"],
                    },
                    "startedAt": started_at,
                    "completedAt": _utc_now() if completed else None,
                    "finalStatus": final_status,
                    "failureReason": failure_reason,
                }
            )

        async def write_evidence(kind: str, payload: Mapping[str, Any]) -> str:
            enriched = dict(payload)
            enriched["audit"] = audit_snapshot(completed=final_status is not None)
            return await self.evidence_writer.write(kind, _redact_sensitive(enriched))

        _add_progress(
            progress_events, "LOCK_WAITING", "Waiting for deployment update lock."
        )
        async with await self.lock_manager.acquire(
            parsed["stack"], wait_seconds=self.lock_wait_seconds
        ):
            try:
                _add_progress(
                    progress_events,
                    "CAPTURING_BEFORE_STATE",
                    "Capturing current deployment state.",
                )
                before_state = await self.runner.capture_state(
                    stack=parsed["stack"], phase="before"
                )
                command_plan = _command_plan_targeting_stack_services(
                    command_plan,
                    before_state=before_state,
                    requested_repository=str(parsed["image"]["repository"]),
                    excluded_services=self.excluded_services,
                )
                one_shot_services = _one_shot_services_from_plan(command_plan)
                # The egress gateway is aligned ahead of the workers that
                # attest it, and normally stays in the main recreation too:
                # the deployment-update-infrastructure-reconciliation replay
                # records the incident that holding it out causes, where the
                # restricted-egress network it defines went absent and the
                # worker restarted with exit code 1. Compose leaves an
                # already-aligned service alone, so including it costs
                # nothing.
                #
                # `--force-recreate` is the exception: it would bounce the
                # gateway a second time, concurrently with the workers whose
                # startup attests it, defeating the alignment. The pre-pass
                # inherits the same force flag, so the gateway has already
                # been recreated and its network exists; excluding it here
                # keeps that guarantee without the second bounce.
                forced = "--force-recreate" in tuple(command_plan.up_args)
                excluded_from_main = set(one_shot_services)
                if forced:
                    excluded_from_main |= {
                        service.strip().lower()
                        for service in _attested_gateway_services(before_state)
                    }
                service_command_plan = _command_plan_without_services(
                    command_plan,
                    excluded_services=excluded_from_main,
                )
                command_log["pull"]["command"] = list(command_plan.pull_args)
                command_log["up"]["command"] = list(service_command_plan.up_args)
                command_log["oneShot"] = [
                    {
                        "service": service,
                        "command": list(
                            _compose_up_args_for_services(
                                command_plan.up_args,
                                (service,),
                                one_shot_service=service,
                            )
                        ),
                    }
                    for service in one_shot_services
                ]
                before_ref = await write_evidence("before-state", before_state)

                _add_progress(
                    progress_events, "PULLING_IMAGES", "Pulling requested images."
                )
                pull_result = await self.runner.pull(
                    stack=parsed["stack"],
                    command=command_plan.pull_args,
                    requested_image=requested_image,
                )
                command_log["pull"]["result"] = pull_result
                _ensure_command_succeeded("pull", pull_result)
                if command_plan.missing_pull_args:
                    missing_pull_result = await self.runner.pull(
                        stack=parsed["stack"],
                        command=command_plan.missing_pull_args,
                        requested_image=requested_image,
                    )
                    command_log["missingImagePull"] = {
                        "command": list(command_plan.missing_pull_args),
                        "result": missing_pull_result,
                    }
                    _ensure_command_succeeded("pull", missing_pull_result)
                target_image = await self.runner.inspect_image(requested_image)
                command_log["targetImage"] = _target_image_audit(target_image)
                after_build_id = _target_image_build_id(target_image)
                observed_digest = _resolved_digest_from_target_image(
                    repository=str(parsed["image"]["repository"]), target_image=target_image,
                )
                if not observed_digest or (resolved_digest and resolved_digest != observed_digest):
                    raise ToolFailure(
                        error_code="DEPLOYMENT_IMAGE_IDENTITY_UNVERIFIED",
                        message="The pulled image does not verify the requested repository digest.",
                        retryable=False,
                        details={"failureClass": "image_identity_mismatch"},
                    )
                resolved_digest = observed_digest
                execution_image = f"{parsed['image']['repository']}@{resolved_digest}"
                command_log["executionImage"] = execution_image
                _ensure_runner_survives_update(
                    command_plan=service_command_plan,
                    before_state=before_state,
                    target_image=target_image,
                )

                _add_progress(
                    progress_events,
                    "PERSISTING_DESIRED_STATE",
                    "Persisting requested deployment state.",
                )
                desired_payload = {
                    "stack": parsed["stack"],
                    "imageRepository": parsed["image"]["repository"],
                    "requestedReference": parsed["image"]["reference"],
                    "resolvedDigest": resolved_digest,
                    "reason": parsed["reason"],
                    "operator": operator or None,
                    "createdAt": _utc_now(),
                    "sourceRunId": source_run_id,
                }

                if one_shot_services:
                    _add_progress(
                        progress_events,
                        "RUNNING_ONE_SHOT_SERVICES",
                        "Running one-shot deployment services.",
                    )
                for one_shot_entry in command_log["oneShot"]:
                    service_name = str(one_shot_entry["service"])
                    one_shot_result = await self.runner.up(
                        stack=parsed["stack"],
                        command=tuple(one_shot_entry["command"]),
                        requested_image=execution_image,
                    )
                    one_shot_entry["result"] = one_shot_result
                    _ensure_command_succeeded(
                        f"one-shot service {service_name}",
                        one_shot_result,
                    )

                await self.desired_state_store.persist(desired_payload)

                await self._align_attested_substrate(
                    stack=parsed["stack"],
                    parsed=parsed,
                    command_plan=command_plan,
                    before_state=before_state,
                    execution_image=execution_image,
                    progress_events=progress_events,
                    command_log=command_log,
                )

                _add_progress(
                    progress_events,
                    "RECREATING_SERVICES",
                    "Recreating deployment services.",
                )
                up_result = await self.runner.up(
                    stack=parsed["stack"],
                    command=service_command_plan.up_args,
                    requested_image=execution_image,
                )
                command_log["up"]["result"] = up_result
                _ensure_command_succeeded("up", up_result)
                command_ref = await write_evidence("command-log", command_log)

                worker_code_recovery_ref = await self._reconcile_stale_workers(
                    stack=parsed["stack"],
                    command_log=command_log,
                    progress_events=progress_events,
                    write_evidence=write_evidence,
                )
                if worker_code_recovery_ref is not None:
                    # The reconcile step mutated command_log after the first
                    # command-log write above: rewrite it so
                    # commandLogArtifactRef contains the workerCodeFreshness
                    # evidence instead of a pre-reconcile snapshot.
                    command_ref = await write_evidence("command-log", command_log)

                _add_progress(progress_events, "VERIFYING", "Verifying deployed state.")
                verification = await self.runner.verify(
                    stack=parsed["stack"],
                    requested_image=execution_image,
                    resolved_digest=resolved_digest,
                )
                final_status = _verification_final_status(verification)
                if final_status != "SUCCEEDED":
                    failure_reason = _verification_failure_reason(verification)
                substrate_report = await self._reconcile_excluded_substrate(
                    stack=parsed["stack"],
                    parsed=parsed,
                    command_plan=command_plan,
                    before_state=before_state,
                    execution_image=execution_image,
                    progress_events=progress_events,
                    command_log=command_log,
                    verified=final_status == "SUCCEEDED",
                )
                if substrate_report is not None:
                    # The substrate stage mutated command_log after the
                    # earlier command-log write: rewrite it so the artifact
                    # carries the staged handoff alongside the main plan.
                    command_ref = await write_evidence("command-log", command_log)
                    remaining = substrate_report.get("remaining") or []
                    if remaining:
                        final_status = "FAILED"
                        failure_reason = (
                            "Excluded release substrate did not converge: "
                            + ", ".join(
                                str(item.get("service") or "unknown")
                                for item in remaining
                                if isinstance(item, Mapping)
                            )
                        )
                verification_payload: dict[str, Any] = {
                    "succeeded": verification.succeeded,
                    "status": final_status,
                    "details": dict(verification.details),
                    "requestedImage": requested_image,
                    "resolvedDigest": resolved_digest,
                }
                if substrate_report is not None:
                    verification_payload["substrate"] = substrate_report
                verification_ref = await write_evidence(
                    "verification", verification_payload
                )
            except Exception as exc:
                final_status = "FAILED"
                failure_reason = failure_reason or _failure_reason(exc)
                _record_command_exception(command_log, exc)
                raise
            finally:
                if (
                    command_ref is None
                    and ("result" in command_log["pull"] or "error" in command_log)
                ):
                    command_ref = await write_evidence("command-log", command_log)
                if before_ref is not None:
                    _add_progress(
                        progress_events,
                        "CAPTURING_AFTER_STATE",
                        "Capturing deployment state after update.",
                    )
                    after_state = await self.runner.capture_state(
                        stack=parsed["stack"], phase="after"
                    )
                    after_ref = await write_evidence("after-state", after_state)

        if verification is None:
            raise ToolFailure(
                error_code="DEPLOYMENT_FAILED",
                message="Deployment update did not complete verification.",
                retryable=False,
                details={"stack": parsed["stack"]},
            )
        if final_status is None:
            final_status = _verification_final_status(verification)
        if after_ref is None:
            raise ToolFailure(
                error_code="DEPLOYMENT_EVIDENCE_INCOMPLETE",
                message="Deployment update completed without after-state evidence.",
                retryable=False,
                details={"stack": parsed["stack"]},
            )
        if command_ref is None:
            raise ToolFailure(
                error_code="DEPLOYMENT_EVIDENCE_INCOMPLETE",
                message="Deployment update completed without command-log evidence.",
                retryable=False,
                details={"stack": parsed["stack"]},
            )
        if verification_ref is None:
            raise ToolFailure(
                error_code="DEPLOYMENT_EVIDENCE_INCOMPLETE",
                message="Deployment update completed without verification evidence.",
                retryable=False,
                details={"stack": parsed["stack"]},
            )

        terminal_message = _terminal_progress_message(final_status)
        _add_progress(progress_events, final_status, terminal_message)
        outputs = {
            "status": final_status,
            "stack": parsed["stack"],
            "requestedImage": requested_image,
            "resolvedDigest": resolved_digest,
            "afterBuildId": after_build_id,
            "updatedServices": list(verification.updated_services),
            "runningServices": [
                dict(service) for service in verification.running_services
            ],
            "beforeStateArtifactRef": before_ref,
            "afterStateArtifactRef": after_ref,
            "commandLogArtifactRef": command_ref,
            "verificationArtifactRef": verification_ref,
            "audit": _redact_sensitive(audit_snapshot(completed=True)),
        }
        if worker_code_recovery_ref is not None:
            outputs["workerCodeFreshnessArtifactRef"] = worker_code_recovery_ref
        if final_status != "SUCCEEDED":
            outputs["failure"] = {
                "class": "verification_failure",
                "reason": _redact_sensitive(
                    failure_reason
                    or "Deployment verification did not prove desired state."
                ),
                "retryable": False,
            }
        return ToolResult(
            status="COMPLETED" if final_status == "SUCCEEDED" else "FAILED",
            outputs=outputs,
            progress={
                "percent": 100,
                "state": final_status,
                "message": terminal_message,
                "events": progress_events,
            },
        )


def build_env_stale_worker_checker(
    urls: Sequence[tuple[str, str]] | None = None,
) -> Callable[[], Awaitable[Sequence[Mapping[str, Any]]]] | None:
    """Build the deployment-update stale-worker checker from readiness URLs.

    Returns ``None`` when no worker readiness endpoint is configured, in which
    case the executor keeps the previous image-only behavior. Otherwise returns
    an async checker yielding ``[{worker, startupRevision, currentRevision,
    busy, service}]`` for workers whose running modules differ from the
    checkout on disk (MoonLadderStudios/MoonMind#4224).
    """

    from moonmind.workflows.temporal.worker_code_identity import (
        collect_worker_code_freshness,
        payload_busy_hint,
        probe_worker_readiness,
        readiness_urls_from_env,
    )

    targets = list(urls) if urls is not None else readiness_urls_from_env()
    if not targets:
        return None

    async def _check() -> Sequence[Mapping[str, Any]]:
        payload_by_url: dict[str, Any] = {}

        def _recording_probe(url: str) -> Any:
            payload = probe_worker_readiness(url)
            payload_by_url[url] = payload
            return payload

        freshness = await asyncio.to_thread(
            collect_worker_code_freshness, targets, probe=_recording_probe
        )
        url_by_name = dict(targets)
        items: list[dict[str, Any]] = []
        for item in freshness:
            # Surface stale workers for restart and unknown workers for
            # post-restart verification; healthy workers need no action.
            if item.status not in ("stale", "unknown"):
                continue
            url = url_by_name.get(item.name)
            if url is None and "/" in item.name:
                # Workflow-group lane ("<group>/<lane>"): the busy signal
                # lives on the group's envelope payload.
                url = url_by_name.get(item.name.split("/", 1)[0])
            payload = payload_by_url.get(url or "")
            busy = (
                payload_busy_hint(payload)
                if isinstance(payload, Mapping)
                else True
            )
            items.append(
                {
                    "worker": item.name,
                    "status": item.status,
                    "startupRevision": item.startup_revision,
                    "currentRevision": item.current_revision,
                    "busy": busy,
                    "service": _compose_service_for_worker(item.name),
                }
            )
        return items

    return _check


def _compose_service_for_worker(worker_name: str) -> str | None:
    """Map a readiness worker name to its Compose service, when known.

    Workflow-group lane names look like ``"<group>/<lane>"``: they
    restart through the group service Compose knows. A raw URL is never
    a service name — unnamed readiness targets resolve their hostname
    in ``readiness_urls_from_env``, and anything URL-shaped left over is
    rejected here so recovery cannot hand a URL to ``docker compose
    restart``. A bare ``temporal-worker-*`` hostname (from an unnamed
    readiness URL) is already the Compose service name.
    """

    try:
        from moonmind.workflows.temporal.workers import _FLEET_SERVICE_NAMES
    except Exception:
        return None
    raw = str(worker_name or "").strip()
    candidates = [raw]
    if "/" in raw:
        candidates.append(raw.split("/", 1)[0].strip())
    for candidate in candidates:
        if not candidate or "://" in candidate or "/" in candidate:
            continue
        normalized = candidate.lower()
        for fleet, service in _FLEET_SERVICE_NAMES.items():
            if normalized in {fleet, service}:
                return service
        if normalized.startswith("temporal-worker-"):
            return candidate
    return None


def build_compose_stale_worker_restarter(
    *,
    project_dir: str | Path,
    compose_file: str | None = None,
    project_name: str = "moonmind",
    excluded_services: Sequence[str] = (),
    idle_grace_seconds: int = 30,
    drain_grace_seconds: int = 300,
    command_timeout_seconds: int = 900,
) -> Callable[
    [Sequence[Mapping[str, Any]]], Awaitable[Mapping[str, Any]]
]:
    """Build a restarter that recovers stale workers via Compose.

    Idle stale workers restart immediately (``docker compose restart -t
    <idle_grace>``); busy ones are drained rather than killed — the longer
    grace lets in-flight activities finish or fail over through Temporal
    retry before the process is replaced
    (MoonLadderStudios/MoonMind#4224). Services in ``excluded_services``
    (notably the deployment-control runner itself) are never restarted
    mid-update; they are reported as ``skipped`` so the update fails loudly
    instead of killing its own runner.
    """

    from moonmind.workflows.temporal.worker_code_identity import (
        plan_stale_worker_recovery,
    )

    excluded = {str(item).strip() for item in excluded_services if str(item).strip()}

    async def _restart(service: str, grace: int) -> tuple[str, str]:
        command = ["docker", "compose"]
        if compose_file:
            command += ["--file", compose_file]
        if project_name:
            command += ["--project-name", project_name]
        command += ["restart", "--timeout", str(grace), service]
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=str(project_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                _, stderr = await asyncio.wait_for(
                    process.communicate(), timeout=command_timeout_seconds
                )
            except asyncio.TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
                await process.wait()
                return service, f"restart timed out after {command_timeout_seconds}s"
            if process.returncode != 0:
                detail = (stderr or b"").decode("utf-8", errors="ignore")[-500:]
                return service, f"restart exited {process.returncode}: {detail}"
        except OSError as exc:
            return service, f"restart failed: {exc}"
        return service, ""

    async def _restart_all(stale: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
        names = [
            str(item.get("worker") or item.get("name") or "").strip() for item in stale
        ]
        busy = {
            str(item.get("worker") or item.get("name") or "").strip()
            for item in stale
            if item.get("busy") is True
        }
        plan = plan_stale_worker_recovery(names, busy=sorted(busy))
        restarted: list[str] = []
        drained: list[str] = []
        failed: list[dict[str, str]] = []
        skipped: list[str] = []

        async def _run_group(
            group: Sequence[str], grace: int, outcome: list[str]
        ) -> None:
            for name in group:
                service = next(
                    (
                        str(item.get("service") or "").strip()
                        for item in stale
                        if str(item.get("worker") or item.get("name") or "").strip()
                        == name
                        and str(item.get("service") or "").strip()
                    ),
                    None,
                ) or _compose_service_for_worker(name) or name
                if not service or "://" in service or "/" in service:
                    # Unnamed readiness targets have no mapped Compose
                    # service: failing loudly beats handing a URL to
                    # ``docker compose restart`` as the service name.
                    failed.append(
                        {
                            "worker": name,
                            "error": (
                                "no restartable Compose service for worker "
                                f"'{name}': configure a service=name=url "
                                "readiness entry"
                            ),
                        }
                    )
                    continue
                if service in excluded:
                    skipped.append(name)
                    continue
                _, error = await _restart(service, grace)
                if error:
                    failed.append({"worker": name, "error": error})
                else:
                    outcome.append(name)

        await _run_group(plan.restart_now, idle_grace_seconds, restarted)
        await _run_group(plan.drain_then_restart, drain_grace_seconds, drained)
        return {
            "restarted": restarted,
            "drained": drained,
            "failed": failed,
            "skipped": skipped,
        }

    return _restart_all


def _add_progress(events: list[dict[str, str]], state: str, message: str) -> None:
    events.append({"state": state, "message": message})


def _verification_final_status(verification: ComposeVerification) -> str:
    explicit = str(verification.status or "").strip().upper()
    if explicit:
        if explicit not in DEPLOYMENT_FINAL_STATUSES:
            raise ToolFailure(
                error_code="DEPLOYMENT_VERIFICATION_INVALID",
                message=f"Unsupported deployment verification status '{explicit}'.",
                retryable=False,
                details={
                    "status": explicit,
                    "failureClass": "verification_failure",
                },
            )
        if verification.succeeded and explicit != "SUCCEEDED":
            raise ToolFailure(
                error_code="DEPLOYMENT_VERIFICATION_INVALID",
                message="Deployment verification status conflicts with success flag.",
                retryable=False,
                details={
                    "status": explicit,
                    "succeeded": verification.succeeded,
                    "failureClass": "verification_failure",
                },
            )
        if explicit == "SUCCEEDED" and not verification.succeeded:
            raise ToolFailure(
                error_code="DEPLOYMENT_VERIFICATION_INVALID",
                message=(
                    "Deployment verification success status conflicts "
                    "with success flag."
                ),
                retryable=False,
                details={
                    "status": explicit,
                    "succeeded": verification.succeeded,
                    "failureClass": "verification_failure",
                },
            )
        return explicit
    return "SUCCEEDED" if verification.succeeded else "FAILED"


def _verification_failure_reason(verification: ComposeVerification) -> str | None:
    details = dict(verification.details)
    for key in ("failureReason", "failure_reason", "message", "reason"):
        value = details.get(key)
        if value:
            return str(value)
    failed_checks = details.get("failedChecks") or details.get("failed_checks")
    if failed_checks:
        return f"Verification checks failed: {failed_checks}"
    if not verification.succeeded:
        return "Deployment verification did not prove desired state."
    return None


def _ensure_runner_survives_update(
    *,
    command_plan: ComposeCommandPlan,
    before_state: Mapping[str, Any],
    target_image: Mapping[str, Any] | None = None,
) -> None:
    if command_plan.runner_mode != "privileged_worker":
        return
    current_container_id = _current_container_id()
    if not current_container_id:
        return
    matching_service = _service_for_container_id(
        before_state.get("services"),
        current_container_id,
    )
    if not matching_service:
        return
    targeted_services = _compose_up_target_services(command_plan.up_args)
    if targeted_services and matching_service not in targeted_services:
        return
    if "--force-recreate" not in command_plan.up_args and _runner_already_uses_target_image(
        before_state=before_state,
        service_name=matching_service,
        target_image=target_image,
    ):
        return
    raise ToolFailure(
        error_code="DEPLOYMENT_RUNNER_UNSAFE",
        message=(
            "Deployment update would recreate the worker container that is "
            "running the update command. Configure an external or ephemeral "
            "deployment updater before running full-stack updates."
        ),
        retryable=False,
        details={
            "runnerMode": command_plan.runner_mode,
            "service": matching_service,
            "failureClass": "runner_self_recreation_unsafe",
        },
    )


def _compose_up_target_services(args: Sequence[str]) -> tuple[str, ...]:
    services: list[str] = []
    passthrough = False
    skip_next = False
    for raw in args[3:]:
        part = str(raw)
        if skip_next:
            skip_next = False
            continue
        if passthrough:
            services.append(part)
            continue
        if part == "--":
            passthrough = True
            continue
        if part.startswith("-"):
            # Option values (for example ``--pull never``) are not services.
            option_name = part.split("=", 1)[0]
            if option_name in _COMPOSE_OPTION_FLAGS_WITH_VALUES and "=" not in part:
                skip_next = True
            continue
        services.append(part)
    return tuple(services)


def _command_plan_targeting_stack_services(
    command_plan: ComposeCommandPlan,
    *,
    before_state: Mapping[str, Any],
    requested_repository: str,
    excluded_services: Sequence[str],
) -> ComposeCommandPlan:
    excluded = _normalized_service_names(excluded_services)
    # Pull only services governed by the requested MoonMind image target.
    pull_candidates = _target_service_names_from_state(
        before_state,
        requested_repository=requested_repository,
    )
    if not pull_candidates and not excluded and not isinstance(
        before_state.get("configuredServiceImages"),
        Mapping,
    ):
        return command_plan
    pull_services = tuple(
        service_name
        for service_name in pull_candidates
        if not _service_is_excluded(service_name, excluded)
    )
    if not pull_services:
        raise ToolFailure(
            error_code="DEPLOYMENT_RUNNER_UNSAFE",
            message=(
                "Deployment update has no target services for the requested "
                "image after applying service exclusions."
            ),
            retryable=False,
            details={
                "excludedServices": sorted(excluded),
                "requestedRepository": requested_repository,
                "failureClass": "runner_self_recreation_unsafe",
            },
        )
    # Compose up must still reconcile the active stack so infrastructure-only
    # configuration, networks, and dependencies required by the new image exist.
    reconciliation_services = tuple(
        service_name
        for service_name in _configured_service_names_from_state(before_state)
        if not _service_is_excluded(service_name, excluded)
    )
    if not reconciliation_services:
        raise ToolFailure(
            error_code="DEPLOYMENT_RUNNER_UNSAFE",
            message=(
                "Deployment update has no configured services to reconcile "
                "after applying service exclusions."
            ),
            retryable=False,
            details={
                "excludedServices": sorted(excluded),
                "failureClass": "runner_self_recreation_unsafe",
            },
        )
    infrastructure_services = tuple(
        service_name
        for service_name in reconciliation_services
        if service_name not in pull_services
    )
    return ComposeCommandPlan(
        runner_mode=command_plan.runner_mode,
        pull_args=(*command_plan.pull_args, *pull_services),
        up_args=(
            *command_plan.up_args,
            "--no-deps",
            *reconciliation_services,
        ),
        missing_pull_args=(
            _missing_image_pull_args(command_plan.pull_args, infrastructure_services)
            if infrastructure_services
            else ()
        ),
    )


def _missing_image_pull_args(
    pull_args: Sequence[str], services: Sequence[str]
) -> tuple[str, ...]:
    """Return ``pull_args`` for ``services`` under the ``missing`` policy."""
    args: list[str] = []
    skip_next = False
    for part in pull_args:
        if skip_next:
            skip_next = False
            continue
        if part == "--policy":
            skip_next = True
            continue
        if part.startswith("--policy="):
            continue
        args.append(part)
    pull_index = args.index("pull") + 1
    return (
        *args[:pull_index],
        "--policy",
        "missing",
        *args[pull_index:],
        *services,
    )


def _substrate_reconciliation_targets(
    *,
    before_state: Mapping[str, Any],
    excluded_services: Sequence[str],
) -> tuple[str, ...]:
    """Excluded services the staged substrate pass still reconciles.

    The main update excludes substrate (docker-proxy, sandbox-egress-proxy,
    postgres, ...) so the controller never recreates its own transport
    mid-update. Those services are still release-owned: when the selected
    release configures them, a final staged pass reconciles them after the
    main stack verifies instead of reporting success on stale substrate.
    The deployment-control runner itself and one-shot services are never
    substrate targets.
    """
    excluded = _normalized_service_names(excluded_services)
    if not excluded:
        return ()
    protected = _normalized_service_names(
        (DEPLOYMENT_CONTROL_SERVICE, *DEPLOYMENT_ONE_SHOT_SERVICES)
    )
    targets: list[str] = []
    for service_name in _configured_service_names_from_state(before_state):
        normalized = str(service_name or "").strip().lower()
        if not normalized or normalized in protected:
            continue
        if _service_is_excluded(service_name, excluded):
            targets.append(str(service_name).strip())
    return tuple(targets)


def _normalize_configured_image(value: Any) -> str:
    """Normalize a configured service image for convergence comparison."""
    text = str(value or "").strip()
    if "@" in text:
        text = text.split("@", 1)[0].strip()
    return text


def _running_service_images(
    state: Mapping[str, Any], service_name: str
) -> tuple[str, ...]:
    """Candidate image references for one running Compose service."""
    services = state.get("services")
    images = state.get("images")
    candidates: list[str] = []
    containers: list[str] = []
    if isinstance(services, Sequence) and not isinstance(services, (str, bytes)):
        for entry in services:
            if not isinstance(entry, Mapping):
                continue
            if str(entry.get("State") or "").strip().lower() != "running":
                continue
            if not _service_name_matches(
                entry.get("Service") or entry.get("Name") or "",
                str(service_name or "").strip().lower(),
            ):
                continue
            image = _normalize_configured_image(entry.get("Image"))
            if image:
                candidates.append(image)
            for key in ("Name", "ID"):
                value = str(entry.get(key) or "").strip()
                if value:
                    containers.append(value)
    if isinstance(images, Sequence) and not isinstance(images, (str, bytes)):
        for image in images:
            if not isinstance(image, Mapping):
                continue
            if containers and str(image.get("ContainerName") or "").strip() not in containers:
                continue
            repository = str(
                image.get("Repository") or image.get("repository") or ""
            ).strip()
            tag = str(image.get("Tag") or image.get("tag") or "").strip()
            if repository and tag:
                candidates.append(f"{repository}:{tag}")
            elif repository:
                candidates.append(repository)
    seen: set[str] = set()
    ordered: list[str] = []
    for candidate in candidates:
        if candidate not in seen:
            seen.add(candidate)
            ordered.append(candidate)
    return tuple(ordered)


def _substrate_service_mismatches(
    *,
    state: Mapping[str, Any],
    targets: Sequence[str],
    expected_images: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Prove each staged substrate service runs its release configuration.

    A target is converged when a running container resolves to the image the
    selected release configures for it; anything else (stopped, missing, or
    a previous image) is a mismatch the release must not report as success.
    """
    mismatches: list[dict[str, Any]] = []
    for target in targets:
        service = str(target or "").strip()
        if not service:
            continue
        running = _running_service_images(state, service)
        expected = None
        if isinstance(expected_images, Mapping):
            for key in (service, service.lower()):
                if key in expected_images:
                    expected = _normalize_configured_image(expected_images[key])
                    break
        if not running:
            mismatches.append(
                {
                    "service": service,
                    "expectedImage": expected,
                    "actualImages": [],
                    "reason": "substrate service is not running",
                }
            )
            continue
        if expected and expected not in running:
            mismatches.append(
                {
                    "service": service,
                    "expectedImage": expected,
                    "actualImages": list(running),
                    "reason": (
                        "substrate service is not running the release image"
                    ),
                }
            )
    return mismatches


def _one_shot_services_from_plan(command_plan: ComposeCommandPlan) -> tuple[str, ...]:
    one_shot_services = {
        service.lower() for service in DEPLOYMENT_ONE_SHOT_SERVICES
    }
    return tuple(
        service
        for service in _compose_up_target_services(command_plan.up_args)
        if service.lower() in one_shot_services
    )


def _command_plan_without_services(
    command_plan: ComposeCommandPlan,
    *,
    excluded_services: Sequence[str],
) -> ComposeCommandPlan:
    excluded = _normalized_service_names(excluded_services)
    if not excluded:
        return command_plan
    target_services = _compose_up_target_services(command_plan.up_args)
    remaining_targets = tuple(
        service
        for service in target_services
        if service.lower() not in excluded
    )
    if target_services and not remaining_targets:
        raise ToolFailure(
            error_code="DEPLOYMENT_RUNNER_UNSAFE",
            message=(
                "Deployment update has no long-running target services after "
                "separating one-shot services."
            ),
            retryable=False,
            details={
                "excludedServices": sorted(excluded),
                "failureClass": "runner_self_recreation_unsafe",
            },
        )
    return ComposeCommandPlan(
        runner_mode=command_plan.runner_mode,
        pull_args=_remove_services_from_command_args(
            command_plan.pull_args,
            excluded_services=excluded,
        ),
        up_args=_remove_services_from_command_args(
            command_plan.up_args,
            excluded_services=excluded,
        ),
        missing_pull_args=command_plan.missing_pull_args,
    )


def _compose_up_args_for_services(
    up_args: Sequence[str],
    services: Sequence[str],
    *,
    one_shot_service: str | None = None,
) -> tuple[str, ...]:
    prefix, _target_services = _split_trailing_service_args(up_args)
    requested_services = tuple(str(service) for service in services)
    if one_shot_service is None:
        return (*prefix, *requested_services)
    return _compose_one_shot_up_args(
        prefix,
        one_shot_service=str(one_shot_service),
    )


def _compose_one_shot_up_args(
    up_prefix_args: Sequence[str],
    *,
    one_shot_service: str,
) -> tuple[str, ...]:
    parts: list[str] = []
    skip_next = False
    for raw_part in up_prefix_args:
        part = str(raw_part)
        if skip_next:
            skip_next = False
            continue
        if part in {"-d", "--detach", "--wait"}:
            continue
        if part == "--wait-timeout":
            skip_next = True
            continue
        if part.startswith("--wait-timeout="):
            continue
        parts.append(part)
    if parts and parts[-1] == "--":
        parts.pop()
    return (*parts, "--exit-code-from", one_shot_service, one_shot_service)


def _attested_gateway_services(before_state: Mapping[str, Any]) -> tuple[str, ...]:
    """Configured services whose policy workers attest at startup.

    Selected from the configured services, never from the exclusion list. The
    documented default excludes only the deployment-control runner, so keying
    this off exclusions made the pre-worker alignment a no-op on exactly the
    supported default configuration.
    """
    from moonmind.security.egress import EGRESS_GATEWAY_SERVICE

    configured = before_state.get("configuredServices")
    if not isinstance(configured, Sequence) or isinstance(configured, (str, bytes)):
        return ()
    return tuple(
        str(service)
        for service in configured
        if _service_name_matches(str(service), EGRESS_GATEWAY_SERVICE)
    )


def _remove_services_from_command_args(
    args: Sequence[str],
    *,
    excluded_services: set[str],
) -> tuple[str, ...]:
    if not excluded_services:
        return tuple(args)
    prefix, trailing_services = _split_trailing_service_args(args)
    return (
        *prefix,
        *(
            service
            for service in trailing_services
            if service.strip().lower() not in excluded_services
        ),
    )


def _split_trailing_service_args(
    args: Sequence[str],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    parts = tuple(str(part) for part in args)
    if len(parts) <= 3:
        return parts, ()
    index = 3
    while index < len(parts):
        part = parts[index]
        if part == "--":
            return parts[: index + 1], parts[index + 1 :]
        if part.startswith("-"):
            option_name = part.split("=", 1)[0]
            index += 1
            if option_name in _COMPOSE_OPTION_FLAGS_WITH_VALUES and "=" not in part:
                index += 1
            continue
        return parts[:index], parts[index:]
    return parts, ()


def _target_service_names_from_state(
    before_state: Mapping[str, Any],
    *,
    requested_repository: str,
) -> tuple[str, ...]:
    configured_image_targets = _target_service_names_from_configured_images(
        before_state=before_state,
        requested_repository=requested_repository,
    )
    if configured_image_targets:
        return configured_image_targets
    if isinstance(before_state.get("configuredServiceImages"), Mapping):
        return ()
    names: list[str] = []
    seen: set[str] = set()
    for source in (
        before_state.get("configuredServices"),
        before_state.get("services"),
    ):
        for name in _service_names_from_state(source):
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            names.append(name)
    return tuple(names)


def _configured_service_names_from_state(
    before_state: Mapping[str, Any],
) -> tuple[str, ...]:
    configured_services = _service_names_from_state(
        before_state.get("configuredServices")
    )
    if configured_services:
        return configured_services
    configured_images = before_state.get("configuredServiceImages")
    if isinstance(configured_images, Mapping):
        return _service_names_from_state(tuple(configured_images))
    return _service_names_from_state(before_state.get("services"))


def _target_service_names_from_configured_images(
    *,
    before_state: Mapping[str, Any],
    requested_repository: str,
) -> tuple[str, ...]:
    requested = str(requested_repository or "").strip().lower()
    if not requested:
        return ()
    configured_images = before_state.get("configuredServiceImages")
    if not isinstance(configured_images, Mapping):
        return ()
    configured_services = _service_names_from_state(
        before_state.get("configuredServices")
    )
    service_order = [
        *configured_services,
        *(
            str(service_name)
            for service_name in configured_images
            if str(service_name) not in configured_services
        ),
    ]
    names: list[str] = []
    seen: set[str] = set()
    for service_name in service_order:
        image = str(configured_images.get(service_name) or "").strip()
        if not image:
            continue
        repository, _reference = _split_requested_image(image)
        if repository.strip().lower() != requested:
            continue
        key = service_name.lower()
        if key in seen:
            continue
        seen.add(key)
        names.append(service_name)
    return tuple(names)


def _service_names_from_state(services: Any) -> tuple[str, ...]:
    if not isinstance(services, Sequence) or isinstance(services, (str, bytes)):
        return ()
    names: list[str] = []
    seen: set[str] = set()
    for service in services:
        if isinstance(service, Mapping):
            name = str(
                service.get("Service")
                or service.get("Name")
                or service.get("Names")
                or ""
            ).strip()
        else:
            name = str(service or "").strip()
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        names.append(name)
    return tuple(names)


def _normalized_service_names(services: Sequence[str]) -> set[str]:
    return {
        str(service or "").strip().lower()
        for service in services
        if str(service or "").strip()
    }


def _service_is_excluded(service_name: str, excluded_services: set[str]) -> bool:
    normalized = str(service_name or "").strip().lower()
    return bool(normalized) and any(
        _service_name_matches(normalized, excluded_service)
        for excluded_service in excluded_services
    )


_OCI_IMAGE_VERSION_LABEL = "org.opencontainers.image.version"


def _target_image_build_id(target_image: Mapping[str, Any] | None) -> str | None:
    """Read the MoonMind build id from the target image's OCI version label.

    The Dockerfile stamps ``org.opencontainers.image.version`` with the same
    ``MOONMIND_BUILD_ID`` used by the workflow-header version badge, so this
    lets the executor preserve the deployed build id without re-deriving it.
    """

    if not isinstance(target_image, Mapping):
        return None
    config = target_image.get("Config")
    if not isinstance(config, Mapping):
        return None
    labels = config.get("Labels")
    if not isinstance(labels, Mapping):
        return None
    value = labels.get(_OCI_IMAGE_VERSION_LABEL)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _target_image_audit(target_image: Mapping[str, Any]) -> dict[str, Any]:
    return _compact_mapping(
        {
            "id": target_image.get("Id") or target_image.get("ID"),
            "repoDigests": target_image.get("RepoDigests"),
            "buildId": _target_image_build_id(target_image),
        }
    )


def _resolved_digest_from_target_image(
    *, repository: str, target_image: Mapping[str, Any] | None
) -> str | None:
    if not target_image:
        return None
    repo_digests = target_image.get("RepoDigests")
    if not isinstance(repo_digests, Sequence) or isinstance(
        repo_digests, (str, bytes, bytearray)
    ):
        return None
    repository_prefix = f"{repository}@"
    for raw_digest in repo_digests:
        digest = str(raw_digest or "").strip()
        if "@sha256:" not in digest:
            continue
        _repo, _separator, digest_value = digest.partition("@")
        if not digest_value:
            continue
        if digest.startswith(repository_prefix):
            return digest_value
    return None


def _runner_already_uses_target_image(
    *,
    before_state: Mapping[str, Any],
    service_name: str,
    target_image: Mapping[str, Any] | None,
) -> bool:
    target_id = _normalized_image_id(
        (target_image or {}).get("Id") or (target_image or {}).get("ID")
    )
    if not target_id:
        return False
    for image in _iter_state_images_for_service(
        before_state.get("images"),
        service_name=service_name,
    ):
        image_id = _normalized_image_id(
            image.get("ID")
            or image.get("Id")
            or image.get("ImageID")
            or image.get("image_id")
        )
        if image_id and image_id == target_id:
            return True
    return False


def _iter_state_images_for_service(
    images: Any,
    *,
    service_name: str,
) -> Sequence[Mapping[str, Any]]:
    if not service_name or not isinstance(images, Sequence) or isinstance(images, (str, bytes)):
        return ()
    normalized_service = service_name.strip().lower()
    matches: list[Mapping[str, Any]] = []
    for image in images:
        if not isinstance(image, Mapping):
            continue
        candidates = (
            image.get("Service"),
            image.get("Name"),
            image.get("Container"),
            image.get("ContainerName"),
            image.get("Names"),
        )
        if any(_service_name_matches(candidate, normalized_service) for candidate in candidates):
            matches.append(image)
    return tuple(matches)


def _service_name_matches(value: Any, normalized_service: str) -> bool:
    candidate = str(value or "").strip().lower()
    if not candidate:
        return False
    return candidate == normalized_service or bool(
        re.search(rf"(^|[-_]){re.escape(normalized_service)}[-_]\d+$", candidate)
    )


def _normalized_image_id(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized.startswith("sha256:"):
        return normalized
    if len(normalized) == 64 and all(character in "0123456789abcdef" for character in normalized):
        return f"sha256:{normalized}"
    return normalized


def _current_container_id() -> str:
    for name in ("MOONMIND_CONTAINER_ID", "HOSTNAME"):
        value = str(os.environ.get(name) or "").strip()
        if value:
            return value
    return ""


def _service_for_container_id(services: Any, container_id: str) -> str:
    needle = container_id.strip().lower()
    if not needle:
        return ""
    if not isinstance(services, Sequence) or isinstance(services, (str, bytes)):
        return ""
    for service in services:
        if not isinstance(service, Mapping):
            continue
        candidate = str(
            service.get("ID")
            or service.get("Id")
            or service.get("ContainerID")
            or service.get("container_id")
            or ""
        ).strip().lower()
        if not candidate:
            continue
        if candidate.startswith(needle) or needle.startswith(candidate):
            return str(
                service.get("Service")
                or service.get("Name")
                or service.get("Names")
                or ""
            ).strip()
    return ""


def _failure_reason(exc: Exception) -> str:
    if isinstance(exc, ToolFailure):
        return exc.message
    return str(exc)


def _terminal_progress_message(status: str) -> str:
    if status == "SUCCEEDED":
        return "Deployment update succeeded."
    if status == "PARTIALLY_VERIFIED":
        return "Deployment update partially verified."
    return "Deployment update failed."


def _compact_mapping(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if value is not None}


def _redact_sensitive(value: Any, key: str | None = None) -> Any:
    if key and _SENSITIVE_KEY_PATTERN.search(key):
        return _REDACTED
    if isinstance(value, Mapping):
        return {str(k): _redact_sensitive(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_sensitive(item, key) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_sensitive(item, key) for item in value)
    if isinstance(value, str):
        return _SENSITIVE_VALUE_PATTERN.sub(_REDACTED, value)
    return value


def build_compose_command_plan(
    *,
    mode: str,
    remove_orphans: bool,
    wait: bool,
    runner_mode: str,
) -> ComposeCommandPlan:
    normalized_mode = _required_string(mode, "mode")
    if normalized_mode not in DEPLOYMENT_UPDATE_MODES:
        raise ToolFailure(
            error_code="INVALID_INPUT",
            message=f"Unsupported deployment update mode '{normalized_mode}'.",
            retryable=False,
            details={"mode": normalized_mode, "failureClass": "invalid_input"},
        )
    normalized_runner = _required_string(runner_mode, "deployment_runner_mode")
    if normalized_runner not in DEPLOYMENT_RUNNER_MODES:
        raise ToolFailure(
            error_code="POLICY_VIOLATION",
            message=f"Unsupported deployment runner mode '{normalized_runner}'.",
            retryable=False,
            details={
                "runner_mode": normalized_runner,
                "failureClass": "policy_violation",
            },
        )

    up_args = ["docker", "compose", "up", "-d", "--pull", "never", "--no-build"]
    if normalized_mode == "force_recreate":
        up_args.append("--force-recreate")
    if remove_orphans:
        up_args.append("--remove-orphans")
    if wait:
        up_args.append("--wait")

    return ComposeCommandPlan(
        runner_mode=normalized_runner,
        pull_args=(
            "docker",
            "compose",
            "pull",
            "--policy",
            "always",
            "--ignore-buildable",
        ),
        up_args=tuple(up_args),
    )


def build_deployment_update_handler(
    executor: DeploymentUpdateExecutor | None = None,
):
    resolved_executor = executor or DeploymentUpdateExecutor(
        lock_manager=DeploymentUpdateLockManager(),
        desired_state_store=InMemoryDesiredStateStore(),
        evidence_writer=InMemoryEvidenceWriter(),
        runner=DisabledComposeRunner(),
    )

    async def _handler(
        inputs: Mapping[str, Any], context: Mapping[str, Any] | None = None
    ) -> ToolResult:
        context = dict(context or {})
        context_executor = None
        candidate = context.get("deployment_update_executor")
        if isinstance(candidate, DeploymentUpdateExecutor):
            context_executor = candidate
        active_executor = context_executor or resolved_executor
        artifact_service = context.get("temporal_artifact_service")
        if artifact_service is not None and context_executor is None:
            active_executor = replace(
                active_executor,
                evidence_writer=TemporalDeploymentEvidenceWriter(
                    artifact_service=artifact_service,
                    principal=str(
                        context.get("deployment_evidence_principal")
                        or "system:deployment"
                    ),
                    execution_ref=_execution_ref_from_context(context),
                ),
            )
        return await active_executor.execute(inputs, context)

    return _handler


def register_deployment_update_tool_handler(
    dispatcher: Any,
    *,
    executor: DeploymentUpdateExecutor | None = None,
) -> None:
    dispatcher.register_skill(
        skill_name=DEPLOYMENT_UPDATE_TOOL_NAME,
        handler=build_deployment_update_handler(executor),
    )


def _parse_inputs(inputs: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(inputs, Mapping):
        raise ToolFailure(
            "INVALID_INPUT",
            "Deployment inputs must be an object.",
            False,
            details={"failureClass": "invalid_input"},
        )
    forbidden = {"command", "composeFile", "hostPath", "updaterRunnerImage"}
    found_forbidden = sorted(forbidden.intersection(inputs.keys()))
    if found_forbidden:
        raise ToolFailure(
            error_code="INVALID_INPUT",
            message="Deployment update inputs contain forbidden fields.",
            retryable=False,
            details={"fields": found_forbidden, "failureClass": "invalid_input"},
        )

    image = inputs.get("image")
    if not isinstance(image, Mapping):
        raise ToolFailure(
            "INVALID_INPUT",
            "Deployment image must be an object.",
            False,
            details={"failureClass": "invalid_input"},
        )

    stack = _required_string(inputs.get("stack"), "stack")
    if stack not in DEPLOYMENT_UPDATE_STACKS:
        raise ToolFailure(
            error_code="INVALID_INPUT",
            message=f"Unsupported deployment stack '{stack}'.",
            retryable=False,
            details={
                "stack": stack,
                "allowed_stacks": sorted(DEPLOYMENT_UPDATE_STACKS),
                "failureClass": "invalid_input",
            },
        )

    parsed = {
        "stack": stack,
        "image": {
            "repository": _required_string(image.get("repository"), "image.repository"),
            "reference": _required_string(image.get("reference"), "image.reference"),
        },
        "mode": str(inputs.get("mode") or "changed_services").strip(),
        "removeOrphans": _optional_bool(inputs, "removeOrphans", default=False),
        "wait": _optional_bool(inputs, "wait", default=True),
        "reason": _optional_string(inputs.get("reason")),
    }
    resolved_digest = image.get("resolvedDigest")
    if resolved_digest is not None and str(resolved_digest).strip():
        parsed["image"]["resolvedDigest"] = str(resolved_digest).strip()
    return parsed


def _requested_image(parsed: Mapping[str, Any]) -> str:
    image = parsed["image"]
    assert isinstance(image, Mapping)
    reference = str(image["reference"])
    separator = "@" if reference.startswith("sha256:") else ":"
    return f"{image['repository']}{separator}{reference}"


def _required_string(value: Any, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ToolFailure(
            error_code="INVALID_INPUT",
            message=f"{field_name} is required.",
            retryable=False,
            details={"field": field_name, "failureClass": "invalid_input"},
        )
    return normalized


def _validate_stack_path_component(value: Any) -> str:
    normalized = _required_string(value, "stack")
    if not _STACK_PATH_COMPONENT_PATTERN.fullmatch(normalized):
        raise ToolFailure(
            error_code="INVALID_STACK_NAME",
            message=f"Invalid stack name '{normalized}'.",
            retryable=False,
            details={"stack": normalized, "failureClass": "invalid_input"},
        )
    return normalized


def _optional_string(value: Any) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def _optional_bool(
    inputs: Mapping[str, Any], field_name: str, *, default: bool
) -> bool:
    if field_name not in inputs:
        return default
    value = inputs[field_name]
    if not isinstance(value, bool):
        raise ToolFailure(
            error_code="INVALID_INPUT",
            message=f"{field_name} must be a boolean.",
            retryable=False,
            details={
                "field": field_name,
                "value_type": type(value).__name__,
                "failureClass": "invalid_input",
            },
        )
    return value


def _ensure_command_succeeded(phase: str, result: Mapping[str, Any]) -> None:
    if not isinstance(result, Mapping):
        raise ToolFailure(
            error_code="DEPLOYMENT_COMMAND_FAILED",
            message=f"Deployment {phase} command returned an invalid result.",
            retryable=False,
            details={
                "phase": phase,
                "result_type": type(result).__name__,
                "failureClass": _command_failure_class(phase),
            },
        )
    for key in ("exitCode", "exit_code", "returncode"):
        if key in result:
            try:
                code = int(result[key])
            except (TypeError, ValueError) as exc:
                raise ToolFailure(
                    error_code="DEPLOYMENT_COMMAND_FAILED",
                    message=(
                        f"Deployment {phase} command returned a non-numeric exit code."
                    ),
                    retryable=False,
                    details={
                        "phase": phase,
                        "field": key,
                        "value": result[key],
                        "failureClass": _command_failure_class(phase),
                    },
                ) from exc
            if code != 0:
                raise ToolFailure(
                    error_code="DEPLOYMENT_COMMAND_FAILED",
                    message=f"Deployment {phase} command failed with exit code {code}.",
                    retryable=False,
                    details={
                        "phase": phase,
                        "exit_code": code,
                        "result": dict(result),
                        "failureClass": _command_failure_class(phase),
                    },
                )
            return
    for key in ("ok", "success", "succeeded"):
        if key in result:
            if result[key] is not True:
                raise ToolFailure(
                    error_code="DEPLOYMENT_COMMAND_FAILED",
                    message=f"Deployment {phase} command reported failure.",
                    retryable=False,
                    details={
                        "phase": phase,
                        "field": key,
                        "result": dict(result),
                        "failureClass": _command_failure_class(phase),
                    },
                )
            return
    status = str(result.get("status") or "").strip().lower()
    if status:
        if status not in {"completed", "succeeded", "success", "ok"}:
            raise ToolFailure(
                error_code="DEPLOYMENT_COMMAND_FAILED",
                message=f"Deployment {phase} command reported status '{status}'.",
                retryable=False,
                details={
                    "phase": phase,
                    "status": status,
                    "result": dict(result),
                    "failureClass": _command_failure_class(phase),
                },
            )
        return
    raise ToolFailure(
        error_code="DEPLOYMENT_COMMAND_FAILED",
        message=f"Deployment {phase} command result did not include a success signal.",
        retryable=False,
        details={
            "phase": phase,
            "result": dict(result),
            "failureClass": _command_failure_class(phase),
        },
    )


def _command_failure_class(phase: str) -> str:
    if phase == "pull":
        return "image_pull_failure"
    if phase == "up":
        return "service_recreation_failure"
    if phase.startswith("one-shot service "):
        return "one_shot_service_failure"
    return "compose_config_validation_failure"


def _record_command_exception(command_log: dict[str, Any], exc: Exception) -> None:
    if "error" in command_log:
        return
    if isinstance(exc, ToolFailure):
        command_log["error"] = exc.to_payload()
    else:
        command_log["error"] = {
            "error_code": "DEPLOYMENT_COMMAND_EXCEPTION",
            "message": str(exc),
            "retryable": False,
            "details": {"type": type(exc).__name__},
        }


def _parse_json_records(payload: str) -> list[Mapping[str, Any]]:
    text = str(payload or "").strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        records: list[Mapping[str, Any]] = []
        for line in text.splitlines():
            candidate = line.strip()
            if not candidate:
                continue
            parsed_line = json.loads(candidate)
            if isinstance(parsed_line, Mapping):
                records.append(dict(parsed_line))
        return records
    if isinstance(parsed, list):
        return [dict(item) for item in parsed if isinstance(item, Mapping)]
    if isinstance(parsed, Mapping):
        return [dict(parsed)]
    return []


def _split_requested_image(requested_image: str) -> tuple[str, str]:
    if "@" in requested_image:
        repository, reference = requested_image.rsplit("@", 1)
        return repository, reference
    repository, separator, reference = requested_image.rpartition(":")
    if not separator or "/" not in repository:
        return requested_image, ""
    return repository, reference


def _desired_requested_image(payload: Mapping[str, Any]) -> str:
    repository = _required_string(payload.get("imageRepository"), "imageRepository")
    reference = _required_string(payload.get("requestedReference"), "requestedReference")
    separator = "@" if reference.startswith("sha256:") else ":"
    return f"{repository}{separator}{reference}"


def _desired_deployed_image(payload: Mapping[str, Any]) -> str:
    repository = _required_string(payload.get("imageRepository"), "imageRepository")
    resolved_digest = str(payload.get("resolvedDigest") or "").strip()
    if resolved_digest:
        return f"{repository}@{resolved_digest}"
    return _desired_requested_image(payload)


def _env_value(value: Any) -> str:
    text = str(value or "").strip()
    return text.replace("\r", "").replace("\n", " ")


def _compose_env_value(value: Any) -> str:
    return _env_value(value).replace("\\", "\\\\").replace('"', '\\"')


def _normalize_deployment_state_file_permissions(path: Path) -> None:
    with contextlib.suppress(OSError):
        parent_stat = path.parent.stat()
        geteuid = getattr(os, "geteuid", lambda: -1)
        if hasattr(os, "chown") and geteuid() == 0:
            os.chown(path, parent_stat.st_uid, parent_stat.st_gid)
    with contextlib.suppress(OSError):
        path.chmod(0o664)


def _atomic_write_bytes(
    path: Path,
    payload: bytes,
    *,
    normalize_permissions: bool = False,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        if normalize_permissions:
            _normalize_deployment_state_file_permissions(path)
        _fsync_parent_directory(path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temp_path.unlink()


def _fsync_parent_directory(path: Path) -> None:
    if hasattr(os, "O_DIRECTORY"):
        with contextlib.suppress(OSError):
            dir_fd = os.open(str(path.parent), os.O_DIRECTORY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)


def _write_desired_state_files(
    env_path: Path,
    json_path: Path,
    env_payload: Mapping[str, Any],
    record: Mapping[str, Any],
) -> None:
    env_text = "".join(
        f'{key}="{_compose_env_value(value)}"\n'
        for key, value in env_payload.items()
        if _env_value(value)
    )
    _atomic_write_bytes(
        env_path,
        env_text.encode("utf-8"),
        normalize_permissions=True,
    )
    json_text = json.dumps(record, sort_keys=True, default=str, indent=2) + "\n"
    _atomic_write_bytes(
        json_path,
        json_text.encode("utf-8"),
        normalize_permissions=True,
    )


_ENV_LINE_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=\"(.*)\"$")


def _unescape_desired_state_env_value(text: str) -> str:
    """Invert :func:`_compose_env_value` for values this store wrote."""
    out: list[str] = []
    escaped = False
    for char in text:
        if escaped:
            out.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        else:
            out.append(char)
    if escaped:
        out.append("\\")
    return "".join(out)


def _parse_desired_state_env(
    text: str,
) -> tuple[list[tuple[str, str]], list[str]]:
    """Split desired-state env text into entries and preserved lines.

    Returns ``(entries, preserved)`` where entries are ``(key, value)``
    pairs in file order (last duplicate wins) and preserved holds every
    line this store did not author (comments, blanks, unparseable lines),
    kept verbatim so merges never drop operator content.
    """
    entries: list[tuple[str, str]] = []
    seen: dict[str, int] = {}
    preserved: list[str] = []
    for line in text.splitlines():
        match = _ENV_LINE_RE.fullmatch(line.strip())
        if match is None:
            preserved.append(line)
            continue
        key = match.group(1)
        value = _unescape_desired_state_env_value(match.group(2))
        if key in seen:
            entries[seen[key]] = (key, value)
        else:
            seen[key] = len(entries)
            entries.append((key, value))
    return entries, preserved


def _read_desired_state_files(
    env_path: Path,
    json_path: Path,
) -> tuple[dict[str, str], dict[str, Any]]:
    """Read desired-state files tolerantly; missing files read as empty."""
    try:
        env_text = env_path.expanduser().read_text(encoding="utf-8")
    except OSError:
        env_text = ""
    entries, _preserved = _parse_desired_state_env(env_text)
    try:
        record_text = json_path.expanduser().read_text(encoding="utf-8")
    except OSError:
        return dict(entries), {}
    try:
        record = json.loads(record_text)
    except ValueError:
        return dict(entries), {}
    if not isinstance(record, dict):
        return dict(entries), {}
    return dict(entries), record


def _merge_desired_state_files(
    env_path: Path,
    json_path: Path,
    env_updates: Mapping[str, Any],
    json_updates: Mapping[str, Any],
) -> None:
    """Merge updates into desired-state files, preserving other entries."""
    try:
        env_text = env_path.expanduser().read_text(encoding="utf-8")
    except OSError:
        env_text = ""
    entries, preserved = _parse_desired_state_env(env_text)
    merged = dict(entries)
    for key, value in env_updates.items():
        text = _env_value(value)
        if text:
            merged[str(key)] = text
        else:
            merged.pop(str(key), None)
    ordered = [(key, merged[key]) for key, _ in entries if key in merged]
    ordered.extend(
        (str(key), merged[str(key)])
        for key in env_updates
        if _env_value(env_updates[key]) and str(key) not in dict(entries)
    )
    lines = [f'{key}="{_compose_env_value(value)}"\n' for key, value in ordered]
    lines.extend(
        line + "\n" for line in preserved if line.strip()
    )
    _atomic_write_bytes(
        env_path.expanduser(),
        "".join(lines).encode("utf-8"),
        normalize_permissions=True,
    )
    _existing_env, record = _read_desired_state_files(env_path, json_path)
    record = {**record, **{str(k): v for k, v in json_updates.items()}}
    json_text = json.dumps(record, sort_keys=True, default=str, indent=2) + "\n"
    _atomic_write_bytes(
        json_path.expanduser(),
        json_text.encode("utf-8"),
        normalize_permissions=True,
    )


def _execution_ref_from_context(context: Mapping[str, Any]) -> Mapping[str, Any] | None:
    workflow_id = str(context.get("workflow_id") or "").strip()
    run_id = str(context.get("run_id") or "").strip()
    if not workflow_id or not run_id:
        return None
    return {
        "namespace": str(context.get("namespace") or "default"),
        "workflow_id": workflow_id,
        "run_id": run_id,
        "link_type": "deployment.evidence",
        "label": "deployment update evidence",
    }


def _stable_ref(kind: str, payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    digest = hashlib.sha256(kind.encode("utf-8") + b"\0" + encoded).hexdigest()
    return f"art:sha256:{digest}"


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


__all__ = [
    "ComposeCommandPlan",
    "ComposeVerification",
    "DeploymentUpdateExecutor",
    "DeploymentUpdateLockManager",
    "DEPLOYMENT_RUNNER_MODES",
    "DEPLOYMENT_UPDATE_STACKS",
    "DEPLOYMENT_UPDATE_MODES",
    "DisabledComposeRunner",
    "FileDeploymentUpdateLockManager",
    "FileDesiredStateStore",
    "HostDockerComposeRunner",
    "InMemoryDesiredStateStore",
    "InMemoryEvidenceWriter",
    "TemporalDeploymentEvidenceWriter",
    "build_compose_command_plan",
    "build_compose_stale_worker_restarter",
    "build_deployment_update_handler",
    "build_env_stale_worker_checker",
    "register_deployment_update_tool_handler",
]
