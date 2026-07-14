"""Production Docker Engine backend for durable container-job activities."""

from __future__ import annotations

import asyncio
import hashlib
import json
import mimetypes
import os
import re
import tarfile
import time
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Awaitable, Callable, Sequence

from moonmind.schemas.container_job_models import (
    MAX_LIVE_LOG_EVENT_BYTES,
    MAX_LIVE_LOG_TOTAL_BYTES,
    MAX_OUTPUT_FILE_BYTES,
    MAX_OUTPUT_TOTAL_BYTES,
    MAX_TERMINAL_LOG_BYTES,
    ContainerJobActivityRequest,
    ContainerJobActivityResult,
    ContainerJobEvidenceEntry,
    ContainerJobEvidenceManifest,
    ContainerJobLogEntry,
    EvidenceCollectionStatus,
    ImageObservation,
)
from moonmind.schemas.workspace_locator_models import (
    ExternalStateLocator,
    ManagedWorkspaceLocator,
    SandboxWorkspaceLocator,
)
from moonmind.utils.logging import redact_sensitive_payload, redact_sensitive_text
from moonmind.workflows.temporal.runtime.workspace_locators import (
    ManagedRunRecordStore,
    resolve_managed_workspace_locator,
)

CommandRunner = Callable[[Sequence[str]], Awaitable[tuple[int, bytes, bytes]]]
# Evidence publisher stores a bounded payload and returns an opaque artifact ref.
EvidencePublisher = Callable[
    [ContainerJobActivityRequest, str, bytes, str], Awaitable[str]
]
# Live-log publisher forwards bounded, ordered, redacted entries onto the shared
# Live Logs transport (AC2/AC12). It never becomes authoritative over artifacts.
LiveLogPublisher = Callable[
    [ContainerJobActivityRequest, list[ContainerJobLogEntry]], Awaitable[None]
]
ProjectionWriter = Callable[[ContainerJobActivityRequest], Awaitable[None]]


class ContainerJobWorkspaceNotVisibleError(RuntimeError):
    """Raised when the authorized workspace cannot be resolved or probed (AC1)."""


class _OutputRejected(RuntimeError):
    """Internal signal that a declared output fails collection hardening (AC6)."""


def _within(path: Path, real_root: str) -> bool:
    real = os.path.realpath(path)
    return real == real_root or real.startswith(real_root + os.sep)


def _safe_members(
    candidate: Path, real_root: str
) -> list[tuple[Path, str | None, int]]:
    """Enumerate regular files under a (resolved) declared output path.

    Rejects unsupported file types and symlinks that escape the approved root.
    Returns ``(source_path, archive_suffix, size_bytes)`` tuples.
    """

    if candidate.is_symlink():
        # ``candidate`` was resolved by the caller, so a residual symlink here is
        # a loop/broken link; refuse rather than guess.
        raise _OutputRejected("declared output is an unresolved symlink")
    if candidate.is_file():
        return [(candidate, None, candidate.stat().st_size)]
    if candidate.is_dir():
        results: list[tuple[Path, str | None, int]] = []
        stack: list[tuple[Path, str]] = [(candidate, "")]
        while stack:
            base, prefix = stack.pop()
            for child in sorted(base.iterdir()):
                rel = f"{prefix}{child.name}"
                if child.is_symlink():
                    if not _within(child, real_root):
                        raise _OutputRejected(
                            "symlink in declared output escapes the approved artifact root"
                        )
                    if child.is_dir():
                        raise _OutputRejected(
                            "symlinked directory outputs are not supported"
                        )
                    if child.is_file():
                        results.append(
                            (child.resolve(), rel, child.stat().st_size)
                        )
                        continue
                    raise _OutputRejected("unsupported file type in declared output")
                if child.is_dir():
                    stack.append((child, f"{rel}/"))
                elif child.is_file():
                    results.append((child, rel, child.stat().st_size))
                else:
                    raise _OutputRejected("unsupported file type in declared output")
        return results
    raise _OutputRejected("unsupported file type in declared output")


