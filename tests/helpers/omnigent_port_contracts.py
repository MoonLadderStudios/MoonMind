"""Shared port-contract suite for Omnigent control-plane repositories.

Source issue: MoonLadderStudios/MoonMind#3711
([Omnigent control plane 10/11]).

One behavioural contract per aggregate port, invoked against every adapter that
implements the port so an in-memory test double and the production SQLAlchemy
repository (on SQLite and PostgreSQL) are proven interchangeable behind the same
interface. The assertions deliberately avoid comparing storage-assigned
timestamps and only pin observable behaviour: append idempotency, dedup scope,
bounded reads, ordering, and per-reason counting.

Callers are responsible for provisioning the referenced canonical sessions
(observations and decisions carry a foreign key to ``omnigent_sessions``); the
contract only exercises the observation/decision repository surface.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from moonmind.omnigent.control_plane.records import (
    COMMAND_STATE_APPLIED,
    COMMAND_STATE_CLAIMED,
    COMMAND_STATE_DELIVERY_UNKNOWN,
    COMMAND_STATE_PENDING,
    TURN_STATE_ACCEPTED,
    TURN_STATE_DISPATCHING,
    TURN_STATE_PREPARED,
    TURN_STATE_TERMINAL,
    CommandIdempotencyConflictError,
    ConflictingSessionAuthorityError,
    ControlPlaneOutcome,
    FencingScope,
    NotCommandOwnerError,
    RevisionConflictError,
    TerminalSessionOverwriteError,
    TurnIdempotencyConflictError,
)
from moonmind.omnigent.ports import (
    DecisionRepositoryPort,
    ObservationRepositoryPort,
    SessionRepositoryPort,
)

def _at(seconds: int) -> datetime:
    return datetime(2024, 5, 1, 12, 0, seconds, tzinfo=timezone.utc)


async def run_observation_repository_contract(
    repo: ObservationRepositoryPort,
    *,
    session_a: str,
    session_b: str,
) -> None:
    """Assert the append-only observation index contract for one adapter."""

    first = await repo.append(
        observation_id="obs-1",
        session_id=session_a,
        observation_type="provider_event",
        source="provider",
        observed_at=_at(1),
        deduplication_key="dk-1",
    )
    assert first.observation_id == "obs-1"

    # Idempotent on (session_id, deduplication_key): the original record wins and
    # the second observation_id is never stored.
    duplicate = await repo.append(
        observation_id="obs-1-dupe",
        session_id=session_a,
        observation_type="provider_event",
        source="provider",
        observed_at=_at(9),
        deduplication_key="dk-1",
    )
    assert duplicate.observation_id == "obs-1"

    await repo.append(
        observation_id="obs-2",
        session_id=session_a,
        observation_type="snapshot",
        source="reconciler",
        observed_at=_at(2),
        deduplication_key="dk-2",
    )

    # Same deduplication_key under a different session is a distinct observation.
    await repo.append(
        observation_id="obs-3",
        session_id=session_b,
        observation_type="provider_event",
        source="provider",
        observed_at=_at(3),
        deduplication_key="dk-1",
    )

    all_a = await repo.list_for_session(session_a)
    assert [o.observation_id for o in all_a] == ["obs-1", "obs-2"]

    snapshots = await repo.list_for_session(session_a, observation_type="snapshot")
    assert [o.observation_id for o in snapshots] == ["obs-2"]

    bounded = await repo.list_for_session(session_a, limit=1)
    assert [o.observation_id for o in bounded] == ["obs-1"]

    latest = await repo.latest_for_session(session_a)
    assert latest is not None and latest.observation_id == "obs-2"

    latest_typed = await repo.latest_for_session(
        session_a, observation_types=["provider_event"]
    )
    assert latest_typed is not None and latest_typed.observation_id == "obs-1"

    assert await repo.latest_for_session("no-such-session") is None

    all_b = await repo.list_for_session(session_b)
    assert [o.observation_id for o in all_b] == ["obs-3"]


async def run_decision_repository_contract(
    repo: DecisionRepositoryPort,
    *,
    session_a: str,
    session_b: str,
) -> None:
    """Assert the append-only reconciliation-decision journal contract."""

    await repo.append(
        decision_id="dec-1",
        session_id=session_a,
        decision_code="advance",
        reason_code="ok",
    )
    await repo.append(
        decision_id="dec-2",
        session_id=session_a,
        decision_code="hold",
        reason_code="ambiguous",
    )
    await repo.append(
        decision_id="dec-3",
        session_id=session_a,
        decision_code="hold",
        reason_code="ambiguous",
    )
    await repo.append(
        decision_id="dec-4",
        session_id=session_b,
        decision_code="advance",
        reason_code="ok",
    )

    journal = await repo.list_for_session(session_a)
    assert [d.decision_id for d in journal] == ["dec-1", "dec-2", "dec-3"]

    latest = await repo.latest_for_session(session_a)
    assert latest is not None and latest.decision_id == "dec-3"

    assert await repo.latest_for_session("no-such-session") is None

    # The durable decision journal is the per-session/per-reason detection count.
    assert await repo.count_for_session_reason(session_a, "ambiguous") == 2
    assert await repo.count_for_session_reason(session_a, "ok") == 1
    assert await repo.count_for_session_reason(session_b, "ok") == 1
    assert await repo.count_for_session_reason(session_a, "never") == 0

    journal_b = await repo.list_for_session(session_b)
    assert [d.decision_id for d in journal_b] == ["dec-4"]


async def run_session_repository_contract(
    sessions: SessionRepositoryPort,
    *,
    session_a: str,
    session_b: str,
) -> None:
    """Assert the canonical session revision/fencing/terminal contract.

    Pins the outcomes the issue calls out as needing to be identical across the
    in-memory and PostgreSQL adapters: fail-closed scope uniqueness,
    fencing-before-revision conflict ordering, monotonic revision on every
    applied write, benign lost-update/fenced :class:`CasResult` convergence
    signals, and immutable terminal authority (only cleanup/archive fields may
    advance post-terminal).
    """

    created = await sessions.create(
        session_id=session_a,
        moonmind_workflow_id="wf-a",
        provider="codex",
        provider_session_ref="ps-a",
        chat_binding_id="chat-a",
    )
    assert created.revision == 1
    assert created.fencing_generation == 0
    assert created.terminal_state is None
    assert created.cleanup_state == "pending"

    assert (await sessions.get(session_a)).session_id == session_a
    by_scope = await sessions.get_by_scope("wf-a", "ps-a")
    assert by_scope is not None and by_scope.session_id == session_a
    assert await sessions.get_by_scope("wf-a", "ps-missing") is None
    by_chat = await sessions.get_by_chat_binding("chat-a")
    assert by_chat is not None and by_chat.session_id == session_a
    assert await sessions.get_by_chat_binding("chat-missing") is None

    # A NULL scope lookup is ambiguous and fails closed.
    with pytest.raises(ConflictingSessionAuthorityError):
        await sessions.get_by_scope("wf-a", None)

    # Conflicting immutable authority fails closed rather than selecting a row.
    with pytest.raises(ConflictingSessionAuthorityError):
        await sessions.create(
            session_id=session_a, moonmind_workflow_id="wf-a", provider="codex"
        )
    with pytest.raises(ConflictingSessionAuthorityError):
        await sessions.create(
            session_id=session_b,
            moonmind_workflow_id="wf-a",
            provider="codex",
            provider_session_ref="ps-a",
        )
    with pytest.raises(ConflictingSessionAuthorityError):
        await sessions.create(
            session_id=session_b,
            moonmind_workflow_id="wf-b",
            provider="codex",
            chat_binding_id="chat-a",
        )

    # A distinct scope is admitted and isolated.
    other = await sessions.create(
        session_id=session_b,
        moonmind_workflow_id="wf-b",
        provider="codex",
        provider_session_ref="ps-b",
    )
    assert other.revision == 1

    # An applied lifecycle write advances the revision.
    applied = await sessions.compare_and_swap_session(
        session_a,
        expected_revision=1,
        expected_fencing_generation=0,
        desired_state="running",
    )
    assert applied.outcome is ControlPlaneOutcome.APPLIED
    assert applied.record.revision == 2
    assert applied.record.desired_state == "running"

    # A stale revision is a benign convergence signal; the record is unchanged.
    stale = await sessions.compare_and_swap_session(
        session_a,
        expected_revision=1,
        expected_fencing_generation=0,
        desired_state="hijack",
    )
    assert stale.outcome is ControlPlaneOutcome.REVISION_CONFLICT
    assert stale.record.revision == 2
    assert stale.record.desired_state == "running"

    # A superseded fencing generation is refused, and is checked before revision.
    fenced = await sessions.compare_and_swap_session(
        session_a,
        expected_revision=2,
        expected_fencing_generation=9,
        desired_state="hijack",
    )
    assert fenced.outcome is ControlPlaneOutcome.FENCING_CONFLICT
    assert fenced.record.desired_state == "running"

    # update_lifecycle raises on a lost update instead of returning an outcome.
    with pytest.raises(RevisionConflictError):
        await sessions.update_lifecycle(
            session_a,
            expected_revision=1,
            expected_fencing_generation=0,
            observed_state="stale",
        )

    # Acquire a strictly newer supervisor generation; a racing acquire on the
    # now-stale revision loses, and CLEANUP is owned by another aggregate.
    supervised = await sessions.acquire_fencing_generation(
        session_a, FencingScope.SESSION_SUPERVISOR, expected_revision=2
    )
    assert supervised.fencing_generation == 1
    assert supervised.revision == 3
    with pytest.raises(RevisionConflictError):
        await sessions.acquire_fencing_generation(
            session_a, FencingScope.SESSION_SUPERVISOR, expected_revision=2
        )
    with pytest.raises(ValueError):
        await sessions.acquire_fencing_generation(
            session_a, FencingScope.CLEANUP, expected_revision=3
        )

    # The former owner (generation 0) is now fenced out.
    former = await sessions.compare_and_swap_session(
        session_a,
        expected_revision=3,
        expected_fencing_generation=0,
        desired_state="hijack",
    )
    assert former.outcome is ControlPlaneOutcome.FENCING_CONFLICT
    assert former.record.desired_state == "running"

    # Advance the observation frontier under the current fence; a stale-epoch
    # write is fenced and does not regress the durable frontier.
    frontier = await sessions.advance_observation_frontier(
        session_a,
        expected_revision=3,
        expected_fencing_generation=1,
        provider_event_cursor="cursor-1",
    )
    assert frontier.outcome is ControlPlaneOutcome.APPLIED
    assert frontier.record.provider_event_cursor == "cursor-1"
    assert frontier.record.revision == 4
    stale_frontier = await sessions.advance_observation_frontier(
        session_a,
        expected_revision=3,
        expected_fencing_generation=0,
        provider_event_cursor="cursor-2",
    )
    assert stale_frontier.outcome is ControlPlaneOutcome.FENCING_CONFLICT
    assert stale_frontier.record.provider_event_cursor == "cursor-1"

    # Terminalize under current authority; the same terminal is idempotent even
    # under stale authority, and a contradictory terminal fails closed.
    terminal = await sessions.mark_terminal(
        session_a,
        "succeeded",
        expected_revision=4,
        expected_fencing_generation=1,
    )
    assert terminal.terminal_state == "succeeded"
    assert terminal.revision == 5
    again = await sessions.mark_terminal(
        session_a,
        "succeeded",
        expected_revision=999,
        expected_fencing_generation=999,
    )
    assert again.terminal_state == "succeeded"
    assert again.revision == 5
    with pytest.raises(TerminalSessionOverwriteError):
        await sessions.mark_terminal(
            session_a,
            "failed",
            expected_revision=5,
            expected_fencing_generation=1,
        )

    # Post-terminal: cleanup/archive fields still advance; nonterminal lifecycle
    # state fails closed.
    post = await sessions.compare_and_swap_session(
        session_a,
        expected_revision=5,
        expected_fencing_generation=1,
        cleanup_state="complete",
    )
    assert post.outcome is ControlPlaneOutcome.APPLIED
    assert post.record.cleanup_state == "complete"
    with pytest.raises(TerminalSessionOverwriteError):
        await sessions.compare_and_swap_session(
            session_a,
            expected_revision=6,
            expected_fencing_generation=1,
            desired_state="resurrect",
        )

    # session_b stayed isolated throughout.
    assert (await sessions.get(session_b)).revision == 1


async def run_turn_repository_contract(repos, *, session_id: str) -> None:
    """Assert the turn-attempt idempotency and fenced state-machine contract.

    ``repos`` exposes cooperating ``sessions`` and ``turns`` adapters (the
    production ``OmnigentControlPlaneStore`` transaction or the in-memory store)
    because a turn write is guarded by the *owning session's* live
    session-supervisor generation, not the turn's stored value.
    """

    sessions, turns = repos.sessions, repos.turn_attempts
    await sessions.create(
        session_id=session_id,
        moonmind_workflow_id=f"wf-{session_id}",
        provider="codex",
        provider_session_ref=f"ps-{session_id}",
    )

    first = await turns.create(
        turn_attempt_id="t1",
        session_id=session_id,
        idempotency_key="idem-1",
        instruction_digest="digest-1",
    )
    assert first.state == TURN_STATE_PREPARED
    assert first.revision == 1
    assert first.fencing_generation == 0

    # Idempotent create returns the same attempt; a reused key for a different
    # logical turn fails closed.
    dup = await turns.create(
        turn_attempt_id="t1-dupe",
        session_id=session_id,
        idempotency_key="idem-1",
        instruction_digest="digest-1",
    )
    assert dup.turn_attempt_id == "t1"
    with pytest.raises(TurnIdempotencyConflictError):
        await turns.create(
            turn_attempt_id="t1-conflict",
            session_id=session_id,
            idempotency_key="idem-1",
            instruction_digest="digest-different",
        )

    assert (await turns.get("t1")).turn_attempt_id == "t1"
    assert (await turns.get_by_idempotency_key("idem-1")).turn_attempt_id == "t1"
    assert await turns.get("t1-missing") is None
    assert await turns.count_for_session(session_id) == 1

    # Advance forward under the session's supervisor generation (0).
    advanced = await turns.advance_state(
        "t1",
        TURN_STATE_DISPATCHING,
        expected_revision=1,
        expected_fencing_generation=0,
    )
    assert advanced.state == TURN_STATE_DISPATCHING
    assert advanced.revision == 2

    # A stale revision is a benign convergence signal.
    conflict = await turns.compare_and_swap_turn(
        "t1",
        expected_revision=1,
        expected_fencing_generation=0,
        state=TURN_STATE_ACCEPTED,
    )
    assert conflict.outcome is ControlPlaneOutcome.REVISION_CONFLICT
    assert conflict.record.state == TURN_STATE_DISPATCHING

    # A regressive state is an idempotent no-op (monotonic delivery order).
    regress = await turns.compare_and_swap_turn(
        "t1",
        expected_revision=2,
        expected_fencing_generation=0,
        state=TURN_STATE_PREPARED,
    )
    assert regress.outcome is ControlPlaneOutcome.ALREADY_APPLIED
    assert regress.record.state == TURN_STATE_DISPATCHING
    assert regress.record.revision == 2

    # Turn writes are guarded by the session's *live* supervisor generation.
    # After the session acquires a newer generation, the old fence is refused and
    # the new one is accepted, stamping the guarding authority onto the attempt.
    supervised = await sessions.acquire_fencing_generation(
        session_id, FencingScope.SESSION_SUPERVISOR, expected_revision=1
    )
    assert supervised.fencing_generation == 1
    fenced = await turns.compare_and_swap_turn(
        "t1",
        expected_revision=2,
        expected_fencing_generation=0,
        state=TURN_STATE_ACCEPTED,
    )
    assert fenced.outcome is ControlPlaneOutcome.FENCING_CONFLICT
    accepted = await turns.advance_state(
        "t1",
        TURN_STATE_ACCEPTED,
        expected_revision=2,
        expected_fencing_generation=1,
    )
    assert accepted.state == TURN_STATE_ACCEPTED
    assert accepted.fencing_generation == 1
    assert accepted.revision == 3

    # Terminalize; the same terminal is idempotent, a contradictory one fails.
    terminal = await turns.mark_terminal(
        "t1",
        "succeeded",
        expected_revision=3,
        expected_fencing_generation=1,
        attempt_outcome="ok",
    )
    assert terminal.terminal_state == "succeeded"
    assert terminal.state == TURN_STATE_TERMINAL
    assert terminal.attempt_outcome == "ok"
    assert terminal.revision == 4
    idem = await turns.compare_and_swap_turn(
        "t1",
        expected_revision=999,
        expected_fencing_generation=999,
        terminal_state="succeeded",
    )
    assert idem.outcome is ControlPlaneOutcome.ALREADY_APPLIED
    with pytest.raises(TurnIdempotencyConflictError):
        await turns.compare_and_swap_turn(
            "t1",
            expected_revision=4,
            expected_fencing_generation=1,
            terminal_state="failed",
        )

    # A second attempt is listed in creation order.
    await turns.create(
        turn_attempt_id="t2", session_id=session_id, idempotency_key="idem-2"
    )
    listed = await turns.list_for_session(session_id)
    assert [t.turn_attempt_id for t in listed] == ["t1", "t2"]
    assert await turns.count_for_session(session_id) == 2


async def run_command_repository_contract(repos, *, session_id: str) -> None:
    """Assert the durable command idempotency/claim/delivery contract.

    ``repos`` exposes cooperating ``sessions`` and ``commands`` adapters because
    a stale command (authored under a superseded supervisor generation) is fenced
    at claim against the owning session's live generation.
    """

    sessions, commands = repos.sessions, repos.commands
    await sessions.create(
        session_id=session_id,
        moonmind_workflow_id=f"wf-{session_id}",
        provider="codex",
        provider_session_ref=f"ps-{session_id}",
    )

    first = await commands.record(
        command_id="c1",
        session_id=session_id,
        command_type="send_message",
        idempotency_key="ik-1",
        payload_digest="pd-1",
    )
    assert first.status == COMMAND_STATE_PENDING
    assert first.revision == 1

    # Idempotent record with the same identity returns the same command; reusing
    # the key with a different identity fails closed.
    dup = await commands.record(
        command_id="c1-dupe",
        session_id=session_id,
        command_type="send_message",
        idempotency_key="ik-1",
        payload_digest="pd-1",
    )
    assert dup.command_id == "c1"
    with pytest.raises(CommandIdempotencyConflictError):
        await commands.record(
            command_id="c1-conflict",
            session_id=session_id,
            command_type="send_message",
            idempotency_key="ik-1",
            payload_digest="pd-different",
        )

    assert (await commands.get("c1")).command_id == "c1"
    assert (await commands.get_by_idempotency_key("ik-1")).command_id == "c1"

    # Exclusive claim: the winner claims; the same durable claimant resumes; a
    # racing loser sharing an owner_class is refused.
    claim = await commands.claim_command(
        "c1", owner_class="worker", claim_token="tok-winner"
    )
    assert claim.outcome is ControlPlaneOutcome.APPLIED
    assert claim.record.status == COMMAND_STATE_CLAIMED
    resume = await commands.claim_command(
        "c1", owner_class="worker", claim_token="tok-winner"
    )
    assert resume.outcome is ControlPlaneOutcome.APPLIED
    loser = await commands.claim_command(
        "c1", owner_class="worker", claim_token="tok-loser"
    )
    assert loser.outcome is ControlPlaneOutcome.NOT_OWNER

    # Only the winning claimant may record delivery.
    with pytest.raises(NotCommandOwnerError):
        await commands.record_command_delivery(
            "c1",
            owner_class="worker",
            claim_token="tok-loser",
            outcome=ControlPlaneOutcome.APPLIED,
        )

    # active_for_session surfaces the single claimed command.
    active = await commands.active_for_session(session_id)
    assert active is not None and active.command_id == "c1"

    delivered = await commands.record_command_delivery(
        "c1",
        owner_class="worker",
        claim_token="tok-winner",
        outcome=ControlPlaneOutcome.APPLIED,
        provider_receipt_id="receipt-1",
    )
    assert delivered.outcome is ControlPlaneOutcome.APPLIED
    assert delivered.record.status == COMMAND_STATE_APPLIED
    assert delivered.record.provider_receipt_id == "receipt-1"

    # Re-delivering an agreeing outcome is idempotent; a contradictory outcome on
    # a settled terminal is refused and never reported as success.
    replay = await commands.record_command_delivery(
        "c1",
        owner_class="worker",
        claim_token="tok-winner",
        outcome=ControlPlaneOutcome.APPLIED,
    )
    assert replay.outcome is ControlPlaneOutcome.ALREADY_APPLIED
    contradiction = await commands.record_command_delivery(
        "c1",
        owner_class="worker",
        claim_token="tok-winner",
        outcome=ControlPlaneOutcome.REVISION_CONFLICT,
    )
    assert contradiction.outcome is ControlPlaneOutcome.IMMUTABLE_AUTHORITY_CONFLICT

    # A settled command is not re-executed and is no longer active.
    settled_claim = await commands.claim_command(
        "c1", owner_class="worker", claim_token="tok-winner"
    )
    assert settled_claim.outcome is ControlPlaneOutcome.ALREADY_APPLIED
    assert await commands.active_for_session(session_id) is None

    # A command authored under a superseded supervisor generation is fenced at
    # claim once the session advances its generation.
    supervised = await sessions.acquire_fencing_generation(
        session_id, FencingScope.SESSION_SUPERVISOR, expected_revision=1
    )
    assert supervised.fencing_generation == 1
    await commands.record(
        command_id="c2",
        session_id=session_id,
        command_type="send_message",
        idempotency_key="ik-2",
        payload_digest="pd-2",
        fencing_generation=0,
    )
    fenced = await commands.claim_command(
        "c2", owner_class="worker", claim_token="tok-2"
    )
    assert fenced.outcome is ControlPlaneOutcome.FENCING_CONFLICT

    # A delivery-unknown command is parked as ambiguous and outranks a claimed
    # one in the active-command precedence.
    await commands.record(
        command_id="c3",
        session_id=session_id,
        command_type="send_message",
        idempotency_key="ik-3",
        payload_digest="pd-3",
        fencing_generation=1,
    )
    await commands.claim_command("c3", owner_class="worker", claim_token="tok-3")
    parked = await commands.record_command_delivery(
        "c3",
        owner_class="worker",
        claim_token="tok-3",
        outcome=ControlPlaneOutcome.DELIVERY_UNKNOWN,
    )
    assert parked.outcome is ControlPlaneOutcome.DELIVERY_UNKNOWN
    assert parked.record.status == COMMAND_STATE_DELIVERY_UNKNOWN
    assert parked.record.delivery_ambiguous is True
    active_after = await commands.active_for_session(session_id)
    assert active_after is not None and active_after.command_id == "c3"

    listed = await commands.list_for_session(session_id)
    assert [c.command_id for c in listed] == ["c1", "c2", "c3"]


__all__ = [
    "run_command_repository_contract",
    "run_decision_repository_contract",
    "run_observation_repository_contract",
    "run_session_repository_contract",
    "run_turn_repository_contract",
]
