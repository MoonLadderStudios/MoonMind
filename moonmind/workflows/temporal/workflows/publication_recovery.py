"""Durable orchestration for publication-only recovery."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any, Mapping

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import (
    ActivityError,
    ApplicationError,
    is_cancelled_exception,
)
from temporalio.exceptions import TimeoutError as ActivityTimeoutError

with workflow.unsafe.imports_passed_through():
    from moonmind.workflows.temporal.activity_catalog import (
        AGENT_RUNTIME_TASK_QUEUE,
        ARTIFACTS_TASK_QUEUE,
        INTEGRATIONS_TASK_QUEUE,
    )
    from moonmind.workflows.temporal.publication_recovery import (
        SAVED_WORK_PUBLICATION_RESULT_SCHEMA_VERSION,
        SAVED_WORK_PUBLICATION_SCHEMA_VERSION,
        PublicationObservation,
        PublicationRecoveryContract,
        SavedWorkPublicationContract,
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
# Remote effects are retried by the workflow, which starts no new attempt
# after a cancellation request; Temporal's retries would still run then.
_ONE_ATTEMPT = RetryPolicy(maximum_attempts=1)


# Saved-work rejections that mean the admitted decision no longer applies.
_SAVED_WORK_CONFLICT_CODES = frozenset(
    {
        "PUBLICATION_APPLICATION_CONFLICT",
        "PUBLICATION_CANDIDATE_MISMATCH",
        "PUBLICATION_DESTINATION_NOT_EMPTY",
        "PUBLICATION_STALE_EXPECTATION",
    }
)


def _payload(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _activity_failure(exc: ActivityError) -> tuple[str, str]:
    """Classify an exhausted or rejected saved-publication Activity."""

    cause = exc.cause
    if isinstance(cause, ActivityTimeoutError):
        kind = cause.type.name.lower() if cause.type is not None else "unknown"
        return "unavailable", f"publication_activity_timeout:{kind}"
    code = (cause.type or "").strip() if isinstance(cause, ApplicationError) else ""
    if isinstance(cause, ApplicationError) and cause.non_retryable and code:
        return ("conflict" if code in _SAVED_WORK_CONFLICT_CODES else "rejected"), code
    return "unavailable", code or "publication_activity_unavailable"


def _retryable(exc: ActivityError) -> bool:
    cause = exc.cause
    return not is_cancelled_exception(exc) and not (
        isinstance(cause, ApplicationError) and cause.non_retryable
    )


@workflow.defn(name=WORKFLOW_NAME)
class MoonMindPublicationRecoveryWorkflow:
    """Run only the authority boundaries needed to publish an accepted candidate."""

    def __init__(self) -> None:
        self._phase = "contract_validation"
        self._result: dict[str, Any] | None = None
        self._cancellation: BaseException | None = None
        self._interrupted_attempt: str | None = None

    @workflow.query(name="publication_recovery.state")
    def state(self) -> dict[str, Any]:
        return {"phase": self._phase, "result": self._result}

    async def _activity(
        self,
        name: str,
        payload: Mapping[str, Any],
        *,
        task_queue: str,
        retry_policy: RetryPolicy = _RETRY_POLICY,
    ) -> dict[str, Any]:
        result = await workflow.execute_activity(
            name,
            dict(payload),
            task_queue=task_queue,
            start_to_close_timeout=timedelta(minutes=5),
            schedule_to_close_timeout=timedelta(minutes=15),
            retry_policy=retry_policy,
        )
        return _payload(result)

    async def _to_completion(
        self,
        name: str,
        payload: Mapping[str, Any],
        *,
        task_queue: str,
        retry_policy: RetryPolicy = _RETRY_POLICY,
    ) -> dict[str, Any]:
        """Run one saved-work Activity to its real outcome despite cancellation.

        A workflow cancellation arriving meanwhile is remembered and applied
        after the Activity stops, so a landed push or PR is recorded instead of
        hidden, results persist, and use claims are released after their last
        reader.
        """

        step = asyncio.ensure_future(
            self._activity(
                name, payload, task_queue=task_queue, retry_policy=retry_policy
            )
        )
        while True:
            try:
                return await asyncio.shield(step)
            except asyncio.CancelledError as exc:
                if step.cancelled():
                    raise
                self._cancellation = self._cancellation or exc

    async def _effect(
        self, name: str, payload: Mapping[str, Any], *, task_queue: str
    ) -> dict[str, Any]:
        """Run one remote-effect Activity, retrying only while not cancelled.

        Each attempt runs to its real outcome and every retry reconciles the
        remote before acting. Once a cancellation is requested, no further
        attempt starts: the effect stays unconfirmed for a later run.
        """

        attempt, delay = 1, _RETRY_POLICY.initial_interval
        while True:
            try:
                return await self._to_completion(
                    name, payload, task_queue=task_queue, retry_policy=_ONE_ATTEMPT
                )
            except ActivityError as exc:
                if not _retryable(exc) or attempt >= _RETRY_POLICY.maximum_attempts:
                    raise
                if self._cancellation is None:
                    try:
                        await workflow.sleep(delay)
                    except asyncio.CancelledError as cancelled:
                        self._cancellation = cancelled
                if self._cancellation is not None:
                    self._interrupted_attempt = _activity_failure(exc)[1]
                    raise self._cancellation from None
            attempt += 1
            delay = min(
                delay * _RETRY_POLICY.backoff_coefficient,
                _RETRY_POLICY.maximum_interval,
            )

    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        if (
            isinstance(payload, Mapping)
            and payload.get("schemaVersion") == SAVED_WORK_PUBLICATION_SCHEMA_VERSION
        ):
            return await self._run_saved_work(payload)
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
                {
                    "contract": frozen,
                    "idempotencyKey": operation_key,
                    "destinationWorkflowId": workflow.info().workflow_id,
                    "destinationRunId": workflow.info().run_id,
                },
                task_queue=AGENT_RUNTIME_TASK_QUEUE,
            )
            validate_restored_candidate(contract, restoration)
            self._phase = "publication_operation"
            restoration = await self._activity(
                "publication_recovery.publish_candidate",
                {
                    "contract": frozen,
                    "restoration": restoration,
                    "idempotencyKey": operation_key,
                },
                task_queue=AGENT_RUNTIME_TASK_QUEUE,
            )

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
                "reconciliation": decision.model_dump(
                    by_alias=True, mode="json"
                ),
                "restoration": restoration,
                "idempotencyKey": operation_key,
                "destinationWorkflowId": workflow.info().workflow_id,
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

    async def _run_saved_work(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Publish one immutable saved result through the existing publisher.

        The prepare Activity admits the destination once and durably persists
        the exact candidate before any effect; a later run of the same
        operation reuses that decision. Push and pull request are separate
        Activities, so a PR retry never repeats a confirmed push. Cancellation
        starts no further effect or effect attempt but never abandons one in
        flight or its durable record. No agent, verifier, or model Activity is
        reachable from this path.
        """

        try:
            contract = SavedWorkPublicationContract.model_validate(payload)
        except ValueError as exc:
            raise ApplicationError(
                str(exc), type="PUBLICATION_CONTRACT_INVALID", non_retryable=True
            ) from exc
        frozen = contract.model_dump(by_alias=True, mode="json")
        request = {
            "contract": frozen,
            "idempotencyKey": contract.publication_idempotency_key,
        }
        info = workflow.info()
        prepared: dict[str, Any] = {}
        push: dict[str, Any] | None = None
        pull_request: dict[str, Any] | None = None
        outcome: str | None = None
        reason: str | None = None
        in_flight: str | None = None
        try:
            self._phase = "optional_workspace_restoration"
            prepared = await self._to_completion(
                "publication_recovery.saved_work_prepare",
                {
                    **request,
                    "destinationWorkflowId": info.workflow_id,
                    "destinationRunId": info.run_id,
                },
                task_queue=AGENT_RUNTIME_TASK_QUEUE,
            )
            if (prepared.get("candidate") or {}).get("noChange"):
                outcome, reason = "no_change", "saved_content_matches_destination"
            elif self._cancellation is None:
                self._phase = "publication_operation"
                in_flight = "push"
                push = await self._effect(
                    "publication_recovery.saved_work_push",
                    {**request, "prepared": prepared},
                    task_queue=AGENT_RUNTIME_TASK_QUEUE,
                )
                in_flight = None
                if push.get("status") not in {"pushed", "reconciled"}:
                    outcome, reason = "conflict", str(push.get("reasonCode") or "")
                elif contract.destination.objective == "branch":
                    outcome, reason = "published", str(push.get("reasonCode") or "")
                elif self._cancellation is None:
                    in_flight = "pullRequest"
                    pull_request = await self._effect(
                        "publication_recovery.saved_work_pull_request",
                        {**request, "prepared": prepared, "push": push},
                        task_queue=INTEGRATIONS_TASK_QUEUE,
                    )
                    in_flight = None
                    status = str(pull_request.get("status") or "")
                    if status in {"created", "adopted"}:
                        outcome, reason = "published", f"pull_request_{status}"
                    elif status in {"rejected", "unavailable"}:
                        outcome, reason = status, f"pull_request_{status}"
                    else:
                        outcome, reason = "conflict", f"pull_request_{status}"
        except (ActivityError, asyncio.CancelledError) as exc:
            if is_cancelled_exception(exc):
                self._cancellation = self._cancellation or exc
                code = "publication_cancelled"
            else:
                outcome, reason = _activity_failure(exc)
                code = reason
            # An effect Activity that returned no result is unconfirmed: the
            # next attempt reconciles the remote before acting again.
            unconfirmed: dict[str, Any] = {"status": "unconfirmed", "reasonCode": code}
            if self._interrupted_attempt:
                # The failed attempt a cancellation stopped retrying.
                unconfirmed["lastAttemptReasonCode"] = self._interrupted_attempt
            if in_flight == "push":
                push = unconfirmed
            elif in_flight == "pullRequest":
                pull_request = unconfirmed
        if outcome is None:
            # Only a cancellation stops the publication before a decision.
            outcome, reason = "cancelled", "publication_cancelled"

        result = {
            "schemaVersion": SAVED_WORK_PUBLICATION_RESULT_SCHEMA_VERSION,
            "sourceWorkflowId": contract.source_workflow_id,
            "sourceRunId": contract.source_run_id,
            "destinationWorkflowId": info.workflow_id,
            "destinationRunId": info.run_id,
            "publicationIdempotencyKey": contract.publication_idempotency_key,
            "savedWorkRef": contract.saved_work_ref,
            "savedWorkDigest": contract.saved_work_digest,
            "outcome": outcome,
            "reasonCode": reason or None,
            "admission": prepared.get("admission"),
            "decisionDigest": prepared.get("decisionDigest"),
            "candidate": prepared.get("candidate"),
            "push": push,
            "pullRequest": pull_request,
        }
        try:
            self._phase = "artifact_summary_persistence"
            self._result = await self._to_completion(
                "publication_recovery.persist_result",
                {
                    **request,
                    "savedWorkResult": result,
                    "destinationWorkflowId": info.workflow_id,
                    "destinationRunId": info.run_id,
                },
                task_queue=ARTIFACTS_TASK_QUEUE,
            )
        finally:
            self._phase = "cleanup"
            try:
                await self._to_completion(
                    "publication_recovery.cleanup",
                    request,
                    task_queue=AGENT_RUNTIME_TASK_QUEUE,
                )
            except Exception:
                # Releasing the saved-work use claims is auxiliary; their TTL
                # bounds a missed release and never overwrites persisted evidence.
                pass
        if self._cancellation is not None:
            # The record keeps every confirmed effect; the run honors the
            # cancellation request.
            raise self._cancellation
        if outcome not in {"published", "no_change"}:
            raise ApplicationError(
                f"saved-work publication {outcome}: {reason}",
                type=(
                    "PUBLICATION_RECONCILIATION_BLOCKED"
                    if outcome == "conflict"
                    else reason or "PUBLICATION_UNAVAILABLE"
                ),
                non_retryable=True,
            )
        self._phase = "completed"
        return self._result


__all__ = ["MoonMindPublicationRecoveryWorkflow", "WORKFLOW_NAME"]
