"""Publication-only recovery coverage for MoonLadderStudios/MoonMind#3481."""

from copy import deepcopy

import pytest
from pydantic import ValidationError

from moonmind.workflows.temporal.publication_recovery import (
    PUBLICATION_ONLY_PHASES,
    PublicationObservation,
    PublicationRecoveryContract,
    PublicationRecoveryEvidence,
    PublicationRecoveryError,
    PublicationRecoveryRolloutPolicy,
    publication_action_eligibility,
    publication_operation_key,
    publication_recovery_workflow_id,
    reconcile_publication_state,
    validate_restored_candidate,
)


def _contract_payload(*, semantic_context: str = "accepted") -> dict:
    key = publication_operation_key(
        source_workflow_id="mm:source",
        source_run_id="run-source",
        publication_kind="pull_request",
        repository="MoonLadderStudios/MoonMind",
        head_ref="issue-3481",
        base_ref="main",
    )
    return {
        "sourceWorkflowId": "mm:source",
        "sourceRunId": "run-source",
        "sourceSemanticOutcome": (
            "failed" if semantic_context == "incomplete_draft_handoff" else "accepted"
        ),
        "target": {
            "kind": "publication",
            "publicationKind": "pull_request",
            "sourcePublicationOperationId": "publish-source",
            "semanticContext": semantic_context,
        },
        "continuation": {
            "phase": "resume_publication",
            "publicationIdempotencyKey": key,
            "candidateRef": "artifact://candidate/accepted",
            "beforePublicationCheckpointRef": (
                "artifact://checkpoint/before-publication"
            ),
            "expectedHeadSha": "a" * 40,
            "expectedTreeDigest": "sha256:" + "b" * 64,
            "expectedDiffDigest": "sha256:" + "c" * 64,
            "priorObservationsRef": "artifact://github/observations",
            **(
                {"remainingWorkRef": "artifact://remaining-work"}
                if semantic_context == "incomplete_draft_handoff"
                else {}
            ),
        },
        "intent": {
            "repository": "MoonLadderStudios/MoonMind",
            "baseRef": "main",
            "headRef": "issue-3481",
            "mode": (
                "draft_pr"
                if semantic_context == "incomplete_draft_handoff"
                else "pr"
            ),
            "branchPolicy": "reuse_exact_head",
            "githubAuthorityRef": "managed-secret://github/source",
        },
        "candidateAccepted": semantic_context == "accepted",
        "candidateContaminated": False,
        "hasPublishableChange": True,
        "publicationAuthorityCurrent": True,
        "incompleteDraftAuthorized": semantic_context == "incomplete_draft_handoff",
    }


def test_accepted_publication_contract_has_stable_operation_identity() -> None:
    payload = _contract_payload()
    first = PublicationRecoveryContract.model_validate(payload)
    second = PublicationRecoveryContract.model_validate(deepcopy(payload))

    assert first.target.kind == "publication"
    assert first.continuation.phase == "resume_publication"
    assert (
        first.continuation.publication_idempotency_key
        == second.continuation.publication_idempotency_key
    )


def test_operation_key_changes_with_frozen_publication_intent() -> None:
    common = {
        "source_workflow_id": "mm:source",
        "source_run_id": "run-source",
        "publication_kind": "pull_request",
        "repository": "org/repo",
        "head_ref": "head",
        "base_ref": "main",
    }
    original = publication_operation_key(**common)

    for field, replacement in (
        ("repository", "org/other"),
        ("head_ref", "other-head"),
        ("base_ref", "release"),
        ("publication_kind", "branch"),
    ):
        changed = dict(common)
        changed[field] = replacement
        assert publication_operation_key(**changed) != original


