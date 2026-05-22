"""Execution lifecycle for the deployment update tool."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from .deployment_tools import (
    DEPLOYMENT_UPDATE_TOOL_NAME,
    DEPLOYMENT_UPDATE_TOOL_VERSION,
)
from .tool_plan_contracts import ToolFailure, ToolResult

DEPLOYMENT_RUNNER_MODES = frozenset(
    {"privileged_worker", "ephemeral_updater_container"}
)
DEPLOYMENT_UPDATE_MODES = frozenset({"changed_services", "force_recreate"})
DEPLOYMENT_UPDATE_STACKS = frozenset({"moonmind"})
DEPLOYMENT_FINAL_STATUSES = frozenset({"SUCCEEDED", "FAILED", "PARTIALLY_VERIFIED"})
FILE_LOCK_STALE_AFTER_SECONDS = 6 * 60 * 60
_REDACTED = "[REDACTED]"
_STACK_PATH_COMPONENT_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")
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


class DeploymentUpdateLockManager:
    """Nonblocking per-stack lock manager for deployment updates."""

    def __init__(self) -> None:
        self._guard = asyncio.Lock()
        self._held: set[str] = set()

    async def acquire(self, stack: str) -> "DeploymentUpdateLockLease":
        normalized = _required_string(stack, "stack")
        async with self._guard:
            if normalized in self._held:
                raise ToolFailure(
                    error_code="DEPLOYMENT_LOCKED",
                    message=(
                        "Deployment update for stack "
                        f"'{normalized}' is already running."
                    ),
                    retryable=False,
                    details={
                        "stack": normalized,
                        "failureClass": "deployment_lock_unavailable",
                    },
                )
            self._held.add(normalized)
        return DeploymentUpdateLockLease(self, normalized)

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
    """Atomic per-stack lock manager backed by an allowlisted filesystem path."""

    lock_dir: str

    async def acquire(self, stack: str) -> "FileDeploymentUpdateLockLease":
        normalized = _validate_stack_path_component(stack)
        lock_path = Path(self.lock_dir).expanduser() / f"{normalized}.lock"
        try:
            await asyncio.to_thread(_create_lock_file, lock_path, normalized)
        except FileExistsError as exc:
            recovered = await asyncio.to_thread(
                _recover_stale_lock_file,
                lock_path,
                normalized,
            )
            if recovered:
                try:
                    await asyncio.to_thread(_create_lock_file, lock_path, normalized)
                except FileExistsError:
                    pass
                else:
                    return FileDeploymentUpdateLockLease(lock_path)
            raise ToolFailure(
                error_code="DEPLOYMENT_LOCKED",
                message=(
                    "Deployment update for stack "
                    f"'{normalized}' is already running."
                ),
                retryable=False,
                details={
                    "stack": normalized,
                    "lockPath": str(lock_path),
                    "failureClass": "deployment_lock_unavailable",
                },
            ) from exc
        return FileDeploymentUpdateLockLease(lock_path)


@dataclass(frozen=True, slots=True)
class FileDeploymentUpdateLockLease:
    lock_path: Path

    async def release(self) -> None:
        await asyncio.to_thread(_unlink_lock_file, self.lock_path)

    async def __aenter__(self) -> "FileDeploymentUpdateLockLease":
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
        desired_image = _desired_deployed_image(record)
        requested_image = _desired_requested_image(record)
        run_id = str(record.get("sourceRunId") or "").strip()
        env_payload = {
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
    command_timeout_seconds: int = 900
    local_project_dir: str | None = None
    env_file: str | None = None

    async def capture_state(self, *, stack: str, phase: str) -> Mapping[str, Any]:
        services = await self._run_compose_json(("ps", "--format", "json"))
        images = await self._run_compose_json(("images", "--format", "json"))
        return {
            "stack": stack,
            "phase": phase,
            "projectName": self.project_name,
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
        matched_images = [
            image
            for image in images
            if isinstance(image, Mapping)
            and str(image.get("Repository") or image.get("repository") or "").strip()
            == repository
        ]
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
            if target_id and image_id and image_id != target_id:
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

    def _host_dir(self) -> Path:
        return Path(self.project_dir).expanduser()

    def _compose_base_command(self) -> list[str]:
        host_dir = self._host_dir()
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
        if self.env_file:
            env_file = Path(self.env_file).expanduser()
            if env_file.exists():
                command.extend(["--env-file", str(env_file)])
        return command

    def _compose_command(self, command: Sequence[str]) -> list[str]:
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
        return [*self._compose_base_command(), *parts[2:]]

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
        self._ensure_host_project_read_alias()
        resolved = self._compose_command(command)
        env = os.environ.copy()
        if requested_image and not self.env_file:
            env["MOONMIND_IMAGE"] = requested_image
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

    async def execute(
        self,
        inputs: Mapping[str, Any],
        context: Mapping[str, Any] | None = None,
    ) -> ToolResult:
        context = dict(context or {})
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
        verification: ComposeVerification | None = None
        final_status: str | None = None
        failure_reason: str | None = None
        command_log: dict[str, Any] = {
            "runnerMode": command_plan.runner_mode,
            "pull": {"command": list(command_plan.pull_args)},
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
        async with await self.lock_manager.acquire(parsed["stack"]):
            try:
                _add_progress(
                    progress_events,
                    "CAPTURING_BEFORE_STATE",
                    "Capturing current deployment state.",
                )
                before_state = await self.runner.capture_state(
                    stack=parsed["stack"], phase="before"
                )
                before_ref = await write_evidence("before-state", before_state)

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
                await self.desired_state_store.persist(desired_payload)

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
                target_image = await self.runner.inspect_image(requested_image)
                command_log["targetImage"] = _target_image_audit(target_image)
                _ensure_runner_survives_update(
                    command_plan=command_plan,
                    before_state=before_state,
                    target_image=target_image,
                )

                _add_progress(
                    progress_events,
                    "RECREATING_SERVICES",
                    "Recreating deployment services.",
                )
                up_result = await self.runner.up(
                    stack=parsed["stack"],
                    command=command_plan.up_args,
                    requested_image=requested_image,
                )
                command_log["up"]["result"] = up_result
                _ensure_command_succeeded("up", up_result)
                command_ref = await write_evidence("command-log", command_log)

                _add_progress(progress_events, "VERIFYING", "Verifying deployed state.")
                verification = await self.runner.verify(
                    stack=parsed["stack"],
                    requested_image=requested_image,
                    resolved_digest=resolved_digest,
                )
                final_status = _verification_final_status(verification)
                if final_status != "SUCCEEDED":
                    failure_reason = _verification_failure_reason(verification)
                verification_ref = await write_evidence(
                    "verification",
                    {
                        "succeeded": verification.succeeded,
                        "status": final_status,
                        "details": dict(verification.details),
                        "requestedImage": requested_image,
                        "resolvedDigest": resolved_digest,
                    },
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
    for raw in args[3:]:
        part = str(raw)
        if passthrough:
            services.append(part)
            continue
        if part == "--":
            passthrough = True
            continue
        if part.startswith("-"):
            continue
        services.append(part)
    return tuple(services)


def _target_image_audit(target_image: Mapping[str, Any]) -> dict[str, Any]:
    return _compact_mapping(
        {
            "id": target_image.get("Id") or target_image.get("ID"),
            "repoDigests": target_image.get("RepoDigests"),
        }
    )


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

    up_args = ["docker", "compose", "up", "-d"]
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
        version=DEPLOYMENT_UPDATE_TOOL_VERSION,
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


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
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
        _fsync_parent_directory(path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temp_path.unlink()


def _recover_stale_lock_file(lock_path: Path, stack: str) -> bool:
    if not lock_path.exists():
        return False
    try:
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _unlink_lock_file_if_older_than_lease(lock_path)
    if not isinstance(payload, Mapping):
        return _unlink_lock_file_if_older_than_lease(lock_path)

    lock_stack = str(payload.get("stack") or "").strip()
    if lock_stack and lock_stack != stack:
        return False

    pid = _lock_file_pid(payload)
    if pid is not None and not _pid_is_live(pid):
        _unlink_lock_file(lock_path)
        return True
    if _lock_file_is_older_than_lease(payload):
        _unlink_lock_file(lock_path)
        return True
    return False


def _unlink_lock_file_if_older_than_lease(lock_path: Path) -> bool:
    try:
        age_seconds = datetime.now(UTC).timestamp() - lock_path.stat().st_mtime
    except OSError:
        return False
    if age_seconds <= FILE_LOCK_STALE_AFTER_SECONDS:
        return False
    _unlink_lock_file(lock_path)
    return True


def _lock_file_pid(payload: Mapping[str, Any]) -> int | None:
    try:
        pid = int(str(payload.get("pid") or "").strip())
    except ValueError:
        return None
    return pid if pid > 0 else None


def _pid_is_live(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return True
    return True


def _lock_file_is_older_than_lease(payload: Mapping[str, Any]) -> bool:
    created_at = str(payload.get("createdAt") or "").strip()
    if not created_at:
        return False
    with contextlib.suppress(ValueError):
        parsed = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        age_seconds = (datetime.now(UTC) - parsed.astimezone(UTC)).total_seconds()
        return age_seconds > FILE_LOCK_STALE_AFTER_SECONDS
    return False


def _fsync_parent_directory(path: Path) -> None:
    if hasattr(os, "O_DIRECTORY"):
        with contextlib.suppress(OSError):
            dir_fd = os.open(str(path.parent), os.O_DIRECTORY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)


def _create_lock_file(lock_path: Path, stack: str) -> None:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "stack": stack,
        "pid": os.getpid(),
        "createdAt": _utc_now(),
    }
    encoded = (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8")
    fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        _fsync_parent_directory(lock_path)
    except Exception:
        with contextlib.suppress(FileNotFoundError):
            lock_path.unlink()
        raise


def _unlink_lock_file(lock_path: Path) -> None:
    with contextlib.suppress(FileNotFoundError):
        lock_path.unlink()
    _fsync_parent_directory(lock_path)


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
    _atomic_write_bytes(env_path, env_text.encode("utf-8"))
    json_text = json.dumps(record, sort_keys=True, default=str, indent=2) + "\n"
    _atomic_write_bytes(json_path, json_text.encode("utf-8"))


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
    "build_deployment_update_handler",
    "register_deployment_update_tool_handler",
]
