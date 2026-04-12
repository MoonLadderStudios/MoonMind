"""Docker-backed workload launcher for control-plane owned workload containers."""

from __future__ import annotations

import asyncio
import json
import os
import posixpath
import shlex
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

from moonmind.schemas.workload_models import (
    RunnerProfile,
    ValidatedWorkloadRequest,
    WorkloadMount,
    WorkloadResourceOverrides,
    WorkloadResult,
)


_MAX_CAPTURED_STREAM_CHARS = 64_000
_MAX_CAPTURED_STREAM_BYTES = 64_000


class DockerWorkloadLauncherError(RuntimeError):
    """Raised when the Docker workload launcher cannot execute a request."""

    def __init__(
        self,
        message: str,
        *,
        reason: str = "launcher_error",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.details = dict(details or {})


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
        profile_id = request.profile.id
        async with self._lock:
            active_for_profile = self._active_by_profile.get(profile_id, 0)
            if active_for_profile >= request.profile.max_concurrency:
                raise DockerWorkloadLauncherError(
                    "workload concurrency limit exceeded for profile "
                    f"{profile_id}",
                    reason="concurrency_limit_exceeded",
                    details={
                        "scope": "profile",
                        "profileId": profile_id,
                        "limit": request.profile.max_concurrency,
                    },
                )
            if (
                self._fleet_limit is not None
                and self._active_total >= self._fleet_limit
            ):
                raise DockerWorkloadLauncherError(
                    "workload concurrency limit exceeded for docker_workload fleet",
                    reason="concurrency_limit_exceeded",
                    details={
                        "scope": "fleet",
                        "limit": self._fleet_limit,
                    },
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


def _docker_env(*, docker_host: str | None = None) -> dict[str, str]:
    env = dict(os.environ)
    if docker_host:
        env["DOCKER_HOST"] = docker_host
    return env


def _mount_arg(mount: WorkloadMount) -> str:
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


def _operational_labels(
    request: ValidatedWorkloadRequest,
    *,
    timeout_seconds: float | None = None,
) -> dict[str, str]:
    effective_timeout_seconds = (
        timeout_seconds
        if timeout_seconds is not None
        else request.request.timeout_seconds or request.profile.timeout_seconds
    )
    expires_at = datetime.now(UTC) + timedelta(
        seconds=effective_timeout_seconds + request.profile.cleanup.kill_grace_seconds
    )
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
    return {
        "taskRunId": request.request.task_run_id,
        "stepId": request.request.step_id,
        "attempt": request.request.attempt,
        "toolName": request.request.tool_name,
        "profileId": request.profile.id,
        "imageRef": request.profile.image,
        "containerName": request.container_name,
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

    stdout_ref = _write("runtime.stdout", workload_root / "runtime.stdout.log", stdout)
    stderr_ref = _write("runtime.stderr", workload_root / "runtime.stderr.log", stderr)
    diagnostics_payload = dict(diagnostics)
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


def _ensure_paths_are_mounted(request: ValidatedWorkloadRequest) -> None:
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

        now = _parse_iso_datetime(now_iso or _isoformat(datetime.now(UTC)) or "")
        if now is None:
            now = datetime.now(UTC)
        stdout, _stderr, _returncode = await self._run_control(
            [
                "ps",
                "-a",
                "--filter",
                "label=moonmind.kind=workload",
                "--format",
                '{{.ID}}\t{{.Names}}\t{{.Label "moonmind.expires_at"}}',
            ]
        )
        expired: list[str] = []
        for line in stdout.decode("utf-8", errors="replace").splitlines():
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

    def build_run_args(
        self,
        request: ValidatedWorkloadRequest,
        *,
        timeout_seconds: float | None = None,
    ) -> list[str]:
        _ensure_paths_are_mounted(request)
        profile = request.profile
        workload = request.request
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

        for key, value in _operational_labels(
            request,
            timeout_seconds=timeout_seconds,
        ).items():
            args.extend(["--label", f"{key}={value}"])
        for mount in (*profile.required_mounts, *profile.optional_mounts):
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
            else request.request.timeout_seconds or request.profile.timeout_seconds
        )
        lease = await self._concurrency_limiter.acquire(request)

        try:
            process = await asyncio.create_subprocess_exec(
                *self.build_run_args(request, timeout_seconds=configured_timeout),
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
                        timeout=max(1, request.profile.cleanup.kill_grace_seconds),
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
                            timeout=max(1, request.profile.cleanup.kill_grace_seconds),
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
                if request.profile.cleanup.remove_container_on_exit:
                    await self._janitor.remove(request.container_name)
        finally:
            await lease.release()

        completed_at = datetime.now(UTC)
        duration_seconds = (completed_at - started_at).total_seconds()
        stdout = _decode_stream(bytes(stdout_buffer))
        stderr = _decode_stream(bytes(stderr_buffer))
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
            "cleanup": request.profile.cleanup.model_dump(
                mode="json",
                by_alias=True,
            ),
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
        metadata = {
            "containerName": request.container_name,
            "image": request.profile.image,
            "imageRef": request.profile.image,
            "dockerHost": self._docker_host or os.environ.get("DOCKER_HOST", ""),
            "artifactsDir": request.request.artifacts_dir,
            "stdout": stdout,
            "stderr": stderr,
            "workload": workload_metadata,
            "artifactPublication": artifact_publication,
        }
        return WorkloadResult(
            requestId=request.container_name,
            profileId=request.profile.id,
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

    async def _terminate_container(self, request: ValidatedWorkloadRequest) -> None:
        await self._janitor.stop(
            request.container_name,
            grace_seconds=request.profile.cleanup.kill_grace_seconds,
        )
        await self._janitor.kill(request.container_name)
