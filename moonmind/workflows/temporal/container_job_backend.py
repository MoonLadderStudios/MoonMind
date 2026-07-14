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
import os
import re
import tarfile
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import Awaitable, Callable, Protocol, Sequence, runtime_checkable

from moonmind.config.container_backend_settings import (
    ContainerBackendReadinessError,
    ContainerBackendSettings,
    resolve_container_backend_settings,
)
from moonmind.schemas.container_job_models import (
    ContainerJobActivityRequest,
    ContainerJobActivityResult,
)
from moonmind.workloads.docker_launcher import structured_container_security_args

CommandRunner = Callable[[Sequence[str]], Awaitable[tuple[int, bytes, bytes]]]
EvidencePublisher = Callable[[ContainerJobActivityRequest, str, bytes], Awaitable[str]]
ProjectionWriter = Callable[[ContainerJobActivityRequest], Awaitable[None]]
SecretResolver = Callable[[str], Awaitable[str]]

# Ownership/correlation/expiry label keys. These are applied unconditionally by
# the backend and can never be supplied or overridden through the public spec
# (the contract layer rejects ``label``/``labels`` keys outright).
LABEL_CONTAINER_JOB = "moonmind.container_job"
LABEL_OWNERSHIP = "moonmind.ownership"
LABEL_CORRELATION = "moonmind.correlation"
LABEL_EXPIRES_AT = "moonmind.expires_at"

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


