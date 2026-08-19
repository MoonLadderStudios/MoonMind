"""Unit tests for the durable canonical turn-submission executor.

Source: MoonLadderStudios/MoonMind#3707 ([Omnigent control plane 6/11]
Unify continuations, remediation, checkpoints, and chat under canonical session
and turn ownership).

These exercise ``moonmind.omnigent.control_plane.turn_service.OmnigentTurnService``
against a real (SQLite-backed) control-plane store, proving that same-session
continuations, remediation, native chat, and checkpoint recovery all route
through one canonical session and one chat binding.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import Base, OmnigentSession
from moonmind.omnigent.control_plane import (
    BranchRequiredError,
    CallerAuthorityError,
    ChatCapability,
    CleanupFenceError,
    ControlPlaneOutcome,
    ImmutableSessionDimensions,
    OmnigentControlPlaneStore,
    OmnigentTurnService,
    RecoveryEvidence,
    RecoveryMode,
    RemediationAuthorityError,
    RemediationTurnIntent,
    SessionTerminalError,
    TurnSourceKind,
    TurnSubmissionRequest,
    TurnIdempotencyConflictError,
)


@pytest_asyncio.fixture()
async def session_factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/turn_service.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield maker
    await engine.dispose()


@pytest_asyncio.fixture()
async def store(session_factory):
    return OmnigentControlPlaneStore(session_factory)


@pytest_asyncio.fixture()
async def service(store):
    return OmnigentTurnService(store)


async def _establish(store, *, metadata=None):
    return await store.establish_session(
        session_id="sess-1",
        moonmind_workflow_id="wf-1",
        provider="codex",
        chat_binding_id="chat-1",
        first_turn_attempt_id="turn-initial",
        first_turn_idempotency_key="idem-initial",
        instruction_digest="digest-initial",
        metadata=metadata,
    )


def _reuse_request(kind=TurnSourceKind.REPOSITORY_CONTINUATION, **kwargs):
    instruction_digest = kwargs.pop("instruction_digest", "digest-cont-1")
    remediation = kwargs.get("remediation")
    if kind is TurnSourceKind.REMEDIATION and remediation is not None:
        kwargs.setdefault("controller_id", remediation.loop_id)
    return TurnSubmissionRequest(
        session_id=kwargs.pop("session_id", "sess-1"),
        source_kind=kind,
        caller_id=kwargs.pop("caller_id", "caller-1"),
        idempotency_key=kwargs.pop(
            "idempotency_key", f"idem-{kind.value}-{instruction_digest}"
        ),
        instruction_digest=instruction_digest,
        **kwargs,
    )


async def _count_sessions(session_factory) -> int:
    async with session_factory() as db:
        rows = (await db.execute(select(OmnigentSession))).scalars().all()
        return len(rows)


async def _mark_seed_turn_terminal(store) -> None:
    """Settle the repository helper's initial turn before cleanup tests."""

    async with store.transaction() as repos:
        session = await repos.sessions.get("sess-1")
        turn = await repos.turn_attempts.get("turn-initial")
        assert session is not None and turn is not None
        await repos.turn_attempts.mark_terminal(
            turn.turn_attempt_id,
            "completed",
            expected_revision=turn.revision,
            expected_fencing_generation=session.fencing_generation,
        )


# --- Continuation tests ------------------------------------------------------


@pytest.mark.asyncio
async def test_zero_continuations_leaves_single_session(store, session_factory):
    await _establish(store)
    assert await _count_sessions(session_factory) == 1


@pytest.mark.asyncio
async def test_single_continuation_reuses_session_and_binding(
    store, service, session_factory
):
    session, _ = await _establish(store)
    outcome = await service.submit_reuse_turn(_reuse_request())

    assert outcome.created is True
    assert outcome.session.session_id == session.session_id
    assert outcome.session.chat_binding_id == "chat-1"
    assert outcome.turn_attempt.session_id == session.session_id
    assert outcome.turn_attempt.turn_attempt_id != "turn-initial"
    # New active turn points at the continuation; session is not terminalized.
    assert outcome.session.active_turn_attempt_id == outcome.turn_attempt.turn_attempt_id
    assert outcome.session.is_terminal is False
    assert await _count_sessions(session_factory) == 1


