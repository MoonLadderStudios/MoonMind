"""Bounded, idempotent Temporal activities for ``MoonMind.OmnigentSession``.

Implements the bounded activity model of GitHub issue
MoonLadderStudios/MoonMind#3705. Each activity has a bounded StartToClose
timeout (declared in the activity catalog), executes exactly one durable logical
command through the fenced :class:`OmnigentSessionCommandExecutor`, and is safe
to retry after a crash at every command window. Heartbeat details, if any, are
diagnostic only — no unique correctness state lives in an activity's memory.

These activities are hosted on the workflow fleet (like the checkpoint-branch
turn persistence activities) and share one process-local command executor. The
heavy provider/host realization stays behind the injected provider port, so the
same activities run against a hermetic fake in tests and a production adapter
once admission is enabled. Admission is disabled by default; with no configured
executor the command activities fail closed.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from temporalio import activity

from moonmind.omnigent.session_commands import (
    OmnigentSessionCommandExecutor,
    OmnigentSessionCommandUnavailableError,
)
from moonmind.omnigent.session_reconciler import (
    OmnigentSessionAdmissionPolicy,
    OmnigentSessionCommand,
    OmnigentSessionCommandKind,
    OmnigentSessionFrontier,
    OmnigentSessionIntent,
    OmnigentSessionReconcilePolicy,
    admit_omnigent_session_intent,
)

_EXECUTOR: OmnigentSessionCommandExecutor | None = None


def set_omnigent_session_command_executor(
    executor: OmnigentSessionCommandExecutor | None,
) -> None:
    """Install (or clear) the process-local command executor.

    Production workers wire a provider-backed executor here once admission is
    enabled; tests install a hermetic in-memory executor.
    """

    global _EXECUTOR
    _EXECUTOR = executor


def _require_executor() -> OmnigentSessionCommandExecutor:
    if _EXECUTOR is None:
        raise OmnigentSessionCommandUnavailableError(
            "No Omnigent session command executor is configured on this worker"
        )
    return _EXECUTOR


def _admission_policy_from_env(
    env: Mapping[str, str] | None = None,
) -> OmnigentSessionAdmissionPolicy:
    source = env if env is not None else os.environ

    def _int(key: str, default: int) -> int:
        raw = str(source.get(key, "")).strip()
        if not raw:
            return default
        try:
            return int(raw)
        except ValueError:
            return default

    enabled = str(source.get("OMNIGENT_SESSION_SUPERVISOR_ENABLED", "")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    return OmnigentSessionAdmissionPolicy(
        enabled=enabled,
        admitted_feature_generation=_int(
            "OMNIGENT_SESSION_SUPERVISOR_FEATURE_GENERATION", 1
        ),
        canary_percent=_int("OMNIGENT_SESSION_SUPERVISOR_CANARY_PERCENT", 0),
        compatibility_version=_int("OMNIGENT_SESSION_SUPERVISOR_COMPAT_VERSION", 1),
    )


@activity.defn(name="omnigent.resolve_intent")
async def omnigent_resolve_intent_activity(payload: dict[str, Any]) -> dict[str, Any]:
    """Compile + admit a session intent (recorded in workflow history).

    Deterministic given its inputs and the worker's frozen admission policy, so
    the AgentRun delegation decision is replay-safe.
    """

    payload = dict(payload or {})
    policy = _admission_policy_from_env()
    reconcile_policy = None
    raw_reconcile = payload.get("reconcilePolicy")
    if isinstance(raw_reconcile, Mapping):
        reconcile_policy = OmnigentSessionReconcilePolicy.model_validate(dict(raw_reconcile))

    intent = admit_omnigent_session_intent(
        canonical_session_id=str(payload.get("canonicalSessionId", "")).strip(),
        execution_intent_ref=str(payload.get("executionIntentRef", "")).strip(),
        execution_intent_digest=str(payload.get("executionIntentDigest", "")).strip(),
        owning_workflow_id=str(payload.get("owningWorkflowId", "")).strip(),
        step_execution_id=str(payload.get("stepExecutionId", "")).strip(),
        agent_run_id=str(payload.get("agentRunId", "")).strip(),
        execution_profile_ref=str(payload.get("executionProfileRef", "")).strip(),
        initial_turn_attempt_id=str(payload.get("initialTurnAttemptId", "")).strip(),
        policy=policy,
        reconcile_policy=reconcile_policy,
    )
    if intent is None:
        return {"admitted": False, "intent": None}
    return {
        "admitted": True,
        "intent": intent.model_dump(mode="json", by_alias=True),
    }


@activity.defn(name="omnigent.load_reconciliation_inputs")
async def omnigent_load_reconciliation_inputs_activity(
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Load the durable frontier for the session (idempotent, side-effect free)."""

    payload = dict(payload or {})
    intent = OmnigentSessionIntent.model_validate(dict(payload["intent"]))
    executor = _require_executor()
    record = await executor.store.load(intent.canonical_session_id)
    if record is None:
        frontier = OmnigentSessionFrontier()
    else:
        frontier = record.frontier
    return {"frontier": frontier.model_dump(mode="json", by_alias=True)}


