"""Docker-backed workload launcher for control-plane owned workload containers."""

from __future__ import annotations

import asyncio
import os
import posixpath
from datetime import UTC, datetime
from typing import Mapping, Sequence

from moonmind.schemas.workload_models import (
    RunnerProfile,
    ValidatedWorkloadRequest,
    WorkloadMount,
    WorkloadResourceOverrides,
    WorkloadResult,
)


_MAX_CAPTURED_STREAM_CHARS = 64_000


class DockerWorkloadLauncherError(RuntimeError):
    """Raised when the Docker workload launcher cannot execute a request."""


def _decode_stream(data: bytes) -> str:
    text = data.decode("utf-8", errors="replace")
    if len(text) <= _MAX_CAPTURED_STREAM_CHARS:
        return text
    return text[-_MAX_CAPTURED_STREAM_CHARS:]


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


def _path_is_under_mount(path: str, mounts: Sequence[WorkloadMount]) -> bool:
    normalized = posixpath.normpath(path)
    for mount in mounts:
        target = posixpath.normpath(mount.target)
        if normalized == target or normalized.startswith(f"{target}/"):
            return True
    return False


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
    ) -> None:
        self._docker_binary = docker_binary
        self._docker_host = docker_host
        self._janitor = janitor or DockerContainerJanitor(
            docker_binary=docker_binary,
            docker_host=docker_host,
        )

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
        args.extend(workload.command)
        return args

    async def run(
        self,
        request: ValidatedWorkloadRequest,
        *,
        timeout_seconds: float | None = None,
    ) -> WorkloadResult:
        started_at = datetime.now(UTC)
        stdout = b""
        stderr = b""
        exit_code: int | None = None
        status = "failed"
        timeout_reason: str | None = None
        configured_timeout = (
            timeout_seconds
            if timeout_seconds is not None
            else request.request.timeout_seconds or request.profile.timeout_seconds
        )

        process = await asyncio.create_subprocess_exec(
            *self.build_run_args(request),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_docker_env(docker_host=self._docker_host),
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=configured_timeout,
            )
            exit_code = int(process.returncode or 0)
            status = "succeeded" if exit_code == 0 else "failed"
        except TimeoutError:
            status = "timed_out"
            timeout_reason = "workload exceeded timeoutSeconds"
            await self._terminate_after_timeout(request)
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=max(1, request.profile.cleanup.kill_grace_seconds),
                )
            except TimeoutError:
                process.kill()
                stdout, stderr = await process.communicate()
            exit_code = process.returncode
        except asyncio.CancelledError:
            await self._terminate_after_cancel(request)
            raise
        finally:
            if request.profile.cleanup.remove_container_on_exit:
                await self._janitor.remove(request.container_name)

        completed_at = datetime.now(UTC)
        duration_seconds = (completed_at - started_at).total_seconds()
        metadata = {
            "containerName": request.container_name,
            "image": request.profile.image,
            "dockerHost": self._docker_host or os.environ.get("DOCKER_HOST", ""),
            "artifactsDir": request.request.artifacts_dir,
            "stdout": _decode_stream(stdout),
            "stderr": _decode_stream(stderr),
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
            metadata=metadata,
        )

    async def _terminate_after_timeout(self, request: ValidatedWorkloadRequest) -> None:
        await self._janitor.stop(
            request.container_name,
            grace_seconds=request.profile.cleanup.kill_grace_seconds,
        )
        await self._janitor.kill(request.container_name)

    async def _terminate_after_cancel(self, request: ValidatedWorkloadRequest) -> None:
        await self._janitor.stop(
            request.container_name,
            grace_seconds=request.profile.cleanup.kill_grace_seconds,
        )
        await self._janitor.kill(request.container_name)
