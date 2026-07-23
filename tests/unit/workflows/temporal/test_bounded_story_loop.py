from __future__ import annotations

import pytest
from pydantic import ValidationError

from moonmind.workflows.temporal.bounded_story_loop import (
    BoundedStoryLoopInput,
    CandidateWorkspaceHead,
    CompiledBoundedStoryLoop,
    LoopAttempt,
    LoopBudget,
    LoopStopDecision,
    LoopStopState,
    PreflightDecision,
    PublicationAction,
    PublicationDecision,
    PublicationFeasibility,
    ProviderLeaseDecision,
    RemainingWorkArtifact,
    TypedGateResult,
    VerificationWorkspaceSnapshot,
    advance_candidate_workspace_head,
    compile_bounded_story_loop,
    evaluate_attempt_continuation,
    evaluate_attempt_preflight,
    evaluate_provider_lease,
    evaluate_publication_decision,
    validate_verification_workspace_integrity,
)


def _budget(**overrides: object) -> LoopBudget:
    payload = {
        "maxAttempts": 3,
        "maxConsecutiveNoProgressAttempts": 2,
        "maxRepeatedFailedCommands": 2,
        "maxUnsafeOrPolicyDeniedAttempts": 0,
    }
    payload.update(overrides)
    return LoopBudget.model_validate(payload)


def _gate(**overrides: object) -> TypedGateResult:
    payload = {
        "verdict": "ADDITIONAL_WORK_NEEDED",
        "terminalDisposition": "failed_with_remaining_work",
        "gateResultRef": "artifact://gate/attempt-1",
        "remainingWorkRef": "artifact://remaining-work/attempt-1",
        "progressSignature": "sig-1",
    }
    payload.update(overrides)
    return TypedGateResult.model_validate(payload)


def _attempt(**overrides: object) -> LoopAttempt:
    payload = {
        "attemptOrdinal": 1,
        "kind": "implementation",
        "stepExecutionId": "workflow:run:implement-story:execution:1",
        "checkpointBeforeRef": "artifact://checkpoint/before",
        "checkpointAfterRef": "artifact://checkpoint/after",
        "candidateDiffRef": "artifact://diff/candidate",
        "gateResultRef": "artifact://gate/attempt-1",
        "terminalDisposition": "failed_with_remaining_work",
    }
    payload.update(overrides)
    return LoopAttempt.model_validate(payload)


def test_selected_item_validation_and_compilation_are_single_item_only() -> None:
    loop_input = BoundedStoryLoopInput.model_validate(
        {
            "selectedItemRef": "artifact://story/item-1",
            "selectedItemDigest": "sha256:item-1",
            "publishMode": "pr",
            "mergeAutomationEnabled": True,
            "budgets": _budget().model_dump(by_alias=True),
        }
    )

    compiled = compile_bounded_story_loop(loop_input)

    assert compiled.selected_item_ref == "artifact://story/item-1"
    assert compiled.selected_item_digest == "sha256:item-1"
    assert [node.kind for node in compiled.nodes] == [
        "implementation",
        "verification",
        "remediation",
        "post_remediation_verification",
        "publication_evaluation",
    ]
    assert all(node.selected_item_digest == "sha256:item-1" for node in compiled.nodes)

    with pytest.raises(ValidationError):
        BoundedStoryLoopInput.model_validate(
            {
                "selectedItemRef": "artifact://story/item-1",
                "selectedItemDigest": "sha256:item-1",
                "additionalItemRefs": ["artifact://story/item-2"],
                "budgets": _budget().model_dump(by_alias=True),
            }
        )


def test_stop_state_enum_malformed_gate_and_continuation_decisions() -> None:
    assert {state.value for state in LoopStopState} == {
        "accepted",
        "blocked",
        "needs_human",
        "failed_with_remaining_work",
        "failed_unrecoverable",
    }

    malformed = TypedGateResult.from_boundary_payload(
        {"verdict": "approved in prose", "diagnosticsRef": "artifact://diag"}
    )
    assert malformed.verdict == "NO_DETERMINATION"
    assert malformed.terminal_disposition == "blocked"
    assert malformed.degraded is True

    decision = evaluate_attempt_continuation(
        attempt=_attempt(candidateDiffRef=None, acceptedOutputRef="artifact://accepted"),
        gate=_gate(
            verdict="FULLY_IMPLEMENTED",
            terminalDisposition="accepted",
            remainingWorkRef=None,
        ),
        budget=_budget(consumed={"attempts": 1}),
        checkpoint_available=True,
        policy_allowed=True,
    )

    assert decision.state == "accepted"
    assert decision.reason == "accepted_gate_passed"
    assert decision.continue_loop is False


