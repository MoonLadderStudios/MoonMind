"""Bounded story/PRD loop contracts and decision helpers."""

from __future__ import annotations

from enum import StrEnum
import hashlib
from typing import Any, Literal, Mapping, Sequence

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


ArtifactRef = str

_FORBIDDEN_INLINE_KEYS = {
    "diff",
    "logs",
    "stdout",
    "stderr",
    "providerpayload",
    "provideroutput",
    "credentials",
    "credential",
    "token",
    "password",
    "verificationreport",
    "remainingwork",
    "inlineevidence",
}
_FORBIDDEN_INLINE_TOKENS = (
    "diff --git",
    "raw stdout",
    "raw stderr",
    "provider payload",
    "credential",
    "private key",
    "token=",
    "password=",
)


class _ContractModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class LoopStopState(StrEnum):
    ACCEPTED = "accepted"
    BLOCKED = "blocked"
    NEEDS_HUMAN = "needs_human"
    FAILED_WITH_REMAINING_WORK = "failed_with_remaining_work"
    FAILED_UNRECOVERABLE = "failed_unrecoverable"


class PublicationAction(StrEnum):
    PR = "pr"
    JIRA = "jira"
    MERGE = "merge"
    DEPLOY = "deploy"
    PROVIDER_ACCOUNT = "provider_account"


class LoopNode(_ContractModel):
    kind: Literal[
        "implementation",
        "verification",
        "remediation",
        "post_remediation_verification",
        "publication_evaluation",
    ]
    selected_item_digest: str = Field(alias="selectedItemDigest")


class LoopBudget(_ContractModel):
    max_attempts: int = Field(alias="maxAttempts", ge=1)
    max_consecutive_no_progress_attempts: int = Field(
        alias="maxConsecutiveNoProgressAttempts", ge=1
    )
    max_repeated_failed_commands: int = Field(alias="maxRepeatedFailedCommands", ge=0)
    max_unsafe_or_policy_denied_attempts: int = Field(
        alias="maxUnsafeOrPolicyDeniedAttempts", ge=0
    )
    max_elapsed_seconds: int | None = Field(
        default=None, alias="maxElapsedSeconds", ge=1
    )
    provider_budget: int | None = Field(default=None, alias="providerBudget", ge=0)
    token_budget: int | None = Field(default=None, alias="tokenBudget", ge=0)
    cost_budget: int | None = Field(default=None, alias="costBudget", ge=0)
    consumed: dict[str, int] = Field(default_factory=dict)

    @field_validator("consumed")
    @classmethod
    def _validate_consumed(cls, value: dict[str, int]) -> dict[str, int]:
        for key, consumed in value.items():
            if not isinstance(consumed, int) or consumed < 0:
                raise ValueError(f"budget counter {key} must be a non-negative integer")
        return value


class CanonicalGap(_ContractModel):
    identity: str
    status: str
    severity: str
    score: int = Field(ge=0)
    scope: str | None = None
    required_check_id: str | None = Field(default=None, alias="requiredCheckId")


class CanonicalCheck(_ContractModel):
    identity: str
    status: str
    target: str | None = None
    failure_signature: str | None = Field(default=None, alias="failureSignature")


class RemediationProgressVector(_ContractModel):
    """Compact semantic evidence used by the durable continuation policy."""

    schema_version: Literal["remediation-progress/v1"] = Field(
        "remediation-progress/v1", alias="schemaVersion"
    )
    loop_id: str = Field(alias="loopId", min_length=1)
    attempt_ordinal: int = Field(alias="attemptOrdinal", ge=1)
    base_workspace_digest: str | None = Field(default=None, alias="baseWorkspaceDigest")
    candidate_workspace_digest: str | None = Field(
        default=None, alias="candidateWorkspaceDigest"
    )
    relevant_diff_digest: str | None = Field(default=None, alias="relevantDiffDigest")
    unresolved_gap_digest: str = Field(alias="unresolvedGapDigest")
    unresolved_gap_score: int = Field(alias="unresolvedGapScore", ge=0)
    required_checks_digest: str = Field(alias="requiredChecksDigest")
    required_checks: dict[str, int] = Field(alias="requiredChecks")
    blocker_code: str | None = Field(default=None, alias="blockerCode")
    new_authoritative_evidence_digest: str | None = Field(
        default=None, alias="newAuthoritativeEvidenceDigest"
    )
    repeated_failure_signatures: tuple[str, ...] = Field(
        default=(), alias="repeatedFailureSignatures"
    )
    candidate_disposition: str | None = Field(
        default=None, alias="candidateDisposition"
    )
    classification: Literal["baseline", "meaningful_progress", "no_progress", "regression"]
    regressions: tuple[str, ...] = ()
    gaps: tuple[CanonicalGap, ...] = ()
    checks: tuple[CanonicalCheck, ...] = ()

    @property
    def digest(self) -> str:
        return _canonical_digest(self.model_dump(by_alias=True, mode="json"))


