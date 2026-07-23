"""Temporal entrypoint for a control-stop remediation continuation."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from temporalio import exceptions, workflow
from temporalio.common import RetryPolicy

from moonmind.schemas.checkpoint_restore_models import ManagedWorkspaceRestoreResult
from moonmind.schemas.agent_runtime_models import AgentRunResult
from moonmind.workflows.executions.control_stop_continuation import (
    ControlStopContinuationContract,
)
from moonmind.workflows.temporal.activity_catalog import build_default_activity_catalog

WORKFLOW_NAME = "MoonMind.ControlStopContinuation"
_RESTORE_ACTIVITY = "agent_runtime.restore_workspace_checkpoint"
_REMEDIATE_ACTIVITY = "integration.omnigent.profile_bound_execute"


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

        remediation_route = build_default_activity_catalog().resolve_activity(
            _REMEDIATE_ACTIVITY
        )
        remediation_payload = await workflow.execute_activity(
            _REMEDIATE_ACTIVITY,
            contract.remediation_request(
                destination_run_id=info.run_id,
                destination_workspace_locator=(
                    restored.destination_workspace_locator.model_dump(
                        by_alias=True, mode="json"
                    )
                ),
            ),
            task_queue=remediation_route.task_queue,
            start_to_close_timeout=timedelta(
                seconds=remediation_route.timeouts.start_to_close_seconds
            ),
            schedule_to_close_timeout=timedelta(
                seconds=remediation_route.timeouts.schedule_to_close_seconds
            ),
            heartbeat_timeout=timedelta(
                seconds=remediation_route.timeouts.heartbeat_timeout_seconds
            ),
            retry_policy=RetryPolicy(
                initial_interval=timedelta(seconds=5),
                backoff_coefficient=2.0,
                maximum_interval=timedelta(
                    seconds=remediation_route.retries.max_interval_seconds
                ),
                maximum_attempts=remediation_route.retries.max_attempts,
                non_retryable_error_types=list(
                    remediation_route.retries.non_retryable_error_codes
                ),
            ),
        )
        remediation = AgentRunResult.model_validate(remediation_payload)

        return {
            **contract.workflow_entry(),
            "status": "remediation_completed",
            "restorationEvidenceRef": restored.restoration_evidence_ref,
            "restorationEvidenceDigest": restored.restoration_evidence_digest,
            "destinationWorkspaceLocator": restored.destination_workspace_locator.model_dump(
                by_alias=True, mode="json"
            ),
            "preservedSteps": [
                step.model_dump(by_alias=True, mode="json")
                for step in contract.preserved_steps
            ],
            "sideEffects": [
                effect.model_dump(by_alias=True, mode="json")
                for effect in contract.side_effects
            ],
            "remediationResult": remediation.model_dump(
                by_alias=True, mode="json", exclude_none=True
            ),
        }