@pytest.mark.asyncio
async def test_many_continuations_preserve_one_session_and_binding(
    store, service, session_factory
):
    await _establish(store)
    attempt_ids = set()
    for i in range(5):
        outcome = await service.submit_reuse_turn(
            _reuse_request(instruction_digest=f"digest-cont-{i}")
        )
        attempt_ids.add(outcome.turn_attempt.turn_attempt_id)
        assert outcome.session.chat_binding_id == "chat-1"
    assert len(attempt_ids) == 5  # each continuation is a distinct attempt
    assert await _count_sessions(session_factory) == 1


@pytest.mark.asyncio
async def test_redelivery_rejects_changed_instruction_or_session(store, service):
    await _establish(store)
    request = _reuse_request(idempotency_key="idem-reused")
    await service.submit_reuse_turn(request)

    with pytest.raises(TurnIdempotencyConflictError):
        await service.submit_reuse_turn(
            _reuse_request(
                idempotency_key="idem-reused",
                instruction_digest="different-digest",
            )
        )


@pytest.mark.asyncio
async def test_redelivery_rejects_nonowner_command_claim(store, service):
    await _establish(store)
    request = _reuse_request(idempotency_key="idem-owned")
    await service.submit_reuse_turn(
        request, owner_class="worker", claim_token="owner-a"
    )

    with pytest.raises(CallerAuthorityError):
        await service.submit_reuse_turn(
            request, owner_class="worker", claim_token="owner-b"
        )


@pytest.mark.asyncio
async def test_seven_continuations_reproduce_3685_shape_without_binding_churn(
    store, service, session_factory
):
    # #3685's diagnosed regression created seven bridge rows for seven attempts;
    # the canonical model keeps one session + one chat binding for all seven.
    await _establish(store)
    for i in range(7):
        outcome = await service.submit_reuse_turn(
            _reuse_request(instruction_digest=f"digest-cont-{i}")
        )
        assert outcome.session.session_id == "sess-1"
        assert outcome.session.chat_binding_id == "chat-1"

    assert await _count_sessions(session_factory) == 1
    async with store.transaction() as repos:
        attempts = await repos.turn_attempts.list_for_session("sess-1")
        alias = await repos.chat_binding_aliases.resolve("chat-1")
    # initial + seven continuations = eight attempts, one binding.
    assert len(attempts) == 8
    assert alias is not None and alias.session_id == "sess-1"


@pytest.mark.asyncio
async def test_delivery_ambiguity_does_not_duplicate_continuation(store, service):
    await _establish(store)
    first = await service.submit_reuse_turn(_reuse_request())
    # Redelivery of the identical logical turn (same scope) is idempotent.
    second = await service.submit_reuse_turn(_reuse_request())

    assert second.created is False
    assert second.turn_attempt.turn_attempt_id == first.turn_attempt.turn_attempt_id
    assert second.command.command_id == first.command.command_id
    async with store.transaction() as repos:
        attempts = await repos.turn_attempts.list_for_session("sess-1")
    assert len(attempts) == 2  # initial + one continuation, not two


@pytest.mark.asyncio
async def test_delivery_unknown_is_durable_on_the_same_turn(store, service):
    await _establish(store)
    outcome = await service.submit_reuse_turn(_reuse_request())

    attempt = await service.record_turn_delivery(
        outcome.turn_attempt.idempotency_key,
        outcome=ControlPlaneOutcome.DELIVERY_UNKNOWN,
    )

    assert attempt.turn_attempt_id == outcome.turn_attempt.turn_attempt_id
    assert attempt.state == "delivery_unknown"


@pytest.mark.asyncio
async def test_cleanup_is_fenced_until_accepted_turn_is_terminal(store, service):
    await _establish(store)
    await _mark_seed_turn_terminal(store)
    outcome = await service.submit_reuse_turn(_reuse_request())
    await service.record_turn_delivery(
        outcome.turn_attempt.idempotency_key,
        outcome=ControlPlaneOutcome.APPLIED,
    )

    with pytest.raises(CleanupFenceError):
        await service.claim_cleanup(
            "sess-1", owner_class="test", claim_token="cleanup-1"
        )

    await service.record_turn_terminal(
        outcome.turn_attempt.idempotency_key,
        terminal_state="completed",
        terminal_evidence_ref="artifact://turn/completed",
    )
    claim = await service.claim_cleanup(
        "sess-1", owner_class="test", claim_token="cleanup-1"
    )
    completed = await service.complete_cleanup(
        "sess-1",
        generation=claim.record.generation,
        owner_class="test",
        claim_token="cleanup-1",
    )

    assert completed.applied is True
    async with store.transaction() as repos:
        session = await repos.sessions.get("sess-1")
    assert session is not None
    assert session.cleanup_state == "complete"
    assert session.historical_read_state == "archived"


