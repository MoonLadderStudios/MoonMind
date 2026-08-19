"""AC7 repository/concurrency binding: fault scenarios vs. real control plane.

Source issue: MoonLadderStudios/MoonMind#3709 (acceptance criterion 7 — the
representative scenarios must cross the PostgreSQL repository/concurrency
boundary, not only the pure domain).

This binding replays the *same* fault scenarios the pure-domain suite drives
against the reducer (``moonmind.omnigent.faultlab``) onto the **real** Omnigent
control-plane repositories (``moonmind.omnigent.control_plane`` over
``api_service.db.models``). It is a thin adapter: the fault framework produces a
boundary-neutral :class:`~moonmind.omnigent.faultlab.ProjectedRun` (the durable
logical command stream plus the independent provider ledger), and this module
maps each projected command onto ``record`` / ``claim_command`` /
``record_command_delivery`` / ``update_lifecycle`` / ``mark_terminal`` /
``claim_cleanup`` / ``complete_cleanup`` so the persistence layer re-proves, at
its own boundary:

* **durable revisions** advance monotonically and never regress;
* **command-claim uniqueness** — a logical command is claimed and executed at
  most once; a racing claimant is refused rather than granted a false success;
* **idempotency-key conflict** — reusing a command key for a different logical
  payload fails closed;
* **fencing generation** — a command authored under a superseded session
  supervisor generation is fenced out of execution;
* **distinct terminality** — the attempt terminal never conflates with the
  canonical session terminal;
* **historical-read safety** — the terminal outcome and evidence ref survive
  cleanup completion;
* **at-most-once** — cross-checked against the independent ledger *and* the
  durable command journal, not against MoonMind's own reconciled state.

The SQLite journey is hermetic and required-CI (``integration_ci`` +
``reliability_journey``): the repositories' revision/fencing/idempotency guards
are enforced in application code and unique constraints hold on SQLite, so the
correctness of the adapter and of invariants 1/3/4/6/8/9 is proven without a
server. The decisive *concurrency* races (two claimants, two fenced writers) need
real row locks and run on the ephemeral PostgreSQL cluster from ``conftest.py``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import Base
from moonmind.omnigent.control_plane import (
    COMMAND_STATE_APPLIED,
    TURN_STATE_ACCEPTED,
    TURN_STATE_DISPATCHING,
    CommandIdempotencyConflictError,
    ControlPlaneOutcome,
    FencingScope,
    OmnigentControlPlaneStore,
    TerminalSessionOverwriteError,
)
from moonmind.omnigent.faultlab import (
    FaultPlan,
    ProjectedRun,
    generate_plan,
    project_run,
    run_plan,
)
from moonmind.omnigent.faultlab.corpus import INITIAL_CORPUS
from moonmind.omnigent.faultlab.scenario import CommandWindow, LogicalOperation

pytestmark = [pytest.mark.integration]

#: A small fixed seed corpus. Bounded so the boundary journey stays predictable.
FIXED_SEED_CORPUS = range(24)

#: Maps a projected terminal outcome onto the durable session terminal state.
_TERMINAL_STATE = {
    "success": "success",
    "failure": "failure",
    "cancelled": "cancelled",
}


@dataclass
class ControlPlaneReplayResult:
    """Durable facts observed while replaying one projected run at the boundary."""

    session_id: str
    initial_revision: int
    revisions: list[int] = field(default_factory=list)
    applied_command_ids: list[str] = field(default_factory=list)
    first_claim: dict[str, ControlPlaneOutcome] = field(default_factory=dict)
    reclaim_same_token: dict[str, ControlPlaneOutcome] = field(default_factory=dict)
    reclaim_other_token: dict[str, ControlPlaneOutcome] = field(default_factory=dict)
    applied_journal_count: dict[str, int] = field(default_factory=dict)
    submit_command_ids: list[str] = field(default_factory=list)
    session_terminal_state: str | None = None
    session_terminal_evidence_ref: str | None = None
    session_cleanup_state: str | None = None
    turn_terminal_state: str | None = None


async def replay_projected_run(
    store: OmnigentControlPlaneStore, run: ProjectedRun, *, namespace: str
) -> ControlPlaneReplayResult:
    """Replay one boundary-neutral projected run against the real repositories.

    Every session/workflow identity is namespaced so many scenarios can replay
    into one database without colliding on the canonical-authority unique scope.
    """

    session_id = f"sess-{namespace}"
    workflow_id = f"wf-{namespace}"
    turn_id = f"turn-{namespace}"

    established, _turn = await store.establish_session(
        session_id=session_id,
        moonmind_workflow_id=workflow_id,
        provider="omnigent",
        chat_binding_id=f"cb-{namespace}",
        first_turn_attempt_id=turn_id,
        first_turn_idempotency_key=f"idem-{namespace}",
        provider_session_ref=f"psess-{namespace}",
        instruction_digest="sha256:instruction",
    )
    result = ControlPlaneReplayResult(
        session_id=session_id, initial_revision=established.revision
    )
    current_revision = established.revision

    async def _record_claim_deliver(command_id: str, command_type: str, digest: str) -> None:
        """Record a logical command and prove single-claim execution authority.

        The reducer's ``command_id`` is unique only within its session, so the
        durable idempotency key is namespaced to keep many scenarios independent
        in one database; ``result`` is still keyed by the logical id.
        """

        durable_id = f"{namespace}:{command_id}"
        async with store.transaction() as repos:
            session = await repos.sessions.get(session_id)
            await repos.commands.record(
                command_id=durable_id,
                session_id=session_id,
                command_type=command_type,
                idempotency_key=durable_id,
                payload_digest=digest,
                fencing_generation=session.fencing_generation,
            )
        # First claim wins execution authority.
        async with store.transaction() as repos:
            first = await repos.commands.claim_command(
                durable_id, owner_class="supervisor", claim_token=f"{durable_id}:worker-a"
            )
        result.first_claim[command_id] = first.outcome
        # The winning claimant's own re-claim is an idempotent resume; a racing
        # claimant with a different token never wins the same authority.
        async with store.transaction() as repos:
            same = await repos.commands.claim_command(
                durable_id, owner_class="supervisor", claim_token=f"{durable_id}:worker-a"
            )
            other = await repos.commands.claim_command(
                durable_id, owner_class="supervisor", claim_token=f"{durable_id}:worker-b"
            )
        result.reclaim_same_token[command_id] = same.outcome
        result.reclaim_other_token[command_id] = other.outcome
        async with store.transaction() as repos:
            await repos.commands.record_command_delivery(
                durable_id,
                owner_class="supervisor",
                claim_token=f"{durable_id}:worker-a",
                outcome=ControlPlaneOutcome.APPLIED,
                provider_receipt_id=f"receipt-{durable_id}",
            )
            stored = await repos.commands.get(durable_id)
        if stored is not None and stored.status == COMMAND_STATE_APPLIED:
            result.applied_command_ids.append(command_id)
        result.applied_journal_count[command_id] = 1 if stored and stored.is_terminal else 0

    for command in run.commands:
        await _record_claim_deliver(
            command.command_id, command.command_type, command.payload_digest
        )

        if command.is_submit:
            result.submit_command_ids.append(command.command_id)
            # Advance the attempt through its delivery lifecycle (attempt-owned,
            # never session-owned): dispatching -> accepted.
            async with store.transaction() as repos:
                session = await repos.sessions.get(session_id)
                turn = await repos.turn_attempts.get(turn_id)
                turn = await repos.turn_attempts.advance_state(
                    turn_id,
                    TURN_STATE_DISPATCHING,
                    expected_revision=turn.revision,
                    expected_fencing_generation=session.fencing_generation,
                )
                await repos.turn_attempts.advance_state(
                    turn_id,
                    TURN_STATE_ACCEPTED,
                    expected_revision=turn.revision,
                    expected_fencing_generation=session.fencing_generation,
                )

        if command.is_terminal and result.session_terminal_state is None:
            terminal_state = _TERMINAL_STATE[
                (command.terminal_outcome or run.terminal_outcome).value
            ]
            evidence_ref = f"evref-{namespace}"
            async with store.transaction() as repos:
                session = await repos.sessions.get(session_id)
                turn = await repos.turn_attempts.get(turn_id)
                # The attempt terminal and the canonical session terminal are
                # distinct writes (invariant 6): recording one never records the
                # other.
                await repos.turn_attempts.mark_terminal(
                    turn_id,
                    "terminal",
                    expected_revision=turn.revision,
                    expected_fencing_generation=session.fencing_generation,
                    attempt_outcome=terminal_state,
                )
                marked = await repos.sessions.mark_terminal(
                    session_id,
                    terminal_state,
                    expected_revision=session.revision,
                    expected_fencing_generation=session.fencing_generation,
                    terminal_evidence_ref=evidence_ref,
                )
            result.session_terminal_state = marked.terminal_state
            result.session_terminal_evidence_ref = marked.terminal_evidence_ref
        elif result.session_terminal_state is None:
            # A pre-terminal lifecycle write advances the durable revision so the
            # boundary observes monotonic authority progression.
            async with store.transaction() as repos:
                session = await repos.sessions.get(session_id)
                updated = await repos.sessions.update_lifecycle(
                    session_id,
                    expected_revision=session.revision,
                    expected_fencing_generation=session.fencing_generation,
                    observed_state=command.command_type,
                    last_decision_ref=command.command_id,
                )
            result.revisions.append(updated.revision)
            current_revision = updated.revision

        if command.command_type == "begin_cleanup" and run.cleanup_complete:
            async with store.transaction() as repos:
                claim = await repos.cleanup.claim_cleanup(
                    session_id, owner_class="janitor", claim_token=f"janitor-{namespace}"
                )
                await repos.cleanup.complete_cleanup(
                    session_id,
                    generation=claim.record.generation,
                    owner_class="janitor",
                    claim_token=f"janitor-{namespace}",
                    session_repository=repos.sessions,
                )
                # Cleanup completion is recordable after terminal (post-terminal
                # mutable field) and must not erase the recorded terminal.
                session = await repos.sessions.get(session_id)
                await repos.sessions.update_lifecycle(
                    session_id,
                    expected_revision=session.revision,
                    expected_fencing_generation=session.fencing_generation,
                    cleanup_state="complete",
                )

    async with store.transaction() as repos:
        final = await repos.sessions.get(session_id)
        final_turn = await repos.turn_attempts.get(turn_id)
    result.session_terminal_state = final.terminal_state
    result.session_terminal_evidence_ref = final.terminal_evidence_ref
    result.session_cleanup_state = final.cleanup_state
    result.turn_terminal_state = final_turn.terminal_state
    result.revisions.insert(0, result.initial_revision)
    result.revisions.append(final.revision)
    _ = current_revision
    return result


def _assert_binding_invariants(run: ProjectedRun, result: ControlPlaneReplayResult) -> None:
    """Assert the reliability invariants at the durable persistence boundary."""

    # Invariant 3 — monotonic authority: durable revisions never regress and the
    # run made forward progress.
    for prev, nxt in zip(result.revisions, result.revisions[1:]):
        assert nxt >= prev, f"{result.session_id}: revision regressed {prev} -> {nxt}"
    assert result.revisions[-1] > result.initial_revision

    # Command-claim uniqueness / at-most-once execution authority: every command
    # is claimed once (APPLIED), the same claimant may idempotently resume, and a
    # racing claimant with a distinct token is refused rather than granted a
    # second execution.
    for command in run.commands:
        cid = command.command_id
        assert result.first_claim[cid] is ControlPlaneOutcome.APPLIED, cid
        assert result.reclaim_same_token[cid] is ControlPlaneOutcome.APPLIED, cid
        assert result.reclaim_other_token[cid] is ControlPlaneOutcome.NOT_OWNER, cid
        # Invariant 1/5 — the durable command journal performed at most one
        # applied side effect for the key.
        assert result.applied_journal_count[cid] == 1, cid

    # Invariant 1 cross-checked against the independent provider ledger.
    assert run.ledger_multiple_side_effect_keys == ()

    if run.converged:
        # Invariant 6 — distinct terminality: the session terminal and the
        # attempt terminal are separate durable facts and are not conflated.
        assert result.session_terminal_state == _TERMINAL_STATE[run.terminal_outcome.value]
        assert result.turn_terminal_state == "terminal"
        assert result.session_terminal_state != result.turn_terminal_state

    if run.cleanup_complete:
        # Invariant 9 — historical-read safety: the terminal outcome and evidence
        # ref remain readable after cleanup completes.
        assert result.session_cleanup_state == "complete"
        assert result.session_terminal_state is not None
        assert result.session_terminal_evidence_ref is not None


# --- Hermetic SQLite journey (required CI) -----------------------------------


@pytest_asyncio.fixture()
async def sqlite_store(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/faultlab_control_plane.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield OmnigentControlPlaneStore(maker)
    finally:
        await engine.dispose()


def _corpus_runs() -> list[tuple[str, ProjectedRun]]:
    runs: list[tuple[str, ProjectedRun]] = []
    for entry in INITIAL_CORPUS:
        trace = run_plan(entry.plan)
        runs.append((entry.scenario_id, project_run(trace, scenario_id=entry.scenario_id)))
    for seed in FIXED_SEED_CORPUS:
        trace = run_plan(generate_plan(seed))
        runs.append((f"seed-{seed}", project_run(trace, scenario_id=f"seed-{seed}")))
    return runs


@pytest.mark.integration_ci
@pytest.mark.reliability_journey
@pytest.mark.asyncio
async def test_sqlite_corpus_replays_hold_boundary_invariants(sqlite_store) -> None:
    for index, (scenario_id, run) in enumerate(_corpus_runs()):
        result = await replay_projected_run(
            sqlite_store, run, namespace=f"{index:04d}-{scenario_id}"[:60]
        )
        _assert_binding_invariants(run, result)


@pytest.mark.integration_ci
@pytest.mark.reliability_journey
@pytest.mark.asyncio
async def test_sqlite_pre_receipt_crash_resumes_at_most_once(sqlite_store) -> None:
    """A command that crashed after its side effect but before its receipt resumes
    under the same authority and applies exactly once.

    The projection preserves the crash window (invariant: fault attempts are not
    discarded), so the boundary can replay this exact authority handoff. A
    projection that carried only the final successful transition could not tell
    this apart from a clean delivery, leaving at-most-once-under-crash untested.
    """

    plan = FaultPlan(
        seed=7,
        recovery_round=2,
        command_crashes={
            LogicalOperation.SUBMIT_TURN: (
                CommandWindow.AFTER_SIDE_EFFECT_BEFORE_RECEIPT
            )
        },
    )
    run = project_run(run_plan(plan), scenario_id="pre-receipt-crash")
    submit = run.submit_commands[0]
    assert submit.faulted, "the crashed plan must project a faulted command"
    assert (
        CommandWindow.AFTER_SIDE_EFFECT_BEFORE_RECEIPT.value in submit.crash_windows
    )

    namespace = "pre-receipt-crash"
    session_id = f"sess-{namespace}"
    turn_id = f"turn-{namespace}"
    await sqlite_store.establish_session(
        session_id=session_id,
        moonmind_workflow_id=f"wf-{namespace}",
        provider="omnigent",
        chat_binding_id=f"cb-{namespace}",
        first_turn_attempt_id=turn_id,
        first_turn_idempotency_key=f"idem-{namespace}",
        provider_session_ref=f"psess-{namespace}",
        instruction_digest="sha256:instruction",
    )

    durable_id = f"{namespace}:{submit.command_id}"
    async with sqlite_store.transaction() as repos:
        session = await repos.sessions.get(session_id)
        await repos.commands.record(
            command_id=durable_id,
            session_id=session_id,
            command_type=submit.command_type,
            idempotency_key=durable_id,
            payload_digest=submit.payload_digest,
            fencing_generation=session.fencing_generation,
        )

    # Worker A claims and performs the side effect ...
    async with sqlite_store.transaction() as repos:
        first = await repos.commands.claim_command(
            durable_id, owner_class="supervisor", claim_token=f"{durable_id}:worker-a"
        )
    assert first.outcome is ControlPlaneOutcome.APPLIED

    # ... then the process crashes AFTER the side effect but BEFORE the receipt is
    # recorded (the injected AFTER_SIDE_EFFECT_BEFORE_RECEIPT window): no delivery
    # is written, so the command is not yet observable as applied.
    async with sqlite_store.transaction() as repos:
        mid = await repos.commands.get(durable_id)
    assert mid is not None and mid.status != COMMAND_STATE_APPLIED

    # A racing replacement worker with a different token must not seize execution
    # authority, so the crash cannot cause a second side effect.
    async with sqlite_store.transaction() as repos:
        other = await repos.commands.claim_command(
            durable_id, owner_class="supervisor", claim_token=f"{durable_id}:worker-b"
        )
    assert other.outcome is ControlPlaneOutcome.NOT_OWNER

    # The resumed worker re-claims its own authority idempotently and records the
    # delayed receipt exactly once.
    async with sqlite_store.transaction() as repos:
        resume = await repos.commands.claim_command(
            durable_id, owner_class="supervisor", claim_token=f"{durable_id}:worker-a"
        )
    assert resume.outcome is ControlPlaneOutcome.APPLIED
    async with sqlite_store.transaction() as repos:
        await repos.commands.record_command_delivery(
            durable_id,
            owner_class="supervisor",
            claim_token=f"{durable_id}:worker-a",
            outcome=ControlPlaneOutcome.APPLIED,
            provider_receipt_id=f"receipt-{durable_id}",
        )
        stored = await repos.commands.get(durable_id)
    assert stored is not None and stored.status == COMMAND_STATE_APPLIED

    # The independent provider ledger proves the crash retry never double-fired.
    assert run.ledger_multiple_side_effect_keys == ()


@pytest.mark.integration_ci
@pytest.mark.reliability_journey
@pytest.mark.asyncio
async def test_sqlite_command_key_reuse_with_different_payload_fails_closed(
    sqlite_store,
) -> None:
    # Idempotency-key conflict: a command key is bound to an immutable logical
    # identity; reusing it for a different payload digest fails closed rather than
    # silently returning a receipt for unrelated input.
    entry = INITIAL_CORPUS[0]
    run = project_run(run_plan(entry.plan), scenario_id=entry.scenario_id)
    result = await replay_projected_run(sqlite_store, run, namespace="idem-conflict")
    reused = run.commands[0]
    durable_key = f"idem-conflict:{reused.command_id}"
    with pytest.raises(CommandIdempotencyConflictError):
        async with sqlite_store.transaction() as repos:
            await repos.commands.record(
                command_id="different-command",
                session_id=result.session_id,
                command_type=reused.command_type,
                idempotency_key=durable_key,
                payload_digest="sha256:tampered-different-payload",
            )


@pytest.mark.integration_ci
@pytest.mark.reliability_journey
@pytest.mark.asyncio
async def test_sqlite_superseded_generation_command_is_fenced(sqlite_store) -> None:
    # Fencing safety (invariant 4): a command authored under a superseded session
    # supervisor generation must not be executed after ownership changed.
    async with sqlite_store.transaction() as repos:
        created = await repos.sessions.create(
            session_id="s-fence",
            moonmind_workflow_id="wf-fence",
            provider="omnigent",
            provider_session_ref="psess-fence",
        )
        # A stale supervisor records a command under generation 0.
        await repos.commands.record(
            command_id="stale-command",
            session_id="s-fence",
            command_type="submit_turn",
            idempotency_key="stale-command",
            payload_digest="sha256:stale",
            fencing_generation=created.fencing_generation,
        )
    # A replacement supervisor acquires a strictly newer generation.
    async with sqlite_store.transaction() as repos:
        current = await repos.sessions.get("s-fence")
        await repos.sessions.acquire_fencing_generation(
            "s-fence", FencingScope.SESSION_SUPERVISOR, expected_revision=current.revision
        )
    async with sqlite_store.transaction() as repos:
        claim = await repos.commands.claim_command(
            "stale-command", owner_class="supervisor", claim_token="stale-worker"
        )
    assert claim.outcome is ControlPlaneOutcome.FENCING_CONFLICT


@pytest.mark.integration_ci
@pytest.mark.reliability_journey
@pytest.mark.asyncio
async def test_sqlite_terminal_is_distinct_and_not_overwritten(sqlite_store) -> None:
    # Distinct terminality + monotonic authority: once a session is terminal, a
    # contradictory nonterminal lifecycle write is refused.
    entry = next(e for e in INITIAL_CORPUS if e.scenario_id.startswith("missed-terminal"))
    run = project_run(run_plan(entry.plan), scenario_id=entry.scenario_id)
    result = await replay_projected_run(sqlite_store, run, namespace="terminal-guard")
    assert result.session_terminal_state is not None
    with pytest.raises(TerminalSessionOverwriteError):
        async with sqlite_store.transaction() as repos:
            session = await repos.sessions.get(result.session_id)
            await repos.sessions.update_lifecycle(
                result.session_id,
                expected_revision=session.revision,
                expected_fencing_generation=session.fencing_generation,
                observed_state="reopened",
            )


# --- PostgreSQL concurrency journey (decisive races; needs real row locks) ---


@pytest.mark.integration_ci
@pytest.mark.asyncio
async def test_postgres_faultlab_replays_hold_boundary_invariants(pg_store) -> None:
    # The same scenarios prove the invariants on real PostgreSQL, where revision
    # and fencing guards are backed by true row locks rather than SQLite's
    # write serialization.
    entry = INITIAL_CORPUS[0]
    for name, plan in (
        ("missed-edge", entry.plan),
        ("seed-3", generate_plan(3)),
        ("seed-7", generate_plan(7)),
    ):
        run = project_run(run_plan(plan), scenario_id=name)
        result = await replay_projected_run(pg_store, run, namespace=f"pg-{name}")
        _assert_binding_invariants(run, result)


@pytest.mark.integration_ci
@pytest.mark.asyncio
async def test_postgres_concurrent_command_claim_admits_one_executor(pg_store) -> None:
    # A projected submit command is subjected to concurrent claimants: exactly one
    # wins execution authority (at-most-once), the racer is refused. This is the
    # decisive concurrency case the pure-domain and SQLite layers cannot prove.
    run = project_run(run_plan(generate_plan(5)), scenario_id="concurrent-claim")
    submit = run.submit_commands[0]
    async with pg_store.transaction() as repos:
        created = await repos.sessions.create(
            session_id="s-race",
            moonmind_workflow_id="wf-race",
            provider="omnigent",
            provider_session_ref="psess-race",
        )
        await repos.commands.record(
            command_id=submit.command_id,
            session_id="s-race",
            command_type=submit.command_type,
            idempotency_key=submit.command_id,
            payload_digest=submit.payload_digest,
            fencing_generation=created.fencing_generation,
        )

    async def _claim(token: str):
        async with pg_store.transaction() as repos:
            return await repos.commands.claim_command(
                submit.command_id, owner_class="supervisor", claim_token=token
            )

    results = await asyncio.gather(
        _claim("worker-a"), _claim("worker-b"), return_exceptions=True
    )
    outcomes = [r.outcome for r in results if not isinstance(r, Exception)]
    assert outcomes.count(ControlPlaneOutcome.APPLIED) == 1
    assert outcomes.count(ControlPlaneOutcome.NOT_OWNER) == 1
