"""Unit tests for the canonical session/turn ownership contract.

Source: MoonLadderStudios/MoonMind#3707 ([Omnigent control plane 6/11]
Unify continuations, remediation, checkpoints, and chat under canonical session
and turn ownership).

These exercise the pure-domain contract in
``moonmind.omnigent.control_plane.turn_contract``: the typed command path and its
eight source kinds, same-session reuse vs branch-required, remediation authority,
the one recovery decision boundary, chat capability derivation, and cleanup
fencing. The repository-backed executor is covered in ``test_turn_service.py``.
"""

from __future__ import annotations

import pytest

from moonmind.omnigent.control_plane import (
    CANONICAL_TERMINAL_SESSION_STATES,
    LIFECYCLE_OWNERSHIP_MATRIX,
    BranchRequiredError,
    CallerAuthorityError,
    ChatBindingAliasRecord,
    ChatCapability,
    CleanupDisposition,
    CleanupFenceError,
    CleanupOperation,
    ImmutableSessionDimensions,
    Lifecycle,
    LifecycleActivity,
    RecoveryEvidence,
    RecoveryMode,
    RemediationAuthorityError,
    RemediationTurnIntent,
    SessionRecord,
    SessionTerminalError,
    TurnAttemptRecord,
    TurnCommandStep,
    TurnSourceKind,
    admit_continuation,
    decide_recovery,
    derive_chat_capability,
    evaluate_cleanup_admission,
    idempotency_scope_for,
    plan_turn_submission,
    validate_remediation_authority,
)
from moonmind.omnigent.control_plane.records import ALIAS_STATE_ACTIVE


def _session(
    *,
    session_id: str = "sess-1",
    terminal_state: str | None = None,
    cleanup_state: str = "pending",
    historical_read_state: str = "live",
    metadata: dict | None = None,
    **kwargs,
) -> SessionRecord:
    return SessionRecord(
        session_id=session_id,
        moonmind_workflow_id="wf-1",
        provider=kwargs.pop("provider", "codex"),
        chat_binding_id="chat-1",
        terminal_state=terminal_state,
        cleanup_state=cleanup_state,
        historical_read_state=historical_read_state,
        metadata=metadata or {},
        **kwargs,
    )


def _request(source_kind: TurnSourceKind, **kwargs):
    from moonmind.omnigent.control_plane import TurnSubmissionRequest

    instruction_digest = kwargs.pop("instruction_digest", "digest-abc")
    return TurnSubmissionRequest(
        session_id=kwargs.pop("session_id", "sess-1"),
        source_kind=source_kind,
        caller_id=kwargs.pop("caller_id", "caller-1"),
        idempotency_key=kwargs.pop(
            "idempotency_key", f"idem-{source_kind.value}-{instruction_digest}"
        ),
        instruction_digest=instruction_digest,
        **kwargs,
    )


# --- Lifecycle ownership matrix ---------------------------------------------


def test_lifecycle_ownership_matrix_is_distinct_and_complete():
    lifecycles = [entry.lifecycle for entry in LIFECYCLE_OWNERSHIP_MATRIX]
    # Every lifecycle appears exactly once; no two share an owner+meaning row.
    assert len(lifecycles) == len(set(lifecycles))
    assert set(lifecycles) == set(Lifecycle)
    # Session vs turn-attempt terminal meanings must not be conflated.
    by_lifecycle = {e.lifecycle: e for e in LIFECYCLE_OWNERSHIP_MATRIX}
    assert (
        by_lifecycle[Lifecycle.OMNIGENT_SESSION].terminal_meaning
        != by_lifecycle[Lifecycle.TURN_ATTEMPT].terminal_meaning
    )


# --- Turn submission plan ----------------------------------------------------


