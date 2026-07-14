"""Durable container-job lifecycle for MoonLadderStudios/MoonMind#3277."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from moonmind.schemas.container_job_models import (
        AuxiliaryOutcome,
        ContainerJobActivityRequest,
        ContainerJobActivityResult,
        ContainerJobFailureClass,
        ContainerJobState,
        ContainerJobWorkflowInput,
        ContainerJobWorkflowResult,
        TerminalOutcome,
    )
    from moonmind.workflows.temporal.activity_catalog import (
        build_default_activity_catalog,
    )
    from moonmind.workloads.container_workspace import (
        CONTAINER_WORKSPACE_NOT_FOUND,
        CONTAINER_WORKSPACE_NOT_VISIBLE,
        CONTAINER_WORKSPACE_PERMISSION_DENIED,
    )

CATALOG = build_default_activity_catalog()
_TERMINAL = frozenset({"succeeded", "failed", "canceled", "timed_out"})
CONTAINER_JOB_WORKSPACE_PROBE_PATCH = "container_job_workspace_probe_v1"

# Stable workspace classifications mapped onto the durable failure taxonomy so
# a terminal outcome carries the right class regardless of whether the error
# arrives directly or wrapped by a Temporal activity error.
_WORKSPACE_FAILURE_CLASS = {
    CONTAINER_WORKSPACE_NOT_FOUND: ContainerJobFailureClass.WORKSPACE,
    CONTAINER_WORKSPACE_NOT_VISIBLE: ContainerJobFailureClass.WORKSPACE,
    CONTAINER_WORKSPACE_PERMISSION_DENIED: ContainerJobFailureClass.AUTHORIZATION,
}


def _classify_failure(exc: BaseException) -> ContainerJobFailureClass:
    """Map a lifecycle exception onto the durable container-job failure class.

    Recognizes the stable workspace classification whether it surfaces as a
    direct ``ContainerWorkspaceError`` (``failure_class`` attribute or a
    ``code:`` prefixed message) or as a Temporal ``ApplicationError`` whose
    ``type`` carries the stable code.
    """

    direct = getattr(exc, "failure_class", None)
    if isinstance(direct, ContainerJobFailureClass):
        return direct
    for candidate in (getattr(exc, "type", None), getattr(exc, "cause", None)):
        code = getattr(candidate, "type", candidate)
        if code in _WORKSPACE_FAILURE_CLASS:
            return _WORKSPACE_FAILURE_CLASS[code]
    text = str(exc)
    for code, failure_class in _WORKSPACE_FAILURE_CLASS.items():
        if text.startswith(f"{code}:") or f"{code}:" in text:
            return failure_class
    return ContainerJobFailureClass.INFRASTRUCTURE


@workflow.defn(name="MoonMind.ContainerJob")
class MoonMindContainerJobWorkflow:
    """Own one bounded job from acquisition through evidence and cleanup."""

    def __init__(self) -> None:
        self._state = ContainerJobState.QUEUED
        self._job_id: str | None = None
        self._cancel_requested = False
        self._projection_sequence = 0
        self._projection_repair_required = False

    @workflow.query(name="status")
    def status(self) -> dict[str, Any]:
        return {
            "jobId": self._job_id,
            "state": self._state,
            "cancellationRequested": self._cancel_requested,
            "projectionSequence": self._projection_sequence,
        }

    @workflow.signal(name="cancel")
    async def cancel(self) -> None:
        if self._state not in _TERMINAL:
            self._cancel_requested = True

    async def _activity(
        self, name: str, request: ContainerJobActivityRequest
    ) -> ContainerJobActivityResult:
        route = CATALOG.resolve_activity(name)
        raw = await workflow.execute_activity(
            name,
            request.model_dump(mode="json", by_alias=True, exclude_none=True),
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
        return ContainerJobActivityResult.model_validate(raw)

    async def _project(
        self, request: ContainerJobActivityRequest, state: ContainerJobState
    ) -> None:
        self._state = state
        self._projection_sequence += 1
        request.state = state
        request.projection_sequence = self._projection_sequence
        try:
            await self._activity("container_job.project_status", request)
        except Exception:
            self._projection_repair_required = True

    async def _best_effort(
        self,
        name: str,
        request: ContainerJobActivityRequest,
    ) -> ContainerJobActivityResult | None:
        try:
            return await self._activity(name, request)
        except Exception:
            return None

    @workflow.run
    async def run(self, raw: dict[str, Any]) -> dict[str, Any]:
        inp = ContainerJobWorkflowInput.model_validate(raw)
        self._job_id = inp.job_id
        request = ContainerJobActivityRequest(
            jobId=inp.job_id,
            owner=inp.owner,
            ownershipToken=inp.ownership_token,
            request=inp.request,
        )
        terminal_state = ContainerJobState.FAILED
        exit_code: int | None = None
        failure_class: ContainerJobFailureClass | None = ContainerJobFailureClass.INFRASTRUCTURE
        message: str | None = None

        async def execute_lifecycle() -> None:
            nonlocal terminal_state, exit_code, failure_class
            if not self._cancel_requested:
                await self._project(request, ContainerJobState.PREPARING)
                resolved = await self._activity(
                    "container_job.resolve_workspace", request
                )
                request.resolved_workspace_ref = resolved.resolved_workspace_ref
                # Prove the selected daemon can see the resolved workspace before
                # any (potentially large) image is acquired. A workspace visible
                # to the API/agent but not the daemon fails workspace_not_visible
                # here, before image acquisition.
                if workflow.patched(CONTAINER_JOB_WORKSPACE_PROBE_PATCH):
                    await self._activity("container_job.probe_workspace", request)
            if not self._cancel_requested:
                await self._project(request, ContainerJobState.ACQUIRING_IMAGE)
                image = await self._activity("container_job.acquire_image", request)
                request.resolved_image_ref = image.resolved_image_ref
            if not self._cancel_requested:
                reconciled = await self._activity(
                    "container_job.reconcile_container", request
                )
                request.container_ref = reconciled.container_ref
                if request.container_ref is None:
                    created = await self._activity(
                        "container_job.create_container", request
                    )
                    request.container_ref = created.container_ref
                if not reconciled.running:
                    await self._activity("container_job.start_container", request)
            while not self._cancel_requested and request.container_ref:
                await self._project(request, ContainerJobState.RUNNING)
                observed = await self._activity(
                    "container_job.observe_container", request
                )
                if observed.terminal_state is not None:
                    terminal_state = observed.terminal_state
                    exit_code = observed.exit_code
                    failure_class = (
                        None
                        if terminal_state == ContainerJobState.SUCCEEDED
                        else ContainerJobFailureClass.EXECUTION
                    )
                    break
                try:
                    await workflow.wait_condition(
                        lambda: self._cancel_requested,
                        timeout=timedelta(seconds=inp.observe_interval_seconds),
                    )
                except TimeoutError:
                    # The interval elapsed normally; continue polling the container.
                    pass
            if self._cancel_requested:
                terminal_state = ContainerJobState.CANCELED
                failure_class = ContainerJobFailureClass.CANCELED
        try:
            await asyncio.wait_for(
                execute_lifecycle(), timeout=inp.request.spec.timeout_seconds
            )
        except TimeoutError:
            terminal_state = ContainerJobState.TIMED_OUT
            failure_class = ContainerJobFailureClass.TIMEOUT
            message = "container job exceeded its timeout"
        except Exception as exc:
            terminal_state = ContainerJobState.FAILED
            failure_class = _classify_failure(exc)
            message = str(exc)[:2048]

        request.terminal_state = terminal_state
        if request.container_ref and terminal_state in {
            ContainerJobState.CANCELED,
            ContainerJobState.TIMED_OUT,
        }:
            await self._project(request, ContainerJobState.CANCELING)
            await self._best_effort("container_job.stop_container", request)

        logs_ref: str | None = None
        artifacts_ref: str | None = None
        request.publication_token = f"{inp.ownership_token}:publication"
        published = await self._best_effort("container_job.publish_evidence", request)
        if published is None:
            publication = AuxiliaryOutcome(state="failed")
        else:
            publication = AuxiliaryOutcome(state="succeeded")
            logs_ref = published.logs_ref
            artifacts_ref = published.artifacts_ref

        removed = await self._best_effort("container_job.remove_container", request)
        cleaned = await self._best_effort("container_job.cleanup", request)
        cleanup = AuxiliaryOutcome(
            state="succeeded" if removed is not None and cleaned is not None else "failed"
        )

        self._state = terminal_state
        request.exit_code = exit_code
        request.failure_class = failure_class
        request.message = message
        request.publication = publication
        request.cleanup_outcome = cleanup
        request.logs_ref = logs_ref
        request.artifacts_ref = artifacts_ref
        await self._project(request, terminal_state)
        if self._projection_repair_required:
            repaired = await self._best_effort(
                "container_job.repair_projection", request
            )
            self._projection_repair_required = repaired is None

        result = ContainerJobWorkflowResult(
            jobId=inp.job_id,
            state=terminal_state,
            terminal=TerminalOutcome(
                exitCode=exit_code,
                failureClass=failure_class,
                message=message,
            ),
            publication=publication,
            cleanup=cleanup,
            logsRef=logs_ref,
            artifactsRef=artifacts_ref,
            projectionSequence=self._projection_sequence,
            projectionRepairRequired=self._projection_repair_required,
        )
        return result.model_dump(mode="json", by_alias=True, exclude_none=True)
