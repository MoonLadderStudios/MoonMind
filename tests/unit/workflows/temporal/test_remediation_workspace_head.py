from __future__ import annotations

import pytest
from pydantic import ValidationError

from moonmind.workflows.temporal.remediation_workspace_head import (
    REMEDIATION_HEAD_MISMATCH,
    REMEDIATION_HEAD_RESTORE_INVALID,
    REMEDIATION_HEAD_STALE_VERSION,
    REMEDIATION_VERIFIER_CONTAMINATION,
    RemediationAttemptOutput,
    RemediationHeadError,
    RemediationGitEvidence,
    RemediationWorkspaceHead,
    VerificationEvidence,
    WorkspaceMaterializationEvidence,
    advance_head,
    apply_verification,
    authorize_materialization,
    freeze_attempt_input,
    mark_terminal,
    project_head,
    rollback_head,
)


def _head(**changes: object) -> RemediationWorkspaceHead:
    payload = {
        "loopId": "loop-01",
        "branchRef": "checkpoint-branch:loop-01",
        "rootCheckpointRef": "artifact://workspace/C0",
        "rootWorkspaceDigest": "sha256:c0",
        "headCheckpointRef": "artifact://workspace/C0",
        "headWorkspaceDigest": "sha256:c0",
    }
    payload.update(changes)
    return RemediationWorkspaceHead.model_validate(payload)


def _output(parent: RemediationWorkspaceHead, suffix: str) -> RemediationAttemptOutput:
    return RemediationAttemptOutput.model_validate({
        "attemptEvidenceRef": f"artifact://attempt/{suffix}",
        "parentCheckpointRef": parent.head_checkpoint_ref,
        "parentWorkspaceDigest": parent.head_workspace_digest,
        "outputCheckpointRef": f"artifact://workspace/{suffix}",
        "outputWorkspaceDigest": f"sha256:{suffix.lower()}",
        "candidateDiffRef": f"artifact://diff/{suffix}",
        "changedFilesRef": f"artifact://changed/{suffix}",
        "targetedChecksRef": f"artifact://checks/{suffix}",
        "checkpointManifestRef": f"artifact://manifest/{suffix}",
        "outcome": "candidate_captured",
    })


def _advance(head: RemediationWorkspaceHead, ordinal: int, suffix: str):
    attempt = freeze_attempt_input(head, ordinal)
    return advance_head(
        head, attempt, _output(head, suffix),
        step_execution_id=f"workflow:run:remediate:execution:{ordinal}",
        transition_id=f"loop-01:{ordinal}",
    )


def test_attempts_advance_cumulatively_and_freeze_immediate_parent() -> None:
    c0 = _head()
    c1, _ = _advance(c0, 1, "C1")
    attempt2 = freeze_attempt_input(c1, 2)
    assert attempt2.base_checkpoint_ref == "artifact://workspace/C1"
    assert attempt2.expected_base_digest == "sha256:c1"
    c2, _ = advance_head(c1, attempt2, _output(c1, "C2"), step_execution_id="step:2", transition_id="t2")
    attempt3 = freeze_attempt_input(c2, 3)
    assert (attempt3.base_checkpoint_ref, attempt3.expected_head_version) == ("artifact://workspace/C2", 3)


def test_only_first_attempt_can_use_root_and_parent_mismatch_fails() -> None:
    c1, _ = _advance(_head(), 1, "C1")
    with pytest.raises(RemediationHeadError) as exc:
        freeze_attempt_input(c1, 1)
    assert exc.value.code == REMEDIATION_HEAD_MISMATCH
    attempt2 = freeze_attempt_input(c1, 2)
    bad = _output(_head(), "C2")
    with pytest.raises(RemediationHeadError) as exc:
        advance_head(c1, attempt2, bad, step_execution_id="step:2", transition_id="t2")
    assert exc.value.code == REMEDIATION_HEAD_MISMATCH


@pytest.mark.parametrize("mode", ["live", "restored"])
def test_live_or_cold_workspace_requires_exact_owner_head_evidence(mode: str) -> None:
    head = _head()
    attempt = freeze_attempt_input(head, 1)
    evidence = WorkspaceMaterializationEvidence.model_validate({
        "loopId": head.loop_id, "checkpointRef": head.head_checkpoint_ref,
        "workspaceDigest": head.head_workspace_digest, "headVersion": head.head_version,
        "ownerStepExecutionId": "step:1", "mode": mode,
    })
    authorize_materialization(
        head, attempt, evidence, expected_owner_step_execution_id="step:1"
    )
    bad = evidence.model_copy(update={"workspace_digest": "sha256:wrong"})
    with pytest.raises(RemediationHeadError) as exc:
        authorize_materialization(
            head, attempt, bad, expected_owner_step_execution_id="step:1"
        )
    assert exc.value.code == (REMEDIATION_HEAD_RESTORE_INVALID if mode == "restored" else REMEDIATION_HEAD_MISMATCH)


def test_live_workspace_owned_by_another_step_is_rejected() -> None:
    head = _head()
    attempt = freeze_attempt_input(head, 1)
    evidence = WorkspaceMaterializationEvidence.model_validate({
        "loopId": head.loop_id, "checkpointRef": head.head_checkpoint_ref,
        "workspaceDigest": head.head_workspace_digest, "headVersion": head.head_version,
        "ownerStepExecutionId": "step:other", "mode": "live",
    })
    with pytest.raises(RemediationHeadError) as exc:
        authorize_materialization(
            head, attempt, evidence, expected_owner_step_execution_id="step:1"
        )
    assert exc.value.code == REMEDIATION_HEAD_MISMATCH