@pytest.mark.asyncio
async def test_released_unused_cleanup_claim_allows_new_turn(store, service):
    await _establish(store)
    await _mark_seed_turn_terminal(store)
    claim = await service.claim_cleanup(
        "sess-1", owner_class="oauth_host_janitor", claim_token="janitor:sess-1"
    )
    await service.release_cleanup_claim(
        "sess-1",
        generation=claim.record.generation,
        owner_class="oauth_host_janitor",
        claim_token="janitor:sess-1",
    )

    outcome = await service.submit_reuse_turn(
        _reuse_request(kind=TurnSourceKind.WORKFLOW_CHAT)
    )
    assert outcome.created is True


@pytest.mark.asyncio
async def test_cleanup_is_fenced_by_every_unsettled_turn_not_only_latest(
    store, service
):
    await _establish(store)
    await _mark_seed_turn_terminal(store)
    earlier = await service.submit_reuse_turn(
        _reuse_request(instruction_digest="digest-earlier")
    )
    latest = await service.submit_reuse_turn(
        _reuse_request(
            kind=TurnSourceKind.STEERING,
            instruction_digest="digest-latest",
        )
    )
    await service.record_turn_terminal(
        latest.turn_attempt.idempotency_key, terminal_state="completed"
    )

    with pytest.raises(CleanupFenceError):
        await service.claim_cleanup(
            "sess-1", owner_class="test", claim_token="cleanup-overlap"
        )

    await service.record_turn_terminal(
        earlier.turn_attempt.idempotency_key, terminal_state="completed"
    )
    claim = await service.claim_cleanup(
        "sess-1", owner_class="test", claim_token="cleanup-overlap"
    )
    assert claim.applied is True


@pytest.mark.asyncio
async def test_cleanup_rejects_losing_claimant(store, service):
    await _establish(store)
    await _mark_seed_turn_terminal(store)
    await service.claim_cleanup(
        "sess-1", owner_class="test", claim_token="cleanup-owner"
    )

    with pytest.raises(CleanupFenceError):
        await service.claim_cleanup(
            "sess-1", owner_class="test", claim_token="cleanup-loser"
        )


@pytest.mark.asyncio
async def test_attempt_completion_does_not_terminalize_session(store, service):
    await _establish(store)
    outcome = await service.submit_reuse_turn(_reuse_request())
    await service.record_turn_terminal(
        outcome.turn_attempt.idempotency_key,
        terminal_state="completed",
    )
    async with store.transaction() as repos:
        session = await repos.sessions.get("sess-1")
    assert session is not None
    assert session.is_terminal is False


@pytest.mark.asyncio
async def test_changed_immutable_dimension_requires_branch(store, service):
    await _establish(store, metadata={"repository": "repoA"})
    request = _reuse_request(
        requested_dimensions=ImmutableSessionDimensions(repository="repoB")
    )
    with pytest.raises(BranchRequiredError):
        await service.submit_reuse_turn(request)


@pytest.mark.asyncio
async def test_reuse_after_terminal_cleanup_is_fenced(store, service):
    await _establish(store)
    async with store.transaction() as repos:
        session = await repos.sessions.get("sess-1")
        assert session is not None
        await repos.sessions.update_lifecycle(
            "sess-1",
            expected_revision=session.revision,
            expected_fencing_generation=session.fencing_generation,
            cleanup_state="complete",
        )
    # plan_turn_submission raises SessionTerminalError before admit_continuation;
    # both express the same fail-closed rule against resurrecting torn-down
    # provider authority.
    with pytest.raises(SessionTerminalError):
        await service.submit_reuse_turn(_reuse_request())


@pytest.mark.asyncio
async def test_initial_kind_rejected_from_reuse_path(store, service):
    await _establish(store)
    with pytest.raises(CallerAuthorityError):
        await service.submit_reuse_turn(_reuse_request(kind=TurnSourceKind.INITIAL))


# --- Linked branch -----------------------------------------------------------


