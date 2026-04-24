"""Docker-backed workload launcher for control-plane owned workload containers."""

from __future__ import annotations

import asyncio
import json
import os
import posixpath
import shlex
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Mapping, Protocol, Sequence

from moonmind.schemas.workload_models import (
    RunnerProfile,
    UnrestrictedContainerRequest,
    UnrestrictedDockerRequest,
    ValidatedWorkloadRequest,
    WorkloadMount,
    WorkloadResourceOverrides,
    WorkloadResult,
)
from moonmind.utils.logging import redact_sensitive_payload, redact_sensitive_text

_MAX_CAPTURED_STREAM_CHARS = 64_000
_MAX_CAPTURED_STREAM_BYTES = 64_000
_DEFAULT_TIMEOUT_SECONDS = 300
_DEFAULT_KILL_GRACE_SECONDS = 30
_UNRESTRICTED_RUNNER_PROFILE = RunnerProfile.model_validate(
    {
        "id": "unrestricted",
        "kind": "one_shot",
        "image": "busybox:1.0",
        "workdirTemplate": "/tmp",
        "requiredMounts": [
            {
                "type": "volume",
                "source": "tmp",
                "target": "/tmp",
            }
        ],
        "envAllowlist": [],
        "networkPolicy": "none",
        "resources": {},
        "timeoutSeconds": _DEFAULT_TIMEOUT_SECONDS,
    }
)

class DockerWorkloadLauncherError(RuntimeError):
    """Raised when the Docker workload launcher cannot execute a request."""

class _DockerMount(Protocol):
    type: str
    source: str
    target: str
    read_only: bool

class _ConcurrencyLease:
    def __init__(
        self,
        limiter: "DockerWorkloadConcurrencyLimiter",
        profile_id: str,
    ) -> None:
        self._limiter = limiter
        self._profile_id = profile_id
        self._released = False

    async def release(self) -> None:
        if self._released:
            return
        self._released = True
        await self._limiter.release(self._profile_id)

class DockerWorkloadConcurrencyLimiter:
    """Fail-fast in-process concurrency guard for Docker workload launches."""

    def __init__(self, *, fleet_limit: int | None = None) -> None:
        if fleet_limit is not None and fleet_limit < 1:
            raise ValueError("fleet_limit must be positive")
        self._fleet_limit = fleet_limit
        self._active_total = 0
        self._active_by_profile: dict[str, int] = {}
        self._lock = asyncio.Lock()

    async def acquire(
        self,
        request: ValidatedWorkloadRequest,
    ) -> _ConcurrencyLease:
        profile_id = request.profile.id if request.profile is not None else request.request.tool_name
        max_concurrency = (
            request.profile.max_concurrency if request.profile is not None else None
        )
        async with self._lock:
            active_for_profile = self._active_by_profile.get(profile_id, 0)
            if max_concurrency is not None and active_for_profile >= max_concurrency:
                raise DockerWorkloadLauncherError(
                    "workload concurrency limit exceeded for profile "
                    f"{profile_id}"
                )
            if self._fleet_limit is not None and self._active_total >= self._fleet_limit:
                raise DockerWorkloadLauncherError(
                    "workload concurrency limit exceeded for docker_workload fleet"
                )
            self._active_total += 1
            self._active_by_profile[profile_id] = active_for_profile + 1
        return _ConcurrencyLease(self, profile_id)

    async def release(self, profile_id: str) -> None:
        async with self._lock:
            active_for_profile = self._active_by_profile.get(profile_id, 0)
            if active_for_profile <= 1:
                self._active_by_profile.pop(profile_id, None)
            else:
                self._active_by_profile[profile_id] = active_for_profile - 1
            if self._active_total > 0:
                self._active_total -= 1

def _decode_stream(data: bytes) -> str:
    text = data.decode("utf-8", errors="replace")
    if len(text) <= _MAX_CAPTURED_STREAM_CHARS:
        return text
    return text[-_MAX_CAPTURED_STREAM_CHARS:]

def _append_limited(buffer: bytearray, chunk: bytes) -> None:
    buffer.extend(chunk)
    overflow = len(buffer) - _MAX_CAPTURED_STREAM_BYTES
    if overflow > 0:
        del buffer[:overflow]

async def _read_limited_stream(
    stream: asyncio.StreamReader | None,
    buffer: bytearray,
) -> None:
    if stream is None:
        return
    while True:
        chunk = await stream.read(8192)
        if not chunk:
            return
        _append_limited(buffer, chunk)