def test_additional_work_needed_requires_durable_remaining_work_ref() -> None:
    with pytest.raises(
        ValidationError,
        match="ADDITIONAL_WORK_NEEDED requires remainingWorkRef durable evidence",
    ):
        _gate(remainingWorkRef=None)


def test_remaining_work_v1_is_bounded_and_redacted() -> None:
    artifact = RemainingWorkArtifact.model_validate(
        {
            "sourceGateResultRef": "artifact://gate/final",
            "sourceVerificationRef": "artifact://verification/final",
            "workspaceHeadRef": "artifact://workspace/final",
            "gaps": ["implement result projection", "token=secret"],
            "generatedAt": "2026-07-23T00:00:00Z",
        }
    )

    assert artifact.schema_version == "remaining-work/v1"
    assert artifact.gaps == ("implement result projection", "[REDACTED]")


@pytest.mark.parametrize(
    ("reason", "feasible"),
    [
        ("commits_ahead_of_base", True),
        ("safe_candidate_diff", True),
        ("verified_remote_head", True),
        ("no_candidate_change", False),
        ("publication_unauthorized", False),
        ("candidate_contaminated", False),
        ("publication_state_ambiguous", False),
    ],
)
def test_publication_feasibility_has_stable_complete_reason_set(
    reason: str, feasible: bool
) -> None:
    decision = PublicationFeasibility.model_validate(
        {"feasible": feasible, "reason": reason}
    )
    assert decision.reason == reason


def test_legacy_additional_work_uses_gate_result_as_remaining_work() -> None:
    gate = TypedGateResult.from_boundary_payload(
        {
            "verdict": "ADDITIONAL_WORK_NEEDED",
            "gateResultRef": "artifact://gate/legacy",
        }
    )

    assert gate.remaining_work_ref == "artifact://gate/legacy"
    assert gate.degraded is False


def test_legacy_additional_work_without_evidence_degrades_deterministically() -> None:
    gate = TypedGateResult.from_boundary_payload(
        {"verdict": "ADDITIONAL_WORK_NEEDED"}
    )

    assert gate.verdict == "NO_DETERMINATION"
    assert gate.terminal_disposition == "blocked"
    assert gate.degraded is True


def test_candidate_workspace_head_advances_from_latest_durable_checkpoint() -> None:
    root = advance_candidate_workspace_head(
        previous=None,
        loop_id="mm:loop-1",
        attempt_ordinal=0,
        checkpoint_ref="artifact://checkpoint/root",
        checkpoint_digest="sha256:" + "1" * 64,
    )
    advanced = advance_candidate_workspace_head(
        previous=root,
        loop_id="mm:loop-1",
        attempt_ordinal=1,
        checkpoint_ref="artifact://checkpoint/attempt-1",
        checkpoint_digest="sha256:" + "2" * 64,
    )

    assert root.parent_head_digest is None
    assert advanced.parent_head_digest == root.head_digest
    assert advanced.head_digest != root.head_digest
    assert CandidateWorkspaceHead.model_validate(
        advanced.model_dump(by_alias=True)
    ) == advanced


def test_candidate_workspace_head_rejects_fallback_fork_and_tampering() -> None:
    root = advance_candidate_workspace_head(
        previous=None,
        loop_id="mm:loop-1",
        attempt_ordinal=0,
        checkpoint_ref="artifact://checkpoint/root",
        checkpoint_digest="sha256:" + "1" * 64,
    )

    with pytest.raises(ValueError, match="only the loop root"):
        advance_candidate_workspace_head(
            previous=None,
            loop_id="mm:loop-1",
            attempt_ordinal=2,
            checkpoint_ref="artifact://checkpoint/original-root",
            checkpoint_digest="sha256:" + "1" * 64,
        )
    with pytest.raises(ValueError, match="advance exactly once"):
        advance_candidate_workspace_head(
            previous=root,
            loop_id="mm:loop-1",
            attempt_ordinal=2,
            checkpoint_ref="artifact://checkpoint/fork",
            checkpoint_digest="sha256:" + "2" * 64,
        )

    tampered = root.model_dump(by_alias=True)
    tampered["checkpointRef"] = "artifact://checkpoint/substituted"
    with pytest.raises(ValidationError, match="digest does not match"):
        CandidateWorkspaceHead.model_validate(tampered)


