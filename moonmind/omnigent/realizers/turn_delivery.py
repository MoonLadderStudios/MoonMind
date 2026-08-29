"""One fenced canonical-turn delivery wrapper shared by every realizer.

Source: MoonLadderStudios/MoonMind#3707 ([Omnigent control plane 7/11]).

Codex and the generic Omnigent host must use the *same* session and turn
ownership model through their recorded realizers. This helper owns that model
once: derive the canonical turn source from the admitted request, claim the
canonical turn command, run the harness-specific lifecycle, and settle the
command with the outcome the lifecycle actually produced. It contains no harness
lifecycle branches -- the operation it wraps is supplied by the realizer.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from moonmind.omnigent.control_plane.turn_admission import (
    CanonicalTurnAdmissionRejected,
)
from moonmind.omnigent.control_plane.turn_commands import CanonicalSessionBootstrap
from moonmind.omnigent.control_plane.turn_sources import (
    TurnSource,
    coerce_turn_source,
)
from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)
from moonmind.omnigent.turn_authority import canonical_turn_authority
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunResult


logger = logging.getLogger(__name__)


def execution_identity(request: AgentExecutionRequest) -> tuple[str, str]:
    """Return ``(workflow_id, step_execution_id)`` for one admitted request."""

    if request.step_execution is not None:
        return (
            request.step_execution.workflow_id,
            request.step_execution.step_execution_id,
        )
    return request.correlation_id, request.correlation_id


def _canonical_turn_lineage(request: AgentExecutionRequest) -> Any | None:
    """Return the controller-attested lineage this launch carries, if any."""

    if request.step_execution is None:
        return None
    return request.step_execution.canonical_turn_lineage


def canonical_turn_source(request: AgentExecutionRequest) -> TurnSource:
    """Return the closed turn source the admitted request actually carries.

    The source is derived from typed request authority, never from a harness,
    realizer, or Skill name (#3707). ``stepExecution.canonicalTurnLineage`` is
    the launching controller's attestation -- ``workflows/run.py`` builds it from
    workflow-owned loop state and refuses any plan- or browser-authored value --
    so its presence *is* the capability. Deriving the source here is what makes
    the AC6 non-broadening guard reachable on a real remediation attempt instead
    of only in tests.

    A launch that carries no lineage is the instruction that establishes its
    canonical session.
    """

    lineage = _canonical_turn_lineage(request)
    if lineage is None:
        return TurnSource.INITIAL
    return coerce_turn_source(lineage.source)


def canonical_turn_base_step_execution_id(
    request: AgentExecutionRequest,
) -> str | None:
    """Return the Step Execution whose durable authority bounds this turn."""

    lineage = _canonical_turn_lineage(request)
    return None if lineage is None else lineage.base_step_execution_id


async def deliver_canonical_turn(
    turn_commands: Any,
    *,
    request: AgentExecutionRequest,
    plan: Any,
    command_type: str,
    operation: Callable[[], Awaitable[AgentRunResult]],
) -> AgentRunResult:
    """Run ``operation`` inside one claimed, fenced canonical turn command.

    The turn source is derived from the request by
    :func:`canonical_turn_source`; no realizer may name its own source, so a
    remediation attempt cannot be journaled as an initial turn.

    ``turn_commands`` may be ``None`` in unit harnesses that do not wire the
    control plane; the operation then runs unwrapped. A rejected admission
    (changed immutable authority, terminal session, completed cleanup) is
    surfaced as a typed harness-platform failure *before* the lifecycle runs, so
    the prior session is never silently mutated. A remediation turn that would
    broaden bounded authority raises before the lifecycle runs as well.
    """

    if turn_commands is None:
        return await operation()

    turn_source = canonical_turn_source(request)
    workflow_id, step_execution_id = execution_identity(request)
    try:
        command_claim = await turn_commands.claim(
            workflow_id=workflow_id,
            provider_session_ref="",
            chat_binding_id=None,
            command_type=command_type,
            turn_source=turn_source,
            idempotency_key=request.idempotency_key,
            payload_digest=plan.planRef,
            step_execution_id=step_execution_id,
            base_step_execution_id=canonical_turn_base_step_execution_id(request),
            bootstrap=CanonicalSessionBootstrap(
                provider="omnigent",
                step_execution_id=step_execution_id,
                agent_run_id=request.correlation_id,
                source_idempotency_key=request.idempotency_key,
                execution_plan_ref=plan.planRef,
            ),
            requested_authority=canonical_turn_authority(request, plan),
        )
    except CanonicalTurnAdmissionRejected as exc:
        raise HarnessPlatformError(
            "canonical turn admission returned "
            f"{exc.decision.value}; the prior Omnigent session was not mutated",
            code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
        ) from exc
    if not command_claim.owns_delivery:
        raise HarnessPlatformError(
            "canonical turn command is already settled or owned; "
            "reconciliation is required",
            code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
        )

    from moonmind.omnigent.control_plane.records import ControlPlaneOutcome

    try:
        result = await operation()
    except BaseException:
        try:
            await turn_commands.settle(
                workflow_id=workflow_id,
                idempotency_key=request.idempotency_key,
                outcome=ControlPlaneOutcome.DELIVERY_UNKNOWN,
            )
        except Exception:
            logger.exception(
                "Failed to park Omnigent turn command as delivery unknown"
            )
        raise
    try:
        await turn_commands.settle(
            workflow_id=workflow_id,
            idempotency_key=request.idempotency_key,
            outcome=ControlPlaneOutcome.APPLIED,
            provider_receipt_id=str(
                (result.metadata or {}).get("omnigentSessionId") or ""
            )
            or None,
            result_ref=str((result.metadata or {}).get("externalStateRef") or "")
            or None,
        )
    except Exception:
        logger.exception("Omnigent turn command settlement remains pending")
        result = result.model_copy(
            update={
                "metadata": {
                    **(result.metadata or {}),
                    "canonicalCommandSettlementDeferred": True,
                }
            }
        )
    return result


__all__ = [
    "canonical_turn_base_step_execution_id",
    "canonical_turn_source",
    "deliver_canonical_turn",
    "execution_identity",
]