def build_remediation_progress_vector(
    *,
    loop_id: str,
    attempt_ordinal: int,
    issues: Sequence[Mapping[str, Any]],
    prior: RemediationProgressVector | None = None,
    base_workspace_digest: str | None = None,
    candidate_workspace_digest: str | None = None,
    relevant_diff_digest: str | None = None,
    checks: Sequence[Mapping[str, Any]] = (),
    blocker_code: str | None = None,
    authoritative_evidence_digest: str | None = None,
    candidate_disposition: str | None = None,
) -> RemediationProgressVector:
    """Normalize verifier evidence; refs, prose and runtime identity are excluded."""

    canonical_gaps = tuple(
        sorted((_canonical_gap(issue) for issue in issues), key=lambda gap: gap.identity)
    )
    canonical_checks = tuple(
        sorted(
            (_canonical_check(check) for check in checks),
            key=lambda check: check.identity,
        )
    )
    unresolved = tuple(gap for gap in canonical_gaps if gap.status not in {"passed", "resolved", "satisfied"})
    check_counts = {
        status: sum(check.status == status for check in canonical_checks)
        for status in ("passed", "failed", "not_run")
    }
    failure_signatures = tuple(
        sorted(
            {
                check.failure_signature
                for check in canonical_checks
                if check.status == "failed" and check.failure_signature
            }
        )
    )
    gap_score = sum(gap.score for gap in unresolved)
    classification: Literal["baseline", "meaningful_progress", "no_progress", "regression"] = "baseline"
    regressions: list[str] = []
    if prior is not None:
        previous_gap_ids = {gap.identity for gap in prior.gaps if gap.status not in {"passed", "resolved", "satisfied"}}
        current_gap_ids = {gap.identity for gap in unresolved}
        previous_checks = {check.identity: check.status for check in prior.checks}
        current_checks = {check.identity: check.status for check in canonical_checks}
        if current_gap_ids - previous_gap_ids:
            regressions.append("new_gap_introduced")
        if any(previous_checks.get(key) == "passed" and status == "failed" for key, status in current_checks.items()):
            regressions.append("required_check_regressed")
        if candidate_disposition in {"contaminated", "unsafe_side_effect", "scope_expanded"}:
            regressions.append(candidate_disposition)
        meaningful = bool(
            previous_gap_ids - current_gap_ids
            or gap_score < prior.unresolved_gap_score
            or any(previous_checks.get(key) == "failed" and status == "passed" for key, status in current_checks.items())
            or (
                authoritative_evidence_digest
                and authoritative_evidence_digest != prior.new_authoritative_evidence_digest
            )
            or (
                prior.candidate_disposition == "contaminated"
                and candidate_disposition == "clean"
            )
        )
        classification = "regression" if regressions else ("meaningful_progress" if meaningful else "no_progress")
    return RemediationProgressVector(
        loopId=loop_id,
        attemptOrdinal=attempt_ordinal,
        baseWorkspaceDigest=base_workspace_digest,
        candidateWorkspaceDigest=candidate_workspace_digest,
        relevantDiffDigest=relevant_diff_digest,
        unresolvedGapDigest=_canonical_digest([gap.model_dump(by_alias=True) for gap in unresolved]),
        unresolvedGapScore=gap_score,
        requiredChecksDigest=_canonical_digest([check.model_dump(by_alias=True) for check in canonical_checks]),
        requiredChecks=check_counts,
        blockerCode=_normalize_token(blocker_code),
        newAuthoritativeEvidenceDigest=authoritative_evidence_digest,
        repeatedFailureSignatures=failure_signatures,
        candidateDisposition=_normalize_token(candidate_disposition),
        classification=classification,
        regressions=tuple(regressions),
        gaps=canonical_gaps,
        checks=canonical_checks,
    )