def test_verification_workspace_integrity_preserves_read_only_candidate() -> None:
    candidate = advance_candidate_workspace_head(
        previous=None,
        loop_id="mm:loop-1",
        attempt_ordinal=0,
        checkpoint_ref="artifact://checkpoint/root",
        checkpoint_digest="sha256:" + "1" * 64,
    )
    snapshot = VerificationWorkspaceSnapshot.model_validate(
        {
            "candidateHeadDigest": candidate.head_digest,
            "checkpointDigest": candidate.checkpoint_digest,
            "workspaceDigest": "sha256:" + "2" * 64,
            "projectionRef": "artifact://verification-projection/loop-1",
            "accessMode": "read_only",
        }
    )

    validate_verification_workspace_integrity(
        candidate=candidate,
        before=snapshot,
        after=snapshot.model_copy(),
    )


@pytest.mark.parametrize(
    ("changed_field", "changed_value", "error"),
    [
        ("workspace_digest", "sha256:" + "3" * 64, "contaminated"),
        ("projection_ref", "artifact://verification-projection/replaced", "replaced"),
        ("candidate_head_digest", "sha256:" + "4" * 64, "candidate head"),
        ("checkpoint_digest", "sha256:" + "5" * 64, "candidate checkpoint"),
    ],
)
def test_verification_workspace_integrity_fails_closed_on_mutation_or_substitution(
    changed_field: str, changed_value: str, error: str
) -> None:
    candidate = advance_candidate_workspace_head(
        previous=None,
        loop_id="mm:loop-1",
        attempt_ordinal=0,
        checkpoint_ref="artifact://checkpoint/root",
        checkpoint_digest="sha256:" + "1" * 64,
    )
    before = VerificationWorkspaceSnapshot(
        candidateHeadDigest=candidate.head_digest,
        checkpointDigest=candidate.checkpoint_digest,
        workspaceDigest="sha256:" + "2" * 64,
        projectionRef="artifact://verification-projection/loop-1",
    )
    after = before.model_copy(update={changed_field: changed_value})

    with pytest.raises(ValueError, match=error):
        validate_verification_workspace_integrity(
            candidate=candidate,
            before=before,
            after=after,
        )


def test_verification_workspace_snapshot_rejects_writable_projection() -> None:
    with pytest.raises(ValidationError):
        VerificationWorkspaceSnapshot.model_validate(
            {
                "candidateHeadDigest": "sha256:" + "1" * 64,
                "checkpointDigest": "sha256:" + "2" * 64,
                "workspaceDigest": "sha256:" + "3" * 64,
                "projectionRef": "artifact://verification-projection/loop-1",
                "accessMode": "read_write",
            }
        )


@pytest.mark.parametrize(
    ("budget_overrides", "consumed", "reason"),
    [
        ({"maxAttempts": 2}, {"attempts": 2}, "max_attempts_exhausted"),
        (
            {"maxConsecutiveNoProgressAttempts": 2},
            {"consecutive_no_progress_attempts": 2},
            "no_progress_attempts_exhausted",
        ),
        (
            {"maxRepeatedFailedCommands": 2},
            {"repeated_failed_commands": 2},
            "repeated_failed_commands_exhausted",
        ),
        (
            {"maxUnsafeOrPolicyDeniedAttempts": 1},
            {"unsafe_or_policy_denied_attempts": 1},
            "unsafe_policy_attempts_exhausted",
        ),
        (
            {"maxElapsedSeconds": 300},
            {"elapsed_seconds": 300},
            "wall_clock_budget_exhausted",
        ),
        ({"providerBudget": 2}, {"provider_budget": 2}, "provider_budget_exhausted"),
        ({"tokenBudget": 20}, {"token_budget": 20}, "token_budget_exhausted"),
        ({"costBudget": 3}, {"cost_budget": 3}, "cost_budget_exhausted"),
    ],
)
def test_each_independent_loop_budget_fails_closed_at_its_limit(
    budget_overrides: dict[str, int],
    consumed: dict[str, int],
    reason: str,
) -> None:
    decision = evaluate_attempt_continuation(
        attempt=_attempt(),
        gate=_gate(),
        budget=_budget(**budget_overrides, consumed=consumed),
        checkpoint_available=True,
        policy_allowed=True,
    )

    assert decision.continue_loop is False
    assert decision.reason == reason
    assert decision.remaining_work_ref == "artifact://remaining-work/attempt-1"


