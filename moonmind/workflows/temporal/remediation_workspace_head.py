"""Durable candidate workspace-head contracts for cumulative remediation.

The models in this module are deliberately compact and ref-only so they can be
carried in Temporal state, including Continue-As-New input. Workspace capture,
restore, and artifact persistence remain activity-owned side effects.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


REMEDIATION_HEAD_MISMATCH = "REMEDIATION_HEAD_MISMATCH"
REMEDIATION_HEAD_RESTORE_INVALID = "REMEDIATION_HEAD_RESTORE_INVALID"
REMEDIATION_HEAD_STALE_VERSION = "REMEDIATION_HEAD_STALE_VERSION"
REMEDIATION_VERIFIER_CONTAMINATION = "REMEDIATION_VERIFIER_CONTAMINATION"


def _artifact_ref(value: str) -> str:
    candidate = str(value or "").strip()
    if not candidate.startswith("artifact://") or any(
        token in candidate.lower()
        for token in ("/tmp/", "workspacepath", "token=", "password=")
    ):
        raise ValueError("value must be a safe artifact ref")
    return candidate


def _digest(value: str) -> str:
    candidate = str(value or "").strip()
    if not candidate.startswith("sha256:") or len(candidate) <= 7:
        raise ValueError("value must be a sha256 digest")
    return candidate


class RemediationHeadError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class _Contract(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)


class RemediationHeadStatus(StrEnum):
    CANDIDATE = "candidate"
    VERIFIED_INCOMPLETE = "verified_incomplete"
    ACCEPTED = "accepted"
    CONTAMINATED = "contaminated"
    SUPERSEDED = "superseded"
    TERMINAL_REMAINING_WORK = "terminal_remaining_work"


class RemediationWorkspaceHead(_Contract):
    schema_version: Literal["remediation-workspace-head/v1"] = Field(
        "remediation-workspace-head/v1", alias="schemaVersion"
    )
    loop_id: str = Field(alias="loopId", min_length=1)
    branch_ref: str = Field(alias="branchRef")
    root_checkpoint_ref: str = Field(alias="rootCheckpointRef")
    root_workspace_digest: str = Field(alias="rootWorkspaceDigest")
    head_checkpoint_ref: str = Field(alias="headCheckpointRef")
    head_workspace_digest: str = Field(alias="headWorkspaceDigest")
    head_step_execution_id: str | None = Field(None, alias="headStepExecutionId")
    head_attempt_ordinal: int = Field(0, alias="headAttemptOrdinal", ge=0)
    head_version: int = Field(1, alias="headVersion", ge=1)
    latest_verification_ref: str | None = Field(None, alias="latestVerificationRef")
    latest_verification_verdict: str | None = Field(
        None, alias="latestVerificationVerdict"
    )
    status: RemediationHeadStatus = RemediationHeadStatus.CANDIDATE
    supersedes_checkpoint_ref: str | None = Field(
        None, alias="supersedesCheckpointRef"
    )
    transition_evidence_ref: str | None = Field(None, alias="transitionEvidenceRef")
    remaining_work_ref: str | None = Field(None, alias="remainingWorkRef")

    @field_validator(
        "root_checkpoint_ref",
        "head_checkpoint_ref",
        "latest_verification_ref",
        "supersedes_checkpoint_ref",
        "transition_evidence_ref",
        "remaining_work_ref",
    )
    @classmethod
    def _artifact_refs_only(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _artifact_ref(value)

    @field_validator("branch_ref")
    @classmethod
    def _branch_ref_only(cls, value: str) -> str:
        value = value.strip()
        if not value.startswith("checkpoint-branch:"):
            raise ValueError("branchRef must be a checkpoint-branch ref")
        return value

    @field_validator("root_workspace_digest", "head_workspace_digest")
    @classmethod
    def _digests_only(cls, value: str) -> str:
        return _digest(value)


class RemediationAttemptInput(_Contract):
    loop_id: str = Field(alias="loopId", min_length=1)
    attempt_ordinal: int = Field(alias="attemptOrdinal", ge=1)
    base_checkpoint_ref: str = Field(alias="baseCheckpointRef")
    expected_base_digest: str = Field(alias="expectedBaseDigest")
    expected_head_version: int = Field(alias="expectedHeadVersion", ge=1)
    latest_verification_ref: str | None = Field(None, alias="latestVerificationRef")
    workspace_policy: Literal["continue_from_loop_head"] = Field(
        "continue_from_loop_head", alias="workspacePolicy"
    )

    _base_ref = field_validator("base_checkpoint_ref")(_artifact_ref)
    _base_digest = field_validator("expected_base_digest")(_digest)
    _verification_ref = field_validator("latest_verification_ref")(
        lambda value: None if value is None else _artifact_ref(value)
    )


class RemediationAttemptOutput(_Contract):
    attempt_evidence_ref: str = Field(alias="attemptEvidenceRef")
    parent_checkpoint_ref: str = Field(alias="parentCheckpointRef")
    parent_workspace_digest: str = Field(alias="parentWorkspaceDigest")
    output_checkpoint_ref: str | None = Field(None, alias="outputCheckpointRef")
    output_workspace_digest: str | None = Field(None, alias="outputWorkspaceDigest")
    candidate_diff_ref: str | None = Field(None, alias="candidateDiffRef")
    changed_files_ref: str | None = Field(None, alias="changedFilesRef")
    targeted_checks_ref: str | None = Field(None, alias="targetedChecksRef")
    checkpoint_manifest_ref: str | None = Field(None, alias="checkpointManifestRef")
    outcome: Literal["candidate_captured", "no_candidate_change", "capture_incomplete"]

    @field_validator(
        "attempt_evidence_ref", "parent_checkpoint_ref", "output_checkpoint_ref",
        "candidate_diff_ref", "changed_files_ref", "targeted_checks_ref",
        "checkpoint_manifest_ref",
    )
    @classmethod
    def _refs_only(cls, value: str | None) -> str | None:
        return None if value is None else _artifact_ref(value)

    @field_validator("parent_workspace_digest", "output_workspace_digest")
    @classmethod
    def _output_digests(cls, value: str | None) -> str | None:
        return None if value is None else _digest(value)

    @model_validator(mode="after")
    def _captured_output_is_complete(self) -> "RemediationAttemptOutput":
        if self.outcome == "candidate_captured" and not all(
            (self.output_checkpoint_ref, self.output_workspace_digest, self.checkpoint_manifest_ref)
        ):
            raise ValueError("candidate_captured requires checkpoint, digest, and manifest refs")
        if self.outcome == "no_candidate_change" and (
            self.output_checkpoint_ref or self.output_workspace_digest
        ):
            raise ValueError("no_candidate_change must not manufacture a checkpoint")
        return self


class RemediationHeadTransition(_Contract):
    transition_id: str = Field(alias="transitionId", min_length=1)
    kind: Literal["advance", "rollback", "verification", "terminal", "no_change"]
    from_version: int = Field(alias="fromVersion", ge=1)
    to_version: int = Field(alias="toVersion", ge=1)
    from_checkpoint_ref: str = Field(alias="fromCheckpointRef")
    to_checkpoint_ref: str = Field(alias="toCheckpointRef")
    attempt_ordinal: int = Field(alias="attemptOrdinal", ge=0)
    step_execution_id: str | None = Field(None, alias="stepExecutionId")
    evidence_ref: str = Field(alias="evidenceRef")

    _from_ref = field_validator("from_checkpoint_ref")(_artifact_ref)
    _to_ref = field_validator("to_checkpoint_ref")(_artifact_ref)
    _evidence_ref = field_validator("evidence_ref")(_artifact_ref)


class VerificationEvidence(_Contract):
    input_head_ref: str = Field(alias="inputHeadRef")
    input_head_digest: str = Field(alias="inputHeadDigest")
    input_head_version: int = Field(alias="inputHeadVersion", ge=1)
    pre_verification_workspace_digest: str = Field(alias="preVerificationWorkspaceDigest")
    post_verification_workspace_digest: str = Field(alias="postVerificationWorkspaceDigest")
    verifier_artifact_ref: str = Field(alias="verifierArtifactRef")
    verdict: str = Field(min_length=1)

    _head_ref = field_validator("input_head_ref")(_artifact_ref)
    _verifier_ref = field_validator("verifier_artifact_ref")(_artifact_ref)
    _digests = field_validator(
        "input_head_digest", "pre_verification_workspace_digest",
        "post_verification_workspace_digest",
    )(_digest)

    @property
    def contaminated(self) -> bool:
        return self.pre_verification_workspace_digest != self.post_verification_workspace_digest


class WorkspaceMaterializationEvidence(_Contract):
    loop_id: str = Field(alias="loopId")
    checkpoint_ref: str = Field(alias="checkpointRef")
    workspace_digest: str = Field(alias="workspaceDigest")
    head_version: int = Field(alias="headVersion", ge=1)
    owner_step_execution_id: str | None = Field(None, alias="ownerStepExecutionId")
    mode: Literal["live", "restored"]

    _checkpoint_ref = field_validator("checkpoint_ref")(_artifact_ref)
    _workspace_digest = field_validator("workspace_digest")(_digest)


def freeze_attempt_input(head: RemediationWorkspaceHead, attempt_ordinal: int) -> RemediationAttemptInput:
    if attempt_ordinal != head.head_attempt_ordinal + 1:
        raise RemediationHeadError(REMEDIATION_HEAD_MISMATCH, "attempt ordinal is not the next loop attempt")
    return RemediationAttemptInput(
        loopId=head.loop_id, attemptOrdinal=attempt_ordinal,
        baseCheckpointRef=head.head_checkpoint_ref,
        expectedBaseDigest=head.head_workspace_digest,
        expectedHeadVersion=head.head_version,
        latestVerificationRef=head.latest_verification_ref,
    )


def authorize_materialization(
    head: RemediationWorkspaceHead,
    attempt: RemediationAttemptInput,
    evidence: WorkspaceMaterializationEvidence,
) -> None:
    expected = (head.loop_id, head.head_checkpoint_ref, head.head_workspace_digest, head.head_version)
    actual = (evidence.loop_id, evidence.checkpoint_ref, evidence.workspace_digest, evidence.head_version)
    attempt_expected = (attempt.loop_id, attempt.base_checkpoint_ref, attempt.expected_base_digest, attempt.expected_head_version)
    if attempt_expected != expected:
        raise RemediationHeadError(REMEDIATION_HEAD_MISMATCH, "attempt input does not match the current head")
    if actual != expected:
        code = REMEDIATION_HEAD_RESTORE_INVALID if evidence.mode == "restored" else REMEDIATION_HEAD_MISMATCH
        raise RemediationHeadError(code, "materialized workspace does not match the current head")


def advance_head(
    head: RemediationWorkspaceHead,
    attempt: RemediationAttemptInput,
    output: RemediationAttemptOutput,
    *,
    step_execution_id: str,
    transition_id: str,
    prior_transitions: tuple[RemediationHeadTransition, ...] = (),
) -> tuple[RemediationWorkspaceHead, RemediationHeadTransition]:
    for transition in prior_transitions:
        if transition.transition_id == transition_id:
            if transition.from_version != attempt.expected_head_version:
                raise RemediationHeadError(REMEDIATION_HEAD_STALE_VERSION, "transition identity was reused for another head")
            return head, transition
    if head.head_version != attempt.expected_head_version:
        raise RemediationHeadError(REMEDIATION_HEAD_STALE_VERSION, "head was advanced by another attempt")
    if (output.parent_checkpoint_ref, output.parent_workspace_digest) != (
        head.head_checkpoint_ref, head.head_workspace_digest
    ):
        raise RemediationHeadError(REMEDIATION_HEAD_MISMATCH, "captured candidate parent does not match head")
    if output.outcome == "capture_incomplete":
        raise RemediationHeadError(REMEDIATION_HEAD_RESTORE_INVALID, "checkpoint capture is incomplete")
    if output.outcome == "no_candidate_change":
        transition = RemediationHeadTransition(
            transitionId=transition_id, kind="no_change", fromVersion=head.head_version,
            toVersion=head.head_version, fromCheckpointRef=head.head_checkpoint_ref,
            toCheckpointRef=head.head_checkpoint_ref, attemptOrdinal=attempt.attempt_ordinal,
            stepExecutionId=step_execution_id, evidenceRef=output.attempt_evidence_ref,
        )
        return head, transition
    updated = head.model_copy(update={
        "head_checkpoint_ref": output.output_checkpoint_ref,
        "head_workspace_digest": output.output_workspace_digest,
        "head_step_execution_id": step_execution_id,
        "head_attempt_ordinal": attempt.attempt_ordinal,
        "head_version": head.head_version + 1,
        "status": RemediationHeadStatus.CANDIDATE,
        "transition_evidence_ref": output.attempt_evidence_ref,
        "latest_verification_ref": None,
        "latest_verification_verdict": None,
    })
    transition = RemediationHeadTransition(
        transitionId=transition_id, kind="advance", fromVersion=head.head_version,
        toVersion=updated.head_version, fromCheckpointRef=head.head_checkpoint_ref,
        toCheckpointRef=updated.head_checkpoint_ref, attemptOrdinal=attempt.attempt_ordinal,
        stepExecutionId=step_execution_id, evidenceRef=output.attempt_evidence_ref,
    )
    return updated, transition


def apply_verification(head: RemediationWorkspaceHead, evidence: VerificationEvidence) -> RemediationWorkspaceHead:
    if (evidence.input_head_ref, evidence.input_head_digest, evidence.input_head_version) != (
        head.head_checkpoint_ref, head.head_workspace_digest, head.head_version
    ):
        raise RemediationHeadError(REMEDIATION_HEAD_MISMATCH, "verification did not read the current head")
    if evidence.contaminated:
        return head.model_copy(update={
            "status": RemediationHeadStatus.CONTAMINATED,
            "latest_verification_ref": evidence.verifier_artifact_ref,
            "latest_verification_verdict": REMEDIATION_VERIFIER_CONTAMINATION,
        })
    accepted = evidence.verdict.strip().upper() == "FULLY_IMPLEMENTED"
    return head.model_copy(update={
        "status": RemediationHeadStatus.ACCEPTED if accepted else RemediationHeadStatus.VERIFIED_INCOMPLETE,
        "latest_verification_ref": evidence.verifier_artifact_ref,
        "latest_verification_verdict": evidence.verdict,
    })


def mark_terminal(head: RemediationWorkspaceHead, remaining_work_ref: str) -> RemediationWorkspaceHead:
    return head.model_copy(update={
        "status": RemediationHeadStatus.TERMINAL_REMAINING_WORK,
        "remaining_work_ref": _artifact_ref(remaining_work_ref),
    })


def rollback_head(
    head: RemediationWorkspaceHead, *, checkpoint_ref: str, workspace_digest: str,
    evidence_ref: str, transition_id: str,
) -> tuple[RemediationWorkspaceHead, RemediationHeadTransition]:
    target_ref = _artifact_ref(checkpoint_ref)
    target_digest = _digest(workspace_digest)
    updated = head.model_copy(update={
        "head_checkpoint_ref": target_ref, "head_workspace_digest": target_digest,
        "head_version": head.head_version + 1, "status": RemediationHeadStatus.CANDIDATE,
        "supersedes_checkpoint_ref": head.head_checkpoint_ref,
        "transition_evidence_ref": _artifact_ref(evidence_ref),
    })
    transition = RemediationHeadTransition(
        transitionId=transition_id, kind="rollback", fromVersion=head.head_version,
        toVersion=updated.head_version, fromCheckpointRef=head.head_checkpoint_ref,
        toCheckpointRef=target_ref, attemptOrdinal=head.head_attempt_ordinal,
        stepExecutionId=head.head_step_execution_id, evidenceRef=evidence_ref,
    )
    return updated, transition


def project_head(head: RemediationWorkspaceHead) -> dict[str, Any]:
    """Return the path-free API/UI projection and exact next-action baseline."""
    payload = head.model_dump(by_alias=True, mode="json", exclude_none=True)
    payload["nextActionBaseline"] = {
        "checkpointRef": head.head_checkpoint_ref,
        "workspaceDigest": head.head_workspace_digest,
        "headVersion": head.head_version,
    }
    return payload