def test_duplicate_requests_map_to_one_publication_only_workflow() -> None:
    first = PublicationRecoveryContract.model_validate(_contract_payload())
    second = PublicationRecoveryContract.model_validate(_contract_payload())
    assert publication_recovery_workflow_id(first) == publication_recovery_workflow_id(
        second
    )
    assert PUBLICATION_ONLY_PHASES == (
        "contract_validation",
        "publication_state_reconciliation",
        "optional_workspace_restoration",
        "publication_operation",
        "publication_verification",
        "artifact_summary_persistence",
        "cleanup",
    )
    assert not {
        "implementation",
        "remediation",
        "moonspec_verification",
        "omnigent_agent_run",
    }.intersection(PUBLICATION_ONLY_PHASES)


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"hasPublishableChange": False}, "PUBLICATION_NO_CHANGE"),
        ({"candidateContaminated": True}, "PUBLICATION_CANDIDATE_UNSAFE"),
        (
            {"publicationAuthorityCurrent": False},
            "PUBLICATION_AUTHORITY_UNAVAILABLE",
        ),
        ({"candidateAccepted": False}, "PUBLICATION_CANDIDATE_UNACCEPTED"),
    ],
)
def test_unsafe_candidates_fail_closed_before_reconciliation(
    change: dict, code: str
) -> None:
    payload = _contract_payload()
    payload.update(change)

    with pytest.raises(ValidationError) as exc:
        PublicationRecoveryContract.model_validate(payload)
    assert code in str(exc.value)


def test_incomplete_draft_preserves_source_failure_and_remaining_work() -> None:
    contract = PublicationRecoveryContract.model_validate(
        _contract_payload(semantic_context="incomplete_draft_handoff")
    )
    assert contract.source_semantic_outcome == "failed"
    assert contract.intent.mode == "draft_pr"
    assert contract.continuation.remaining_work_ref

    invalid = _contract_payload(semantic_context="incomplete_draft_handoff")
    invalid["continuation"].pop("remainingWorkRef")
    with pytest.raises(ValidationError) as exc:
        PublicationRecoveryContract.model_validate(invalid)
    assert "PUBLICATION_DRAFT_HANDOFF_INVALID" in str(exc.value)


def test_publication_ready_source_requires_checkpoint_or_verified_remote() -> None:
    payload = _contract_payload()
    payload["continuation"].pop("beforePublicationCheckpointRef")
    with pytest.raises(ValidationError):
        PublicationRecoveryContract.model_validate(payload)

    payload["continuation"]["verifiedRemoteCandidateRef"] = "artifact://remote/head"
    assert PublicationRecoveryContract.model_validate(payload)


def test_destroyed_source_workspace_restores_exact_candidate_to_destination() -> None:
    contract = PublicationRecoveryContract.model_validate(_contract_payload())
    restoration = {
        "destinationWorkspaceLocator": {
            "kind": "managed",
            "agentRunId": "publication-destination",
        },
        "headSha": "a" * 40,
        "treeDigest": "sha256:" + "b" * 64,
        "diffDigest": "sha256:" + "c" * 64,
        "restorationEvidenceRef": "artifact://restoration/verified",
    }
    assert (
        validate_restored_candidate(contract, restoration)
        == "artifact://restoration/verified"
    )
    restoration["headSha"] = "f" * 40
    with pytest.raises(PublicationRecoveryError) as exc:
        validate_restored_candidate(contract, restoration)
    assert exc.value.code == "PUBLICATION_CANDIDATE_MISMATCH"


def test_matching_existing_pull_request_is_reconciled_without_mutation() -> None:
    contract = PublicationRecoveryContract.model_validate(_contract_payload())
    decision = reconcile_publication_state(
        contract,
        PublicationObservation(
            authoritative=True,
            authorityAvailable=True,
            remoteBranchExists=True,
            remoteHeadSha="a" * 40,
            pullRequestExists=True,
            pullRequestUrl="https://github.com/org/repo/pull/1",
            pullRequestHeadRef="issue-3481",
            pullRequestBaseRef="main",
            pullRequestHeadSha="a" * 40,
            pullRequestDraft=False,
        ),
    )
    assert decision.outcome == "already_completed"
    assert decision.mutation_allowed is False