@pytest.mark.parametrize(
    "kind",
    [
        TurnSourceKind.REPOSITORY_CONTINUATION,
        TurnSourceKind.REMEDIATION,
        TurnSourceKind.WORKFLOW_CHAT,
        TurnSourceKind.STEERING,
        TurnSourceKind.APPROVAL_RESPONSE,
        TurnSourceKind.CHECKPOINT_RESUME,
    ],
)
def test_reuse_kinds_reuse_session_and_binding(kind):
    plan = plan_turn_submission(_request(kind), _session())
    assert plan.reuses_session is True
    assert plan.reuses_chat_binding is True
    assert plan.allocates_new_session is False
    # The typed command path always begins with create+validate and ends with
    # recording terminal attempt evidence, regardless of source kind.
    assert plan.steps[0] is TurnCommandStep.CREATE_TURN_ATTEMPT
    assert plan.steps[1] is TurnCommandStep.VALIDATE_AUTHORITY
    assert plan.steps[-1] is TurnCommandStep.RECORD_TERMINAL_EVIDENCE


@pytest.mark.parametrize(
    "kind", [TurnSourceKind.INITIAL, TurnSourceKind.LINKED_BRANCH]
)
def test_allocating_kinds_do_not_reuse(kind):
    plan = plan_turn_submission(_request(kind), None)
    assert plan.allocates_new_session is True
    assert plan.reuses_session is False
    assert plan.reuses_chat_binding is False


def test_approval_response_skips_outbound_scan_others_require_it():
    approval = plan_turn_submission(_request(TurnSourceKind.APPROVAL_RESPONSE), _session())
    assert approval.requires_outbound_scan is False
    assert TurnCommandStep.OUTBOUND_SECURITY_SCAN not in approval.steps

    chat = plan_turn_submission(_request(TurnSourceKind.WORKFLOW_CHAT), _session())
    assert chat.requires_outbound_scan is True
    assert TurnCommandStep.OUTBOUND_SECURITY_SCAN in chat.steps


def test_reuse_turn_requires_existing_session():
    with pytest.raises(CallerAuthorityError):
        plan_turn_submission(_request(TurnSourceKind.WORKFLOW_CHAT), None)


def test_changed_immutable_dimension_requires_branch():
    session = _session(metadata={"repository": "repoA"})
    request = _request(
        TurnSourceKind.REPOSITORY_CONTINUATION,
        requested_dimensions=ImmutableSessionDimensions(repository="repoB"),
    )
    with pytest.raises(BranchRequiredError) as excinfo:
        plan_turn_submission(request, session)
    assert "repository" in excinfo.value.changed_dimensions


@pytest.mark.parametrize(
    "dimension",
    [
        "provider",
        "runtime_id",
        "model",
        "effort",
        "compatibility_profile",
        "provider_profile_id",
        "policy_ref",
        "image_manifest_ref",
        "compatibility_ref",
        "repository",
        "branch",
        "workspace_ref",
        "publication_mode",
        "skill_ref",
        "runtime_authority_ref",
        "instruction_digest",
        "intent_digest",
    ],
)
def test_every_concrete_immutable_dimension_requires_a_branch(dimension):
    column_dimensions = {
        "provider",
        "compatibility_profile",
        "provider_profile_id",
        "image_manifest_ref",
        "compatibility_ref",
        "intent_digest",
    }
    session = _session(
        **(
            {dimension: "authority-a"}
            if dimension in column_dimensions
            else {"metadata": {dimension: "authority-a"}}
        )
    )
    request = _request(
        TurnSourceKind.REPOSITORY_CONTINUATION,
        requested_dimensions=ImmutableSessionDimensions(
            **{dimension: "authority-b"}
        ),
    )

    with pytest.raises(BranchRequiredError) as excinfo:
        plan_turn_submission(request, session)

    assert excinfo.value.changed_dimensions == (dimension,)


def test_unknown_dimension_does_not_force_branch():
    session = _session(metadata={"repository": "repoA"})
    # A request that leaves repository unknown (None) must not spuriously branch.
    request = _request(
        TurnSourceKind.REPOSITORY_CONTINUATION,
        requested_dimensions=ImmutableSessionDimensions(model="gpt-x"),
    )
    plan = plan_turn_submission(request, session)
    assert plan.reuses_session is True