async def _execute_command(payload: dict[str, Any]) -> dict[str, Any]:
    payload = dict(payload or {})
    intent = OmnigentSessionIntent.model_validate(dict(payload["intent"]))
    command = OmnigentSessionCommand.model_validate(dict(payload["command"]))
    executor = _require_executor()
    outcome = await executor.execute(intent, command)
    result = outcome.model_dump(mode="json", by_alias=True)
    frontier_payload = payload.get("frontier")
    if isinstance(frontier_payload, Mapping):
        frontier = OmnigentSessionFrontier.model_validate(dict(frontier_payload))
        result["frontier"] = outcome.merged_frontier(frontier).model_dump(
            mode="json", by_alias=True
        )
    return result


@activity.defn(name="omnigent.ensure_provider_profile_lease")
async def omnigent_ensure_provider_profile_lease_activity(
    payload: dict[str, Any],
) -> dict[str, Any]:
    return await _execute_command(payload)


@activity.defn(name="omnigent.ensure_host")
async def omnigent_ensure_host_activity(payload: dict[str, Any]) -> dict[str, Any]:
    return await _execute_command(payload)


@activity.defn(name="omnigent.ensure_provider_session")
async def omnigent_ensure_provider_session_activity(
    payload: dict[str, Any],
) -> dict[str, Any]:
    return await _execute_command(payload)


@activity.defn(name="omnigent.submit_turn")
async def omnigent_submit_turn_activity(payload: dict[str, Any]) -> dict[str, Any]:
    return await _execute_command(payload)


@activity.defn(name="omnigent.read_event_batch")
async def omnigent_read_event_batch_activity(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a bounded batch of provider observations (never occupies the worker
    for the whole external execution)."""

    return await _execute_command(payload)


@activity.defn(name="omnigent.observe_snapshot")
async def omnigent_observe_snapshot_activity(payload: dict[str, Any]) -> dict[str, Any]:
    """Fetch one authoritative provider snapshot; the convergence source."""

    return await _execute_command(payload)


@activity.defn(name="omnigent.harvest_evidence")
async def omnigent_harvest_evidence_activity(payload: dict[str, Any]) -> dict[str, Any]:
    return await _execute_command(payload)


@activity.defn(name="omnigent.publish_workspace")
async def omnigent_publish_workspace_activity(payload: dict[str, Any]) -> dict[str, Any]:
    return await _execute_command(payload)


@activity.defn(name="omnigent.stop_provider_session")
async def omnigent_stop_provider_session_activity(
    payload: dict[str, Any],
) -> dict[str, Any]:
    return await _execute_command(payload)


@activity.defn(name="omnigent.stop_host")
async def omnigent_stop_host_activity(payload: dict[str, Any]) -> dict[str, Any]:
    return await _execute_command(payload)


@activity.defn(name="omnigent.release_leases")
async def omnigent_release_leases_activity(payload: dict[str, Any]) -> dict[str, Any]:
    return await _execute_command(payload)


@activity.defn(name="omnigent.persist_decision")
async def omnigent_persist_decision_activity(payload: dict[str, Any]) -> dict[str, Any]:
    """Persist the reconciler decision + reason codes (durable evidence).

    Decision bodies stay outside workflow history; only the compact decision
    count is returned to the workflow.
    """

    payload = dict(payload or {})
    intent = OmnigentSessionIntent.model_validate(dict(payload["intent"]))
    decision_count = int(payload.get("decisionCount", 0))
    activity.logger.info(
        "omnigent session decision persisted",
        extra={
            "canonicalSessionId": intent.canonical_session_id,
            "agentRunId": intent.agent_run_id,
            "status": payload.get("status"),
            "reasonCodes": payload.get("reasonCodes"),
            "decisionCount": decision_count,
        },
    )
    return {"persisted": True, "decisionCount": decision_count}


OMNIGENT_SESSION_ACTIVITY_HANDLERS: tuple[Any, ...] = (
    omnigent_resolve_intent_activity,
    omnigent_load_reconciliation_inputs_activity,
    omnigent_ensure_provider_profile_lease_activity,
    omnigent_ensure_host_activity,
    omnigent_ensure_provider_session_activity,
    omnigent_submit_turn_activity,
    omnigent_read_event_batch_activity,
    omnigent_observe_snapshot_activity,
    omnigent_harvest_evidence_activity,
    omnigent_publish_workspace_activity,
    omnigent_stop_provider_session_activity,
    omnigent_stop_host_activity,
    omnigent_release_leases_activity,
    omnigent_persist_decision_activity,
)


__all__ = [
    "OMNIGENT_SESSION_ACTIVITY_HANDLERS",
    "omnigent_ensure_host_activity",
    "omnigent_ensure_provider_profile_lease_activity",
    "omnigent_ensure_provider_session_activity",
    "omnigent_harvest_evidence_activity",
    "omnigent_load_reconciliation_inputs_activity",
    "omnigent_observe_snapshot_activity",
    "omnigent_persist_decision_activity",
    "omnigent_publish_workspace_activity",
    "omnigent_read_event_batch_activity",
    "omnigent_release_leases_activity",
    "omnigent_resolve_intent_activity",
    "omnigent_stop_host_activity",
    "omnigent_stop_provider_session_activity",
    "omnigent_submit_turn_activity",
    "set_omnigent_session_command_executor",
]