async def _wait_with_limited_output(
    process: asyncio.subprocess.Process,
    *,
    stdout_buffer: bytearray,
    stderr_buffer: bytearray,
) -> int:
    await asyncio.gather(
        _read_limited_stream(process.stdout, stdout_buffer),
        _read_limited_stream(process.stderr, stderr_buffer),
    )
    return await process.wait()

async def _kill_and_reap_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is None:
        process.kill()
    await process.wait()

def _docker_env(*, docker_host: str | None = None) -> dict[str, str]:
    env = dict(os.environ)
    if docker_host:
        env["DOCKER_HOST"] = docker_host
    return env

def _mount_arg(mount: _DockerMount) -> str:
    parts = [
        f"type={mount.type}",
        f"source={mount.source}",
        f"target={mount.target}",
    ]
    if mount.read_only:
        parts.append("readonly")
    return ",".join(parts)

def _effective_resources(
    *,
    profile: RunnerProfile,
    overrides: WorkloadResourceOverrides,
) -> dict[str, str]:
    resources: dict[str, str] = {}
    cpu = overrides.cpu or profile.resources.cpu
    memory = overrides.memory or profile.resources.memory
    shm_size = overrides.shm_size or profile.resources.shm_size
    if cpu:
        resources["--cpus"] = cpu
    if memory:
        resources["--memory"] = memory
    if shm_size:
        resources["--shm-size"] = shm_size
    return resources

def _workload_command_args(
    *,
    command_wrapper: Sequence[str],
    workload_command: Sequence[str],
) -> list[str]:
    if command_wrapper and command_wrapper[-1] in {"-c", "-lc"}:
        if len(workload_command) == 1:
            return [workload_command[0]]
        return [shlex.join(workload_command)]
    return list(workload_command)

def _path_is_under_mount(path: str, mounts: Sequence[WorkloadMount]) -> bool:
    normalized = posixpath.normpath(path)
    for mount in mounts:
        target = posixpath.normpath(mount.target)
        if normalized == target or normalized.startswith(f"{target}/"):
            return True
    return False

