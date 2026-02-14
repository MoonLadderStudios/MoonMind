"""Daemon loop and queue API client for the standalone Codex worker."""

from __future__ import annotations

import asyncio
import hashlib
import socket
from contextlib import suppress
from dataclasses import dataclass
from os import environ
from pathlib import Path
from typing import Any, Mapping, Sequence
from uuid import UUID

import httpx

from moonmind.agents.codex_worker.handlers import ArtifactUpload, CodexExecHandler


class QueueClientError(RuntimeError):
    """Raised when queue API requests fail."""


@dataclass(frozen=True, slots=True)
class CodexWorkerConfig:
    """Runtime configuration for the standalone Codex worker."""

    moonmind_url: str
    worker_id: str
    worker_token: str | None
    poll_interval_ms: int
    lease_seconds: int
    workdir: Path
    allowed_types: tuple[str, ...] = ("codex_exec",)
    worker_capabilities: tuple[str, ...] = ()

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "CodexWorkerConfig":
        """Load worker settings from environment variables."""

        source = env or environ
        moonmind_url = str(source.get("MOONMIND_URL", "")).strip()
        if not moonmind_url:
            raise ValueError("MOONMIND_URL must be configured")

        worker_id = (
            str(source.get("MOONMIND_WORKER_ID", "")).strip() or socket.gethostname()
        )
        poll_interval_ms = int(
            str(source.get("MOONMIND_POLL_INTERVAL_MS", "1500")).strip()
        )
        lease_seconds = int(str(source.get("MOONMIND_LEASE_SECONDS", "120")).strip())
        if poll_interval_ms < 1:
            raise ValueError("MOONMIND_POLL_INTERVAL_MS must be >= 1")
        if lease_seconds < 1:
            raise ValueError("MOONMIND_LEASE_SECONDS must be >= 1")

        workdir_raw = (
            str(source.get("MOONMIND_WORKDIR", "var/worker")).strip() or "var/worker"
        )
        worker_token = str(source.get("MOONMIND_WORKER_TOKEN", "")).strip() or None
        capability_csv = str(source.get("MOONMIND_WORKER_CAPABILITIES", "")).strip()
        worker_capabilities = tuple(
            dict.fromkeys(
                [item.strip() for item in capability_csv.split(",") if item.strip()]
            )
        )
        return cls(
            moonmind_url=moonmind_url.rstrip("/"),
            worker_id=worker_id,
            worker_token=worker_token,
            poll_interval_ms=poll_interval_ms,
            lease_seconds=lease_seconds,
            workdir=Path(workdir_raw),
            worker_capabilities=worker_capabilities,
        )


@dataclass(frozen=True, slots=True)
class ClaimedJob:
    """Normalized job returned by queue claim API."""

    id: UUID
    type: str
    payload: dict[str, Any]


