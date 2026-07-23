"""Schemas for Temporal execution lifecycle APIs."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_serializer,
    model_validator,
)
from pydantic.json_schema import SkipJsonSchema

from moonmind.schemas.checkpoint_branch_models import StepExecutionBranchMetadataModel
from moonmind.schemas.temporal_artifact_models import CompactArtifactRefModel
from moonmind.schemas.temporal_payload_policy import validate_compact_temporal_mapping
from moonmind.schemas.workspace_locator_models import ExternalStateLocator, WorkspaceLocator
from moonmind.statuses.step_execution import (
    StepExecutionReason,
    StepExecutionStatus,
    StepExecutionTerminalDisposition,
)
from moonmind.statuses.step_ledger import (
    StepLedgerStatusValue,
    step_execution_to_ledger_status,
)
from moonmind.statuses.temporal_status import TemporalStatusValue

SUPPORTED_WORKFLOW_TYPES = (
    "MoonMind.UserWorkflow",
    "MoonMind.ManifestIngest",
    "MoonMind.MergeAutomation",
)
SUPPORTED_FAILURE_POLICIES = (
    "fail_fast",
    "continue_and_report",
    "best_effort",
)
SUPPORTED_UPDATE_NAMES = (
    "UpdateInputs",
    "SetTitle",
    "RequestRerun",
    "UpdateManifest",
    "SetConcurrency",
    "Pause",
    "Resume",
    "CancelNodes",
    "RetryNodes",
    "Cancel",
    "Approve",
)
SUPPORTED_SIGNAL_NAMES = (
    "ExternalEvent",
    "Pause",
    "Resume",
    "Approve",
    "SkipDependencyWait",
    "SendMessage",
    "DependencyResolved",
    "BypassDependencies",
)
# legacy_run contract — persisted memo/search-attribute/parameter keys written by
# MoonMind.UserWorkflow histories; key values rename/are removed at the
# MoonMind.UserWorkflow v2 cutover (MM-730, hard-switch plan §15.2).
AGENT_RUN_ID_MEMO_KEYS = ("agentRunId", "agent_run_id")
AGENT_RUN_ID_SEARCH_ATTR_KEYS = ("mm_agent_run_id",)
AGENT_RUN_ID_PARAM_KEYS = ("agentRunId", "agent_run_id")

STEP_EXECUTION_MANIFEST_CONTENT_TYPE = (
    "application/vnd.moonmind.step-execution+json;version=1"
)
STEP_EXECUTION_CHECKPOINT_CONTENT_TYPE = (
    "application/vnd.moonmind.step-execution-checkpoint+json;version=1"
)

EvidenceCategory = Literal[
    "checkpoint",
    "context",
    "retrieval",
    "memory",
    "gate",
    "side_effect",
    "environment",
    "provider_lease",
    "preflight",
    "sidecar",
    "ghcr",
    "diagnostics",
]
EvidenceStatus = Literal[
    "available",
    "missing",
    "invalid",
    "unauthorized",
    "expired",
    "legacy",
    "incompatible",
    "skipped",
    "unavailable",
]
RecoveryAction = Literal[
    "continue_same_session",
    "resume_from_workspace_checkpoint",
    "full_retry",
    "fix_environment",
    "manual_intervention",
]
RecoveryDefaultAction = RecoveryAction
RecoveryResumePhase = Literal[
    "rerun_failed_step",
    "continue_to_gate",
    "continue_after_gate",
    "continue_to_remediation",
    "resume_publication",
    "retry_restoration",
]
CheckpointRecoveryDisabledReason = Literal[
    "CHECKPOINT_CAPTURE_UNSUPPORTED",
    "CHECKPOINT_RESTORE_UNSUPPORTED",
    "CHECKPOINT_RESTORE_ROUTE_MISSING",
    "CHECKPOINT_KIND_INCOMPATIBLE",
    "CHECKPOINT_BOUNDARY_INCOMPATIBLE",
    "CHECKPOINT_DESTINATION_IDENTITY_MISMATCH",
    "CHECKPOINT_CAPABILITY_SNAPSHOT_MISSING",
    "CHECKPOINT_CAPABILITY_DIGEST_MISMATCH",
    "CHECKPOINT_ARTIFACT_INVALID",
    "CHECKPOINT_SIDE_EFFECT_UNSAFE",
    "RECOVERY_TARGET_UNAVAILABLE",
]
SameSessionRecoveryDisabledReason = Literal[
    "SAME_SESSION_UNREACHABLE",
    "SAME_SESSION_CONTINUATION_UNSUPPORTED",
]
RecoveryDisabledReason = (
    CheckpointRecoveryDisabledReason
    | SameSessionRecoveryDisabledReason
    | Literal["environment_invalid"]
)
RecoveryOperatorGuidance = Literal[
    "continue_same_session",
    "resume_from_workspace_checkpoint",
    "full_retry",
    "fix_environment",
    "manual_intervention",
]
EnvironmentDiagnosticKind = Literal[
    "environment",
    "sidecar",
    "ghcr",
    "preflight",
    "provider_lease",
    "system_error",
]

_FORBIDDEN_RECOVERY_EVIDENCE_TOKENS = (
    "diff --git",
    "raw log",
    "raw stdout",
    "raw stderr",
    "provider payload",
    "token=",
    "password=",
    "checkpoint archive content",
    "raw verification report",
)


def _reject_forbidden_recovery_evidence_text(value: str | None) -> str | None:
    if value is None:
        return None
    lowered = value.lower()
    if any(token in lowered for token in _FORBIDDEN_RECOVERY_EVIDENCE_TOKENS):
        raise ValueError("summary must be bounded, redacted, and ref-only")
    return value


class EvidenceRefStatusModel(BaseModel):
    """Compact ref availability state for one Step Execution evidence category."""

    model_config = ConfigDict(populate_by_name=True)

    category: EvidenceCategory
    status: EvidenceStatus
    artifact_ref: str | None = Field(None, alias="artifactRef", max_length=500)
    boundary: str | None = Field(None, max_length=100)
    reason_code: str | None = Field(None, alias="reasonCode", max_length=120)
    label: str | None = Field(None, max_length=200)
    summary: str | None = Field(None, max_length=500)

    @field_validator("artifact_ref", "boundary", "reason_code", "label", "summary", mode="before")
    @classmethod
    def _strip_optional_text(cls, value: Any) -> str | None:
        if value is None:
            return None
        candidate = str(value).strip()
        return candidate or None

    @field_validator("summary")
    @classmethod
    def _reject_raw_summary(cls, value: str | None) -> str | None:
        return _reject_forbidden_recovery_evidence_text(value)

    @model_validator(mode="after")
    def _validate_available_ref(self) -> "EvidenceRefStatusModel":
        if self.status == "available" and not self.artifact_ref:
            raise ValueError("available evidence requires artifactRef")
        if self.category == "checkpoint" and self.status == "available" and not self.boundary:
            raise ValueError("checkpoint evidence requires boundary")
        if self.status != "available" and not self.reason_code and self.status not in {"skipped"}:
            raise ValueError("unavailable evidence requires reasonCode")
        return self


class GateSummaryStatusModel(BaseModel):
    """Bounded gate verdict summary for Step Execution detail surfaces."""

    model_config = ConfigDict(populate_by_name=True)

    verdict: str | None = Field(None, max_length=120)
    summary: str | None = Field(None, max_length=500)
    artifact_ref: str | None = Field(None, alias="artifactRef", max_length=500)

    @field_validator("verdict", "summary", "artifact_ref", mode="before")
    @classmethod
    def _strip_optional_text(cls, value: Any) -> str | None:
        if value is None:
            return None
        candidate = str(value).strip()
        return candidate or None

    @field_validator("summary")
    @classmethod
    def _reject_raw_summary(cls, value: str | None) -> str | None:
        return _reject_forbidden_recovery_evidence_text(value)


class SideEffectSummaryModel(BaseModel):
    """Ref-only side-effect outcome summary."""

    model_config = ConfigDict(populate_by_name=True)

    status: EvidenceStatus = "skipped"
    artifact_refs: dict[str, str] = Field(default_factory=dict, alias="artifactRefs")
    summary: str | None = Field(None, max_length=500)

    @field_validator("summary")
    @classmethod
    def _reject_raw_summary(cls, value: str | None) -> str | None:
        return _reject_forbidden_recovery_evidence_text(value)


class EnvironmentDiagnosticReferenceModel(BaseModel):
    """Compact environment/system diagnostic ref for recovery decisions."""

    model_config = ConfigDict(populate_by_name=True)

    kind: EnvironmentDiagnosticKind
    status: EvidenceStatus
    diagnostics_ref: str | None = Field(None, alias="diagnosticsRef", max_length=500)
    reason_code: str = Field(..., alias="reasonCode", min_length=1, max_length=120)
    summary: str = Field(..., min_length=1, max_length=500)

    @field_validator("diagnostics_ref", "reason_code", "summary", mode="before")
    @classmethod
    def _strip_text(cls, value: Any) -> str | None:
        if value is None:
            return None
        candidate = str(value).strip()
        return candidate or None

    @field_validator("summary")
    @classmethod
    def _reject_raw_summary(cls, value: str) -> str:
        return _reject_forbidden_recovery_evidence_text(value) or value

    @model_validator(mode="after")
    def _available_requires_ref(self) -> "EnvironmentDiagnosticReferenceModel":
        if self.status == "available" and not self.diagnostics_ref:
            raise ValueError("available diagnostics require diagnosticsRef")
        return self


class PreservedStepProvenanceDetailModel(BaseModel):
    """Ref-backed provenance for steps preserved during recovery."""

    model_config = ConfigDict(populate_by_name=True)

    logical_step_id: str = Field(..., alias="logicalStepId", min_length=1)
    title: str | None = Field(None, max_length=200)
    source_workflow_id: str | None = Field(None, alias="sourceWorkflowId", max_length=200)
    source_run_id: str | None = Field(None, alias="sourceRunId", max_length=200)
    source_execution_ordinal: int | None = Field(
        None, alias="sourceExecutionOrdinal", ge=1
    )
    state_checkpoint_ref: str | None = Field(None, alias="stateCheckpointRef")
    output_refs: dict[str, str] = Field(default_factory=dict, alias="outputRefs")


class RecoveryEligibilityDiagnosticModel(BaseModel):
    """Typed fail-closed checkpoint Resume decision for API and UI surfaces."""

    model_config = ConfigDict(populate_by_name=True)

    eligible: bool
    requested_action: RecoveryAction = Field(
        "resume_from_workspace_checkpoint", alias="requestedAction"
    )
    default_action: RecoveryDefaultAction = Field(..., alias="defaultAction")
    disabled_reason_code: RecoveryDisabledReason | None = Field(
        None, alias="disabledReasonCode", max_length=120
    )
    checkpoint_boundary: str | None = Field(
        None,
        alias="checkpointBoundary",
        validation_alias=AliasChoices("checkpointBoundary", "requiredBoundary"),
        max_length=100,
    )
    resume_phase: RecoveryResumePhase | None = Field(None, alias="resumePhase")
    checkpoint_kind: str | None = Field(None, alias="checkpointKind", max_length=100)
    target_runtime_id: str | None = Field(None, alias="targetRuntimeId", max_length=100)
    capability_set_version: str | None = Field(None, alias="capabilitySetVersion", max_length=100)
    capability_digest: str | None = Field(None, alias="capabilityDigest", max_length=128)
    checkpoint_restore_kinds: tuple[str, ...] = Field(default=(), alias="checkpointRestoreKinds")
    restore_activity: str | None = Field(None, alias="restoreActivity", max_length=200)
    workspace_authority: str | None = Field(None, alias="workspaceAuthority", max_length=100)
    session_recoverable: bool | None = Field(None, alias="sessionRecoverable")
    workspace_recoverable: bool | None = Field(None, alias="workspaceRecoverable")
    authoritative_workspace_checkpoint_kind: str | None = Field(
        None, alias="authoritativeWorkspaceCheckpointKind", max_length=100
    )
    partial_recovery_reason: str | None = Field(
        None, alias="partialRecoveryReason", max_length=240
    )
    checkpoint_ref: str | None = Field(None, alias="checkpointRef", max_length=500)
    source_workflow_id: str | None = Field(None, alias="sourceWorkflowId", max_length=200)
    source_run_id: str | None = Field(None, alias="sourceRunId", max_length=200)
    live_session_id: str | None = Field(None, alias="liveSessionId", max_length=200)
    supports_same_session_continuation: bool | None = Field(
        None, alias="supportsSameSessionContinuation"
    )
    operator_guidance: RecoveryOperatorGuidance = Field(..., alias="operatorGuidance")
    evidence: list[EvidenceRefStatusModel] = Field(default_factory=list)
    runtime_id: str | None = Field(None, alias="runtimeId", max_length=80)
    deployment_generation: str | None = Field(
        None, alias="deploymentGeneration", max_length=120
    )
    capability_set_version: str | None = Field(
        None, alias="capabilitySetVersion", max_length=120
    )
    capability_digest: str | None = Field(
        None, alias="capabilityDigest", max_length=160
    )
    checkpoint_kind: str | None = Field(None, alias="checkpointKind", max_length=80)
    promotion_state: str | None = Field(None, alias="promotionState", max_length=40)

    @model_validator(mode="before")
    @classmethod
    def _read_legacy_recovery_tokens(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            return value
        migrated = dict(value)
        aliases = {
            "resume_from_checkpoint": "resume_from_workspace_checkpoint",
            "environment_fix": "fix_environment",
            "resume": "resume_from_workspace_checkpoint",
            "needs_human": "manual_intervention",
        }
        for key in ("defaultAction", "default_action", "operatorGuidance", "operator_guidance"):
            if migrated.get(key) in aliases:
                migrated[key] = aliases[migrated[key]]
        return migrated

    @field_validator(
        "disabled_reason_code",
        "checkpoint_boundary",
        "checkpoint_kind",
        "target_runtime_id",
        "capability_set_version",
        "capability_digest",
        "restore_activity",
        "checkpoint_ref",
        "source_workflow_id",
        "source_run_id",
        "live_session_id",
        mode="before",
    )
    @classmethod
    def _strip_optional_text(cls, value: Any) -> str | None:
        if value is None:
            return None
        candidate = str(value).strip()
        return candidate or None

    @model_validator(mode="after")
    def _validate_decision(self) -> "RecoveryEligibilityDiagnosticModel":
        if self.requested_action == "continue_same_session":
            if self.checkpoint_ref or self.checkpoint_restore_kinds or self.restore_activity:
                raise ValueError("same-session continuation cannot use checkpoint evidence")
            if self.eligible:
                if not self.live_session_id or not self.supports_same_session_continuation:
                    raise ValueError("same-session continuation requires a reachable capable session")
                if self.default_action != "continue_same_session":
                    raise ValueError("eligible same-session continuation must be the default")
                if self.operator_guidance != "continue_same_session":
                    raise ValueError("eligible same-session continuation requires matching guidance")
                if self.disabled_reason_code is not None:
                    raise ValueError("eligible same-session continuation cannot be disabled")
            else:
                if self.disabled_reason_code not in {
                    "SAME_SESSION_UNREACHABLE",
                    "SAME_SESSION_CONTINUATION_UNSUPPORTED",
                }:
                    raise ValueError("ineligible same-session continuation requires a session reason")
                if self.default_action == "continue_same_session":
                    raise ValueError("ineligible same-session continuation cannot be the default")
            return self
        if self.eligible:
            if self.requested_action != "resume_from_workspace_checkpoint":
                raise ValueError("eligible checkpoint recovery requires checkpoint Resume")
            if self.default_action != "resume_from_workspace_checkpoint":
                raise ValueError("eligible checkpoint recovery must default to checkpoint Resume")
            if not self.checkpoint_ref:
                raise ValueError("eligible checkpoint recovery requires checkpointRef")
            if self.operator_guidance != "resume_from_workspace_checkpoint":
                raise ValueError("eligible checkpoint recovery requires resume guidance")
            # Old Temporal payloads had none of these fields. Continue to read
            # them for replay, while every new decision carrying any v2 proof
            # field must carry the complete immutable proof set.
            proof = (
                self.resume_phase, self.checkpoint_kind, self.target_runtime_id,
                self.capability_set_version, self.capability_digest,
                self.restore_activity, self.workspace_authority,
            )
            if any(proof):
                if not all((self.checkpoint_boundary, *proof)):
                    raise ValueError("checkpoint recovery requires immutable restore proof")
                if self.checkpoint_kind not in self.checkpoint_restore_kinds:
                    raise ValueError("checkpoint kind is absent from restore capability snapshot")
            if self.disabled_reason_code is not None:
                raise ValueError("eligible checkpoint recovery cannot include disabledReasonCode")
        else:
            if not self.disabled_reason_code:
                raise ValueError("ineligible checkpoint recovery requires disabledReasonCode")
            if self.default_action == "resume_from_workspace_checkpoint":
                raise ValueError("ineligible checkpoint recovery cannot default to resume")
        if self.default_action == "fix_environment" and self.operator_guidance != "fix_environment":
            raise ValueError("environment recovery requires fix_environment guidance")
        return self


class StepEvidenceSummaryModel(BaseModel):
    """Grouped compact Step Execution evidence surface."""

    model_config = ConfigDict(populate_by_name=True)

    logical_step_id: str = Field(..., alias="logicalStepId", min_length=1)
    execution_ordinal: int | None = Field(None, alias="executionOrdinal", ge=1)
    checkpoint_refs_by_boundary: dict[str, EvidenceRefStatusModel] = Field(
        default_factory=dict, alias="checkpointRefsByBoundary"
    )
    context_bundle_ref: EvidenceRefStatusModel | None = Field(
        None, alias="contextBundleRef"
    )
    retrieval_manifest_ref: EvidenceRefStatusModel | None = Field(
        None, alias="retrievalManifestRef"
    )
    memory_manifest_ref: EvidenceRefStatusModel | None = Field(
        None, alias="memoryManifestRef"
    )
    gate_summary: GateSummaryStatusModel | None = Field(None, alias="gateSummary")
    terminal_disposition: str | None = Field(None, alias="terminalDisposition")
    side_effect_summary: SideEffectSummaryModel | None = Field(
        None, alias="sideEffectSummary"
    )
    diagnostic_refs: list[EnvironmentDiagnosticReferenceModel] = Field(
        default_factory=list, alias="diagnosticRefs"
    )
StepExecutionSemanticOperation = Literal[
    "retry",
    "reexecute",
    "recover",
    "checkpoint_branch",
]
StepExecutionCheckpointBoundary = Literal[
    "after_prepare",
    "before_execution",
    "after_execution",
    "after_gate",
    "before_publication",
    "before_recovery_restoration",
]
WorkspaceCheckpointKind = Literal[
    "git_commit",
    "git_patch",
    "worktree_archive",
    "ephemeral_workspace_ref",
    "external_state_ref",
]
WorkspacePolicy = Literal[
    "restore_pre_execution",
    "continue_from_previous_execution",
    "apply_previous_execution_diff_to_clean_baseline",
    "start_from_last_passed_commit",
    "fresh_branch_from_source",
]
StepCheckpointValidationFailureCode = Literal[
    "source_mismatch",
    "task_input_mismatch",
    "plan_mismatch",
    "step_mismatch",
    "execution_mismatch",
    "artifact_missing",
    "artifact_unauthorized",
    "artifact_corrupted",
    "workspace_mismatch",
    "checkpoint_kind_incompatible",
    "policy_incompatible",
    "invalid_checkpoint",
    "unsupported_checkpoint_kind",
    "unsafe_checkpoint",
    "workspace_incompatible",
]
WorkspaceCheckpointCaptureStatus = Literal[
    "captured",
    "unsupported",
    "unsafe",
    "invalid",
    "skipped",
]
WorkspacePolicyApplyStatus = Literal[
    "applied",
    "prepared",
    "rejected",
    "unsupported",
    "unsafe",
]

# --- Failed-run recovery manifest (MM-881) -------------------------------
# Every failed run emits one of these before terminal failure is reported so
# operators can resume from the last valid checkpoint or understand exactly why
# resume is blocked, without ever silently degrading a blocked or unvalidated
# checkpoint into a full rerun presented as resume.
FAILED_RUN_RECOVERY_MANIFEST_CONTENT_TYPE = (
    "application/vnd.moonmind.failed-run-recovery+json;version=1"
)
RecoveryCheckpointValidationOutcome = Literal[
    "valid",
    "missing",
    "corrupted",
    "unauthorized",
    "incompatible",
    "invalid",
    "not_evaluated",
]
RecoverySideEffectDisposition = Literal[
    "accepted",
    "discarded",
    "blocked",
    "needs_compensation",
]


class RecoveryCheckpointValidationModel(BaseModel):
    """Checkpoint validation outcome recorded for a failed-run recovery decision.

    ``valid`` means a resumable checkpoint is present and passed structural
    capture-time validation; restore-time re-validation still runs (fail-closed)
    when a resume is actually attempted. Every non-``valid`` outcome blocks
    resume so a failed run cannot silently degrade into a full rerun.
    """

    model_config = ConfigDict(populate_by_name=True)

    result: RecoveryCheckpointValidationOutcome
    # Bounded diagnostic code (typically a StepCheckpointValidationFailureCode);
    # kept as a string so the manifest tolerates newly introduced or unknown
    # provider validation codes while still failing closed on resume.
    failure_code: str | None = Field(None, alias="failureCode", max_length=120)
    checkpoint_ref: str | None = Field(None, alias="checkpointRef", max_length=500)
    boundary: str | None = Field(None, max_length=100)
    summary: str | None = Field(None, max_length=500)

    @field_validator("failure_code", "checkpoint_ref", "boundary", "summary", mode="before")
    @classmethod
    def _strip_optional_text(cls, value: Any) -> str | None:
        if value is None:
            return None
        candidate = str(value).strip()
        return candidate or None

    @field_validator("summary")
    @classmethod
    def _reject_raw_summary(cls, value: str | None) -> str | None:
        return _reject_forbidden_recovery_evidence_text(value)

    @model_validator(mode="after")
    def _validate_outcome(self) -> "RecoveryCheckpointValidationModel":
        if self.result == "valid" and not self.checkpoint_ref:
            raise ValueError("valid checkpoint validation requires a checkpointRef")
        if (
            self.result in {"corrupted", "unauthorized", "incompatible", "invalid"}
            and not self.failure_code
        ):
            raise ValueError(
                "failed checkpoint validation requires a failureCode"
            )
        return self


class RecoverySideEffectDispositionModel(BaseModel):
    """Classification of one observed side effect before resume is allowed."""

    model_config = ConfigDict(populate_by_name=True)

    effect_class: str = Field(..., alias="class", min_length=1, max_length=120)
    operation: str = Field(..., min_length=1, max_length=200)
    disposition: RecoverySideEffectDisposition
    target: str | None = Field(None, max_length=300)
    reason: str | None = Field(None, max_length=300)
    idempotency_key: str | None = Field(None, alias="idempotencyKey", max_length=300)
    compensation_ref: str | None = Field(None, alias="compensationRef", max_length=500)

    @field_validator(
        "effect_class",
        "operation",
        "target",
        "reason",
        "idempotency_key",
        "compensation_ref",
        mode="before",
    )
    @classmethod
    def _strip_text(cls, value: Any) -> str | None:
        if value is None:
            return None
        candidate = str(value).strip()
        return candidate or None

    @field_validator("target", "reason")
    @classmethod
    def _reject_raw_text(cls, value: str | None) -> str | None:
        return _reject_forbidden_recovery_evidence_text(value)


class RecoveryStepRefModel(BaseModel):
    """Compact reference to a logical step execution in the recovery manifest."""

    model_config = ConfigDict(populate_by_name=True)

    logical_step_id: str = Field(..., alias="logicalStepId", min_length=1, max_length=200)
    execution_ordinal: int | None = Field(None, alias="executionOrdinal", ge=1)
    terminal_disposition: str | None = Field(
        None, alias="terminalDisposition", max_length=120
    )
    title: str | None = Field(None, max_length=200)

    @field_validator("title", "terminal_disposition", mode="before")
    @classmethod
    def _strip_optional_text(cls, value: Any) -> str | None:
        if value is None:
            return None
        candidate = str(value).strip()
        return candidate or None


class FailedRunRecoveryManifestModel(BaseModel):
    """Recovery manifest emitted for every failed run before terminal failure.

    Names the last accepted step, the failed logical step and its execution
    ordinal, checkpoint refs, the checkpoint validation result, side-effect
    dispositions, resume allowance, and the blocked reason when resume is not
    allowed. The contract is fail-closed: ``resumeAllowed`` can be ``True`` only
    when checkpoint validation is ``valid`` and a checkpoint ref is present, so a
    failed run can never silently degrade a blocked or unvalidated checkpoint
    into a full rerun presented as resume.
    """

    model_config = ConfigDict(populate_by_name=True)

    schema_version: Literal["v1"] = Field("v1", alias="schemaVersion")
    content_type: Literal[FAILED_RUN_RECOVERY_MANIFEST_CONTENT_TYPE] = Field(
        FAILED_RUN_RECOVERY_MANIFEST_CONTENT_TYPE, alias="contentType"
    )
    workflow_id: str = Field(..., alias="workflowId", min_length=1)
    run_id: str = Field(..., alias="runId", min_length=1)
    failure_stage: str | None = Field(None, alias="failureStage", max_length=120)
    failure_category: str | None = Field(None, alias="failureCategory", max_length=120)
    failed_logical_step_id: str | None = Field(
        None, alias="failedLogicalStepId", max_length=200
    )
    failed_execution_ordinal: int | None = Field(
        None, alias="failedExecutionOrdinal", ge=1
    )
    last_accepted_step: RecoveryStepRefModel | None = Field(
        None, alias="lastAcceptedStep"
    )
    checkpoint_refs: list[EvidenceRefStatusModel] = Field(
        default_factory=list, alias="checkpointRefs"
    )
    validation: RecoveryCheckpointValidationModel
    side_effect_dispositions: list[RecoverySideEffectDispositionModel] = Field(
        default_factory=list, alias="sideEffectDispositions"
    )
    resume_allowed: bool = Field(..., alias="resumeAllowed")
    blocked_reason: str | None = Field(None, alias="blockedReason", max_length=300)
    recovery_eligibility: RecoveryEligibilityDiagnosticModel = Field(
        ..., alias="recoveryEligibility"
    )
    created_at: datetime = Field(..., alias="createdAt")

    @field_validator("failure_stage", "failure_category", "blocked_reason", mode="before")
    @classmethod
    def _strip_optional_text(cls, value: Any) -> str | None:
        if value is None:
            return None
        candidate = str(value).strip()
        return candidate or None

    @field_validator("blocked_reason")
    @classmethod
    def _reject_raw_blocked_reason(cls, value: str | None) -> str | None:
        return _reject_forbidden_recovery_evidence_text(value)

    @model_validator(mode="after")
    def _validate_resume_decision(self) -> "FailedRunRecoveryManifestModel":
        if self.resume_allowed != self.recovery_eligibility.eligible:
            raise ValueError(
                "resumeAllowed must match recoveryEligibility.eligible"
            )
        if self.resume_allowed:
            if self.validation.result != "valid":
                raise ValueError(
                    "resume cannot be allowed unless checkpoint validation is valid"
                )
            if not self.validation.checkpoint_ref:
                raise ValueError(
                    "resume cannot be allowed without a validated checkpoint ref"
                )
            if self.blocked_reason:
                raise ValueError(
                    "resume-allowed recovery manifest must not carry a blockedReason"
                )
        elif not self.blocked_reason:
            raise ValueError("blocked recovery manifest requires a blockedReason")
        return self


PullAuthMode = Literal["authenticated", "anonymous", "unavailable"]
_STEP_EXECUTION_INLINE_EVIDENCE_KEYS = {
    "content",
    "credential",
    "credentials",
    "diff",
    "diagnostics",
    "log",
    "logs",
    "logtext",
    "payload",
    "provideroutput",
    "raw",
    "report",
    "stderr",
    "stdout",
    "verificationreport",
}


def _reject_inline_checkpoint_evidence(value: Any, path: str) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            key_text = str(key)
            normalized = (
                key_text.replace("_", "").replace("-", "").replace(" ", "").lower()
            )
            is_ref_key = normalized.endswith("ref") or normalized.endswith("refs")
            is_summary_key = "summary" in normalized or "message" in normalized
            if (
                (
                    normalized in _STEP_EXECUTION_INLINE_EVIDENCE_KEYS
                    or normalized == "inlinecheckpointpayload"
                )
                and not is_ref_key
                and not is_summary_key
            ):
                raise ValueError(
                    "Step Execution checkpoints must store large evidence as "
                    f"compact refs, not inline values at {path}.{key_text}."
                )
            if isinstance(nested, str) and len(nested) > 1000:
                if not is_ref_key and not is_summary_key:
                    raise ValueError(
                        "Step Execution checkpoints must store large evidence as "
                        f"compact refs, not inline values at {path}.{key_text}."
                    )
            _reject_inline_checkpoint_evidence(nested, f"{path}.{key_text}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _reject_inline_checkpoint_evidence(nested, f"{path}[{index}]")


_SECRET_TEXT_MARKERS = (
    "ghcr_pull_user",
    "ghcr_pull_token",
    "authorization:",
    "bearer ",
    "password=",
    "token=",
    "secret-token",
    "secret-user",
)


def _reject_raw_secret_text(value: Any, path: str) -> None:
    if isinstance(value, str):
        lowered = value.strip().lower()
        if any(marker in lowered for marker in _SECRET_TEXT_MARKERS):
            raise ValueError(f"{path} must contain refs or summaries, not raw credentials")
    elif isinstance(value, dict):
        for key, nested in value.items():
            _reject_raw_secret_text(str(key), f"{path}.{key}.__key__")
            _reject_raw_secret_text(nested, f"{path}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _reject_raw_secret_text(nested, f"{path}[{index}]")

def normalize_dependency_ids(raw_value: Any) -> list[str]:
    """Normalize a list of dependency IDs, stripping whitespace and removing duplicates/non-strings."""
    if not isinstance(raw_value, list):
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for item in raw_value:
        if not isinstance(item, str):
            continue
        candidate = item.strip()
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        normalized.append(candidate)
    return normalized


class StepExecutionIdentityModel(BaseModel):
    """Run-scoped identity for one semantic execution of a logical step."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    workflow_id: str = Field(..., alias="workflowId", min_length=1)
    run_id: str = Field(..., alias="runId", min_length=1)
    logical_step_id: str = Field(..., alias="logicalStepId", min_length=1)
    execution_ordinal: int = Field(..., alias="executionOrdinal", ge=1)


