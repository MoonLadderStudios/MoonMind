"""Production Docker Engine backend for durable container-job activities."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import stat
import tarfile
from io import BytesIO
from pathlib import Path
from typing import Awaitable, Callable, Sequence

from moonmind.schemas.container_job_models import (
    ContainerJobActivityRequest,
    ContainerJobActivityResult,
    ContainerJobBackendError,
    ContainerJobFailureClass,
    RegistryAuthorization,
    normalize_image_reference,
)
from moonmind.workflows.temporal.runtime.registry_auth_resolve import (
    RegistryAuthResolutionError,
    RegistryCredential,
    resolve_registry_pull_credentials,
)

CommandRunner = Callable[[Sequence[str]], Awaitable[tuple[int, bytes, bytes]]]
EvidencePublisher = Callable[[ContainerJobActivityRequest, str, bytes], Awaitable[str]]
ProjectionWriter = Callable[[ContainerJobActivityRequest], Awaitable[None]]
RegistryAuthResolver = Callable[[str], Awaitable[RegistryCredential]]


def _redact(text: str, secrets: Sequence[str]) -> str:
    """Remove any resolved credential material from an observable string."""

    redacted = text
    for secret in secrets:
        token = str(secret or "").strip()
        if token:
            redacted = redacted.replace(token, "[redacted]")
    return redacted


class DockerContainerJobBackend:
    """Thin, deployment-selected Docker CLI adapter with owned identities."""

    def __init__(
        self,
        *,
        workspace_root: str | Path,
        docker_binary: str = "docker",
        docker_host: str | None = None,
        command_runner: CommandRunner | None = None,
        evidence_publisher: EvidencePublisher | None = None,
        projection_writer: ProjectionWriter | None = None,
        registry_auth_resolver: RegistryAuthResolver | None = None,
        auth_root: str | Path | None = None,
    ) -> None:
        self._workspace_root = Path(workspace_root).resolve()
        self._docker_binary = docker_binary
        self._docker_host = docker_host
        self._runner = command_runner or self._run
        self._publish = evidence_publisher
        self._write_projection = projection_writer
        self._resolve_registry_auth = (
            registry_auth_resolver or resolve_registry_pull_credentials
        )
        # Per-job ephemeral Docker auth material lives under a dedicated,
        # deployment-writable root, never inside the mounted job workspace.
        self._auth_root = (
            Path(auth_root).resolve()
            if auth_root is not None
            else self._workspace_root.parent / ".mm-container-job-auth"
        )

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
        spec = request.request.spec
        image = spec.image
        policy = spec.pull_policy
        credential_ref = spec.registry_credential_ref

        if credential_ref is None:
            return await self._acquire_public_image(image, policy)
        return await self._acquire_private_image(request, image, policy, credential_ref)

    async def _acquire_public_image(self, image: str, policy: str):
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

    async def _acquire_private_image(
        self,
        request: ContainerJobActivityRequest,
        image: str,
        policy: str,
        credential_ref: str,
    ):
        # Authorization is re-checked on every run before either a cache hit or
        # a pull is accepted: image presence in the shared daemon never bypasses
        # policy. The API-owned decision travels with the request; the worker
        # only enforces and resolves the credential, it does not re-decide.
        authorization = request.registry_authorization
        if authorization is None or not authorization.authorized:
            raise ContainerJobBackendError(
                ContainerJobFailureClass.IMAGE_USE_DENIED,
                "private image use was not authorized for this job",
            )
        normalized = normalize_image_reference(image)
        self._enforce_authorized_scope(normalized, authorization)

        # A local inspect needs no registry authentication.
        inspect_code, stdout, _ = await self._runner(
            ("image", "inspect", "--format", "{{.Id}}", image)
        )
        cache_present = inspect_code == 0
        need_pull = policy == "always" or (not cache_present and policy == "if-missing")
        if policy == "never" and not cache_present:
            raise ContainerJobBackendError(
                ContainerJobFailureClass.IMAGE,
                "private image is absent under the 'never' pull policy",
            )

        if need_pull:
            credential = await self._resolve_credential(credential_ref)
            secrets = (credential.username, credential.secret)
            auth_dir = self._auth_dir(request)
            try:
                self._materialize_registry_auth(
                    auth_dir, normalized.registry, credential
                )
                await self._authorized_pull(image, auth_dir, secrets)
            finally:
                # Remove ephemeral auth immediately after the pull. The terminal
                # ``cleanup`` activity re-removes it deterministically so a crash
                # between here and cleanup cannot leak credentials.
                self._remove_auth_dir(auth_dir, best_effort=True)
            inspect_code, stdout, _ = await self._runner(
                ("image", "inspect", "--format", "{{.Id}}", image)
            )

        if inspect_code:
            raise ContainerJobBackendError(
                ContainerJobFailureClass.IMAGE,
                "private image is unavailable under the selected pull policy",
            )
        resolved = stdout.decode(errors="replace").strip() or image
        return ContainerJobActivityResult(resolvedImageRef=resolved)

    @staticmethod
    def _enforce_authorized_scope(
        normalized, authorization: RegistryAuthorization
    ) -> None:
        if normalized.registry.lower() != authorization.registry.lower():
            raise ContainerJobBackendError(
                ContainerJobFailureClass.REPOSITORY_SCOPE_MISMATCH,
                "resolved registry is outside the authorized scope",
            )
        if normalized.repository != authorization.repository:
            raise ContainerJobBackendError(
                ContainerJobFailureClass.REPOSITORY_SCOPE_MISMATCH,
                "resolved repository is outside the authorized scope",
            )
        if authorization.digest is not None and normalized.digest != authorization.digest:
            raise ContainerJobBackendError(
                ContainerJobFailureClass.REPOSITORY_SCOPE_MISMATCH,
                "resolved digest does not match the authorized digest",
            )

    async def _resolve_credential(self, credential_ref: str) -> RegistryCredential:
        try:
            return await self._resolve_registry_auth(credential_ref)
        except RegistryAuthResolutionError as exc:
            raise ContainerJobBackendError(
                ContainerJobFailureClass.CREDENTIAL_UNRESOLVED,
                "registry credential reference could not be resolved",
            ) from exc

    def _auth_dir(self, request: ContainerJobActivityRequest) -> Path:
        suffix = hashlib.sha256(request.ownership_token.encode()).hexdigest()[:20]
        return self._auth_root / f"job-{suffix}"

    def _materialize_registry_auth(
        self, auth_dir: Path, registry: str, credential: RegistryCredential
    ) -> None:
        """Write a per-job Docker config with restrictive ownership and mode."""

        self._remove_auth_dir(auth_dir, best_effort=True)
        auth_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(auth_dir, stat.S_IRWXU)  # 0o700
        config = {"auths": {registry: credential.docker_auth_entry()}}
        config_path = auth_dir / "config.json"
        fd = os.open(
            config_path,
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
            stat.S_IRUSR | stat.S_IWUSR,  # 0o600
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(config, handle)
        finally:
            os.chmod(config_path, stat.S_IRUSR | stat.S_IWUSR)

    def _remove_auth_dir(self, auth_dir: Path, *, best_effort: bool) -> None:
        if best_effort:
            shutil.rmtree(auth_dir, ignore_errors=True)
            return
        if auth_dir.exists():
            shutil.rmtree(auth_dir)

    async def _authorized_pull(
        self, image: str, auth_dir: Path, secrets: tuple[str, ...]
    ) -> None:
        code, _, stderr = await self._runner(
            ("--config", str(auth_dir), "pull", image)
        )
        if code:
            detail = _redact(stderr.decode(errors="replace").strip()[:1000], secrets)
            raise ContainerJobBackendError(
                ContainerJobFailureClass.REGISTRY_AUTH_FAILED,
                f"authorized registry pull failed: {detail}",
            )

    async def reconcile_container(self, request: ContainerJobActivityRequest):
        name = self._name(request)
        code, stdout, _ = await self._runner(
            ("inspect", "--format", "{{.State.Running}}", name)
        )
        if code:
            return ContainerJobActivityResult()
        return ContainerJobActivityResult(
            containerRef=name, running=stdout.strip() == b"true"
        )

    async def create_container(self, request: ContainerJobActivityRequest):
        if not request.resolved_workspace_ref or not request.resolved_image_ref:
            raise RuntimeError("resolved workspace and image are required")
        spec = request.request.spec
        name = self._name(request)
        args = [
            "create",
            "--name",
            name,
            "--label",
            f"moonmind.container_job={request.job_id}",
            "--label",
            f"moonmind.ownership={request.ownership_token}",
            "--network",
            spec.network_mode,
            "--cpus",
            str(spec.resources.cpu_millis / 1000),
            "--memory",
            f"{spec.resources.memory_mib}m",
            "--pids-limit",
            str(spec.resources.pids),
            "--workdir",
            spec.workdir,
            "--mount",
            f"type=bind,src={request.resolved_workspace_ref},dst=/workspace",
        ]
        for item in spec.environment:
            if item.secret_ref is not None:
                raise RuntimeError("secretRef resolution is unavailable on this worker")
            args.extend(("--env", f"{item.name}={item.value}"))
        if spec.entrypoint:
            args.extend(("--entrypoint", spec.entrypoint[0]))
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
        # Deterministic, job-owned removal of any ephemeral registry auth. This
        # runs on success, failure, cancellation, timeout, and orphan
        # reconciliation, so credential material never outlives the job. A
        # cleanup failure is reported through the workflow's separate cleanup
        # auxiliary outcome and never rewrites the primary workload result.
        auth_dir = self._auth_dir(request)
        try:
            self._remove_auth_dir(auth_dir, best_effort=False)
        except OSError as exc:
            raise ContainerJobBackendError(
                ContainerJobFailureClass.CREDENTIAL_CLEANUP_FAILED,
                "ephemeral registry credential directory could not be removed",
            ) from exc
        return ContainerJobActivityResult()