@pytest.mark.asyncio
async def test_open_linked_branch_allocates_new_session_and_binding(
    store, service, session_factory
):
    await _establish(store, metadata={"repository": "repoA"})
    request = TurnSubmissionRequest(
        session_id="sess-2",
        source_kind=TurnSourceKind.LINKED_BRANCH,
        caller_id="caller-1",
        idempotency_key="idem-linked-branch",
        instruction_digest="digest-branch",
        requested_dimensions=ImmutableSessionDimensions(repository="repoB"),
    )
    outcome = await service.open_linked_branch(
        request,
        moonmind_workflow_id="wf-1",
        provider="codex",
        new_session_id="sess-2",
        new_chat_binding_id="chat-2",
        parent_session_id="sess-1",
    )
    assert outcome.session.session_id == "sess-2"
    assert outcome.session.chat_binding_id == "chat-2"
    assert outcome.session.metadata.get("branched_from_session_id") == "sess-1"
    assert await _count_sessions(session_factory) == 2


@pytest.mark.asyncio
async def test_parent_cleanup_is_fenced_while_linked_branch_turn_is_active(
    store, service
):
    await _establish(store)
    await _mark_seed_turn_terminal(store)
    request = TurnSubmissionRequest(
        session_id="sess-2",
        source_kind=TurnSourceKind.LINKED_BRANCH,
        caller_id="caller-1",
        idempotency_key="idem-linked-active",
        instruction_digest="digest-branch-active",
    )
    branch = await service.open_linked_branch(
        request,
        moonmind_workflow_id="wf-1",
        provider="codex",
        new_session_id="sess-2",
        new_chat_binding_id="chat-2",
        parent_session_id="sess-1",
    )

    with pytest.raises(CleanupFenceError):
        await service.claim_cleanup(
            "sess-1", owner_class="test", claim_token="cleanup-parent"
        )

    await service.record_turn_terminal(
        branch.turn_attempt.idempotency_key, terminal_state="completed"
    )
    claim = await service.claim_cleanup(
        "sess-1", owner_class="test", claim_token="cleanup-parent"
    )
    assert claim.applied is True


# --- Remediation tests -------------------------------------------------------


def _remediation_intent(**kwargs) -> RemediationTurnIntent:
    base = dict(
        loop_id="loop-1",
        remediation_attempt_ordinal=1,
        of_turn_attempt_id="turn-initial",
        gate_result_ref="art://gate",
        remaining_work_ref="art://remaining",
        candidate_workspace_ref="art://ws",
        remediator_skill="fix-it",
        runtime_authority_ref="runtime://x",
        production_boundary_evidence_ref="art://prod",
        attempt_budget=3,
        branch_budget=2,
    )
    base.update(kwargs)
    return RemediationTurnIntent(**base)


@pytest.mark.asyncio
async def test_remediation_admits_typed_turn_linked_to_prior_attempt(store, service):
    await _establish(store)
    await _mark_seed_turn_terminal(store)
    request = _reuse_request(
        kind=TurnSourceKind.REMEDIATION,
        instruction_digest="digest-remediate",
        remediation=_remediation_intent(),
    )
    outcome = await service.submit_reuse_turn(request)
    assert outcome.created is True
    assert outcome.turn_attempt.remediation_of_turn_attempt_id == "turn-initial"
    assert outcome.session.session_id == "sess-1"


@pytest.mark.asyncio
async def test_remediation_requires_intent(store, service):
    await _establish(store)
    request = _reuse_request(
        kind=TurnSourceKind.REMEDIATION, instruction_digest="digest-remediate"
    )
    with pytest.raises(CallerAuthorityError):
        await service.submit_reuse_turn(request)


@pytest.mark.asyncio
async def test_remediation_requires_matching_controller_authority(store, service):
    await _establish(store)
    await _mark_seed_turn_terminal(store)
    request = _reuse_request(
        kind=TurnSourceKind.REMEDIATION,
        instruction_digest="digest-remediate",
        remediation=_remediation_intent(),
        controller_id="different-loop",
    )
    with pytest.raises(CallerAuthorityError):
        await service.submit_reuse_turn(request)


@pytest.mark.asyncio
async def test_remediation_requires_authoritative_predecessor(store, service):
    await _establish(store)
    request = _reuse_request(
        kind=TurnSourceKind.REMEDIATION,
        instruction_digest="digest-remediate",
        remediation=_remediation_intent(of_turn_attempt_id="turn-missing"),
    )
    with pytest.raises(CallerAuthorityError, match="unknown canonical predecessor"):
        await service.submit_reuse_turn(request)