class StepExecutionLineageModel(BaseModel):
    """Optional cross-run provenance for recovered or related executions."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    source_workflow_id: str = Field(..., alias="sourceWorkflowId", min_length=1)
    source_run_id: str = Field(..., alias="sourceRunId", min_length=1)
    source_logical_step_id: str = Field(
        ..., alias="sourceLogicalStepId", min_length=1
    )
    source_execution_ordinal: int = Field(..., alias="sourceExecutionOrdinal", ge=1)
    relationship: str | None = Field(None, alias="relationship")
    lineage_execution_ordinal: int | None = Field(
        None, alias="lineageExecutionOrdinal", ge=1
    )
    preserved_steps: list[PreservedStepProvenanceDetailModel] = Field(
        default_factory=list, alias="preservedSteps"
    )

    @model_serializer(mode="wrap")
    def _serialize_without_empty_preserved_steps(self, handler):
        data = handler(self)
        if not self.preserved_steps:
            data.pop("preservedSteps", None)
            data.pop("preserved_steps", None)
        return data


class StepExecutionSummaryRefModel(BaseModel):
    """Compact workflow-visible reference to artifact-backed execution evidence."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    manifest_artifact_ref: str = Field(..., alias="manifestArtifactRef", min_length=1)
    step_execution_id: str = Field(..., alias="stepExecutionId", min_length=1)
    logical_step_id: str = Field(..., alias="logicalStepId", min_length=1)
    execution_ordinal: int = Field(..., alias="executionOrdinal", ge=1)
    reason: StepExecutionReason = Field(..., alias="reason")
    status: StepExecutionStatus = Field(..., alias="status")
    terminal_disposition: StepExecutionTerminalDisposition | None = Field(
        None, alias="terminalDisposition"
    )
    summary: str | None = Field(None, alias="summary", max_length=1000)


class StepExecutionBranchMetadataModel(BaseModel):
    """Optional Checkpoint Branch lineage attached to a Step Execution manifest."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    branch_id: str = Field(..., alias="branchId", min_length=1)
    branch_turn_id: str = Field(..., alias="branchTurnId", min_length=1)
    root_checkpoint_ref: str | None = Field(
        None, alias="rootCheckpointRef", min_length=1
    )
    source_state_kind: str | None = Field(None, alias="sourceStateKind", min_length=1)
    source_state_ref: str | None = Field(None, alias="sourceStateRef", min_length=1)
    source_state_digest: str | None = Field(
        None, alias="sourceStateDigest", min_length=1
    )
    parent_branch_id: str | None = Field(None, alias="parentBranchId")
    parent_turn_id: str | None = Field(None, alias="parentTurnId")
    git_work_branch: str | None = Field(None, alias="gitWorkBranch")

    @field_validator(
        "branch_id",
        "branch_turn_id",
        "root_checkpoint_ref",
        "source_state_kind",
        "source_state_ref",
        "source_state_digest",
        "parent_branch_id",
        "parent_turn_id",
        "git_work_branch",
        mode="before",
    )
    @classmethod
    def _strip_optional_text(cls, value: Any) -> str | None:
        if value is None:
            return None
        candidate = str(value).strip()
        return candidate or None

    @model_validator(mode="after")
    def _requires_checkpoint_or_typed_state(
        self,
    ) -> "StepExecutionBranchMetadataModel":
        if self.root_checkpoint_ref:
            return self
        if self.source_state_kind and self.source_state_ref:
            return self
        raise ValueError(
            "branch manifest metadata requires rootCheckpointRef or typed sourceStateRef"
        )


class StepExecutionManifestModel(BaseModel):
    """Artifact-backed Step Execution manifest payload."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    schema_version: Literal["v1"] = Field("v1", alias="schemaVersion")
    step_execution_id: str = Field(..., alias="stepExecutionId", min_length=1)
    workflow_id: str = Field(..., alias="workflowId", min_length=1)
    run_id: str = Field(..., alias="runId", min_length=1)
    logical_step_id: str = Field(..., alias="logicalStepId", min_length=1)
    execution_ordinal: int = Field(..., alias="executionOrdinal", ge=1)
    execution_scope: Literal["run"] = Field("run", alias="executionScope")
    lineage: StepExecutionLineageModel | None = Field(None, alias="lineage")
    branch: StepExecutionBranchMetadataModel | None = Field(None, alias="branch")
    reason: StepExecutionReason = Field(..., alias="reason")
    status: StepExecutionStatus = Field(..., alias="status")
    terminal_disposition: StepExecutionTerminalDisposition | None = Field(
        None, alias="terminalDisposition"
    )
    started_at: datetime | None = Field(None, alias="startedAt")
    updated_at: datetime | None = Field(None, alias="updatedAt")
    input: dict[str, Any] | None = Field(default_factory=dict, alias="input")
    context: dict[str, Any] | None = Field(default_factory=dict, alias="context")
    workspace: dict[str, Any] | None = Field(default_factory=dict, alias="workspace")
    execution: dict[str, Any] | None = Field(default_factory=dict, alias="execution")
    outputs: dict[str, Any] | None = Field(default_factory=dict, alias="outputs")
    checks: list[dict[str, Any]] = Field(default_factory=list, alias="checks")
    side_effects: dict[str, Any] | None = Field(
        default_factory=dict, alias="sideEffects"
    )
    dependency_effects: dict[str, Any] | None = Field(
        default_factory=dict, alias="dependencyEffects"
    )
    recovery_source: dict[str, Any] | None = Field(
        default_factory=dict, alias="recoverySource"
    )
    budget: dict[str, Any] | None = Field(default_factory=dict, alias="budget")

    @field_validator(
        "input",
        "context",
        "workspace",
        "execution",
        "outputs",
        "side_effects",
        "dependency_effects",
        "budget",
        mode="before",
    )
    @classmethod
    def _coerce_nullable_mapping(cls, value: Any) -> dict[str, Any]:
        if value is None:
            return {}
        return value

    @field_validator("checks", mode="before")
    @classmethod
    def _coerce_nullable_checks(cls, value: Any) -> list[dict[str, Any]]:
        if value is None:
            return []
        return value

    @model_validator(mode="after")
    def _validate_compact_evidence(self) -> "StepExecutionManifestModel":
        sections = {
            "input": self.input,
            "context": self.context,
            "workspace": self.workspace,
            "execution": self.execution,
            "outputs": self.outputs,
            "checks": self.checks,
            "branch": self.branch.model_dump(by_alias=True) if self.branch else None,
            "sideEffects": self.side_effects,
            "dependencyEffects": self.dependency_effects,
            "recoverySource": self.recovery_source,
            "budget": self.budget,
        }
        for section_name, section_value in sections.items():
            self._reject_inline_evidence(section_value, section_name)
        return self

    @classmethod
    def _reject_inline_evidence(cls, value: Any, path: str) -> None:
        if isinstance(value, dict):
            for key, nested in value.items():
                key_text = str(key)
                normalized = (
                    key_text.replace("_", "")
                    .replace("-", "")
                    .replace(" ", "")
                    .lower()
                )
                is_ref_key = normalized.endswith("ref") or normalized.endswith("refs")
                is_summary_key = "summary" in normalized or "message" in normalized
                if (
                    normalized in _STEP_EXECUTION_INLINE_EVIDENCE_KEYS
                    and not is_ref_key
                    and not is_summary_key
                ):
                    raise ValueError(
                        "Step Execution manifests must store large evidence as "
                        f"compact refs, not inline values at {path}.{key_text}."
                    )
                if isinstance(nested, str) and len(nested) > 1000:
                    if not is_ref_key and not is_summary_key:
                        raise ValueError(
                            "Step Execution manifests must store large evidence as "
                            f"compact refs, not inline values at {path}.{key_text}."
                        )
                cls._reject_inline_evidence(nested, f"{path}.{key_text}")
        elif isinstance(value, list):
            for index, nested in enumerate(value):
                cls._reject_inline_evidence(nested, f"{path}[{index}]")


