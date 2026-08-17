"""``MoonMind.OmnigentSession`` — durable Omnigent session supervisor.

Implements GitHub issue MoonLadderStudios/MoonMind#3705. This workflow owns one
canonical Omnigent session lifecycle and drives it through the shared pure
reconciler using short, bounded, idempotent activities. It removes correctness
dependence on one long-running streaming activity: provider streams and
callbacks become observation sources that wake reconciliation, while a periodic
authoritative snapshot deadline guarantees eventual convergence after event
loss, worker restart, provider restart, or activity retry.

Ownership hierarchy::

    MoonMind.UserWorkflow
      -> MoonMind.AgentRun
           -> MoonMind.OmnigentSession

The workflow input carries only compact, immutable, versioned authority. Raw
credentials, provider tokens, mutable Docker paths, large prompts, transcripts,
diffs, and artifact bodies never enter workflow history.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

with workflow.unsafe.imports_passed_through():
    from moonmind.omnigent.session_commands import OmnigentSessionCommandOutcome
    from moonmind.omnigent.session_reconciler import (
        OmnigentSessionCommand,
        OmnigentSessionCommandCondition,
        OmnigentSessionCommandKind,
        OmnigentSessionDecision,
        OmnigentSessionFrontier,
        OmnigentSessionIntent,
        OmnigentSessionResult,
        OmnigentSessionSignals,
        OmnigentSessionStatus,
        OmnigentSessionWorkflowInput,
        reconcile_omnigent_session,
        should_continue_as_new,
    )
    from moonmind.workflows.temporal.activity_catalog import (
        TemporalActivityRoute,
        build_default_activity_catalog,
    )

DEFAULT_ACTIVITY_CATALOG = build_default_activity_catalog()

# Commands that advance the observation frontier (used to track snapshot cadence).
_OBSERVATION_COMMANDS = frozenset(
    {
        OmnigentSessionCommandKind.READ_EVENT_BATCH,
        OmnigentSessionCommandKind.OBSERVE_SNAPSHOT,
    }
)

# Statuses where a durable timer must remain active even after progress: the
# workflow is waiting on the provider (periodic snapshot convergence) or backing
# off a transient failure, rather than driving the launch/cleanup sequence.
_WAITING_STATUSES = frozenset(
    {
        OmnigentSessionStatus.AWAITING_OBSERVATION,
        OmnigentSessionStatus.INTEGRATION_UNAVAILABLE,
        OmnigentSessionStatus.DELIVERY_UNKNOWN,
        OmnigentSessionStatus.CLEANUP_INCOMPLETE,
    }
)


@workflow.defn(name="MoonMind.OmnigentSession")
class MoonMindOmnigentSessionWorkflow:
    @workflow.init
    def __init__(self, workflow_input: OmnigentSessionWorkflowInput) -> None:
        self._intent: OmnigentSessionIntent = workflow_input.intent
        self._frontier: OmnigentSessionFrontier = (
            workflow_input.frontier
            if workflow_input.frontier is not None
            else OmnigentSessionFrontier(
                fencingGeneration=1,
                currentTurnAttemptId=workflow_input.intent.initial_turn_attempt_id,
            )
        )
        self._cancel_requested = workflow_input.cancel_requested
        self._cleanup_requested = workflow_input.cleanup_requested
        self._quarantined = workflow_input.quarantined
        self._last_command_condition = OmnigentSessionCommandCondition.OK
        self._status = OmnigentSessionStatus.RESOLVING_INTENT
        self._reason_codes: tuple[str, ...] = ()
        self._signal_epoch = 0
        self._is_degraded = False
        self._session_start_epoch_seconds = workflow_input.session_start_epoch_seconds
        self._last_observation_epoch_seconds = (
            workflow_input.last_observation_epoch_seconds
        )
        self._terminal_result: OmnigentSessionResult | None = None

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _retry_policy_for_route(route: TemporalActivityRoute) -> RetryPolicy:
        return RetryPolicy(
            initial_interval=timedelta(seconds=2),
            backoff_coefficient=2.0,
            maximum_interval=timedelta(seconds=route.retries.max_interval_seconds),
            maximum_attempts=route.retries.max_attempts,
            non_retryable_error_types=list(route.retries.non_retryable_error_codes),
        )

    def _execute_kwargs_for_route(self, route: TemporalActivityRoute) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "task_queue": route.task_queue,
            "start_to_close_timeout": timedelta(
                seconds=route.timeouts.start_to_close_seconds
            ),
            "schedule_to_close_timeout": timedelta(
                seconds=route.timeouts.schedule_to_close_seconds
            ),
            "retry_policy": self._retry_policy_for_route(route),
        }
        if route.timeouts.heartbeat_timeout_seconds is not None:
            kwargs["heartbeat_timeout"] = timedelta(
                seconds=route.timeouts.heartbeat_timeout_seconds
            )
        return kwargs

    async def _execute_activity(
        self, activity_name: str, payload: dict[str, Any]
    ) -> Any:
        route = DEFAULT_ACTIVITY_CATALOG.resolve_activity(activity_name)
        return await workflow.execute_activity(
            activity_name,
            payload,
            summary=activity_name,
            **self._execute_kwargs_for_route(route),
        )

    def _now_seconds(self) -> float:
        return workflow.now().timestamp()

    def _wake(self) -> None:
        self._signal_epoch += 1

    def _search_attributes(self) -> dict[str, list[Any]]:
        return {
            "AgentRunId": [self._intent.agent_run_id],
            "SessionId": [self._intent.canonical_session_id],
            "SessionEpoch": [self._frontier.fencing_generation],
            "SessionStatus": [self._status.value],
            "IsDegraded": [self._is_degraded],
        }

    def _current_details(self) -> str:
        parts = [
            "Omnigent session",
            f"session={self._intent.canonical_session_id}",
            f"agentRun={self._intent.agent_run_id}",
            f"status={self._status.value}",
            f"generation={self._frontier.fencing_generation}",
            f"turnAttempts={self._frontier.turn_attempts}",
            f"observations={self._frontier.observation_count}",
            f"decisions={self._frontier.decision_count}",
        ]
        if self._reason_codes:
            parts.append("reasons=" + ",".join(self._reason_codes))
        if self._is_degraded:
            parts.append("degraded=true")
        return " | ".join(parts)

    def _update_operator_visibility(self) -> None:
        try:
            workflow.set_current_details(self._current_details())
            workflow.upsert_search_attributes(self._search_attributes())
        except Exception as exc:  # pragma: no cover - non-workflow context in unit tests
            if "NotInWorkflow" in type(exc).__name__ or "Not in workflow" in str(exc):
                return
            raise

    # -------------------------------------------------------------------- loop
    @workflow.run
    async def run(
        self, workflow_input: OmnigentSessionWorkflowInput
    ) -> dict[str, Any]:
        del workflow_input  # captured in __init__
        if self._session_start_epoch_seconds is None:
            self._session_start_epoch_seconds = self._now_seconds()
        if self._last_observation_epoch_seconds is None:
            self._last_observation_epoch_seconds = self._session_start_epoch_seconds
        self._update_operator_visibility()

        while True:
            decision = self._reconcile()
            self._status = decision.status
            self._reason_codes = decision.reason_codes
            self._frontier = self._frontier.model_copy(
                update={"decision_count": self._frontier.decision_count + 1}
            )
            self._update_operator_visibility()
            await self._persist_decision(decision)

            if decision.is_terminal:
                self._terminal_result = self._build_terminal_result(decision)
                break

            frontier_before = self._frontier
            for command in decision.commands:
                await self._run_command(command)
            progressed = self._frontier != frontier_before

            if should_continue_as_new(
                self._intent,
                self._frontier,
                history_length=self._history_length(),
            ):
                await workflow.wait_condition(lambda: workflow.all_handlers_finished())
                workflow.continue_as_new(self._build_continue_as_new_input())

            # Advance immediately when a bounded command made progress, except in
            # observation/backoff phases where a durable timer must remain active
            # (so a lost provider event still triggers a periodic snapshot and a
            # failing command backs off instead of hot-looping).
            if progressed and decision.status not in _WAITING_STATUSES:
                continue
            await self._sleep_until_next(decision.next_deadline_seconds)

        await workflow.wait_condition(lambda: workflow.all_handlers_finished())
        return self._terminal_result.model_dump(mode="json", by_alias=True)

    def _reconcile(self) -> OmnigentSessionDecision:
        elapsed = self._now_seconds() - float(self._session_start_epoch_seconds)
        seconds_since_observation = self._now_seconds() - float(
            self._last_observation_epoch_seconds
        )
        signals = OmnigentSessionSignals(
            cancelRequested=self._cancel_requested,
            cleanupRequested=self._cleanup_requested,
            quarantined=self._quarantined,
            lastCommandCondition=self._last_command_condition,
            secondsSinceLastObservation=max(0.0, seconds_since_observation),
        )
        return reconcile_omnigent_session(
            self._intent, self._frontier, signals, elapsed_seconds=elapsed
        )

    async def _persist_decision(self, decision: OmnigentSessionDecision) -> None:
        try:
            await self._execute_activity(
                OmnigentSessionCommandKind.PERSIST_DECISION.value,
                {
                    "intent": self._intent.model_dump(mode="json", by_alias=True),
                    "status": decision.status.value,
                    "reasonCodes": list(decision.reason_codes),
                    "decisionCount": self._frontier.decision_count,
                    "frontier": self._frontier.model_dump(mode="json", by_alias=True),
                },
            )
        except (ActivityError, ApplicationError):
            # Decision persistence is diagnostic; a persistence failure must not
            # stall convergence. The next loop persists again.
            self._is_degraded = True

    async def _run_command(self, command: OmnigentSessionCommand) -> None:
        payload = {
            "intent": self._intent.model_dump(mode="json", by_alias=True),
            "command": command.model_dump(mode="json", by_alias=True),
            "frontier": self._frontier.model_dump(mode="json", by_alias=True),
        }
        try:
            raw = await self._execute_activity(command.kind.value, payload)
        except (ActivityError, ApplicationError):
            # Bounded failure: report the boundary as integration-unavailable so
            # the reconciler re-observes authoritative state rather than blindly
            # repeating a mutating command.
            self._last_command_condition = (
                OmnigentSessionCommandCondition.INTEGRATION_UNAVAILABLE
            )
            self._is_degraded = True
            return

        outcome = OmnigentSessionCommandOutcome.model_validate(dict(raw))

        # Workflow-side fencing: discard a result whose command was issued for a
        # superseded generation (e.g. a delayed activity after a new turn epoch).
        if command.expected_generation != self._frontier.fencing_generation:
            return

        self._frontier = outcome.merged_frontier(self._frontier)
        self._last_command_condition = outcome.condition
        self._is_degraded = False
        if command.kind in _OBSERVATION_COMMANDS:
            self._last_observation_epoch_seconds = self._now_seconds()
            self._frontier = self._frontier.model_copy(
                update={"observation_count": self._frontier.observation_count + 1}
            )

    async def _sleep_until_next(self, next_deadline_seconds: float | None) -> None:
        observed_epoch = self._signal_epoch
        if next_deadline_seconds is None:
            await workflow.wait_condition(
                lambda: self._signal_epoch != observed_epoch
            )
            return
        try:
            await workflow.wait_condition(
                lambda: self._signal_epoch != observed_epoch,
                timeout=timedelta(seconds=max(0.0, next_deadline_seconds)),
            )
        except asyncio.TimeoutError:
            return

    def _history_length(self) -> int:
        info = workflow.info()
        getter = getattr(info, "get_current_history_length", None)
        if callable(getter):
            try:
                return int(getter())
            except Exception:  # pragma: no cover - defensive
                return 0
        return 0

    def _build_terminal_result(
        self, decision: OmnigentSessionDecision
    ) -> OmnigentSessionResult:
        failure_class: str | None = None
        if decision.status is OmnigentSessionStatus.EXECUTION_FAILED:
            failure_class = "execution_failed"
        elif decision.status is OmnigentSessionStatus.TIMED_OUT:
            failure_class = "timed_out"
        elif decision.status is OmnigentSessionStatus.RECONCILIATION_QUARANTINED:
            failure_class = "reconciliation_quarantined"
        elif decision.status is OmnigentSessionStatus.DELIVERY_UNKNOWN:
            failure_class = "delivery_unknown"
        return OmnigentSessionResult(
            status=decision.status,
            canonicalSessionId=self._intent.canonical_session_id,
            agentRunId=self._intent.agent_run_id,
            reasonCodes=decision.reason_codes,
            terminalResultRef=self._frontier.terminal_result_ref,
            diagnosticsRef=self._frontier.diagnostics_ref,
            summary=f"Omnigent session {decision.status.value}",
            failureClass=failure_class,
            turnAttempts=self._frontier.turn_attempts,
            observationCount=self._frontier.observation_count,
            decisionCount=self._frontier.decision_count,
        )

    def _build_continue_as_new_input(self) -> OmnigentSessionWorkflowInput:
        return OmnigentSessionWorkflowInput(
            intent=self._intent,
            frontier=self._frontier,
            sessionStartEpochSeconds=self._session_start_epoch_seconds,
            lastObservationEpochSeconds=self._last_observation_epoch_seconds,
            cancelRequested=self._cancel_requested,
            cleanupRequested=self._cleanup_requested,
            quarantined=self._quarantined,
        )

    # ---------------------------------------------------------------- commands
    @workflow.signal(name="provider_observation_available")
    def provider_observation_available(self, payload: dict[str, Any] | None = None) -> None:
        del payload  # safe refs only; the loop re-resolves authority
        self._wake()

    @workflow.signal(name="provider_callback_or_host_exit")
    def provider_callback_or_host_exit(self, payload: dict[str, Any] | None = None) -> None:
        del payload
        self._wake()

    @workflow.signal(name="submit_turn")
    def submit_turn(self, payload: dict[str, Any] | None = None) -> None:
        payload = dict(payload or {})
        turn_attempt_id = str(payload.get("turnAttemptId") or "").strip() or None
        # Authorize a new turn attempt on a fresh fencing generation so any
        # in-flight command from the prior attempt is fenced out.
        updates: dict[str, Any] = {
            "turn_submitted": False,
            "terminal_observed": False,
            "terminal_outcome": None,
            "turn_attempts": self._frontier.turn_attempts,
            "fencing_generation": self._frontier.fencing_generation + 1,
        }
        if turn_attempt_id is not None:
            updates["current_turn_attempt_id"] = turn_attempt_id
        self._frontier = self._frontier.model_copy(update=updates)
        self._last_command_condition = OmnigentSessionCommandCondition.OK
        self._wake()

    @workflow.signal(name="cancel_or_interrupt")
    def cancel_or_interrupt(self, payload: dict[str, Any] | None = None) -> None:
        del payload
        self._cancel_requested = True
        self._wake()

    @workflow.signal(name="approval_or_intervention_state_changed")
    def approval_or_intervention_state_changed(
        self, payload: dict[str, Any] | None = None
    ) -> None:
        del payload
        self._wake()

    @workflow.signal(name="cleanup_requested")
    def cleanup_requested(self, payload: dict[str, Any] | None = None) -> None:
        del payload
        self._cleanup_requested = True
        self._wake()

    @workflow.signal(name="operator_safe_reconcile")
    def operator_safe_reconcile(self, payload: dict[str, Any] | None = None) -> None:
        del payload
        self._wake()

    @workflow.query(name="get_status")
    def get_status(self) -> dict[str, Any]:
        return {
            "canonicalSessionId": self._intent.canonical_session_id,
            "agentRunId": self._intent.agent_run_id,
            "owningWorkflowId": self._intent.owning_workflow_id,
            "stepExecutionId": self._intent.step_execution_id,
            "status": self._status.value,
            "reasonCodes": list(self._reason_codes),
            "fencingGeneration": self._frontier.fencing_generation,
            "turnAttempts": self._frontier.turn_attempts,
            "observationCount": self._frontier.observation_count,
            "decisionCount": self._frontier.decision_count,
            "cancelRequested": self._cancel_requested,
            "cleanupRequested": self._cleanup_requested,
            "isDegraded": self._is_degraded,
            "frontier": self._frontier.model_dump(mode="json", by_alias=True),
        }
