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
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import Base, OmnigentSession
from moonmind.omnigent.control_plane import (
    BranchRequiredError,
    CallerAuthorityError,
    ChatCapability,
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
)
from sqlalchemy import select


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
    return TurnSubmissionRequest(
        session_id=kwargs.pop("session_id", "sess-1"),
        source_kind=kind,
        caller_id=kwargs.pop("caller_id", "caller-1"),
        instruction_digest=kwargs.pop("instruction_digest", "digest-cont-1"),
        **kwargs,
    )


async def _count_sessions(session_factory) -> int:
    async with session_factory() as db:
        rows = (await db.execute(select(OmnigentSession))).scalars().all()
        return len(rows)


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
async def test_attempt_completion_does_not_terminalize_session(store, service):
    await _establish(store)
    outcome = await service.submit_reuse_turn(_reuse_request())
    async with store.transaction() as repos:
        await repos.turn_attempts.mark_terminal(
            outcome.turn_attempt.turn_attempt_id, "completed"
        )
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
        await repos.sessions.update_lifecycle("sess-1", cleanup_state="complete")
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
async def test_remediator_cannot_broaden_workspace_authority(store, service):
    await _establish(store, metadata={"workspace_ref": "ws-1"})
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
async def test_remediator_cannot_grant_publication_authority(store, service):
    await _establish(store)  # base grants_publication_authority defaults to False
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
    async with store.transaction() as repos:
        await repos.turn_attempts.mark_terminal(
            outcome.turn_attempt.turn_attempt_id, "completed"
        )
    decision = await service.resolve_chat_capability("chat-1", caller_authorized=True)
    assert decision.capability is ChatCapability.READ_WRITE


@pytest.mark.asyncio
async def test_chat_final_session_terminality_is_read_only(store, service):
    await _establish(store)
    async with store.transaction() as repos:
        await repos.sessions.mark_terminal("sess-1", "completed")
    decision = await service.resolve_chat_capability("chat-1", caller_authorized=True)
    assert decision.capability is ChatCapability.READ_ONLY
    assert decision.historical_read_available is True


@pytest.mark.asyncio
async def test_chat_history_readable_after_cleanup(store, service):
    await _establish(store)
    async with store.transaction() as repos:
        await repos.sessions.mark_terminal("sess-1", "completed")
        await repos.sessions.update_lifecycle(
            "sess-1", cleanup_state="released", historical_read_state="archived"
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
async def test_recovery_unknown_session_fails_closed(store, service):
    with pytest.raises(CallerAuthorityError):
        await service.decide_session_recovery(
            "sess-missing",
            intent_dimensions=ImmutableSessionDimensions(),
            live_authority=RecoveryEvidence(
                intent_dimensions=ImmutableSessionDimensions(),
                session_dimensions=ImmutableSessionDimensions(),
            ),
        )