def _bounded_log(raw: bytes) -> tuple[str, "EvidenceCollectionStatus"]:
    """Redact and bound a terminal log stream, preserving the tail if truncated."""

    if not raw:
        return "", EvidenceCollectionStatus.EMPTY
    truncated = len(raw) > MAX_TERMINAL_LOG_BYTES
    if truncated:
        raw = raw[-MAX_TERMINAL_LOG_BYTES:]
    text = redact_sensitive_text(raw.decode("utf-8", errors="replace"))
    if truncated:
        return (
            "[...truncated to durable-log tail...]\n" + text,
            EvidenceCollectionStatus.TRUNCATED,
        )
    return text, EvidenceCollectionStatus.COLLECTED


def _stream_entry(
    stream: str,
    payload_bytes: bytes,
    status: "EvidenceCollectionStatus",
    ref: str,
) -> ContainerJobEvidenceEntry:
    return ContainerJobEvidenceEntry(
        name=f"{stream}.log",
        kind=stream,
        artifactRef=ref,
        sizeBytes=len(payload_bytes),
        sha256=hashlib.sha256(payload_bytes).hexdigest(),
        mediaType="text/plain",
        collectionStatus=status,
    )


def _diagnostics_payload(request: ContainerJobActivityRequest) -> bytes:
    """Build redacted, bounded runtime diagnostics/exit metadata (AC5/AC11)."""

    payload = {
        "jobId": request.job_id,
        "state": request.state or request.terminal_state,
        "terminalState": request.terminal_state,
        "exitCode": request.exit_code,
        "failureClass": request.failure_class,
        "message": request.message,
        "containerRef": request.container_ref,
        "resolvedImageRef": request.resolved_image_ref,
    }
    redacted = redact_sensitive_payload(payload)
    return json.dumps(
        redacted, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")


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
        live_log_publisher: LiveLogPublisher | None = None,
        projection_writer: ProjectionWriter | None = None,
        managed_run_store: ManagedRunRecordStore | None = None,
    ) -> None:
        self._workspace_root = Path(workspace_root).resolve()
        self._docker_binary = docker_binary
        self._docker_host = docker_host
        self._runner = command_runner or self._run
        self._publish = evidence_publisher
        self._publish_live = live_log_publisher
        self._write_projection = projection_writer
        self._managed_run_store = managed_run_store

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
            raise ContainerJobWorkspaceNotVisibleError(
                "authorized container-job workspace is unavailable"
            )
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
        cache_present = inspect_code == 0
        pull_duration_ms: int | None = None
        pulled = False
        if policy == "always" or (inspect_code and policy == "if-missing"):
            started = time.monotonic()
            await self._checked("pull", image)
            pull_duration_ms = max(0, int((time.monotonic() - started) * 1000))
            pulled = True
            inspect_code, stdout, _ = await self._runner(
                ("image", "inspect", "--format", "{{.Id}}", image)
            )
        if inspect_code:
            raise RuntimeError(
                "container image is unavailable under the selected pull policy"
            )
        resolved = stdout.decode(errors="replace").strip() or image
        observation = ImageObservation(
            requestedReference=image,
            resolvedDigest=resolved if resolved.startswith("sha256:") else None,
            cachePresent=cache_present,
            cacheHit=cache_present and not pulled,
            pullDurationMs=pull_duration_ms,
        )
        return ContainerJobActivityResult(
            resolvedImageRef=resolved, image=observation
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
        # Capture incremental output for the shared Live Logs transport before
        # reading terminal state so partial evidence survives cancel/timeout (AC7).
        capture = await self._capture_incremental_logs(request, ref)
        raw = await self._checked("inspect", "--format", "{{json .State}}", ref)
        state = json.loads(raw)
        if state.get("Running"):
            return ContainerJobActivityResult(
                containerRef=ref, running=True, **capture
            )
        exit_code = int(state.get("ExitCode", 1))
        return ContainerJobActivityResult(
            containerRef=ref,
            running=False,
            terminalState="succeeded" if exit_code == 0 else "failed",
            exitCode=exit_code,
            **capture,
        )

    async def _capture_incremental_logs(
        self, request: ContainerJobActivityRequest, ref: str
    ) -> dict[str, int]:
        """Emit ordered, redacted, bounded live-log entries since the last cursor.

        Returns the advanced cursor so the workflow can thread it into the next
        observe cycle. Live delivery is best-effort and never authoritative over
        the durable terminal artifacts published at completion (AC2/AC4).
        """

        if self._publish_live is None:
            return {}
        code, stdout, stderr = await self._runner(("logs", ref))
        if code and not stdout and not stderr:
            return {}
        sequence = request.log_sequence
        entries: list[ContainerJobLogEntry] = []
        stdout_offset, sequence = self._delta_entries(
            stdout, request.log_stdout_offset, "stdout", sequence, entries
        )
        stderr_offset, sequence = self._delta_entries(
            stderr, request.log_stderr_offset, "stderr", sequence, entries
        )
        if entries:
            await self._publish_live(request, entries)
        return {
            "logStdoutOffset": stdout_offset,
            "logStderrOffset": stderr_offset,
            "logSequence": sequence,
        }

    @staticmethod
    def _delta_entries(
        raw: bytes,
        offset: int,
        stream: str,
        sequence: int,
        entries: list[ContainerJobLogEntry],
    ) -> tuple[int, int]:
        total = len(raw)
        if total <= offset:
            return total, sequence
        delta = raw[offset:]
        # Bound how much unseen output a single cycle forwards to the live plane;
        # the durable artifact fallback retains the complete stream at terminal.
        if len(delta) > MAX_LIVE_LOG_TOTAL_BYTES:
            delta = delta[-MAX_LIVE_LOG_TOTAL_BYTES:]
        text = redact_sensitive_text(delta.decode("utf-8", errors="replace"))
        now = datetime.now(timezone.utc)
        for line in text.splitlines():
            if not line:
                continue
            sequence += 1
            entries.append(
                ContainerJobLogEntry(
                    sequence=sequence,
                    timestamp=now,
                    stream=stream,
                    text=line[:MAX_LIVE_LOG_EVENT_BYTES],
                )
            )
        return total, sequence

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
        if code and not stdout and not stderr:
            raise RuntimeError("container evidence is unavailable")
        if self._publish is None:
            return ContainerJobActivityResult()

        entries: list[ContainerJobEvidenceEntry] = []

        stdout_text, stdout_status = _bounded_log(stdout)
        stdout_bytes = stdout_text.encode("utf-8")
        logs_ref = await self._publish(
            request, f"{request.job_id}-stdout.log", stdout_bytes, "text/plain"
        )
        entries.append(
            _stream_entry("stdout", stdout_bytes, stdout_status, logs_ref)
        )

        stderr_text, stderr_status = _bounded_log(stderr)
        stderr_bytes = stderr_text.encode("utf-8")
        stderr_ref = await self._publish(
            request, f"{request.job_id}-stderr.log", stderr_bytes, "text/plain"
        )
        entries.append(
            _stream_entry("stderr", stderr_bytes, stderr_status, stderr_ref)
        )

        diagnostics_bytes = _diagnostics_payload(request)
        diagnostics_ref = await self._publish(
            request,
            f"{request.job_id}-diagnostics.json",
            diagnostics_bytes,
            "application/json",
        )
        entries.append(
            ContainerJobEvidenceEntry(
                name="diagnostics.json",
                kind="diagnostics",
                artifactRef=diagnostics_ref,
                sizeBytes=len(diagnostics_bytes),
                sha256=hashlib.sha256(diagnostics_bytes).hexdigest(),
                mediaType="application/json",
                collectionStatus=EvidenceCollectionStatus.COLLECTED,
            )
        )

        archive, output_entries = self._collect_outputs(request)
        artifacts_ref: str | None = None
        if archive is not None:
            artifacts_ref = await self._publish(
                request,
                f"{request.job_id}-outputs.tar.gz",
                archive,
                "application/gzip",
            )
        for entry in output_entries:
            if entry.collection_status == EvidenceCollectionStatus.COLLECTED:
                entry.artifact_ref = artifacts_ref
        entries.extend(output_entries)

        manifest = ContainerJobEvidenceManifest(jobId=request.job_id, entries=entries)
        manifest_bytes = manifest.model_dump_json(
            by_alias=True, exclude_none=True
        ).encode("utf-8")
        manifest_ref = await self._publish(
            request,
            f"{request.job_id}-manifest.json",
            manifest_bytes,
            "application/json",
        )
        # ``artifactsRef`` points at the manifest so the durable record indexes
        # every stream/diagnostics/output artifact while staying compact (AC5).
        return ContainerJobActivityResult(
            logsRef=logs_ref,
            artifactsRef=manifest_ref,
            manifestRef=manifest_ref,
            diagnosticsRef=diagnostics_ref,
        )

    def _collect_outputs(
        self, request: ContainerJobActivityRequest
    ) -> tuple[bytes | None, list[ContainerJobEvidenceEntry]]:
        """Validate and collect declared outputs after launch (AC5/AC6).

        Rejects traversal, symlink escape, unsupported file types, and per-file
        or aggregate size ceilings; records a manifest entry per declaration with
        its collection status rather than failing the whole publication.
        """

        outputs = request.request.spec.outputs
        if not outputs:
            return None, []
        workspace = Path(request.resolved_workspace_ref or "").resolve()
        real_workspace = os.path.realpath(workspace)
        entries: list[ContainerJobEvidenceEntry] = []
        archive = BytesIO()
        total_bytes = 0
        collected_any = False
        with tarfile.open(fileobj=archive, mode="w:gz") as bundle:
            for output in outputs:
                entry = ContainerJobEvidenceEntry(
                    name=output.name,
                    kind="output",
                    relativePath=output.relative_path,
                    mediaType="application/octet-stream",
                    collectionStatus=EvidenceCollectionStatus.MISSING,
                )
                entries.append(entry)
                candidate = (workspace / output.relative_path).resolve()
                # Lexical containment (OutputDeclaration already rejects '..'),
                # then defend against symlink escape via the real path.
                if not candidate.is_relative_to(workspace) or not _within(
                    candidate, real_workspace
                ):
                    entry.collection_status = EvidenceCollectionStatus.REJECTED
                    entry.detail = "declared output escapes the approved artifact root"
                    continue
                if not candidate.exists():
                    entry.detail = "declared output was not produced"
                    continue
                try:
                    members = _safe_members(candidate, real_workspace)
                except _OutputRejected as rejected:
                    entry.collection_status = EvidenceCollectionStatus.REJECTED
                    entry.detail = str(rejected)[:512]
                    continue
                declaration_bytes = sum(size for _, _, size in members)
                if any(size > MAX_OUTPUT_FILE_BYTES for _, _, size in members):
                    entry.collection_status = EvidenceCollectionStatus.REJECTED
                    entry.detail = "declared output exceeds the per-file size ceiling"
                    continue
                if total_bytes + declaration_bytes > MAX_OUTPUT_TOTAL_BYTES:
                    entry.collection_status = EvidenceCollectionStatus.REJECTED
                    entry.detail = "declared outputs exceed the aggregate size ceiling"
                    continue
                digest = hashlib.sha256()
                for abs_path, arc_suffix, _ in members:
                    arcname = (
                        output.name
                        if arc_suffix is None
                        else f"{output.name}/{arc_suffix}"
                    )
                    bundle.add(str(abs_path), arcname=arcname, recursive=False)
                    digest.update(abs_path.read_bytes())
                total_bytes += declaration_bytes
                collected_any = collected_any or bool(members)
                entry.size_bytes = declaration_bytes
                if candidate.is_dir():
                    entry.media_type = "application/x-directory"
                    entry.collection_status = (
                        EvidenceCollectionStatus.COLLECTED
                        if members
                        else EvidenceCollectionStatus.EMPTY
                    )
                else:
                    entry.media_type = (
                        mimetypes.guess_type(output.relative_path)[0]
                        or "application/octet-stream"
                    )
                    entry.sha256 = digest.hexdigest()
                    entry.collection_status = (
                        EvidenceCollectionStatus.COLLECTED
                        if declaration_bytes
                        else EvidenceCollectionStatus.EMPTY
                    )
        return (archive.getvalue() if collected_any else None), entries

    async def project_status(self, request: ContainerJobActivityRequest):
        if self._write_projection is None:
            raise RuntimeError("durable container-job projection writer is unavailable")
        await self._write_projection(request)
        return ContainerJobActivityResult(terminalState=request.terminal_state)

    async def repair_projection(self, request: ContainerJobActivityRequest):
        return await self.project_status(request)

    async def cleanup(self, request: ContainerJobActivityRequest):
        return ContainerJobActivityResult()
