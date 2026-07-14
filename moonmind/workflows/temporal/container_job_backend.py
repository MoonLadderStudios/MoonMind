"""Production Docker Engine backend for durable container-job activities."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import tarfile
import shutil
from io import BytesIO
from pathlib import Path
from typing import Awaitable, Callable, Sequence

from moonmind.schemas.container_job_models import (
    ContainerJobActivityRequest,
    ContainerJobActivityResult,
)
from moonmind.workloads.container_workspace import (
    CONTAINER_WORKSPACE_NOT_VISIBLE,
    ApprovedWorkspaceMapping,
    ContainerMountPlan,
    ContainerWorkspaceError,
    ContainerWorkspaceResolver,
)

CommandRunner = Callable[[Sequence[str]], Awaitable[tuple[int, bytes, bytes]]]
EvidencePublisher = Callable[[ContainerJobActivityRequest, str, bytes], Awaitable[str]]
ProjectionWriter = Callable[[ContainerJobActivityRequest], Awaitable[None]]

# Small, always-available probe image used only to prove daemon visibility of
# the resolved workspace before the requested (potentially large) image is
# acquired. Overridable for air-gapped mirrors.
DEFAULT_PROBE_IMAGE = "busybox:stable"


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
        workspace_mapping: ApprovedWorkspaceMapping | None = None,
        omnigent_worktree_root: str | Path | None = None,
        probe_image: str | None = None,
    ) -> None:
        self._workspace_root = Path(workspace_root).resolve()
        self._docker_binary = docker_binary
        self._docker_host = docker_host
        self._runner = command_runner or self._run
        self._publish = evidence_publisher
        self._write_projection = projection_writer
        self._resolver = ContainerWorkspaceResolver(
            mapping=workspace_mapping
            or ApprovedWorkspaceMapping.from_workspace_root(
                self._workspace_root,
                omnigent_worktree_root=omnigent_worktree_root,
            )
        )
        self._probe_image = probe_image or os.environ.get(
            "MOONMIND_CONTAINER_JOB_PROBE_IMAGE", DEFAULT_PROBE_IMAGE
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

    def _plan(self, request: ContainerJobActivityRequest) -> ContainerMountPlan:
        """Deterministically re-resolve the owner-side mount plan.

        Called at every owner-side boundary so the daemon-visible source never
        has to cross workflow history to be reused downstream.
        """

        return self._resolver.resolve(request)

    async def resolve_workspace(self, request: ContainerJobActivityRequest):
        # Prove ownership, containment, and symlink safety now, but return only
        # an opaque handle. The resolved host/volume source stays owner-side and
        # never enters MCP/HTTP responses, activity results, or Temporal history.
        self._plan(request)
        return ContainerJobActivityResult(
            resolvedWorkspaceRef=self._resolver.opaque_handle(request)
        )

    async def probe_workspace(self, request: ContainerJobActivityRequest):
        """Prove the selected daemon can mount/read (and write) the workspace.

        Runs before image acquisition using a small probe image so a workspace
        visible to the API/agent but not the daemon fails as
        ``workspace_not_visible`` without pulling the requested large image. The
        only thing the writable probe mutates is a deterministic, job-owned
        marker, and both markers are removed in a ``finally`` block.
        """

        plan = self._plan(request)
        marker = self._resolver.visibility_marker_name(request)
        expected = hashlib.sha256(request.ownership_token.encode()).hexdigest()[:16]
        workspace_marker = Path(plan.workspace_source) / marker
        artifacts_marker = Path(plan.artifacts_source) / marker
        name = f"{self._name(request)}-probe"
        try:
            await asyncio.to_thread(workspace_marker.write_text, expected, encoding="utf-8")
            workspace_mount = next(m for m in plan.mounts if m.target == "/workspace")
            artifacts_mount = next(m for m in plan.mounts if m.target == "/artifacts")
            code, stdout, stderr = await self._runner(
                (
                    "run",
                    "--rm",
                    "--name",
                    name,
                    "--network",
                    "none",
                    "--mount",
                    workspace_mount.to_mount_arg() + ",readonly",
                    "--mount",
                    artifacts_mount.to_mount_arg(),
                    self._probe_image,
                    "sh",
                    "-c",
                    f"cat /workspace/{marker} && printf '{expected}' > /artifacts/{marker}",
                )
            )
            observed = stdout.decode(errors="replace").strip()
            if code or observed != expected:
                # Never fold raw docker stderr into the failure message: on a
                # mount failure it routinely contains the resolved bind
                # ``src=`` host path, and this message becomes an
                # ApplicationError string that enters Temporal history and
                # ordinary logs (AC10). Emit a fixed, host-path-free
                # classification message only.
                raise ContainerWorkspaceError(
                    CONTAINER_WORKSPACE_NOT_VISIBLE,
                    "selected Docker daemon cannot read the resolved workspace",
                )
            artifact_observed = await asyncio.to_thread(
                artifacts_marker.read_text, encoding="utf-8"
            )
            if artifact_observed.strip() != expected:
                raise ContainerWorkspaceError(
                    CONTAINER_WORKSPACE_NOT_VISIBLE,
                    "selected Docker daemon cannot write the resolved artifacts area",
                )
        except OSError as exc:
            # OSError stringifies with the offending filename, which is the
            # resolved host marker path under the workspace/artifacts source.
            # Keep it out of the caller-visible classification message (AC10).
            raise ContainerWorkspaceError(
                CONTAINER_WORKSPACE_NOT_VISIBLE,
                "workspace visibility marker could not be prepared",
            ) from exc
        finally:
            for path in (workspace_marker, artifacts_marker):
                try:
                    path.unlink()
                except FileNotFoundError:
                    # Probe cleanup is idempotent; either side may not have
                    # created its marker after a partial daemon failure.
                    continue
        return ContainerJobActivityResult(
            resolvedWorkspaceRef=self._resolver.opaque_handle(request)
        )

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
        # Re-resolve owner-side so the daemon-visible sources are derived from
        # trusted authority here, not from the opaque handle carried in history.
        plan = self._plan(request)
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
        ]
        args.extend(plan.mount_args())
        for item in spec.environment:
            if item.secret_ref is not None:
                raise RuntimeError("secretRef resolution is unavailable on this worker")
            args.extend(("--env", f"{item.name}={item.value}"))
        if spec.entrypoint:
            args.extend(("--entrypoint", spec.entrypoint[0]))
        args.append(request.resolved_image_ref)
        args.extend(spec.entrypoint[1:])
        args.extend(spec.command)
        code, _, _ = await self._runner(args)
        if code:
            raise ContainerWorkspaceError(
                CONTAINER_WORKSPACE_NOT_VISIBLE,
                "selected Docker daemon rejected the resolved workspace mounts",
            )
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
            # Outputs are collected only from the approved, job-owned artifact
            # root (/artifacts), never from the writable repository workspace.
            artifacts_root = Path(self._plan(request).artifacts_source).resolve()
            def create_archive() -> BytesIO:
                archive = BytesIO()
                with tarfile.open(fileobj=archive, mode="w:gz") as bundle:
                    for output in request.request.spec.outputs:
                        path = (artifacts_root / output.relative_path).resolve()
                        if artifacts_root not in path.parents and path != artifacts_root:
                            raise RuntimeError("declared output escapes the artifact root")
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
        digest = hashlib.sha256(request.ownership_token.encode()).hexdigest()[:20]
        job_dir = self._resolver.mapping.job_scratch_root / digest
        await asyncio.to_thread(shutil.rmtree, job_dir, True)
        return ContainerJobActivityResult()
