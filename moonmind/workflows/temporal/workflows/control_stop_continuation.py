"""Temporal entrypoint for a control-stop remediation continuation."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from temporalio import exceptions, workflow
from temporalio.common import RetryPolicy

from moonmind.schemas.agent_runtime_models import AgentRunResult
from moonmind.schemas.checkpoint_restore_models import ManagedWorkspaceRestoreResult
from moonmind.schemas.managed_checkpoint_models import (
    ManagedWorkspaceCheckpointCaptureResult,
)
from moonmind.workflows.executions.control_stop_continuation import (
    ControlStopContinuationContract,
    ControlStopContinuationError,
)
from moonmind.workflows.temporal.activity_catalog import build_default_activity_catalog

WORKFLOW_NAME = "MoonMind.ControlStopContinuation"
_RESTORE_ACTIVITY = "agent_runtime.restore_workspace_checkpoint"
_REMEDIATE_ACTIVITY = "integration.omnigent.profile_bound_execute"
_CAPTURE_ACTIVITY = "agent_runtime.capture_workspace_checkpoint"


@workflow.defn(name=WORKFLOW_NAME)
class MoonMindControlStopContinuationWorkflow:
    """Restore the frozen cumulative head before any new semantic operation."""

    @workflow.run
    async def run(self, input_payload: dict[str, Any]) -> dict[str, Any]:
        try:
            contract = ControlStopContinuationContract.model_validate(input_payload)
        except Exception as exc:
            raise exceptions.ApplicationError(
                "invalid control-stop continuation contract",
                type="CONTROL_STOP_CONTINUATION_INVALID",
                non_retryable=True,
            ) from exc

        info = workflow.info()
        if info.workflow_id != contract.destination_workflow_id:
            raise exceptions.ApplicationError(
                "destination workflow identity does not match frozen contract",
                type="CONTROL_STOP_DESTINATION_IDENTITY_MISMATCH",
                non_retryable=True,
            )

        route = build_default_activity_catalog().resolve_activity(_RESTORE_ACTIVITY)
        restored_payload = await workflow.execute_activity(
            _RESTORE_ACTIVITY,
            contract.restore_request(destination_run_id=info.run_id),
            task_queue=route.task_queue,
            start_to_close_timeout=timedelta(
                seconds=route.timeouts.start_to_close_seconds
            ),
            schedule_to_close_timeout=timedelta(
                seconds=route.timeouts.schedule_to_close_seconds
            ),
            heartbeat_timeout=timedelta(
                seconds=route.timeouts.heartbeat_timeout_seconds
            ),
            retry_policy=RetryPolicy(
                initial_interval=timedelta(seconds=5),
                backoff_coefficient=2.0,
                maximum_interval=timedelta(seconds=route.retries.max_interval_seconds),
                maximum_attempts=route.retries.max_attempts,
                non_retryable_error_types=list(
                    route.retries.non_retryable_error_codes
                ),
            ),
        )
        restored = ManagedWorkspaceRestoreResult.model_validate(restored_payload)
        expected_key = f"{contract.destination_workflow_id}:restore"
        if (
            restored.checkpoint_ref != contract.workspace_head_ref
            or restored.base_commit != contract.workspace_base_commit
            or restored.idempotency_key != expected_key
            or restored.destination_workspace_locator.agent_run_id
            != contract.destination_workspace_id
        ):
            raise exceptions.ApplicationError(
                "workspace restoration result does not match frozen contract",
                type="CONTROL_STOP_RESTORE_EVIDENCE_MISMATCH",
                non_retryable=True,
            )

        locator = restored.destination_workspace_locator.model_dump(
            by_alias=True, mode="json"
        )
        budget = contract.continuation_budget
        attempts: list[dict[str, Any]] = []
        latest_head_ref = contract.workspace_head_ref
        remaining_work_ref = contract.remaining_work_ref
        terminal_status = "control_stop"

        async def execute(activity_name: str, payload: dict[str, Any]) -> Any:
            activity_route = build_default_activity_catalog().resolve_activity(
                activity_name
            )
            return await workflow.execute_activity(
                activity_name,
                payload,
                task_queue=activity_route.task_queue,
                start_to_close_timeout=timedelta(
                    seconds=activity_route.timeouts.start_to_close_seconds
                ),
                schedule_to_close_timeout=timedelta(
                    seconds=activity_route.timeouts.schedule_to_close_seconds
                ),
                heartbeat_timeout=timedelta(
                    seconds=activity_route.timeouts.heartbeat_timeout_seconds
                ),
                retry_policy=RetryPolicy(
                    initial_interval=timedelta(seconds=5),
                    backoff_coefficient=2.0,
                    maximum_interval=timedelta(
                        seconds=activity_route.retries.max_interval_seconds
                    ),
                    maximum_attempts=activity_route.retries.max_attempts,
                    non_retryable_error_types=list(
                        activity_route.retries.non_retryable_error_codes
                    ),
                ),
            )

        while budget.consumed_attempts < budget.max_attempts:
            attempt = (
                contract.source_budget.consumed_attempts
                + budget.consumed_attempts
                + 1
            )
            remediation = AgentRunResult.model_validate(
                await execute(
                    _REMEDIATE_ACTIVITY,
                    contract.remediation_request(
                        destination_run_id=info.run_id,
                        destination_workspace_locator=locator,
                        attempt=attempt,
                        workspace_head_ref=latest_head_ref,
                        remaining_work_ref=remaining_work_ref,
                    ),
                )
            )
            capture = ManagedWorkspaceCheckpointCaptureResult.model_validate(
                await execute(
                    _CAPTURE_ACTIVITY,
                    contract.capture_request(
                        destination_run_id=info.run_id,
                        destination_workspace_locator=locator,
                        attempt=attempt,
                    ),
                )
            )
            if capture.status != "captured" or capture.workspace is None:
                raise exceptions.ApplicationError(
                    "remediated candidate was not captured",
                    type="CONTROL_STOP_CAPTURE_FAILED",
                    non_retryable=True,
                )
            latest_head_ref = (
                capture.workspace.archive_ref
                or capture.workspace.workspace_artifact_ref
                or capture.workspace.workspace_ref
                or ""
            )
            if not latest_head_ref:
                raise exceptions.ApplicationError(
                    "captured candidate has no durable workspace head",
                    type="CONTROL_STOP_CAPTURE_EVIDENCE_MISSING",
                    non_retryable=True,
                )
            verification = AgentRunResult.model_validate(
                await execute(
                    _REMEDIATE_ACTIVITY,
                    contract.verification_request(
                        destination_run_id=info.run_id,
                        destination_workspace_locator=locator,
                        attempt=attempt,
                        workspace_head_ref=latest_head_ref,
                    ),
                )
            )
            verdict = str(
                verification.metadata.get("verdict")
                or verification.metrics.get("verdict")
                or ""
            ).strip().upper()
            next_remaining_work_ref = str(
                verification.metadata.get("remainingWorkRef")
                or verification.metrics.get("remainingWorkRef")
                or ""
            ).strip()
            progress = bool(
                next_remaining_work_ref
                and next_remaining_work_ref != remaining_work_ref
            )
            attempts.append(
                {
                    "attemptOrdinal": attempt,
                    "workspaceHeadRef": latest_head_ref,
                    "remediationResult": remediation.model_dump(
                        by_alias=True, mode="json", exclude_none=True
                    ),
                    "verificationResult": verification.model_dump(
                        by_alias=True, mode="json", exclude_none=True
                    ),
                    "verdict": verdict,
                }
            )
            if verdict == "FULLY_IMPLEMENTED":
                budget = budget.consume(progress=True)
                terminal_status = "accepted"
                break
            if verdict in {
                "BLOCKED",
                "FAILED_UNRECOVERABLE",
                "CONTAMINATED",
                "UNSAFE",
            }:
                budget = budget.consume(progress=True)
                terminal_status = "blocked"
                break
            if verdict != "ADDITIONAL_WORK_NEEDED" or not next_remaining_work_ref:
                raise exceptions.ApplicationError(
                    "verifier returned unsupported or incomplete terminal evidence",
                    type="CONTROL_STOP_VERIFICATION_INVALID",
                    non_retryable=True,
                )
            try:
                budget = budget.consume(progress=progress)
            except ControlStopContinuationError:
                terminal_status = "control_stop"
                remaining_work_ref = next_remaining_work_ref
                break
            remaining_work_ref = next_remaining_work_ref

        return {
            **contract.workflow_entry(),
            "status": terminal_status,
            "restorationEvidenceRef": restored.restoration_evidence_ref,
            "restorationEvidenceDigest": restored.restoration_evidence_digest,
            "destinationWorkspaceLocator": locator,
            "latestWorkspaceHeadRef": latest_head_ref,
            "remainingWorkRef": remaining_work_ref,
            "sourceBudget": contract.source_budget.model_dump(
                by_alias=True, mode="json"
            ),
            "continuationBudget": budget.model_dump(by_alias=True, mode="json"),
            "preservedSteps": [
                step.model_dump(by_alias=True, mode="json")
                for step in contract.preserved_steps
            ],
            "sideEffects": [
                effect.model_dump(by_alias=True, mode="json")
                for effect in contract.side_effects
            ],
            "attempts": attempts,
        }