def test_push_timeout_is_reconciled_from_authoritative_remote_head() -> None:
    contract = PublicationRecoveryContract.model_validate(_contract_payload())
    decision = reconcile_publication_state(
        contract,
        PublicationObservation(
            authoritative=True,
            authorityAvailable=True,
            remoteBranchExists=True,
            remoteHeadSha="a" * 40,
        ),
    )
    assert decision.outcome == "safe_to_retry"
    assert decision.reason_code == "matching_remote_head_reconciled"


@pytest.mark.parametrize(
    "observation",
    [
        {
            "authoritative": True,
            "authorityAvailable": True,
            "remoteBranchExists": True,
            "remoteHeadSha": "f" * 40,
        },
        {
            "authoritative": True,
            "authorityAvailable": True,
            "conflictingEvidence": True,
        },
        {
            "authoritative": True,
            "authorityAvailable": False,
        },
        {
            "authoritative": False,
            "authorityAvailable": True,
            "transientAbsenceOnly": True,
        },
    ],
)
def test_conflicting_ambiguous_or_unauthorized_state_denies_mutation(
    observation: dict,
) -> None:
    decision = reconcile_publication_state(
        PublicationRecoveryContract.model_validate(_contract_payload()),
        PublicationObservation.model_validate(observation),
    )
    assert decision.outcome in {"conflict", "ambiguous"}
    assert decision.mutation_allowed is False


def test_verified_evidence_proves_lineage_and_no_implementation_rerun() -> None:
    contract = PublicationRecoveryContract.model_validate(_contract_payload())
    evidence = PublicationRecoveryEvidence(
        sourceWorkflowId=contract.source_workflow_id,
        sourceRunId=contract.source_run_id,
        destinationWorkflowId="mm:publication-recovery",
        publicationIdempotencyKey=contract.continuation.publication_idempotency_key,
        reconciliationOutcome="already_completed",
        expectedHeadSha="a" * 40,
        observedHeadSha="a" * 40,
        repository=contract.intent.repository,
        baseRef=contract.intent.base_ref,
        headRef=contract.intent.head_ref,
        pullRequestUrl="https://github.com/org/repo/pull/1",
        pullRequestDraft=False,
        githubAuthorityRef=contract.intent.github_authority_ref,
        secretScanRef="artifact://scan/clean",
        diagnosticsRef="artifact://diagnostics/publication",
        publicationObservationsRef="artifact://github/observations",
        sourceSemanticOutcome=contract.source_semantic_outcome,
    )
    assert evidence.implementation_rerun is False
    assert evidence.verification_rerun is False


def test_rollout_supports_shadow_canary_disablement_and_inflight_freeze() -> None:
    disabled = PublicationRecoveryRolloutPolicy()
    assert disabled.admission_reason(
        repository="org/repo", owner_id="owner", mode="pr"
    ) == "publication_recovery_disabled"
    shadow = PublicationRecoveryRolloutPolicy(enabled=True, shadow=True)
    assert shadow.admission_reason(
        repository="org/repo", owner_id="owner", mode="pr"
    ) == "publication_recovery_shadow_only"
    canary = PublicationRecoveryRolloutPolicy(
        enabled=True,
        canaryRepositories=("org/canary",),
        allowedModes=("pr",),
        generation="canary-1",
    )
    assert canary.admission_reason(
        repository="org/other", owner_id=None, mode="pr"
    ) == "publication_recovery_not_in_canary"
    assert canary.admission_reason(
        repository="org/canary", owner_id=None, mode="pr"
    ) is None
    assert canary.admission_reason(
        repository="org/canary", owner_id=None, mode="draft_pr"
    ) == "publication_mode_not_allowed"


def test_action_projection_requires_complete_unambiguous_contract() -> None:
    payload = _contract_payload()
    assert publication_action_eligibility(payload) == (True, None)
    payload["ambiguityState"] = "timeout_unknown"
    assert publication_action_eligibility(payload) == (
        False,
        "publication_state_ambiguous",
    )
    assert publication_action_eligibility(None) == (
        False,
        "publication_recovery_evidence_missing",
    )
