"""Durable lifecycle orchestration for MoonLadderStudios/MoonMind#3253."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from moonmind.schemas.container_job_models import (
        ContainerJobActivityRequest,
        ContainerJobActivityResult,
        ContainerJobInput,
        ContainerJobSnapshot,
        TERMINAL_CONTAINER_JOB_STATES,
    )
    from moonmind.workflows.temporal.activity_catalog import (
        build_default_activity_catalog,
    )

CATALOG = build_default_activity_catalog()


@workflow.defn(name="MoonMind.ContainerJob")
class MoonMindContainerJobWorkflow:
    def __init__(self) -> None:
        self._snapshot: ContainerJobSnapshot | None = None
        self._cancel_requested = False

    @workflow.query(name="status")
    def status(self) -> dict[str, Any]:
        return (
            self._snapshot.model_dump(mode="json", by_alias=True)
            if self._snapshot
            else {}
        )

    @workflow.query(name="progress")
    def progress(self) -> dict[str, Any]:
        if self._snapshot is None:
            return {}
        return {
            "jobId": self._snapshot.job_id,
            "state": self._snapshot.state,
            "cancellationRequested": self._snapshot.cancellation_requested,
        }

    @workflow.signal(name="cancel")
    async def cancel(self) -> None:
        if (
            self._snapshot is None
            or self._snapshot.state not in TERMINAL_CONTAINER_JOB_STATES
        ):
            self._cancel_requested = True
            if self._snapshot is not None:
                self._snapshot.cancellation_requested = True

    async def _activity(
        self, name: str, request: ContainerJobActivityRequest
    ) -> ContainerJobActivityResult:
        route = CATALOG.resolve_activity(name)
        return await workflow.execute_activity(
            name,
            request.model_dump(mode="json", by_alias=True),
            task_queue=route.task_queue,
            start_to_close_timeout=timedelta(
                seconds=route.timeouts.start_to_close_seconds
            ),
            schedule_to_close_timeout=timedelta(
                seconds=route.timeouts.schedule_to_close_seconds
            ),
            retry_policy=RetryPolicy(
                maximum_attempts=route.retries.max_attempts,
                maximum_interval=timedelta(seconds=route.retries.max_interval_seconds),
                non_retryable_error_types=list(route.retries.non_retryable_error_codes),
            ),
        )

    async def _project(self, request: ContainerJobActivityRequest, state: str) -> None:
        self._snapshot.state = state  # type: ignore[assignment,union-attr]
        request.state = state  # type: ignore[assignment]
        try:
            await self._activity("container_job.project_status", request)
        except Exception:
            # Projection is repairable from workflow state and must not rewrite outcome.
            pass

    @workflow.run
    async def run(self, raw: dict[str, Any]) -> dict[str, Any]:
        inp = ContainerJobInput.model_validate(raw)
        self._snapshot = ContainerJobSnapshot(jobId=inp.job_id, state="queued")
        req = ContainerJobActivityRequest(
            jobId=inp.job_id,
            ownershipToken=inp.ownership_token,
            requestRef=inp.request_ref,
            workspaceRef=inp.workspace_ref,
            imageRef=inp.image_ref,
        )
        outcome = "failed"
        timed_out = False
        try:
            async with asyncio.timeout(inp.timeout_seconds):
                if not self._cancel_requested:
                    await self._project(req, "resolving_workspace")
                    resolved = await self._activity(
                        "container_job.resolve_workspace", req
                    )
                    req.workspace_ref = resolved.workspace_ref or req.workspace_ref
                if not self._cancel_requested:
                    await self._project(req, "acquiring_image")
                    image = await self._activity("container_job.acquire_image", req)
                    req.image_ref = image.image_ref or req.image_ref
                if not self._cancel_requested:
                    await self._project(req, "starting")
                    created = await self._activity(
                        "container_job.create_container", req
                    )
                    req.container_ref = created.container_ref
                    self._snapshot.container_ref = created.container_ref
                    await self._activity("container_job.start_container", req)
                while not self._cancel_requested and req.container_ref:
                    await self._project(req, "running")
                    observed = await self._activity(
                        "container_job.observe_container", req
                    )
                    if observed.terminal_state:
                        outcome = observed.terminal_state
                        break
                    await workflow.sleep(
                        timedelta(seconds=inp.observe_interval_seconds)
                    )
                if self._cancel_requested:
                    outcome = "canceled"
        except TimeoutError:
            timed_out = True
            outcome = "timed_out"
        except Exception:
            outcome = "failed"

        self._snapshot.primary_outcome = outcome  # type: ignore[assignment]
        if (self._cancel_requested or timed_out) and req.container_ref:
            await self._project(req, "canceling")
            try:
                await self._activity("container_job.stop_container", req)
            except Exception:
                self._snapshot.cleanup_diagnostics += ("stop_failed",)
        await self._project(req, "publishing_evidence")
        try:
            published = await self._activity("container_job.publish_evidence", req)
            self._snapshot.evidence_refs = published.evidence_refs
            self._snapshot.publication_diagnostics = published.diagnostic_codes
        except Exception:
            self._snapshot.publication_diagnostics += ("publication_failed",)
        await self._project(req, "cleaning_up")
        try:
            await self._activity("container_job.cleanup", req)
        except Exception:
            self._snapshot.cleanup_diagnostics += ("cleanup_failed",)
        await self._project(req, outcome)
        return self._snapshot.model_dump(mode="json", by_alias=True)
