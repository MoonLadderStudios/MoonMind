"""Production dispatcher from the canonical turn boundary to the supervisor.

Source issue: MoonLadderStudios/MoonMind#3707.

:class:`SupervisorTurnDispatcher` is the one production sender of the
``submit_authorized_continuation`` signal. It runs *after* the canonical turn
attempt and its fenced ``omnigent.submit_turn`` command are durably committed, so
a lost signal is recoverable from the command journal rather than producing an
untracked provider side effect.

The dispatcher is deliberately thin. It performs no admission, no source
derivation, and no harness selection: it forwards the compact, already-admitted
payload -- request id, turn-attempt id, artifact-backed instruction ref, and
canonical turn source -- to the durable supervisor that owns the session.
"""

from __future__ import annotations

from typing import Any, Protocol

from moonmind.omnigent.control_plane.turn_service import (
    CanonicalTurnResult,
    CanonicalTurnService,
)
from moonmind.schemas.omnigent_session_models import OmnigentSessionSignal

SUBMIT_TURN_SIGNAL = "submit_authorized_continuation"


class WorkflowSignalClient(Protocol):
    """The narrow Temporal capability this dispatcher needs."""

    async def signal_workflow(
        self, workflow_id: str, signal_name: str, arg: Any = None
    ) -> None: ...


class SupervisorTurnDispatcher:
    """Forward an admitted canonical turn to its session supervisor."""

    def __init__(self, client: WorkflowSignalClient) -> None:
        self._client = client

    async def __call__(self, result: CanonicalTurnResult) -> None:
        """Signal the supervisor for one admitted turn.

        Raises when handed a refused submission: ``dispatch_payload`` fails
        closed, so a ``branch_required`` / ``new_session_required`` /
        ``resume_unavailable`` decision can never be delivered as new work.
        """

        from moonmind.workflows.temporal.workflows.omnigent_session import (
            omnigent_session_workflow_id,
        )

        payload = result.dispatch_payload()
        signal = OmnigentSessionSignal.model_validate(payload)
        await self._client.signal_workflow(
            omnigent_session_workflow_id(result.decision.session_id),
            SUBMIT_TURN_SIGNAL,
            signal.model_dump(mode="json", by_alias=True, exclude_none=True),
        )


def production_turn_service(store: Any) -> CanonicalTurnService:
    """Build the one production canonical turn service over ``store``.

    Every production producer -- Workflow Chat, the repository-publication
    continuation loop, remediation, checkpoint resume, and the Checkpoint Branch
    turn launcher -- constructs the boundary through this factory, so all of them
    share one submission authority *and* one delivery path. The service invokes
    the dispatcher only for a submission that declares
    ``supervisor_delivered=True`` on a session that records a supervisor
    generation
    (:func:`~moonmind.omnigent.control_plane.turn_service.session_supervises_turns`).
    A producer-delivered turn and a pre-canonical session are both delivered by
    their own transport owner, never signalled, so no turn is submitted twice.
    """

    from moonmind.workflows.temporal.client import TemporalClientAdapter

    return CanonicalTurnService(
        store, dispatcher=SupervisorTurnDispatcher(TemporalClientAdapter())
    )


__all__ = [
    "SUBMIT_TURN_SIGNAL",
    "SupervisorTurnDispatcher",
    "WorkflowSignalClient",
    "production_turn_service",
]
