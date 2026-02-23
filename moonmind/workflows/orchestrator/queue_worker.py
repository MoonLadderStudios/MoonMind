"""DB queue worker for orchestrator runs."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import socket
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import httpx

from api_service.db import models as db_models
from moonmind.workflows.agent_queue.job_types import ORCHESTRATOR_RUN_JOB_TYPE
from moonmind.workflows.orchestrator.tasks import _execute_plan_step_async

logger = logging.getLogger(__name__)


class QueueClientError(RuntimeError):
    """Raised when queue API requests fail."""


@dataclass(frozen=True, slots=True)
class QueueWorkerConfig:
    """Runtime configuration for the orchestrator DB queue worker."""

    moonmind_url: str
    worker_id: str
    worker_token: str
    poll_interval_ms: int
    lease_seconds: int
    allowed_types: tuple[str, ...]
    worker_capabilities: tuple[str, ...]

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "QueueWorkerConfig":
        source = env or os.environ
        moonmind_url = str(source.get("MOONMIND_URL", "")).strip()
        if not moonmind_url:
            raise ValueError("MOONMIND_URL must be configured")

        worker_id = (
            str(source.get("MOONMIND_WORKER_ID", "")).strip() or socket.gethostname()
        )
        worker_token = str(source.get("MOONMIND_WORKER_TOKEN", "")).strip()
        if not worker_token:
            raise ValueError("MOONMIND_WORKER_TOKEN must be configured")

        poll_interval_ms = int(str(source.get("MOONMIND_POLL_INTERVAL_MS", "1500")))
        if poll_interval_ms < 1:
            raise ValueError("MOONMIND_POLL_INTERVAL_MS must be >= 1")
        lease_seconds = int(str(source.get("MOONMIND_LEASE_SECONDS", "120")))
        if lease_seconds < 1:
            raise ValueError("MOONMIND_LEASE_SECONDS must be >= 1")

        allowed_types_csv = (
            str(source.get("MOONMIND_WORKER_ALLOWED_TYPES", "")).strip()
            or ORCHESTRATOR_RUN_JOB_TYPE
        )
        allowed_types = tuple(
            dict.fromkeys(
                [item.strip() for item in allowed_types_csv.split(",") if item.strip()]
            )
        )
        if ORCHESTRATOR_RUN_JOB_TYPE not in allowed_types:
            allowed_types = (*allowed_types, ORCHESTRATOR_RUN_JOB_TYPE)

        caps_csv = (
            str(source.get("MOONMIND_WORKER_CAPABILITIES", "")).strip()
            or "orchestrator"
        )
        worker_capabilities = tuple(
            dict.fromkeys([item.strip() for item in caps_csv.split(",") if item.strip()])
        )
        if "orchestrator" not in {item.lower() for item in worker_capabilities}:
            worker_capabilities = (*worker_capabilities, "orchestrator")

        return cls(
            moonmind_url=moonmind_url.rstrip("/"),
            worker_id=worker_id,
            worker_token=worker_token,
            poll_interval_ms=poll_interval_ms,
            lease_seconds=lease_seconds,
            allowed_types=allowed_types,
            worker_capabilities=worker_capabilities,
        )


@dataclass(frozen=True, slots=True)
class ClaimedJob:
    """Minimal claimed job payload from the queue API."""

    id: UUID
    type: str
    payload: dict[str, Any]


class QueueApiClient:
    """HTTP wrapper for queue job claim/mutate endpoints."""

    def __init__(self, *, base_url: str, worker_token: str) -> None:
        headers = {
            "Accept": "application/json",
            "X-MoonMind-Worker-Token": worker_token,
        }
        self._client = httpx.AsyncClient(base_url=base_url, timeout=30.0, headers=headers)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def claim_job(
        self,
        *,
        worker_id: str,
        lease_seconds: int,
        allowed_types: tuple[str, ...],
        worker_capabilities: tuple[str, ...],
    ) -> ClaimedJob | None:
        body = {
            "workerId": worker_id,
            "leaseSeconds": lease_seconds,
            "allowedTypes": list(allowed_types),
            "workerCapabilities": list(worker_capabilities),
        }
        data = await self._post_json("/api/queue/jobs/claim", json=body)
        job_data = data.get("job")
        if not isinstance(job_data, dict):
            return None
        return ClaimedJob(
            id=UUID(str(job_data["id"])),
            type=str(job_data["type"]),
            payload=dict(job_data.get("payload") or {}),
        )

    async def heartbeat(self, *, job_id: UUID, worker_id: str, lease_seconds: int) -> None:
        await self._post_json(
            f"/api/queue/jobs/{job_id}/heartbeat",
            json={"workerId": worker_id, "leaseSeconds": lease_seconds},
        )

    async def complete_job(
        self,
        *,
        job_id: UUID,
        worker_id: str,
        result_summary: str | None = None,
    ) -> None:
        body: dict[str, Any] = {"workerId": worker_id}
        if result_summary:
            body["resultSummary"] = result_summary
        await self._post_json(f"/api/queue/jobs/{job_id}/complete", json=body)

    async def fail_job(
        self,
        *,
        job_id: UUID,
        worker_id: str,
        error_message: str,
    ) -> None:
        await self._post_json(
            f"/api/queue/jobs/{job_id}/fail",
            json={
                "workerId": worker_id,
                "errorMessage": error_message,
                "retryable": False,
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

    async def _post_json(self, path: str, *, json: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self._client.post(path, json=json)
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPError as exc:
            raise QueueClientError(f"queue API request failed: {path}: {exc}") from exc
        if isinstance(payload, dict):
            return payload
        raise QueueClientError(f"queue API response for {path} was not a JSON object")


class OrchestratorQueueWorker:
    """Single-purpose worker that executes orchestrator runs from DB queue jobs."""

    def __init__(self, *, config: QueueWorkerConfig, queue_client: QueueApiClient) -> None:
        self._config = config
        self._queue = queue_client

    async def run_forever(self, *, stop_event: asyncio.Event | None = None) -> None:
        stopper = stop_event or asyncio.Event()
        while not stopper.is_set():
            try:
                claimed = await self._queue.claim_job(
                    worker_id=self._config.worker_id,
                    lease_seconds=self._config.lease_seconds,
                    allowed_types=self._config.allowed_types,
                    worker_capabilities=self._config.worker_capabilities,
                )
            except Exception:
                logger.exception("Queue claim failed for orchestrator worker")
                await asyncio.sleep(self._config.poll_interval_ms / 1000.0)
                continue

            if claimed is None:
                await asyncio.sleep(self._config.poll_interval_ms / 1000.0)
                continue

            await self._process_job(claimed)

    async def _process_job(self, job: ClaimedJob) -> None:
        if job.type != ORCHESTRATOR_RUN_JOB_TYPE:
            await self._queue.fail_job(
                job_id=job.id,
                worker_id=self._config.worker_id,
                error_message=f"unsupported job type: {job.type}",
            )
            return

        try:
            run_id, steps, include_rollback = self._parse_payload(job.payload)
        except Exception as exc:
            await self._queue.fail_job(
                job_id=job.id,
                worker_id=self._config.worker_id,
                error_message=f"invalid orchestrator payload: {exc}",
            )
            return

        await self._queue.append_event(
            job_id=job.id,
            worker_id=self._config.worker_id,
            level="info",
            message="Executing orchestrator run from DB queue",
            payload={
                "runId": str(run_id),
                "steps": [step.value for step in steps],
                "includeRollback": include_rollback,
            },
        )

        heartbeat_stop = asyncio.Event()
        heartbeat_task = asyncio.create_task(self._heartbeat_loop(job.id, heartbeat_stop))
        failure_message: str | None = None

        try:
            for step in steps:
                await self._queue.append_event(
                    job_id=job.id,
                    worker_id=self._config.worker_id,
                    level="info",
                    message="Starting orchestrator step",
                    payload={"runId": str(run_id), "step": step.value},
                )
                await _execute_plan_step_async(run_id, step.value)
                await self._queue.append_event(
                    job_id=job.id,
                    worker_id=self._config.worker_id,
                    level="info",
                    message="Completed orchestrator step",
                    payload={"runId": str(run_id), "step": step.value},
                )
        except Exception as exc:
            failure_message = f"orchestrator step failed: {exc}"
            logger.exception("Orchestrator run %s failed while processing queue job", run_id)
            if include_rollback:
                try:
                    await self._queue.append_event(
                        job_id=job.id,
                        worker_id=self._config.worker_id,
                        level="warn",
                        message="Attempting rollback after orchestrator step failure",
                        payload={"runId": str(run_id)},
                    )
                    await _execute_plan_step_async(
                        run_id, db_models.OrchestratorPlanStep.ROLLBACK.value
                    )
                except Exception as rollback_exc:
                    logger.exception(
                        "Rollback failed for orchestrator run %s after queue failure",
                        run_id,
                    )
                    failure_message = f"{failure_message}; rollback failed: {rollback_exc}"
        finally:
            heartbeat_stop.set()
            await heartbeat_task

        if failure_message is not None:
            await self._queue.fail_job(
                job_id=job.id,
                worker_id=self._config.worker_id,
                error_message=failure_message,
            )
            return

        await self._queue.complete_job(
            job_id=job.id,
            worker_id=self._config.worker_id,
            result_summary=f"orchestrator run {run_id} completed",
        )

    async def _heartbeat_loop(self, job_id: UUID, stop_event: asyncio.Event) -> None:
        interval = max(1.0, self._config.lease_seconds / 3.0)
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval)
                break
            except TimeoutError:
                pass
            try:
                await self._queue.heartbeat(
                    job_id=job_id,
                    worker_id=self._config.worker_id,
                    lease_seconds=self._config.lease_seconds,
                )
            except Exception:
                logger.exception("Failed to heartbeat orchestrator queue job %s", job_id)

    @staticmethod
    def _parse_payload(
        payload: dict[str, Any],
    ) -> tuple[UUID, list[db_models.OrchestratorPlanStep], bool]:
        run_id_raw = payload.get("runId") or payload.get("run_id")
        run_id = UUID(str(run_id_raw))

        steps_raw = payload.get("steps")
        if not isinstance(steps_raw, list) or not steps_raw:
            raise ValueError("steps must be a non-empty list")

        steps: list[db_models.OrchestratorPlanStep] = []
        for item in steps_raw:
            step = db_models.OrchestratorPlanStep(str(item))
            if step is db_models.OrchestratorPlanStep.ROLLBACK:
                continue
            steps.append(step)
        if not steps:
            raise ValueError("steps must contain at least one non-rollback step")

        include_rollback = bool(payload.get("includeRollback"))
        return run_id, steps, include_rollback


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="moonmind-orchestrator-queue-worker")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Claim and process at most one DB queue job.",
    )
    return parser


async def _run(args: argparse.Namespace) -> int:
    config = QueueWorkerConfig.from_env()
    client = QueueApiClient(base_url=config.moonmind_url, worker_token=config.worker_token)
    worker = OrchestratorQueueWorker(config=config, queue_client=client)
    try:
        if args.once:
            claimed = await client.claim_job(
                worker_id=config.worker_id,
                lease_seconds=config.lease_seconds,
                allowed_types=config.allowed_types,
                worker_capabilities=config.worker_capabilities,
            )
            if claimed is None:
                return 0
            await worker._process_job(claimed)
            return 0
        await worker.run_forever()
        return 0
    finally:
        await client.aclose()


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper())
    try:
        return asyncio.run(_run(args))
    except Exception as exc:
        parser.exit(status=1, message=f"moonmind-orchestrator-queue-worker failed: {exc}\n")
    return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

