"""Deployment-owned Docker Engine adapter for resolved container jobs.

The endpoint and policy in this module are trusted worker configuration.  They
are deliberately absent from public, MCP, and Temporal job request contracts.
MoonLadderStudios/MoonMind#3254.
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import re
from dataclasses import dataclass
from typing import Mapping, Protocol, Sequence

from moonmind.schemas.container_job_models import ImageObservation, ResolvedContainerLaunchPlan

_SUPPORTED_KIND = "docker-engine"
_CACHE_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class DockerBackendError(RuntimeError):
    """Fail-closed backend configuration, policy, or Docker operation error."""


@dataclass(frozen=True)
class DockerBackendPolicy:
    max_cpu_millis: int = 8_000
    max_memory_mib: int = 16_384
    max_shm_mib: int = 1_024
    max_pids: int = 1_024
    max_timeout_seconds: int = 14_400
    max_output_bytes: int = 24_000
    stop_grace_seconds: int = 30
    allow_bridge_network: bool = True
    allowed_environment: frozenset[str] = frozenset()
    cache_volume_prefix: str = "moonmind-cache-"


@dataclass(frozen=True)
class DockerBackendSettings:
    enabled: bool
    default_backend_ref: str
    kind: str
    endpoint: str
    allow_raw_docker_cli: bool = False

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> "DockerBackendSettings":
        source = os.environ if environ is None else environ
        enabled = source.get("MOONMIND_DOCKER_BACKEND_ENABLED", "true").strip().lower() in {"1", "true", "yes"}
        endpoint = source.get("SYSTEM_DOCKER_HOST", "").strip()
        settings = cls(
            enabled=enabled,
            default_backend_ref=source.get("MOONMIND_DOCKER_BACKEND_DEFAULT_REF", "system").strip(),
            kind=source.get("MOONMIND_DOCKER_BACKEND_KIND", _SUPPORTED_KIND).strip(),
            endpoint=endpoint,
            allow_raw_docker_cli=source.get("MOONMIND_ALLOW_RAW_DOCKER_CLI", "false").strip().lower() in {"1", "true", "yes"},
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if not self.enabled:
            return
        if not self.default_backend_ref:
            raise DockerBackendError("docker backend defaultBackendRef is required")
        if self.kind != _SUPPORTED_KIND:
            raise DockerBackendError(f"unsupported docker backend kind: {self.kind or '<empty>'}")
        if not self.endpoint:
            raise DockerBackendError("SYSTEM_DOCKER_HOST is required for the enabled docker backend")


@dataclass(frozen=True)
class ContainerExecution:
    container_id: str
    exit_code: int
    stdout: bytes
    stderr: bytes
    reattached: bool


class DockerBackendAdapter(Protocol):
    async def ready(self) -> None: ...
    async def inspect_image(self, image: str) -> ImageObservation: ...
    async def run(self, plan: ResolvedContainerLaunchPlan, *, secrets: Mapping[str, str] | None = None) -> ContainerExecution: ...
    async def stop(self, container_id: str, grace_seconds: int) -> None: ...
    async def remove(self, container_id: str) -> None: ...
    async def find_owned(self, job_id: str) -> str | None: ...


class DockerEngineAdapter:
    """Narrow CLI transport for one deployment-selected Docker Engine."""

    def __init__(self, settings: DockerBackendSettings, *, policy: DockerBackendPolicy | None = None) -> None:
        settings.validate()
        if not settings.enabled:
            raise DockerBackendError("docker backend is disabled")
        self.settings = settings
        self.policy = policy or DockerBackendPolicy()

    async def _command(self, args: Sequence[str], *, timeout: float = 30) -> tuple[bytes, bytes, int]:
        env = dict(os.environ)
        env["DOCKER_HOST"] = self.settings.endpoint
        process = await asyncio.create_subprocess_exec(
            "docker", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except asyncio.TimeoutError as exc:
            process.kill()
            await process.wait()
            raise DockerBackendError(f"docker operation timed out: {args[0]}") from exc
        return stdout, stderr, int(process.returncode or 0)

    async def _checked(self, args: Sequence[str], *, timeout: float = 30) -> bytes:
        stdout, stderr, code = await self._command(args, timeout=timeout)
        if code:
            message = stderr.decode(errors="replace")[-2048:].strip()
            raise DockerBackendError(f"docker {args[0]} failed ({code}): {message}")
        return stdout

    async def ready(self) -> None:
        await self._checked(("version", "--format", "{{.Server.Version}}"), timeout=10)

    async def inspect_image(self, image: str) -> ImageObservation:
        stdout, _stderr, code = await self._command(("image", "inspect", image, "--format", "{{index .RepoDigests 0}}"))
        digest = None
        if code == 0:
            rendered = stdout.decode(errors="replace").strip()
            if "@sha256:" in rendered:
                digest = rendered.rsplit("@", 1)[1]
        return ImageObservation(requestedReference=image, resolvedDigest=digest, cachePresent=code == 0, cacheHit=code == 0)

    @staticmethod
    def _name(job_id: str) -> str:
        return "mm-container-job-" + job_id.removeprefix("container-job:")

    @staticmethod
    def _labels(plan: ResolvedContainerLaunchPlan) -> dict[str, str]:
        return {
            "moonmind.kind": "container-job",
            "moonmind.owner": "moonmind",
            "moonmind.job_id": plan.job_id,
            "moonmind.backend_ref": plan.backend_ref,
        }

    def _validate_plan(self, plan: ResolvedContainerLaunchPlan) -> None:
        if plan.backend_kind != self.settings.kind or plan.backend_ref != self.settings.default_backend_ref:
            raise DockerBackendError("resolved launch plan does not target the configured backend")
        limits = plan.spec.resources
        if limits.cpu_millis > self.policy.max_cpu_millis:
            raise DockerBackendError("cpuMillis exceeds deployment ceiling")
        if limits.memory_mib > self.policy.max_memory_mib:
            raise DockerBackendError("memoryMiB exceeds deployment ceiling")
        if limits.pids > self.policy.max_pids:
            raise DockerBackendError("pids exceeds deployment ceiling")
        if plan.spec.timeout_seconds > self.policy.max_timeout_seconds:
            raise DockerBackendError("timeoutSeconds exceeds deployment ceiling")
        if plan.spec.network_mode == "bridge" and not self.policy.allow_bridge_network:
            raise DockerBackendError("bridge networking is disabled by deployment policy")
        workspace = pathlib.PurePosixPath(plan.resolved_workspace_ref)
        forbidden = (pathlib.PurePosixPath("/"), pathlib.PurePosixPath("/var/lib/docker"), pathlib.PurePosixPath("/var/run/docker.sock"))
        if not workspace.is_absolute() or workspace in forbidden or any(root == workspace or root in workspace.parents for root in forbidden[1:]):
            raise DockerBackendError("resolved workspace is not an approved daemon-visible source")
        for item in plan.spec.environment:
            if item.name not in self.policy.allowed_environment:
                raise DockerBackendError(f"environment key is not allowlisted: {item.name}")
        for cache in plan.spec.caches:
            if not _CACHE_REF.fullmatch(cache.cache_ref):
                raise DockerBackendError("cacheRef is not a valid deployment cache identity")

    def build_create_args(self, plan: ResolvedContainerLaunchPlan, *, secrets: Mapping[str, str] | None = None) -> list[str]:
        self._validate_plan(plan)
        spec = plan.spec
        args = [
            "create", "--name", self._name(plan.job_id), "--workdir", spec.workdir,
            "--network", spec.network_mode, "--privileged=false", "--pid", "private",
            "--ipc", "private", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", "--cpus", str(spec.resources.cpu_millis / 1000),
            "--memory", f"{spec.resources.memory_mib}m", "--shm-size", f"{self.policy.max_shm_mib}m",
            "--pids-limit", str(spec.resources.pids),
            "--mount", f"type=bind,source={plan.resolved_workspace_ref},target=/workspace",
        ]
        for key, value in self._labels(plan).items():
            args.extend(("--label", f"{key}={value}"))
        materialized = secrets or {}
        for item in spec.environment:
            value = item.value if item.value is not None else materialized.get(item.secret_ref or "")
            if value is None:
                raise DockerBackendError(f"secret was not materialized for environment key: {item.name}")
            args.extend(("--env", f"{item.name}={value}"))
        for cache in spec.caches:
            mount = f"type=volume,source={self.policy.cache_volume_prefix}{cache.cache_ref},target={cache.target}"
            if cache.read_only:
                mount += ",readonly"
            args.extend(("--mount", mount))
        if spec.entrypoint:
            args.extend(("--entrypoint", spec.entrypoint[0]))
        args.append(spec.image)
        args.extend(spec.entrypoint[1:])
        args.extend(spec.command)
        return args

    async def find_owned(self, job_id: str) -> str | None:
        stdout = await self._checked(("ps", "-aq", "--filter", "label=moonmind.kind=container-job", "--filter", f"label=moonmind.job_id={job_id}"))
        found = [line for line in stdout.decode().splitlines() if line.strip()]
        if len(found) > 1:
            raise DockerBackendError("multiple containers claim the same MoonMind job")
        return found[0].strip() if found else None

    async def _assert_owned(self, container_id: str, job_id: str) -> None:
        value = (await self._checked(("inspect", container_id, "--format", '{{index .Config.Labels "moonmind.job_id"}}'))).decode().strip()
        if value != job_id:
            raise DockerBackendError("container ownership collision")

    async def run(self, plan: ResolvedContainerLaunchPlan, *, secrets: Mapping[str, str] | None = None) -> ContainerExecution:
        self._validate_plan(plan)
        container_id = await self.find_owned(plan.job_id)
        reattached = container_id is not None
        if container_id is None:
            container_id = (await self._checked(self.build_create_args(plan, secrets=secrets))).decode().strip()
        await self._assert_owned(container_id, plan.job_id)
        _stdout, _stderr, start_code = await self._command(("start", container_id))
        if start_code not in (0, 304):
            raise DockerBackendError("failed to start owned container")
        wait = await self._checked(("wait", container_id), timeout=plan.spec.timeout_seconds)
        exit_code = int(wait.decode().strip())
        stdout = await self._checked(("logs", "--stdout", container_id))
        stderr = await self._checked(("logs", "--stderr", container_id))
        limit = self.policy.max_output_bytes
        return ContainerExecution(container_id, exit_code, stdout[-limit:], stderr[-limit:], reattached)

    async def stop(self, container_id: str, grace_seconds: int) -> None:
        await self._checked(("stop", "-t", str(min(max(0, grace_seconds), self.policy.stop_grace_seconds)), container_id))

    async def remove(self, container_id: str) -> None:
        kind = (await self._checked(("inspect", container_id, "--format", '{{index .Config.Labels "moonmind.kind"}}'))).decode().strip()
        if kind != "container-job":
            raise DockerBackendError("refusing to remove a container not owned by the container-job backend")
        await self._checked(("rm", "-f", container_id))
