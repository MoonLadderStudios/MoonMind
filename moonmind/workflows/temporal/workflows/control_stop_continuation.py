"""Temporal controller for a profile-bound control-stop continuation."""

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
    ContinuationVerificationResult,
    ControlStopContinuationContract,
)
from moonmind.workflows.temporal.activity_catalog import build_default_activity_catalog

WORKFLOW_NAME = "MoonMind.ControlStopContinuation"
_RESTORE_ACTIVITY = "agent_runtime.restore_workspace_checkpoint"
_CAPTURE_ACTIVITY = "agent_runtime.capture_workspace_checkpoint"
_PROFILE_ACTIVITY = "integration.omnigent.profile_bound_execute"


async def _execute(activity_name: str, payload: dict[str, Any]) -> Any:
    route = build_default_activity_catalog().resolve_activity(activity_name)
    return await workflow.execute_activity(
        activity_name,
        payload,
        task_queue=route.task_queue,
        start_to_close_timeout=timedelta(
            seconds=route.timeouts.start_to_close_seconds
        ),
        schedule_to_close_timeout=timedelta(
            seconds=route.timeouts.schedule_to_close_seconds
        ),
        heartbeat_timeout=timedelta(seconds=route.timeouts.heartbeat_timeout_seconds),
        retry_policy=RetryPolicy(
            initial_interval=timedelta(seconds=5),
            backoff_coefficient=2.0,
            maximum_interval=timedelta(seconds=route.retries.max_interval_seconds),
            maximum_attempts=route.retries.max_attempts,
            non_retryable_error_types=list(route.retries.non_retryable_error_codes),
        ),
    )


@workflow.defn(name=WORKFLOW_NAME)
class MoonMindControlStopContinuationWorkflow:
    """Restore Cn, then repeat remediation -> capture -> verification."""

    def __init__(self) -> None:
        self._projection: dict[str, Any] = {"status": "validating_contract"}

    @workflow.query
    def continuation_state(self) -> dict[str, Any]:
        """Return the durable destination projection without consulting source state."""

        return self._projection

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

        restored = ManagedWorkspaceRestoreResult.model_validate(
            await _execute(
                _RESTORE_ACTIVITY,
                contract.restore_request(destination_run_id=info.run_id),
            )
        )
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
        remaining_work_ref = contract.remaining_work_ref
        latest_candidate_ref = contract.workspace_head_ref
        attempts: list[dict[str, Any]] = []
        self._projection = {
            **contract.workflow_entry(),
            "status": "restored",
            "restorationEvidenceRef": restored.restoration_evidence_ref,
            "restorationEvidenceDigest": restored.restoration_evidence_digest,
            "destinationWorkspaceLocator": locator,
            "latestCandidateRef": latest_candidate_ref,
            "attempts": attempts,
        }

        while budget.consumed_attempts < budget.max_attempts:
            attempt_ordinal = (
                contract.source_budget.consumed_attempts
                + budget.consumed_attempts
                + 1
            )
            current_contract = contract.model_copy(
                update={"continuation_budget": budget}
            )
            self._projection["status"] = "remediating"
            remediation = AgentRunResult.model_validate(
                await _execute(
                    _PROFILE_ACTIVITY,
                    current_contract.remediation_request(
                        destination_run_id=info.run_id,
                        destination_workspace_locator=locator,
                    ),
                )
            )
            if remediation.failure_class:
                self._projection.update(
                    status="failed",
                    failurePhase="remediation",
                    latestCandidateRef=latest_candidate_ref,
                )
                return self._projection

            capture = ManagedWorkspaceCheckpointCaptureResult.model_validate(
                await _execute(
                    _CAPTURE_ACTIVITY,
                    contract.capture_request(
                        destination_run_id=info.run_id,
                        destination_workspace_locator=locator,
                        attempt_ordinal=attempt_ordinal,
                    ),
                )
            )
            expected_capture_key = (
                f"{contract.destination_workflow_id}:attempt:"
                f"{attempt_ordinal}:capture"
            )
            if (
                capture.status != "captured"
                or capture.workspace is None
                or not capture.workspace.archive_ref
                or capture.idempotency_key != expected_capture_key
                or capture.source_workspace_locator != restored.destination_workspace_locator
            ):
                raise exceptions.ApplicationError(
                    "captured candidate does not match destination workspace",
                    type="CONTROL_STOP_CAPTURE_EVIDENCE_MISMATCH",
                    non_retryable=True,
                )
            latest_candidate_ref = capture.workspace.archive_ref
            self._projection.update(
                status="verifying", latestCandidateRef=latest_candidate_ref
            )

            verification_run = AgentRunResult.model_validate(
                await _execute(
                    _PROFILE_ACTIVITY,
                    contract.verification_request(
                        destination_run_id=info.run_id,
                        destination_workspace_locator=locator,
                        attempt_ordinal=attempt_ordinal,
                        candidate_ref=latest_candidate_ref,
                        remaining_work_ref=remaining_work_ref,
                    ),
                )
            )
            if verification_run.failure_class:
                self._projection.update(
                    status="failed",
                    failurePhase="verification",
                    latestCandidateRef=latest_candidate_ref,
                )
                return self._projection
            try:
                gate = ContinuationVerificationResult.model_validate(
                    verification_run.metadata.get("controlStopVerification")
                )
            except ValueError as exc:
                raise exceptions.ApplicationError(
                    "verifier did not return typed continuation evidence",
                    type="CONTROL_STOP_VERIFICATION_EVIDENCE_INVALID",
                    non_retryable=True,
                ) from exc
            budget = budget.consume(progress=gate.progress)

            attempts.append(
                {
                    "attemptOrdinal": attempt_ordinal,
                    "candidateRef": latest_candidate_ref,
                    "verificationRef": gate.verification_ref,
                    "verdict": gate.verdict,
                    "progress": gate.progress,
                }
            )
            self._projection.update(
                attempts=attempts,
                continuationBudget=budget.model_dump(by_alias=True, mode="json"),
                latestVerificationRef=gate.verification_ref,
            )
            if gate.verdict == "FULLY_IMPLEMENTED":
                self._projection.update(
                    status="accepted", candidateState="accepted_complete"
                )
                return self._projection
            if gate.verdict != "ADDITIONAL_WORK_NEEDED":
                self._projection.update(status="blocked", terminalVerdict=gate.verdict)
                return self._projection
            remaining_work_ref = str(gate.remaining_work_ref)
            if (
                budget.consumed_attempts >= budget.max_attempts
                or budget.consecutive_no_progress_attempts
                >= budget.max_consecutive_no_progress_attempts
            ):
                self._projection.update(
                    status="control_stop",
                    candidateState="recovered_candidate",
                    remainingWorkRef=remaining_work_ref,
                    stopReason=(
                        "continuation_attempt_budget_exhausted"
                        if budget.consumed_attempts >= budget.max_attempts
                        else "continuation_no_progress_budget_exhausted"
                    ),
                )
                return self._projection

        self._projection.update(status="control_stop")
        return self._projection
