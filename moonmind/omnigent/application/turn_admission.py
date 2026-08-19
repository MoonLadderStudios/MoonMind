"""Turn-admission use case.

Source issue: MoonLadderStudios/MoonMind#3711
([Omnigent control plane 10/11]).

``OpenTurnAttemptUseCase`` coordinates admitting a new turn attempt onto a
canonical session over the narrow session and turn ports. It depends only on
domain types (the immutable :class:`CompiledSessionIntent`) and the ports --
never on a concrete SQLAlchemy repository, FastAPI, or provider client. The same
gating previously had to be repeated inside the large bridge modules; here it is
one interface-driven use case that runs identically against the in-memory
reference adapters and the production repositories.

Policy enforced before persisting an attempt:

* the target session must have a canonical record (fail closed otherwise);
* a terminal session cannot admit a new attempt;
* the immutable intent's ``max_turn_attempts`` budget bounds how many attempts a
  session may consume;
* admission is idempotent on the request idempotency key, so a retried request
  returns the existing attempt without consuming budget.
"""

from __future__ import annotations

from typing import Optional

from moonmind.omnigent.control_plane.records import (
    TURN_STATE_PREPARED,
    TurnAttemptRecord,
)
from moonmind.omnigent.ports import (
    SessionRepositoryPort,
    TurnRepositoryPort,
)
from moonmind.omnigent.reconciler import CompiledSessionIntent

from .errors import (
    MaxTurnAttemptsExceededError,
    SessionNotFoundError,
    SessionTerminalError,
)


class OpenTurnAttemptUseCase:
    """Admit a bounded, idempotent turn attempt onto a canonical session."""

    def __init__(
        self,
        *,
        sessions: SessionRepositoryPort,
        turns: TurnRepositoryPort,
    ) -> None:
        self._sessions = sessions
        self._turns = turns

    async def open_attempt(
        self,
        *,
        intent: CompiledSessionIntent,
        turn_attempt_id: str,
        idempotency_key: str,
        lineage_kind: str = "instruction",
        step_execution_id: Optional[str] = None,
        parent_turn_attempt_id: Optional[str] = None,
        remediation_of_turn_attempt_id: Optional[str] = None,
        instruction_digest: Optional[str] = None,
        provider_marker: Optional[str] = None,
    ) -> TurnAttemptRecord:
        """Return the (possibly pre-existing) attempt for ``idempotency_key``.

        Raises :class:`SessionNotFoundError` when the session has no canonical
        record, :class:`SessionTerminalError` when it is terminal, and
        :class:`MaxTurnAttemptsExceededError` when a *new* attempt would exceed
        the intent budget.
        """

        session_id = intent.session_id
        session = await self._sessions.get(session_id)
        if session is None:
            raise SessionNotFoundError(session_id)
        if session.is_terminal:
            raise SessionTerminalError(session_id, session.terminal_state or "")

        # Idempotent replay: a retried request for the same logical turn returns
        # the existing attempt and never consumes additional budget.
        existing = await self._turns.get_by_idempotency_key(idempotency_key)
        if existing is not None:
            return existing

        attempts = await self._turns.count_for_session(session_id)
        if attempts >= intent.max_turn_attempts:
            raise MaxTurnAttemptsExceededError(
                session_id,
                attempts=attempts,
                limit=intent.max_turn_attempts,
            )

        return await self._turns.create(
            turn_attempt_id=turn_attempt_id,
            session_id=session_id,
            idempotency_key=idempotency_key,
            lineage_kind=lineage_kind,
            step_execution_id=step_execution_id,
            parent_turn_attempt_id=parent_turn_attempt_id,
            remediation_of_turn_attempt_id=remediation_of_turn_attempt_id,
            instruction_digest=instruction_digest,
            provider_marker=provider_marker,
            state=TURN_STATE_PREPARED,
        )


__all__ = ["OpenTurnAttemptUseCase"]
