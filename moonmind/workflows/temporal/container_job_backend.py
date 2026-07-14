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
import time
import uuid
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
    ContainerJobFailureClass,
    ImageObservation,
)
from moonmind.workflows.temporal.container_image_acquisition import (
    FilesystemImageAcquisitionLock,
    ImageAcquisitionError,
    ImageAcquisitionLock,
    classify_pull_failure,
    image_lock_key,
    normalize_image_reference,
    parse_resolved_digest,
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

# Only the exact image id and registry repo digests are read; never the full
# manifest, so unbounded inspect output cannot reach the observation payload.
_INSPECT_FORMAT = "{{.Id}}\t{{join .RepoDigests \",\"}}"
# Bytes of bounded pull progress retained as diagnostics evidence. The full,
# unbounded pull output never crosses the activity/Temporal boundary.
_PULL_DIAGNOSTICS_MAX_BYTES = 8192


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
        image_lock: ImageAcquisitionLock | None = None,
        image_lock_root: str | Path | None = None,
        pull_lease_ttl_seconds: float = 240.0,
        pull_lock_poll_seconds: float = 2.0,
        pull_lock_max_wait_seconds: float = 280.0,
        secret_resolver: SecretResolver | None = None,
        managed_run_store: ManagedRunRecordStore | None = None,
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
        return ContainerJobActivityResult(resolvedWorkspaceRef=str(workspace))

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

    async def acquire_image(self, request: ContainerJobActivityRequest):
        spec = request.request.spec
        if spec.registry_credential_ref:
            raise RuntimeError(
                "registryCredentialRef is not supported by the selected Docker backend"
            )
        # Workspace visibility is authorized before any expensive acquisition,
        # so a missing-image pull can never precede workspace resolution.
        if not request.resolved_workspace_ref:
            raise ImageAcquisitionError(
                "workspace must be resolved before image acquisition",
                failure_class=ContainerJobFailureClass.WORKSPACE,
            )

        image = spec.image
        policy = spec.pull_policy
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
                lock_wait_ms=0,
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
                            lock_wait_ms=lock_wait_ms,
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
                        lock_wait_ms=lock_wait_ms,
                        pull_duration_ms=pull_ms,
                        diagnostics_ref=diagnostics_ref,
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
                    lock_wait_ms=int((time.monotonic() - waited_started) * 1000),
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
        lock_wait_ms: int,
        pull_duration_ms: int | None = None,
        diagnostics_ref: str | None = None,
    ) -> ContainerJobActivityResult:
        observation = ImageObservation(
            requestedReference=requested,
            resolvedDigest=digest,
            cachePresent=cache_present,
            cacheHit=cache_hit,
            pullLockWaitMs=max(0, lock_wait_ms),
            pullDurationMs=pull_duration_ms,
        )
        return ContainerJobActivityResult(
            resolvedImageRef=resolved_ref,
            imageObservation=observation,
            diagnosticsRef=diagnostics_ref,
        )

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
            def create_archive() -> BytesIO:
                archive = BytesIO()
                with tarfile.open(fileobj=archive, mode="w:gz") as bundle:
                    for output in request.request.spec.outputs:
                        path = (workspace / output.relative_path).resolve()
                        if workspace not in path.parents and path != workspace:
                            raise RuntimeError("declared output escapes the workspace")
                        if not path.exists():
                            raise RuntimeError(f"declared output is missing: {output.name}")
                        bundle.add(path, arcname=output.name, recursive=True)
                return archive

            archive = await asyncio.to_thread(create_archive)
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