class StepExecutionBoundaryResultModel(BaseModel):
    """Compact activity/workflow boundary result for step execution manifest operations."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    identity: StepExecutionIdentityModel = Field(..., alias="identity")
    manifest_artifact_ref: str = Field(..., alias="manifestArtifactRef", min_length=1)
    idempotency_key: str = Field(..., alias="idempotencyKey", min_length=1)
    summary: str | None = Field(None, alias="summary", max_length=1000)


class WorkspaceCheckpointEvidenceModel(BaseModel):
    """Compact workspace evidence for restoring or validating a checkpoint."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    kind: WorkspaceCheckpointKind = Field(..., alias="kind")
    base_commit: str | None = Field(None, alias="baseCommit")
    head_commit: str | None = Field(None, alias="headCommit")
    patch_ref: str | None = Field(None, alias="patchRef")
    archive_ref: str | None = Field(None, alias="archiveRef")
    archive_digest: str | None = Field(None, alias="archiveDigest")
    archive_bytes: int | None = Field(None, alias="archiveBytes", ge=1)
    workspace_ref: str | None = Field(None, alias="workspaceRef")
    workspace_artifact_ref: str | None = Field(None, alias="workspaceArtifactRef")
    external_state_ref: str | None = Field(None, alias="externalStateRef")
    idempotency_key: str | None = Field(None, alias="idempotencyKey")
    omnigent_session_id: str | None = Field(None, alias="omnigentSessionId")
    provider_session_ref: str | None = Field(None, alias="providerSessionRef")
    provider_profile_id: str | None = Field(None, alias="providerProfileId")
    credential_generation: int | None = Field(None, alias="credentialGeneration", ge=1)
    provider_lease_ref: str | None = Field(None, alias="providerLeaseRef")
    host_binding_ref: str | None = Field(None, alias="hostBindingRef")
    host_lease_ref: str | None = Field(None, alias="hostLeaseRef")
    endpoint_ref: str | None = Field(None, alias="endpointRef")
    omnigent_host_id: str | None = Field(None, alias="omnigentHostId")
    bridge_session_id: str | None = Field(None, alias="bridgeSessionId")
    terminal_ref: str | None = Field(None, alias="terminalRef")
    diagnostics_ref: str | None = Field(None, alias="diagnosticsRef")
    manifest_ref: str | None = Field(None, alias="manifestRef")
    manifest_digest: str | None = Field(None, alias="manifestDigest")
    branch: str | None = Field(None, alias="branch")
    includes_untracked: bool = Field(False, alias="includesUntracked")
    includes_ignored_files: bool = Field(False, alias="includesIgnoredFiles")
    created_at: datetime | None = Field(None, alias="createdAt")
    expires_at: datetime | None = Field(None, alias="expiresAt")

    @field_validator(
        "base_commit",
        "head_commit",
        "patch_ref",
        "archive_ref",
        "archive_digest",
        "workspace_ref",
        "workspace_artifact_ref",
        "external_state_ref",
        "idempotency_key",
        "omnigent_session_id",
        "provider_session_ref",
        "provider_profile_id",
        "provider_lease_ref",
        "host_binding_ref",
        "host_lease_ref",
        "endpoint_ref",
        "omnigent_host_id",
        "bridge_session_id",
        "terminal_ref",
        "diagnostics_ref",
        "manifest_ref",
        "manifest_digest",
        "branch",
        mode="before",
    )
    @classmethod
    def _normalize_optional_text(cls, value: Any) -> str | None:
        if value is None:
            return None
        candidate = str(value).strip()
        return candidate or None

    @model_validator(mode="after")
    def _validate_kind_evidence(self) -> "WorkspaceCheckpointEvidenceModel":
        if self.kind == "git_commit" and not (
            self.base_commit or self.head_commit
        ):
            raise ValueError(
                "git_commit checkpoint requires baseCommit or headCommit"
            )
        if self.kind == "git_patch" and not (
            self.base_commit and self.patch_ref
        ):
            raise ValueError("git_patch checkpoint requires baseCommit and patchRef")
        if self.kind == "worktree_archive" and not (
            self.archive_ref and self.manifest_ref
        ):
            raise ValueError(
                "worktree_archive checkpoint requires archiveRef and manifestRef"
            )
        if self.kind == "ephemeral_workspace_ref" and not (
            self.workspace_ref or self.workspace_artifact_ref
        ):
            raise ValueError(
                "ephemeral_workspace_ref checkpoint requires workspaceRef or "
                "workspaceArtifactRef"
            )
        if self.kind == "external_state_ref" and not self.external_state_ref:
            raise ValueError("external_state_ref checkpoint requires externalStateRef")
        omnigent_identity = (
            self.provider_profile_id,
            self.credential_generation,
            self.provider_lease_ref,
            self.host_binding_ref,
            self.host_lease_ref,
            self.endpoint_ref,
            self.omnigent_host_id,
            self.omnigent_session_id,
            self.bridge_session_id,
        )
        if any(value is not None for value in omnigent_identity):
            if self.kind != "external_state_ref":
                raise ValueError(
                    "Omnigent checkpoint identity requires external_state_ref evidence"
                )
            required = {
                "providerProfileId": self.provider_profile_id,
                "credentialGeneration": self.credential_generation,
                "hostBindingRef": self.host_binding_ref,
                "endpointRef": self.endpoint_ref,
                "bridgeSessionId": self.bridge_session_id,
                "idempotencyKey": self.idempotency_key,
            }
            missing = [key for key, value in required.items() if value is None]
            if missing:
                raise ValueError(
                    "Omnigent checkpoint identity is missing: " + ", ".join(missing)
                )
        dumped = self.model_dump(by_alias=True, mode="json")
        _reject_inline_checkpoint_evidence(
            dumped,
            "workspace",
        )
        _reject_raw_secret_text(dumped, "workspace")
        return self


class StepCheckpointValidationResultModel(BaseModel):
    """Compact workflow-visible checkpoint validation decision."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    valid: bool = Field(..., alias="valid")
    failure_code: StepCheckpointValidationFailureCode | None = Field(
        None, alias="failureCode"
    )
    message: str = Field(..., alias="message", min_length=1, max_length=1000)
    checkpoint_id: str = Field(..., alias="checkpointId", min_length=1)
    checkpoint_ref: str | None = Field(None, alias="checkpointRef")

    @model_validator(mode="after")
    def _validate_result_shape(self) -> "StepCheckpointValidationResultModel":
        if self.valid and self.failure_code is not None:
            raise ValueError("valid checkpoint result cannot include failureCode")
        if not self.valid and self.failure_code is None:
            raise ValueError("invalid checkpoint result requires failureCode")
        return self


class StepExecutionCheckpointModel(BaseModel):
    """Artifact-backed Step Execution checkpoint payload."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    schema_version: Literal["v1"] = Field("v1", alias="schemaVersion")
    content_type: Literal[STEP_EXECUTION_CHECKPOINT_CONTENT_TYPE] = Field(
        STEP_EXECUTION_CHECKPOINT_CONTENT_TYPE,
        alias="contentType",
    )
    checkpoint_id: str = Field(..., alias="checkpointId", min_length=1)
    checkpoint_kind: Literal["step_boundary"] = Field(
        "step_boundary", alias="checkpointKind"
    )
    boundary: StepExecutionCheckpointBoundary = Field(..., alias="boundary")
    source: StepExecutionIdentityModel = Field(..., alias="source")
    # legacy_run contract — "taskInputSnapshotRef"/"task*" wire keys below (and
    # their python field names) appear in persisted checkpoint/update/signal
    # payloads for MoonMind.UserWorkflow histories; they rename at the
    # MoonMind.UserWorkflow v2 cutover (MM-730).
    task_input_snapshot_ref: str = Field(
        ..., alias="taskInputSnapshotRef", min_length=1
    )
    plan_ref: str | None = Field(None, alias="planRef")
    plan_digest: str | None = Field(None, alias="planDigest")
    prepared_input_refs: list[str] = Field(
        default_factory=list, alias="preparedInputRefs"
    )
    workspace: WorkspaceCheckpointEvidenceModel = Field(..., alias="workspace")
    step_outputs: dict[str, Any] = Field(default_factory=dict, alias="stepOutputs")
    validation: StepCheckpointValidationResultModel | None = Field(
        None, alias="validation"
    )
    created_at: datetime = Field(..., alias="createdAt")

    @field_validator("plan_ref", "plan_digest", mode="before")
    @classmethod
    def _normalize_optional_ref(cls, value: Any) -> str | None:
        if value is None:
            return None
        candidate = str(value).strip()
        return candidate or None

    @field_validator("prepared_input_refs")
    @classmethod
    def _normalize_prepared_input_refs(cls, value: list[str]) -> list[str]:
        return [str(item).strip() for item in value if str(item).strip()]

    @field_validator("step_outputs", mode="before")
    @classmethod
    def _validate_compact_step_outputs(cls, value: Any) -> dict[str, Any]:
        output = validate_compact_temporal_mapping(
            value, field_name="stepOutputs"
        )
        _reject_inline_checkpoint_evidence(output, "stepOutputs")
        return output

    @model_validator(mode="after")
    def _validate_checkpoint_identity(self) -> "StepExecutionCheckpointModel":
        expected = (
            f"{self.source.workflow_id}:{self.source.run_id}:"
            f"{self.source.logical_step_id}:execution:{self.source.execution_ordinal}:"
            f"checkpoint:{self.boundary}"
        )
        if self.checkpoint_id != expected:
            raise ValueError("checkpointId must match Step Execution boundary identity")
        if self.plan_ref is None and self.plan_digest is None:
            raise ValueError("checkpoint requires planRef or planDigest")
        return self


class StepCheckpointValidationRequestModel(BaseModel):
    """Input for validating checkpoint evidence before attempt or Resume execution."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    checkpoint: StepExecutionCheckpointModel = Field(..., alias="checkpoint")
    expected_source: StepExecutionIdentityModel = Field(..., alias="expectedSource")
    expected_task_input_snapshot_ref: str = Field(
        ..., alias="expectedTaskInputSnapshotRef", min_length=1
    )
    expected_plan_ref: str | None = Field(None, alias="expectedPlanRef")
    expected_plan_digest: str | None = Field(None, alias="expectedPlanDigest")
    workspace_policy: WorkspacePolicy | None = Field(None, alias="workspacePolicy")
    required_artifact_refs: list[str] = Field(
        default_factory=list, alias="requiredArtifactRefs"
    )
    unauthorized_artifact_refs: list[str] = Field(
        default_factory=list, alias="unauthorizedArtifactRefs"
    )
    corrupted_artifact_refs: list[str] = Field(
        default_factory=list, alias="corruptedArtifactRefs"
    )
    expected_workspace: dict[str, Any] = Field(
        default_factory=dict, alias="expectedWorkspace"
    )
    checkpoint_ref: str | None = Field(None, alias="checkpointRef")

    @field_validator(
        "expected_plan_ref",
        "expected_plan_digest",
        "checkpoint_ref",
        mode="before",
    )
    @classmethod
    def _normalize_optional_text(cls, value: Any) -> str | None:
        if value is None:
            return None
        candidate = str(value).strip()
        return candidate or None

    @field_validator(
        "required_artifact_refs",
        "unauthorized_artifact_refs",
        "corrupted_artifact_refs",
    )
    @classmethod
    def _normalize_artifact_refs(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            candidate = str(item).strip()
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            normalized.append(candidate)
        return normalized

    @field_validator("expected_workspace", mode="before")
    @classmethod
    def _validate_expected_workspace(cls, value: Any) -> dict[str, Any]:
        return validate_compact_temporal_mapping(
            value, field_name="expectedWorkspace"
        )


class PullAuthDiagnosticsModel(BaseModel):
    """Secret-safe sidecar pull-auth diagnostics for checkpoint evidence."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    mode: PullAuthMode = Field(..., alias="mode")
    diagnostic_refs: list[str] = Field(default_factory=list, alias="diagnosticRefs")

    @model_validator(mode="after")
    def _validate_secret_safe(self) -> "PullAuthDiagnosticsModel":
        _reject_raw_secret_text(self.model_dump(by_alias=True), "pullAuth")
        return self


class WorkspaceCheckpointCaptureInput(BaseModel):
    """Activity input for checkpointing workspace evidence."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    identity: StepExecutionIdentityModel = Field(..., alias="identity")
    boundary: StepExecutionCheckpointBoundary = Field(..., alias="boundary")
    kind: WorkspaceCheckpointKind = Field(..., alias="kind")
    workspace_locator: WorkspaceLocator | None = Field(None, alias="workspaceLocator")
    workspace_root_ref: str | None = Field(None, alias="workspaceRootRef")
    workspace_path: str | None = Field(None, alias="workspacePath")
    external_state_ref: str | None = Field(None, alias="externalStateRef")
    artifact_namespace: str = Field(..., alias="artifactNamespace", min_length=1)
    idempotency_key: str = Field(..., alias="idempotencyKey", min_length=1)
    base_commit: str | None = Field(None, alias="baseCommit")
    include_untracked: bool = Field(False, alias="includeUntracked")
    include_ignored_files: bool = Field(False, alias="includeIgnoredFiles")
    pull_auth_context_ref: str | None = Field(None, alias="pullAuthContextRef")
    provider_lease_context_ref: str | None = Field(None, alias="providerLeaseContextRef")
    skill_projection_policy: dict[str, Any] = Field(
        default_factory=dict, alias="skillProjectionPolicy"
    )

    @field_validator(
        "workspace_root_ref",
        "workspace_path",
        "external_state_ref",
        "base_commit",
        "pull_auth_context_ref",
        "provider_lease_context_ref",
        mode="before",
    )
    @classmethod
    def _normalize_optional_text(cls, value: Any) -> str | None:
        if value is None:
            return None
        candidate = str(value).strip()
        return candidate or None

    @field_validator("skill_projection_policy", mode="before")
    @classmethod
    def _validate_policy(cls, value: Any) -> dict[str, Any]:
        return validate_compact_temporal_mapping(
            value or {}, field_name="skillProjectionPolicy"
        )

    @model_validator(mode="after")
    def _validate_input(self) -> "WorkspaceCheckpointCaptureInput":
        if self.kind == "external_state_ref":
            if (
                not isinstance(self.workspace_locator, ExternalStateLocator)
                and self.external_state_ref is None
                and self.workspace_root_ref is None
            ):
                raise ValueError(
                    "external_state_ref checkpoint capture requires externalStateRef"
                )
        elif (
            self.workspace_locator is None
            and self.workspace_root_ref is None
            and self.workspace_path is None
        ):
            raise ValueError(
                "workspace capture requires workspaceLocator, workspaceRootRef, or workspacePath"
            )
        if self.kind == "git_patch" and self.base_commit is None:
            raise ValueError("git_patch checkpoint capture requires baseCommit")
        _reject_raw_secret_text(self.model_dump(by_alias=True), "captureInput")
        return self


class WorkspaceCheckpointCaptureResult(BaseModel):
    """Compact activity result for captured workspace evidence."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    status: WorkspaceCheckpointCaptureStatus = Field(..., alias="status")
    workspace: WorkspaceCheckpointEvidenceModel | None = Field(None, alias="workspace")
    summary: str | None = Field(None, alias="summary", max_length=1000)
    diagnostic_refs: list[str] = Field(default_factory=list, alias="diagnosticRefs")
    cleanup_refs: list[str] = Field(default_factory=list, alias="cleanupRefs")
    pull_auth: PullAuthDiagnosticsModel = Field(
        default_factory=lambda: PullAuthDiagnosticsModel(mode="unavailable"),
        alias="pullAuth",
    )
    provider_lease_refs: list[str] = Field(
        default_factory=list, alias="providerLeaseRefs"
    )
    failure_code: StepCheckpointValidationFailureCode | None = Field(
        None, alias="failureCode"
    )

    @model_validator(mode="after")
    def _validate_result(self) -> "WorkspaceCheckpointCaptureResult":
        payload = self.model_dump(by_alias=True, mode="json")
        _reject_inline_checkpoint_evidence(payload, "captureResult")
        _reject_raw_secret_text(payload, "captureResult")
        if self.status == "captured" and self.workspace is None:
            raise ValueError("captured checkpoint result requires workspace")
        if self.status != "captured" and self.failure_code is None:
            raise ValueError("non-captured checkpoint result requires failureCode")
        return self


class StepCheckpointCreateInput(BaseModel):
    """Activity input for writing a canonical Step Execution checkpoint artifact."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    identity: StepExecutionIdentityModel = Field(..., alias="identity")
    boundary: StepExecutionCheckpointBoundary = Field(..., alias="boundary")
    task_input_snapshot_ref: str = Field(..., alias="taskInputSnapshotRef", min_length=1)
    workspace: WorkspaceCheckpointEvidenceModel = Field(..., alias="workspace")
    created_at: datetime = Field(..., alias="createdAt")
    plan_ref: str | None = Field(None, alias="planRef")
    plan_digest: str | None = Field(None, alias="planDigest")
    prepared_input_refs: list[str] = Field(default_factory=list, alias="preparedInputRefs")
    step_outputs: dict[str, Any] = Field(default_factory=dict, alias="stepOutputs")
    diagnostic_refs: list[str] = Field(default_factory=list, alias="diagnosticRefs")
    idempotency_key: str = Field(..., alias="idempotencyKey", min_length=1)

    @field_validator("plan_ref", "plan_digest", mode="before")
    @classmethod
    def _normalize_optional_text(cls, value: Any) -> str | None:
        if value is None:
            return None
        candidate = str(value).strip()
        return candidate or None

    @field_validator("prepared_input_refs", "diagnostic_refs")
    @classmethod
    def _normalize_refs(cls, value: list[str]) -> list[str]:
        return [str(item).strip() for item in value if str(item).strip()]

    @field_validator("step_outputs", mode="before")
    @classmethod
    def _validate_outputs(cls, value: Any) -> dict[str, Any]:
        output = validate_compact_temporal_mapping(value or {}, field_name="stepOutputs")
        _reject_inline_checkpoint_evidence(output, "stepOutputs")
        return output

    @model_validator(mode="after")
    def _validate_input(self) -> "StepCheckpointCreateInput":
        if self.plan_ref is None and self.plan_digest is None:
            raise ValueError("checkpoint creation requires planRef or planDigest")
        _reject_raw_secret_text(self.model_dump(by_alias=True, mode="json"), "createInput")
        return self


class StepCheckpointCreateResult(BaseModel):
    """Compact result for a canonical checkpoint artifact write."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    checkpoint_ref: str = Field(..., alias="checkpointRef", min_length=1)
    checkpoint_id: str = Field(..., alias="checkpointId", min_length=1)
    content_type: Literal[STEP_EXECUTION_CHECKPOINT_CONTENT_TYPE] = Field(
        STEP_EXECUTION_CHECKPOINT_CONTENT_TYPE,
        alias="contentType",
    )
    workspace_kind: WorkspaceCheckpointKind = Field(..., alias="workspaceKind")
    summary: str | None = Field(None, alias="summary", max_length=1000)
    diagnostic_refs: list[str] = Field(default_factory=list, alias="diagnosticRefs")
    idempotency_key: str = Field(..., alias="idempotencyKey", min_length=1)

    @model_validator(mode="after")
    def _validate_result(self) -> "StepCheckpointCreateResult":
        _reject_raw_secret_text(self.model_dump(by_alias=True), "createResult")
        return self


class StepCheckpointValidateInput(BaseModel):
    """Activity input for validating checkpoint evidence."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    checkpoint: Any = Field(..., alias="checkpoint")
    expected_source: StepExecutionIdentityModel = Field(..., alias="expectedSource")
    expected_task_input_snapshot_ref: str = Field(
        ..., alias="expectedTaskInputSnapshotRef", min_length=1
    )
    expected_plan_ref: str | None = Field(None, alias="expectedPlanRef")
    expected_plan_digest: str | None = Field(None, alias="expectedPlanDigest")
    workspace_policy: WorkspacePolicy | None = Field(None, alias="workspacePolicy")
    required_artifact_refs: list[str] = Field(
        default_factory=list, alias="requiredArtifactRefs"
    )
    unauthorized_artifact_refs: list[str] = Field(
        default_factory=list, alias="unauthorizedArtifactRefs"
    )
    corrupted_artifact_refs: list[str] = Field(
        default_factory=list, alias="corruptedArtifactRefs"
    )
    expected_workspace: dict[str, Any] = Field(
        default_factory=dict, alias="expectedWorkspace"
    )
    checkpoint_ref: str | None = Field(None, alias="checkpointRef")
    unsupported_artifact_refs: list[str] = Field(
        default_factory=list, alias="unsupportedArtifactRefs"
    )
    unsafe_artifact_refs: list[str] = Field(default_factory=list, alias="unsafeArtifactRefs")
    workspace_incompatible_refs: list[str] = Field(
        default_factory=list, alias="workspaceIncompatibleRefs"
    )

    @field_validator(
        "unsupported_artifact_refs",
        "unsafe_artifact_refs",
        "workspace_incompatible_refs",
        "required_artifact_refs",
        "unauthorized_artifact_refs",
        "corrupted_artifact_refs",
    )
    @classmethod
    def _normalize_extra_refs(cls, value: list[str]) -> list[str]:
        return [str(item).strip() for item in value if str(item).strip()]

    @field_validator("expected_workspace", mode="before")
    @classmethod
    def _validate_expected_workspace(cls, value: Any) -> dict[str, Any]:
        return validate_compact_temporal_mapping(
            value or {}, field_name="expectedWorkspace"
        )


class StepCheckpointValidateResult(StepCheckpointValidationResultModel):
    """Activity validation result with optional diagnostic refs."""

    diagnostic_refs: list[str] = Field(default_factory=list, alias="diagnosticRefs")


class WorkspacePolicyApplyInput(BaseModel):
    """Activity input for preparing or applying checkpoint workspace policy."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    identity: StepExecutionIdentityModel = Field(..., alias="identity")
    workspace_policy: WorkspacePolicy = Field(..., alias="workspacePolicy")
    checkpoint_ref: str = Field(..., alias="checkpointRef", min_length=1)
    checkpoint: dict[str, Any] = Field(default_factory=dict, alias="checkpoint")
    target_workspace_locator: WorkspaceLocator | None = Field(
        None, alias="targetWorkspaceLocator"
    )
    target_workspace_ref: str | None = Field(None, alias="targetWorkspaceRef")
    expected_plan_ref: str | None = Field(None, alias="expectedPlanRef")
    expected_plan_digest: str | None = Field(None, alias="expectedPlanDigest")
    required_artifact_refs: list[str] = Field(default_factory=list, alias="requiredArtifactRefs")
    provider_lease_context_ref: str | None = Field(None, alias="providerLeaseContextRef")
    idempotency_key: str = Field(..., alias="idempotencyKey", min_length=1)

    @field_validator("checkpoint", mode="before")
    @classmethod
    def _validate_checkpoint(cls, value: Any) -> dict[str, Any]:
        checkpoint = validate_compact_temporal_mapping(value or {}, field_name="checkpoint")
        _reject_inline_checkpoint_evidence(checkpoint, "checkpoint")
        return checkpoint

    @model_validator(mode="after")
    def _validate_input(self) -> "WorkspacePolicyApplyInput":
        _reject_raw_secret_text(self.model_dump(by_alias=True), "policyInput")
        return self


