"""Deterministic contracts for workflow-owned remediation loops.

The models in this module deliberately contain no provider routing logic and no
large evidence payloads.  Activities produce immutable evidence references;
workflow code applies :func:`decide_remediation_continuation` and persists that
single decision as the authority for subsequent projections.

Implementation reference: MoonLadderStudios/MoonMind#3475.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class _Contract(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class RemediationLoopPhase(StrEnum):
    INITIAL_VERIFICATION_PENDING = "initial_verification_pending"
    INITIAL_VERIFICATION_RUNNING = "initial_verification_running"
    INITIAL_VERIFICATION_EVALUATING = "initial_verification_evaluating"
    REMEDIATION_PENDING = "remediation_pending"
    REMEDIATION_RUNNING = "remediation_running"
    CANDIDATE_CAPTURING = "candidate_capturing"
    VERIFICATION_PENDING = "verification_pending"
    VERIFICATION_RUNNING = "verification_running"
    VERIFICATION_EVALUATING = "verification_evaluating"
    CONTINUATION_DECIDING = "continuation_deciding"
    ACCEPTED = "accepted"
    STOPPED_REMAINING_WORK = "stopped_remaining_work"
    BLOCKED = "blocked"
    NEEDS_HUMAN = "needs_human"
    FAILED_UNRECOVERABLE = "failed_unrecoverable"


class ToolDescriptor(_Contract):
    type: Literal["skill", "agent_runtime"] = "skill"
    name: str
    inputs: dict[str, object] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def _name_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("tool name is required")
        return value


class RemediationLoopBudgets(_Contract):
    hard_max_attempts: int = Field(alias="hardMaxAttempts", ge=1)
    max_consecutive_semantic_no_progress: int = Field(
        default=2, alias="maxConsecutiveSemanticNoProgress", ge=1
    )
    max_repeated_failure_signature: int = Field(
        default=2, alias="maxRepeatedFailureSignature", ge=1
    )
    max_evidence_retries: int = Field(default=1, alias="maxEvidenceRetries", ge=0)
    max_contract_repairs: int = Field(default=1, alias="maxContractRepairs", ge=0)
    max_wall_clock_seconds: int | None = Field(
        default=None, alias="maxWallClockSeconds", ge=1
    )
    provider_budget: int | None = Field(default=None, alias="providerBudget", ge=0)
    token_budget: int | None = Field(default=None, alias="tokenBudget", ge=0)
    cost_budget: int | None = Field(default=None, alias="costBudget", ge=0)


class RemediationTerminalPolicy(_Contract):
    fully_implemented: Literal["advance"] = Field(alias="fullyImplemented")
    additional_work_needed: Literal["continue_when_allowed", "stop"] = Field(
        alias="additionalWorkNeeded"
    )
    blocked: Literal["stop"]
    no_determination: Literal["retry_evidence_or_stop", "stop"] = Field(
        alias="noDetermination"
    )
    failed_unrecoverable: Literal["stop"] = Field(alias="failedUnrecoverable")


class RemediationLoopSpec(_Contract):
    kind: Literal["remediation_loop"] = "remediation_loop"
    loop_id: str = Field(alias="loopId")
    remediation_tool: ToolDescriptor = Field(alias="remediationTool")
    verification_tool: ToolDescriptor = Field(alias="verificationTool")
    workspace_policy: Literal["continue_from_loop_head"] = Field(alias="workspacePolicy")
    budgets: RemediationLoopBudgets
    terminal_policy: RemediationTerminalPolicy = Field(alias="terminalPolicy")
    side_effect_policy: Literal["workflow_owned"] = Field(alias="sideEffectPolicy")
    approval_policy: Literal["inherit", "required", "disabled"] = Field(
        default="inherit", alias="approvalPolicy"
    )
    publication_policy: Literal["evaluate_after_terminal"] = Field(
        alias="publicationPolicy"
    )
    continue_as_new_attempt_threshold: int | None = Field(
        default=None, alias="continueAsNewAttemptThreshold", ge=1
    )

    @field_validator("loop_id")
    @classmethod
    def _loop_id_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("loopId is required")
        return value


class ConsumedRemediationBudgets(_Contract):
    attempts: int = Field(default=0, ge=0)
    evidence_retries: int = Field(default=0, alias="evidenceRetries", ge=0)
    contract_repairs: int = Field(default=0, alias="contractRepairs", ge=0)
    activity_retries: int = Field(default=0, alias="activityRetries", ge=0)
    consecutive_semantic_no_progress: int = Field(
        default=0, alias="consecutiveSemanticNoProgress", ge=0
    )
    repeated_failure_signature: int = Field(
        default=0, alias="repeatedFailureSignature", ge=0
    )


class RemediationLoopState(_Contract):
    loop_id: str = Field(alias="loopId")
    attempt_ordinal: int = Field(default=0, alias="attemptOrdinal", ge=0)
    phase: RemediationLoopPhase
    workspace_head_ref: str | None = Field(default=None, alias="workspaceHeadRef")
    latest_verification_ref: str | None = Field(
        default=None, alias="latestVerificationRef"
    )
    consumed_budgets: ConsumedRemediationBudgets = Field(alias="consumedBudgets")
    continuation_decision_ref: str | None = Field(
        default=None, alias="continuationDecisionRef"
    )
    source_run_id: str | None = Field(default=None, alias="sourceRunId")
    plan_digest: str | None = Field(default=None, alias="planDigest")
    capability_digest: str | None = Field(default=None, alias="capabilityDigest")
    latest_verdict: str | None = Field(default=None, alias="latestVerdict")
    latest_progress_ref: str | None = Field(default=None, alias="latestProgressRef")
    continuation_reason: str | None = Field(
        default=None, alias="continuationReason"
    )
    continue_as_new_count: int = Field(default=0, alias="continueAsNewCount", ge=0)

    @model_validator(mode="after")
    def _attempt_counter_matches_ordinal(self) -> "RemediationLoopState":
        if self.attempt_ordinal != self.consumed_budgets.attempts:
            raise ValueError("attemptOrdinal must equal consumedBudgets.attempts")
        return self

    @field_validator(
        "workspace_head_ref",
        "latest_verification_ref",
        "latest_progress_ref",
        "continuation_decision_ref",
    )
    @classmethod
    def _artifact_refs_only(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith("artifact://"):
            raise ValueError("loop evidence must use an artifact:// reference")
        return value


class RemediationContinuationDecision(_Contract):
    loop_id: str = Field(alias="loopId")
    current_attempt: int = Field(alias="currentAttempt", ge=0)
    verdict: Literal[
        "FULLY_IMPLEMENTED",
        "ADDITIONAL_WORK_NEEDED",
        "BLOCKED",
        "NO_DETERMINATION",
        "FAILED_UNRECOVERABLE",
        "ENVIRONMENT_CONTAMINATED_BY_SKILL_PROJECTION",
    ]
    continue_loop: bool = Field(alias="continueLoop")
    reason: str
    next_attempt: int | None = Field(default=None, alias="nextAttempt", ge=1)
    next_phase: RemediationLoopPhase = Field(alias="nextPhase")
    workspace_head_ref: str | None = Field(default=None, alias="workspaceHeadRef")
    gate_result_ref: str = Field(alias="gateResultRef")
    remaining_work_ref: str | None = Field(default=None, alias="remainingWorkRef")
    budget_ref: str | None = Field(default=None, alias="budgetRef")
    progress_ref: str | None = Field(default=None, alias="progressRef")
    retry_kind: Literal["evidence", "contract_repair"] | None = Field(
        default=None, alias="retryKind"
    )

    @model_validator(mode="after")
    def _continuation_has_next_attempt(self) -> "RemediationContinuationDecision":
        if self.continue_loop and self.next_phase == RemediationLoopPhase.REMEDIATION_PENDING:
            if self.next_attempt is None:
                raise ValueError("semantic continuation requires nextAttempt")
        return self


def remediation_step_execution_id(
    workflow_id: str, run_id: str, loop_id: str, kind: Literal["remediation", "verification"], ordinal: int
) -> str:
    """Build the deterministic identity for one semantic loop operation."""

    if ordinal < 1:
        raise ValueError("ordinal must be positive")
    return f"{workflow_id}:{run_id}:{loop_id}:{kind}:{ordinal}"


def start_remediation_attempt(
    state: RemediationLoopState,
) -> RemediationLoopState:
    """Admit one semantic attempt and consume its budget exactly once."""

    if state.phase != RemediationLoopPhase.REMEDIATION_PENDING:
        raise ValueError("remediation can only start from remediation_pending")
    next_attempt = state.attempt_ordinal + 1
    budgets = state.consumed_budgets.model_copy(
        update={"attempts": next_attempt}
    )
    return state.model_copy(
        update={
            "attempt_ordinal": next_attempt,
            "phase": RemediationLoopPhase.REMEDIATION_RUNNING,
            "consumed_budgets": budgets,
        }
    )


def capture_remediation_candidate(
    state: RemediationLoopState, *, workspace_head_ref: str
) -> RemediationLoopState:
    """Atomically advance the workflow-owned candidate before verification."""

    if state.phase not in {
        RemediationLoopPhase.REMEDIATION_RUNNING,
        RemediationLoopPhase.CANDIDATE_CAPTURING,
    }:
        raise ValueError("candidate capture requires a running remediation")
    if not workspace_head_ref.startswith("artifact://"):
        raise ValueError("workspace head must use an artifact:// reference")
    return state.model_copy(
        update={
            "workspace_head_ref": workspace_head_ref,
            "phase": RemediationLoopPhase.VERIFICATION_PENDING,
        }
    )


def start_verification(state: RemediationLoopState) -> RemediationLoopState:
    """Start verifier execution without consuming a remediation attempt."""

    if state.phase not in {
        RemediationLoopPhase.INITIAL_VERIFICATION_PENDING,
        RemediationLoopPhase.VERIFICATION_PENDING,
    }:
        raise ValueError("verification can only start from a pending phase")
    return state.model_copy(update={"phase": RemediationLoopPhase.VERIFICATION_RUNNING})


def record_verification_evidence(
    state: RemediationLoopState, *, verification_ref: str
) -> RemediationLoopState:
    if state.phase != RemediationLoopPhase.VERIFICATION_RUNNING:
        raise ValueError("verification evidence requires a running verifier")
    if not verification_ref.startswith("artifact://"):
        raise ValueError("verification evidence must use an artifact:// reference")
    return state.model_copy(
        update={
            "latest_verification_ref": verification_ref,
            "phase": RemediationLoopPhase.CONTINUATION_DECIDING,
        }
    )


def record_semantic_progress(
    state: RemediationLoopState, *, progress_ref: str | None
) -> RemediationLoopState:
    """Update bounded no-progress counters from authoritative verifier evidence."""

    if not progress_ref:
        return state
    repeated = progress_ref == state.latest_progress_ref
    consumed = state.consumed_budgets.model_copy(
        update={
            "consecutive_semantic_no_progress": (
                state.consumed_budgets.consecutive_semantic_no_progress + 1
                if repeated
                else 0
            ),
            "repeated_failure_signature": (
                state.consumed_budgets.repeated_failure_signature + 1
                if repeated
                else 0
            ),
        }
    )
    return state.model_copy(
        update={
            "latest_progress_ref": progress_ref,
            "consumed_budgets": consumed,
        }
    )


def apply_continuation_decision(
    state: RemediationLoopState,
    *,
    decision: RemediationContinuationDecision,
    decision_ref: str,
) -> RemediationLoopState:
    """Persist and project the one routing authority for all consumers."""

    if state.phase != RemediationLoopPhase.CONTINUATION_DECIDING:
        raise ValueError("continuation decision requires continuation_deciding")
    if decision.loop_id != state.loop_id:
        raise ValueError("continuation decision belongs to another loop")
    if not decision_ref.startswith("artifact://"):
        raise ValueError("continuation decision must use an artifact:// reference")
    budgets = state.consumed_budgets
    if decision.retry_kind == "evidence":
        budgets = budgets.model_copy(
            update={"evidence_retries": budgets.evidence_retries + 1}
        )
    elif decision.retry_kind == "contract_repair":
        budgets = budgets.model_copy(
            update={"contract_repairs": budgets.contract_repairs + 1}
        )
    return state.model_copy(
        update={
            "phase": decision.next_phase,
            "continuation_decision_ref": decision_ref,
            "latest_verdict": decision.verdict,
            "continuation_reason": decision.reason,
            "consumed_budgets": budgets,
        }
    )


def should_continue_as_new(
    *, spec: RemediationLoopSpec, state: RemediationLoopState
) -> bool:
    threshold = spec.continue_as_new_attempt_threshold
    return bool(
        threshold
        and state.attempt_ordinal > 0
        and state.attempt_ordinal % threshold == 0
        and state.phase
        in {
            RemediationLoopPhase.REMEDIATION_PENDING,
            RemediationLoopPhase.VERIFICATION_PENDING,
        }
    )


def project_remediation_loop(
    *, spec: RemediationLoopSpec, state: RemediationLoopState
) -> dict[str, object]:
    """Return the compact API/UI projection; never include evidence bodies."""

    return {
        "loopId": state.loop_id,
        "status": state.phase.value,
        "attemptOrdinal": state.attempt_ordinal,
        "hardMaxAttempts": spec.budgets.hard_max_attempts,
        "workspaceHeadRef": state.workspace_head_ref,
        "latestVerificationRef": state.latest_verification_ref,
        "latestVerdict": state.latest_verdict,
        "continuationDecisionRef": state.continuation_decision_ref,
        "continuationReason": state.continuation_reason,
        "continueAsNewCount": state.continue_as_new_count,
        "sourceRunId": state.source_run_id,
        "consumedBudgets": state.consumed_budgets.model_dump(
            by_alias=True, mode="json"
        ),
    }


def materialize_attempt_nodes(
    *,
    spec: RemediationLoopSpec,
    workflow_id: str,
    run_id: str,
    ordinal: int,
    workspace_head_ref: str | None,
    verification_inputs: Mapping[str, object] | None = None,
) -> tuple[dict[str, object], dict[str, object]]:
    """Build only the admitted attempt pair with deterministic lineage."""

    remediation_id = remediation_step_execution_id(
        workflow_id, run_id, spec.loop_id, "remediation", ordinal
    )
    verification_id = remediation_step_execution_id(
        workflow_id, run_id, spec.loop_id, "verification", ordinal
    )
    common_annotations = {
        "remediationLoopId": spec.loop_id,
        "moonSpecRemediationAttempt": ordinal,
        "moonSpecRemediationMaxAttempts": spec.budgets.hard_max_attempts,
    }
    remediation_inputs = dict(spec.remediation_tool.inputs)
    remediation_inputs.update(
        {
            "remediationLoopId": spec.loop_id,
            "remediationAttempt": ordinal,
            "remediationWorkspaceHeadRef": workspace_head_ref,
        }
    )
    verifier_inputs = dict(spec.verification_tool.inputs)
    verifier_inputs.update(dict(verification_inputs or {}))
    verifier_inputs.update(
        {
            "remediationLoopId": spec.loop_id,
            "remediationAttempt": ordinal,
            "remediationWorkspaceHeadRef": workspace_head_ref,
            "readOnlyWorkspaceHead": True,
        }
    )
    remediation_inputs.setdefault("selectedSkill", spec.remediation_tool.name)
    verifier_inputs.setdefault("selectedSkill", spec.verification_tool.name)
    remediation = {
        "id": remediation_id,
        "title": f"Remediate verification gaps (attempt {ordinal})",
        "tool": {"type": "agent_runtime", "name": "auto"},
        "inputs": remediation_inputs,
        "annotations": {
            **common_annotations,
            "issueImplementRole": "moonspec-remediation",
        },
    }
    verification = {
        "id": verification_id,
        "title": f"Verify remediation (attempt {ordinal})",
        "tool": {"type": "agent_runtime", "name": "auto"},
        "inputs": verifier_inputs,
        "annotations": {
            **common_annotations,
            "issueImplementRole": "moonspec-verification-gate",
        },
        "dependsOn": [remediation_id],
    }
    return remediation, verification


def decide_remediation_continuation(
    *,
    spec: RemediationLoopSpec,
    state: RemediationLoopState,
    verdict: str,
    gate_result_ref: str,
    remaining_work_ref: str | None = None,
    budget_ref: str | None = None,
    progress_ref: str | None = None,
    recoverable_evidence: bool = False,
) -> RemediationContinuationDecision:
    """Return the one deterministic routing decision for verifier evidence."""

    if state.loop_id != spec.loop_id:
        raise ValueError("loop state does not belong to loop specification")
    normalized = verdict.strip().upper()
    common = {
        "loopId": spec.loop_id,
        "currentAttempt": state.attempt_ordinal,
        "verdict": normalized,
        "workspaceHeadRef": state.workspace_head_ref,
        "gateResultRef": gate_result_ref,
        "remainingWorkRef": remaining_work_ref,
        "budgetRef": budget_ref,
        "progressRef": progress_ref,
    }
    if normalized == "FULLY_IMPLEMENTED":
        return RemediationContinuationDecision.model_validate(
            {**common, "continueLoop": False, "reason": "verification_accepted", "nextPhase": "accepted"}
        )
    if normalized == "BLOCKED":
        return RemediationContinuationDecision.model_validate(
            {**common, "continueLoop": False, "reason": "verification_blocked", "nextPhase": "blocked"}
        )
    if normalized == "FAILED_UNRECOVERABLE":
        return RemediationContinuationDecision.model_validate(
            {**common, "continueLoop": False, "reason": "failed_unrecoverable", "nextPhase": "failed_unrecoverable"}
        )
    if normalized == "ENVIRONMENT_CONTAMINATED_BY_SKILL_PROJECTION":
        return RemediationContinuationDecision.model_validate(
            {
                **common,
                "continueLoop": False,
                "reason": "environment_contaminated_by_skill_projection",
                "nextPhase": "blocked",
            }
        )
    if normalized == "NO_DETERMINATION":
        can_retry = (
            recoverable_evidence
            and spec.terminal_policy.no_determination == "retry_evidence_or_stop"
            and state.consumed_budgets.evidence_retries < spec.budgets.max_evidence_retries
        )
        return RemediationContinuationDecision.model_validate(
            {
                **common,
                "continueLoop": can_retry,
                "reason": "recoverable_evidence_retry" if can_retry else "evidence_unavailable",
                "nextPhase": "verification_pending" if can_retry else "needs_human",
                "retryKind": "evidence" if can_retry else None,
            }
        )
    if normalized != "ADDITIONAL_WORK_NEEDED":
        raise ValueError(f"unsupported verifier verdict: {verdict}")

    consumed = state.consumed_budgets
    allowed = (
        spec.terminal_policy.additional_work_needed == "continue_when_allowed"
        and consumed.attempts < spec.budgets.hard_max_attempts
        and consumed.consecutive_semantic_no_progress
        < spec.budgets.max_consecutive_semantic_no_progress
        and consumed.repeated_failure_signature
        < spec.budgets.max_repeated_failure_signature
    )
    reason = "verification_requested_remediation" if allowed else "remediation_budget_or_progress_exhausted"
    return RemediationContinuationDecision.model_validate(
        {
            **common,
            "continueLoop": allowed,
            "reason": reason,
            "nextAttempt": consumed.attempts + 1 if allowed else None,
            "nextPhase": "remediation_pending" if allowed else "stopped_remaining_work",
        }
    )
