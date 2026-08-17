"""Contract tests for canonical session/turn ownership (MoonLadderStudios/MoonMind#3707)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from moonmind.omnigent.checkpoints import (
    IMMUTABLE_RECOVERY_DIMENSIONS,
    OmnigentRecoveryMode,
)
from moonmind.omnigent.session_turns import (
    LIFECYCLE_OWNERSHIP,
    CanonicalSessionRef,
    ContinuationDecision,
    OmnigentLifecycle,
    RemediationTurnIntent,
    TurnDeliveryState,
    TurnSourceKind,
    TurnSubmission,
    build_continuation_turn,
    decide_continuation,
    session_is_terminal,
)

_DIGEST = "sha256:" + "a" * 64


def _dimensions(**overrides: str) -> dict[str, str]:
    base = {
        "instructionDigest": "sha256:instructions",
        "runtimeId": "omnigent",
        "model": "default",
        "effort": "medium",
        "providerProfileId": "profile-1",
        "launchPolicyRef": "artifact://policy/1",
        "repositoryBranch": "main",
        "publishMode": "none",
    }
    base.update(overrides)
    return base


def _session(**overrides) -> CanonicalSessionRef:
    kwargs = {
        "canonicalSessionId": "sess-1",
        "chatBindingId": "chat-binding-1",
        "providerProfileId": "profile-1",
        "immutableDimensions": _dimensions(),
    }
    kwargs.update(overrides)
    return CanonicalSessionRef(**kwargs)


# --------------------------------------------------------------------------- #
# Source kinds and lifecycle matrix
# --------------------------------------------------------------------------- #


def test_turn_source_kinds_are_the_eight_typed_origins() -> None:
    assert {kind.value for kind in TurnSourceKind} == {
        "initial",
        "repository_continuation",
        "remediation",
        "workflow_chat",
        "steering",
        "approval_response",
        "checkpoint_resume",
        "linked_branch",
    }


def test_lifecycle_matrix_gives_each_lifecycle_one_owner_and_meaning() -> None:
    assert set(LIFECYCLE_OWNERSHIP) == set(OmnigentLifecycle)
    for lifecycle, ownership in LIFECYCLE_OWNERSHIP.items():
        assert ownership.owner
        assert ownership.terminal_meaning
    assert (
        LIFECYCLE_OWNERSHIP[OmnigentLifecycle.OMNIGENT_SESSION].owner
        == "MoonMind.OmnigentSession"
    )
    assert (
        LIFECYCLE_OWNERSHIP[OmnigentLifecycle.TURN_ATTEMPT].owner == "Session workflow"
    )


@pytest.mark.parametrize("attempt_terminal", [True, False])
def test_attempt_terminality_never_implies_session_terminality(
    attempt_terminal: bool,
) -> None:
    # A terminal attempt with no authoritative session evidence must not
    # terminalize the session.
    assert (
        session_is_terminal(
            attempt_terminal=attempt_terminal,
            session_policy_terminal=False,
            authoritative_session_evidence=True,
        )
        is False
    )
    # Even a terminal attempt cannot flip the session terminal on its own; both
    # session policy and authoritative session evidence are required.
    assert (
        session_is_terminal(
            attempt_terminal=attempt_terminal,
            session_policy_terminal=True,
            authoritative_session_evidence=False,
        )
        is False
    )
    assert (
        session_is_terminal(
            attempt_terminal=attempt_terminal,
            session_policy_terminal=True,
            authoritative_session_evidence=True,
        )
        is True
    )


# --------------------------------------------------------------------------- #
# Turn submission identity
# --------------------------------------------------------------------------- #


def test_turn_attempt_identity_is_distinct_from_the_session() -> None:
    turn = TurnSubmission(
        canonicalSessionId="sess-1",
        chatBindingId="chat-binding-1",
        turnAttemptId="turn-1",
        sourceKind=TurnSourceKind.WORKFLOW_CHAT,
        idempotencyKey="idem-1",
        instructionDigest=_DIGEST,
    )
    assert turn.delivery_state is TurnDeliveryState.PENDING
    assert turn.attempt_terminal is False
    with pytest.raises(ValidationError):
        TurnSubmission(
            canonicalSessionId="sess-1",
            turnAttemptId="sess-1",
            sourceKind=TurnSourceKind.WORKFLOW_CHAT,
            idempotencyKey="idem-1",
            instructionDigest=_DIGEST,
        )


def test_canonical_session_requires_complete_immutable_dimensions() -> None:
    incomplete = _dimensions()
    incomplete.pop("model")
    with pytest.raises(ValidationError):
        CanonicalSessionRef(
            canonicalSessionId="sess-1",
            providerProfileId="profile-1",
            immutableDimensions=incomplete,
        )
    with pytest.raises(ValidationError):
        CanonicalSessionRef(
            canonicalSessionId="sess-1",
            providerProfileId="profile-2",  # mismatched vs dimension snapshot
            immutableDimensions=_dimensions(),
        )


# --------------------------------------------------------------------------- #
# Same-session continuation
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("continuation_count", [0, 1, 3, 7])
def test_many_same_session_continuations_preserve_one_session_and_binding(
    continuation_count: int,
) -> None:
    """Zero..many continuations (incl. #3685's 7-attempt shape) never churn the binding."""

    session = _session()
    prior_turn: str | None = None
    prior_key: str | None = None
    for index in range(continuation_count):
        outcome = decide_continuation(session.immutable_dimensions, _dimensions())
        assert outcome.decision is ContinuationDecision.ACCEPT_SAME_SESSION
        turn = build_continuation_turn(
            session,
            source_kind=TurnSourceKind.REPOSITORY_CONTINUATION,
            turn_attempt_id=f"turn-{index}",
            idempotency_key=f"idem-{index}",
            instruction_digest=_DIGEST,
            prior_turn_attempt_id=prior_turn,
            prior_idempotency_key=prior_key,
        )
        # One canonical session, one chat binding across every continuation.
        assert turn.canonical_session_id == "sess-1"
        assert turn.chat_binding_id == "chat-binding-1"
        assert turn.source_kind is TurnSourceKind.REPOSITORY_CONTINUATION
        # New attempt identity every time.
        assert turn.turn_attempt_id == f"turn-{index}"
        assert turn.idempotency_key == f"idem-{index}"
        prior_turn, prior_key = turn.turn_attempt_id, turn.idempotency_key


@pytest.mark.parametrize("dimension", list(IMMUTABLE_RECOVERY_DIMENSIONS))
def test_changed_immutable_dimension_requires_branch(dimension: str) -> None:
    current = _dimensions()
    requested = _dimensions(**{dimension: "changed-value"})
    outcome = decide_continuation(current, requested)
    assert outcome.decision is ContinuationDecision.BRANCH_REQUIRED
    assert outcome.changed_dimensions == [dimension]
    assert outcome.reason_codes == [f"immutable_{dimension}_changed"]
    # The continuation vocabulary matches the recovery boundary.
    assert (
        ContinuationDecision.BRANCH_REQUIRED.value
        == OmnigentRecoveryMode.BRANCH_REQUIRED.value
    )


def test_continuation_rejects_new_session_source_kinds() -> None:
    session = _session()
    for kind in (TurnSourceKind.INITIAL, TurnSourceKind.LINKED_BRANCH):
        with pytest.raises(ValueError, match="does not continue"):
            build_continuation_turn(
                session,
                source_kind=kind,
                turn_attempt_id="turn-x",
                idempotency_key="idem-x",
                instruction_digest=_DIGEST,
            )


def test_continuation_after_terminal_session_requires_branch_or_new_session() -> None:
    session = _session(sessionTerminal=True)
    with pytest.raises(ValueError, match="terminal canonical session"):
        build_continuation_turn(
            session,
            source_kind=TurnSourceKind.WORKFLOW_CHAT,
            turn_attempt_id="turn-1",
            idempotency_key="idem-1",
            instruction_digest=_DIGEST,
        )


def test_continuation_rejects_reused_attempt_or_idempotency() -> None:
    session = _session()
    with pytest.raises(ValueError, match="new turn-attempt id"):
        build_continuation_turn(
            session,
            source_kind=TurnSourceKind.STEERING,
            turn_attempt_id="turn-1",
            idempotency_key="idem-2",
            instruction_digest=_DIGEST,
            prior_turn_attempt_id="turn-1",
        )
    with pytest.raises(ValueError, match="new idempotency key"):
        build_continuation_turn(
            session,
            source_kind=TurnSourceKind.STEERING,
            turn_attempt_id="turn-2",
            idempotency_key="idem-1",
            instruction_digest=_DIGEST,
            prior_idempotency_key="idem-1",
        )


# --------------------------------------------------------------------------- #
# Remediation turn intent
# --------------------------------------------------------------------------- #


def _remediation_intent(**overrides) -> RemediationTurnIntent:
    kwargs = {
        "loopId": "loop-1",
        "attemptOrdinal": 1,
        "gateResultRef": "artifact://gate/1",
        "remainingWorkRef": "artifact://remaining/1",
        "candidateWorkspaceRef": "artifact://workspace/1",
        "checkpointRef": "artifact://checkpoint/1",
        "remediatorSkill": "pr-resolver",
        "runtimeAuthorityRef": "ref://runtime/1",
        "verificationRequirements": ["targeted_tests"],
        "attemptBudget": 3,
        "branchBudget": 1,
        "sameSessionReuseAllowed": True,
        "productionBoundaryEvidenceRef": "artifact://boundary/1",
    }
    kwargs.update(overrides)
    return RemediationTurnIntent(**kwargs)


def test_remediation_intent_compiles_to_a_same_session_remediation_turn() -> None:
    session = _session()
    turn = _remediation_intent().into_turn_submission(
        session,
        turn_attempt_id="turn-rem-1",
        idempotency_key="idem-rem-1",
        instruction_digest=_DIGEST,
    )
    assert turn.source_kind is TurnSourceKind.REMEDIATION
    assert turn.canonical_session_id == "sess-1"
    assert turn.chat_binding_id == "chat-binding-1"


def test_remediation_intent_rejects_non_durable_refs() -> None:
    with pytest.raises(ValidationError):
        _remediation_intent(gateResultRef="/tmp/gate.json")
    with pytest.raises(ValidationError):
        _remediation_intent(runtimeAuthorityRef="token=abc123")


def test_remediator_cannot_broaden_profile_workspace_or_publication_authority() -> None:
    session = _session()
    with pytest.raises(ValueError, match="Provider Profile"):
        _remediation_intent(
            requestedProviderProfileId="profile-2"
        ).assert_within_session_authority(session)
    with pytest.raises(ValueError, match="publication authority"):
        _remediation_intent(
            requestedPublishMode="pull_request"
        ).assert_within_session_authority(session)
    with pytest.raises(ValidationError):
        _remediation_intent(requestedWorkspaceRef="/etc/passwd")


def test_remediation_intent_forbidding_reuse_requires_branch() -> None:
    session = _session()
    with pytest.raises(ValueError, match="branch is required"):
        _remediation_intent(sameSessionReuseAllowed=False).into_turn_submission(
            session,
            turn_attempt_id="turn-rem-1",
            idempotency_key="idem-rem-1",
            instruction_digest=_DIGEST,
        )