def test_capture_failure_keeps_prior_head_and_capture_must_precede_advance() -> None:
    head = _head()
    attempt = freeze_attempt_input(head, 1)
    incomplete = RemediationAttemptOutput.model_validate({
        "attemptEvidenceRef": "artifact://attempt/1", "parentCheckpointRef": head.head_checkpoint_ref,
        "parentWorkspaceDigest": head.head_workspace_digest, "outcome": "capture_incomplete",
    })
    with pytest.raises(RemediationHeadError) as exc:
        advance_head(head, attempt, incomplete, step_execution_id="step:1", transition_id="t1")
    assert exc.value.code == REMEDIATION_HEAD_RESTORE_INVALID
    assert head.head_checkpoint_ref == "artifact://workspace/C0"


def test_duplicate_completion_reconciles_and_stale_writer_is_rejected() -> None:
    head = _head()
    attempt = freeze_attempt_input(head, 1)
    advanced, transition = advance_head(head, attempt, _output(head, "C1"), step_execution_id="step:1", transition_id="t1")
    reconciled, duplicate = advance_head(advanced, attempt, _output(head, "C1"), step_execution_id="step:1", transition_id="t1", prior_transitions=(transition,))
    assert reconciled == advanced and duplicate == transition
    with pytest.raises(RemediationHeadError) as exc:
        advance_head(advanced, attempt, _output(head, "other"), step_execution_id="stale", transition_id="other")
    assert exc.value.code == REMEDIATION_HEAD_STALE_VERSION


def test_read_only_verification_detects_contamination_without_advancing() -> None:
    head, _ = _advance(_head(), 1, "C1")
    evidence = VerificationEvidence.model_validate({
        "inputHeadRef": head.head_checkpoint_ref, "inputHeadDigest": head.head_workspace_digest,
        "inputHeadVersion": head.head_version, "preVerificationWorkspaceDigest": head.head_workspace_digest,
        "postVerificationWorkspaceDigest": "sha256:mutated", "verifierArtifactRef": "artifact://verify/V1",
        "verdict": "FULLY_IMPLEMENTED",
    })
    contaminated = apply_verification(head, evidence)
    assert contaminated.status == "contaminated"
    assert contaminated.latest_verification_verdict == REMEDIATION_VERIFIER_CONTAMINATION
    assert contaminated.head_version == head.head_version


def test_no_change_does_not_manufacture_progress() -> None:
    head = _head()
    attempt = freeze_attempt_input(head, 1)
    output = RemediationAttemptOutput.model_validate({
        "attemptEvidenceRef": "artifact://attempt/1", "parentCheckpointRef": head.head_checkpoint_ref,
        "parentWorkspaceDigest": head.head_workspace_digest, "outcome": "no_candidate_change",
    })
    unchanged, transition = advance_head(head, attempt, output, step_execution_id="step:1", transition_id="t1")
    assert unchanged == head
    assert transition.kind == "no_change" and transition.to_version == transition.from_version


def test_rollback_is_new_append_only_transition() -> None:
    c1, prior = _advance(_head(), 1, "C1")
    rolled_back, rollback = rollback_head(
        c1, checkpoint_ref=c1.root_checkpoint_ref, workspace_digest=c1.root_workspace_digest,
        evidence_ref="artifact://decision/rollback", transition_id="rollback-1",
    )
    assert rolled_back.head_version == c1.head_version + 1
    assert rolled_back.supersedes_checkpoint_ref == c1.head_checkpoint_ref
    assert [prior.kind, rollback.kind] == ["advance", "rollback"]


def test_continue_as_new_round_trip_and_terminal_linked_baseline() -> None:
    c1, _ = _advance(_head(), 1, "C1")
    carried = RemediationWorkspaceHead.model_validate(c1.model_dump(by_alias=True, mode="json"))
    terminal = mark_terminal(carried, "artifact://remaining/terminal")
    next_attempt = freeze_attempt_input(terminal, 2)
    assert next_attempt.base_checkpoint_ref == "artifact://workspace/C1"
    assert terminal.status == "terminal_remaining_work"


def test_projection_is_ref_only_and_exposes_exact_next_baseline() -> None:
    projection = project_head(_head())
    assert projection["nextActionBaseline"] == {
        "checkpointRef": "artifact://workspace/C0", "workspaceDigest": "sha256:c0", "headVersion": 1,
    }
    assert "path" not in str(projection).lower()
    for bad in ("/tmp/workspace", "raw diff", "token=secret"):
        with pytest.raises(ValidationError):
            _head(headCheckpointRef=bad)


def test_contracts_reject_raw_payload_fields() -> None:
    payload = _head().model_dump(by_alias=True)
    payload["providerPayload"] = {"token": "secret"}
    with pytest.raises(ValidationError):
        RemediationWorkspaceHead.model_validate(payload)


def test_git_evidence_is_path_free_and_digest_bound() -> None:
    evidence = RemediationGitEvidence.model_validate({
        "baseCommit": "0123456789abcdef",
        "worktreeStateRef": "artifact://git/worktree-state/1",
        "worktreeDigest": "sha256:worktree",
        "candidateRef": "refs/heads/remediation/loop-01",
        "candidateDiffDigest": "sha256:diff",
    })
    assert evidence.base_commit == "0123456789abcdef"
    for field, value in (
        ("worktreeStateRef", "/tmp/worktree"),
        ("candidateRef", "/tmp/branch"),
        ("baseCommit", "not-a-commit"),
    ):
        payload = evidence.model_dump(by_alias=True)
        payload[field] = value
        with pytest.raises(ValidationError):
            RemediationGitEvidence.model_validate(payload)