@pytest.mark.parametrize("cleanup_state", ["complete", "released"])
def test_reuse_after_terminal_cleanup_is_rejected(cleanup_state):
    session = _session(cleanup_state=cleanup_state)
    with pytest.raises(SessionTerminalError):
        plan_turn_submission(_request(TurnSourceKind.REPOSITORY_CONTINUATION), session)


def test_reuse_of_terminal_session_is_rejected():
    session = _session(terminal_state="completed")
    with pytest.raises(SessionTerminalError):
        plan_turn_submission(_request(TurnSourceKind.WORKFLOW_CHAT), session)


def test_idempotency_scope_collapses_redelivery_but_separates_loops():
    a = idempotency_scope_for(_request(TurnSourceKind.REPOSITORY_CONTINUATION))
    b = idempotency_scope_for(_request(TurnSourceKind.REPOSITORY_CONTINUATION))
    assert a == b  # identical logical work collapses (no duplicate continuation)

    c = idempotency_scope_for(
        _request(TurnSourceKind.WORKFLOW_CHAT, instruction_digest="digest-other")
    )
    assert c != a


def test_turn_submission_requires_nonempty_caller_authority():
    with pytest.raises(CallerAuthorityError):
        plan_turn_submission(
            _request(TurnSourceKind.REPOSITORY_CONTINUATION, caller_id=""),
            _session(),
        )


def test_remediation_exact_refs_participate_in_idempotency_scope():
    first = _request(
        TurnSourceKind.REMEDIATION,
        remediation=_remediation_intent(remaining_work_ref="art://remaining-a"),
    )
    changed = _request(
        TurnSourceKind.REMEDIATION,
        remediation=_remediation_intent(remaining_work_ref="art://remaining-b"),
    )
    assert idempotency_scope_for(first) != idempotency_scope_for(changed)


# --- Remediation authority ---------------------------------------------------