def _isoformat(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")

def _parse_iso_datetime(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)

def _session_context(request: ValidatedWorkloadRequest) -> dict[str, object] | None:
    workload = request.request
    if workload.session_id is None:
        return None
    context: dict[str, object] = {"sessionId": workload.session_id}
    if workload.session_epoch is not None:
        context["sessionEpoch"] = workload.session_epoch
    if workload.source_turn_id is not None:
        context["sourceTurnId"] = workload.source_turn_id
    return context

def _operational_labels(request: ValidatedWorkloadRequest) -> dict[str, str]:
    if request.profile is not None and request.profile.kind == "bounded_service":
        ttl_seconds = (
            request.request.ttl_seconds
            or request.profile.helper_ttl_seconds
            or request.profile.timeout_seconds
        )
        expires_at = datetime.now(UTC) + timedelta(seconds=ttl_seconds)
        return {
            **request.ownership.labels,
            "moonmind.expires_at": _isoformat(expires_at) or "",
            "moonmind.helper_ttl_seconds": str(ttl_seconds),
        }
    timeout_seconds = _request_timeout_seconds(request)
    kill_grace_seconds = _request_kill_grace_seconds(request)
    expires_at = datetime.now(UTC) + timedelta(seconds=timeout_seconds + kill_grace_seconds)
    return {
        **request.ownership.labels,
        "moonmind.expires_at": _isoformat(expires_at) or "",
    }

def _workload_metadata(
    request: ValidatedWorkloadRequest,
    *,
    status: str,
    exit_code: int | None,
    started_at: datetime,
    completed_at: datetime,
    duration_seconds: float,
    timeout_reason: str | None,
) -> dict[str, object]:
    image_ref = request.profile.image if request.profile is not None else getattr(request.request, "image", None)
    profile_id = request.profile.id if request.profile is not None else None
    return {
        "taskRunId": request.request.task_run_id,
        "stepId": request.request.step_id,
        "attempt": request.request.attempt,
        "toolName": request.request.tool_name,
        "profileId": profile_id,
        "workflowDockerMode": request.ownership.workflow_docker_mode,
        "imageRef": image_ref,
        "containerName": request.container_name,
        "identityKind": request.ownership.kind,
        "status": status,
        "exitCode": exit_code,
        "startedAt": _isoformat(started_at),
        "completedAt": _isoformat(completed_at),
        "durationSeconds": duration_seconds,
        "timeoutReason": timeout_reason,
        "labels": dict(request.ownership.labels),
        "artifactsDir": request.request.artifacts_dir,
        "sessionContext": _session_context(request),
    }

def _helper_metadata(
    request: ValidatedWorkloadRequest,
    *,
    status: str,
    started_at: datetime,
    completed_at: datetime,
    duration_seconds: float,
    readiness: Mapping[str, object] | None = None,
    teardown: Mapping[str, object] | None = None,
) -> dict[str, object]:
    ttl_seconds = request.request.ttl_seconds or request.profile.helper_ttl_seconds
    return {
        "taskRunId": request.request.task_run_id,
        "stepId": request.request.step_id,
        "attempt": request.request.attempt,
        "toolName": request.request.tool_name,
        "profileId": request.profile.id,
        "workflowDockerMode": request.ownership.workflow_docker_mode,
        "imageRef": request.profile.image,
        "containerName": request.container_name,
        "identityKind": request.ownership.kind,
        "status": status,
        "startedAt": _isoformat(started_at),
        "completedAt": _isoformat(completed_at),
        "durationSeconds": duration_seconds,
        "ttlSeconds": ttl_seconds,
        "labels": dict(request.ownership.labels),
        "artifactsDir": request.request.artifacts_dir,
        "sessionContext": _session_context(request),
        "readiness": dict(readiness or {}),
        "teardown": dict(teardown or {}),
    }

def _write_text_artifact(path: Path, payload: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")
    return str(path)

def _declared_output_refs(
    request: ValidatedWorkloadRequest,
) -> tuple[dict[str, str], dict[str, str]]:
    artifact_root = Path(request.request.artifacts_dir)
    refs: dict[str, str] = {}
    missing: dict[str, str] = {}
    for artifact_class, relative_path in request.request.declared_outputs.items():
        output_path = (artifact_root / relative_path).resolve()
        try:
            output_path.relative_to(artifact_root.resolve())
        except ValueError:
            missing[artifact_class] = relative_path
            continue
        if output_path.is_file():
            refs[artifact_class] = str(output_path)
        else:
            missing[artifact_class] = relative_path
    return refs, missing

def _publish_workload_artifacts(
    request: ValidatedWorkloadRequest,
    *,
    stdout: str,
    stderr: str,
    diagnostics: Mapping[str, object],
    declared_output_refs: Mapping[str, str],
) -> tuple[str | None, str | None, str | None, dict[str, str], dict[str, object]]:
    artifact_root = Path(request.request.artifacts_dir)
    workload_root = artifact_root / "workload" / request.container_name
    errors: dict[str, str] = {}

    def _write(class_name: str, path: Path, payload: str) -> str | None:
        try:
            return _write_text_artifact(path, payload)
        except OSError as exc:
            errors[class_name] = str(exc)
            return None

    sanitized_stdout = redact_sensitive_text(stdout)
    sanitized_stderr = redact_sensitive_text(stderr)
    stdout_ref = _write(
        "runtime.stdout",
        workload_root / "runtime.stdout.log",
        sanitized_stdout,
    )
    stderr_ref = _write(
        "runtime.stderr",
        workload_root / "runtime.stderr.log",
        sanitized_stderr,
    )
    diagnostics_payload = redact_sensitive_payload(diagnostics)
    diagnostics_payload["artifactPublication"] = (
        {
            "status": "failed",
            "error": next(iter(errors.values())),
            "errors": dict(errors),
        }
        if errors
        else {"status": "complete"}
    )
    diagnostics_ref = _write(
        "runtime.diagnostics",
        workload_root / "runtime.diagnostics.json",
        json.dumps(diagnostics_payload, sort_keys=True, indent=2) + "\n",
    )
    output_refs: dict[str, str] = {}
    if stdout_ref is not None:
        output_refs["runtime.stdout"] = stdout_ref
        output_refs["output.logs"] = stdout_ref
    if stderr_ref is not None:
        output_refs["runtime.stderr"] = stderr_ref
    if diagnostics_ref is not None:
        output_refs["runtime.diagnostics"] = diagnostics_ref
    output_refs.update(declared_output_refs)
    publication: dict[str, object]
    if errors:
        publication = {
            "status": "failed",
            "error": next(iter(errors.values())),
            "errors": errors,
        }
    else:
        publication = {"status": "complete"}
    return stdout_ref, stderr_ref, diagnostics_ref, output_refs, publication

def _request_timeout_seconds(request: ValidatedWorkloadRequest) -> int | float:
    return request.request.timeout_seconds or (
        request.profile.timeout_seconds
        if request.profile is not None
        else _DEFAULT_TIMEOUT_SECONDS
    )

def _request_kill_grace_seconds(request: ValidatedWorkloadRequest) -> int:
    return (
        request.profile.cleanup.kill_grace_seconds
        if request.profile is not None
        else _DEFAULT_KILL_GRACE_SECONDS
    )

def _build_unrestricted_run_args(
    *,
    docker_binary: str,
    request: ValidatedWorkloadRequest,
) -> list[str]:
    workload = request.request
    if isinstance(workload, UnrestrictedDockerRequest):
        return [docker_binary, *workload.command[1:]]
    if not isinstance(workload, UnrestrictedContainerRequest):
        raise DockerWorkloadLauncherError("unsupported unrestricted workload request")
    args = [
        docker_binary,
        "run",
        "--name",
        request.container_name,
        "--workdir",
        workload.workdir or workload.repo_dir,
        "--network",
        workload.network_mode,
        "--privileged=false",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
    ]
    for key, value in _operational_labels(request).items():
        args.extend(["--label", f"{key}={value}"])
    args.extend(["--mount", f"type=bind,source={workload.repo_dir},target={workload.repo_dir}"])
    args.extend(["--mount", f"type=bind,source={workload.artifacts_dir},target={workload.artifacts_dir}"])
    args.extend(["--mount", f"type=bind,source={workload.scratch_dir},target={workload.scratch_dir}"])
    for mount in workload.cache_mounts:
        suffix = ",readonly" if mount.read_only else ""
        args.extend(["--mount", f"type=volume,source={mount.source},target={mount.target}{suffix}"])
    for key, value in workload.env_overrides.items():
        args.extend(["--env", f"{key}={value}"])
    for flag, value in _effective_resources(
        profile=_UNRESTRICTED_RUNNER_PROFILE,
        overrides=workload.resources,
    ).items():
        args.extend([flag, value])
    if workload.entrypoint:
        args.extend(["--entrypoint", workload.entrypoint[0]])
    args.append(workload.image)
    if workload.entrypoint[1:]:
        args.extend(workload.entrypoint[1:])
    args.extend(workload.command)
    return args

def _ensure_paths_are_mounted(request: ValidatedWorkloadRequest) -> None:
    if request.profile is None:
        return
    mounts = (*request.profile.required_mounts, *request.profile.optional_mounts)
    workload = request.request
    if not _path_is_under_mount(workload.repo_dir, mounts):
        raise DockerWorkloadLauncherError(
            f"repoDir is not covered by approved profile mounts: {workload.repo_dir}"
        )
    if not _path_is_under_mount(workload.artifacts_dir, mounts):
        raise DockerWorkloadLauncherError(
            "artifactsDir is not covered by approved profile mounts: "
            f"{workload.artifacts_dir}"
        )

class DockerContainerJanitor:
    """Small Docker cleanup helper for workload containers."""

    def __init__(
        self,
        *,
        docker_binary: str = "docker",
        docker_host: str | None = None,
    ) -> None:
        self._docker_binary = docker_binary
        self._docker_host = docker_host

    async def stop(self, container_name: str, *, grace_seconds: int) -> None:
        await self._run_control(
            ["stop", "-t", str(max(0, grace_seconds)), container_name],
        )

    async def kill(self, container_name: str) -> None:
        await self._run_control(["kill", container_name])

    async def remove(self, container_name: str) -> None:
        await self._run_control(["rm", "-f", container_name])

    async def find_by_labels(self, labels: Mapping[str, str]) -> tuple[str, ...]:
        args = ["ps", "-a"]
        for key, value in labels.items():
            args.extend(["--filter", f"label={key}={value}"])
        args.extend(["--format", "{{.ID}}"])
        result = await self._run_control(args)
        return tuple(
            line.strip()
            for line in _decode_stream(result[0]).splitlines()
            if line.strip()
        )

    async def sweep_expired_workloads(
        self,
        *,
        now_iso: str | None = None,
    ) -> tuple[str, ...]:
        """Remove orphaned workload containers whose TTL label has expired."""

        return await self._sweep_expired_kind(
            kind="workload",
            now_iso=now_iso,
        )

    async def sweep_expired_helpers(
        self,
        *,
        now_iso: str | None = None,
    ) -> tuple[str, ...]:
        """Remove orphaned bounded helper containers whose TTL label has expired."""

        return await self._sweep_expired_kind(
            kind="bounded_service",
            now_iso=now_iso,
        )

    async def _sweep_expired_kind(
        self,
        *,
        kind: str,
        now_iso: str | None = None,
    ) -> tuple[str, ...]:
        now = _parse_iso_datetime(now_iso or _isoformat(datetime.now(UTC)) or "")
        if now is None:
            now = datetime.now(UTC)
        stdout, _stderr, _returncode = await self._run_control(
            [
                "ps",
                "-a",
                "--filter",
                f"label=moonmind.kind={kind}",
                "--format",
                '{{.ID}}\t{{.Names}}\t{{.Label "moonmind.expires_at"}}',
            ]
        )
        expired: list[str] = []
        for line in _decode_stream(stdout).splitlines():
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            container_id = parts[0].strip()
            expires_at = _parse_iso_datetime(parts[2])
            if container_id and expires_at is not None and expires_at <= now:
                await self.remove(container_id)
                expired.append(container_id)
        return tuple(expired)

    async def _run_control(self, args: Sequence[str]) -> tuple[bytes, bytes, int]:
        process = await asyncio.create_subprocess_exec(
            self._docker_binary,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_docker_env(docker_host=self._docker_host),
        )
        stdout, stderr = await process.communicate()
        return stdout, stderr, int(process.returncode or 0)

class DockerWorkloadLauncher:
    """Turn a validated workload request into one bounded Docker execution."""

    def __init__(
        self,
        *,
        docker_binary: str = "docker",
        docker_host: str | None = None,
        janitor: DockerContainerJanitor | None = None,
        concurrency_limiter: DockerWorkloadConcurrencyLimiter | None = None,
    ) -> None:
        self._docker_binary = docker_binary
        self._docker_host = docker_host
        self._janitor = janitor or DockerContainerJanitor(
            docker_binary=docker_binary,
            docker_host=docker_host,
        )
        self._concurrency_limiter = (
            concurrency_limiter or DockerWorkloadConcurrencyLimiter()
        )
        self._helper_leases: dict[str, _ConcurrencyLease] = {}

    def build_run_args(self, request: ValidatedWorkloadRequest) -> list[str]:
        _ensure_paths_are_mounted(request)
        profile = request.profile
        workload = request.request
        if profile is None:
            return _build_unrestricted_run_args(
                docker_binary=self._docker_binary,
                request=request,
            )
        args = [
            self._docker_binary,
            "run",
            "--name",
            request.container_name,
            "--workdir",
            workload.repo_dir,
            "--network",
            profile.network_policy,
            "--privileged=false",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
        ]

        for key, value in _operational_labels(request).items():
            args.extend(["--label", f"{key}={value}"])
        for mount in (*profile.required_mounts, *profile.optional_mounts):
            args.extend(["--mount", _mount_arg(mount)])
        for mount in profile.credential_mounts:
            args.extend(["--mount", _mount_arg(mount)])
        for key, value in workload.env_overrides.items():
            args.extend(["--env", f"{key}={value}"])
        for flag, value in _effective_resources(
            profile=profile,
            overrides=workload.resources,
        ).items():
            args.extend([flag, value])
        if profile.entrypoint:
            args.extend(["--entrypoint", profile.entrypoint[0]])

        args.append(profile.image)
        if len(profile.entrypoint) > 1:
            args.extend(profile.entrypoint[1:])
        args.extend(profile.command_wrapper)
        args.extend(
            _workload_command_args(
                command_wrapper=profile.command_wrapper,
                workload_command=workload.command,
            )
        )
        return args

    def build_helper_run_args(self, request: ValidatedWorkloadRequest) -> list[str]:
        profile = request.profile
        workload = request.request
        if profile is None:
            return _build_unrestricted_run_args(
                docker_binary=self._docker_binary,
                request=request,
            )
        if profile.kind != "bounded_service":
            raise DockerWorkloadLauncherError(
                "start_helper requires a bounded_service runner profile"
            )
        _ensure_paths_are_mounted(request)
        args = [
            self._docker_binary,
            "run",
            "--detach",
            "--name",
            request.container_name,
            "--workdir",
            workload.repo_dir,
            "--network",
            profile.network_policy,
            "--privileged=false",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
        ]
        for key, value in _operational_labels(request).items():
            args.extend(["--label", f"{key}={value}"])
        for mount in (*profile.required_mounts, *profile.optional_mounts):
            args.extend(["--mount", _mount_arg(mount)])
        for mount in profile.credential_mounts:
            args.extend(["--mount", _mount_arg(mount)])
        for key, value in workload.env_overrides.items():
            args.extend(["--env", f"{key}={value}"])
        for flag, value in _effective_resources(
            profile=profile,
            overrides=workload.resources,
        ).items():
            args.extend([flag, value])
        if profile.entrypoint:
            args.extend(["--entrypoint", profile.entrypoint[0]])
        args.append(profile.image)
        if len(profile.entrypoint) > 1:
            args.extend(profile.entrypoint[1:])
        args.extend(profile.command_wrapper)
        args.extend(
            _workload_command_args(
                command_wrapper=profile.command_wrapper,
                workload_command=workload.command,
            )
        )
        return args

    async def run(
        self,
        request: ValidatedWorkloadRequest,
        *,
        timeout_seconds: float | None = None,
    ) -> WorkloadResult:
        started_at = datetime.now(UTC)
        stdout_buffer = bytearray()
        stderr_buffer = bytearray()
        exit_code: int | None = None
        timeout_reason: str | None = None
        configured_timeout = (
            timeout_seconds
            if timeout_seconds is not None
            else _request_timeout_seconds(request)
        )
        lease = await self._concurrency_limiter.acquire(request)

        try:
            process = await asyncio.create_subprocess_exec(
                *self.build_run_args(request),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=_docker_env(docker_host=self._docker_host),
            )
            try:
                exit_code = await asyncio.wait_for(
                    _wait_with_limited_output(
                        process,
                        stdout_buffer=stdout_buffer,
                        stderr_buffer=stderr_buffer,
                    ),
                    timeout=configured_timeout,
                )
                status = "succeeded" if exit_code == 0 else "failed"
            except asyncio.TimeoutError:
                status = "timed_out"
                timeout_reason = "workload exceeded timeoutSeconds"
                await self._terminate_container(request)
                try:
                    await asyncio.wait_for(
                        _wait_with_limited_output(
                            process,
                            stdout_buffer=stdout_buffer,
                            stderr_buffer=stderr_buffer,
                        ),
                        timeout=max(1, _request_kill_grace_seconds(request)),
                    )
                except asyncio.TimeoutError:
                    process.kill()
                    await _wait_with_limited_output(
                        process,
                        stdout_buffer=stdout_buffer,
                        stderr_buffer=stderr_buffer,
                    )
                exit_code = process.returncode
            except asyncio.CancelledError:
                await self._terminate_container(request)
                if process.returncode is None:
                    process.terminate()
                    try:
                        await asyncio.wait_for(
                            _wait_with_limited_output(
                                process,
                                stdout_buffer=stdout_buffer,
                                stderr_buffer=stderr_buffer,
                            ),
                            timeout=max(1, _request_kill_grace_seconds(request)),
                        )
                    except asyncio.TimeoutError:
                        process.kill()
                        await _wait_with_limited_output(
                            process,
                            stdout_buffer=stdout_buffer,
                            stderr_buffer=stderr_buffer,
                        )
                raise
            finally:
                if request.profile is not None and request.profile.cleanup.remove_container_on_exit:
                    await self._janitor.remove(request.container_name)
        finally:
            await lease.release()

        completed_at = datetime.now(UTC)
        duration_seconds = (completed_at - started_at).total_seconds()
        stdout = redact_sensitive_text(_decode_stream(bytes(stdout_buffer)))
        stderr = redact_sensitive_text(_decode_stream(bytes(stderr_buffer)))
        workload_metadata = _workload_metadata(
            request,
            status=status,
            exit_code=exit_code,
            started_at=started_at,
            completed_at=completed_at,
            duration_seconds=duration_seconds,
            timeout_reason=timeout_reason,
        )
        diagnostics = {
            **workload_metadata,
            "command": list(request.request.command),
            "envOverrideKeys": sorted(request.request.env_overrides),
            "declaredOutputs": dict(request.request.declared_outputs),
            "resourceOverrides": request.request.resources.model_dump(
                mode="json",
                by_alias=True,
                exclude_none=True,
            ),
            "cleanup": (request.profile.cleanup.model_dump(mode="json", by_alias=True) if request.profile is not None else {"removeContainerOnExit": False, "killGraceSeconds": _DEFAULT_KILL_GRACE_SECONDS}),
        }
        declared_refs, missing_declared_outputs = _declared_output_refs(request)
        diagnostics["declaredOutputRefs"] = dict(declared_refs)
        diagnostics["missingDeclaredOutputs"] = dict(missing_declared_outputs)
        stdout_ref, stderr_ref, diagnostics_ref, output_refs, artifact_publication = (
            _publish_workload_artifacts(
                request,
                stdout=stdout,
                stderr=stderr,
                diagnostics=diagnostics,
                declared_output_refs=declared_refs,
            )
        )
        workload_metadata["artifactPublication"] = artifact_publication
        metadata = redact_sensitive_payload({
            "containerName": request.container_name,
            "image": request.profile.image if request.profile is not None else getattr(request.request, "image", None),
            "imageRef": request.profile.image if request.profile is not None else getattr(request.request, "image", None),
            "dockerHost": self._docker_host or os.environ.get("DOCKER_HOST", ""),
            "artifactsDir": request.request.artifacts_dir,
            "stdout": stdout,
            "stderr": stderr,
            "workload": workload_metadata,
            "artifactPublication": artifact_publication,
        })
        return WorkloadResult(
            requestId=request.container_name,
            profileId=request.profile.id if request.profile is not None else request.request.tool_name,
            status=status,
            labels=request.ownership.labels,
            exitCode=exit_code,
            startedAt=started_at,
            completedAt=completed_at,
            durationSeconds=duration_seconds,
            timeoutReason=timeout_reason,
            stdoutRef=stdout_ref,
            stderrRef=stderr_ref,
            diagnosticsRef=diagnostics_ref,
            outputRefs=output_refs,
            metadata=metadata,
        )

    async def start_helper(
        self,
        request: ValidatedWorkloadRequest,
    ) -> WorkloadResult:
        started_at = datetime.now(UTC)
        lease = await self._concurrency_limiter.acquire(request)
        try:
            process = await asyncio.create_subprocess_exec(
                *self.build_helper_run_args(request),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=_docker_env(docker_host=self._docker_host),
            )
            stdout, stderr = await process.communicate()
            if int(process.returncode or 0) != 0:
                completed_at = datetime.now(UTC)
                return self._helper_result(
                    request,
                    status="failed",
                    started_at=started_at,
                    completed_at=completed_at,
                    stdout=_decode_stream(stdout),
                    stderr=_decode_stream(stderr),
                    readiness={
                        "status": "not_started",
                        "reason": "docker run failed",
                    },
                )
            self._helper_leases[request.container_name] = lease
            lease = None
            readiness = await self._wait_for_helper_readiness(request)
        finally:
            if lease is not None:
                await lease.release()

        completed_at = datetime.now(UTC)
        status = "ready" if readiness.get("status") == "ready" else "unhealthy"
        return self._helper_result(
            request,
            status=status,
            started_at=started_at,
            completed_at=completed_at,
            stdout=_decode_stream(stdout),
            stderr=_decode_stream(stderr),
            readiness=readiness,
        )

    async def stop_helper(
        self,
        request: ValidatedWorkloadRequest,
        *,
        reason: str = "bounded_window_complete",
    ) -> WorkloadResult:
        if request.profile.kind != "bounded_service":
            raise DockerWorkloadLauncherError(
                "stop_helper requires a bounded_service runner profile"
            )
        started_at = datetime.now(UTC)
        stdout, stderr = await self._collect_container_logs(request.container_name)
        try:
            await self._terminate_container(request)
            if request.profile.cleanup.remove_container_on_exit:
                await self._janitor.remove(request.container_name)
        finally:
            lease = self._helper_leases.pop(request.container_name, None)
            if lease is not None:
                await lease.release()
        completed_at = datetime.now(UTC)
        return self._helper_result(
            request,
            status="stopped",
            started_at=started_at,
            completed_at=completed_at,
            stdout=stdout,
            stderr=stderr,
            readiness={},
            teardown={
                "status": "complete",
                "reason": reason,
                "removeContainerOnExit": request.profile.cleanup.remove_container_on_exit,
            },
        )

    async def _wait_for_helper_readiness(
        self,
        request: ValidatedWorkloadRequest,
    ) -> dict[str, object]:
        probe = request.profile.readiness_probe
        if probe is None:
            raise DockerWorkloadLauncherError(
                "bounded_service profiles must define a readinessProbe"
            )
        last_stdout = ""
        last_stderr = ""
        for attempt in range(1, probe.retries + 1):
            process = await asyncio.create_subprocess_exec(
                self._docker_binary,
                "exec",
                request.container_name,
                *probe.command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=_docker_env(docker_host=self._docker_host),
            )
            try:
                stdout_buffer = bytearray()
                stderr_buffer = bytearray()
                await asyncio.wait_for(
                    _wait_with_limited_output(
                        process,
                        stdout_buffer=stdout_buffer,
                        stderr_buffer=stderr_buffer,
                    ),
                    timeout=probe.timeout_seconds,
                )
            except asyncio.TimeoutError:
                await _kill_and_reap_process(process)
                last_stdout = ""
                last_stderr = "readiness probe timed out"
                if attempt < probe.retries and probe.interval_seconds:
                    await asyncio.sleep(probe.interval_seconds)
                continue
            last_stdout = _decode_stream(bytes(stdout_buffer))
            last_stderr = _decode_stream(bytes(stderr_buffer))
            if int(process.returncode or 0) == 0:
                return {
                    "status": "ready",
                    "attempts": attempt,
                    "command": list(probe.command),
                    "stdoutBytes": len(last_stdout.encode("utf-8")),
                    "stderrBytes": len(last_stderr.encode("utf-8")),
                }
            if attempt < probe.retries and probe.interval_seconds:
                await asyncio.sleep(probe.interval_seconds)
        return {
            "status": "unhealthy",
            "attempts": probe.retries,
            "command": list(probe.command),
            "stdoutBytes": len(last_stdout.encode("utf-8")),
            "stderrBytes": len(last_stderr.encode("utf-8")),
        }

    def _helper_result(
        self,
        request: ValidatedWorkloadRequest,
        *,
        status: str,
        started_at: datetime,
        completed_at: datetime,
        stdout: str,
        stderr: str,
        readiness: Mapping[str, object],
        teardown: Mapping[str, object] | None = None,
    ) -> WorkloadResult:
        duration_seconds = (completed_at - started_at).total_seconds()
        helper_metadata = _helper_metadata(
            request,
            status=status,
            started_at=started_at,
            completed_at=completed_at,
            duration_seconds=duration_seconds,
            readiness=readiness,
            teardown=teardown,
        )
        diagnostics = {
            **helper_metadata,
            "command": list(request.request.command),
            "envOverrideKeys": sorted(request.request.env_overrides),
            "resourceOverrides": request.request.resources.model_dump(
                mode="json",
                by_alias=True,
                exclude_none=True,
            ),
            "cleanup": (request.profile.cleanup.model_dump(mode="json", by_alias=True) if request.profile is not None else {"removeContainerOnExit": False, "killGraceSeconds": 30}),
        }
        declared_refs, missing_declared_outputs = _declared_output_refs(request)
        diagnostics["declaredOutputRefs"] = dict(declared_refs)
        diagnostics["missingDeclaredOutputs"] = dict(missing_declared_outputs)
        stdout_ref, stderr_ref, diagnostics_ref, output_refs, artifact_publication = (
            _publish_workload_artifacts(
                request,
                stdout=stdout,
                stderr=stderr,
                diagnostics=diagnostics,
                declared_output_refs=declared_refs,
            )
        )
        helper_metadata["artifactPublication"] = artifact_publication
        metadata = {
            "containerName": request.container_name,
            "image": request.profile.image if request.profile is not None else getattr(request.request, "image", None),
            "imageRef": request.profile.image if request.profile is not None else getattr(request.request, "image", None),
            "dockerHost": self._docker_host or os.environ.get("DOCKER_HOST", ""),
            "artifactsDir": request.request.artifacts_dir,
            "stdout": stdout,
            "stderr": stderr,
            "helper": helper_metadata,
            "artifactPublication": artifact_publication,
        }
        return WorkloadResult(
            requestId=request.container_name,
            profileId=request.profile.id if request.profile is not None else request.request.tool_name,
            status=status,
            labels=request.ownership.labels,
            exitCode=None,
            startedAt=started_at,
            completedAt=completed_at,
            durationSeconds=duration_seconds,
            stdoutRef=stdout_ref,
            stderrRef=stderr_ref,
            diagnosticsRef=diagnostics_ref,
            outputRefs=output_refs,
            metadata=metadata,
        )

    async def _collect_container_logs(self, container_name: str) -> tuple[str, str]:
        stdout_buffer = bytearray()
        stderr_buffer = bytearray()
        process = await asyncio.create_subprocess_exec(
            self._docker_binary,
            "logs",
            container_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_docker_env(docker_host=self._docker_host),
        )
        await _wait_with_limited_output(
            process,
            stdout_buffer=stdout_buffer,
            stderr_buffer=stderr_buffer,
        )
        return _decode_stream(bytes(stdout_buffer)), _decode_stream(bytes(stderr_buffer))

    async def _terminate_container(self, request: ValidatedWorkloadRequest) -> None:
        await self._janitor.stop(
            request.container_name,
            grace_seconds=request.profile.cleanup.kill_grace_seconds if request.profile is not None else 30,
        )
        await self._janitor.kill(request.container_name)