class BoundedStoryLoopInput(_ContractModel):
    selected_item_ref: ArtifactRef = Field(alias="selectedItemRef")
    selected_item_digest: str = Field(alias="selectedItemDigest")
    publish_mode: str | None = Field(default=None, alias="publishMode")
    merge_automation_enabled: bool = Field(default=False, alias="mergeAutomationEnabled")
    budgets: LoopBudget
    additional_item_refs: tuple[ArtifactRef, ...] = Field(
        default=(), alias="additionalItemRefs"
    )

    @field_validator("selected_item_ref")
    @classmethod
    def _selected_item_ref_required(cls, value: str) -> str:
        return _require_ref(value, field_name="selectedItemRef")

    @field_validator("selected_item_digest")
    @classmethod
    def _selected_item_digest_required(cls, value: str) -> str:
        digest = str(value or "").strip()
        if not digest:
            raise ValueError("selectedItemDigest is required")
        return digest

    @model_validator(mode="after")
    def _single_item_only(self) -> "BoundedStoryLoopInput":
        if self.additional_item_refs:
            raise ValueError("bounded story loop accepts exactly one selected item")
        return self


class CompiledBoundedStoryLoop(_ContractModel):
    selected_item_ref: ArtifactRef = Field(alias="selectedItemRef")
    selected_item_digest: str = Field(alias="selectedItemDigest")
    nodes: tuple[LoopNode, ...]

    @field_validator("selected_item_ref")
    @classmethod
    def _compiled_ref_is_ref(cls, value: str) -> str:
        return _require_ref(value, field_name="selectedItemRef")