def test_zero_optional_and_failure_budgets_do_not_stop_before_consumption() -> None:
    decision = evaluate_attempt_continuation(
        attempt=_attempt(),
        gate=_gate(),
        budget=_budget(
            maxRepeatedFailedCommands=0,
            maxUnsafeOrPolicyDeniedAttempts=0,
            maxElapsedSeconds=None,
            providerBudget=0,
            tokenBudget=0,
            costBudget=0,
            consumed={},
        ),
        checkpoint_available=True,
        policy_allowed=True,
    )

    assert decision.continue_loop is True
    assert decision.reason == "verification_requested_remediation"


def test_checkpoint_candidate_remaining_work_refs_are_required_and_ref_only() -> None:
    failed = _attempt()
    assert failed.checkpoint_before_ref == "artifact://checkpoint/before"
    assert failed.checkpoint_after_ref == "artifact://checkpoint/after"
    assert failed.candidate_diff_ref == "artifact://diff/candidate"
    assert failed.accepted_output_ref is None
    assert failed.commit_allowed is False
    assert failed.publication_allowed is False

    with pytest.raises(ValidationError):
        LoopAttempt.model_validate(
            {
                "attemptOrdinal": 1,
                "kind": "implementation",
                "stepExecutionId": "workflow:run:implement-story:execution:1",
                "checkpointBeforeRef": "artifact://checkpoint/before",
                "checkpointAfterRef": "artifact://checkpoint/after",
                "candidateDiffRef": "diff --git a/file b/file",
                "gateResultRef": "artifact://gate",
                "terminalDisposition": "failed_with_remaining_work",
            }
        )

    with pytest.raises(ValidationError):
        TypedGateResult.model_validate(
            {
                "verdict": "ADDITIONAL_WORK_NEEDED",
                "gateResultRef": "artifact://gate",
                "remainingWork": "raw logs and stdout must not be inline",
            }
        )


@pytest.mark.parametrize(
    "payload",
    [
        {
            "model": CompiledBoundedStoryLoop,
            "data": {
                "selectedItemRef": "raw selected item payload",
                "selectedItemDigest": "sha256:item-1",
                "nodes": [
                    {
                        "kind": "implementation",
                        "selectedItemDigest": "sha256:item-1",
                    }
                ],
            },
        },
        {
            "model": LoopStopDecision,
            "data": {
                "state": "failed_with_remaining_work",
                "reason": "verification_requested_remediation",
                "remainingWorkRef": "diff --git a/file b/file",
            },
        },
        {
            "model": LoopStopDecision,
            "data": {
                "state": "blocked",
                "reason": "verification_blocked",
                "diagnosticsRef": "raw stderr from provider",
            },
        },
        {
            "model": PublicationDecision,
            "data": {
                "allowed": False,
                "reason": "typed_gate_not_accepted",
                "action": "pr",
                "gateResultRef": "provider payload with token=value",
            },
        },
        {
            "model": PublicationDecision,
            "data": {
                "allowed": True,
                "reason": "accepted_latest_step_execution",
                "action": "pr",
                "sideEffectRefs": ["artifact://accepted", "raw stdout"],
            },
        },
        {
            "model": PreflightDecision,
            "data": {
                "allowed": False,
                "state": "blocked",
                "reason": "runtime_preflight_failed",
                "diagnosticsRef": "diagnostics in prose",
                "consumesAttemptBudget": False,
            },
        },
        {
            "model": ProviderLeaseDecision,
            "data": {
                "allowed": True,
                "queued": False,
                "reason": "provider_lease_granted",
                "leaseRef": "lease details inline",
            },
        },
        {
            "model": ProviderLeaseDecision,
            "data": {
                "allowed": False,
                "queued": False,
                "reason": "provider_lease_denied",
                "diagnosticsRef": "private key leaked inline",
            },
        },
    ],
)
def test_boundary_decision_refs_reject_inline_payloads(
    payload: dict[str, object],
) -> None:
    model = payload["model"]
    data = payload["data"]

    with pytest.raises(ValidationError):
        model.model_validate(data)