class WorkspacePolicyApplyResult(BaseModel):
    """Compact result for policy preparation or application."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    status: WorkspacePolicyApplyStatus = Field(..., alias="status")
    workspace_ref: str | None = Field(None, alias="workspaceRef")
    applied_checkpoint_ref: str | None = Field(None, alias="appliedCheckpointRef")
    diagnostic_refs: list[str] = Field(default_factory=list, alias="diagnosticRefs")
    cleanup_refs: list[str] = Field(default_factory=list, alias="cleanupRefs")
    provider_lease_refs: list[str] = Field(default_factory=list, alias="providerLeaseRefs")
    summary: str | None = Field(None, alias="summary", max_length=1000)
    failure_code: StepCheckpointValidationFailureCode | None = Field(
        None, alias="failureCode"
    )

    @model_validator(mode="after")
    def _validate_result(self) -> "WorkspacePolicyApplyResult":
        _reject_raw_secret_text(self.model_dump(by_alias=True), "policyResult")
        if self.status in {"rejected", "unsupported", "unsafe"} and self.failure_code is None:
            raise ValueError("non-applied policy result requires failureCode")
        return self


class StepExecutionSemanticOperationModel(BaseModel):
    """Explicit semantic operation label that keeps retry, reattempt, and resume distinct."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    kind: StepExecutionSemanticOperation = Field(..., alias="kind")


def build_step_execution_id(identity: StepExecutionIdentityModel) -> str:
    """Build the deterministic identifier for a Step Execution."""

    return (
        f"{identity.workflow_id}:{identity.run_id}:"
        f"{identity.logical_step_id}:execution:{identity.execution_ordinal}"
    )


def build_step_execution_idempotency_key(
    identity: StepExecutionIdentityModel,
    operation: str,
) -> str:
    """Build a deterministic idempotency key for an attempt-scoped side effect."""

    normalized_operation = str(operation or "").strip()
    if not normalized_operation:
        raise ValueError("operation must be a non-empty string")
    return f"{build_step_execution_id(identity)}:{normalized_operation}"


