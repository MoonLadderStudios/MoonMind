"""Temporal controller for a profile-bound control-stop continuation."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from temporalio import exceptions, workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import CancelledError

from moonmind.schemas.agent_runtime_models import AgentRunResult
from moonmind.schemas.checkpoint_restore_models import ManagedWorkspaceRestoreResult
from moonmind.schemas.managed_checkpoint_models import (
    ManagedWorkspaceCheckpointCaptureResult,
)
from moonmind.workflows.executions.control_stop_continuation import (
    ContinuationAttemptEvidence,
    ContinuationVerificationResult,
    ControlStopContinuationContract,
    ControlStopContinuationState,
    ControlStopContinuationWorkflowInput,
)
from moonmind.workflows.temporal.activity_catalog import build_default_activity_catalog

WORKFLOW_NAME = "MoonMind.ControlStopContinuation"
_RESTORE_ACTIVITY = "agent_runtime.restore_workspace_checkpoint"
_PROFILE_ACTIVITY = "integration.omnigent.profile_bound_execute"
_CAPTURE_ACTIVITY = "agent_runtime.capture_workspace_checkpoint"


async def _execute(activity_type: str, payload: dict[str, Any]) -> Any:
    """Execute one catalogued activity with its canonical retry policy."""

    route = build_default_activity_catalog().resolve_activity(activity_type)
    return await workflow.execute_activity(
        activity_type,
        payload,
        task_queue=route.task_queue,
        start_to_close_timeout=timedelta(seconds=route.timeouts.start_to_close_seconds),
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


def _safe_lifecycle(result: AgentRunResult) -> dict[str, str | bool | int | None]:
    """Project only bounded Omnigent lifecycle evidence returned by the Activity."""

    metadata = result.metadata or {}
    projected: dict[str, str | bool | int | None] = {
        "normalizedStatus": str(metadata.get("normalizedStatus") or "completed"),
        "cleanupOwner": "profile_bound_activity",
        # The profile-bound Activity returns only after its finally block has
        # stopped/drained the host and released the Provider Profile last.
        "activityCleanupCompleted": True,
    }
    for key in (
        "omnigentSessionId",
        "externalStateRef",
        "captureManifestRef",
        "stateCheckpointRef",
        "checkpointKind",
        "providerName",
    ):
        value = metadata.get(key)
        if isinstance(value, (str, bool, int)) or value is None:
            projected[key] = value
    if result.diagnostics_ref:
        projected["diagnosticsRef"] = result.diagnostics_ref
    return projected


def _attempt_lifecycle(
    *,
    remediation: AgentRunResult,
    verification: AgentRunResult,
) -> dict[str, str | bool | int | None]:
    """Join both profile-bound host lifecycles without carrying provider payloads."""

    remediation_lifecycle = _safe_lifecycle(remediation)
    verification_lifecycle = _safe_lifecycle(verification)
    lifecycle: dict[str, str | bool | int | None] = {
        "cleanupOwner": "profile_bound_activity",
        "activityCleanupCompleted": bool(
            remediation_lifecycle["activityCleanupCompleted"]
            and verification_lifecycle["activityCleanupCompleted"]
        ),
    }
    for prefix, source in (
        ("remediation", remediation_lifecycle),
        ("verification", verification_lifecycle),
    ):
        for key, value in source.items():
            if key in {"cleanupOwner", "activityCleanupCompleted"}:
                continue
            lifecycle[f"{prefix}{key[0].upper()}{key[1:]}"] = value
    return lifecycle


@workflow.defn(name=WORKFLOW_NAME)
class MoonMindControlStopContinuationWorkflow:
    """Restore Cn, then repeat remediation -> capture -> verification."""

    def __init__(self) -> None:
        self._projection: dict[str, Any] = {"status": "validating_contract"}

    @workflow.query
    def continuation_state(self) -> dict[str, Any]:
        """Return the destination lifecycle without consulting mutable source state."""

        return self._projection

    def _project(
        self,
        *,
        contract: ControlStopContinuationContract,
        state: ControlStopContinuationState,
        status: str,
        failure_phase: str | None = None,
        reason_code: str | None = None,
    ) -> dict[str, Any]:
        attempts = [
            attempt.model_dump(by_alias=True, mode="json") for attempt in state.attempts
        ]
        latest_lifecycle = attempts[-1].get("lifecycle") if attempts else None
        projection: dict[str, Any] = {
            **contract.workflow_entry(),
            "status": status,
            "sourceOutcome": {
                "workflowId": contract.source_workflow_id,
                "runId": contract.source_run_id,
                "status": "failed",
                "kind": "workflow_gate",
                "immutable": True,
            },
            "destinationOutcome": {
                "workflowId": contract.destination_workflow_id,
                "status": status,
            },
            "restorationEvidenceRef": state.restoration_evidence_ref,
            "restorationEvidenceDigest": state.restoration_evidence_digest,
            "destinationWorkspaceLocator": state.destination_workspace_locator,
            "candidateState": (
                "accepted_complete" if status == "accepted" else "recovered_candidate"
            ),
            "latestWorkspaceHeadRef": state.latest_workspace_head_ref,
            "latestWorkspaceHeadDigest": state.latest_workspace_head_digest,
            "latestVerificationRef": state.latest_verification_ref,
            "remainingWorkRef": state.remaining_work_ref,
            "sourceBudget": contract.source_budget.model_dump(
                by_alias=True, mode="json"
            ),
            "continuationBudget": state.continuation_budget.model_dump(
                by_alias=True, mode="json"
            ),
            "preservedSteps": [
                step.model_dump(by_alias=True, mode="json")
                for step in contract.preserved_steps
            ],
            "skippedSideEffects": [
                effect.model_dump(by_alias=True, mode="json")
                for effect in contract.side_effects
            ],
            "attempts": attempts,
            "hostSessionLifecycle": latest_lifecycle,
            "continueAsNewCount": state.continue_as_new_count,
            "metrics": {
                "restoreSucceeded": 1,
                "attemptsCompleted": len(attempts),
                "converged": status == "accepted",
                "sideEffectsSkipped": sum(
                    effect.disposition == "already_performed"
                    for effect in contract.side_effects
                ),
            },
        }
        if failure_phase:
            projection["failurePhase"] = failure_phase
        if reason_code:
            projection["reasonCode"] = reason_code
        self._projection = projection
        return projection

    def _pre_restore_terminal(
        self,
        *,
        contract: ControlStopContinuationContract,
        status: str,
        failure_phase: str,
        reason_code: str,
    ) -> dict[str, Any]:
        projection = {
            **contract.workflow_entry(),
            "status": status,
            "failurePhase": failure_phase,
            "reasonCode": reason_code,
            "sourceOutcome": {
                "workflowId": contract.source_workflow_id,
                "runId": contract.source_run_id,
                "status": "failed",
                "kind": "workflow_gate",
                "immutable": True,
            },
            "destinationOutcome": {
                "workflowId": contract.destination_workflow_id,
                "status": status,
            },
            "latestWorkspaceHeadRef": contract.workspace_head_ref,
            "latestWorkspaceHeadDigest": contract.workspace_head_digest,
            "latestVerificationRef": contract.gate_result_ref,
            "remainingWorkRef": contract.remaining_work_ref,
            "sourceBudget": contract.source_budget.model_dump(
                by_alias=True, mode="json"
            ),
            "continuationBudget": contract.continuation_budget.model_dump(
                by_alias=True, mode="json"
            ),
            "preservedSteps": [
                step.model_dump(by_alias=True, mode="json")
                for step in contract.preserved_steps
            ],
            "skippedSideEffects": [
                effect.model_dump(by_alias=True, mode="json")
                for effect in contract.side_effects
            ],
            "attempts": [],
            "metrics": {
                "restoreSucceeded": 0,
                "attemptsCompleted": 0,
                "converged": False,
            },
        }
        self._projection = projection
        return projection

    @workflow.run
    async def run(self, input_payload: dict[str, Any]) -> dict[str, Any]:
        try:
            workflow_input = ControlStopContinuationWorkflowInput.model_validate(
                input_payload
            )
        except Exception as exc:
            raise exceptions.ApplicationError(
                "invalid control-stop continuation workflow input",
                type="CONTROL_STOP_CONTINUATION_INVALID",
                non_retryable=True,
            ) from exc

        contract = workflow_input.contract
        info = workflow.info()
        if info.workflow_id != contract.destination_workflow_id:
            raise exceptions.ApplicationError(
                "destination workflow identity does not match frozen contract",
                type="CONTROL_STOP_DESTINATION_IDENTITY_MISMATCH",
                non_retryable=True,
            )

        state = workflow_input.state
        if state is None:
            self._projection = {
                **contract.workflow_entry(),
                "status": "restoring",
                "sourceOutcome": {
                    "workflowId": contract.source_workflow_id,
                    "runId": contract.source_run_id,
                    "status": "failed",
                    "kind": "workflow_gate",
                    "immutable": True,
                },
            }
            try:
                restored_payload = await _execute(
                    _RESTORE_ACTIVITY,
                    contract.restore_request(destination_run_id=info.run_id),
                )
                restored = ManagedWorkspaceRestoreResult.model_validate(
                    restored_payload
                )
            except CancelledError:
                self._pre_restore_terminal(
                    contract=contract,
                    status="canceled",
                    failure_phase="restoration",
                    reason_code="continuation_canceled",
                )
                raise
            except Exception as exc:  # noqa: BLE001 - phase becomes terminal evidence
                return self._pre_restore_terminal(
                    contract=contract,
                    status="failed",
                    failure_phase="restoration",
                    reason_code=type(exc).__name__,
                )

            expected_key = f"{contract.destination_workflow_id}:restore"
            if (
                restored.checkpoint_ref != contract.checkpoint_ref
                or restored.base_commit != contract.workspace_base_commit
                or restored.idempotency_key != expected_key
                or restored.destination_workspace_locator.agent_run_id
                != contract.destination_workspace_id
            ):
                return self._pre_restore_terminal(
                    contract=contract,
                    status="failed",
                    failure_phase="restoration",
                    reason_code="CONTROL_STOP_RESTORE_EVIDENCE_MISMATCH",
                )

            state = ControlStopContinuationState(
                destinationWorkspaceLocator=(
                    restored.destination_workspace_locator.model_dump(
                        by_alias=True, mode="json"
                    )
                ),
                restorationEvidenceRef=restored.restoration_evidence_ref,
                restorationEvidenceDigest=restored.restoration_evidence_digest,
                latestWorkspaceHeadRef=contract.workspace_head_ref,
                latestWorkspaceHeadDigest=contract.workspace_head_digest,
                latestVerificationRef=contract.gate_result_ref,
                remainingWorkRef=contract.remaining_work_ref,
                continuationBudget=contract.continuation_budget,
            )

        self._project(contract=contract, state=state, status="restored")

        while state.continuation_budget.consumed_attempts < (
            state.continuation_budget.max_attempts
        ):
            budget = state.continuation_budget
            attempt = (
                contract.source_budget.consumed_attempts + budget.consumed_attempts + 1
            )
            self._project(contract=contract, state=state, status="remediating")
            try:
                remediation = AgentRunResult.model_validate(
                    await _execute(
                        _PROFILE_ACTIVITY,
                        contract.remediation_request(
                            destination_run_id=info.run_id,
                            destination_workspace_locator=(
                                state.destination_workspace_locator
                            ),
                            attempt=attempt,
                            workspace_head_ref=state.latest_workspace_head_ref,
                            latest_verification_ref=state.latest_verification_ref,
                            remaining_work_ref=state.remaining_work_ref,
                            continuation_budget=budget,
                        ),
                    )
                )
            except CancelledError:
                self._project(
                    contract=contract,
                    state=state,
                    status="canceled",
                    failure_phase="remediation",
                    reason_code="continuation_canceled",
                )
                raise
            except Exception as exc:  # noqa: BLE001 - phase becomes terminal evidence
                return self._project(
                    contract=contract,
                    state=state,
                    status="failed",
                    failure_phase="remediation",
                    reason_code=type(exc).__name__,
                )
            if remediation.failure_class:
                return self._project(
                    contract=contract,
                    state=state,
                    status="failed",
                    failure_phase="remediation",
                    reason_code=str(remediation.failure_class),
                )

            self._project(contract=contract, state=state, status="capturing_candidate")
            try:
                captured = ManagedWorkspaceCheckpointCaptureResult.model_validate(
                    await _execute(
                        _CAPTURE_ACTIVITY,
                        contract.capture_request(
                            destination_run_id=info.run_id,
                            destination_workspace_locator=(
                                state.destination_workspace_locator
                            ),
                            attempt=attempt,
                        ),
                    )
                )
            except CancelledError:
                self._project(
                    contract=contract,
                    state=state,
                    status="canceled",
                    failure_phase="candidate_capture",
                    reason_code="continuation_canceled",
                )
                raise
            except Exception as exc:  # noqa: BLE001 - phase becomes terminal evidence
                return self._project(
                    contract=contract,
                    state=state,
                    status="failed",
                    failure_phase="candidate_capture",
                    reason_code=type(exc).__name__,
                )

            expected_capture_key = (
                f"{contract.destination_workflow_id}:remediation:{attempt}:capture"
            )
            if (
                captured.status != "captured"
                or captured.workspace is None
                or not captured.workspace.archive_ref
                or not captured.workspace.archive_digest
                or captured.idempotency_key != expected_capture_key
                or captured.source_workspace_locator.model_dump(
                    by_alias=True, mode="json"
                )
                != state.destination_workspace_locator
            ):
                return self._project(
                    contract=contract,
                    state=state,
                    status="failed",
                    failure_phase="candidate_capture",
                    reason_code="CONTROL_STOP_CAPTURE_EVIDENCE_MISMATCH",
                )

            candidate_ref = captured.workspace.archive_ref
            candidate_digest = captured.workspace.archive_digest
            capture_state = state.model_copy(
                update={
                    "latest_workspace_head_ref": candidate_ref,
                    "latest_workspace_head_digest": candidate_digest,
                }
            )
            self._project(contract=contract, state=capture_state, status="verifying")
            try:
                verification = AgentRunResult.model_validate(
                    await _execute(
                        _PROFILE_ACTIVITY,
                        contract.verification_request(
                            destination_run_id=info.run_id,
                            destination_workspace_locator=(
                                state.destination_workspace_locator
                            ),
                            attempt=attempt,
                            workspace_head_ref=candidate_ref,
                            remaining_work_ref=state.remaining_work_ref,
                        ),
                    )
                )
            except CancelledError:
                self._project(
                    contract=contract,
                    state=capture_state,
                    status="canceled",
                    failure_phase="verification",
                    reason_code="continuation_canceled",
                )
                raise
            except Exception as exc:  # noqa: BLE001 - phase becomes terminal evidence
                return self._project(
                    contract=contract,
                    state=capture_state,
                    status="failed",
                    failure_phase="verification",
                    reason_code=type(exc).__name__,
                )
            if verification.failure_class:
                return self._project(
                    contract=contract,
                    state=capture_state,
                    status="failed",
                    failure_phase="verification",
                    reason_code=str(verification.failure_class),
                )

            try:
                gate = ContinuationVerificationResult.model_validate(
                    verification.metadata.get("controlStopVerification")
                )
            except Exception:  # noqa: BLE001 - malformed provider evidence fails closed
                return self._project(
                    contract=contract,
                    state=capture_state,
                    status="failed",
                    failure_phase="verification",
                    reason_code="CONTROL_STOP_VERIFICATION_EVIDENCE_INVALID",
                )
            if gate.verification_ref not in verification.output_refs:
                return self._project(
                    contract=contract,
                    state=capture_state,
                    status="failed",
                    failure_phase="verification",
                    reason_code="CONTROL_STOP_VERIFICATION_REF_MISMATCH",
                )

            budget = budget.consume(progress=gate.progress)
            attempt_evidence = ContinuationAttemptEvidence(
                attemptOrdinal=attempt,
                remediationStepExecutionId=(
                    f"{contract.destination_workflow_id}:remediation:"
                    f"execution:{attempt}"
                ),
                verificationStepExecutionId=(
                    f"{contract.destination_workflow_id}:verification:"
                    f"execution:{attempt}"
                ),
                candidateRef=candidate_ref,
                candidateDigest=candidate_digest,
                progress=gate.progress,
                semanticVerdict=gate.verdict,
                verificationRef=gate.verification_ref,
                remainingWorkRef=gate.remaining_work_ref,
                lifecycle=_attempt_lifecycle(
                    remediation=remediation,
                    verification=verification,
                ),
            )
            next_remaining_work_ref = (
                gate.remaining_work_ref or state.remaining_work_ref
            )
            state = capture_state.model_copy(
                update={
                    "latest_verification_ref": gate.verification_ref,
                    "remaining_work_ref": next_remaining_work_ref,
                    "continuation_budget": budget,
                    "attempts": [*state.attempts, attempt_evidence],
                }
            )

            if gate.verdict == "FULLY_IMPLEMENTED":
                result = self._project(
                    contract=contract, state=state, status="accepted"
                )
                result["nextSemanticOperation"] = "publication_gate"
                return result
            if gate.verdict in {
                "BLOCKED",
                "ENVIRONMENT_CONTAMINATED_BY_SKILL_PROJECTION",
            }:
                return self._project(
                    contract=contract,
                    state=state,
                    status="blocked",
                    failure_phase="verification",
                    reason_code=gate.verdict,
                )
            if gate.verdict == "FAILED_UNRECOVERABLE":
                return self._project(
                    contract=contract,
                    state=state,
                    status="failed",
                    failure_phase="verification",
                    reason_code=gate.verdict,
                )

            attempts_exhausted = budget.consumed_attempts >= budget.max_attempts
            no_progress_exhausted = (
                budget.consecutive_no_progress_attempts
                >= budget.max_consecutive_no_progress_attempts
            )
            if attempts_exhausted or no_progress_exhausted:
                return self._project(
                    contract=contract,
                    state=state,
                    status="control_stop",
                    reason_code=(
                        "continuation_attempt_budget_exhausted"
                        if attempts_exhausted
                        else "continuation_no_progress_budget_exhausted"
                    ),
                )

            if budget.consumed_attempts % contract.continue_as_new_after_attempts == 0:
                continued_state = state.model_copy(
                    update={"continue_as_new_count": state.continue_as_new_count + 1}
                )
                self._project(
                    contract=contract,
                    state=continued_state,
                    status="continuing_as_new",
                )
                workflow.continue_as_new(
                    ControlStopContinuationWorkflowInput(
                        contract=contract,
                        state=continued_state,
                    ).model_dump(by_alias=True, mode="json")
                )

        return self._project(
            contract=contract,
            state=state,
            status="control_stop",
            reason_code="continuation_attempt_budget_exhausted",
        )