class TypedGateResult(_ContractModel):
    verdict: Literal[
        "FULLY_IMPLEMENTED",
        "ADDITIONAL_WORK_NEEDED",
        "BLOCKED",
        "NO_DETERMINATION",
        "FAILED_UNRECOVERABLE",
    ]
    terminal_disposition: str = Field(
        default="failed_with_remaining_work", alias="terminalDisposition"
    )
    gate_result_ref: ArtifactRef | None = Field(default=None, alias="gateResultRef")
    remaining_work_ref: ArtifactRef | None = Field(default=None, alias="remainingWorkRef")
    diagnostics_ref: ArtifactRef | None = Field(default=None, alias="diagnosticsRef")
    progress_signature: str | None = Field(default=None, alias="progressSignature")
    progress_vector: RemediationProgressVector | None = Field(
        default=None, alias="progressVector"
    )
    degraded: bool = False

    @field_validator("gate_result_ref", "remaining_work_ref", "diagnostics_ref")
    @classmethod
    def _refs_are_refs(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _require_ref(value, field_name="evidence ref")

    @model_validator(mode="before")
    @classmethod
    def _reject_inline_evidence(cls, data: Any) -> Any:
        if isinstance(data, dict):
            _reject_forbidden_inline_evidence(data)
        return data

    @model_validator(mode="after")
    def _additional_work_requires_durable_evidence(self) -> "TypedGateResult":
        if self.verdict == "ADDITIONAL_WORK_NEEDED" and not self.remaining_work_ref:
            raise ValueError(
                "ADDITIONAL_WORK_NEEDED requires remainingWorkRef durable evidence"
            )
        return self

    @classmethod
    def from_boundary_payload(cls, payload: dict[str, Any]) -> "TypedGateResult":
        raw_verdict = str(payload.get("verdict") or "").strip().upper()
        if raw_verdict not in {
            "FULLY_IMPLEMENTED",
            "ADDITIONAL_WORK_NEEDED",
            "BLOCKED",
            "NO_DETERMINATION",
            "FAILED_UNRECOVERABLE",
        }:
            degraded_payload = {
                "verdict": "NO_DETERMINATION",
                "terminalDisposition": "blocked",
                "diagnosticsRef": payload.get("diagnosticsRef")
                or payload.get("diagnostics_ref"),
                "degraded": True,
            }
            return cls.model_validate(degraded_payload)
        merged = dict(payload)
        merged["verdict"] = raw_verdict
        if raw_verdict == "ADDITIONAL_WORK_NEEDED" and not (
            merged.get("remainingWorkRef") or merged.get("remaining_work_ref")
        ):
            gate_result_ref = merged.get("gateResultRef") or merged.get(
                "gate_result_ref"
            )
            if gate_result_ref:
                merged["remainingWorkRef"] = gate_result_ref
            else:
                return cls.model_validate(
                    {
                        "verdict": "NO_DETERMINATION",
                        "terminalDisposition": "blocked",
                        "diagnosticsRef": merged.get("diagnosticsRef")
                        or merged.get("diagnostics_ref"),
                        "degraded": True,
                    }
                )
        return cls.model_validate(merged)


class LoopAttempt(_ContractModel):
    attempt_ordinal: int = Field(alias="attemptOrdinal", ge=1)
    kind: Literal["implementation", "remediation"]
    step_execution_id: str = Field(alias="stepExecutionId")
    provider_lease_ref: ArtifactRef | None = Field(default=None, alias="providerLeaseRef")
    preflight_decision_ref: ArtifactRef | None = Field(
        default=None, alias="preflightDecisionRef"
    )
    checkpoint_before_ref: ArtifactRef | None = Field(
        default=None, alias="checkpointBeforeRef"
    )
    checkpoint_after_ref: ArtifactRef | None = Field(
        default=None, alias="checkpointAfterRef"
    )
    candidate_diff_ref: ArtifactRef | None = Field(default=None, alias="candidateDiffRef")
    accepted_output_ref: ArtifactRef | None = Field(default=None, alias="acceptedOutputRef")
    gate_result_ref: ArtifactRef | None = Field(default=None, alias="gateResultRef")
    terminal_disposition: str = Field(alias="terminalDisposition")

    @field_validator(
        "provider_lease_ref",
        "preflight_decision_ref",
        "checkpoint_before_ref",
        "checkpoint_after_ref",
        "candidate_diff_ref",
        "accepted_output_ref",
        "gate_result_ref",
    )
    @classmethod
    def _attempt_refs_are_refs(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _require_ref(value, field_name="attempt evidence ref")

    @property
    def commit_allowed(self) -> bool:
        return bool(
            self.terminal_disposition == LoopStopState.ACCEPTED.value
            and self.accepted_output_ref
        )

    @property
    def publication_allowed(self) -> bool:
        return self.commit_allowed


class CandidateWorkspaceHead(_ContractModel):
    """Compact MoonMind-owned authority for one cumulative candidate workspace."""

    schema_version: Literal["v1"] = Field("v1", alias="schemaVersion")
    loop_id: str = Field(alias="loopId", min_length=1)
    attempt_ordinal: int = Field(alias="attemptOrdinal", ge=0)
    checkpoint_ref: ArtifactRef = Field(alias="checkpointRef")
    checkpoint_digest: str = Field(
        alias="checkpointDigest", pattern=r"^sha256:[0-9a-f]{64}$"
    )
    parent_head_digest: str | None = Field(
        default=None,
        alias="parentHeadDigest",
        pattern=r"^sha256:[0-9a-f]{64}$",
    )
    head_digest: str = Field(alias="headDigest", pattern=r"^sha256:[0-9a-f]{64}$")

    @field_validator("checkpoint_ref")
    @classmethod
    def _checkpoint_ref_is_ref(cls, value: str) -> str:
        return _require_ref(value, field_name="checkpointRef")

    @model_validator(mode="after")
    def _validate_chain_identity(self) -> "CandidateWorkspaceHead":
        if self.attempt_ordinal == 0 and self.parent_head_digest is not None:
            raise ValueError("loop root must not declare parentHeadDigest")
        if self.attempt_ordinal > 0 and self.parent_head_digest is None:
            raise ValueError("advanced candidate head requires parentHeadDigest")
        expected = _candidate_head_digest(
            loop_id=self.loop_id,
            attempt_ordinal=self.attempt_ordinal,
            checkpoint_ref=self.checkpoint_ref,
            checkpoint_digest=self.checkpoint_digest,
            parent_head_digest=self.parent_head_digest,
        )
        if self.head_digest != expected:
            raise ValueError("candidate head digest does not match chained workspace identity")
        return self


class VerificationWorkspaceSnapshot(_ContractModel):
    """Artifact-backed identity observed at a verifier projection boundary."""

    candidate_head_digest: str = Field(
        alias="candidateHeadDigest", pattern=r"^sha256:[0-9a-f]{64}$"
    )
    checkpoint_digest: str = Field(
        alias="checkpointDigest", pattern=r"^sha256:[0-9a-f]{64}$"
    )
    workspace_digest: str = Field(
        alias="workspaceDigest", pattern=r"^sha256:[0-9a-f]{64}$"
    )
    projection_ref: ArtifactRef = Field(alias="projectionRef")
    access_mode: Literal["read_only"] = Field("read_only", alias="accessMode")

    @field_validator("projection_ref")
    @classmethod
    def _projection_ref_is_ref(cls, value: str) -> str:
        return _require_ref(value, field_name="projectionRef")


def validate_verification_workspace_integrity(
    *,
    candidate: CandidateWorkspaceHead,
    before: VerificationWorkspaceSnapshot,
    after: VerificationWorkspaceSnapshot,
) -> None:
    """Fail closed unless verification preserved the exact candidate projection."""

    for boundary, snapshot in (("before", before), ("after", after)):
        if snapshot.candidate_head_digest != candidate.head_digest:
            raise ValueError(
                f"verification {boundary} snapshot does not match candidate head"
            )
        if snapshot.checkpoint_digest != candidate.checkpoint_digest:
            raise ValueError(
                f"verification {boundary} snapshot does not match candidate checkpoint"
            )
    if after.projection_ref != before.projection_ref:
        raise ValueError("verification replaced the candidate projection")
    if after.workspace_digest != before.workspace_digest:
        raise ValueError("verification contaminated the candidate workspace")


def advance_candidate_workspace_head(
    *,
    previous: CandidateWorkspaceHead | None,
    loop_id: str,
    attempt_ordinal: int,
    checkpoint_ref: ArtifactRef,
    checkpoint_digest: str,
) -> CandidateWorkspaceHead:
    """Create the only valid next head; only ordinal zero may start at loop root."""

    normalized_loop_id = str(loop_id or "").strip()
    if not normalized_loop_id:
        raise ValueError("loopId is required")
    if previous is None:
        if attempt_ordinal != 0:
            raise ValueError("only the loop root may advance without a previous head")
        parent_digest = None
    else:
        if previous.loop_id != normalized_loop_id:
            raise ValueError("candidate head belongs to a different loop")
        if attempt_ordinal != previous.attempt_ordinal + 1:
            raise ValueError("candidate head attempt must advance exactly once")
        parent_digest = previous.head_digest
    head_digest = _candidate_head_digest(
        loop_id=normalized_loop_id,
        attempt_ordinal=attempt_ordinal,
        checkpoint_ref=checkpoint_ref,
        checkpoint_digest=checkpoint_digest,
        parent_head_digest=parent_digest,
    )
    return CandidateWorkspaceHead(
        loopId=normalized_loop_id,
        attemptOrdinal=attempt_ordinal,
        checkpointRef=checkpoint_ref,
        checkpointDigest=checkpoint_digest,
        parentHeadDigest=parent_digest,
        headDigest=head_digest,
    )


def _candidate_head_digest(
    *,
    loop_id: str,
    attempt_ordinal: int,
    checkpoint_ref: str,
    checkpoint_digest: str,
    parent_head_digest: str | None,
) -> str:
    canonical = "\x00".join(
        (
            "moonmind-candidate-workspace-head-v1",
            loop_id,
            str(attempt_ordinal),
            parent_head_digest or "",
            checkpoint_ref,
            checkpoint_digest,
        )
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


class LoopStopDecision(_ContractModel):
    state: LoopStopState
    reason: str
    latest_producing_step_execution_id: str | None = Field(
        default=None, alias="latestProducingStepExecutionId"
    )
    remaining_work_ref: ArtifactRef | None = Field(default=None, alias="remainingWorkRef")
    diagnostics_ref: ArtifactRef | None = Field(default=None, alias="diagnosticsRef")
    continue_loop: bool = Field(default=False, alias="continueLoop")
    next_attempt_kind: Literal["remediation"] | None = Field(
        default=None, alias="nextAttemptKind"
    )

    @field_validator("remaining_work_ref", "diagnostics_ref")
    @classmethod
    def _stop_refs_are_refs(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _require_ref(value, field_name="stop decision ref")


class PublicationDecision(_ContractModel):
    allowed: bool
    reason: str
    action: PublicationAction
    latest_producing_step_execution_id: str | None = Field(
        default=None, alias="latestProducingStepExecutionId"
    )
    gate_result_ref: ArtifactRef | None = Field(default=None, alias="gateResultRef")
    side_effect_refs: list[ArtifactRef] = Field(
        default_factory=list, alias="sideEffectRefs"
    )

    @field_validator("gate_result_ref")
    @classmethod
    def _gate_result_ref_is_ref(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _require_ref(value, field_name="gateResultRef")

    @field_validator("side_effect_refs")
    @classmethod
    def _side_effect_refs_are_refs(cls, value: list[str]) -> list[str]:
        return [_require_ref(ref, field_name="sideEffectRefs") for ref in value]


class PreflightDecision(_ContractModel):
    allowed: bool
    state: LoopStopState | None = None
    reason: str
    diagnostics_ref: ArtifactRef | None = Field(default=None, alias="diagnosticsRef")
    consumes_attempt_budget: bool = Field(alias="consumesAttemptBudget")

    @field_validator("diagnostics_ref")
    @classmethod
    def _preflight_ref_is_ref(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _require_ref(value, field_name="diagnosticsRef")


class ProviderLeaseDecision(_ContractModel):
    allowed: bool
    queued: bool = False
    reason: str
    lease_ref: ArtifactRef | None = Field(default=None, alias="leaseRef")
    diagnostics_ref: ArtifactRef | None = Field(default=None, alias="diagnosticsRef")

    @field_validator("lease_ref", "diagnostics_ref")
    @classmethod
    def _lease_refs_are_refs(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _require_ref(value, field_name="provider lease ref")


def compile_bounded_story_loop(
    loop_input: BoundedStoryLoopInput,
) -> CompiledBoundedStoryLoop:
    nodes = tuple(
        LoopNode(kind=kind, selectedItemDigest=loop_input.selected_item_digest)
        for kind in (
            "implementation",
            "verification",
            "remediation",
            "post_remediation_verification",
            "publication_evaluation",
        )
    )
    return CompiledBoundedStoryLoop(
        selectedItemRef=loop_input.selected_item_ref,
        selectedItemDigest=loop_input.selected_item_digest,
        nodes=nodes,
    )


def evaluate_attempt_continuation(
    *,
    attempt: LoopAttempt,
    gate: TypedGateResult,
    budget: LoopBudget,
    checkpoint_available: bool,
    policy_allowed: bool,
) -> LoopStopDecision:
    if not policy_allowed:
        return _stop(
            attempt,
            state=LoopStopState.BLOCKED,
            reason="policy_denied",
            diagnostics_ref=gate.diagnostics_ref,
        )
    if not checkpoint_available:
        return _stop(
            attempt,
            state=LoopStopState.FAILED_UNRECOVERABLE,
            reason="checkpoint_unavailable",
            diagnostics_ref=gate.diagnostics_ref,
        )

    budget_reason = _budget_exhaustion_reason(budget)
    if budget_reason:
        return _stop(
            attempt,
            state=LoopStopState.FAILED_WITH_REMAINING_WORK,
            reason=budget_reason,
            remaining_work_ref=gate.remaining_work_ref,
            diagnostics_ref=gate.diagnostics_ref,
        )

    if (
        gate.verdict == "FULLY_IMPLEMENTED"
        and gate.terminal_disposition == LoopStopState.ACCEPTED.value
        and attempt.accepted_output_ref
    ):
        return _stop(
            attempt,
            state=LoopStopState.ACCEPTED,
            reason="accepted_gate_passed",
            diagnostics_ref=gate.diagnostics_ref,
        )
    if gate.verdict == "ADDITIONAL_WORK_NEEDED":
        return LoopStopDecision(
            state=LoopStopState.FAILED_WITH_REMAINING_WORK,
            reason="verification_requested_remediation",
            latestProducingStepExecutionId=attempt.step_execution_id,
            remainingWorkRef=gate.remaining_work_ref,
            diagnosticsRef=gate.diagnostics_ref,
            continueLoop=True,
            nextAttemptKind="remediation",
        )
    if gate.verdict == "BLOCKED":
        return _stop(
            attempt,
            state=LoopStopState.BLOCKED,
            reason="verification_blocked",
            diagnostics_ref=gate.diagnostics_ref,
        )
    if gate.verdict == "FAILED_UNRECOVERABLE":
        return _stop(
            attempt,
            state=LoopStopState.FAILED_UNRECOVERABLE,
            reason="verification_failed_unrecoverable",
            diagnostics_ref=gate.diagnostics_ref,
        )
    return _stop(
        attempt,
        state=LoopStopState.NEEDS_HUMAN,
        reason="no_determination",
        remaining_work_ref=gate.remaining_work_ref,
        diagnostics_ref=gate.diagnostics_ref,
    )


def evaluate_publication_decision(
    *,
    action: PublicationAction,
    latest_attempt: LoopAttempt,
    gate: TypedGateResult,
) -> PublicationDecision:
    if latest_attempt.terminal_disposition != LoopStopState.ACCEPTED.value:
        return PublicationDecision(
            allowed=False,
            action=action,
            reason="latest_step_execution_not_accepted",
            latestProducingStepExecutionId=latest_attempt.step_execution_id,
            gateResultRef=gate.gate_result_ref,
        )
    if gate.verdict != "FULLY_IMPLEMENTED" or gate.terminal_disposition != "accepted":
        return PublicationDecision(
            allowed=False,
            action=action,
            reason="typed_gate_not_accepted",
            latestProducingStepExecutionId=latest_attempt.step_execution_id,
            gateResultRef=gate.gate_result_ref,
        )
    if not latest_attempt.accepted_output_ref:
        return PublicationDecision(
            allowed=False,
            action=action,
            reason="accepted_output_ref_missing",
            latestProducingStepExecutionId=latest_attempt.step_execution_id,
            gateResultRef=gate.gate_result_ref,
        )
    return PublicationDecision(
        allowed=True,
        action=action,
        reason="accepted_latest_step_execution",
        latestProducingStepExecutionId=latest_attempt.step_execution_id,
        gateResultRef=gate.gate_result_ref,
        sideEffectRefs=[latest_attempt.accepted_output_ref],
    )


def evaluate_attempt_preflight(
    checks: dict[str, Any],
    *,
    budget: LoopBudget,
) -> PreflightDecision:
    del budget
    for raw_name, raw_check in checks.items():
        name = _camel_to_reason(str(raw_name))
        if not isinstance(raw_check, dict):
            return PreflightDecision(
                allowed=False,
                state=LoopStopState.BLOCKED,
                reason=f"{name}_preflight_failed",
                consumesAttemptBudget=False,
            )
        if bool(raw_check.get("ok", False)):
            continue
        diagnostics_ref = raw_check.get("diagnosticsRef") or raw_check.get(
            "diagnostics_ref"
        )
        return PreflightDecision(
            allowed=False,
            state=LoopStopState.BLOCKED,
            reason=f"{name}_preflight_failed",
            diagnosticsRef=diagnostics_ref,
            consumesAttemptBudget=False,
        )
    return PreflightDecision(
        allowed=True,
        state=None,
        reason="preflight_passed",
        consumesAttemptBudget=True,
    )


def evaluate_provider_lease(decision: dict[str, Any]) -> ProviderLeaseDecision:
    status = str(decision.get("status") or "").strip().lower()
    lease_ref = decision.get("leaseRef") or decision.get("lease_ref")
    diagnostics_ref = decision.get("diagnosticsRef") or decision.get("diagnostics_ref")
    if status in {"granted", "available"} and not bool(decision.get("stale", False)):
        return ProviderLeaseDecision(
            allowed=True,
            queued=False,
            reason="provider_lease_granted",
            leaseRef=lease_ref,
            diagnosticsRef=diagnostics_ref,
        )
    if status in {"granted", "available"} and bool(decision.get("stale", False)):
        return ProviderLeaseDecision(
            allowed=False,
            queued=False,
            reason="provider_lease_stale",
            leaseRef=lease_ref,
            diagnosticsRef=diagnostics_ref,
        )
    if status == "unavailable":
        return ProviderLeaseDecision(
            allowed=False,
            queued=bool(decision.get("queueWhenUnavailable", False)),
            reason="provider_lease_unavailable",
            leaseRef=lease_ref,
            diagnosticsRef=diagnostics_ref,
        )
    if status == "denied":
        return ProviderLeaseDecision(
            allowed=False,
            queued=False,
            reason="provider_lease_denied",
            leaseRef=lease_ref,
            diagnosticsRef=diagnostics_ref,
        )
    return ProviderLeaseDecision(
        allowed=False,
        queued=False,
        reason="provider_lease_unknown",
        leaseRef=lease_ref,
        diagnosticsRef=diagnostics_ref,
    )


def _budget_exhaustion_reason(budget: LoopBudget) -> str | None:
    consumed = budget.consumed

    def _consumed(*keys: str) -> int:
        """Read canonical counters while accepting workflow-authored snake case."""

        return max((consumed.get(key, 0) for key in keys), default=0)

    checks = (
        (("attempts",), budget.max_attempts, "max_attempts_exhausted"),
        (
            (
                "consecutiveNoProgressAttempts",
                "consecutive_no_progress_attempts",
            ),
            budget.max_consecutive_no_progress_attempts,
            "no_progress_attempts_exhausted",
        ),
        (
            ("repeatedFailedCommands", "repeated_failed_commands"),
            budget.max_repeated_failed_commands,
            "repeated_failed_commands_exhausted",
        ),
    )
    for keys, limit, reason in checks:
        amount = _consumed(*keys)
        if amount >= limit and amount > 0:
            return reason
    if (
        _consumed(
            "unsafeOrPolicyDeniedAttempts",
            "unsafe_or_policy_denied_attempts",
        )
        >= budget.max_unsafe_or_policy_denied_attempts
        and _consumed(
            "unsafeOrPolicyDeniedAttempts",
            "unsafe_or_policy_denied_attempts",
        )
        > 0
    ):
        return "unsafe_policy_attempts_exhausted"
    optional = (
        (
            ("elapsedSeconds", "elapsed_seconds"),
            budget.max_elapsed_seconds,
            "wall_clock_budget_exhausted",
        ),
        (
            ("provider", "provider_budget"),
            budget.provider_budget,
            "provider_budget_exhausted",
        ),
        (("tokens", "token_budget"), budget.token_budget, "token_budget_exhausted"),
        (("cost", "cost_budget"), budget.cost_budget, "cost_budget_exhausted"),
    )
    for keys, limit, reason in optional:
        amount = _consumed(*keys)
        if limit is not None and amount >= limit and amount > 0:
            return reason
    return None


def _stop(
    attempt: LoopAttempt,
    *,
    state: LoopStopState,
    reason: str,
    remaining_work_ref: str | None = None,
    diagnostics_ref: str | None = None,
) -> LoopStopDecision:
    return LoopStopDecision(
        state=state,
        reason=reason,
        latestProducingStepExecutionId=attempt.step_execution_id,
        remainingWorkRef=remaining_work_ref,
        diagnosticsRef=diagnostics_ref,
    )


def _require_ref(value: str, *, field_name: str) -> str:
    ref = str(value or "").strip()
    if not ref:
        raise ValueError(f"{field_name} is required")
    if not ref.startswith("artifact://"):
        raise ValueError(f"{field_name} must be an artifact ref")
    if any(token in ref.lower() for token in _FORBIDDEN_INLINE_TOKENS):
        raise ValueError(f"{field_name} must not contain inline evidence")
    return ref


def _reject_forbidden_inline_evidence(data: dict[str, Any]) -> None:
    for key, value in data.items():
        normalized_key = str(key).replace("_", "").lower()
        if normalized_key in _FORBIDDEN_INLINE_KEYS:
            raise ValueError(f"{key} must be supplied as an artifact ref")
        if isinstance(value, str):
            lowered = value.lower()
            if len(value) > 1000 or any(
                token in lowered for token in _FORBIDDEN_INLINE_TOKENS
            ):
                raise ValueError(f"{key} contains forbidden inline evidence")


def _camel_to_reason(value: str) -> str:
    chars: list[str] = []
    for index, char in enumerate(value):
        if char.isupper() and index > 0:
            chars.append("_")
        chars.append(char.lower())
    return "".join(chars).replace("-", "_")


_SEVERITY_SCORES = {
    "critical": 8,
    "high": 5,
    "medium": 3,
    "low": 1,
    "info": 0,
}


def _normalize_token(value: Any) -> str | None:
    normalized = " ".join(str(value or "").strip().lower().split())
    return normalized.replace(" ", "_") or None


def _canonical_digest(value: Any) -> str:
    encoded = json_dumps_canonical(value).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def json_dumps_canonical(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _canonical_gap(issue: Mapping[str, Any]) -> CanonicalGap:
    requirement = (
        issue.get("requirementId")
        or issue.get("acceptanceCriterionId")
        or issue.get("requirement")
        or issue.get("id")
        or issue.get("code")
    )
    scope = issue.get("scope") or issue.get("component") or issue.get("file")
    check_id = issue.get("requiredCheckId") or issue.get("checkId")
    if not requirement:
        # Structured bounded fields are the fallback identity. Free-form feedback,
        # artifact refs and execution identity are intentionally never included.
        requirement = _canonical_digest(
            {
                "scope": _normalize_token(scope),
                "check": _normalize_token(check_id),
                "category": _normalize_token(issue.get("category") or issue.get("type")),
            }
        )
    severity = _normalize_token(issue.get("severity") or issue.get("priority")) or "medium"
    score_value = issue.get("score")
    score = (
        int(score_value)
        if isinstance(score_value, int) and not isinstance(score_value, bool) and score_value >= 0
        else _SEVERITY_SCORES.get(severity, 3)
    )
    return CanonicalGap(
        identity=str(requirement).strip().lower(),
        status=_normalize_token(issue.get("status")) or "unresolved",
        severity=severity,
        score=score,
        scope=_normalize_token(scope),
        requiredCheckId=_normalize_token(check_id),
    )


def _canonical_check(check: Mapping[str, Any]) -> CanonicalCheck:
    identity = check.get("checkId") or check.get("commandId") or check.get("id")
    target = check.get("target") or check.get("scope")
    if not identity:
        identity = _canonical_digest(
            {
                "target": _normalize_token(target),
                "kind": _normalize_token(check.get("kind") or check.get("type")),
            }
        )
    status = _normalize_token(check.get("status")) or "not_run"
    if status in {"success", "succeeded", "pass"}:
        status = "passed"
    elif status in {"failure", "failed", "error"}:
        status = "failed"
    elif status in {"notrun", "skipped", "pending"}:
        status = "not_run"
    failure = check.get("failureSignature") or check.get("failureClass")
    return CanonicalCheck(
        identity=str(identity).strip().lower(),
        status=status,
        target=_normalize_token(target),
        failureSignature=_normalize_token(failure),
    )
