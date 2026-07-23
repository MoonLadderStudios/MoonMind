"""Durable orchestration for publication-only recovery."""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Mapping

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError

with workflow.unsafe.imports_passed_through():
    from moonmind.workflows.temporal.activity_catalog import (
        AGENT_RUNTIME_TASK_QUEUE,
        ARTIFACTS_TASK_QUEUE,
        INTEGRATIONS_TASK_QUEUE,
    )
    from moonmind.workflows.temporal.publication_recovery import (
        PublicationObservation,
        PublicationRecoveryContract,
        reconcile_publication_state,
        validate_restored_candidate,
    )

WORKFLOW_NAME = "MoonMind.PublicationRecoveryV1"

_RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    backoff_coefficient=2,
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=5,
)


def _payload(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


@workflow.defn(name=WORKFLOW_NAME)
class MoonMindPublicationRecoveryWorkflow:
    """Run only the authority boundaries needed to publish an accepted candidate."""

    def __init__(self) -> None:
        self._phase = "contract_validation"
        self._result: dict[str, Any] | None = None

    @workflow.query(name="publication_recovery.state")
    def state(self) -> dict[str, Any]:
        return {"phase": self._phase, "result": self._result}

    async def _activity(
        self, name: str, payload: Mapping[str, Any], *, task_queue: str
    ) -> dict[str, Any]:
        result = await workflow.execute_activity(
            name,
            dict(payload),
            task_queue=task_queue,
            start_to_close_timeout=timedelta(minutes=5),
            schedule_to_close_timeout=timedelta(minutes=15),
            retry_policy=_RETRY_POLICY,
        )
        return _payload(result)

    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        contract = PublicationRecoveryContract.model_validate(payload)
        frozen = contract.model_dump(by_alias=True, mode="json")
        operation_key = contract.continuation.publication_idempotency_key

        self._phase = "publication_state_reconciliation"
        observation_payload = await self._activity(
            "publication_recovery.observe",
            {"contract": frozen, "idempotencyKey": operation_key},
            task_queue=INTEGRATIONS_TASK_QUEUE,
        )
        observation = PublicationObservation.model_validate(observation_payload)
        decision = reconcile_publication_state(contract, observation)
        if decision.outcome in {"conflict", "ambiguous"}:
            raise ApplicationError(
                decision.reason_code,
                type="PUBLICATION_RECONCILIATION_BLOCKED",
                non_retryable=True,
            )

        restoration: dict[str, Any] | None = None
        if (
            decision.mutation_allowed
            and not contract.continuation.verified_remote_candidate_ref
        ):
            self._phase = "optional_workspace_restoration"
            restoration = await self._activity(
                "publication_recovery.restore_candidate",
                {"contract": frozen, "idempotencyKey": operation_key},
                task_queue=AGENT_RUNTIME_TASK_QUEUE,
            )
            validate_restored_candidate(contract, restoration)

        publication = {
            "pullRequestUrl": decision.existing_pull_request_url,
            "reconciliationOutcome": decision.outcome,
        }
        if decision.mutation_allowed:
            self._phase = "publication_operation"
            publication = await self._activity(
                "publication_recovery.publish",
                {
                    "contract": frozen,
                    "restoration": restoration,
                    "observation": observation_payload,
                    "idempotencyKey": operation_key,
                },
                task_queue=INTEGRATIONS_TASK_QUEUE,
            )

        self._phase = "publication_verification"
        verified = await self._activity(
            "publication_recovery.verify",
            {
                "contract": frozen,
                "publication": publication,
                "idempotencyKey": operation_key,
            },
            task_queue=INTEGRATIONS_TASK_QUEUE,
        )

        self._phase = "artifact_summary_persistence"
        self._result = await self._activity(
            "publication_recovery.persist_result",
            {
                "contract": frozen,
                "reconciliation": decision.model_dump(
                    by_alias=True, mode="json"
                ),
                "publication": publication,
                "verifiedEvidence": verified,
                "idempotencyKey": operation_key,
                "destinationWorkflowId": workflow.info().workflow_id,
                "destinationRunId": workflow.info().run_id,
            },
            task_queue=ARTIFACTS_TASK_QUEUE,
        )
        self._phase = "cleanup"
        try:
            await self._activity(
                "publication_recovery.cleanup",
                {
                    "contract": frozen,
                    "restoration": restoration,
                    "idempotencyKey": operation_key,
                },
                task_queue=AGENT_RUNTIME_TASK_QUEUE,
            )
        except Exception:
            # Cleanup is auxiliary and cannot overwrite authoritative publication
            # success. The persisted result remains the terminal evidence.
            pass
        self._phase = "completed"
        return self._result


__all__ = ["MoonMindPublicationRecoveryWorkflow", "WORKFLOW_NAME"]
