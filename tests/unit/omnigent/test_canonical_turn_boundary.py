"""Unit tests for the canonical Omnigent turn-command boundary.

Source: MoonLadderStudios/MoonMind#3707 ([Omnigent control plane] route all
continuations, remediation, checkpoints, and chat through canonical sessions and
turn attempts).

Covers the closed source vocabulary, the producer inventory, immutable
execution-authority binding, the one typed live-reattach / cold-restore /
branch / new-session / unavailable decision boundary, and the durable
same-session, delivery-ambiguity, remediation, checkpoint, and cleanup-race
behaviour of :class:`CanonicalTurnService`.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import Base
from moonmind.omnigent.control_plane import (
    IMMUTABLE_AUTHORITY_METADATA_KEY,
    TURN_INSTRUCTION_METADATA_PREFIX,
    CanonicalTurnRequest,
    CanonicalTurnService,
    OmnigentControlPlaneStore,
    TurnIdempotencyConflictError,
    TurnProducerNotRegisteredError,
)
from moonmind.omnigent.control_plane.records import (
    CLEANUP_STATE_CLAIMED,
    ControlPlaneOutcome,
    TURN_STATE_DELIVERY_UNKNOWN,
    FencingScope,
)
from moonmind.omnigent.turn_contracts import (
    ACTOR_NOT_SESSION_OWNER,
    CHAT_BINDING_MISMATCH,
    CLEANUP_IN_PROGRESS,
    EXECUTION_PLAN_NOT_RECORDED,
    FENCING_GENERATION_SUPERSEDED,
    IMMUTABLE_AUTHORITY_CHANGED,
    IMMUTABLE_AUTHORITY_DIMENSIONS,
    PARENT_TURN_REQUIRED,
    REMEDIATION_EVIDENCE_REQUIRED,
    REMEDIATION_LOCKED_DIMENSIONS,
    REMEDIATION_WOULD_BROADEN_AUTHORITY,
    RUNTIME_AUTHORITY_INCOMPLETE,
    SESSION_NOT_FOUND,
    SESSION_REUSE_NOT_PERMITTED,
    SESSION_REVISION_CONFLICT,
    SESSION_TERMINAL,
    TURN_PRODUCER_SOURCES,
    TURN_SOURCE_POLICIES,
    TURN_SOURCE_VOCABULARY_VERSION,
    ImmutableExecutionAuthority,
    OmnigentTurnSource,
    RuntimeAuthorityEvidence,
    TurnAdmissionRequest,
    TurnDisposition,
    UnknownTurnSourceError,
    evaluate_turn_admission,
    resolve_turn_source,
)

PLAN_REF = "omnigent-execution-plan:sha256:" + "a" * 64
BINDING_REF = "omnigent-runtime-binding:sha256:" + "b" * 64

RECORDED_AUTHORITY = ImmutableExecutionAuthority(
    executionPlanRef=PLAN_REF,
    runtimeBindingRef=BINDING_REF,
    harnessId="generic-native",
    executionRealizerRef="realizer:generic",
    providerProfileId="profile-1",
    providerProfileGeneration=3,
    modelConfigDigest="sha256:" + "c" * 64,
    repositoryRef="repo:example",
    branchRef="refs/heads/main",
    workspaceIntentRef="workspace-intent:1",
    resolvedSkillsDigest="sha256:" + "d" * 64,
    launchPolicyRef="launch-policy:1",
    policySnapshotRef="policy-snapshot:1",
    publicationAuthorityRef="publication:1",
)

LIVE_EVIDENCE = RuntimeAuthorityEvidence(
    providerSessionAttached=True,
    providerSessionResumable=True,
    hostAttached=True,
    hostLeaseActive=True,
    credentialLeaseActive=True,
    providerProfileGenerationCurrent=True,
    workspaceAvailable=True,
)


# --- Fixtures ----------------------------------------------------------------


@pytest_asyncio.fixture()
async def session_factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/turn_boundary.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield maker
    await engine.dispose()


@pytest_asyncio.fixture()
async def store(session_factory):
    return OmnigentControlPlaneStore(session_factory)


async def _establish(store, *, session_id="s1", chat_binding_id="cb-1", actor="user-1"):
    session, turn = await store.establish_session(
        session_id=session_id,
        moonmind_workflow_id=f"wf-{session_id}",
        provider="generic",
        chat_binding_id=chat_binding_id,
        provider_session_ref=f"psess-{session_id}",
        first_turn_attempt_id=f"{session_id}-t0",
        first_turn_idempotency_key=f"{session_id}-idem-0",
        metadata={
            "actorId": actor,
            IMMUTABLE_AUTHORITY_METADATA_KEY: RECORDED_AUTHORITY.as_dict(),
        },
    )
    async with store.transaction() as repos:
        session = await repos.sessions.bind_runtime_authority(
            session_id,
            expected_revision=session.revision,
            expected_fencing_generation=session.fencing_generation,
            provider_profile_id="profile-1",
            provider_profile_generation=3,
            host_binding_ref="host-binding-1",
            host_lease_ref="host-lease-1",
            credential_generation=3,
        )
    return session, turn


def _request(**overrides) -> CanonicalTurnRequest:
    kwargs = dict(
        producer="omnigent.repository_output_continuation",
        session_id="s1",
        turn_attempt_id="t1",
        idempotency_key="idem-1",
        instruction_ref="artifact://instructions/1",
        source=OmnigentTurnSource.REPOSITORY_CONTINUATION,
        parent_turn_attempt_id="s1-t0",
        evidence=LIVE_EVIDENCE,
    )
    kwargs.update(overrides)
    return CanonicalTurnRequest(**kwargs)


def _admission(**overrides) -> TurnAdmissionRequest:
    kwargs = dict(
        producer="omnigent.repository_output_continuation",
        source=OmnigentTurnSource.REPOSITORY_CONTINUATION,
        sessionId="s1",
        parentTurnAttemptId="t0",
        expectedSessionRevision=2,
        currentSessionRevision=2,
        expectedFencingGeneration=0,
        currentFencingGeneration=0,
        recordedAuthority=RECORDED_AUTHORITY,
        evidence=LIVE_EVIDENCE,
    )
    kwargs.update(overrides)
    return TurnAdmissionRequest(**kwargs)


# --- Closed source vocabulary -------------------------------------------------


def test_turn_source_vocabulary_is_closed_and_versioned() -> None:
    assert TURN_SOURCE_VOCABULARY_VERSION == "moonmind.omnigent-turn-source/v1"
    assert {item.value for item in OmnigentTurnSource} == {
        "initial",
        "repository_continuation",
        "remediation",
        "workflow_chat",
        "steering",
        "approval_response",
        "checkpoint_resume",
        "linked_branch",
    }
    # Every member has exactly one policy; an unknown value fails closed rather
    # than degrading to a permissive default.
    assert set(TURN_SOURCE_POLICIES) == set(OmnigentTurnSource)
    with pytest.raises(UnknownTurnSourceError):
        resolve_turn_source("continuation")
    with pytest.raises(UnknownTurnSourceError):
        resolve_turn_source("")


def test_every_registered_producer_binds_to_one_vocabulary_member() -> None:
    """AC1: the production-producer inventory is exhaustive and typed."""

    assert set(TURN_PRODUCER_SOURCES.values()) <= set(OmnigentTurnSource)
    # Each historical follow-up producer named by the issue is present.
    for producer in (
        "omnigent.repository_output_continuation",
        "omnigent.remediation_controller",
        "omnigent.workflow_chat.http",
        "omnigent.workflow_chat.websocket",
        "omnigent.workflow_chat.steering",
        "omnigent.workflow_chat.approval_response",
        "omnigent.checkpoint_resume",
        "omnigent.checkpoint_branch_turn",
        "omnigent.linked_branch_workflow",
        "omnigent.edit_and_rerun_reconstruction",
        "omnigent.execution_realizer",
    ):
        assert producer in TURN_PRODUCER_SOURCES


def test_source_kind_never_changes_the_command_or_fencing_model() -> None:
    """Source kind varies only authorization, evidence, and reuse policy."""

    varying = {
        "requires_end_user_actor",
        "requires_parent_turn",
        "requires_remediation_evidence",
        "requires_checkpoint_evidence",
        "may_reuse_session",
        "requires_new_session",
        "requires_chat_binding",
        "source",
    }
    assert set(TURN_SOURCE_POLICIES[OmnigentTurnSource.WORKFLOW_CHAT].model_dump()) == (
        varying
    )


def test_canonical_turn_service_is_harness_neutral() -> None:
    """Required work 6: no Codex-versus-OpenCode lifecycle branch in the path.

    Executable tokens only: prose may *name* the harnesses it refuses to branch
    on, but no identifier, literal, or comparison in the canonical turn path may
    mention a specific harness.
    """

    import io
    import tokenize
    from pathlib import Path

    root = Path(__file__).resolve().parents[3] / "moonmind/omnigent"
    for module in ("turn_contracts.py", "control_plane/turn_service.py"):
        source = (root / module).read_text()
        code_tokens = [
            token.string.lower()
            for token in tokenize.generate_tokens(io.StringIO(source).readline)
            if token.type not in {tokenize.STRING, tokenize.COMMENT, tokenize.NL}
        ]
        joined = " ".join(code_tokens)
        # ``harness_id`` may appear: the harness is one immutable *authority
        # dimension* compared against the recorded plan. A specific harness
        # *name* may not, because that is a lifecycle branch.
        for harness_token in ("codex", "opencode", "claude", "gemini"):
            assert harness_token not in joined, (module, harness_token)


# --- Immutable execution authority --------------------------------------------


def test_changed_immutable_authority_requires_a_branch() -> None:
    """AC5: a changed immutable dimension is an explicit branch decision."""

    for dimension in IMMUTABLE_AUTHORITY_DIMENSIONS:
        requested = RECORDED_AUTHORITY.model_copy(
            update={
                _python_name(dimension): (
                    99
                    if dimension == "providerProfileGeneration"
                    else f"changed-{dimension}"
                )
            }
        )
        decision = evaluate_turn_admission(_admission(requestedAuthority=requested))
        assert not decision.admitted, dimension
        assert decision.disposition is TurnDisposition.BRANCH_REQUIRED, dimension
        assert dimension in decision.changed_dimensions, dimension
        assert decision.reason_code == IMMUTABLE_AUTHORITY_CHANGED


def _python_name(alias: str) -> str:
    return ImmutableExecutionAuthority.model_fields and next(
        name
        for name, field in ImmutableExecutionAuthority.model_fields.items()
        if field.alias == alias
    )


def test_unspecified_dimensions_do_not_force_a_spurious_branch() -> None:
    decision = evaluate_turn_admission(_admission())
    assert decision.admitted
    assert decision.changed_dimensions == ()
    assert decision.disposition is TurnDisposition.LIVE_REATTACH


def test_missing_recorded_plan_requires_a_new_session() -> None:
    decision = evaluate_turn_admission(
        _admission(recordedAuthority=ImmutableExecutionAuthority())
    )
    assert decision.disposition is TurnDisposition.NEW_SESSION_REQUIRED
    assert decision.reason_code == EXECUTION_PLAN_NOT_RECORDED


def test_remediation_cannot_broaden_authority() -> None:
    """AC6: remediation narrows work; it never broadens authority."""

    for dimension in sorted(REMEDIATION_LOCKED_DIMENSIONS):
        requested = RECORDED_AUTHORITY.model_copy(
            update={_python_name(dimension): f"broadened-{dimension}"}
        )
        decision = evaluate_turn_admission(
            _admission(
                producer="omnigent.remediation_controller",
                source=OmnigentTurnSource.REMEDIATION,
                remediationOfTurnAttemptId="t0",
                remediationGateRef="artifact://gates/publish",
                requestedAuthority=requested,
            )
        )
        assert not decision.admitted, dimension
        assert decision.reason_code == REMEDIATION_WOULD_BROADEN_AUTHORITY, dimension
        assert decision.disposition is TurnDisposition.BRANCH_REQUIRED


def test_typed_remediation_requires_gate_and_lineage_evidence() -> None:
    decision = evaluate_turn_admission(
        _admission(
            producer="omnigent.remediation_controller",
            source=OmnigentTurnSource.REMEDIATION,
        )
    )
    assert decision.reason_code == REMEDIATION_EVIDENCE_REQUIRED
    assert not decision.admitted


def test_continuation_requires_its_parent_turn() -> None:
    decision = evaluate_turn_admission(_admission(parentTurnAttemptId=None))
    assert decision.reason_code == PARENT_TURN_REQUIRED


# --- One typed decision boundary ---------------------------------------------


def test_decision_vocabulary_is_the_single_evidence_gated_contract() -> None:
    """AC7: exactly five outcomes, shared by resume, restore, and branching."""

    assert {item.value for item in TurnDisposition} == {
        "live_reattach",
        "cold_restore",
        "branch_required",
        "new_session_required",
        "resume_unavailable",
    }


def test_live_reattach_requires_complete_current_authority() -> None:
    for field in (
        "providerSessionAttached",
        "providerSessionResumable",
        "hostAttached",
        "hostLeaseActive",
        "credentialLeaseActive",
        "providerProfileGenerationCurrent",
        "workspaceAvailable",
    ):
        partial = LIVE_EVIDENCE.model_copy(
            update={_evidence_name(field): False, "checkpoint_restorable": True}
        )
        decision = evaluate_turn_admission(_admission(evidence=partial))
        assert decision.disposition is TurnDisposition.COLD_RESTORE, field
        assert decision.admitted


def _evidence_name(alias: str) -> str:
    return next(
        name
        for name, field in RuntimeAuthorityEvidence.model_fields.items()
        if field.alias == alias
    )


def test_cold_restore_requires_artifact_backed_evidence() -> None:
    """A destroyed host-local path is not cold-restore evidence."""

    stripped = RuntimeAuthorityEvidence(checkpointRestorable=False)
    decision = evaluate_turn_admission(_admission(evidence=stripped))
    assert decision.disposition is TurnDisposition.RESUME_UNAVAILABLE
    assert decision.reason_code == RUNTIME_AUTHORITY_INCOMPLETE
    restorable = RuntimeAuthorityEvidence(checkpointRestorable=True)
    decision = evaluate_turn_admission(_admission(evidence=restorable))
    assert decision.disposition is TurnDisposition.COLD_RESTORE
    assert decision.admitted


def test_branch_sources_always_get_a_new_canonical_session() -> None:
    for source, producer in (
        (OmnigentTurnSource.LINKED_BRANCH, "omnigent.linked_branch_workflow"),
        (OmnigentTurnSource.INITIAL, "omnigent.session_supervisor.initial"),
    ):
        decision = evaluate_turn_admission(
            _admission(
                producer=producer,
                source=source,
                checkpointRef="artifact://checkpoints/1",
            )
        )
        assert decision.disposition is TurnDisposition.NEW_SESSION_REQUIRED
        assert decision.reason_code == SESSION_REUSE_NOT_PERMITTED
        assert not decision.admitted


def test_terminal_session_forces_a_new_session_without_erasing_history() -> None:
    """AC4: session terminality and turn terminality stay distinct."""

    decision = evaluate_turn_admission(
        _admission(evidence=LIVE_EVIDENCE.model_copy(update={"session_terminal": True}))
    )
    assert decision.disposition is TurnDisposition.NEW_SESSION_REQUIRED
    assert decision.reason_code == SESSION_TERMINAL


def test_stale_revision_and_superseded_fence_fail_before_mutation() -> None:
    stale_revision = evaluate_turn_admission(_admission(expectedSessionRevision=1))
    assert stale_revision.reason_code == SESSION_REVISION_CONFLICT
    superseded = evaluate_turn_admission(_admission(currentFencingGeneration=4))
    assert superseded.reason_code == FENCING_GENERATION_SUPERSEDED
    assert not superseded.admitted


def test_cross_user_and_cross_binding_submissions_are_refused() -> None:
    cross_user = evaluate_turn_admission(
        _admission(
            producer="omnigent.workflow_chat.http",
            source=OmnigentTurnSource.WORKFLOW_CHAT,
            actorId="attacker",
            sessionActorId="owner",
            chatBindingId="cb-1",
            sessionChatBindingId="cb-1",
        )
    )
    assert cross_user.reason_code == ACTOR_NOT_SESSION_OWNER
    cross_binding = evaluate_turn_admission(
        _admission(
            producer="omnigent.workflow_chat.http",
            source=OmnigentTurnSource.WORKFLOW_CHAT,
            actorId="owner",
            sessionActorId="owner",
            chatBindingId="cb-other",
            sessionChatBindingId="cb-1",
        )
    )
    assert cross_binding.reason_code == CHAT_BINDING_MISMATCH


def test_cleanup_racing_a_new_turn_is_fenced_deterministically() -> None:
    claimed = evaluate_turn_admission(
        _admission(evidence=LIVE_EVIDENCE.model_copy(update={"cleanup_state": "claimed"}))
    )
    assert claimed.reason_code == CLEANUP_IN_PROGRESS
    assert claimed.disposition is TurnDisposition.RESUME_UNAVAILABLE
    complete = evaluate_turn_admission(
        _admission(
            evidence=LIVE_EVIDENCE.model_copy(update={"cleanup_state": "complete"})
        )
    )
    assert complete.disposition is TurnDisposition.NEW_SESSION_REQUIRED


# --- Durable service behaviour ------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("continuation_count", [0, 1, 7])
async def test_n_continuations_preserve_one_session_and_one_binding(
    store, continuation_count
) -> None:
    """AC2: zero, one, and seven continuations keep one session and binding."""

    await _establish(store)
    service = CanonicalTurnService(store)
    for index in range(continuation_count):
        result = await service.submit_turn(
            _request(
                turn_attempt_id=f"t{index + 1}",
                idempotency_key=f"idem-{index + 1}",
                instruction_ref=f"artifact://instructions/{index + 1}",
            )
        )
        assert result.admitted
        assert result.disposition is TurnDisposition.LIVE_REATTACH

    async with store.transaction() as repos:
        sessions_for_binding = await repos.sessions.get_by_chat_binding("cb-1")
        turns = await repos.turn_attempts.list_for_session("s1")
        commands = await repos.commands.list_for_session("s1")
    assert sessions_for_binding.session_id == "s1"
    assert sessions_for_binding.chat_binding_id == "cb-1"
    # One initial turn plus one distinct attempt per continuation.
    assert len(turns) == continuation_count + 1
    assert len({t.turn_attempt_id for t in turns}) == continuation_count + 1
    submit_commands = [
        c for c in commands if c.command_type == "omnigent.submit_turn"
    ]
    assert len(submit_commands) == continuation_count


@pytest.mark.asyncio
async def test_chat_continuation_and_remediation_share_one_journal(store) -> None:
    """AC1/AC9: three different producers, one turn repository and journal."""

    await _establish(store)
    service = CanonicalTurnService(store)
    submissions = (
        _request(
            producer="omnigent.workflow_chat.http",
            source=OmnigentTurnSource.WORKFLOW_CHAT,
            actor_id="user-1",
            chat_binding_id="cb-1",
            turn_attempt_id="chat-1",
            idempotency_key="chat-idem-1",
        ),
        _request(turn_attempt_id="cont-1", idempotency_key="cont-idem-1"),
        _request(
            producer="omnigent.remediation_controller",
            source=OmnigentTurnSource.REMEDIATION,
            turn_attempt_id="rem-1",
            idempotency_key="rem-idem-1",
            remediation_of_turn_attempt_id="s1-t0",
            remediation_gate_ref="artifact://gates/publish",
        ),
    )
    for request in submissions:
        assert (await service.submit_turn(request)).admitted

    async with store.transaction() as repos:
        turns = await repos.turn_attempts.list_for_session("s1")
        commands = await repos.commands.list_for_session("s1")
    sources = {t.turn_attempt_id: t.turn_source for t in turns}
    assert sources["chat-1"] == "workflow_chat"
    assert sources["cont-1"] == "repository_continuation"
    assert sources["rem-1"] == "remediation"
    submit_ids = {
        c.turn_attempt_id
        for c in commands
        if c.command_type == "omnigent.submit_turn"
    }
    assert submit_ids == {"chat-1", "cont-1", "rem-1"}


@pytest.mark.asyncio
async def test_every_turn_records_the_required_authority(store) -> None:
    """AC3: source, digest, idempotency, revision, fence, and refs are durable."""

    await _establish(store)
    service = CanonicalTurnService(store)
    result = await service.submit_turn(_request())
    turn = result.turn_attempt
    assert turn.turn_source == "repository_continuation"
    assert turn.idempotency_key == "idem-1"
    assert turn.instruction_digest
    assert turn.execution_plan_ref == PLAN_REF
    assert turn.runtime_binding_ref == BINDING_REF
    assert turn.authority_digest == RECORDED_AUTHORITY.authority_digest
    assert turn.expected_session_revision is not None
    command = result.command
    assert command.expected_session_revision == turn.expected_session_revision
    assert command.fencing_generation == 0
    async with store.transaction() as repos:
        session = await repos.sessions.get("s1")
    assert session.active_turn_attempt_id == "t1"
    assert (
        session.metadata[f"{TURN_INSTRUCTION_METADATA_PREFIX}t1"]
        == "artifact://instructions/1"
    )


@pytest.mark.asyncio
async def test_duplicate_request_reuses_one_logical_turn(store) -> None:
    """Delivery/retry: a duplicate browser or controller request is one turn."""

    await _establish(store)
    service = CanonicalTurnService(store)
    first = await service.submit_turn(_request())
    second = await service.submit_turn(_request())
    assert first.turn_attempt.turn_attempt_id == second.turn_attempt.turn_attempt_id
    assert first.command.command_id == second.command.command_id
    async with store.transaction() as repos:
        turns = await repos.turn_attempts.list_for_session("s1")
    assert len([t for t in turns if t.turn_source == "repository_continuation"]) == 1


@pytest.mark.asyncio
async def test_reused_key_for_a_different_logical_turn_fails_closed(store) -> None:
    await _establish(store)
    service = CanonicalTurnService(store)
    await service.submit_turn(_request())
    with pytest.raises(TurnIdempotencyConflictError):
        await service.submit_turn(
            _request(
                turn_attempt_id="t-other",
                instruction_ref="artifact://instructions/changed",
            )
        )


@pytest.mark.asyncio
async def test_ambiguous_delivery_is_not_resubmitted_blindly(store) -> None:
    """Retry observes delivery ambiguity instead of issuing a second command."""

    await _establish(store)
    service = CanonicalTurnService(store)
    first = await service.submit_turn(_request())
    async with store.transaction() as repos:
        claim = await repos.commands.claim_command(
            first.command.command_id,
            owner_class="activity",
            claim_token="claim-1",
        )
        assert claim.applied
        await repos.commands.record_command_delivery(
            first.command.command_id,
            owner_class="activity",
            claim_token="claim-1",
            outcome=ControlPlaneOutcome.DELIVERY_UNKNOWN,
        )
        await repos.turn_attempts.advance_state(
            first.turn_attempt.turn_attempt_id,
            state=TURN_STATE_DELIVERY_UNKNOWN,
            expected_revision=first.turn_attempt.revision,
            expected_fencing_generation=0,
        )
    retry = await service.submit_turn(_request())
    async with store.transaction() as repos:
        commands = await repos.commands.list_for_session("s1")
        turn = await repos.turn_attempts.get(first.turn_attempt.turn_attempt_id)
    submits = [c for c in commands if c.command_type == "omnigent.submit_turn"]
    # The retry resolves the same logical command; it does not journal a second
    # provider submission over an ambiguous delivery.
    assert len(submits) == 1
    assert submits[0].delivery_ambiguous is True
    assert retry.command.command_id == first.command.command_id
    assert turn.state == TURN_STATE_DELIVERY_UNKNOWN


@pytest.mark.asyncio
async def test_stale_fencing_generation_is_refused_without_mutation(store) -> None:
    session, _ = await _establish(store)
    async with store.transaction() as repos:
        await repos.sessions.acquire_fencing_generation(
            "s1",
            FencingScope.SESSION_SUPERVISOR,
            expected_revision=session.revision,
        )
    service = CanonicalTurnService(store)
    result = await service.submit_turn(
        _request(expected_fencing_generation=session.fencing_generation)
    )
    assert not result.admitted
    assert result.decision.reason_code == FENCING_GENERATION_SUPERSEDED
    async with store.transaction() as repos:
        turns = await repos.turn_attempts.list_for_session("s1")
    assert [t.turn_attempt_id for t in turns] == ["s1-t0"]


@pytest.mark.asyncio
async def test_cleanup_claim_refuses_a_new_turn_before_provider_mutation(
    store,
) -> None:
    await _establish(store)
    async with store.transaction() as repos:
        claim = await repos.cleanup.claim_cleanup(
            "s1", owner_class="janitor", claim_token="janitor-1"
        )
    assert claim.record.state == CLEANUP_STATE_CLAIMED
    service = CanonicalTurnService(store)
    result = await service.submit_turn(_request())
    assert not result.admitted
    assert result.decision.reason_code == CLEANUP_IN_PROGRESS
    async with store.transaction() as repos:
        turns = await repos.turn_attempts.list_for_session("s1")
        commands = await repos.commands.list_for_session("s1")
    assert [t.turn_attempt_id for t in turns] == ["s1-t0"]
    assert not [c for c in commands if c.command_type == "omnigent.submit_turn"]


@pytest.mark.asyncio
async def test_terminal_turn_leaves_an_active_session_submittable(store) -> None:
    """AC4: a completed turn does not terminalize the session."""

    await _establish(store)
    service = CanonicalTurnService(store)
    first = await service.submit_turn(_request())
    async with store.transaction() as repos:
        await repos.turn_attempts.mark_terminal(
            first.turn_attempt.turn_attempt_id,
            terminal_state="completed",
            expected_revision=first.turn_attempt.revision,
            expected_fencing_generation=0,
        )
        session = await repos.sessions.get("s1")
    assert session.terminal_state is None
    follow_up = await service.submit_turn(
        _request(
            turn_attempt_id="t2",
            idempotency_key="idem-2",
            instruction_ref="artifact://instructions/2",
        )
    )
    assert follow_up.admitted


@pytest.mark.asyncio
async def test_final_session_terminality_makes_the_binding_read_only(store) -> None:
    session, _ = await _establish(store)
    async with store.transaction() as repos:
        await repos.sessions.mark_terminal(
            "s1",
            terminal_state="completed",
            expected_revision=session.revision,
            expected_fencing_generation=session.fencing_generation,
        )
    service = CanonicalTurnService(store)
    result = await service.submit_turn(_request())
    assert not result.admitted
    assert result.decision.reason_code == SESSION_TERMINAL
    assert result.disposition is TurnDisposition.NEW_SESSION_REQUIRED
    # Historical reads survive: the canonical session and its turns stay readable.
    async with store.transaction() as repos:
        turns = await repos.turn_attempts.list_for_session("s1")
        alias = await repos.chat_binding_aliases.resolve("cb-1")
    assert turns
    assert alias.resolves


@pytest.mark.asyncio
async def test_cross_binding_submission_fails_before_mutation(store) -> None:
    await _establish(store)
    await _establish(store, session_id="s2", chat_binding_id="cb-2", actor="user-2")
    service = CanonicalTurnService(store)
    result = await service.submit_turn(
        _request(
            producer="omnigent.workflow_chat.http",
            source=OmnigentTurnSource.WORKFLOW_CHAT,
            actor_id="user-1",
            chat_binding_id="cb-2",
        )
    )
    assert not result.admitted
    assert result.decision.reason_code == CHAT_BINDING_MISMATCH
    async with store.transaction() as repos:
        turns = await repos.turn_attempts.list_for_session("s1")
    assert [t.turn_attempt_id for t in turns] == ["s1-t0"]


@pytest.mark.asyncio
async def test_cross_user_submission_fails_before_mutation(store) -> None:
    await _establish(store)
    service = CanonicalTurnService(store)
    result = await service.submit_turn(
        _request(
            producer="omnigent.workflow_chat.http",
            source=OmnigentTurnSource.WORKFLOW_CHAT,
            actor_id="intruder",
            chat_binding_id="cb-1",
        )
    )
    assert not result.admitted
    assert result.decision.reason_code == ACTOR_NOT_SESSION_OWNER


@pytest.mark.asyncio
async def test_unregistered_producer_cannot_reach_the_boundary(store) -> None:
    await _establish(store)
    service = CanonicalTurnService(store)
    with pytest.raises(TurnProducerNotRegisteredError):
        await service.submit_turn(
            _request(producer="omnigent.workflow_chat.http")
        )
    from moonmind.omnigent.turn_contracts import UnknownTurnProducerError

    with pytest.raises(UnknownTurnProducerError):
        await service.submit_turn(_request(producer="some.rogue.controller"))


@pytest.mark.asyncio
async def test_refused_submission_is_journalled_but_never_dispatched(store) -> None:
    session, _ = await _establish(store)
    dispatched: list[str] = []

    async def dispatcher(result):
        dispatched.append(result.turn_attempt.turn_attempt_id)

    async with store.transaction() as repos:
        await repos.sessions.mark_terminal(
            "s1",
            terminal_state="failed",
            expected_revision=session.revision,
            expected_fencing_generation=session.fencing_generation,
        )
    service = CanonicalTurnService(store, dispatcher=dispatcher)
    result = await service.submit_turn(_request())
    assert not dispatched
    assert result.decision_ref
    async with store.transaction() as repos:
        decisions = await repos.decisions.list_for_session("s1")
    refused = [d for d in decisions if d.reason_code == SESSION_TERMINAL]
    assert refused
    assert refused[0].product_visible_transition == "new_session_required"


@pytest.mark.asyncio
async def test_admitted_submission_dispatches_the_canonical_source(store) -> None:
    await _establish(store)
    payloads: list[dict] = []

    async def dispatcher(result):
        payloads.append(result.dispatch_payload())

    service = CanonicalTurnService(store, dispatcher=dispatcher)
    await service.submit_turn(
        _request(
            producer="omnigent.checkpoint_resume",
            source=OmnigentTurnSource.CHECKPOINT_RESUME,
            checkpoint_ref="artifact://checkpoints/1",
            turn_attempt_id="ckpt-1",
            idempotency_key="ckpt-idem-1",
        )
    )
    assert payloads == [
        {
            "requestId": "ckpt-idem-1",
            "turnAttemptId": "ckpt-1",
            "instructionRef": "artifact://instructions/1",
            "turnSource": "checkpoint_resume",
            "reasonCode": "admitted",
        }
    ]


@pytest.mark.asyncio
async def test_unknown_session_is_told_to_allocate_new_authority(store) -> None:
    service = CanonicalTurnService(store)
    result = await service.submit_turn(_request(session_id="missing"))
    assert not result.admitted
    assert result.disposition is TurnDisposition.NEW_SESSION_REQUIRED
    assert result.decision.reason_code == SESSION_NOT_FOUND
    assert result.turn_attempt is None


# --- Native Workflow Chat routes through the same canonical authority ---------


def _bridge_row(*, chat_binding_id: str = "cb-1"):
    from types import SimpleNamespace

    return SimpleNamespace(chat_binding_id=chat_binding_id)


@pytest.mark.asyncio
async def test_workflow_chat_message_creates_a_canonical_turn(store, monkeypatch) -> None:
    """AC9: a native chat message is a canonical turn, not a bridge-only claim."""

    from api_service.api.routers import omnigent_bridge

    await _establish(store)
    monkeypatch.setattr(omnigent_bridge, "_control_plane_store", lambda: store)
    await omnigent_bridge._admit_canonical_turn(
        row=_bridge_row(),
        event_type="message",
        actor="user-1",
        idempotency_key="chat-key-1",
        payload_digest="f" * 64,
    )
    async with store.transaction() as repos:
        turns = await repos.turn_attempts.list_for_session("s1")
        commands = await repos.commands.list_for_session("s1")
    chat_turns = [t for t in turns if t.turn_source == "workflow_chat"]
    assert len(chat_turns) == 1
    assert chat_turns[0].idempotency_key == "chat-key-1"
    assert chat_turns[0].execution_plan_ref == PLAN_REF
    assert [c.turn_attempt_id for c in commands if c.command_type == "omnigent.submit_turn"] == [
        chat_turns[0].turn_attempt_id
    ]


@pytest.mark.asyncio
async def test_workflow_chat_non_turn_controls_create_no_turn(store, monkeypatch) -> None:
    from api_service.api.routers import omnigent_bridge

    await _establish(store)
    monkeypatch.setattr(omnigent_bridge, "_control_plane_store", lambda: store)
    for event_type in ("interrupt", "stop_session", "harvest_session", "cleanup_session"):
        await omnigent_bridge._admit_canonical_turn(
            row=_bridge_row(),
            event_type=event_type,
            actor="user-1",
            idempotency_key=f"key-{event_type}",
            payload_digest="a" * 64,
        )
    async with store.transaction() as repos:
        turns = await repos.turn_attempts.list_for_session("s1")
    assert [t.turn_attempt_id for t in turns] == ["s1-t0"]


@pytest.mark.asyncio
async def test_workflow_chat_cross_user_submission_is_refused_server_side(
    store, monkeypatch
) -> None:
    from api_service.api.routers import omnigent_bridge
    from moonmind.omnigent.workflow_chat_facade import WorkflowChatFacadeError

    await _establish(store)
    monkeypatch.setattr(omnigent_bridge, "_control_plane_store", lambda: store)
    with pytest.raises(WorkflowChatFacadeError):
        await omnigent_bridge._admit_canonical_turn(
            row=_bridge_row(),
            event_type="message",
            actor="intruder",
            idempotency_key="chat-key-2",
            payload_digest="b" * 64,
        )
    async with store.transaction() as repos:
        turns = await repos.turn_attempts.list_for_session("s1")
    assert [t.turn_attempt_id for t in turns] == ["s1-t0"]


@pytest.mark.asyncio
async def test_pre_canonical_binding_stays_on_the_legacy_path(store, monkeypatch) -> None:
    """A binding with no canonical session is not silently migrated (#3712)."""

    from api_service.api.routers import omnigent_bridge

    monkeypatch.setattr(omnigent_bridge, "_control_plane_store", lambda: store)
    await omnigent_bridge._admit_canonical_turn(
        row=_bridge_row(chat_binding_id="cb-unknown"),
        event_type="message",
        actor="user-1",
        idempotency_key="chat-key-3",
        payload_digest="c" * 64,
    )


# --- Production dispatcher to the durable supervisor --------------------------


@pytest.mark.asyncio
async def test_supervisor_dispatcher_is_the_production_signal_sender(store) -> None:
    """The admitted turn reaches the supervisor as a validated typed signal."""

    from moonmind.omnigent.supervisor_turn_dispatch import (
        SUBMIT_TURN_SIGNAL,
        SupervisorTurnDispatcher,
    )

    sent: list[tuple[str, str, dict]] = []

    class _Client:
        async def signal_workflow(self, workflow_id, signal_name, arg=None):
            sent.append((workflow_id, signal_name, arg))

    await _establish(store)
    service = CanonicalTurnService(store, dispatcher=SupervisorTurnDispatcher(_Client()))
    await service.submit_turn(_request(instruction_ref="artifact://instructions/9"))
    assert len(sent) == 1
    workflow_id, signal_name, arg = sent[0]
    assert workflow_id == "omnigent-session:s1"
    assert signal_name == SUBMIT_TURN_SIGNAL
    assert arg["turnSource"] == "repository_continuation"
    assert arg["turnAttemptId"] == "t1"
    assert arg["instructionRef"] == "artifact://instructions/9"


@pytest.mark.asyncio
async def test_supervisor_dispatcher_refuses_to_deliver_a_refused_turn(store) -> None:
    from moonmind.omnigent.control_plane import CanonicalTurnServiceError
    from moonmind.omnigent.supervisor_turn_dispatch import SupervisorTurnDispatcher
    from moonmind.omnigent.turn_contracts import (
        ImmutableExecutionAuthority as _Authority,
    )
    from moonmind.omnigent.turn_contracts import (
        TurnAdmissionDecision,
    )

    class _Client:
        async def signal_workflow(self, workflow_id, signal_name, arg=None):
            raise AssertionError("a refused turn must never be dispatched")

    from moonmind.omnigent.control_plane import CanonicalTurnResult

    refused = CanonicalTurnResult(
        decision=TurnAdmissionDecision(
            admitted=False,
            disposition=TurnDisposition.BRANCH_REQUIRED,
            reasonCode=IMMUTABLE_AUTHORITY_CHANGED,
            source=OmnigentTurnSource.REMEDIATION,
            producer="omnigent.remediation_controller",
            sessionId="s1",
            authorityDigest=_Authority().authority_digest,
        )
    )
    with pytest.raises(CanonicalTurnServiceError):
        await SupervisorTurnDispatcher(_Client())(refused)


@pytest.mark.asyncio
async def test_workflow_chat_replay_and_key_reuse_behave_distinctly(
    store, monkeypatch
) -> None:
    """A retry is one turn; a key reused for changed content is a typed conflict."""

    from types import SimpleNamespace

    from api_service.api.routers import omnigent_bridge
    from moonmind.omnigent.workflow_chat_facade import (
        CODE_IDEMPOTENCY_CONFLICT,
        WorkflowChatFacadeError,
    )

    await _establish(store)
    monkeypatch.setattr(omnigent_bridge, "_control_plane_store", lambda: store)
    row = SimpleNamespace(chat_binding_id="cb-1", bridge_session_id="brs-1")
    for _ in range(2):
        await omnigent_bridge._admit_canonical_turn(
            row=row,
            event_type="message",
            actor="user-1",
            idempotency_key="dup-key",
            payload_digest="d" * 64,
        )
    async with store.transaction() as repos:
        turns = await repos.turn_attempts.list_for_session("s1")
    assert len([t for t in turns if t.turn_source == "workflow_chat"]) == 1

    # A *different* logical turn under the same key is refused with the same
    # typed idempotency conflict the bridge journal reports.
    other_row = SimpleNamespace(chat_binding_id="cb-1", bridge_session_id="brs-other")
    with pytest.raises(WorkflowChatFacadeError) as excinfo:
        await omnigent_bridge._admit_canonical_turn(
            row=other_row,
            event_type="message",
            actor="user-1",
            idempotency_key="dup-key",
            payload_digest="e" * 64,
        )
    assert excinfo.value.code == CODE_IDEMPOTENCY_CONFLICT


def test_only_turn_submitting_composer_events_map_to_a_source() -> None:
    """AC9: lifecycle controls are not turns; route/frame names are not events."""

    from moonmind.omnigent.workflow_chat_facade import (
        canonical_turn_source_for_event,
    )

    assert canonical_turn_source_for_event("message") is (
        OmnigentTurnSource.WORKFLOW_CHAT
    )
    assert canonical_turn_source_for_event("user.message") is (
        OmnigentTurnSource.WORKFLOW_CHAT
    )
    assert canonical_turn_source_for_event("steering") is OmnigentTurnSource.STEERING
    assert canonical_turn_source_for_event("resolve_elicitation") is (
        OmnigentTurnSource.APPROVAL_RESPONSE
    )
    for non_turn in (
        "interrupt",
        "stop",
        "session.stop",
        "stop_session",
        "clear_session",
        "reset_session",
        "harvest_session",
        "cleanup_session",
        "terminal_cleanup",
        # Route and WebSocket-frame names are not composer event types.
        "post_event",
        "stream_events_websocket_frame",
        "terminal_attach_frame",
        "",
        None,
    ):
        assert canonical_turn_source_for_event(non_turn) is None, non_turn
