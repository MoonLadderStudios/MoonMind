"""Session reconciliation use case.

Source issue: MoonLadderStudios/MoonMind#3711
([Omnigent control plane 10/11]).

``ReconcileSessionUseCase`` is the application-layer coordinator that turns one
pure reconciliation decision into durable control-plane writes. It depends only
on domain types (the pure :mod:`moonmind.omnigent.reconciler` reducer and its
contracts) and the narrow persistence ports -- never on SQLAlchemy, HTTP,
Docker, FastAPI, or a concrete repository. The same coordination previously had
to live inside the large infrastructure-coupled bridge modules; consolidating it
here lets the identical use case run against the in-memory reference adapters and
the production SQLAlchemy repositories behind one interface.

Responsibilities:

* evaluate the pure reducer for the given immutable intent, durable authority,
  and observations;
* record the authorized side-effect command (when the decision carries one) in
  the durable command / idempotency journal, keyed by the reducer's
  deterministic ``command_id`` so a replay never issues a second logical command
  (invariant 7);
* append the decision to the append-only decision journal, referencing the
  command it authorized.

The use case is replay-safe: a decision is content-addressed by the durable
input state and observation frontier, so re-running the same reconciliation is
idempotent and does not double-append the journal.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from moonmind.omnigent.control_plane.records import (
    CommandRecord,
    DecisionRecord,
    FencingScope,
    compute_digest,
)
from moonmind.omnigent.ports import (
    CommandRepositoryPort,
    DecisionRepositoryPort,
)
from moonmind.omnigent.reconciler import (
    CompiledSessionIntent,
    DurableSessionState,
    ObservationSet,
    ReconciliationDecision,
    reconcile,
)

# Canonical owner label for session-supervisor side effects. It matches
# ``FencingScope.SESSION_SUPERVISOR`` so the command journal records who is
# authorized to execute the logical side effect without embedding provider
# vocabulary.
_SESSION_SUPERVISOR_OWNER = FencingScope.SESSION_SUPERVISOR.value


@dataclass(frozen=True)
class ReconcileSessionResult:
    """Outcome of one :meth:`ReconcileSessionUseCase.execute` call.

    ``decision`` is the pure reducer output; ``decision_record`` is the durable
    journal entry; ``command_record`` is the recorded side-effect command when
    the decision authorized one, otherwise ``None``.
    """

    decision: ReconciliationDecision
    decision_record: DecisionRecord
    command_record: Optional[CommandRecord] = None


class ReconcileSessionUseCase:
    """Coordinate one reconciliation decision over the persistence ports.

    Depends only on the narrow ports; the concrete adapter (in-memory reference
    or production SQLAlchemy repository) is injected by the caller/composition
    layer, never selected here.
    """

    def __init__(
        self,
        *,
        decisions: DecisionRepositoryPort,
        commands: CommandRepositoryPort,
    ) -> None:
        self._decisions = decisions
        self._commands = commands

    async def execute(
        self,
        *,
        intent: CompiledSessionIntent,
        durable: DurableSessionState,
        observations: ObservationSet,
        now,
    ) -> ReconcileSessionResult:
        """Evaluate the reducer and persist its decision (and any command).

        The pure reducer owns *what* to do; this use case owns coordinating the
        durable writes over the ports. Re-running with the same durable input and
        observation frontier is idempotent.
        """

        decision = reconcile(
            intent=intent,
            durable=durable,
            observations=observations,
            now=now,
        )

        input_state_digest = compute_digest(durable.model_dump(mode="json"))
        observation_frontier_digest = compute_digest(
            observations.model_dump(mode="json")
        )
        decision_id = self._decision_identity(
            durable.session_id,
            decision,
            input_state_digest,
            observation_frontier_digest,
        )

        existing = await self._existing_decision(durable.session_id, decision_id)
        if existing is not None:
            command_record = None
            if existing.resulting_command_id is not None:
                command_record = await self._commands.get(
                    existing.resulting_command_id
                )
            return ReconcileSessionResult(
                decision=decision,
                decision_record=existing,
                command_record=command_record,
            )

        command_record: Optional[CommandRecord] = None
        if decision.command is not None:
            command_record = await self._record_command(
                durable.session_id, decision
            )

        product_visible_transition = (
            decision.reason_code.value
            if decision.changes_product_visible_state
            else None
        )
        decision_record = await self._decisions.append(
            decision_id=decision_id,
            session_id=durable.session_id,
            decision_code=decision.kind.value,
            input_state_digest=input_state_digest,
            observation_frontier_digest=observation_frontier_digest,
            expected_revision=decision.expected_revision,
            fencing_generation=decision.expected_fencing_generation,
            reason_code=decision.reason_code.value,
            resulting_command_id=(
                command_record.command_id if command_record is not None else None
            ),
            next_deadline=decision.next_deadline,
            product_visible_transition=product_visible_transition,
        )
        return ReconcileSessionResult(
            decision=decision,
            decision_record=decision_record,
            command_record=command_record,
        )

    async def _record_command(
        self, session_id: str, decision: ReconciliationDecision
    ) -> CommandRecord:
        spec = decision.command
        assert spec is not None  # guarded by the caller
        payload_digest = compute_digest(
            {
                "command_kind": spec.command_kind.value,
                "attempt_id": spec.attempt_id,
                "provider_session_id": spec.provider_session_id,
                "terminal_outcome": (
                    spec.terminal_outcome.value
                    if spec.terminal_outcome is not None
                    else None
                ),
            }
        )
        # ``command_id`` is the reducer's deterministic idempotency identity, so a
        # replay of the same decision records the same command exactly once.
        return await self._commands.record(
            command_id=spec.command_id,
            session_id=session_id,
            command_type=spec.command_kind.value,
            idempotency_key=spec.command_id,
            payload_digest=payload_digest,
            turn_attempt_id=spec.attempt_id,
            expected_session_revision=decision.expected_revision,
            fencing_generation=decision.expected_fencing_generation,
            owner_class=_SESSION_SUPERVISOR_OWNER,
        )

    async def _existing_decision(
        self, session_id: str, decision_id: str
    ) -> Optional[DecisionRecord]:
        for record in await self._decisions.list_for_session(session_id):
            if record.decision_id == decision_id:
                return record
        return None

    @staticmethod
    def _decision_identity(
        session_id: str,
        decision: ReconciliationDecision,
        input_state_digest: str,
        observation_frontier_digest: str,
    ) -> str:
        """Content-address the decision so identical inputs collapse to one id.

        A decision is uniquely identified by the session it governs, the durable
        input state it was computed against, the observation frontier it saw, and
        the decision's own kind/reason. Two genuinely different reconciliations
        (different observations) get different ids and both persist; a replay of
        the same inputs collapses to one journal entry.
        """

        digest = compute_digest(
            {
                "session_id": session_id,
                "kind": decision.kind.value,
                "reason_code": decision.reason_code.value,
                "expected_revision": decision.expected_revision,
                "expected_fencing_generation": (
                    decision.expected_fencing_generation
                ),
                "input_state_digest": input_state_digest,
                "observation_frontier_digest": observation_frontier_digest,
            }
        )
        return f"dec-{digest[:40]}"


__all__ = ["ReconcileSessionResult", "ReconcileSessionUseCase"]