def _remediation_intent(**kwargs) -> RemediationTurnIntent:
    base = dict(
        loop_id="loop-1",
        remediation_attempt_ordinal=1,
        of_turn_attempt_id="turn-0",
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


def test_remediation_authority_accepts_valid_intent():
    validate_remediation_authority(
        base_dimensions=ImmutableSessionDimensions(
            provider_profile_id="prof-1", workspace_ref="ws-1"
        ),
        intent=_remediation_intent(),
    )


def test_remediation_rejects_nonpositive_budget():
    with pytest.raises(RemediationAuthorityError):
        validate_remediation_authority(
            base_dimensions=ImmutableSessionDimensions(),
            intent=_remediation_intent(attempt_budget=0),
        )


def test_remediation_rejects_exhausted_attempt_budget():
    with pytest.raises(RemediationAuthorityError, match="exceeds attempt budget"):
        validate_remediation_authority(
            base_dimensions=ImmutableSessionDimensions(),
            intent=_remediation_intent(
                remediation_attempt_ordinal=4,
                attempt_budget=3,
            ),
        )


def test_remediation_rejects_missing_evidence():
    with pytest.raises(RemediationAuthorityError):
        validate_remediation_authority(
            base_dimensions=ImmutableSessionDimensions(),
            intent=_remediation_intent(gate_result_ref=""),
        )


def test_remediation_cannot_broaden_profile_or_workspace():
    with pytest.raises(RemediationAuthorityError):
        validate_remediation_authority(
            base_dimensions=ImmutableSessionDimensions(provider_profile_id="prof-1"),
            intent=_remediation_intent(
                granted_dimensions=ImmutableSessionDimensions(
                    provider_profile_id="prof-2"
                )
            ),
        )


@pytest.mark.parametrize(
    "dimension",
    [
        "provider_profile_id",
        "workspace_ref",
        "repository",
        "branch",
        "publication_mode",
        "runtime_id",
        "model",
        "effort",
        "policy_ref",
        "image_manifest_ref",
        "compatibility_ref",
        "skill_ref",
        "runtime_authority_ref",
    ],
)
def test_remediation_fails_closed_when_required_base_authority_is_absent(
    dimension,
):
    with pytest.raises(RemediationAuthorityError, match="missing base authority"):
        validate_remediation_authority(
            base_dimensions=ImmutableSessionDimensions(),
            intent=_remediation_intent(
                granted_dimensions=ImmutableSessionDimensions(
                    **{dimension: "new-authority"}
                )
            ),
        )


def test_remediation_cannot_grant_publication_authority():
    with pytest.raises(RemediationAuthorityError):
        validate_remediation_authority(
            base_dimensions=ImmutableSessionDimensions(),
            intent=_remediation_intent(grants_publication_authority=True),
            base_grants_publication_authority=False,
        )


# --- Recovery decision boundary ----------------------------------------------


def _full_live_evidence(**kwargs) -> RecoveryEvidence:
    dims = ImmutableSessionDimensions(repository="repoA")
    base = dict(
        intent_dimensions=dims,
        session_dimensions=dims,
        provider_profile_lease_current=True,
        host_available=True,
        provider_session_reachable=True,
        cursor_present=True,
        first_message_consistent=True,
        credential_generation_current=True,
        workspace_artifact_valid=True,
        session_evidence_valid=True,
    )
    base.update(kwargs)
    return RecoveryEvidence(**base)


def test_recovery_live_reattach_with_complete_authority():
    assert decide_recovery(_full_live_evidence()).mode is RecoveryMode.LIVE_REATTACH


def test_recovery_cold_restore_when_live_authority_incomplete():
    evidence = _full_live_evidence(host_available=False, provider_session_reachable=False)
    assert decide_recovery(evidence).mode is RecoveryMode.COLD_RESTORE


@pytest.mark.parametrize(
    "missing_live_evidence",
    [
        "provider_profile_lease_current",
        "host_available",
        "provider_session_reachable",
        "cursor_present",
        "first_message_consistent",
        "credential_generation_current",
    ],
)
def test_recovery_rejects_live_reattach_when_authority_is_stale_or_missing(
    missing_live_evidence,
):
    evidence = _full_live_evidence(**{missing_live_evidence: False})
    assert decide_recovery(evidence).mode is RecoveryMode.COLD_RESTORE


def test_recovery_branch_required_on_immutable_change_takes_precedence():
    # Even with full live authority, a changed immutable input forces a branch.
    evidence = _full_live_evidence(
        intent_dimensions=ImmutableSessionDimensions(repository="repoB"),
        session_dimensions=ImmutableSessionDimensions(repository="repoA"),
    )
    decision = decide_recovery(evidence)
    assert decision.mode is RecoveryMode.BRANCH_REQUIRED
    assert "repository" in decision.changed_dimensions


def test_recovery_is_unavailable_when_canonical_immutable_authority_is_missing():
    decision = decide_recovery(
        _full_live_evidence(
            intent_dimensions=ImmutableSessionDimensions(
                runtime_id="omnigent", repository="repoA"
            ),
            session_dimensions=ImmutableSessionDimensions(repository="repoA"),
        )
    )
    assert decision.mode is RecoveryMode.RESUME_UNAVAILABLE
    assert decision.reason == "immutable_session_authority_missing"
    assert decision.changed_dimensions == ("runtime_id",)


def test_recovery_resume_unavailable_without_evidence():
    evidence = _full_live_evidence(
        host_available=False,
        provider_session_reachable=False,
        workspace_artifact_valid=False,
        session_evidence_valid=False,
    )
    assert decide_recovery(evidence).mode is RecoveryMode.RESUME_UNAVAILABLE


def test_recovery_is_idempotent_for_identical_evidence():
    evidence = _full_live_evidence()
    assert decide_recovery(evidence) == decide_recovery(evidence)


# --- Chat capability derivation ---------------------------------------------


def _alias(session_id: str | None = "sess-1") -> ChatBindingAliasRecord:
    return ChatBindingAliasRecord(
        chat_binding_id="chat-1",
        session_id=session_id,
        alias_state=ALIAS_STATE_ACTIVE,
    )


def test_chat_active_session_is_read_write():
    decision = derive_chat_capability(
        alias=_alias(), session=_session(), caller_authorized=True
    )
    assert decision.capability is ChatCapability.READ_WRITE
    assert decision.read_only is False


def test_chat_terminal_session_is_read_only():
    decision = derive_chat_capability(
        alias=_alias(),
        session=_session(terminal_state="completed"),
        caller_authorized=True,
    )
    assert decision.capability is ChatCapability.READ_ONLY
    assert decision.read_only is True
    assert decision.historical_read_available is True


def test_chat_terminal_attempt_does_not_downgrade_active_session():
    # An attempt row can never supersede chat authority by being newer/terminal.
    terminal_attempt = TurnAttemptRecord(
        turn_attempt_id="turn-9",
        session_id="sess-1",
        idempotency_key="k",
        terminal_state="completed",
        state="terminal",
    )
    decision = derive_chat_capability(
        alias=_alias(),
        session=_session(),
        caller_authorized=True,
        active_turn=terminal_attempt,
    )
    assert decision.capability is ChatCapability.READ_WRITE


def test_chat_unresolved_binding_is_unavailable():
    decision = derive_chat_capability(
        alias=ChatBindingAliasRecord(chat_binding_id="chat-1", session_id=None),
        session=None,
        caller_authorized=True,
    )
    assert decision.capability is ChatCapability.UNAVAILABLE


def test_chat_unauthorized_caller_is_unavailable_but_history_survives():
    decision = derive_chat_capability(
        alias=_alias(), session=_session(), caller_authorized=False
    )
    assert decision.capability is ChatCapability.UNAVAILABLE
    assert decision.historical_read_available is True


def test_chat_history_survives_cleanup():
    decision = derive_chat_capability(
        alias=_alias(),
        session=_session(cleanup_state="released", historical_read_state="archived"),
        caller_authorized=True,
    )
    assert decision.capability is ChatCapability.READ_ONLY
    assert decision.historical_read_available is True


def test_canonical_terminal_states_flip_binding_read_only():
    for state in CANONICAL_TERMINAL_SESSION_STATES:
        decision = derive_chat_capability(
            alias=_alias(),
            session=_session(terminal_state=state),
            caller_authorized=True,
        )
        assert decision.capability is ChatCapability.READ_ONLY


# --- Cleanup coordination / fencing -----------------------------------------


@pytest.mark.parametrize(
    "operation",
    [
        CleanupOperation.SESSION_STOP,
        CleanupOperation.HOST_CLEANUP,
        CleanupOperation.PROVIDER_PROFILE_RELEASE,
        CleanupOperation.JANITOR_RECOVERY,
    ],
)
def test_cleanup_is_fenced_while_work_is_active(operation):
    decision = evaluate_cleanup_admission(
        operation=operation, activity=LifecycleActivity(active_turn=True)
    )
    assert decision.disposition is CleanupDisposition.FENCE


def test_cleanup_admitted_when_idle():
    decision = evaluate_cleanup_admission(
        operation=CleanupOperation.SESSION_STOP, activity=LifecycleActivity()
    )
    assert decision.disposition is CleanupDisposition.ADMIT


def test_repository_publication_is_never_fenced_as_turn_effect():
    decision = evaluate_cleanup_admission(
        operation=CleanupOperation.REPOSITORY_PUBLICATION,
        activity=LifecycleActivity(active_turn=True),
    )
    assert decision.disposition is CleanupDisposition.ADMIT


def test_admit_continuation_rejects_terminal_cleanup():
    with pytest.raises(CleanupFenceError):
        admit_continuation(
            session=_session(cleanup_state="complete"), activity=LifecycleActivity()
        )


def test_admit_continuation_allows_live_session():
    admit_continuation(session=_session(), activity=LifecycleActivity(active_turn=True))
