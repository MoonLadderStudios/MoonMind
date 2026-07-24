"""Temporal entrypoint for a control-stop remediation continuation."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from temporalio import exceptions, workflow
from temporalio.common import RetryPolicy

from moonmind.schemas.managed_checkpoint_models import (
    ManagedWorkspaceCheckpointCaptureResult,
)
from moonmind.schemas.checkpoint_restore_models import ManagedWorkspaceRestoreResult
from moonmind.schemas.agent_runtime_models import AgentRunResult
from moonmind.workflows.executions.control_stop_continuation import (
    ControlStopContinuationContract,
    ControlStopContinuationError,
)
from moonmind.workflows.temporal.activity_catalog import build_default_activity_catalog

WORKFLOW_NAME = "MoonMind.ControlStopContinuation"
_RESTORE_ACTIVITY = "agent_runtime.restore_workspace_checkpoint"
_REMEDIATE_ACTIVITY = "integration.omnigent.profile_bound_execute"
_CAPTURE_ACTIVITY = "agent_runtime.capture_workspace_checkpoint"
_VERIFY_ACTIVITY = "integration.omnigent.profile_bound_execute"


async def _execute(activity_type: str, payload: dict[str, Any]) -> Any:
    """Execute one catalogued activity with its canonical retry policy."""

    route = build_default_activity_catalog().resolve_activity(activity_type)
    return await workflow.execute_activity(
        activity_type,
        payload,
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
            maximum_interval=timedelta(
                seconds=route.retries.max_interval_seconds
            ),
            maximum_attempts=route.retries.max_attempts,
            non_retryable_error_types=list(
                route.retries.non_retryable_error_codes
            ),
        ),
    )


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

        restored_payload = await _execute(
            _RESTORE_ACTIVITY,
            contract.restore_request(destination_run_id=info.run_id),
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

        workspace_locator = restored.destination_workspace_locator.model_dump(
            by_alias=True, mode="json"
        )
        state: dict[str, Any] = {
            **contract.workflow_entry(),
            "restorationEvidenceRef": restored.restoration_evidence_ref,
            "restorationEvidenceDigest": restored.restoration_evidence_digest,
            "destinationWorkspaceLocator": workspace_locator,
            "preservedSteps": [
                step.model_dump(by_alias=True, mode="json")
                for step in contract.preserved_steps
            ],
            "sideEffects": [
                effect.model_dump(by_alias=True, mode="json")
                for effect in contract.side_effects
            ],
            "sourceBudget": contract.source_budget.model_dump(
                by_alias=True, mode="json"
            ),
            "attempts": [],
        }
        budget = contract.continuation_budget
        workspace_head_ref = contract.workspace_head_ref
        workspace_head_digest = contract.workspace_head_digest
        remaining_work_ref = contract.remaining_work_ref
        latest_verification_ref = contract.gate_result_ref

        while budget.consumed_attempts < budget.max_attempts:
            if (
                budget.consecutive_no_progress_attempts
                >= budget.max_consecutive_no_progress_attempts
            ):
                return {
                    **state,
                    "status": "control_stop",
                    "outcomeKind": "workflow_gate",
                    "reasonCode": "continuation_no_progress_budget_exhausted",
                    "latestWorkspaceHeadRef": workspace_head_ref,
                    "latestWorkspaceHeadDigest": workspace_head_digest,
                    "remainingWorkRef": remaining_work_ref,
                    "continuationBudget": budget.model_dump(
                        by_alias=True, mode="json"
                    ),
                }
            attempt = (
                contract.source_budget.consumed_attempts
                + budget.consumed_attempts
                + 1
            )
            remediation = AgentRunResult.model_validate(
                await _execute(
                    _REMEDIATE_ACTIVITY,
                    contract.remediation_request(
                        destination_run_id=info.run_id,
                        destination_workspace_locator=workspace_locator,
                        attempt=attempt,
                        workspace_head_ref=workspace_head_ref,
                        latest_verification_ref=latest_verification_ref,
                        remaining_work_ref=remaining_work_ref,
                        continuation_budget=budget,
                    ),
                )
            )
            if remediation.failure_class:
                return {
                    **state,
                    "status": "failed",
                    "failurePhase": "remediation",
                    "latestWorkspaceHeadRef": workspace_head_ref,
                    "latestWorkspaceHeadDigest": workspace_head_digest,
                    "continuationBudget": budget.model_dump(
                        by_alias=True, mode="json"
                    ),
                    "remediationResult": remediation.model_dump(
                        by_alias=True, mode="json", exclude_none=True
                    ),
                }

            captured = ManagedWorkspaceCheckpointCaptureResult.model_validate(
                await _execute(
                    _CAPTURE_ACTIVITY,
                    contract.capture_request(
                        destination_run_id=info.run_id,
                        destination_workspace_locator=workspace_locator,
                        attempt=attempt,
                    ),
                )
            )
            if captured.status != "captured" or captured.workspace is None:
                return {
                    **state,
                    "status": "failed",
                    "failurePhase": "candidate_capture",
                    "latestWorkspaceHeadRef": workspace_head_ref,
                    "latestWorkspaceHeadDigest": workspace_head_digest,
                    "continuationBudget": budget.model_dump(
                        by_alias=True, mode="json"
                    ),
                    "captureResult": captured.model_dump(
                        by_alias=True, mode="json", exclude_none=True
                    ),
                }
            candidate_ref = captured.workspace.archive_ref
            candidate_digest = captured.workspace.archive_digest
            if not candidate_ref or not candidate_digest:
                raise exceptions.ApplicationError(
                    "captured candidate lacks restorable archive evidence",
                    type="CONTROL_STOP_CAPTURE_EVIDENCE_MISSING",
                    non_retryable=True,
                )
            progress = candidate_digest != workspace_head_digest
            try:
                budget = budget.consume(progress=progress)
            except ControlStopContinuationError as exc:
                return {
                    **state,
                    "status": "control_stop",
                    "outcomeKind": "workflow_gate",
                    "reasonCode": str(exc),
                    "latestWorkspaceHeadRef": candidate_ref,
                    "latestWorkspaceHeadDigest": candidate_digest,
                    "remainingWorkRef": remaining_work_ref,
                    "continuationBudget": budget.model_dump(
                        by_alias=True, mode="json"
                    ),
                }

            verification = AgentRunResult.model_validate(
                await _execute(
                    _VERIFY_ACTIVITY,
                    contract.verification_request(
                        destination_run_id=info.run_id,
                        destination_workspace_locator=workspace_locator,
                        attempt=attempt,
                        workspace_head_ref=candidate_ref,
                        remaining_work_ref=remaining_work_ref,
                    ),
                )
            )
            gate = verification.metadata.get("moonSpecVerify")
            gate = gate if isinstance(gate, dict) else verification.metadata
            verdict = str(
                gate.get("semanticVerdict")
                or gate.get("verdict")
                or gate.get("gateVerdict")
                or gate.get("moonSpecVerdict")
                or gate.get("verificationVerdict")
                or ""
            ).strip().upper()
            verification_ref = str(
                gate.get("gateResultRef")
                or (verification.output_refs[0] if verification.output_refs else "")
            ).strip()
            attempt_result = {
                "attemptOrdinal": attempt,
                "remediationStepExecutionId": (
                    f"{contract.destination_workflow_id}:remediation:"
                    f"execution:{attempt}"
                ),
                "verificationStepExecutionId": (
                    f"{contract.destination_workflow_id}:verification:"
                    f"execution:{attempt}"
                ),
                "candidateRef": candidate_ref,
                "candidateDigest": candidate_digest,
                "progress": progress,
                "semanticVerdict": verdict,
                "verificationRef": verification_ref or None,
            }
            state["attempts"].append(attempt_result)
            workspace_head_ref = candidate_ref
            workspace_head_digest = candidate_digest
            latest_verification_ref = verification_ref or latest_verification_ref

            if verification.failure_class or verdict in {
                "BLOCKED",
                "FAILED_UNRECOVERABLE",
            }:
                return {
                    **state,
                    "status": "blocked" if verdict == "BLOCKED" else "failed",
                    "failurePhase": "verification",
                    "latestWorkspaceHeadRef": workspace_head_ref,
                    "latestWorkspaceHeadDigest": workspace_head_digest,
                    "continuationBudget": budget.model_dump(
                        by_alias=True, mode="json"
                    ),
                    "verificationResult": verification.model_dump(
                        by_alias=True, mode="json", exclude_none=True
                    ),
                }
            if verdict == "FULLY_IMPLEMENTED":
                return {
                    **state,
                    "status": "accepted",
                    "candidateState": "accepted_complete",
                    "nextSemanticOperation": "publication_gate",
                    "latestWorkspaceHeadRef": workspace_head_ref,
                    "latestWorkspaceHeadDigest": workspace_head_digest,
                    "latestVerificationRef": latest_verification_ref,
                    "continuationBudget": budget.model_dump(
                        by_alias=True, mode="json"
                    ),
                }
            if verdict != "ADDITIONAL_WORK_NEEDED":
                raise exceptions.ApplicationError(
                    "authoritative verifier returned an unsupported verdict",
                    type="CONTROL_STOP_VERDICT_INVALID",
                    non_retryable=True,
                )
            next_remaining = str(
                gate.get("remainingWorkRef")
                or gate.get("remaining_work_ref")
                or ""
            ).strip()
            if not next_remaining:
                raise exceptions.ApplicationError(
                    "additional work verdict lacks remaining-work evidence",
                    type="CONTROL_STOP_REMAINING_WORK_MISSING",
                    non_retryable=True,
                )
            remaining_work_ref = next_remaining

        return {
            **state,
            "status": "control_stop",
            "outcomeKind": "workflow_gate",
            "reasonCode": "continuation_attempt_budget_exhausted",
            "latestWorkspaceHeadRef": workspace_head_ref,
            "latestWorkspaceHeadDigest": workspace_head_digest,
            "latestVerificationRef": latest_verification_ref,
            "remainingWorkRef": remaining_work_ref,
            "continuationBudget": budget.model_dump(by_alias=True, mode="json"),
        }
