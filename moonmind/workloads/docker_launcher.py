"""Docker-backed workload launcher for control-plane owned workload containers."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import posixpath
import shlex
from datetime import UTC, datetime
from pathlib import Path
from typing import Mapping, Sequence

from moonmind.schemas.workload_models import (
    RunnerProfile,
    ValidatedWorkloadRequest,
    WorkloadMount,
    WorkloadResourceOverrides,
    WorkloadResult,
)


logger = logging.getLogger(__name__)
_MAX_CAPTURED_STREAM_CHARS = 64_000
_MAX_CAPTURED_STREAM_BYTES = 64_000


class DockerWorkloadLauncherError(RuntimeError):
    """Raised when the Docker workload launcher cannot execute a request."""


class WorkloadConcurrencyLease:
    """Async lease returned by the workload concurrency limiter."""

    def __init__(self, limiter: "WorkloadConcurrencyLimiter", profile_id: str) -> None:
        self._limiter = limiter
        self._profile_id = profile_id
        self._released = False

    async def __aenter__(self) -> "WorkloadConcurrencyLease":
        return self

    async def __aexit__(self, *_exc_info: object) -> None:
        if self._released:
            return
        self._released = True
        await self._limiter.release(self._profile_id)


class WorkloadConcurrencyLimiter:
    """Nonblocking fleet/profile concurrency guard for Docker workloads."""

    def __init__(
        self,
        *,
        fleet_limit: int | None = None,
        per_profile_limits: Mapping[str, int] | None = None,
    ) -> None:
        self._fleet_limit = _normalize_limit(fleet_limit)
        self._per_profile_limits = {
            str(profile_id): _normalize_limit(limit)
            for profile_id, limit in dict(per_profile_limits or {}).items()
        }
        self._active_fleet = 0
        self._active_profiles: dict[str, int] = {}
        self._lock = asyncio.Lock()

    async def acquire(self, profile_id: str) -> WorkloadConcurrencyLease:
        normalized_profile = str(profile_id)
        async with self._lock:
            profile_limit = self._per_profile_limits.get(normalized_profile)
            profile_active = self._active_profiles.get(normalized_profile, 0)
            if (
                self._fleet_limit is not None
                and self._active_fleet >= self._fleet_limit
            ):
                raise DockerWorkloadLauncherError(
                    "workload fleet concurrency limit reached"
                )
            if profile_limit is not None and profile_active >= profile_limit:
                raise DockerWorkloadLauncherError(
                    f"workload profile concurrency limit reached for {normalized_profile}"
                )
            self._active_fleet += 1
            self._active_profiles[normalized_profile] = profile_active + 1
        return WorkloadConcurrencyLease(self, normalized_profile)

    async def release(self, profile_id: str) -> None:
        normalized_profile = str(profile_id)
        async with self._lock:
            self._active_fleet = max(0, self._active_fleet - 1)
            profile_active = self._active_profiles.get(normalized_profile, 0)
            if profile_active <= 1:
                self._active_profiles.pop(normalized_profile, None)
            else:
                self._active_profiles[normalized_profile] = profile_active - 1


def _normalize_limit(value: int | None) -> int | None:
    if value is None:
        return None
    return max(0, int(value))


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
        return await self._list_by_labels(labels)

    async def sweep_orphans_by_labels(
        self,
        labels: Mapping[str, str],
        *,
        ttl_seconds: int,
    ) -> tuple[str, ...]:
        containers = await self._list_by_labels(
            labels,
            extra_filters=(f"until={max(0, int(ttl_seconds))}s",),
        )
        for container in containers:
            await self.remove(container)
        if containers:
            logger.info(
                "Swept Docker workload orphan containers",
                extra={
                    "workload_orphan_sweep": {
                        "labels": dict(labels),
                        "ttlSeconds": max(0, int(ttl_seconds)),
                        "removed": list(containers),
                    }
                },
            )
        return containers

    async def _list_by_labels(
        self,
        labels: Mapping[str, str],
        *,
        extra_filters: Sequence[str] = (),
    ) -> tuple[str, ...]:
        args = ["ps", "-a"]
        for key, value in labels.items():
            args.extend(["--filter", f"label={key}={value}"])
        for item in extra_filters:
            args.extend(["--filter", item])
        args.extend(["--format", "{{.ID}}"])
        result = await self._run_control(args)
        return tuple(
            line.strip()
            for line in _decode_stream(result[0]).splitlines()
            if line.strip()
        )

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
        concurrency_limiter: WorkloadConcurrencyLimiter | None = None,
    ) -> None:
        self._docker_binary = docker_binary
        self._docker_host = docker_host
        self._janitor = janitor or DockerContainerJanitor(
            docker_binary=docker_binary,
            docker_host=docker_host,
        )
        self._concurrency_limiter = concurrency_limiter

    def build_run_args(self, request: ValidatedWorkloadRequest) -> list[str]:
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
        ]

        for key, value in request.ownership.labels.items():
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

        lease = (
            await self._concurrency_limiter.acquire(request.profile.id)
            if self._concurrency_limiter is not None
            else None
        )
        if lease is None:
            lease_context = _NoopAsyncContext()
        else:
            lease_context = lease

        async with lease_context:
            logger.info(
                "Launching Docker workload",
                extra={
                    "workload_launch": {
                        "profileId": request.profile.id,
                        "containerName": request.container_name,
                        "toolName": request.request.tool_name,
                        "taskRunId": request.request.task_run_id,
                        "stepId": request.request.step_id,
                    }
                },
            )
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


class _NoopAsyncContext:
    async def __aenter__(self) -> "_NoopAsyncContext":
        return self

    async def __aexit__(self, *_exc_info: object) -> None:
        return None