class QueueApiClient:
    """HTTP client wrapper for queue and artifact endpoints."""

    def __init__(
        self,
        *,
        base_url: str,
        worker_token: str | None,
        timeout_seconds: float = 30.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        headers: dict[str, str] = {"Accept": "application/json"}
        if worker_token:
            headers["X-MoonMind-Worker-Token"] = worker_token
        self._client = client or httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout_seconds,
            headers=headers,
        )
        self._owns_client = client is None

    async def aclose(self) -> None:
        """Close the underlying HTTP client when owned by this wrapper."""

        if self._owns_client:
            await self._client.aclose()

    async def claim_job(
        self,
        *,
        worker_id: str,
        lease_seconds: int,
        allowed_types: Sequence[str] | None = None,
        worker_capabilities: Sequence[str] | None = None,
    ) -> ClaimedJob | None:
        payload: dict[str, Any] = {
            "workerId": worker_id,
            "leaseSeconds": lease_seconds,
        }
        if allowed_types:
            payload["allowedTypes"] = list(allowed_types)
        if worker_capabilities:
            payload["workerCapabilities"] = list(worker_capabilities)
        data = await self._post_json("/api/queue/jobs/claim", json=payload)
        job_data = data.get("job")
        if not job_data:
            return None
        return ClaimedJob(
            id=UUID(str(job_data["id"])),
            type=str(job_data["type"]),
            payload=dict(job_data.get("payload") or {}),
        )

    async def heartbeat(
        self,
        *,
        job_id: UUID,
        worker_id: str,
        lease_seconds: int,
    ) -> None:
        await self._post_json(
            f"/api/queue/jobs/{job_id}/heartbeat",
            json={"workerId": worker_id, "leaseSeconds": lease_seconds},
        )

    async def complete_job(
        self,
        *,
        job_id: UUID,
        worker_id: str,
        result_summary: str | None,
    ) -> None:
        payload = {"workerId": worker_id}
        if result_summary:
            payload["resultSummary"] = result_summary
        await self._post_json(f"/api/queue/jobs/{job_id}/complete", json=payload)

    async def fail_job(
        self,
        *,
        job_id: UUID,
        worker_id: str,
        error_message: str,
        retryable: bool = False,
    ) -> None:
        await self._post_json(
            f"/api/queue/jobs/{job_id}/fail",
            json={
                "workerId": worker_id,
                "errorMessage": error_message,
                "retryable": retryable,
            },
        )

    async def append_event(
        self,
        *,
        job_id: UUID,
        worker_id: str,
        level: str,
        message: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        body: dict[str, Any] = {
            "workerId": worker_id,
            "level": level,
            "message": message,
        }
        if payload is not None:
            body["payload"] = payload
        await self._post_json(f"/api/queue/jobs/{job_id}/events", json=body)

    async def upload_artifact(
        self,
        *,
        job_id: UUID,
        worker_id: str,
        artifact: ArtifactUpload,
    ) -> None:
        if not artifact.path.exists():
            raise QueueClientError(f"artifact file does not exist: {artifact.path}")

        content_type = artifact.content_type or "application/octet-stream"
        data: dict[str, str] = {
            "name": artifact.name,
            "workerId": worker_id,
        }
        if artifact.content_type:
            data["contentType"] = artifact.content_type

        digest = artifact.digest or self._sha256_file(artifact.path)
        if digest:
            data["digest"] = digest

        with artifact.path.open("rb") as handle:
            files = {
                "file": (artifact.path.name, handle, content_type),
            }
            try:
                response = await self._client.post(
                    f"/api/queue/jobs/{job_id}/artifacts/upload",
                    data=data,
                    files=files,
                )
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise QueueClientError(
                    f"artifact upload failed for job {job_id}: {exc}"
                ) from exc

    async def _post_json(self, path: str, *, json: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self._client.post(path, json=json)
            response.raise_for_status()
            return dict(response.json()) if response.content else {}
        except httpx.HTTPError as exc:
            raise QueueClientError(f"queue API request failed: {path}: {exc}") from exc

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(8192), b""):
                digest.update(chunk)
        return f"sha256:{digest.hexdigest()}"


class CodexWorker:
    """Single-worker daemon that claims and executes queue jobs."""

    def __init__(
        self,
        *,
        config: CodexWorkerConfig,
        queue_client: QueueApiClient,
        codex_exec_handler: CodexExecHandler,
    ) -> None:
        self._config = config
        self._queue_client = queue_client
        self._codex_exec_handler = codex_exec_handler

    async def run_forever(self, *, stop_event: asyncio.Event | None = None) -> None:
        """Continuously process queue jobs until asked to stop."""

        run_stop = stop_event or asyncio.Event()
        while not run_stop.is_set():
            try:
                claimed_work = await self.run_once()
            except Exception:
                await asyncio.sleep(self._config.poll_interval_ms / 1000.0)
                continue
            if claimed_work:
                continue
            await asyncio.sleep(self._config.poll_interval_ms / 1000.0)

    async def run_once(self) -> bool:
        """Claim and process one job if available."""

        job = await self._claim_next_job()
        if job is None:
            return False

        await self._emit_event(
            job_id=job.id,
            level="info",
            message="Worker claimed job",
            payload={"jobType": job.type},
        )

        if job.type != "codex_exec":
            await self._emit_event(
                job_id=job.id,
                level="error",
                message="Unsupported job type",
                payload={"jobType": job.type},
            )
            await self._queue_client.fail_job(
                job_id=job.id,
                worker_id=self._config.worker_id,
                error_message=f"unsupported job type: {job.type}",
                retryable=False,
            )
            return True

        heartbeat_stop = asyncio.Event()
        heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(job_id=job.id, stop_event=heartbeat_stop)
        )

        try:
            await self._emit_event(
                job_id=job.id,
                level="info",
                message="Starting codex_exec handler",
            )
            result = await self._codex_exec_handler.handle(
                job_id=job.id,
                payload=job.payload,
            )
            await self._upload_artifacts(job_id=job.id, artifacts=result.artifacts)
            if result.succeeded:
                await self._queue_client.complete_job(
                    job_id=job.id,
                    worker_id=self._config.worker_id,
                    result_summary=result.summary,
                )
                await self._emit_event(
                    job_id=job.id,
                    level="info",
                    message="Job completed",
                    payload={"summary": result.summary},
                )
            else:
                await self._queue_client.fail_job(
                    job_id=job.id,
                    worker_id=self._config.worker_id,
                    error_message=result.error_message or "codex_exec failed",
                    retryable=False,
                )
                await self._emit_event(
                    job_id=job.id,
                    level="error",
                    message="Job failed",
                    payload={"error": result.error_message or "codex_exec failed"},
                )
        except Exception as exc:
            await self._queue_client.fail_job(
                job_id=job.id,
                worker_id=self._config.worker_id,
                error_message=str(exc),
                retryable=False,
            )
            await self._emit_event(
                job_id=job.id,
                level="error",
                message="Worker exception while executing job",
                payload={"error": str(exc)},
            )
        finally:
            heartbeat_stop.set()
            heartbeat_task.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat_task

        return True

    async def _claim_next_job(self) -> ClaimedJob | None:
        """Claim next eligible job using policy-safe claim parameters."""

        # Repository allowlist enforcement stays server-side; worker only forwards
        # allowed job types and capabilities from its local runtime config.
        return await self._queue_client.claim_job(
            worker_id=self._config.worker_id,
            lease_seconds=self._config.lease_seconds,
            allowed_types=self._config.allowed_types,
            worker_capabilities=self._config.worker_capabilities,
        )

    async def _heartbeat_loop(self, *, job_id: UUID, stop_event: asyncio.Event) -> None:
        """Send lease renewals while a job is actively executing."""

        interval_seconds = max(1.0, self._config.lease_seconds / 3.0)
        while not stop_event.is_set():
            await asyncio.sleep(interval_seconds)
            if stop_event.is_set():
                return
            try:
                await self._queue_client.heartbeat(
                    job_id=job_id,
                    worker_id=self._config.worker_id,
                    lease_seconds=self._config.lease_seconds,
                )
            except Exception:
                # Heartbeat errors are tolerated so terminal transition can still run.
                continue

    async def _upload_artifacts(
        self,
        *,
        job_id: UUID,
        artifacts: Sequence[ArtifactUpload],
    ) -> None:
        for artifact in artifacts:
            await self._queue_client.upload_artifact(
                job_id=job_id,
                worker_id=self._config.worker_id,
                artifact=artifact,
            )

    async def _emit_event(
        self,
        *,
        job_id: UUID,
        level: str,
        message: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """Best-effort event emission for streaming-ish worker logs."""

        try:
            await self._queue_client.append_event(
                job_id=job_id,
                worker_id=self._config.worker_id,
                level=level,
                message=message,
                payload=payload,
            )
        except Exception:
            # Event publication failures should not break terminal job transitions.
            return


__all__ = [
    "ClaimedJob",
    "CodexWorker",
    "CodexWorkerConfig",
    "QueueApiClient",
    "QueueClientError",
]