@pytest.mark.asyncio
async def test_remediation_redelivery_rejects_changed_evidence(store, service):
    await _establish(store)
    await _mark_seed_turn_terminal(store)
    first = _reuse_request(
        kind=TurnSourceKind.REMEDIATION,
        instruction_digest="digest-remediate",
        idempotency_key="idem-remediation-reused",
        remediation=_remediation_intent(remaining_work_ref="art://remaining-a"),
    )
    await service.submit_reuse_turn(first)

    with pytest.raises(TurnIdempotencyConflictError):
        await service.submit_reuse_turn(
            _reuse_request(
                kind=TurnSourceKind.REMEDIATION,
                instruction_digest="digest-remediate",
                idempotency_key="idem-remediation-reused",
                remediation=_remediation_intent(
                    remaining_work_ref="art://remaining-b"
                ),
            )
        )


@pytest.mark.asyncio
async def test_remediator_cannot_broaden_workspace_authority(store, service):
    await _establish(store, metadata={"workspace_ref": "ws-1"})
    await _mark_seed_turn_terminal(store)
    request = _reuse_request(
        kind=TurnSourceKind.REMEDIATION,
        instruction_digest="digest-remediate",
        remediation=_remediation_intent(
            granted_dimensions=ImmutableSessionDimensions(workspace_ref="ws-2")
        ),
    )
    with pytest.raises(RemediationAuthorityError):
        await service.submit_reuse_turn(request)


@pytest.mark.asyncio
async def test_remediation_policy_can_require_a_branch(store, service):
    await _establish(store)
    request = _reuse_request(
        kind=TurnSourceKind.REMEDIATION,
        instruction_digest="digest-remediate",
        remediation=_remediation_intent(allow_same_session_reuse=False),
    )
    with pytest.raises(BranchRequiredError):
        await service.submit_reuse_turn(request)


@pytest.mark.asyncio
async def test_remediator_cannot_grant_publication_authority(store, service):
    await _establish(store)  # base grants_publication_authority defaults to False
    await _mark_seed_turn_terminal(store)
    request = _reuse_request(
        kind=TurnSourceKind.REMEDIATION,
        instruction_digest="digest-remediate",
        remediation=_remediation_intent(grants_publication_authority=True),
    )
    with pytest.raises(RemediationAuthorityError):
        await service.submit_reuse_turn(request)


# --- Chat capability ---------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_active_session_advertises_write(store, service):
    await _establish(store)
    decision = await service.resolve_chat_capability("chat-1", caller_authorized=True)
    assert decision.capability is ChatCapability.READ_WRITE


@pytest.mark.asyncio
async def test_chat_terminal_attempt_within_active_session_stays_interactive(
    store, service
):
    await _establish(store)
    outcome = await service.submit_reuse_turn(_reuse_request())
    await service.record_turn_terminal(
        outcome.turn_attempt.idempotency_key,
        terminal_state="completed",
    )
    decision = await service.resolve_chat_capability("chat-1", caller_authorized=True)
    assert decision.capability is ChatCapability.READ_WRITE


@pytest.mark.asyncio
async def test_chat_final_session_terminality_is_read_only(store, service):
    await _establish(store)
    await service.mark_session_terminal("sess-1", terminal_state="completed")
    decision = await service.resolve_chat_capability("chat-1", caller_authorized=True)
    assert decision.capability is ChatCapability.READ_ONLY
    assert decision.historical_read_available is True


@pytest.mark.asyncio
@pytest.mark.parametrize("cleanup_state", ["complete", "released"])
async def test_chat_history_readable_after_cleanup(
    store, service, cleanup_state
):
    await _establish(store)
    await service.mark_session_terminal("sess-1", terminal_state="completed")
    async with store.transaction() as repos:
        session = await repos.sessions.get("sess-1")
        assert session is not None
        await repos.sessions.update_lifecycle(
            "sess-1",
            expected_revision=session.revision,
            expected_fencing_generation=session.fencing_generation,
            cleanup_state=cleanup_state,
            historical_read_state="archived",
        )
    decision = await service.resolve_chat_capability("chat-1", caller_authorized=True)
    assert decision.capability is ChatCapability.READ_ONLY
    assert decision.historical_read_available is True


# --- Checkpoint recovery -----------------------------------------------------