class PullRequestRefModel(BaseModel):
    """Compact pull request identity carried through merge automation history."""

    model_config = ConfigDict(populate_by_name=True)

    repo: str = Field(..., alias="repo")
    number: int = Field(..., alias="number")
    url: str = Field(..., alias="url")
    head_sha: str = Field(..., alias="headSha")
    head_branch: str | None = Field(None, alias="headBranch")
    base_branch: str | None = Field(None, alias="baseBranch")

    @field_validator("repo", "url", "head_sha")
    @classmethod
    def _required_text(cls, value: str) -> str:
        candidate = str(value or "").strip()
        if not candidate:
            raise ValueError("field must be a non-empty string")
        return candidate

    @field_validator("number")
    @classmethod
    def _positive_number(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("number must be positive")
        return value

MergeAutomationPolicySetting = Literal["required", "optional", "disabled"]
MergeMethod = Literal["merge", "squash", "rebase"]
PostMergeJiraStrategy = Literal["done_category"]

class MergeAutomationGitHubGateModel(BaseModel):
    """GitHub readiness policy for merge automation."""

    model_config = ConfigDict(populate_by_name=True)

    checks: MergeAutomationPolicySetting = Field("required", alias="checks")
    automated_review: MergeAutomationPolicySetting = Field(
        "required", alias="automatedReview"
    )

class MergeAutomationJiraGateModel(BaseModel):
    """Jira readiness policy for merge automation."""

    model_config = ConfigDict(populate_by_name=True)

    enabled: bool = Field(False, alias="enabled")
    issue_key: str | None = Field(None, alias="issueKey")
    allowed_statuses: list[str] = Field(default_factory=list, alias="allowedStatuses")
    status: MergeAutomationPolicySetting = Field("optional", alias="status")

class MergeAutomationGateModel(BaseModel):
    """Readiness policy for one merge automation workflow."""

    model_config = ConfigDict(populate_by_name=True)

    github: MergeAutomationGitHubGateModel = Field(
        default_factory=MergeAutomationGitHubGateModel,
        alias="github",
    )
    jira: MergeAutomationJiraGateModel = Field(
        default_factory=MergeAutomationJiraGateModel,
        alias="jira",
    )

class MergeAutomationResolverModel(BaseModel):
    """Resolver configuration for merge automation."""

    model_config = ConfigDict(populate_by_name=True)

    skill: str = Field("pr-resolver", alias="skill")
    merge_method: MergeMethod = Field("squash", alias="mergeMethod")

class MergeAutomationTimeoutsModel(BaseModel):
    """Bounded wait settings for merge automation."""

    model_config = ConfigDict(populate_by_name=True)

    fallback_poll_seconds: int = Field(120, alias="fallbackPollSeconds")
    expire_after_seconds: int | None = Field(None, alias="expireAfterSeconds")

    @field_validator("fallback_poll_seconds", mode="before")
    @classmethod
    def _normalize_fallback_poll_seconds(cls, value: Any) -> int:
        try:
            candidate = int(value)
        except (TypeError, ValueError):
            return 120
        if candidate <= 0:
            return 120
        return min(candidate, 3600)

    @field_validator("expire_after_seconds", mode="before")
    @classmethod
    def _normalize_expire_after_seconds(cls, value: Any) -> int | None:
        if value is None or value == "":
            return None
        try:
            candidate = int(value)
        except (TypeError, ValueError):
            return None
        if candidate <= 0:
            return None
        return min(candidate, 2_592_000)

class MergeAutomationPostMergeJiraModel(BaseModel):
    """Runtime policy for Jira completion after verified merge success."""

    model_config = ConfigDict(populate_by_name=True)

    enabled: bool = Field(False, alias="enabled")
    issue_key: str | None = Field(None, alias="issueKey")
    transition_id: str | None = Field(None, alias="transitionId")
    transition_name: str | None = Field(None, alias="transitionName")
    strategy: PostMergeJiraStrategy = Field("done_category", alias="strategy")
    required: bool = Field(True, alias="required")
    fields: dict[str, Any] = Field(default_factory=dict, alias="fields")

    @field_validator("issue_key", "transition_id", "transition_name", mode="before")
    @classmethod
    def _normalize_optional_text(cls, value: Any) -> str | None:
        if value is None:
            return None
        candidate = str(value).strip()
        return candidate or None

    @field_validator("fields", mode="before")
    @classmethod
    def _validate_fields(cls, value: Any) -> dict[str, Any]:
        return validate_compact_temporal_mapping(value, field_name="postMergeJira.fields")


class MergeAutomationPostMergeGithubModel(BaseModel):
    """Runtime policy for GitHub issue completion after verified merge success."""

    model_config = ConfigDict(populate_by_name=True)

    enabled: bool = Field(False, alias="enabled")
    repository: str | None = Field(None, alias="repository")
    issue_number: int | None = Field(None, alias="issueNumber")
    required: bool = Field(True, alias="required")

    @field_validator("repository", mode="before")
    @classmethod
    def _normalize_repository(cls, value: Any) -> str | None:
        if value is None:
            return None
        candidate = str(value).strip()
        return candidate or None

    @field_validator("issue_number", mode="before")
    @classmethod
    def _normalize_issue_number(cls, value: Any) -> int | None:
        if value is None or value == "":
            return None
        try:
            candidate = int(value)
        except (TypeError, ValueError):
            return None
        return candidate if candidate > 0 else None

    @model_validator(mode="after")
    def _require_identity_when_enabled(self) -> "MergeAutomationPostMergeGithubModel":
        if self.enabled and (not self.repository or self.issue_number is None):
            raise ValueError(
                "Enabled postMergeGithub requires repository and issueNumber."
            )
        return self


class MergeAutomationConfigModel(BaseModel):
    """Full merge automation configuration carried by workflow input."""

    model_config = ConfigDict(populate_by_name=True)

    gate: MergeAutomationGateModel = Field(
        default_factory=MergeAutomationGateModel,
        alias="gate",
    )
    resolver: MergeAutomationResolverModel = Field(
        default_factory=MergeAutomationResolverModel,
        alias="resolver",
    )
    timeouts: MergeAutomationTimeoutsModel = Field(
        default_factory=MergeAutomationTimeoutsModel,
        alias="timeouts",
    )
    post_merge_jira: MergeAutomationPostMergeJiraModel = Field(
        default_factory=MergeAutomationPostMergeJiraModel,
        alias="postMergeJira",
    )
    post_merge_github: MergeAutomationPostMergeGithubModel = Field(
        default_factory=MergeAutomationPostMergeGithubModel,
        alias="postMergeGithub",
    )

ReadinessBlockerKind = Literal[
    "checks_running",
    "checks_failed",
    "merge_conflict",
    "automated_review_pending",
    "jira_status_pending",
    "pull_request_closed",
    "stale_revision",
    "policy_denied",
    "external_state_unavailable",
    "manual_review",
    "failed",
    "resolver_disposition_invalid",
    "resolver_continuation_invalid",
]

class ReadinessBlockerModel(BaseModel):
    """Bounded operator-visible reason a merge gate is not open."""

    model_config = ConfigDict(populate_by_name=True)

    kind: ReadinessBlockerKind = Field(..., alias="kind")
    summary: str = Field(..., alias="summary")
    retryable: bool = Field(True, alias="retryable")
    source: str | None = Field(None, alias="source")

    @field_validator("summary")
    @classmethod
    def _summary_required(cls, value: str) -> str:
        candidate = str(value or "").strip()
        if not candidate:
            raise ValueError("summary must be a non-empty string")
        return candidate[:500]

class ReadinessEvidenceModel(BaseModel):
    """Compact result of evaluating external PR readiness."""

    model_config = ConfigDict(populate_by_name=True)

    head_sha: str = Field(..., alias="headSha")
    ready: bool = Field(False, alias="ready")
    pull_request_open: bool | None = Field(None, alias="pullRequestOpen")
    pull_request_merged: bool | None = Field(None, alias="pullRequestMerged")
    checks_complete: bool | None = Field(None, alias="checksComplete")
    checks_passing: bool | None = Field(None, alias="checksPassing")
    automated_review_complete: bool | None = Field(
        None, alias="automatedReviewComplete"
    )
    jira_status_allowed: bool | None = Field(None, alias="jiraStatusAllowed")
    policy_allowed: bool | None = Field(None, alias="policyAllowed")
    blockers: list[ReadinessBlockerModel] = Field(default_factory=list, alias="blockers")

    @field_validator("head_sha")
    @classmethod
    def _head_sha_required(cls, value: str) -> str:
        candidate = str(value or "").strip()
        if not candidate:
            raise ValueError("headSha must be a non-empty string")
        return candidate

    @model_validator(mode="after")
    def _ready_requires_no_blockers(self) -> "ReadinessEvidenceModel":
        if self.ready and self.blockers:
            raise ValueError("ready evidence cannot include blockers")
        return self

class ResolverRunRefModel(BaseModel):
    """Reference to the resolver follow-up run launched by merge automation."""

    model_config = ConfigDict(populate_by_name=True)

    workflow_id: str = Field(..., alias="workflowId")
    run_id: str | None = Field(None, alias="runId")
    created: bool = Field(True, alias="created")

class MergeAutomationStartInput(BaseModel):
    """Start input for ``MoonMind.MergeAutomation``."""

    model_config = ConfigDict(populate_by_name=True)

    workflow_type: Literal["MoonMind.MergeAutomation"] = Field(..., alias="workflowType")
    parent_workflow_id: str = Field(..., alias="parentWorkflowId")
    parent_run_id: str | None = Field(None, alias="parentRunId")
    principal: str | None = Field(None, alias="principal")
    publish_context_ref: str = Field(..., alias="publishContextRef")
    pull_request: PullRequestRefModel = Field(..., alias="pullRequest")
    jira_issue_key: str | None = Field(None, alias="jiraIssueKey")
    config: MergeAutomationConfigModel = Field(
        default_factory=MergeAutomationConfigModel,
        alias="mergeAutomationConfig",
    )
    resolver_template: dict[str, Any] = Field(
        default_factory=dict,
        alias="resolverTemplate",
    )
    blockers: list[ReadinessBlockerModel] = Field(default_factory=list, alias="blockers")
    cycle_count: int = Field(0, alias="cycleCount")
    resolver_history: list[ResolverRunRefModel] = Field(
        default_factory=list,
        alias="resolverHistory",
    )
    expire_at: str | None = Field(None, alias="expireAt")
    idempotency_key: str | None = Field(None, alias="idempotencyKey")

    @field_validator("parent_workflow_id", "publish_context_ref")
    @classmethod
    def _required_text(cls, value: str) -> str:
        candidate = str(value or "").strip()
        if not candidate:
            raise ValueError("field must be a non-empty string")
        return candidate

    @field_validator("cycle_count")
    @classmethod
    def _non_negative_cycle_count(cls, value: int) -> int:
        if value < 0:
            raise ValueError("cycleCount must be non-negative")
        return value


class PRResolverPolicyModel(BaseModel):
    """Bounded retry and no-progress policy for ``MoonMind.PRResolver``."""

    model_config = ConfigDict(populate_by_name=True)

    poll_interval_seconds: int = Field(60, alias="pollIntervalSeconds", ge=1, le=3600)
    max_elapsed_seconds: int = Field(7200, alias="maxElapsedSeconds", ge=1, le=86400)
    max_finalize_attempts: int = Field(60, alias="maxFinalizeAttempts", ge=1, le=500)
    max_remediations_per_type: int = Field(
        2, alias="maxRemediationsPerType", ge=1, le=20
    )
    max_identical_blockers_without_progress: int = Field(
        2, alias="maxIdenticalBlockersWithoutProgress", ge=1, le=20
    )
    checks: Literal["required", "optional", "disabled"] = "required"
    automated_review: Literal["required", "optional", "disabled"] = Field(
        "required", alias="automatedReview"
    )


class PRResolverStartInput(BaseModel):
    """Compact durable input for ``MoonMind.PRResolver``."""

    model_config = ConfigDict(populate_by_name=True)

    workflow_type: Literal["MoonMind.PRResolver"] = Field(..., alias="workflowType")
    parent_workflow_id: str = Field(..., alias="parentWorkflowId")
    parent_run_id: str | None = Field(None, alias="parentRunId")
    principal: str = Field(..., alias="principal")
    repository: str = Field(..., alias="repository")
    pr_number: int = Field(..., alias="prNumber", gt=0)
    pr_url: str = Field(..., alias="prUrl")
    merge_method: MergeMethod = Field("squash", alias="mergeMethod")
    head_sha: str | None = Field(None, alias="headSha")
    base_sha: str | None = Field(None, alias="baseSha")
    step_id: str = Field(..., alias="stepId")
    correlation_id: str = Field(..., alias="correlationId")
    base_agent_request: dict[str, Any] = Field(..., alias="baseAgentRequest")
    blocker_snapshot_refs: list[str] = Field(
        default_factory=list, alias="blockerSnapshotRefs"
    )
    policy: PRResolverPolicyModel = Field(
        default_factory=PRResolverPolicyModel, alias="policy"
    )
    shadow_mode: bool = Field(False, alias="shadowMode")
    owned_by_merge_automation_gate: bool = Field(
        False, alias="ownedByMergeAutomationGate"
    )
    implementation_identity: dict[str, Any] = Field(
        default_factory=dict, alias="implementationIdentity"
    )
    worker_capability: dict[str, Any] = Field(
        default_factory=dict, alias="workerCapability"
    )
    canary_mode: bool = Field(False, alias="canaryMode")

    @field_validator(
        "parent_workflow_id",
        "principal",
        "repository",
        "pr_url",
        "step_id",
        "correlation_id",
    )
    @classmethod
    def _required_pr_resolver_text(cls, value: str) -> str:
        candidate = str(value or "").strip()
        if not candidate:
            raise ValueError("field must be a non-empty string")
        return candidate

    @field_validator("base_agent_request", mode="after")
    @classmethod
    def _compact_base_agent_request(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_compact_temporal_mapping(
            value, field_name="baseAgentRequest"
        )


class PRResolverTerminalResultModel(BaseModel):
    """Canonical terminal resolver evidence returned to existing consumers."""

    model_config = ConfigDict(populate_by_name=True)

    schema_version: Literal[3] = Field(3, alias="schemaVersion")
    status: Literal["merged", "already_merged", "manual_review", "failed", "canceled"]
    merge_outcome: Literal[
        "merged", "already_merged", "manual_review", "failed", "canceled"
    ] = Field(..., alias="mergeOutcome")
    merge_automation_disposition: Literal[
        "merged", "already_merged", "manual_review", "failed"
    ] = Field(..., alias="mergeAutomationDisposition")
    reason: str = Field(..., alias="reason")
    reason_code: str = Field(..., alias="reasonCode")
    next_step: str = Field(..., alias="nextStep")
    repository: str
    pr_number: int = Field(..., alias="prNumber")
    pr_url: str = Field(..., alias="prUrl")
    verified_head_sha: str | None = Field(None, alias="verifiedHeadSha")
    verified_merge_sha: str | None = Field(None, alias="verifiedMergeSha")
    finalize_attempt_count: int = Field(..., alias="finalizeAttemptCount")
    remediation_counts: dict[str, int] = Field(
        default_factory=dict, alias="remediationCounts"
    )
    attempt_summary_refs: list[str] = Field(
        default_factory=list, alias="attemptSummaryRefs"
    )
    publish_evidence_ref: str | None = Field(None, alias="publishEvidenceRef")
    latest_snapshot: dict[str, Any] = Field(default_factory=dict, alias="latestSnapshot")
    timeline: list[dict[str, Any]] = Field(default_factory=list, alias="timeline")
    workflow_id: str = Field(..., alias="workflowId")
    step_id: str = Field(..., alias="stepId")
    correlation_id: str = Field(..., alias="correlationId")
    implementation_contract: str | None = Field(
        None, alias="implementationContract"
    )
    resolver_core_version: str | None = Field(None, alias="resolverCoreVersion")
    resolver_core_digest: str | None = Field(None, alias="resolverCoreDigest")
    resolved_skill_content_digest: str | None = Field(
        None, alias="resolvedSkillContentDigest"
    )
    resolved_skill_source: str | None = Field(None, alias="resolvedSkillSource")
    worker_build_sha: str | None = Field(None, alias="workerBuildSha")
    worker_image_digest: str | None = Field(None, alias="workerImageDigest")
    worker_deployment_id: str | None = Field(None, alias="workerDeploymentId")
    workflow_registry_fingerprint: str | None = Field(
        None, alias="workflowRegistryFingerprint"
    )
    task_queue: str | None = Field(None, alias="taskQueue")
    workflow_type: str | None = Field(None, alias="workflowType")

class DependencyResolvedSignalPayload(BaseModel):
    """Signal payload emitted when a prerequisite execution reaches a terminal state."""

    model_config = ConfigDict(populate_by_name=True)

    prerequisite_workflow_id: str = Field(..., alias="prerequisiteWorkflowId")
    terminal_state: str = Field(..., alias="terminalState")
    close_status: str | None = Field(None, alias="closeStatus")
    resolved_at: datetime = Field(..., alias="resolvedAt")
    failure_category: str | None = Field(None, alias="failureCategory")
    message: str | None = Field(None, alias="message")

from moonmind.schemas.manifest_ingest_models import (
    ManifestExecutionPolicyModel,
    ManifestNodeCountsModel,
    RequestedByModel,
)

NormalizedIntegrationStatus = Literal[
    "queued",
    "running",
    "completed",
    "failed",
    "canceled",
    "unknown",
]

class ScheduleParameters(BaseModel):
    """Inline scheduling parameters for deferred or recurring execution."""

    model_config = ConfigDict(populate_by_name=True)

    mode: Literal["once", "recurring"] = Field(..., alias="mode")

    # mode=once fields
    scheduled_for: Optional[datetime] = Field(None, alias="scheduledFor")

    # mode=recurring fields
    name: Optional[str] = Field(None, alias="name")
    description: Optional[str] = Field(None, alias="description")
    cron: Optional[str] = Field(None, alias="cron")
    timezone: Optional[str] = Field(None, alias="timezone")
    enabled: bool = Field(True, alias="enabled")
    scope_type: Optional[Literal["personal", "global"]] = Field(
        None, alias="scopeType"
    )
    policy: Optional[dict[str, Any]] = Field(None, alias="policy")

    @model_validator(mode="after")
    def _validate_mode_fields(self) -> "ScheduleParameters":
        if self.mode == "once" and self.scheduled_for is None:
            raise ValueError(
                "scheduledFor is required when schedule mode is 'once'"
            )
        if self.mode == "recurring" and not self.cron:
            raise ValueError(
                "cron is required when schedule mode is 'recurring'"
            )
        return self

USER_WORKFLOW_PLAN_SOURCE_ERROR = (
    "MoonMind.UserWorkflow requires non-empty instructions, a selected skill, "
    "inputArtifactRef, or planArtifactRef before a workflow can be started"
)


def _has_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _as_dict(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    for method_name in ("model_dump", "dict"):
        method = getattr(value, method_name, None)
        if not callable(method):
            continue
        try:
            dumped = method()
        except TypeError:
            continue
        if isinstance(dumped, dict):
            return dumped
    return None


def _has_artifact_ref(value: Any) -> bool:
    if _has_text(value):
        return True
    ref = _as_dict(value)
    if ref is None:
        return False
    return _has_text(ref.get("artifact_id")) or _has_text(ref.get("artifactId"))


def _has_skill_selector(value: Any) -> bool:
    if _has_text(value):
        return True
    if isinstance(value, list):
        return any(_has_skill_selector(item) for item in value)
    selector = _as_dict(value)
    if selector is None:
        return False
    return (
        _has_text(selector.get("name"))
        or _has_text(selector.get("id"))
        or _has_text(selector.get("skill"))
        or _has_text(selector.get("skillName"))
        or _has_skill_selector(selector.get("include"))
        or _has_skill_selector(selector.get("sets"))
    )


def _has_nonempty_sequence(value: Any) -> bool:
    if not isinstance(value, list):
        return False
    for item in value:
        if item is None:
            continue
        if isinstance(item, str) and not item.strip():
            continue
        return True
    return False


def _has_structured_workflow_source(mapping: dict[str, Any]) -> bool:
    if "remediation" in mapping and mapping.get("remediation") is not None:
        return True
    if _has_nonempty_sequence(mapping.get("dependsOn")):
        return True
    return False


def _mapping_has_skill_or_step_source(mapping: dict[str, Any]) -> bool:
    for key in (
        "skill",
        "selectedSkill",
        "selected_skill",
        "targetSkill",
        "target_skill",
        "skills",
        "skillSelection",
        "skill_selection",
    ):
        if _has_skill_selector(mapping.get(key)):
            return True

    if _has_structured_workflow_source(mapping):
        return True

    tool = _as_dict(mapping.get("tool"))
    if tool is not None:
        if str(tool.get("type") or "").strip().lower() == "skill":
            if _has_skill_selector(tool) or _has_skill_selector(tool.get("skill")):
                return True

    steps = mapping.get("steps")
    if isinstance(steps, list):
        for step in steps:
            step_mapping = _as_dict(step)
            if step_mapping is None:
                continue
            if _has_text(step_mapping.get("instructions")):
                return True
            if _mapping_has_skill_or_step_source(step_mapping):
                return True

    return False


def has_user_workflow_plan_source(
    *,
    initial_parameters: dict[str, Any] | None,
    input_artifact_ref: Any,
    plan_artifact_ref: Any,
) -> bool:
    """Return whether a UserWorkflow start has enough planning input to run."""

    if _has_artifact_ref(input_artifact_ref) or _has_artifact_ref(plan_artifact_ref):
        return True

    params = _as_dict(initial_parameters) or {}
    payloads: list[dict[str, Any]] = [params]
    for key in ("workflow", "task", "inputs", "parameters"):
        payload = _as_dict(params.get(key))
        if payload is not None:
            payloads.append(payload)

    for payload in payloads:
        if _has_text(payload.get("instructions")):
            return True
        if _mapping_has_skill_or_step_source(payload):
            return True

    return False


class CreateExecutionRequest(BaseModel):
    """Request payload for starting a workflow execution."""

    model_config = ConfigDict(populate_by_name=True)

    workflow_type: str = Field(
        ...,
        alias="workflowType",
        json_schema_extra={"enum": SUPPORTED_WORKFLOW_TYPES},
    )
    title: Optional[str] = Field(None, alias="title")
    input_artifact_ref: Optional[str] = Field(None, alias="inputArtifactRef")
    plan_artifact_ref: Optional[str] = Field(None, alias="planArtifactRef")
    manifest_artifact_ref: Optional[str] = Field(None, alias="manifestArtifactRef")
    failure_policy: Optional[str] = Field(
        None,
        alias="failurePolicy",
        json_schema_extra={"enum": SUPPORTED_FAILURE_POLICIES},
    )
    initial_parameters: dict[str, Any] = Field(
        default_factory=dict, alias="initialParameters"
    )
    idempotency_key: Optional[str] = Field(None, alias="idempotencyKey")
    schedule: Optional[ScheduleParameters] = Field(None, alias="schedule")

    @model_validator(mode="after")
    def _validate_required_fields(self) -> "CreateExecutionRequest":
        if (
            self.workflow_type == "MoonMind.ManifestIngest"
            and not self.manifest_artifact_ref
        ):
            raise ValueError(
                "manifestArtifactRef is required when workflowType is "
                "MoonMind.ManifestIngest"
            )
        if (
            self.workflow_type == "MoonMind.UserWorkflow"
            and not has_user_workflow_plan_source(
                initial_parameters=self.initial_parameters,
                input_artifact_ref=self.input_artifact_ref,
                plan_artifact_ref=self.plan_artifact_ref,
            )
        ):
            raise ValueError(USER_WORKFLOW_PLAN_SOURCE_ERROR)
        return self

class UpdateExecutionRequest(BaseModel):
    """Request payload for workflow updates."""

    model_config = ConfigDict(populate_by_name=True)

    update_name: str = Field(
        "UpdateInputs",
        alias="updateName",
        json_schema_extra={"enum": SUPPORTED_UPDATE_NAMES},
    )
    input_artifact_ref: Optional[str] = Field(None, alias="inputArtifactRef")
    plan_artifact_ref: Optional[str] = Field(None, alias="planArtifactRef")
    parameters_patch: Optional[dict[str, Any]] = Field(None, alias="parametersPatch")
    title: Optional[str] = Field(None, alias="title")
    new_manifest_artifact_ref: Optional[str] = Field(
        None, alias="newManifestArtifactRef"
    )
    mode: Optional[Literal["REPLACE_FUTURE", "APPEND"]] = Field(None, alias="mode")
    max_concurrency: Optional[int] = Field(None, alias="maxConcurrency")
    node_ids: list[str] = Field(default_factory=list, alias="nodeIds")
    idempotency_key: Optional[str] = Field(None, alias="idempotencyKey")

class RecoverFromFailedStepRequest(BaseModel):
    """Request payload for creating a failed-step recovery follow-up execution."""

    model_config = ConfigDict(
        populate_by_name=True,
        extra="allow",
        json_schema_extra={"additionalProperties": False},
    )

    idempotency_key: str = Field(..., alias="idempotencyKey", min_length=1, max_length=128)
    source_workflow_id: Optional[str] = Field(None, alias="sourceWorkflowId", min_length=1)
    source_run_id: Optional[str] = Field(None, alias="sourceRunId", min_length=1)
    logical_step_id: Optional[str] = Field(None, alias="logicalStepId", min_length=1)
    source_execution_ordinal: Optional[int] = Field(
        None, alias="sourceExecutionOrdinal", ge=1
    )
    recovery_checkpoint_ref: Optional[str] = Field(None, alias="recoveryCheckpointRef")
    failed_run_recovery_manifest_ref: Optional[str] = Field(
        None, alias="failedRunRecoveryManifestRef"
    )
    checkpoint_boundary: Optional[StepExecutionCheckpointBoundary] = Field(
        None, alias="checkpointBoundary"
    )
    task_input_snapshot_ref: Optional[str] = Field(
        None, alias="taskInputSnapshotRef", min_length=1
    )
    plan_ref: Optional[str] = Field(None, alias="planRef", min_length=1)
    plan_digest: Optional[str] = Field(None, alias="planDigest", min_length=1)
    preserved_step_refs: list[str] = Field(default_factory=list, alias="preservedStepRefs")
    dependency_signatures: dict[str, Any] = Field(
        default_factory=dict, alias="dependencySignatures"
    )
    workspace_policy: Optional[WorkspacePolicy] = Field(None, alias="workspacePolicy")
    operator_metadata: dict[str, Any] = Field(
        default_factory=dict, alias="operatorMetadata"
    )

    @field_validator(
        "source_workflow_id",
        "source_run_id",
        "logical_step_id",
        "recovery_checkpoint_ref",
        "failed_run_recovery_manifest_ref",
        "task_input_snapshot_ref",
        "plan_ref",
        "plan_digest",
        mode="before",
    )
    @classmethod
    def _normalize_optional_text(cls, value: Any) -> str | None:
        if value is None:
            return None
        candidate = str(value).strip()
        return candidate or None

    @field_validator("preserved_step_refs")
    @classmethod
    def _normalize_preserved_step_refs(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            candidate = str(item).strip()
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            normalized.append(candidate)
        return normalized

    @field_validator("dependency_signatures", mode="before")
    @classmethod
    def _validate_dependency_signatures(cls, value: Any) -> dict[str, Any]:
        return validate_compact_temporal_mapping(
            value or {}, field_name="dependencySignatures"
        )


class RecoverFromSelectedStepRequest(RecoverFromFailedStepRequest):
    """Request payload for creating a selected-step recovery follow-up execution."""

    model_config = ConfigDict(
        populate_by_name=True,
        extra="allow",
        json_schema_extra={"additionalProperties": False},
    )

    source_workflow_id: str = Field(..., alias="sourceWorkflowId", min_length=1)
    source_run_id: str = Field(..., alias="sourceRunId", min_length=1)
    selected_start_step_id: str = Field(
        ..., alias="selectedStartStepId", min_length=1
    )


class RecoveryCheckpointSourceModel(BaseModel):
    """Source identity embedded in failed-step Recovery checkpoint evidence."""

    model_config = ConfigDict(populate_by_name=True)

    workflow_id: str = Field(..., alias="workflowId", min_length=1)
    run_id: str = Field(..., alias="runId", min_length=1)

class RecoveryCheckpointFailedStepModel(BaseModel):
    """Failed logical step identity to retry."""

    model_config = ConfigDict(populate_by_name=True)

    logical_step_id: str = Field(..., alias="logicalStepId", min_length=1)
    order: int = Field(..., alias="order", ge=1)
    execution_ordinal: int = Field(
        ...,
        alias="executionOrdinal",
        validation_alias=AliasChoices("executionOrdinal", "at" + "tempt"),
        ge=1,
    )
    title: Optional[str] = Field(None, alias="title")

class RecoveryCheckpointPreservedStepModel(BaseModel):
    """Completed source step that may be materialized as preserved."""

    model_config = ConfigDict(populate_by_name=True)

    logical_step_id: str = Field(..., alias="logicalStepId", min_length=1)
    order: int = Field(..., alias="order", ge=1)
    status: Literal["completed", "skipped"] = Field(..., alias="status")
    source_execution_ordinal: int = Field(..., alias="sourceExecutionOrdinal", ge=1)
    artifacts: dict[str, Any] = Field(default_factory=dict, alias="artifacts")
    state_checkpoint_ref: Optional[str] = Field(None, alias="stateCheckpointRef")
    workspace_checkpoint_ref: Optional[str] = Field(
        None, alias="workspaceCheckpointRef"
    )
    step_checkpoint_ref: Optional[str] = Field(None, alias="stepCheckpointRef")

    @field_validator("status", mode="before")
    @classmethod
    def _normalize_legacy_status(cls, value: Any) -> Any:
        if str(value or "").strip().lower() == "succeeded":
            return "completed"
        return value

    @field_validator("artifacts", mode="before")
    @classmethod
    def _validate_compact_artifacts(cls, value: Any) -> dict[str, Any]:
        return validate_compact_temporal_mapping(
            value, field_name="preservedStep.artifacts"
        )

    @field_validator("state_checkpoint_ref", mode="before")
    @classmethod
    def _normalize_state_checkpoint_ref(cls, value: Any) -> str | None:
        if value is None:
            return None
        candidate = str(value).strip()
        return candidate or None

    @field_validator("workspace_checkpoint_ref", "step_checkpoint_ref", mode="before")
    @classmethod
    def _normalize_optional_checkpoint_ref(cls, value: Any) -> str | None:
        if value is None:
            return None
        candidate = str(value).strip()
        return candidate or None

    @model_validator(mode="after")
    def _require_recovery_evidence(self) -> "RecoveryCheckpointPreservedStepModel":
        if not any(value for value in self.artifacts.values()):
            raise ValueError("preserved step requires at least one artifact ref")
        if self.state_checkpoint_ref is None:
            raise ValueError("preserved step requires a state checkpoint ref")
        return self

class RecoveryCheckpointModel(BaseModel):
    """Compact checkpoint evidence required before failed-step recovery execution."""

    model_config = ConfigDict(populate_by_name=True)

    schema_version: Literal["v1"] = Field("v1", alias="schemaVersion")
    source: RecoveryCheckpointSourceModel = Field(..., alias="source")
    task_input_snapshot_ref: str = Field(..., alias="taskInputSnapshotRef", min_length=1)
    plan_ref: Optional[str] = Field(None, alias="planRef")
    plan_digest: Optional[str] = Field(None, alias="planDigest")
    failed_step: RecoveryCheckpointFailedStepModel = Field(..., alias="failedStep")
    preserved_steps: list[RecoveryCheckpointPreservedStepModel] = Field(
        default_factory=list, alias="preservedSteps"
    )
    prepared_artifact_refs: list[str] = Field(
        default_factory=list, alias="preparedArtifactRefs"
    )
    recovery_workspace: dict[str, Any] = Field(
        default_factory=dict, alias="recoveryWorkspace"
    )

    @field_validator("plan_ref", "plan_digest", mode="before")
    @classmethod
    def _normalize_optional_ref(cls, value: Any) -> str | None:
        if value is None:
            return None
        candidate = str(value).strip()
        return candidate or None

    @field_validator("recovery_workspace", mode="before")
    @classmethod
    def _validate_compact_recovery_workspace(cls, value: Any) -> dict[str, Any]:
        compact = validate_compact_temporal_mapping(
            value, field_name="recoveryWorkspace"
        )
        for raw_key in compact:
            key = str(raw_key).strip()
            if key.lower() == "inlinecheckpointpayload":
                raise ValueError(
                    "recoveryWorkspace must not contain inline checkpoint payload"
                )
        return compact

    @field_validator("prepared_artifact_refs")
    @classmethod
    def _normalize_prepared_refs(cls, value: list[str]) -> list[str]:
        return [item.strip() for item in value if isinstance(item, str) and item.strip()]

    @model_validator(mode="after")
    def _require_recovery_evidence(self) -> "RecoveryCheckpointModel":
        if self.plan_ref is None and self.plan_digest is None:
            raise ValueError("recovery checkpoint requires plan identity or digest")
        if not any(value for value in self.recovery_workspace.values()):
            raise ValueError("recovery checkpoint requires workspace checkpoint evidence")
        return self

class RecoverySourceModel(BaseModel):
    """Compact source provenance carried by a resumed execution."""

    model_config = ConfigDict(populate_by_name=True)

    kind: Literal["recover_from_failed_step"] = Field(
        "recover_from_failed_step", alias="kind"
    )
    recovery_action: Literal["resume_from_workspace_checkpoint"] = Field(
        "resume_from_workspace_checkpoint", alias="recoveryAction"
    )
    resume_phase: RecoveryResumePhase = Field(..., alias="resumePhase")
    target_runtime_id: str = Field(..., alias="targetRuntimeId", min_length=1)
    capability_set_version: str = Field(..., alias="capabilitySetVersion", min_length=1)
    capability_digest: str = Field(..., alias="capabilityDigest", min_length=1)
    checkpoint_kind: str = Field(..., alias="checkpointKind", min_length=1)
    checkpoint_restore_kinds: tuple[str, ...] = Field(..., alias="checkpointRestoreKinds")
    checkpoint_restore_activity: str = Field(..., alias="checkpointRestoreActivity", min_length=1)
    workspace_authority: str = Field(..., alias="workspaceAuthority", min_length=1)
    # Optional only while decoding pre-patch histories. New recovery creation
    # supplies the complete proof set and patch-gated workflow preflight rejects
    # absent or contradictory values before restoration.
    selected_target_runtime_id: str | None = Field(None, alias="selectedTargetRuntimeId")
    selected_capability_digest: str | None = Field(None, alias="selectedCapabilityDigest")
    registered_restore_activity: str | None = Field(None, alias="registeredRestoreActivity")
    checkpoint_source_workflow_id: str | None = Field(None, alias="checkpointSourceWorkflowId")
    checkpoint_source_run_id: str | None = Field(None, alias="checkpointSourceRunId")
    side_effect_safe: bool | None = Field(None, alias="sideEffectSafe")
    source_workflow_id: str = Field(..., alias="sourceWorkflowId", min_length=1)
    source_run_id: str = Field(..., alias="sourceRunId", min_length=1)
    source_task_input_snapshot_ref: str = Field(
        ..., alias="sourceTaskInputSnapshotRef", min_length=1
    )
    source_plan_ref: Optional[str] = Field(None, alias="sourcePlanRef")
    source_plan_digest: Optional[str] = Field(None, alias="sourcePlanDigest")
    failed_step_id: str = Field(..., alias="failedStepId", min_length=1)
    failed_step_execution: int = Field(..., alias="failedStepExecution", ge=1)
    recovery_mode: Literal["last_failed_step", "selected_step"] = Field(
        "last_failed_step", alias="recoveryMode"
    )
    selected_start_step_id: Optional[str] = Field(None, alias="selectedStartStepId")
    selected_start_step_execution: Optional[int] = Field(
        None, alias="selectedStartStepExecution", ge=1
    )
    recovery_checkpoint_ref: str = Field(..., alias="recoveryCheckpointRef", min_length=1)
    failed_run_recovery_manifest_ref: str | None = Field(
        None, alias="failedRunRecoveryManifestRef"
    )
    selected_checkpoint_boundary: str | None = Field(
        None, alias="selectedCheckpointBoundary"
    )
    admitted_checkpoint_resume_decision: dict[str, Any] | None = Field(
        None, alias="admittedCheckpointResumeDecision"
    )
    recovery_workspace: dict[str, Any] = Field(
        default_factory=dict, alias="recoveryWorkspace"
    )
    preserved_steps: list[RecoveryCheckpointPreservedStepModel] = Field(
        default_factory=list, alias="preservedSteps"
    )
    checkpoint_recovery: dict[str, Any] | None = Field(
        None, alias="checkpointRecovery"
    )

    @model_validator(mode="after")
    def _validate_restore_contract(self) -> "RecoverySourceModel":
        if self.checkpoint_kind not in self.checkpoint_restore_kinds:
            raise ValueError("checkpointKind must be present in checkpointRestoreKinds")
        return self

class ResumeExecutionRefModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    workflow_id: str = Field(..., alias="workflowId", min_length=1)
    run_id: str = Field(..., alias="runId", min_length=1)
    detail_href: Optional[str] = Field(None, alias="detailHref")

class RecoverFromFailedStepResponse(BaseModel):
    """Response from the failed-step recovery command."""

    model_config = ConfigDict(populate_by_name=True)

    accepted: Literal[True] = Field(True, alias="accepted")
    applied: Literal["created_resumed_execution"] = Field(
        "created_resumed_execution", alias="applied"
    )
    source: ResumeExecutionRefModel = Field(..., alias="source")
    execution: ResumeExecutionRefModel = Field(..., alias="execution")
    relationship: Literal[
        "Recovered from failed step", "Recovered from selected step"
    ] = Field("Recovered from failed step", alias="relationship")
    recovery_checkpoint_ref: str = Field(..., alias="recoveryCheckpointRef")


class RecoverExecutionResponse(BaseModel):
    """Response from the canonical typed recovery command."""

    model_config = ConfigDict(populate_by_name=True)

    accepted: Literal[True] = Field(True, alias="accepted")
    applied: Literal["created_recovery_execution"] = Field(
        "created_recovery_execution", alias="applied"
    )
    target_kind: Literal[
        "failed_step", "control_stop", "publication", "restoration_failure"
    ] = Field(..., alias="targetKind")
    continuation_phase: Literal[
        "rerun_failed_step",
        "continue_to_gate",
        "continue_after_gate",
        "continue_to_remediation",
        "resume_publication",
        "retry_restoration",
    ] = Field(..., alias="continuationPhase")
    source: ResumeExecutionRefModel
    execution: ResumeExecutionRefModel
    recovery_checkpoint_ref: str = Field(..., alias="recoveryCheckpointRef")
    creation_key: str = Field(..., alias="creationKey")


class SignalExecutionRequest(BaseModel):
    """Request payload for asynchronous workflow signals."""

    model_config = ConfigDict(populate_by_name=True)

    signal_name: str = Field(
        ...,
        alias="signalName",
        json_schema_extra={"enum": SUPPORTED_SIGNAL_NAMES},
    )
    payload: dict[str, Any] = Field(default_factory=dict, alias="payload")
    payload_artifact_ref: Optional[str] = Field(None, alias="payloadArtifactRef")

class ConfigureIntegrationMonitoringRequest(BaseModel):
    """Request payload for starting Temporal-side external monitoring."""

    model_config = ConfigDict(populate_by_name=True)

    integration_name: str = Field(..., alias="integrationName", min_length=1)
    correlation_id: Optional[str] = Field(None, alias="correlationId")
    external_operation_id: str = Field(..., alias="externalOperationId", min_length=1)
    normalized_status: NormalizedIntegrationStatus = Field(
        ..., alias="normalizedStatus"
    )
    provider_status: Optional[str] = Field(None, alias="providerStatus")
    callback_supported: bool = Field(..., alias="callbackSupported")
    callback_correlation_key: Optional[str] = Field(
        None, alias="callbackCorrelationKey"
    )
    recommended_poll_seconds: Optional[int] = Field(
        None, alias="recommendedPollSeconds", ge=1
    )
    external_url: Optional[str] = Field(None, alias="externalUrl")
    provider_summary: dict[str, Any] = Field(
        default_factory=dict, alias="providerSummary"
    )
    result_refs: list[str] = Field(default_factory=list, alias="resultRefs")

    @field_validator("provider_summary", mode="after")
    @classmethod
    def _validate_provider_summary(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_compact_temporal_mapping(
            value,
            field_name="providerSummary",
        )

class PollIntegrationRequest(BaseModel):
    """Request payload for polling updates while awaiting external completion."""

    model_config = ConfigDict(populate_by_name=True)

    normalized_status: NormalizedIntegrationStatus = Field(
        ..., alias="normalizedStatus"
    )
    provider_status: Optional[str] = Field(None, alias="providerStatus")
    observed_at: Optional[datetime] = Field(None, alias="observedAt")
    recommended_poll_seconds: Optional[int] = Field(
        None, alias="recommendedPollSeconds", ge=1
    )
    external_url: Optional[str] = Field(None, alias="externalUrl")
    provider_summary: dict[str, Any] = Field(
        default_factory=dict, alias="providerSummary"
    )
    result_refs: list[str] = Field(default_factory=list, alias="resultRefs")
    completed_wait_cycles: int = Field(1, alias="completedWaitCycles", ge=0)

    @field_validator("provider_summary", mode="after")
    @classmethod
    def _validate_provider_summary(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_compact_temporal_mapping(
            value,
            field_name="providerSummary",
        )

class IntegrationCallbackRequest(BaseModel):
    """Generic provider callback payload resolved through correlation storage."""

    model_config = ConfigDict(populate_by_name=True)

    source: Optional[str] = Field(None, alias="source")
    event_type: str = Field(..., alias="eventType", min_length=1)
    external_operation_id: Optional[str] = Field(None, alias="externalOperationId")
    provider_event_id: Optional[str] = Field(None, alias="providerEventId")
    normalized_status: Optional[NormalizedIntegrationStatus] = Field(
        None, alias="normalizedStatus"
    )
    provider_status: Optional[str] = Field(None, alias="providerStatus")
    observed_at: Optional[datetime] = Field(None, alias="observedAt")
    external_url: Optional[str] = Field(None, alias="externalUrl")
    provider_summary: dict[str, Any] = Field(
        default_factory=dict, alias="providerSummary"
    )
    payload_artifact_ref: Optional[str] = Field(None, alias="payloadArtifactRef")

    @field_validator("provider_summary", mode="after")
    @classmethod
    def _validate_provider_summary(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_compact_temporal_mapping(
            value,
            field_name="providerSummary",
        )

class IntegrationStateModel(BaseModel):
    """Compact persisted state for a monitored external integration."""

    model_config = ConfigDict(populate_by_name=True)

    integration_name: str = Field(..., alias="integrationName")
    correlation_id: str = Field(..., alias="correlationId")
    external_operation_id: str = Field(..., alias="externalOperationId")
    normalized_status: NormalizedIntegrationStatus = Field(
        ..., alias="normalizedStatus"
    )
    provider_status: Optional[str] = Field(None, alias="providerStatus")
    started_at: datetime = Field(..., alias="startedAt")
    last_observed_at: datetime = Field(..., alias="lastObservedAt")
    monitor_attempt_count: int = Field(..., alias="monitorAttemptCount", ge=0)
    callback_supported: bool = Field(..., alias="callbackSupported")
    result_refs: list[str] = Field(default_factory=list, alias="resultRefs")
    callback_correlation_key: Optional[str] = Field(
        None, alias="callbackCorrelationKey"
    )
    provider_event_ids_seen: list[str] = Field(
        default_factory=list, alias="providerEventIdsSeen"
    )
    next_poll_at: Optional[datetime] = Field(None, alias="nextPollAt")
    poll_interval_seconds: Optional[int] = Field(None, alias="pollIntervalSeconds")
    external_url: Optional[str] = Field(None, alias="externalUrl")
    provider_summary: dict[str, Any] = Field(
        default_factory=dict, alias="providerSummary"
    )

    @field_validator("provider_summary", mode="after")
    @classmethod
    def _validate_provider_summary(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_compact_temporal_mapping(
            value,
            field_name="providerSummary",
        )

class CancelExecutionRequest(BaseModel):
    """Request payload for cancellation."""

    model_config = ConfigDict(populate_by_name=True)

    action: Literal["cancel", "reject"] = Field("cancel", alias="action")
    reason: Optional[str] = Field(None, alias="reason")
    graceful: bool = Field(True, alias="graceful")

class RescheduleExecutionRequest(BaseModel):
    """Request payload for rescheduling a deferred execution."""

    model_config = ConfigDict(populate_by_name=True)

    scheduled_for: datetime = Field(..., alias="scheduledFor")

class ExecutionActionCapabilityModel(BaseModel):
    """State-aware Temporal action visibility returned to the dashboard."""

    model_config = ConfigDict(populate_by_name=True)

    can_set_title: bool = Field(False, alias="canSetTitle")
    can_update_inputs: bool = Field(False, alias="canUpdateInputs")
    can_edit_for_rerun: bool = Field(False, alias="canEditForRerun")
    can_rerun: bool = Field(False, alias="canRerun")
    can_approve: bool = Field(False, alias="canApprove")
    can_pause: bool = Field(False, alias="canPause")
    can_resume: bool = Field(False, alias="canResume")
    can_failed_step_resume: bool = Field(
        False, alias="canResumeFromFailedStep"
    )
    can_continue_remediation: bool = Field(False, alias="canContinueRemediation")
    can_retry_publication: bool = Field(False, alias="canRetryPublication")
    can_full_retry: bool = Field(False, alias="canFullRetry")
    action_evidence: dict[str, dict[str, str | None]] = Field(
        default_factory=dict, alias="actionEvidence"
    )
    can_cancel: bool = Field(False, alias="canCancel")
    can_reject: bool = Field(False, alias="canReject")
    can_send_message: bool = Field(False, alias="canSendMessage")
    can_bypass_dependencies: bool = Field(False, alias="canBypassDependencies")
    can_skip_dependency_wait: bool = Field(False, alias="canSkipDependencyWait")
    disabled_reasons: dict[str, str] = Field(
        default_factory=dict, alias="disabledReasons"
    )

class ExecutionInterventionAuditEntryModel(BaseModel):
    """One explicit operator intervention record shown outside stdout/stderr logs."""

    model_config = ConfigDict(populate_by_name=True)

    action: str = Field(..., alias="action")
    transport: str = Field(..., alias="transport")
    summary: str = Field(..., alias="summary")
    detail: Optional[str] = Field(None, alias="detail")
    created_at: datetime = Field(..., alias="createdAt")

class ExecutionDebugFieldsModel(BaseModel):
    """Optional debug metadata gated by Temporal dashboard settings."""

    model_config = ConfigDict(populate_by_name=True)

    workflow_id: str = Field(..., alias="workflowId")
    run_id: str = Field(..., alias="runId")
    temporal_run_id: str | None = Field(None, alias="temporalRunId", exclude=True)
    legacy_run_id: Optional[str] = Field(None, alias="legacyRunId", exclude=True)
    namespace: str = Field(..., alias="namespace")
    temporal_status: str = Field(..., alias="temporalStatus")
    raw_state: str = Field(..., alias="rawState")
    close_status: Optional[str] = Field(None, alias="closeStatus")
    waiting_reason: Optional[str] = Field(None, alias="waitingReason")
    attention_required: bool = Field(False, alias="attentionRequired")

class ExecutionDependencyOutcomeModel(BaseModel):
    """One resolved prerequisite outcome surfaced on execution detail."""

    model_config = ConfigDict(populate_by_name=True)

    workflow_id: str = Field(..., alias="workflowId")
    terminal_state: Optional[str] = Field(None, alias="terminalState")
    close_status: Optional[str] = Field(None, alias="closeStatus")
    resolved_at: Optional[datetime] = Field(None, alias="resolvedAt")
    failure_category: Optional[str] = Field(None, alias="failureCategory")
    message: Optional[str] = Field(None, alias="message")
    resolution: Optional[str] = Field(None, alias="resolution")
    failure_count: Optional[int] = Field(None, alias="failureCount")
    last_failed_at: Optional[datetime] = Field(None, alias="lastFailedAt")

class ExecutionDependencySummaryModel(BaseModel):
    """Compact linked execution metadata for prerequisites or dependents."""

    model_config = ConfigDict(populate_by_name=True)

    workflow_id: str = Field(..., alias="workflowId")
    title: Optional[str] = Field(None, alias="title")
    summary: Optional[str] = Field(None, alias="summary")
    state: Optional[str] = Field(None, alias="state")
    close_status: Optional[str] = Field(None, alias="closeStatus")
    workflow_type: Optional[str] = Field(None, alias="workflowType")

class ExecutionSkillEvidenceSummaryModel(BaseModel):
    """Compact operator-safe evidence for one selected skill."""

    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(..., alias="name")
    source_kind: Optional[str] = Field(None, alias="sourceKind")
    source_path: Optional[str] = Field(None, alias="sourcePath")
    content_ref: Optional[str] = Field(None, alias="contentRef")
    content_digest: Optional[str] = Field(None, alias="contentDigest")

class ExecutionSkillProvenanceModel(BaseModel):
    """Compact source provenance for one selected skill."""

    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(..., alias="name")
    source_kind: Optional[str] = Field(None, alias="sourceKind")
    source_path: Optional[str] = Field(None, alias="sourcePath")

class ExecutionProjectionDiagnosticModel(BaseModel):
    """Sanitized projection diagnostic for operator-visible skill failures."""

    model_config = ConfigDict(populate_by_name=True)

    path: Optional[str] = Field(None, alias="path")
    object_kind: Optional[str] = Field(None, alias="objectKind")
    attempted_action: Optional[str] = Field(None, alias="attemptedAction")
    remediation: Optional[str] = Field(None, alias="remediation")
    cause: Optional[str] = Field(None, alias="cause")

class ExecutionSkillLifecycleIntentModel(BaseModel):
    """How skill intent or snapshot reuse is preserved across lifecycle paths."""

    model_config = ConfigDict(populate_by_name=True)

    source: str = Field(..., alias="source")
    selectors: list[str] = Field(default_factory=list, alias="selectors")
    resolved_skillset_ref: Optional[str] = Field(None, alias="resolvedSkillsetRef")
    resolution_mode: str = Field(..., alias="resolutionMode")
    explanation: str = Field(..., alias="explanation")

class ExecutionSkillRuntimeModel(BaseModel):
    """Compact skill runtime evidence exposed by execution detail APIs."""

    model_config = ConfigDict(populate_by_name=True)

    resolved_skillset_ref: Optional[str] = Field(None, alias="resolvedSkillsetRef")
    selected_skills: list[str] = Field(default_factory=list, alias="selectedSkills")
    selected_evidence: list[ExecutionSkillEvidenceSummaryModel] = Field(
        default_factory=list, alias="selectedEvidence"
    )
    source_provenance: list[ExecutionSkillProvenanceModel] = Field(
        default_factory=list, alias="sourceProvenance"
    )
    materialization_mode: Optional[str] = Field(None, alias="materializationMode")
    visible_path: Optional[str] = Field(None, alias="visiblePath")
    backing_path: Optional[str] = Field(None, alias="backingPath")
    read_only: Optional[bool] = Field(None, alias="readOnly")
    manifest_ref: Optional[str] = Field(None, alias="manifestRef")
    prompt_index_ref: Optional[str] = Field(None, alias="promptIndexRef")
    activation_summary_ref: Optional[str] = Field(None, alias="activationSummaryRef")
    diagnostics: Optional[ExecutionProjectionDiagnosticModel] = Field(
        None, alias="diagnostics"
    )
    lifecycle_intent: Optional[ExecutionSkillLifecycleIntentModel] = Field(
        None, alias="lifecycleIntent"
    )

class ExecutionProgressModel(BaseModel):
    """Bounded latest-run progress summary derived from workflow-owned step state."""

    model_config = ConfigDict(populate_by_name=True)

    total: int = Field(0, alias="total", ge=0)
    pending: int = Field(0, alias="pending", ge=0)
    ready: int = Field(0, alias="ready", ge=0)
    executing: int = Field(0, alias="executing", ge=0)
    awaiting_external: int = Field(0, alias="awaitingExternal", ge=0)
    reviewing: int = Field(0, alias="reviewing", ge=0)
    completed: int = Field(0, alias="completed", ge=0)
    failed: int = Field(0, alias="failed", ge=0)
    skipped: int = Field(0, alias="skipped", ge=0)
    canceled: int = Field(0, alias="canceled", ge=0)
    current_step_title: str | None = Field(None, alias="currentStepTitle")
    updated_at: datetime = Field(..., alias="updatedAt")

    @model_validator(mode="before")
    @classmethod
    def _normalize_legacy_progress_fields(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            return value
        payload = dict(value)
        if payload.get("executing") is None and payload.get("running") is not None:
            payload["executing"] = payload["running"]
        if payload.get("completed") is None and payload.get("succeeded") is not None:
            payload["completed"] = payload["succeeded"]
        return payload

class ExecutionMergeAutomationBlockerModel(BaseModel):
    """Operator-visible merge automation blocker."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    kind: str | None = Field(None, alias="kind")
    summary: str | None = Field(None, alias="summary")
    retryable: bool | None = Field(None, alias="retryable")
    source: str | None = Field(None, alias="source")

class ExecutionMergeAutomationArtifactRefsModel(BaseModel):
    """Artifact refs produced by merge automation."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    summary: str | None = Field(None, alias="summary")
    gate_snapshots: list[str] = Field(default_factory=list, alias="gateSnapshots")
    resolver_attempts: list[str] = Field(default_factory=list, alias="resolverAttempts")

class ExecutionMergeAutomationResolverChildModel(BaseModel):
    """Resolver child workflow reference plus observability binding when known."""

    model_config = ConfigDict(populate_by_name=True)

    workflow_id: str = Field(..., alias="workflowId")
    agent_run_id: str | None = Field(
        None,
        alias="agentRunId",
        validation_alias=AliasChoices("agentRunId", "agent_run_id"),
    )
    status: str | None = Field(None, alias="status")
    detail_href: str | None = Field(None, alias="detailHref")

class ExecutionMergeAutomationModel(BaseModel):
    """Live or terminal merge automation visibility for an execution."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    enabled: bool = Field(True, alias="enabled")
    workflow_id: str | None = Field(None, alias="workflowId")
    child_workflow_id: str | None = Field(None, alias="childWorkflowId")
    status: str | None = Field(None, alias="status")
    pr_number: int | str | None = Field(None, alias="prNumber")
    pr_url: str | None = Field(None, alias="prUrl")
    latest_head_sha: str | None = Field(None, alias="latestHeadSha")
    cycles: int | str | None = Field(None, alias="cycles")
    blockers: list[ExecutionMergeAutomationBlockerModel] = Field(
        default_factory=list,
        alias="blockers",
    )
    artifact_refs: ExecutionMergeAutomationArtifactRefsModel | None = Field(
        None,
        alias="artifactRefs",
    )
    resolver_child_workflow_ids: list[str] = Field(
        default_factory=list,
        alias="resolverChildWorkflowIds",
    )
    resolver_children: list[ExecutionMergeAutomationResolverChildModel] = Field(
        default_factory=list,
        alias="resolverChildren",
    )

    @model_validator(mode="after")
    def _mirror_child_workflow_id(self) -> "ExecutionMergeAutomationModel":
        if not self.workflow_id and self.child_workflow_id:
            self.workflow_id = self.child_workflow_id
        if not self.child_workflow_id and self.workflow_id:
            self.child_workflow_id = self.workflow_id
        return self

class WorkflowInputSnapshotDescriptorModel(BaseModel):
    """Compact pointer to the authoritative original task input snapshot."""

    model_config = ConfigDict(populate_by_name=True)

    available: bool = Field(False, alias="available")
    artifact_ref: str | None = Field(None, alias="artifactRef")
    snapshot_version: int | None = Field(None, alias="snapshotVersion")
    source_kind: Literal["create", "edit", "rerun", "unknown"] = Field(
        "unknown", alias="sourceKind"
    )
    reconstruction_mode: Literal[
        "authoritative",
        "degraded_read_only",
        "unavailable",
    ] = Field("unavailable", alias="reconstructionMode")
    disabled_reasons: dict[str, str] = Field(
        default_factory=dict, alias="disabledReasons"
    )
    fallback_evidence_refs: list[str] = Field(
        default_factory=list, alias="fallbackEvidenceRefs"
    )

    @model_validator(mode="after")
    def _authoritative_requires_ref(self) -> "WorkflowInputSnapshotDescriptorModel":
        if self.reconstruction_mode == "authoritative" and not self.artifact_ref:
            raise ValueError("authoritative reconstruction requires artifactRef")
        return self

class StepLedgerCheckModel(BaseModel):
    """Structured step-level review or check result."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    kind: str = Field(..., alias="kind", min_length=1)
    status: str = Field(..., alias="status", min_length=1)
    summary: str | None = Field(None, alias="summary")
    retry_count: int = Field(0, alias="retryCount", ge=0)
    artifact_ref: str | None = Field(None, alias="artifactRef")

class StepLedgerRefsModel(BaseModel):
    """Stable ref slots for child workflow and managed agent-run linkage."""

    model_config = ConfigDict(populate_by_name=True)

    child_workflow_id: str | None = Field(None, alias="childWorkflowId")
    child_run_id: str | None = Field(None, alias="childRunId")
    agent_run_id: str | None = Field(
        None,
        alias="agentRunId",
        validation_alias=AliasChoices("agentRunId", "agent_run_id"),
    )
    latest_step_execution_manifest_ref: str | None = Field(
        None, alias="latestStepExecutionManifestRef"
    )
    step_execution_manifest_refs: list[str] = Field(
        default_factory=list, alias="stepExecutionManifestRefs"
    )
    latest_step_execution_checkpoint_ref: str | None = Field(
        None, alias="latestStepExecutionCheckpointRef"
    )
    step_execution_checkpoint_refs: list[str] = Field(
        default_factory=list, alias="stepExecutionCheckpointRefs"
    )
    checkpoint_refs_by_boundary: dict[str, str] = Field(
        default_factory=dict, alias="checkpointRefsByBoundary"
    )

class StepLedgerArtifactsModel(BaseModel):
    """Stable semantic artifact slots for step evidence."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    output_summary: str | None = Field(None, alias="outputSummary")
    output_primary: str | None = Field(None, alias="outputPrimary")
    runtime_stdout: str | None = Field(None, alias="runtimeStdout")
    runtime_stderr: str | None = Field(None, alias="runtimeStderr")
    runtime_merged_logs: str | None = Field(None, alias="runtimeMergedLogs")
    runtime_diagnostics: str | None = Field(None, alias="runtimeDiagnostics")
    provider_snapshot: str | None = Field(None, alias="providerSnapshot")
    step_execution_manifest_ref: str | None = Field(None, alias="stepExecutionManifestRef")
    step_execution_manifest_refs: list[str] = Field(
        default_factory=list,
        alias="stepExecutionManifestRefs",
    )

class StepLedgerResumePreservationModel(BaseModel):
    """Bounded failed-step recovery preservation eligibility for one source step."""

    model_config = ConfigDict(populate_by_name=True)

    eligible: bool = Field(..., alias="eligible")
    reason: Literal[
        "complete", "not_completed", "missing_output_refs", "missing_state_checkpoint"
    ] = Field(..., alias="reason")
    message: str | None = Field(None, alias="message")


class StepLedgerWorkloadModel(BaseModel):
    """Bounded Docker-backed workload metadata linked to a producing step."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    agent_run_id: str | None = Field(
        None,
        alias="agentRunId",
        validation_alias=AliasChoices("agentRunId", "agent_run_id"),
    )
    step_id: str | None = Field(None, alias="stepId")
    execution_ordinal: int | None = Field(
        None,
        alias="executionOrdinal",
        validation_alias=AliasChoices("executionOrdinal", "at" + "tempt"),
        ge=1,
    )
    tool_name: str | None = Field(None, alias="toolName")
    profile_id: str | None = Field(None, alias="profileId")
    image_ref: str | None = Field(None, alias="imageRef")
    status: str | None = Field(None, alias="status")
    exit_code: int | None = Field(None, alias="exitCode")
    duration_seconds: float | None = Field(None, alias="durationSeconds", ge=0)
    timeout_reason: str | None = Field(None, alias="timeoutReason")
    cancel_reason: str | None = Field(None, alias="cancelReason")
    session_context: dict[str, Any] | None = Field(None, alias="sessionContext")
    artifact_publication: dict[str, Any] | None = Field(
        None,
        alias="artifactPublication",
    )


class StepExecutionOutcomeModel(BaseModel):
    """Authoritative primary execution outcome, independent of finalization."""

    model_config = ConfigDict(populate_by_name=True)

    status: Literal["succeeded", "failed"]
    result_ref: str | None = Field(None, alias="resultRef")
    output_refs: list[str] = Field(default_factory=list, alias="outputRefs")
    diagnostics_ref: str | None = Field(None, alias="diagnosticsRef")
    workspace_locator: WorkspaceLocator | str | None = Field(None, alias="workspaceLocator")
    recorded_at: datetime = Field(..., alias="recordedAt")


class StepFinalizationOutcomeModel(BaseModel):
    """Outcome of retryable work performed after primary execution."""

    model_config = ConfigDict(populate_by_name=True)

    status: Literal[
        "not_started", "succeeded", "retry_pending", "failed", "degraded", "unsupported"
    ]
    phase: str | None = None
    criticality: Literal["required", "recoverability_only", "unsupported"]
    failure_code: str | None = Field(None, alias="failureCode")
    terminal_failure_code: str | None = Field(None, alias="terminalFailureCode")
    retry_count: int = Field(0, alias="retryCount", ge=0)
    checkpoint_ref: str | None = Field(None, alias="checkpointRef")
    message: str | None = None
    updated_at: datetime = Field(..., alias="updatedAt")


StepTimingPrecision = Literal["exact", "live", "fallback", "unavailable"]


class StepTimingModel(BaseModel):
    """Bounded user-facing logical timing for a step or step execution."""

    model_config = ConfigDict(populate_by_name=True)

    started_at: datetime | None = Field(None, alias="startedAt")
    ended_at: datetime | None = Field(None, alias="endedAt")
    duration_ms: int | None = Field(None, alias="durationMs", ge=0)
    elapsed_ms: int | None = Field(None, alias="elapsedMs", ge=0)
    server_now: datetime | None = Field(None, alias="serverNow")
    precision: StepTimingPrecision = Field("unavailable", alias="precision")
    preserved: bool = Field(False, alias="preserved")


_ACTIVE_STEP_TIMING_STATUSES = {"executing", "reviewing", "awaiting_external"}
_TERMINAL_STEP_TIMING_STATUSES = {"completed", "failed", "skipped", "canceled"}


def _duration_ms_between(
    started_at: datetime | None,
    ended_at: datetime | None,
) -> int | None:
    if started_at is None or ended_at is None:
        return None
    started = started_at
    ended = ended_at
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    if ended.tzinfo is None:
        ended = ended.replace(tzinfo=timezone.utc)
    return max(0, int((ended - started).total_seconds() * 1000))


def _fallback_step_timing_payload(
    *,
    status: str | None,
    started_at: datetime | None,
    updated_at: datetime | None,
    preserved: bool = False,
    status_source: Literal["ledger", "step_execution"] = "ledger",
) -> dict[str, Any]:
    normalized_status = str(status or "").strip()
    if status_source == "step_execution" and normalized_status:
        normalized_status = step_execution_to_ledger_status(normalized_status).value
    if normalized_status in _ACTIVE_STEP_TIMING_STATUSES and started_at is not None:
        elapsed_ms = _duration_ms_between(started_at, updated_at)
        return {
            "startedAt": started_at,
            "endedAt": None,
            "durationMs": None,
            "elapsedMs": elapsed_ms,
            "serverNow": updated_at,
            "precision": "live" if elapsed_ms is not None else "unavailable",
            "preserved": preserved,
        }
    if normalized_status in _TERMINAL_STEP_TIMING_STATUSES and started_at is not None:
        duration_ms = _duration_ms_between(started_at, updated_at)
        return {
            "startedAt": started_at,
            "endedAt": updated_at if duration_ms is not None else None,
            "durationMs": duration_ms,
            "elapsedMs": duration_ms,
            "serverNow": updated_at,
            "precision": "fallback" if duration_ms is not None else "unavailable",
            "preserved": preserved,
        }
    return {
        "startedAt": started_at,
        "endedAt": None,
        "durationMs": None,
        "elapsedMs": None,
        "serverNow": updated_at,
        "precision": "unavailable",
        "preserved": preserved,
    }

class PreservedStepProvenanceModel(BaseModel):
    """Source execution provenance for a preserved step row."""

    model_config = ConfigDict(populate_by_name=True)

    workflow_id: str = Field(..., alias="workflowId", min_length=1)
    run_id: str = Field(..., alias="runId", min_length=1)
    logical_step_id: str = Field(..., alias="logicalStepId", min_length=1)
    execution_ordinal: int = Field(..., alias="executionOrdinal", ge=1)

class StepLedgerRowModel(BaseModel):
    """Current/latest Step Execution state for one logical step in the active run."""

    model_config = ConfigDict(populate_by_name=True)

    logical_step_id: str = Field(..., alias="logicalStepId", min_length=1)
    order: int = Field(..., alias="order", ge=1)
    title: str = Field(..., alias="title", min_length=1)
    tool: dict[str, Any] = Field(default_factory=dict, alias="tool")
    depends_on: list[str] = Field(default_factory=list, alias="dependsOn")
    status: StepLedgerStatusValue = Field(..., alias="status")
    waiting_reason: str | None = Field(None, alias="waitingReason")
    attention_required: bool = Field(False, alias="attentionRequired")
    execution_ordinal: int = Field(
        0,
        alias="executionOrdinal",
        validation_alias=AliasChoices("executionOrdinal", "at" + "tempt"),
        ge=0,
    )
    started_at: datetime | None = Field(None, alias="startedAt")
    updated_at: datetime = Field(..., alias="updatedAt")
    summary: str | None = Field(None, alias="summary")
    checks: list[StepLedgerCheckModel] = Field(default_factory=list, alias="checks")
    refs: StepLedgerRefsModel = Field(default_factory=StepLedgerRefsModel, alias="refs")
    artifacts: StepLedgerArtifactsModel = Field(
        default_factory=StepLedgerArtifactsModel, alias="artifacts"
    )
    preserved_from: PreservedStepProvenanceModel | None = Field(
        None, alias="preservedFrom"
    )
    state_checkpoint_ref: str | None = Field(None, alias="stateCheckpointRef")
    workspace_checkpoint_ref: str | None = Field(None, alias="workspaceCheckpointRef")
    step_checkpoint_ref: str | None = Field(None, alias="stepCheckpointRef")
    resume_preservation: StepLedgerResumePreservationModel | None = Field(
        None, alias="recoveryPreservation"
    )
    workload: StepLedgerWorkloadModel | None = Field(None, alias="workload")
    execution_outcome: StepExecutionOutcomeModel | None = Field(
        None, alias="executionOutcome"
    )
    finalization_outcome: StepFinalizationOutcomeModel | None = Field(
        None, alias="finalizationOutcome"
    )
    timing: StepTimingModel | None = Field(None, alias="timing")
    last_error: str | None = Field(None, alias="lastError")

    @model_validator(mode="after")
    def _populate_timing(self) -> "StepLedgerRowModel":
        if self.timing is None:
            self.timing = StepTimingModel.model_validate(
                _fallback_step_timing_payload(
                    status=self.status,
                    started_at=self.started_at,
                    updated_at=self.updated_at,
                    preserved=self.preserved_from is not None,
                )
            )
        return self

class StepLedgerSnapshotModel(BaseModel):
    """Latest-run step-ledger query payload."""

    model_config = ConfigDict(populate_by_name=True)

    workflow_id: str = Field(..., alias="workflowId", min_length=1)
    run_id: str = Field(..., alias="runId", min_length=1)
    run_scope: Literal["latest"] = Field("latest", alias="runScope")
    prepared_artifact_refs: list[str] = Field(
        default_factory=list, alias="preparedArtifactRefs"
    )
    steps: list[StepLedgerRowModel] = Field(default_factory=list, alias="steps")


class CompatibilityBoundaryDecisionModel(BaseModel):
    """Typed fail-closed decision for degraded persisted boundary values."""

    model_config = ConfigDict(populate_by_name=True)

    valid: bool = Field(..., alias="valid")
    decision: Literal["valid", "invalid", "degraded"] = Field(..., alias="decision")
    failure_code: str | None = Field(None, alias="failureCode", max_length=120)
    message: str = Field(..., alias="message", max_length=500)


class StepExecutionProjectionModel(BaseModel):
    """Bounded Step Execution projection derived from manifest refs."""

    model_config = ConfigDict(populate_by_name=True)

    manifest_artifact_ref: str = Field(..., alias="manifestArtifactRef", min_length=1)
    step_execution_id: str = Field(..., alias="stepExecutionId", min_length=1)
    workflow_id: str = Field(..., alias="workflowId", min_length=1)
    run_id: str = Field(..., alias="runId", min_length=1)
    logical_step_id: str = Field(..., alias="logicalStepId", min_length=1)
    execution_ordinal: int = Field(..., alias="executionOrdinal", ge=1)
    source_execution_ordinal: int | None = Field(None, alias="sourceExecutionOrdinal", ge=1)
    lineage: StepExecutionLineageModel | None = Field(None, alias="lineage")
    branch: StepExecutionBranchMetadataModel | None = Field(None, alias="branch")
    reason: StepExecutionReason | None = Field(None, alias="reason")
    status: StepExecutionStatus | None = Field(None, alias="status")
    terminal_disposition: StepExecutionTerminalDisposition | None = Field(
        None, alias="terminalDisposition"
    )
    started_at: datetime | None = Field(None, alias="startedAt")
    updated_at: datetime | None = Field(None, alias="updatedAt")
    timing: StepTimingModel | None = Field(None, alias="timing")
    summary: str | None = Field(None, alias="summary", max_length=1000)
    runtime_child_refs: dict[str, Any] = Field(
        default_factory=dict, alias="runtimeChildRefs"
    )
    workspace_policy: str | None = Field(None, alias="workspacePolicy")
    git_disposition: str | None = Field(None, alias="gitDisposition")
    quality_gate_verdict: str | None = Field(None, alias="qualityGateVerdict")
    manifest_refs: dict[str, Any] = Field(default_factory=dict, alias="manifestRefs")
    output_refs: dict[str, Any] = Field(default_factory=dict, alias="outputRefs")
    evidence_summary: StepEvidenceSummaryModel | None = Field(
        None, alias="stepEvidence"
    )
    recovery_eligibility: RecoveryEligibilityDiagnosticModel | None = Field(
        None, alias="recoveryEligibility"
    )
    compatibility_decision: CompatibilityBoundaryDecisionModel | None = Field(
        None, alias="compatibilityDecision"
    )

    @model_validator(mode="after")
    def _populate_timing(self) -> "StepExecutionProjectionModel":
        if self.timing is None:
            self.timing = StepTimingModel.model_validate(
                _fallback_step_timing_payload(
                    status=self.status,
                    started_at=self.started_at,
                    updated_at=self.updated_at,
                    status_source="step_execution",
                )
            )
        return self


class StepExecutionDetailModel(StepExecutionProjectionModel):
    """Bounded Step Execution detail projection with section refs only."""

    input_refs: dict[str, Any] = Field(default_factory=dict, alias="inputRefs")
    context_refs: dict[str, Any] = Field(default_factory=dict, alias="contextRefs")
    workspace_refs: dict[str, Any] = Field(default_factory=dict, alias="workspaceRefs")
    execution_refs: dict[str, Any] = Field(default_factory=dict, alias="executionRefs")
    check_refs: dict[str, Any] | list[Any] = Field(
        default_factory=dict, alias="checkRefs"
    )
    side_effect_refs: dict[str, Any] = Field(
        default_factory=dict, alias="sideEffectRefs"
    )
    dependency_effect_refs: dict[str, Any] = Field(
        default_factory=dict, alias="dependencyEffectRefs"
    )
    preserved_step_provenance: list[PreservedStepProvenanceDetailModel] = Field(
        default_factory=list, alias="preservedStepProvenance"
    )


class StepExecutionListModel(BaseModel):
    """Bounded Step Execution history for one logical step."""

    model_config = ConfigDict(populate_by_name=True)

    workflow_id: str = Field(..., alias="workflowId", min_length=1)
    run_id: str = Field(..., alias="runId", min_length=1)
    run_scope: Literal["latest"] = Field("latest", alias="runScope")
    logical_step_id: str = Field(..., alias="logicalStepId", min_length=1)
    step_executions: list[StepExecutionProjectionModel] = Field(
        default_factory=list, alias="stepExecutions"
    )


class ExecutionReportProjectionModel(BaseModel):
    """Bounded report summary surfaced on execution detail responses."""

    model_config = ConfigDict(populate_by_name=True)

    has_report: bool = Field(..., alias="hasReport")
    latest_report_ref: CompactArtifactRefModel | None = Field(
        None, alias="latestReportRef"
    )
    latest_report_summary_ref: CompactArtifactRefModel | None = Field(
        None, alias="latestReportSummaryRef"
    )
    report_artifact_refs: dict[str, CompactArtifactRefModel] | None = Field(
        None, alias="reportArtifactRefs"
    )
    report_type: str | None = Field(None, alias="reportType")
    report_status: str | None = Field(None, alias="reportStatus")
    finding_counts: dict[str, int] | None = Field(None, alias="findingCounts")
    severity_counts: dict[str, int] | None = Field(None, alias="severityCounts")

    @model_serializer(mode="wrap")
    def _serialize_without_nulls(self, handler):
        payload = handler(self)
        return {key: value for key, value in payload.items() if value is not None}

class ExecutionResumeSummaryModel(BaseModel):
    """Failed-step recovery availability and checkpoint summary."""

    model_config = ConfigDict(populate_by_name=True)

    available: bool = Field(False, alias="available")
    checkpoint_ref: Optional[str] = Field(None, alias="checkpointRef")
    failed_step_id: Optional[str] = Field(None, alias="failedStepId")
    source_run_id: Optional[str] = Field(None, alias="sourceRunId")
    disabled_reason: Optional[str] = Field(None, alias="disabledReason")

class ExecutionRelatedRunModel(BaseModel):
    """Operator-visible relationship between source and resumed executions."""

    model_config = ConfigDict(populate_by_name=True)

    workflow_id: str = Field(..., alias="workflowId", min_length=1)
    run_id: Optional[str] = Field(None, alias="runId")
    relationship: str = Field(..., alias="relationship", min_length=1)
    status: Optional[str] = Field(None, alias="status")
    target_runtime: Optional[str] = Field(None, alias="targetRuntime")
    model: Optional[str] = Field(None, alias="model")
    requested_model: Optional[str] = Field(None, alias="requestedModel")
    resolved_model: Optional[str] = Field(None, alias="resolvedModel")
    effort: Optional[str] = Field(None, alias="effort")
    created_at: Optional[datetime] = Field(None, alias="createdAt")
    href: str = Field(..., alias="href", min_length=1)

class ExecutionRecurrenceProvenanceModel(BaseModel):
    """Schedule provenance for executions spawned by recurring definitions."""

    model_config = ConfigDict(populate_by_name=True)

    definition_id: str = Field(..., alias="definitionId", min_length=1)
    href: str = Field(..., alias="href", min_length=1)

class ExecutionTargetDiagnosticAttachmentModel(BaseModel):
    """Attachment bound to the objective or a single executable step."""

    model_config = ConfigDict(populate_by_name=True)

    artifact_ref: str | None = Field(None, alias="artifactRef")
    filename: str | None = Field(None, alias="filename")
    content_type: str | None = Field(None, alias="contentType")
    size_bytes: int | None = Field(None, alias="sizeBytes", ge=0)
    preview_available: bool = Field(False, alias="previewAvailable")

class ExecutionTargetDiagnosticRefModel(BaseModel):
    """Semantic evidence ref associated with target preparation."""

    model_config = ConfigDict(populate_by_name=True)

    ref_kind: str = Field(..., alias="refKind", min_length=1)
    artifact_ref: str | None = Field(None, alias="artifactRef")
    path: str | None = Field(None, alias="path")

class ExecutionTargetDiagnosticFailureModel(BaseModel):
    """Failure evidence for upload, validation, materialization, or context build."""

    model_config = ConfigDict(populate_by_name=True)

    phase: Literal[
        "upload",
        "validation",
        "materialization",
        "context_generation",
        "degraded",
    ] = Field(..., alias="phase")
    message: str = Field(..., alias="message", min_length=1)
    evidence_ref: str | None = Field(None, alias="evidenceRef")

class ExecutionTargetDiagnosticTargetModel(BaseModel):
    """Diagnostics for one target scope: objective or individual step."""

    model_config = ConfigDict(populate_by_name=True)

    target_kind: Literal["objective", "step"] = Field(..., alias="targetKind")
    step_id: str | None = Field(None, alias="stepId")
    label: str = Field(..., alias="label", min_length=1)
    attachments: list[ExecutionTargetDiagnosticAttachmentModel] = Field(
        default_factory=list,
        alias="attachments",
    )
    refs: list[ExecutionTargetDiagnosticRefModel] = Field(
        default_factory=list,
        alias="refs",
    )
    failures: list[ExecutionTargetDiagnosticFailureModel] = Field(
        default_factory=list,
        alias="failures",
    )

class ExecutionTargetDiagnosticsPreservedStepModel(BaseModel):
    """Source provenance for a step output preserved into a resumed run."""

    model_config = ConfigDict(populate_by_name=True)

    logical_step_id: str = Field(..., alias="logicalStepId", min_length=1)
    title: str | None = Field(None, alias="title")
    source_execution_ordinal: int | None = Field(None, alias="sourceExecutionOrdinal", ge=1)
    source_workflow_id: str | None = Field(None, alias="sourceWorkflowId")
    source_run_id: str | None = Field(None, alias="sourceRunId")

class ExecutionTargetDiagnosticsRecoveryModel(BaseModel):
    """Failed-step recovery provenance and degraded recovery diagnostics."""

    model_config = ConfigDict(populate_by_name=True)

    resumed: bool = Field(False, alias="resumed")
    source_workflow_id: str | None = Field(None, alias="sourceWorkflowId")
    source_run_id: str | None = Field(None, alias="sourceRunId")
    checkpoint_ref: str | None = Field(None, alias="checkpointRef")
    preserved_steps: list[ExecutionTargetDiagnosticsPreservedStepModel] = Field(
        default_factory=list,
        alias="preservedSteps",
    )
    failed_recovery_phase: Literal[
        "checkpoint_validation",
        "workspace_restoration",
        "preserved_output_injection",
        "failed_step_execution",
    ] | None = Field(None, alias="failedRecoveryPhase")

class ExecutionTargetDiagnosticsModel(BaseModel):
    """Target-aware task diagnostics surfaced on execution detail."""

    model_config = ConfigDict(populate_by_name=True)

    targets: list[ExecutionTargetDiagnosticTargetModel] = Field(
        default_factory=list,
        alias="targets",
    )
    recovery: ExecutionTargetDiagnosticsRecoveryModel | None = Field(
        None,
        alias="recovery",
    )
    degraded_reason: str | None = Field(None, alias="degradedReason")

class ExecutionOutputBranchModel(BaseModel):
    """Verified repository output evidence, distinct from authored inputs."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    name: str
    url: str | None = None
    head_sha: str | None = Field(None, alias="headSha")
    base_branch: str | None = Field(None, alias="baseBranch")
    intent: Literal["normal", "terminal_checkpoint"] = "normal"
    status: str
    evidence_ref: str | None = Field(None, alias="evidenceRef")


class ExecutionModel(BaseModel):
    """Materialized execution view returned by lifecycle APIs."""

    model_config = ConfigDict(populate_by_name=True)

    source: Literal["temporal"] = Field("temporal", alias="source")
    task_id: SkipJsonSchema[str | None] = Field(None, alias="taskId", exclude=True)
    agent_run_id: Optional[str] = Field(None, alias="agentRunId")
    progress: ExecutionProgressModel | None = Field(None, alias="progress")
    namespace: str = Field(..., alias="namespace")
    workflow_id: str = Field(..., alias="workflowId")
    run_id: str = Field(..., alias="runId")
    temporal_run_id: str | None = Field(None, alias="temporalRunId", exclude=True)
    legacy_run_id: Optional[str] = Field(None, alias="legacyRunId", exclude=True)
    workflow_type: str = Field(..., alias="workflowType")
    entry: Literal["user_workflow", "manifest"] = Field(..., alias="entry")
    owner_type: Literal["user", "system", "service"] = Field(..., alias="ownerType")
    owner_id: str = Field(..., alias="ownerId")
    title: str = Field(..., alias="title")
    summary: str = Field(..., alias="summary")
    task_instructions: Optional[str] = Field(None, alias="taskInstructions")
    status: Literal[
        "queued",
        "running",
        "awaiting_action",
        "waiting",
        "completed",
        "failed",
        "canceled",
    ] = Field(..., alias="status")
    dashboard_status: Literal[
        "queued",
        "running",
        "awaiting_action",
        "waiting",
        "completed",
        "failed",
        "canceled",
    ] = Field(..., alias="dashboardStatus")
    state: str = Field(..., alias="state")
    raw_state: str = Field(..., alias="rawState")
    temporal_status: TemporalStatusValue = Field(
        ..., alias="temporalStatus"
    )
    close_status: Optional[str] = Field(None, alias="closeStatus")
    waiting_reason: Optional[str] = Field(None, alias="waitingReason")
    attention_required: bool = Field(False, alias="attentionRequired")
    intervention_audit: list[ExecutionInterventionAuditEntryModel] = Field(
        default_factory=list, alias="interventionAudit"
    )
    search_attributes: dict[str, Any] = Field(
        default_factory=dict, alias="searchAttributes"
    )
    memo: dict[str, Any] = Field(default_factory=dict, alias="memo")
    input_parameters: dict[str, Any] = Field(
        default_factory=dict, alias="inputParameters"
    )
    input_artifact_ref: Optional[str] = Field(None, alias="inputArtifactRef")
    task_input_snapshot: WorkflowInputSnapshotDescriptorModel = Field(
        default_factory=WorkflowInputSnapshotDescriptorModel,
        alias="taskInputSnapshot",
    )
    target_runtime: Optional[str] = Field(None, alias="targetRuntime")
    target_skill: Optional[str] = Field(None, alias="targetSkill")
    model: Optional[str] = Field(None, alias="model")
    requested_model: Optional[str] = Field(None, alias="requestedModel")
    resolved_model: Optional[str] = Field(None, alias="resolvedModel")
    model_source: Optional[str] = Field(None, alias="modelSource")
    profile_id: Optional[str] = Field(None, alias="profileId")
    provider_id: Optional[str] = Field(None, alias="providerId")
    provider_label: Optional[str] = Field(None, alias="providerLabel")
    effort: Optional[str] = Field(None, alias="effort")
    priority: Optional[int] = Field(None, alias="priority")
    starting_branch: Optional[str] = Field(None, alias="startingBranch")
    target_branch: Optional[str] = Field(None, alias="targetBranch")
    output_branch: Optional["ExecutionOutputBranchModel"] = Field(
        None,
        alias="outputBranch",
        description="Remotely verified branch produced by publication.",
    )
    repository: Optional[str] = Field(None, alias="repository")
    pr_url: Optional[str] = Field(
        None,
        alias="prUrl",
        description="URL of the pull request associated with this execution.",
    )
    publish_mode: Optional[str] = Field(None, alias="publishMode")
    merge_automation: ExecutionMergeAutomationModel | None = Field(
        None,
        alias="mergeAutomation",
    )
    resolved_skillset_ref: Optional[str] = Field(None, alias="resolvedSkillsetRef")
    task_skills: Optional[list[str]] = Field(None, alias="taskSkills")
    skill_runtime: Optional[ExecutionSkillRuntimeModel] = Field(
        None, alias="skillRuntime"
    )
    artifact_refs: list[str] = Field(default_factory=list, alias="artifactRefs")
    report_projection: ExecutionReportProjectionModel | None = Field(
        None, alias="reportProjection"
    )
    actions: ExecutionActionCapabilityModel = Field(
        default_factory=ExecutionActionCapabilityModel, alias="actions"
    )
    resume: ExecutionResumeSummaryModel | None = Field(None, alias="resume")
    recovery_eligibility: RecoveryEligibilityDiagnosticModel | None = Field(
        None, alias="recoveryEligibility"
    )
    related_runs: list[ExecutionRelatedRunModel] = Field(
        default_factory=list, alias="relatedRuns"
    )
    recurrence: ExecutionRecurrenceProvenanceModel | None = Field(
        None, alias="recurrence"
    )
    target_diagnostics: ExecutionTargetDiagnosticsModel | None = Field(
        None, alias="targetDiagnostics"
    )
    run_metrics: dict[str, Any] | None = Field(None, alias="runMetrics")
    improvement_signals: list[dict[str, Any]] = Field(
        default_factory=list, alias="improvementSignals"
    )
    recommended_next_action: str | None = Field(
        None, alias="recommendedNextAction"
    )
    log_context: dict[str, Any] | None = Field(None, alias="logContext")
    proposal_summary: dict[str, Any] | None = Field(None, alias="proposalSummary")
    proposal_outcomes: list[dict[str, Any]] = Field(
        default_factory=list, alias="proposalOutcomes"
    )
    finish_outcome_code: str | None = Field(None, alias="finishOutcomeCode")
    finish_summary: dict[str, Any] | None = Field(None, alias="finishSummary")
    debug_fields: Optional[ExecutionDebugFieldsModel] = Field(None, alias="debugFields")
    redirect_path: Optional[str] = Field(None, alias="redirectPath")
    manifest_artifact_ref: Optional[str] = Field(None, alias="manifestArtifactRef")
    plan_artifact_ref: Optional[str] = Field(None, alias="planArtifactRef")
    summary_artifact_ref: Optional[str] = Field(None, alias="summaryArtifactRef")
    run_index_artifact_ref: Optional[str] = Field(None, alias="runIndexArtifactRef")
    checkpoint_artifact_ref: Optional[str] = Field(None, alias="checkpointArtifactRef")
    requested_by: Optional[RequestedByModel] = Field(None, alias="requestedBy")
    execution_policy: Optional[ManifestExecutionPolicyModel] = Field(
        None,
        alias="executionPolicy",
    )
    phase: Optional[str] = Field(None, alias="phase")
    paused: Optional[bool] = Field(None, alias="paused")
    counts: Optional[ManifestNodeCountsModel] = Field(None, alias="counts")
    artifacts_count: int = Field(0, alias="artifactsCount")
    scheduled_for: Optional[datetime] = Field(None, alias="scheduledFor")
    created_at: datetime = Field(..., alias="createdAt")
    steps_href: Optional[str] = Field(None, alias="stepsHref")
    integration: Optional[IntegrationStateModel] = Field(None, alias="integration")
    latest_run_view: bool = Field(True, alias="latestRunView")
    continue_as_new_cause: Optional[str] = Field(None, alias="continueAsNewCause")
    depends_on: list[str] = Field(default_factory=list, alias="dependsOn")
    has_dependencies: bool = Field(False, alias="hasDependencies")
    dependency_wait_occurred: bool = Field(False, alias="dependencyWaitOccurred")
    dependency_wait_duration_ms: Optional[int] = Field(
        None, alias="dependencyWaitDurationMs"
    )
    dependency_resolution: Optional[str] = Field(None, alias="dependencyResolution")
    failed_dependency_id: Optional[str] = Field(None, alias="failedDependencyId")
    blocked_on_dependencies: bool = Field(False, alias="blockedOnDependencies")
    dependency_outcomes: list[ExecutionDependencyOutcomeModel] = Field(
        default_factory=list, alias="dependencyOutcomes"
    )
    prerequisites: list[ExecutionDependencySummaryModel] = Field(
        default_factory=list, alias="prerequisites"
    )
    dependents: list[ExecutionDependencySummaryModel] = Field(
        default_factory=list, alias="dependents"
    )
    started_at: datetime | None = Field(None, alias="startedAt")
    queued_at: datetime | None = Field(None, alias="queuedAt")
    updated_at: datetime = Field(..., alias="updatedAt")
    closed_at: datetime | None = Field(None, alias="closedAt")
    detail_href: str = Field(..., alias="detailHref")
    ui_query_model: Literal["compatibility_adapter"] = Field(
        "compatibility_adapter", alias="uiQueryModel"
    )
    stale_state: bool = Field(False, alias="staleState")
    refreshed_at: datetime | None = Field(None, alias="refreshedAt")


class ExecutionListItemModel(BaseModel):
    """Compact execution row returned by list APIs.

    The detail endpoint returns ``ExecutionModel``. List views use this bounded
    row model so high-cardinality dashboard polling does not ship memo,
    parameters, summaries, debug fields, or other detail-only payloads.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    source: Literal["temporal"] = Field("temporal", alias="source")
    workflow_id: str = Field(..., alias="workflowId")
    run_id: str = Field(..., alias="runId")
    workflow_type: str = Field(..., alias="workflowType")
    entry: Literal["user_workflow", "manifest"] = Field(..., alias="entry")
    owner_type: Literal["user", "system", "service"] = Field(..., alias="ownerType")
    owner_id: str = Field(..., alias="ownerId")
    title: str = Field(..., alias="title")
    status: Literal[
        "queued",
        "running",
        "awaiting_action",
        "waiting",
        "completed",
        "failed",
        "canceled",
    ] = Field(..., alias="status")
    dashboard_status: Literal[
        "queued",
        "running",
        "awaiting_action",
        "waiting",
        "completed",
        "failed",
        "canceled",
    ] = Field(..., alias="dashboardStatus")
    state: str = Field(..., alias="state")
    raw_state: str = Field(..., alias="rawState")
    temporal_status: TemporalStatusValue = Field(
        ..., alias="temporalStatus"
    )
    close_status: Optional[str] = Field(None, alias="closeStatus")
    waiting_reason: Optional[str] = Field(None, alias="waitingReason")
    attention_required: bool = Field(False, alias="attentionRequired")
    target_runtime: Optional[str] = Field(None, alias="targetRuntime")
    target_skill: Optional[str] = Field(None, alias="targetSkill")
    task_skills: Optional[list[str]] = Field(None, alias="taskSkills")
    repository: Optional[str] = Field(None, alias="repository")
    progress: ExecutionProgressModel | None = Field(None, alias="progress")
    scheduled_for: Optional[datetime] = Field(None, alias="scheduledFor")
    created_at: datetime = Field(..., alias="createdAt")
    started_at: datetime | None = Field(None, alias="startedAt")
    queued_at: datetime | None = Field(None, alias="queuedAt")
    updated_at: datetime = Field(..., alias="updatedAt")
    closed_at: datetime | None = Field(None, alias="closedAt")
    depends_on: list[str] = Field(default_factory=list, alias="dependsOn")
    blocked_on_dependencies: bool = Field(False, alias="blockedOnDependencies")
    detail_href: str = Field(..., alias="detailHref")
    redirect_path: Optional[str] = Field(None, alias="redirectPath")
    latest_run_view: bool = Field(True, alias="latestRunView")
    continue_as_new_cause: Optional[str] = Field(None, alias="continueAsNewCause")
    ui_query_model: Literal["compatibility_adapter"] = Field(
        "compatibility_adapter", alias="uiQueryModel"
    )
    stale_state: bool = Field(False, alias="staleState")
    refreshed_at: datetime | None = Field(None, alias="refreshedAt")


class ExecutionRefreshEnvelope(BaseModel):
    """Compatibility metadata for patching one acted-on row and refetching lists."""

    model_config = ConfigDict(populate_by_name=True)

    ui_query_model: Literal["compatibility_adapter"] = Field(
        "compatibility_adapter", alias="uiQueryModel"
    )
    patched_execution: bool = Field(..., alias="patchedExecution")
    list_stale: bool = Field(..., alias="listStale")
    refetch_suggested: bool = Field(..., alias="refetchSuggested")
    refreshed_at: datetime = Field(..., alias="refreshedAt")

class UpdateExecutionResponse(BaseModel):
    """Outcome from an update command."""

    model_config = ConfigDict(populate_by_name=True)

    accepted: bool = Field(..., alias="accepted")
    applied: Literal["immediate", "next_safe_point", "continue_as_new"] = Field(
        ..., alias="applied"
    )
    message: str = Field(..., alias="message")
    continue_as_new_cause: Optional[str] = Field(None, alias="continueAsNewCause")
    execution: ExecutionModel | None = Field(None, alias="execution")
    refresh: ExecutionRefreshEnvelope | None = Field(None, alias="refresh")

class ScheduleCreatedResponse(BaseModel):
    """Response returned when a recurring schedule is created from the create endpoint."""

    model_config = ConfigDict(populate_by_name=True)

    scheduled: Literal[True] = Field(True, alias="scheduled")
    definition_id: str = Field(..., alias="definitionId")
    name: str = Field(..., alias="name")
    cron: str = Field(..., alias="cron")
    timezone: str = Field(..., alias="timezone")
    next_run_at: datetime = Field(..., alias="nextRunAt")
    redirect_path: str = Field(..., alias="redirectPath")

class ExecutionListResponse(BaseModel):
    """Paginated list response for executions."""

    model_config = ConfigDict(populate_by_name=True)

    items: list[ExecutionListItemModel] = Field(default_factory=list, alias="items")
    next_page_token: Optional[str] = Field(None, alias="nextPageToken")
    count: Optional[int] = Field(None, alias="count")
    count_mode: Literal["exact", "estimated_or_unknown"] = Field(
        "exact", alias="countMode"
    )
    ui_query_model: Literal["compatibility_adapter"] = Field(
        "compatibility_adapter", alias="uiQueryModel"
    )
    stale_state: bool = Field(False, alias="staleState")
    degraded_count: bool = Field(False, alias="degradedCount")
    refreshed_at: datetime | None = Field(None, alias="refreshedAt")

class ExecutionMetricsDurationModel(BaseModel):
    """Run-duration aggregates for the operational metrics dashboard."""

    model_config = ConfigDict(populate_by_name=True)

    average_seconds: float | None = Field(None, alias="averageSeconds", ge=0)
    median_seconds: float | None = Field(None, alias="medianSeconds", ge=0)
    min_seconds: float | None = Field(None, alias="minSeconds", ge=0)
    max_seconds: float | None = Field(None, alias="maxSeconds", ge=0)
    observed_count: int = Field(0, alias="observedCount", ge=0)


class ExecutionMetricsCostModel(BaseModel):
    """Cost aggregates for runs that publish bounded cost metadata."""

    model_config = ConfigDict(populate_by_name=True)

    total_estimate_usd: float = Field(0.0, alias="totalEstimateUsd", ge=0)
    average_estimate_usd: float | None = Field(
        None, alias="averageEstimateUsd", ge=0
    )
    observed_count: int = Field(0, alias="observedCount", ge=0)


class ExecutionMetricsResponse(BaseModel):
    """Operational run metrics for MoonMind dashboards."""

    model_config = ConfigDict(populate_by_name=True)

    total_runs: int = Field(0, alias="totalRuns", ge=0)
    completed_runs: int = Field(0, alias="completedRuns", ge=0)
    failed_runs: int = Field(0, alias="failedRuns", ge=0)
    canceled_runs: int = Field(0, alias="canceledRuns", ge=0)
    terminal_runs: int = Field(0, alias="terminalRuns", ge=0)
    success_rate: float | None = Field(None, alias="successRate", ge=0, le=1)
    duration: ExecutionMetricsDurationModel = Field(
        default_factory=ExecutionMetricsDurationModel, alias="duration"
    )
    cost: ExecutionMetricsCostModel = Field(
        default_factory=ExecutionMetricsCostModel, alias="cost"
    )
    sample_size: int = Field(0, alias="sampleSize", ge=0)
    count_mode: Literal["exact", "estimated_or_unknown"] = Field(
        "exact", alias="countMode"
    )
    refreshed_at: datetime = Field(..., alias="refreshedAt")

class ExecutionFacetItemModel(BaseModel):
    """One value bucket returned by an execution list facet query."""

    model_config = ConfigDict(populate_by_name=True)

    value: str = Field(..., alias="value")
    label: str = Field(..., alias="label")
    count: int = Field(..., alias="count")

class ExecutionFacetResponse(BaseModel):
    """Facet values and counts for execution list column filters."""

    model_config = ConfigDict(populate_by_name=True)

    facet: Literal[
        "status",
        "targetRuntime",
        "targetSkill",
        "repository",
        "integration",
    ] = Field(..., alias="facet")
    items: list[ExecutionFacetItemModel] = Field(default_factory=list, alias="items")
    blank_count: int | None = Field(None, alias="blankCount")
    count_mode: Literal["exact", "estimated_or_unknown"] = Field(
        "exact", alias="countMode"
    )
    truncated: bool = Field(False, alias="truncated")
    next_page_token: Optional[str] = Field(None, alias="nextPageToken")
    source: Literal["authoritative", "current_page_fallback"] = Field(
        "authoritative", alias="source"
    )