class DockerContainerJobBackend:
    """Thin, deployment-selected Docker CLI adapter with owned identities."""

    def __init__(
        self,
        *,
        workspace_root: str | Path,
        settings: ContainerBackendSettings | None = None,
        docker_binary: str = "docker",
        docker_host: str | None = None,
        command_runner: CommandRunner | None = None,
        evidence_publisher: EvidencePublisher | None = None,
        projection_writer: ProjectionWriter | None = None,
        secret_resolver: SecretResolver | None = None,
    ) -> None:
        self._workspace_root = Path(workspace_root).resolve()
        # Deployment-owned policy. Default to env-independent defaults so unit
        # construction is deterministic; trusted worker code passes resolved
        # settings sourced from deployment configuration.
        self._settings = settings or resolve_container_backend_settings({})
        self._docker_binary = docker_binary
        self._docker_host = docker_host or self._settings.endpoint
        self._runner = command_runner or self._run
        self._publish = evidence_publisher
        self._write_projection = projection_writer
        self._resolve_secret = secret_resolver

    # ------------------------------------------------------------------ helpers

    async def _run(self, args: Sequence[str]) -> tuple[int, bytes, bytes]:
        env = os.environ.copy()
        if self._docker_host:
            env["DOCKER_HOST"] = self._docker_host
        process = await asyncio.create_subprocess_exec(
            self._docker_binary,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, stderr = await process.communicate()
        return int(process.returncode or 0), stdout, stderr

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
        code, _stdout, stderr = await self._runner(
            ("version", "--format", "{{.Server.Version}}")
        )
        if code:
            detail = stderr.decode(errors="replace").strip()[:500]
            raise ContainerBackendReadinessError(
                f"container-job backend endpoint is unreachable: {detail}"
            )
        return ContainerJobActivityResult()

    async def resolve_workspace(self, request: ContainerJobActivityRequest):
        ref = request.request.spec.workspace_ref
        identity = ref.get("artifactRef") or ref.get("sessionId")
        # Logical identifiers never become arbitrary paths: sanitize and contain.
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", str(identity))
        workspace = (self._workspace_root / safe).resolve()
        if self._workspace_root not in workspace.parents or not workspace.is_dir():
            raise RuntimeError("authorized container-job workspace is unavailable")
        return ContainerJobActivityResult(resolvedWorkspaceRef=str(workspace))

    async def acquire_image(self, request: ContainerJobActivityRequest):
        if request.request.spec.registry_credential_ref:
            raise RuntimeError(
                "registryCredentialRef is not supported by the selected Docker backend"
            )
        image = request.request.spec.image
        policy = request.request.spec.pull_policy
        inspect_code, stdout, _ = await self._runner(
            ("image", "inspect", "--format", "{{.Id}}", image)
        )
        if policy == "always" or (inspect_code and policy == "if-missing"):
            await self._checked("pull", image)
            inspect_code, stdout, _ = await self._runner(
                ("image", "inspect", "--format", "{{.Id}}", image)
            )
        if inspect_code:
            raise RuntimeError(
                "container image is unavailable under the selected pull policy"
            )
        resolved = stdout.decode(errors="replace").strip() or image
        return ContainerJobActivityResult(resolvedImageRef=resolved)

    async def reconcile_container(self, request: ContainerJobActivityRequest):
        name = self._name(request)
        ownership = await self._reject_ownership_collision(request, name)
        if ownership is None:
            return ContainerJobActivityResult(containerRef=name, running=False)
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
            f"type=bind,src={request.resolved_workspace_ref},dst=/workspace",
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
        await self._checked(*args)
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
        if state.get("Running"):
            return ContainerJobActivityResult(containerRef=ref, running=True)
        exit_code = int(state.get("ExitCode", 1))
        return ContainerJobActivityResult(
            containerRef=ref,
            running=False,
            terminalState="succeeded" if exit_code == 0 else "failed",
            exitCode=exit_code,
        )

    async def stop_container(self, request: ContainerJobActivityRequest):
        await self._checked(
            "stop", "--time", "10", request.container_ref or self._name(request)
        )
        return ContainerJobActivityResult(
            containerRef=request.container_ref or self._name(request), running=False
        )

    async def remove_container(self, request: ContainerJobActivityRequest):
        await self._checked(
            "rm", "--force", request.container_ref or self._name(request)
        )
        return ContainerJobActivityResult()

    async def publish_evidence(self, request: ContainerJobActivityRequest):
        ref = request.container_ref or self._name(request)
        code, stdout, stderr = await self._runner(("logs", ref))
        payload = stdout + (b"\n[stderr]\n" + stderr if stderr else b"")
        # Bound captured output to the deployment ceiling; keep the tail so the
        # terminal failure context survives truncation.
        limit = self._settings.max_output_bytes
        if len(payload) > limit:
            payload = b"[truncated]\n" + payload[-limit:]
        if code and not payload:
            raise RuntimeError("container evidence is unavailable")
        logs_ref = (
            await self._publish(request, f"{request.job_id}-logs.txt", payload)
            if self._publish
            else None
        )
        artifacts_ref = None
        if self._publish and request.request.spec.outputs:
            workspace = Path(request.resolved_workspace_ref or "").resolve()
            archive = BytesIO()
            with tarfile.open(fileobj=archive, mode="w:gz") as bundle:
                for output in request.request.spec.outputs:
                    path = (workspace / output.relative_path).resolve()
                    if workspace not in path.parents and path != workspace:
                        raise RuntimeError("declared output escapes the workspace")
                    if not path.exists():
                        raise RuntimeError(f"declared output is missing: {output.name}")
                    bundle.add(path, arcname=output.name, recursive=True)
            artifacts_ref = await self._publish(
                request, f"{request.job_id}-outputs.tar.gz", archive.getvalue()
            )
        return ContainerJobActivityResult(
            logsRef=logs_ref, artifactsRef=artifacts_ref
        )

    async def project_status(self, request: ContainerJobActivityRequest):
        if self._write_projection is None:
            raise RuntimeError("durable container-job projection writer is unavailable")
        await self._write_projection(request)
        return ContainerJobActivityResult(terminalState=request.terminal_state)

    async def repair_projection(self, request: ContainerJobActivityRequest):
        return await self.project_status(request)

    async def cleanup(self, request: ContainerJobActivityRequest):
        return ContainerJobActivityResult()