@pytest.mark.asyncio
async def test_recovery_live_reattach_with_complete_authority(store, service):
    await _establish(store, metadata={"repository": "repoA"})
    decision = await service.decide_session_recovery(
        "sess-1",
        recovery_idempotency_key="recovery-live",
        intent_dimensions=ImmutableSessionDimensions(repository="repoA"),
        live_authority=RecoveryEvidence(
            intent_dimensions=ImmutableSessionDimensions(),
            session_dimensions=ImmutableSessionDimensions(),
            provider_profile_lease_current=True,
            host_available=True,
            provider_session_reachable=True,
            cursor_present=True,
            first_message_consistent=True,
            credential_generation_current=True,
        ),
    )
    assert decision.mode is RecoveryMode.LIVE_REATTACH


@pytest.mark.asyncio
async def test_recovery_cold_restore_after_source_removal(store, service):
    await _establish(store, metadata={"repository": "repoA"})
    decision = await service.decide_session_recovery(
        "sess-1",
        recovery_idempotency_key="recovery-cold",
        intent_dimensions=ImmutableSessionDimensions(repository="repoA"),
        live_authority=RecoveryEvidence(
            intent_dimensions=ImmutableSessionDimensions(),
            session_dimensions=ImmutableSessionDimensions(),
            # host/process/provider session removed; only artifacts survive.
            workspace_artifact_valid=True,
            session_evidence_valid=True,
        ),
    )
    assert decision.mode is RecoveryMode.COLD_RESTORE


@pytest.mark.asyncio
async def test_recovery_branch_required_on_immutable_input_change(store, service):
    await _establish(store, metadata={"repository": "repoA"})
    decision = await service.decide_session_recovery(
        "sess-1",
        recovery_idempotency_key="recovery-branch",
        intent_dimensions=ImmutableSessionDimensions(repository="repoB"),
        live_authority=RecoveryEvidence(
            intent_dimensions=ImmutableSessionDimensions(),
            session_dimensions=ImmutableSessionDimensions(),
            provider_profile_lease_current=True,
            host_available=True,
            provider_session_reachable=True,
            cursor_present=True,
            first_message_consistent=True,
            credential_generation_current=True,
        ),
    )
    assert decision.mode is RecoveryMode.BRANCH_REQUIRED


@pytest.mark.asyncio
async def test_recovery_repeated_requests_are_idempotent(store, service):
    await _establish(store, metadata={"repository": "repoA"})
    kwargs = dict(
        recovery_idempotency_key="recovery-repeat",
        intent_dimensions=ImmutableSessionDimensions(repository="repoA"),
        live_authority=RecoveryEvidence(
            intent_dimensions=ImmutableSessionDimensions(),
            session_dimensions=ImmutableSessionDimensions(),
            workspace_artifact_valid=True,
            session_evidence_valid=True,
        ),
    )
    first = await service.decide_session_recovery("sess-1", **kwargs)
    second = await service.decide_session_recovery("sess-1", **kwargs)
    assert first == second


@pytest.mark.asyncio
async def test_recovery_idempotency_key_rejects_changed_evidence(store, service):
    await _establish(store, metadata={"repository": "repoA"})
    evidence = RecoveryEvidence(
        intent_dimensions=ImmutableSessionDimensions(),
        session_dimensions=ImmutableSessionDimensions(),
        workspace_artifact_valid=True,
        session_evidence_valid=True,
    )
    await service.decide_session_recovery(
        "sess-1",
        recovery_idempotency_key="recovery-conflict",
        intent_dimensions=ImmutableSessionDimensions(repository="repoA"),
        live_authority=evidence,
    )

    with pytest.raises(TurnIdempotencyConflictError):
        await service.decide_session_recovery(
            "sess-1",
            recovery_idempotency_key="recovery-conflict",
            intent_dimensions=ImmutableSessionDimensions(repository="repoB"),
            live_authority=evidence,
        )


@pytest.mark.asyncio
async def test_recovery_unknown_session_fails_closed(store, service):
    with pytest.raises(CallerAuthorityError):
        await service.decide_session_recovery(
            "sess-missing",
            recovery_idempotency_key="recovery-missing",
            intent_dimensions=ImmutableSessionDimensions(),
            live_authority=RecoveryEvidence(
                intent_dimensions=ImmutableSessionDimensions(),
                session_dimensions=ImmutableSessionDimensions(),
            ),
        )
