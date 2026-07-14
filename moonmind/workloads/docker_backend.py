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
import tempfile
from dataclasses import dataclass
from typing import Mapping, Protocol, Sequence

from moonmind.schemas.container_job_models import ImageObservation, ResolvedContainerLaunchPlan

_SUPPORTED_KIND = "docker-engine"
_CACHE_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_TRUE = frozenset({"1", "true", "yes"})
_FALSE = frozenset({"0", "false", "no"})


def _strict_bool(source: Mapping[str, str], name: str, default: str) -> bool:
    value = source.get(name, default).strip().lower()
    if value not in _TRUE | _FALSE:
        raise DockerBackendError(f"{name} must be one of true/false, 1/0, or yes/no")
    return value in _TRUE


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
    approved_mount_roots: Mapping[str, tuple[str, ...]] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.approved_mount_roots is None:
            object.__setattr__(self, "approved_mount_roots", {
                "workspace": ("/work/agent_jobs",),
                "artifact": ("/var/artifacts",),
                "scratch": ("/work/agent_jobs",),
            })


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
        enabled = _strict_bool(source, "MOONMIND_DOCKER_BACKEND_ENABLED", "true")
        endpoint = source.get("SYSTEM_DOCKER_HOST", "").strip()
        settings = cls(
            enabled=enabled,
            default_backend_ref=source.get("MOONMIND_DOCKER_BACKEND_DEFAULT_REF", "system").strip(),
            kind=source.get("MOONMIND_DOCKER_BACKEND_KIND", _SUPPORTED_KIND).strip(),
            endpoint=endpoint,
            allow_raw_docker_cli=_strict_bool(source, "MOONMIND_ALLOW_RAW_DOCKER_CLI", "false"),
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
            message = stderr.decode(errors="replace")[-2048:].replace(self.settings.endpoint, "<docker-endpoint>").strip()
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
            "moonmind.correlation_id": plan.correlation_id,
            "moonmind.expires_at": plan.expires_at.isoformat(),
        }

    def _validate_plan(self, plan: ResolvedContainerLaunchPlan) -> None:
        if plan.backend_kind != self.settings.kind or plan.backend_ref != self.settings.default_backend_ref:
            raise DockerBackendError("resolved launch plan does not target the configured backend")
        limits = plan.spec.resources
        if limits.cpu_millis > self.policy.max_cpu_millis:
            raise DockerBackendError("cpuMillis exceeds deployment ceiling")
        if limits.memory_mib > self.policy.max_memory_mib:
            raise DockerBackendError("memoryMiB exceeds deployment ceiling")
        if limits.shm_mib > self.policy.max_shm_mib:
            raise DockerBackendError("shmMiB exceeds deployment ceiling")
        if limits.pids > self.policy.max_pids:
            raise DockerBackendError("pids exceeds deployment ceiling")
        if plan.spec.timeout_seconds > self.policy.max_timeout_seconds:
            raise DockerBackendError("timeoutSeconds exceeds deployment ceiling")
        if plan.spec.network_mode == "bridge" and not self.policy.allow_bridge_network:
            raise DockerBackendError("bridge networking is disabled by deployment policy")
        mounts = plan.resolved_mounts or [{"mount_class": "workspace", "resolved_ref": plan.resolved_workspace_ref, "target": "/workspace", "read_only": False}]
        seen: set[str] = set()
        for mount in mounts:
            mount_class = mount.mount_class if hasattr(mount, "mount_class") else mount["mount_class"]
            resolved_ref = mount.resolved_ref if hasattr(mount, "resolved_ref") else mount["resolved_ref"]
            target = mount.target if hasattr(mount, "target") else mount["target"]
            path = pathlib.PurePosixPath(resolved_ref)
            roots = tuple(pathlib.PurePosixPath(root) for root in self.policy.approved_mount_roots.get(mount_class, ()))
            if not roots or not path.is_absolute() or not any(root == path or root in path.parents for root in roots):
                raise DockerBackendError(f"resolved {mount_class} mount is not an approved daemon-visible source")
            if target in seen or (mount_class == "workspace" and target != "/workspace"):
                raise DockerBackendError("resolved mount target is invalid or duplicated")
            seen.add(target)
        for item in plan.spec.environment:
            if item.name not in self.policy.allowed_environment:
                raise DockerBackendError(f"environment key is not allowlisted: {item.name}")
        for cache in plan.spec.caches:
            if not _CACHE_REF.fullmatch(cache.cache_ref):
                raise DockerBackendError("cacheRef is not a valid deployment cache identity")

    def build_create_args(self, plan: ResolvedContainerLaunchPlan, *, secret_env_file: str | None = None) -> list[str]:
        self._validate_plan(plan)
        spec = plan.spec
        args = [
            "create", "--name", self._name(plan.job_id), "--workdir", spec.workdir,
            "--network", spec.network_mode, "--privileged=false", "--pid", "private",
            "--ipc", "private", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", "--cpus", str(spec.resources.cpu_millis / 1000),
            "--memory", f"{spec.resources.memory_mib}m", "--shm-size", f"{spec.resources.shm_mib}m",
            "--pids-limit", str(spec.resources.pids),
            "--userns", "private",
        ]
        mounts = plan.resolved_mounts or []
        if not mounts:
            args.extend(("--mount", f"type=bind,source={plan.resolved_workspace_ref},target=/workspace"))
        for mount in mounts:
            rendered = f"type=bind,source={mount.resolved_ref},target={mount.target}"
            if mount.read_only:
                rendered += ",readonly"
            args.extend(("--mount", rendered))
        for key, value in self._labels(plan).items():
            args.extend(("--label", f"{key}={value}"))
        for item in spec.environment:
            if item.value is not None:
                args.extend(("--env", f"{item.name}={item.value}"))
            elif secret_env_file is None:
                raise DockerBackendError(f"secret was not materialized for environment key: {item.name}")
        if secret_env_file is not None:
            args.extend(("--env-file", secret_env_file))
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
        value = (await self._checked(("inspect", container_id, "--format", '{{index .Config.Labels "moonmind.owner"}}|{{index .Config.Labels "moonmind.kind"}}|{{index .Config.Labels "moonmind.job_id"}}|{{index .Config.Labels "moonmind.backend_ref"}}|{{.Name}}'))).decode().strip()
        expected = f"moonmind|container-job|{job_id}|{self.settings.default_backend_ref}|/{self._name(job_id)}"
        if value != expected:
            raise DockerBackendError("container ownership collision")

    async def run(self, plan: ResolvedContainerLaunchPlan, *, secrets: Mapping[str, str] | None = None) -> ContainerExecution:
        self._validate_plan(plan)
        container_id = await self.find_owned(plan.job_id)
        reattached = container_id is not None
        if container_id is None:
            secret_file = None
            try:
                secret_items = []
                for item in plan.spec.environment:
                    if item.secret_ref is not None:
                        value = (secrets or {}).get(item.secret_ref)
                        if value is None:
                            raise DockerBackendError(f"secret was not materialized for environment key: {item.name}")
                        secret_items.append(f"{item.name}={value}\n")
                if secret_items:
                    handle = tempfile.NamedTemporaryFile(mode="w", prefix="mm-docker-env-", delete=False)
                    secret_file = handle.name
                    os.chmod(secret_file, 0o600)
                    with handle:
                        handle.writelines(secret_items)
                stdout, _stderr, code = await self._command(self.build_create_args(plan, secret_env_file=secret_file))
                if code:
                    container_id = await self.find_owned(plan.job_id)
                    if container_id is None:
                        raise DockerBackendError("failed to create owned container")
                    reattached = True
                else:
                    container_id = stdout.decode().strip()
            finally:
                if secret_file:
                    pathlib.Path(secret_file).unlink(missing_ok=True)
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
        labels = await self._ownership(container_id)
        await self._assert_owned(container_id, labels[2])
        grace = min(max(0, grace_seconds), self.policy.stop_grace_seconds)
        _out, _err, code = await self._command(("stop", "-t", str(grace), container_id), timeout=grace + 5)
        if code:
            await self._checked(("kill", container_id), timeout=5)

    async def _ownership(self, container_id: str) -> tuple[str, str, str, str]:
        value = (await self._checked(("inspect", container_id, "--format", '{{index .Config.Labels "moonmind.owner"}}|{{index .Config.Labels "moonmind.kind"}}|{{index .Config.Labels "moonmind.job_id"}}|{{index .Config.Labels "moonmind.backend_ref"}}'))).decode().strip()
        parts = tuple(value.split("|"))
        if len(parts) != 4 or parts[:2] != ("moonmind", "container-job") or parts[3] != self.settings.default_backend_ref:
            raise DockerBackendError("refusing lifecycle operation for an unowned container")
        return parts  # type: ignore[return-value]

    async def remove(self, container_id: str) -> None:
        labels = await self._ownership(container_id)
        await self._assert_owned(container_id, labels[2])
        await self._checked(("rm", "-f", container_id))
