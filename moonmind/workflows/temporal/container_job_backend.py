"""Production Docker Engine backend for durable container-job activities."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Awaitable, Callable, Sequence

from moonmind.schemas.container_job_models import (
    ContainerJobActivityRequest,
    ContainerJobActivityResult,
)

CommandRunner = Callable[[Sequence[str]], Awaitable[tuple[int, bytes, bytes]]]
EvidencePublisher = Callable[[str, bytes], Awaitable[str]]
ProjectionWriter = Callable[[ContainerJobActivityRequest], Awaitable[None]]


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
    ) -> None:
        self._workspace_root = Path(workspace_root).resolve()
        self._docker_binary = docker_binary
        self._docker_host = docker_host
        self._runner = command_runner or self._run
        self._publish = evidence_publisher
        self._write_projection = projection_writer

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
        args.append(spec.image)
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
            await self._publish(f"{request.job_id}-logs.txt", payload)
            if self._publish
            else None
        )
        return ContainerJobActivityResult(logsRef=logs_ref)

    async def project_status(self, request: ContainerJobActivityRequest):
        if self._write_projection is None:
            raise RuntimeError("durable container-job projection writer is unavailable")
        await self._write_projection(request)
        return ContainerJobActivityResult(terminalState=request.terminal_state)

    async def repair_projection(self, request: ContainerJobActivityRequest):
        return await self.project_status(request)

    async def cleanup(self, request: ContainerJobActivityRequest):
        return ContainerJobActivityResult()
