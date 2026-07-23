"""Durable container-job lifecycle for MoonLadderStudios/MoonMind#3277."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

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
        failure_class_from_exception,
    )
    from moonmind.workflows.temporal.activity_catalog import (
        build_default_activity_catalog,
    )

CATALOG = build_default_activity_catalog()
_TERMINAL = frozenset({"succeeded", "failed", "canceled", "timed_out"})
# Granular image-acquisition failure classes surfaced by the trusted backend
# through the ApplicationError type, mapped back onto the durable outcome.
_IMAGE_FAILURE_TYPES = frozenset(
    {
        ContainerJobFailureClass.IMAGE.value,
        ContainerJobFailureClass.IMAGE_NOT_FOUND.value,
        ContainerJobFailureClass.IMAGE_PULL_TIMEOUT.value,
        ContainerJobFailureClass.IMAGE_PULL_AUTH_FAILED.value,
        ContainerJobFailureClass.IMAGE_BUILD_NOT_CONFIGURED.value,
        ContainerJobFailureClass.IMAGE_BUILD_INPUTS_UNAVAILABLE.value,
        ContainerJobFailureClass.IMAGE_BUILD_TIMEOUT.value,
        ContainerJobFailureClass.IMAGE_BUILD_FAILED.value,
        ContainerJobFailureClass.IMAGE_VALIDATION_FAILED.value,
        ContainerJobFailureClass.IMAGE_PLATFORM_MISMATCH.value,
        ContainerJobFailureClass.IMAGE_BACKEND_UNAVAILABLE.value,
        ContainerJobFailureClass.WORKSPACE.value,
    }
)


def _acquisition_failure_class(exc: BaseException) -> ContainerJobFailureClass | None:
    """Return the granular failure class carried by an acquisition error."""

    error: BaseException | None = exc
    if isinstance(error, ActivityError) and error.cause is not None:
        error = error.cause
    if isinstance(error, ApplicationError) and error.type in _IMAGE_FAILURE_TYPES:
        return ContainerJobFailureClass(error.type)
    return None


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
        start_to_close_seconds = route.timeouts.start_to_close_seconds
        schedule_to_close_seconds = route.timeouts.schedule_to_close_seconds
        if (
            name == "container_job.acquire_image"
            and request.request.spec.image_source_ref is not None
        ):
            # Existing direct-image histories retain the original five-minute
            # command shape. New deployment-owned local recipes receive a
            # bounded build budget without changing replay for in-flight jobs.
            start_to_close_seconds = min(
                1800, request.request.spec.timeout_seconds
            )
            schedule_to_close_seconds = min(
                2100, request.request.spec.timeout_seconds + 300
            )
        raw = await workflow.execute_activity(
            name,
            request.model_dump(mode="json", by_alias=True, exclude_none=True),
            task_queue=route.task_queue,
            start_to_close_timeout=timedelta(
                seconds=start_to_close_seconds
            ),
            schedule_to_close_timeout=timedelta(
                seconds=schedule_to_close_seconds
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
            registryAuthorization=inp.registry_authorization,
        )
        terminal_state = ContainerJobState.FAILED
        exit_code: int | None = None
        failure_class: ContainerJobFailureClass | None = ContainerJobFailureClass.INFRASTRUCTURE
        message: str | None = None

        async def execute_lifecycle() -> None:
            nonlocal terminal_state, exit_code, failure_class
            if not self._cancel_requested:
                await self._project(request, ContainerJobState.RESOLVING_WORKSPACE)
                resolved = await self._activity(
                    "container_job.resolve_workspace", request
                )
                request.resolved_workspace_ref = resolved.resolved_workspace_ref
                request.resolved_workspace_volume_name = (
                    resolved.resolved_workspace_volume_name
                )
                request.resolved_workspace_volume_subpath = (
                    resolved.resolved_workspace_volume_subpath
                )
                request.workspace_probe = resolved.workspace_probe
            if not self._cancel_requested:
                await self._project(request, ContainerJobState.ACQUIRING_IMAGE)
                image = await self._activity("container_job.acquire_image", request)
                request.resolved_image_ref = image.resolved_image_ref
                request.image_observation = image.image_observation
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
                    await self._project(request, ContainerJobState.STARTING)
                    await self._activity("container_job.start_container", request)
            while not self._cancel_requested and request.container_ref:
                await self._project(request, ContainerJobState.RUNNING)
                observed = await self._activity(
                    "container_job.observe_container", request
                )
                # Carry the resumable live-log cursor across polls so each poll
                # publishes only the newer log delta.
                request.log_cursor = observed.log_cursor
                if observed.terminal_state is not None:
                    terminal_state = observed.terminal_state
                    exit_code = observed.exit_code
                    request.started_at = observed.started_at
                    request.finished_at = observed.finished_at
                    request.duration_ms = observed.duration_ms
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
            # Preserve the trusted backend's specific failure class (denied image
            # use, unresolved credential, repository-scope mismatch, registry
            # auth failure) across the activity boundary; fall back to
            # infrastructure only when no class is carried.
            failure_class = (
                failure_class_from_exception(exc)
                or
                _acquisition_failure_class(exc)
                or ContainerJobFailureClass.INFRASTRUCTURE
            )
            message = str(exc)[:2048]

        request.terminal_state = terminal_state
        # Exit metadata is set before publication so the runtime-diagnostics
        # artifact records the authoritative exit/failure/timing evidence.
        request.exit_code = exit_code
        request.failure_class = failure_class
        request.message = message

        # Distinguish a workspace-visibility failure as its own status before the
        # terminal FAILED projection so a reader can tell it apart from a launch
        # or execution failure.
        if (
            terminal_state == ContainerJobState.FAILED
            and failure_class == ContainerJobFailureClass.WORKSPACE
        ):
            await self._project(request, ContainerJobState.WORKSPACE_NOT_VISIBLE)

        if request.container_ref and terminal_state in {
            ContainerJobState.CANCELED,
            ContainerJobState.TIMED_OUT,
        }:
            await self._project(request, ContainerJobState.CANCELING)
            await self._best_effort("container_job.stop_container", request)

        logs_ref: str | None = None
        artifacts_ref: str | None = None
        events_ref: str | None = None
        await self._project(request, ContainerJobState.PUBLISHING_ARTIFACTS)
        request.publication_token = f"{inp.ownership_token}:publication"
        published = await self._best_effort("container_job.publish_evidence", request)
        if published is None:
            publication = AuxiliaryOutcome(state="failed")
        else:
            publication = AuxiliaryOutcome(
                state="succeeded", diagnosticsRef=published.diagnostics_ref
            )
            logs_ref = published.logs_ref
            artifacts_ref = published.artifacts_ref
            events_ref = published.events_ref

        await self._project(request, ContainerJobState.CLEANING_UP)
        removed = await self._best_effort("container_job.remove_container", request)
        cleaned = await self._best_effort("container_job.cleanup", request)
        cleanup = AuxiliaryOutcome(
            state="succeeded" if removed is not None and cleaned is not None else "failed"
        )

        self._state = terminal_state
        request.publication = publication
        request.cleanup_outcome = cleanup
        request.logs_ref = logs_ref
        request.artifacts_ref = artifacts_ref
        request.events_ref = events_ref
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