@pytest.mark.parametrize(
    "action",
    [
        PublicationAction.PR,
        PublicationAction.JIRA,
        PublicationAction.MERGE,
        PublicationAction.DEPLOY,
        PublicationAction.PROVIDER_ACCOUNT,
    ],
)
def test_publication_decision_requires_latest_accepted_step(action: PublicationAction) -> None:
    denied = evaluate_publication_decision(
        action=action,
        latest_attempt=_attempt(),
        gate=_gate(),
    )
    assert denied.allowed is False
    assert denied.reason == "latest_step_execution_not_accepted"

    allowed = evaluate_publication_decision(
        action=action,
        latest_attempt=_attempt(
            candidateDiffRef=None,
            acceptedOutputRef="artifact://accepted-output",
            terminalDisposition="accepted",
        ),
        gate=_gate(
            verdict="FULLY_IMPLEMENTED",
            terminalDisposition="accepted",
            remainingWorkRef=None,
        ),
    )
    assert allowed.allowed is True
    assert allowed.reason == "accepted_latest_step_execution"
    assert allowed.latest_producing_step_execution_id.endswith(":execution:1")


@pytest.mark.parametrize(
    ("consumed", "expected_reason"),
    [
        ({"attempts": 3}, "max_attempts_exhausted"),
        ({"consecutiveNoProgressAttempts": 2}, "no_progress_attempts_exhausted"),
        ({"repeatedFailedCommands": 3}, "repeated_failed_commands_exhausted"),
        ({"unsafeOrPolicyDeniedAttempts": 1}, "unsafe_policy_attempts_exhausted"),
        ({"provider": 11}, "provider_budget_exhausted"),
        ({"tokens": 101}, "token_budget_exhausted"),
        ({"cost": 11}, "cost_budget_exhausted"),
    ],
)
def test_budget_dimensions_stop_with_remaining_work(
    consumed: dict[str, int], expected_reason: str
) -> None:
    decision = evaluate_attempt_continuation(
        attempt=_attempt(),
        gate=_gate(),
        budget=_budget(
            providerBudget=10,
            tokenBudget=100,
            costBudget=10,
            consumed=consumed,
        ),
        checkpoint_available=True,
        policy_allowed=True,
    )

    assert decision.state in {"needs_human", "failed_with_remaining_work"}
    assert decision.reason == expected_reason
    assert decision.remaining_work_ref == "artifact://remaining-work/attempt-1"
    assert decision.continue_loop is False


def test_preflight_blocks_before_remediation_budget_consumption() -> None:
    decision = evaluate_attempt_preflight(
        {
            "sidecar": {"ok": True},
            "runtime": {"ok": False, "diagnosticsRef": "artifact://runtime-diag"},
            "skillProjection": {"ok": True},
            "role": {"ok": True},
            "exceptionalWorkload": {"ok": True},
            "policy": {"ok": True},
        },
        budget=_budget(consumed={"attempts": 1}),
    )

    assert decision.allowed is False
    assert decision.state == "blocked"
    assert decision.reason == "runtime_preflight_failed"
    assert decision.diagnostics_ref == "artifact://runtime-diag"
    assert decision.consumes_attempt_budget is False


def test_provider_lease_decisions_reject_unavailable_and_stale_entitlements() -> None:
    unavailable = evaluate_provider_lease(
        {
            "status": "unavailable",
            "leaseRef": "artifact://lease/unavailable",
            "queueWhenUnavailable": True,
        }
    )
    assert unavailable.allowed is False
    assert unavailable.queued is True
    assert unavailable.reason == "provider_lease_unavailable"

    stale = evaluate_provider_lease(
        {
            "status": "granted",
            "leaseRef": "artifact://lease/stale",
            "stale": True,
        }
    )
    assert stale.allowed is False
    assert stale.queued is False
    assert stale.reason == "provider_lease_stale"
