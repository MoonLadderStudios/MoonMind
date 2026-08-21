"""Durable Omnigent session supervisor.

Source: MoonLadderStudios/MoonMind#3705.

``MoonMind.OmnigentSession`` owns one canonical session lifecycle. It drives the
pure reconciler and invokes only short, idempotent Activities; provider streams
and callbacks are wake sources, while the periodic snapshot timer remains the
correctness backstop for lost terminal edges.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timedelta
from typing import Any, Mapping

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import CancelledError

with workflow.unsafe.imports_passed_through():
    from moonmind.omnigent.reconciler import (
        DecisionKind,
        DurableSessionState,
        ObservationSet,
        ReconciliationDecision,
        CompiledSessionIntent,
        reconcile,
    )
    from moonmind.schemas.agent_runtime_models import AgentRunResult
    from moonmind.schemas.omnigent_session_models import (
        OmnigentPersistDecisionRequest,
        OmnigentPersistFailureRequest,
        OmnigentPersistSignalsRequest,
        OmnigentFailureAuthorityRequest,
        OmnigentSessionActivityRequest,
        OmnigentSessionContinueAsNewState,
        OmnigentSessionSignal,
        OmnigentSessionTerminalResult,
        OmnigentSessionWorkflowInput,
    )
    from moonmind.workflows.temporal.activity_catalog import (
        TemporalActivityRoute,
        build_default_activity_catalog,
    )


WORKFLOW_TYPE = "MoonMind.OmnigentSession"
DEFAULT_ACTIVITY_CATALOG = build_default_activity_catalog()

SNAPSHOT_INTERVAL_SECONDS = 30
TIMEOUT_SNAPSHOT_MAX_ATTEMPTS = 3
CONTINUE_AS_NEW_DECISION_THRESHOLD = 100
CONTINUE_AS_NEW_OBSERVATION_THRESHOLD = 500
CONTINUE_AS_NEW_HISTORY_LENGTH_THRESHOLD = 2_000
CONTINUE_AS_NEW_SESSION_AGE_SECONDS = 86_400
CONTINUE_AS_NEW_TURN_ATTEMPT_THRESHOLD = 20
MAX_PENDING_SIGNAL_INTENTS = 100
# The janitor reclaims an assigned host this long after its last heartbeat,
# independently of the much longer lease expiry.
HOST_LEASE_HEARTBEAT_TIMEOUT_SECONDS = 90

# A reconciler decision may authorize several independently retryable cleanup or
# publication phases, but never more than this fixed, bounded sequence.
BOUNDED_COMMAND_ACTIVITIES: dict[DecisionKind, tuple[str, ...]] = {
    DecisionKind.ENSURE_PROFILE_LEASE: (
        "omnigent.ensure_provider_profile_lease",
    ),
    DecisionKind.ENSURE_HOST: ("omnigent.ensure_host",),
    DecisionKind.ENSURE_PROVIDER_SESSION: ("omnigent.ensure_provider_session",),
    DecisionKind.SUBMIT_TURN: ("omnigent.submit_turn",),
    DecisionKind.RECORD_PROVIDER_TERMINAL: ("omnigent.record_terminal",),
    DecisionKind.SYNTHESIZE_TERMINAL_FROM_SNAPSHOT: (
        "omnigent.record_terminal",
    ),
    DecisionKind.HARVEST_EVIDENCE: (
        "omnigent.harvest_evidence",
        "omnigent.publish_workspace",
    ),
    DecisionKind.BEGIN_CLEANUP: (
        "omnigent.stop_provider_session",
        "omnigent.stop_host",
    ),
    DecisionKind.RELEASE_LEASES: ("omnigent.release_leases",),
}

_TERMINAL_FAILURE_DECISIONS = frozenset(
    {
        DecisionKind.FAIL_NONRETRYABLE,
        DecisionKind.QUARANTINE_AMBIGUOUS_STATE,
    }
)
_CLEANUP_ACTIVITIES = frozenset(
    {
        "omnigent.stop_provider_session",
        "omnigent.stop_host",
        "omnigent.release_leases",
    }
)
_PROVIDER_INTEGRATION_ACTIVITIES = frozenset(
    {
        "omnigent.ensure_provider_profile_lease",
        "omnigent.ensure_host",
        "omnigent.ensure_provider_session",
        "omnigent.load_reconciliation_inputs",
        "omnigent.read_event_batch",
        "omnigent.observe_snapshot",
        "omnigent.harvest_evidence",
        "omnigent.publish_workspace",
        "omnigent.record_terminal",
    }
)


def _is_revision_conflict(error: BaseException) -> bool:
    """Recognize the typed Activity failure without parsing error prose."""

    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if type(current).__name__ == "RevisionConflictError":
            return True
        if getattr(current, "type", None) == "RevisionConflictError":
            return True
        cause = getattr(current, "cause", None)
        current = cause if isinstance(cause, BaseException) else None
    return False


def _is_cancellation_failure(error: BaseException) -> bool:
    """Recognize direct or Activity-wrapped Temporal cancellation."""

    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, (CancelledError, asyncio.CancelledError)):
            return True
        cause = getattr(current, "cause", None) or getattr(
            current, "__cause__", None
        )
        current = cause if isinstance(cause, BaseException) else None
    return False


def canonical_omnigent_session_id(
    *, workflow_id: str, step_execution_id: str, agent_run_id: str
) -> str:
    """Return the canonical identity derived only from durable owner scope."""

    authority = json.dumps(
        ["omnigent-session/v1", workflow_id, step_execution_id, agent_run_id],
        separators=(",", ":"),
    ).encode("utf-8")
    return "oms_" + hashlib.sha256(authority).hexdigest()[:40]


def canonical_omnigent_turn_attempt_id(session_id: str, ordinal: int = 1) -> str:
    authority = f"omnigent-turn/v1:{session_id}:{ordinal}".encode("utf-8")
    return "ota_" + hashlib.sha256(authority).hexdigest()[:40]


def omnigent_session_workflow_id(session_id: str) -> str:
    return f"omnigent-session:{session_id}"


@workflow.defn(name=WORKFLOW_TYPE)
class MoonMindOmnigentSessionWorkflow:
    """Reconcile one canonical Omnigent session to durable closure."""

    def __init__(self) -> None:
        self._input: OmnigentSessionWorkflowInput | None = None
        self._wake_sequence = 0
        self._last_consumed_wake_sequence = 0
        self._pending_signal_intents: list[dict[str, Any]] = []
        self._dropped_signal_intents = 0
        self._host_lease_heartbeat: str | None = None
        self._cancel_requested = False
        self._cleanup_requested = False
        self._phase = "initializing"
        self._last_reason_code: str | None = None
        self._last_decision_kind: str | None = None
        self._last_revision: int | None = None
        self._last_fencing_generation = 0
        self._last_event_cursor: str | None = None
        self._last_snapshot_frontier: str | None = None
        self._terminal_status: str | None = None
        self._terminal_result_ref: str | None = None
        self._cleanup_evidence_ref: str | None = None
        self._active_activity: str | None = None
        self._last_failed_activity: str | None = None
        self._active_decision_id: str | None = None
        self._active_command_id: str | None = None
        self._timeout_snapshot_observed = False
        self._timeout_snapshot_attempt_count = 0
        self._decision_count = 0
        self._observation_count = 0
        self._turn_attempt_count = 1
        self._segment_initial_turn_attempt_count = 1
        self._continue_as_new_count = 0
        self._segment_decision_count = 0
        self._segment_observation_count = 0
        self._session_started_at: datetime | None = None
        self._segment_started_at: datetime | None = None

    def _initialize(self, session_input: OmnigentSessionWorkflowInput) -> None:
        self._input = session_input
        carried = session_input.resume_state
        if carried is not None:
            self._continue_as_new_count = carried.continue_as_new_count
            self._decision_count = carried.decision_count
            self._observation_count = carried.observation_count
            self._turn_attempt_count = carried.turn_attempt_count
            self._segment_initial_turn_attempt_count = carried.turn_attempt_count
            self._last_revision = carried.last_session_revision
            self._last_event_cursor = carried.last_event_cursor
            self._last_snapshot_frontier = carried.last_snapshot_frontier
            self._terminal_result_ref = carried.terminal_result_ref
            self._session_started_at = carried.session_started_at

    @staticmethod
    def _retry_policy(route: TemporalActivityRoute) -> RetryPolicy:
        return RetryPolicy(
            initial_interval=timedelta(seconds=1),
            backoff_coefficient=2.0,
            maximum_interval=timedelta(
                seconds=route.retries.max_interval_seconds
            ),
            maximum_attempts=route.retries.max_attempts,
            non_retryable_error_types=list(route.retries.non_retryable_error_codes),
        )

    async def _execute_activity(
        self, activity_name: str, payload: Mapping[str, Any]
    ) -> object:
        route = DEFAULT_ACTIVITY_CATALOG.resolve_activity(activity_name)
        kwargs: dict[str, Any] = {
            "task_queue": route.task_queue,
            "start_to_close_timeout": timedelta(
                seconds=route.timeouts.start_to_close_seconds
            ),
            "schedule_to_close_timeout": timedelta(
                seconds=route.timeouts.schedule_to_close_seconds
            ),
            "retry_policy": self._retry_policy(route),
            "summary": f"Reconcile Omnigent session: {activity_name}",
        }
        if route.timeouts.heartbeat_timeout_seconds is not None:
            kwargs["heartbeat_timeout"] = timedelta(
                seconds=route.timeouts.heartbeat_timeout_seconds
            )
        self._active_activity = activity_name
        try:
            result = await workflow.execute_activity(
                activity_name,
                dict(payload),
                **kwargs,
            )
            self._last_failed_activity = None
            return result
        except BaseException as exc:
            if not _is_cancellation_failure(exc):
                self._last_failed_activity = activity_name
            raise
        finally:
            self._active_activity = None

    def _require_input(self) -> OmnigentSessionWorkflowInput:
        if self._input is None:
            raise RuntimeError("Omnigent session workflow input is not initialized")
        return self._input

    def _base_activity_request(
        self,
        durable: DurableSessionState,
        decision: ReconciliationDecision | None = None,
    ) -> OmnigentSessionActivityRequest:
        session_input = self._require_input()
        command = decision.command if decision is not None else None
        return OmnigentSessionActivityRequest(
            sessionId=session_input.session_id,
            compiledExecutionIntentRef=session_input.compiled_execution_intent_ref,
            compiledExecutionIntentDigest=(
                session_input.compiled_execution_intent_digest
            ),
            omnigentExecutionPlan=session_input.omnigent_execution_plan,
            expectedRevision=durable.revision,
            fencingGeneration=durable.fencing_generation,
            runtimeBindingRef=durable.runtime_binding_ref,
            runtimeBindingRevision=durable.runtime_binding_revision,
            runtimeBindingFencingGeneration=(
                durable.runtime_binding_fencing_generation
            ),
            decisionId=(
                f"{session_input.session_id}:decision:{self._decision_count + 1}"
                if decision is not None
                else None
            ),
            commandId=command.command_id if command is not None else None,
            turnAttemptId=command.attempt_id if command is not None else None,
            terminalOutcome=(
                command.terminal_outcome.value
                if command is not None and command.terminal_outcome is not None
                else None
            ),
        )

    def _update_visibility(self) -> None:
        session_input = self._require_input()
        try:
            workflow.set_current_details(
                " | ".join(
                    filter(
                        None,
                        (
                            "Durable Omnigent session",
                            f"session={session_input.session_id}",
                            f"phase={self._phase}",
                            (
                                f"decision={self._last_decision_kind}"
                                if self._last_decision_kind
                                else None
                            ),
                            (
                                f"reason={self._last_reason_code}"
                                if self._last_reason_code
                                else None
                            ),
                        ),
                    )
                )
            )
            workflow.upsert_search_attributes(
                {
                    "AgentRunId": [session_input.agent_run_id],
                    "SessionId": [session_input.session_id],
                    "SessionStatus": [self._phase],
                    "IsDegraded": [
                        self._terminal_status
                        in {
                            "integration_unavailable",
                            "delivery_unknown",
                            "reconciliation_quarantined",
                            "cleanup_incomplete",
                        }
                    ],
                }
            )
        except Exception as exc:
            # Direct unit construction is outside a workflow context.
            if "NotInWorkflow" in type(exc).__name__ or "Not in workflow" in str(exc):
                return
            raise

    @staticmethod
    def _coerce_terminal_result(
        payload: object,
        *,
        fallback_status: str,
        fallback_summary: str,
    ) -> OmnigentSessionTerminalResult:
        if isinstance(payload, OmnigentSessionTerminalResult):
            return payload
        if isinstance(payload, Mapping):
            raw = payload.get("terminalResult") or payload.get("terminal_result")
            if isinstance(raw, Mapping):
                return OmnigentSessionTerminalResult.model_validate(raw)
        failure_class = (
            "integration_error"
            if fallback_status
            in {"integration_unavailable", "delivery_unknown"}
            else "execution_error"
        )
        return OmnigentSessionTerminalResult(
            status=fallback_status,
            result=AgentRunResult(
                summary=fallback_summary,
                failureClass=failure_class,
                metadata={"omnigentSessionStatus": fallback_status},
            ),
        )

    async def _persist_pending_signal_intents(
        self, durable: DurableSessionState
    ) -> bool:
        if not self._pending_signal_intents:
            return False
        pending = tuple(self._pending_signal_intents)
        request = self._base_activity_request(durable)
        await self._execute_activity(
            "omnigent.persist_signal_intents",
            OmnigentPersistSignalsRequest(
                **request.model_dump(mode="python", by_alias=True),
                signals=pending,
            ).model_dump(mode="json", by_alias=True, exclude_none=True),
        )
        del self._pending_signal_intents[: len(pending)]
        return True

    @staticmethod
    def _activity_failure_status(activity_name: str) -> str:
        if activity_name in _CLEANUP_ACTIVITIES:
            return "cleanup_incomplete"
        if activity_name == "omnigent.submit_turn":
            return "delivery_unknown"
        if activity_name in _PROVIDER_INTEGRATION_ACTIVITIES:
            return "integration_unavailable"
        return "execution_failed"

    async def _persist_failure(
        self,
        *,
        durable: DurableSessionState,
        status: str,
        failed_activity: str,
        reason_code: str,
    ) -> OmnigentSessionTerminalResult | None:
        request = self._base_activity_request(durable)
        failure_payload = request.model_dump(mode="python", by_alias=True)
        failure_payload.update(
            {
                "decisionId": self._active_decision_id,
                "commandId": self._active_command_id,
                "status": status,
                "failedActivity": failed_activity,
                "reasonCode": reason_code,
            }
        )
        payload = OmnigentPersistFailureRequest.model_validate(
            failure_payload
        ).model_dump(mode="json", by_alias=True, exclude_none=True)
        persisted = await self._execute_activity(
            "omnigent.persist_failure", payload
        )
        if isinstance(persisted, Mapping) and persisted.get("reconcileRequired"):
            return None
        terminal = self._coerce_terminal_result(
            persisted,
            fallback_status=status,
            fallback_summary="Omnigent failure evidence could not be decoded",
        )
        self._terminal_status = terminal.status
        self._terminal_result_ref = terminal.result_ref
        if terminal.status == "cleanup_incomplete":
            self._cleanup_evidence_ref = terminal.result_ref
        self._phase = terminal.status
        self._update_visibility()
        return terminal

    async def _load_failure_authority(
        self, session_input: OmnigentSessionWorkflowInput
    ) -> DurableSessionState:
        loaded = await self._execute_activity(
            "omnigent.load_failure_authority",
            OmnigentFailureAuthorityRequest(
                sessionId=session_input.session_id,
                compiledExecutionIntentRef=(
                    session_input.compiled_execution_intent_ref
                ),
                compiledExecutionIntentDigest=(
                    session_input.compiled_execution_intent_digest
                ),
                workflowId=session_input.workflow_id,
                stepExecutionId=session_input.step_execution_id,
                agentRunId=session_input.agent_run_id,
            ).model_dump(mode="json", by_alias=True),
        )
        if not isinstance(loaded, Mapping):
            raise ValueError("omnigent.load_failure_authority returned no mapping")
        return DurableSessionState(
            sessionId=session_input.session_id,
            revision=int(loaded.get("revision") or 0),
            ownerToken=f"omnigent-session:{session_input.session_id}",
            fencingGeneration=int(loaded.get("fencingGeneration") or 0),
        )

    async def _heartbeat_host_lease(self, durable: DurableSessionState) -> None:
        """Hold the durable host lease for one more poll cycle.

        The janitor reclaims an assigned host
        ``HOST_LEASE_HEARTBEAT_TIMEOUT_SECONDS`` after its last heartbeat, which
        is shorter than a normal turn, so renewal has to happen on every cycle
        and not only at launch. Renewal is not authority: a lease already owned by
        another cleanup owner is recorded and left to the reconciler rather than
        failing the session here.
        """

        request = self._base_activity_request(durable).model_dump(
            mode="json", by_alias=True, exclude_none=True
        )
        result = await self._execute_activity(
            "omnigent.heartbeat_host_lease", request
        )
        if isinstance(result, Mapping):
            self._host_lease_heartbeat = str(
                result.get("hostLeaseHeartbeat") or ""
            ) or None

    async def _observe_after_wait(self, durable: DurableSessionState) -> bool:
        request = self._base_activity_request(durable).model_dump(
            mode="json", by_alias=True, exclude_none=True
        )
        await self._heartbeat_host_lease(durable)
        batch = await self._execute_activity("omnigent.read_event_batch", request)
        snapshot = await self._execute_activity("omnigent.observe_snapshot", request)
        for result in (batch, snapshot):
            if isinstance(result, Mapping):
                count = int(result.get("observationCount") or 0)
                self._observation_count += max(0, count)
                self._segment_observation_count += max(0, count)
                cursor = result.get("eventCursor")
                frontier = result.get("snapshotFrontier")
                if cursor is not None:
                    self._last_event_cursor = str(cursor)
                if frontier is not None:
                    self._last_snapshot_frontier = str(frontier)
        return not (
            isinstance(snapshot, Mapping)
            and snapshot.get("readStatus") == "unavailable"
        )

    async def _await_wake_or_snapshot_deadline(
        self, decision: ReconciliationDecision
    ) -> None:
        consumed = self._wake_sequence
        self._last_consumed_wake_sequence = consumed
        now = workflow.now()
        deadline = decision.next_deadline or (
            now + timedelta(seconds=SNAPSHOT_INTERVAL_SECONDS)
        )
        wait_seconds = max(
            0.001,
            min(
                SNAPSHOT_INTERVAL_SECONDS,
                (deadline - now).total_seconds(),
            ),
        )
        try:
            await workflow.wait_condition(
                lambda: self._wake_sequence > consumed,
                timeout=timedelta(seconds=wait_seconds),
            )
        except (asyncio.TimeoutError, TimeoutError):
            # Timing out is the normal path: no wake signal arrived before the
            # snapshot deadline, so fall through and let the caller take the
            # next scheduled provider snapshot.
            pass

    def _should_continue_as_new(self) -> bool:
        if self._terminal_status is not None:
            return False
        try:
            info = workflow.info()
        except Exception:
            return False
        suggested = getattr(info, "is_continue_as_new_suggested", False)
        if suggested() if callable(suggested) else bool(suggested):
            return True
        history_length = getattr(info, "get_current_history_length", None)
        if callable(history_length) and (
            history_length() >= CONTINUE_AS_NEW_HISTORY_LENGTH_THRESHOLD
        ):
            return True
        if self._segment_decision_count >= CONTINUE_AS_NEW_DECISION_THRESHOLD:
            return True
        if self._segment_observation_count >= CONTINUE_AS_NEW_OBSERVATION_THRESHOLD:
            return True
        if (
            self._turn_attempt_count - self._segment_initial_turn_attempt_count
            >= CONTINUE_AS_NEW_TURN_ATTEMPT_THRESHOLD
        ):
            return True
        if self._segment_started_at is not None:
            return (
                workflow.now() - self._segment_started_at
            ).total_seconds() >= CONTINUE_AS_NEW_SESSION_AGE_SECONDS
        return False

    def _build_continue_as_new_input(self) -> OmnigentSessionWorkflowInput:
        session_input = self._require_input()
        carried = OmnigentSessionContinueAsNewState(
            continueAsNewCount=self._continue_as_new_count + 1,
            decisionCount=self._decision_count,
            observationCount=self._observation_count,
            turnAttemptCount=self._turn_attempt_count,
            lastSessionRevision=self._last_revision,
            lastEventCursor=self._last_event_cursor,
            lastSnapshotFrontier=self._last_snapshot_frontier,
            terminalResultRef=self._terminal_result_ref,
            sessionStartedAt=self._session_started_at,
        )
        return session_input.model_copy(update={"resume_state": carried})

    @workflow.run
    async def run(
        self, session_input: OmnigentSessionWorkflowInput
    ) -> AgentRunResult:
        self._initialize(session_input)
        if self._session_started_at is None:
            self._session_started_at = workflow.now()
        self._segment_started_at = workflow.now()
        self._update_visibility()

        while True:
            try:
                return await self._run_until_terminal(session_input)
            except (CancelledError, asyncio.CancelledError):
                await self._resume_cancelled_session(session_input)
                continue
            except Exception as exc:
                if _is_cancellation_failure(exc):
                    await self._resume_cancelled_session(session_input)
                    continue
                failed_activity = self._last_failed_activity
                if failed_activity is None:
                    raise
                if failed_activity in {
                    "omnigent.load_failure_authority",
                    "omnigent.persist_failure",
                }:
                    raise
                # Always refresh the canonical fence. The failed phase may
                # have advanced session revision before its last retry, and a
                # previously loaded workflow-side value must not authorize the
                # failure writer.
                durable = await self._load_failure_authority(session_input)
                try:
                    status = self._activity_failure_status(failed_activity)
                    if self._terminal_status in {
                        "integration_unavailable",
                        "execution_failed",
                        "delivery_unknown",
                        "reconciliation_quarantined",
                    }:
                        # The primary outcome is already durable. If a later
                        # bounded phase cannot make cleanup progress, hand the
                        # remaining resources to the janitor instead of
                        # repeatedly rewriting the primary failure forever.
                        status = "cleanup_incomplete"
                    terminal = await self._persist_failure(
                        durable=durable,
                        status=status,
                        failed_activity=failed_activity,
                        reason_code="bounded_activity_exhausted",
                    )
                except Exception as persist_exc:
                    if _is_revision_conflict(persist_exc):
                        self._last_failed_activity = None
                        continue
                    raise
                if terminal is None:
                    self._last_failed_activity = None
                    continue
                if terminal.status == "cleanup_incomplete":
                    return terminal.result
                # The failure Activity attached compact terminal evidence. Run
                # the normal reconciler again so cleanup and release remain
                # distinct durable commands rather than exception unwinding.
                self._last_failed_activity = None

    async def _resume_cancelled_session(
        self, session_input: OmnigentSessionWorkflowInput
    ) -> None:
        """Convert child cancellation into durable intent and ordered cleanup."""

        current_task = asyncio.current_task()
        uncancel = getattr(current_task, "uncancel", None)
        if callable(uncancel):
            uncancel()
        self._cancel_requested = True
        if not any(
            item.get("kind") == "cancel_or_interrupt_requested"
            for item in self._pending_signal_intents
        ):
            self._pending_signal_intents.append(
                {
                    "kind": "cancel_or_interrupt_requested",
                    "payload": {
                        "requestId": f"{session_input.session_id}:workflow-cancel",
                        "reasonCode": "agent_run_cancelled",
                    },
                }
            )
        self._wake()

    async def _run_until_terminal(
        self, session_input: OmnigentSessionWorkflowInput
    ) -> AgentRunResult:

        while True:
            self._active_decision_id = None
            self._active_command_id = None
            loaded = await self._execute_activity(
                "omnigent.load_reconciliation_inputs",
                {
                    "sessionId": session_input.session_id,
                    "compiledExecutionIntentRef": (
                        session_input.compiled_execution_intent_ref
                    ),
                    "compiledExecutionIntentDigest": (
                        session_input.compiled_execution_intent_digest
                    ),
                    "omnigentExecutionPlan": (
                        session_input.omnigent_execution_plan.model_dump(
                            mode="json", by_alias=True
                        )
                        if session_input.omnigent_execution_plan is not None
                        else None
                    ),
                },
            )
            if not isinstance(loaded, Mapping):
                raise ValueError("omnigent.load_reconciliation_inputs returned no mapping")
            intent = CompiledSessionIntent.model_validate(loaded.get("intent"))
            durable = DurableSessionState.model_validate(loaded.get("durable"))
            observations = ObservationSet.model_validate(
                loaded.get("observations") or {}
            )
            self._last_revision = durable.revision
            self._last_fencing_generation = durable.fencing_generation
            self._last_event_cursor = durable.last_cursor
            self._last_snapshot_frontier = durable.last_snapshot_digest
            self._turn_attempt_count = max(
                self._turn_attempt_count, durable.turn_attempts
            )

            timeout_elapsed = False
            timeout_at_raw = loaded.get("timeoutAt")
            if timeout_at_raw and durable.terminal_outcome is None:
                timeout_at = datetime.fromisoformat(
                    str(timeout_at_raw).replace("Z", "+00:00")
                )
                timeout_elapsed = workflow.now() >= timeout_at

            # Persisted signal intent mutates canonical revision/desired state.
            # Re-load before reconciling so no command is issued against the
            # stale pre-signal fence.
            try:
                if await self._persist_pending_signal_intents(durable):
                    continue
            except Exception as exc:
                if _is_revision_conflict(exc):
                    continue
                raise

            decision = reconcile(
                intent=intent,
                durable=durable,
                observations=observations,
                now=workflow.now(),
            )

            # A workflow-side deadline is intent, not proof that the provider
            # failed. Reconcile one fresh bounded event batch and authoritative
            # snapshot before recording timeout intent. If that observation
            # proves terminality, the reducer's terminal command wins; otherwise
            # the next iteration persists timeout under freshly loaded authority.
            if (
                timeout_elapsed
                and durable.desired.value == "run"
                and decision.kind
                not in {
                    DecisionKind.RECORD_PROVIDER_TERMINAL,
                    DecisionKind.SYNTHESIZE_TERMINAL_FROM_SNAPSHOT,
                    *_TERMINAL_FAILURE_DECISIONS,
                }
            ):
                if not self._timeout_snapshot_observed:
                    self._timeout_snapshot_attempt_count += 1
                    self._timeout_snapshot_observed = (
                        await self._observe_after_wait(durable)
                    )
                    if self._timeout_snapshot_observed:
                        # The observation Activities persisted a fresh canonical
                        # frontier. Reload it before recording timeout intent so
                        # terminal provider evidence always wins the deadline
                        # race, including when the terminal stream edge was lost.
                        continue
                    if (
                        self._timeout_snapshot_attempt_count
                        < TIMEOUT_SNAPSHOT_MAX_ATTEMPTS
                    ):
                        await self._await_wake_or_snapshot_deadline(decision)
                        continue
                self._pending_signal_intents.append(
                    {
                        "kind": "timeout_requested",
                        "payload": {
                            "requestId": f"{session_input.session_id}:timeout",
                            "reasonCode": "execution_deadline_elapsed",
                        },
                    }
                )
                try:
                    await self._persist_pending_signal_intents(durable)
                except Exception as exc:
                    if _is_revision_conflict(exc):
                        continue
                    raise
                continue
            self._last_decision_kind = decision.kind.value
            self._last_reason_code = decision.reason_code.value
            self._phase = str(loaded.get("phase") or decision.kind.value)
            self._update_visibility()

            base = self._base_activity_request(durable, decision)
            self._active_decision_id = base.decision_id
            self._active_command_id = base.command_id
            try:
                await self._execute_activity(
                    "omnigent.persist_decision",
                    OmnigentPersistDecisionRequest(
                        **base.model_dump(mode="python", by_alias=True),
                        decision=decision.model_dump(
                            mode="json", by_alias=True, exclude_none=True
                        ),
                    ).model_dump(mode="json", by_alias=True, exclude_none=True),
                )
            except Exception as exc:
                if _is_revision_conflict(exc):
                    continue
                raise
            self._decision_count += 1
            self._segment_decision_count += 1

            phases = BOUNDED_COMMAND_ACTIVITIES.get(decision.kind, ())
            if phases:
                command_payload = base.model_dump(
                    mode="json", by_alias=True, exclude_none=True
                )
                last_result: object = None
                reload_authority = False
                for activity_name in phases:
                    try:
                        last_result = await self._execute_activity(
                            activity_name, command_payload
                        )
                    except Exception as exc:
                        if _is_revision_conflict(exc):
                            reload_authority = True
                            break
                        raise
                if reload_authority:
                    continue
                if isinstance(last_result, Mapping):
                    result_ref = last_result.get("terminalResultRef")
                    if result_ref:
                        self._terminal_result_ref = str(result_ref)
                continue

            if decision.kind in _TERMINAL_FAILURE_DECISIONS:
                status = (
                    "reconciliation_quarantined"
                    if decision.kind is DecisionKind.QUARANTINE_AMBIGUOUS_STATE
                    else "execution_failed"
                )
                try:
                    await self._persist_failure(
                        durable=durable,
                        status=status,
                        failed_activity="omnigent.reconcile",
                        reason_code=decision.reason_code.value,
                    )
                except Exception as exc:
                    if _is_revision_conflict(exc):
                        self._last_failed_activity = None
                        continue
                    raise
                # Re-load the newly terminal canonical state and continue into
                # the same ordered cleanup/release chain as provider terminals.
                continue

            if decision.kind is DecisionKind.NO_OP:
                terminal = self._coerce_terminal_result(
                    loaded,
                    fallback_status="reconciliation_quarantined",
                    fallback_summary=(
                        "Omnigent session closed without readable terminal evidence"
                    ),
                )
                self._terminal_status = terminal.status
                self._terminal_result_ref = terminal.result_ref
                self._phase = terminal.status
                self._update_visibility()
                return terminal.result

            if self._should_continue_as_new():
                await workflow.wait_condition(workflow.all_handlers_finished)
                # A signal may arrive after the iteration's initial persistence
                # boundary. Never drop that durable intent during history
                # rollover; reload authority and persist it before continuing.
                if self._pending_signal_intents:
                    continue
                workflow.continue_as_new(self._build_continue_as_new_input())

            await self._await_wake_or_snapshot_deadline(decision)
            await self._observe_after_wait(durable)

    def _wake(self) -> None:
        self._wake_sequence += 1

    def _queue_signal_intent(self, kind: str, payload: OmnigentSessionSignal) -> None:
        intent = {
            "kind": kind,
            "payload": payload.model_dump(
                mode="json", by_alias=True, exclude_none=True
            ),
        }
        # Raising from a signal handler fails the workflow task, and replay
        # re-delivers the same signal against the same full queue, so the
        # supervisor could never drain it again. Deduplicate by request id
        # instead, then record bounded overflow rather than throwing.
        request_id = str(payload.request_id or "").strip()
        if request_id:
            for queued in self._pending_signal_intents:
                if queued.get("kind") == kind and str(
                    (queued.get("payload") or {}).get("requestId") or ""
                ) == request_id:
                    self._wake()
                    return
        if len(self._pending_signal_intents) >= MAX_PENDING_SIGNAL_INTENTS:
            # Cancellation and cleanup are the intents needed to recover a
            # wedged session, so admit one of each past the bound; anything
            # else is counted as dropped and left for the operator-visible
            # overflow signal instead of failing the workflow task.
            recovery_kind = kind in {
                "cancel_or_interrupt_requested",
                "cleanup_requested",
            }
            already_queued = any(
                queued.get("kind") == kind
                for queued in self._pending_signal_intents
            )
            if not recovery_kind or already_queued:
                self._dropped_signal_intents += 1
                self._wake()
                return
        self._pending_signal_intents.append(intent)
        self._wake()

    @workflow.signal(name="provider_observation_available")
    def provider_observation_available(self, payload: OmnigentSessionSignal) -> None:
        self._wake()

    @workflow.signal(name="submit_authorized_continuation")
    def submit_authorized_turn(self, payload: OmnigentSessionSignal) -> None:
        if not payload.turn_attempt_id or not payload.instruction_ref:
            raise ValueError(
                "authorized continuation requires turnAttemptId and instructionRef"
            )
        self._turn_attempt_count += 1
        self._queue_signal_intent("submit_authorized_continuation", payload)

    @workflow.signal(name="cancel_or_interrupt_requested")
    def cancel_or_interrupt_requested(self, payload: OmnigentSessionSignal) -> None:
        self._cancel_requested = True
        self._queue_signal_intent("cancel_or_interrupt_requested", payload)

    @workflow.signal(name="approval_or_intervention_changed")
    def approval_or_intervention_changed(
        self, payload: OmnigentSessionSignal
    ) -> None:
        self._wake()

    @workflow.signal(name="cleanup_requested")
    def cleanup_requested(self, payload: OmnigentSessionSignal) -> None:
        self._cleanup_requested = True
        self._queue_signal_intent("cleanup_requested", payload)

    @workflow.signal(name="operator_reconcile_requested")
    def operator_reconcile_requested(self, payload: OmnigentSessionSignal) -> None:
        self._wake()

    @workflow.signal(name="provider_callback_or_host_exit_recorded")
    def provider_callback_or_host_exit_recorded(
        self, payload: OmnigentSessionSignal
    ) -> None:
        self._wake()

    @workflow.query(name="omnigent_session.state")
    def get_state(self) -> dict[str, Any]:
        session_input = self._require_input()
        return {
            "schemaVersion": "omnigent-session-query/v1",
            "sessionId": session_input.session_id,
            "workflowId": session_input.workflow_id,
            "stepExecutionId": session_input.step_execution_id,
            "agentRunId": session_input.agent_run_id,
            "featureGeneration": session_input.admitted_feature_generation,
            "compatibilityVersion": session_input.compatibility_version,
            "phase": self._phase,
            "lastDecisionKind": self._last_decision_kind,
            "lastReasonCode": self._last_reason_code,
            "lastSessionRevision": self._last_revision,
            "fencingGeneration": self._last_fencing_generation,
            "lastEventCursor": self._last_event_cursor,
            "lastSnapshotFrontier": self._last_snapshot_frontier,
            "decisionCount": self._decision_count,
            "observationCount": self._observation_count,
            "turnAttemptCount": self._turn_attempt_count,
            "continueAsNewCount": self._continue_as_new_count,
            "wakeSequence": self._wake_sequence,
            "cancelRequested": self._cancel_requested,
            "cleanupRequested": self._cleanup_requested,
            "timeoutSnapshotObserved": self._timeout_snapshot_observed,
            "timeoutSnapshotAttemptCount": self._timeout_snapshot_attempt_count,
            "pendingIntentCount": len(self._pending_signal_intents),
            "droppedIntentCount": self._dropped_signal_intents,
            "hostLeaseHeartbeat": self._host_lease_heartbeat,
            "terminalStatus": self._terminal_status,
            "terminalResultRef": self._terminal_result_ref,
            "cleanupEvidenceRef": self._cleanup_evidence_ref,
        }


__all__ = [
    "WORKFLOW_TYPE",
    "HOST_LEASE_HEARTBEAT_TIMEOUT_SECONDS",
    "BOUNDED_COMMAND_ACTIVITIES",
    "MoonMindOmnigentSessionWorkflow",
    "canonical_omnigent_session_id",
    "canonical_omnigent_turn_attempt_id",
    "omnigent_session_workflow_id",
]
