"""Typed Step Execution manifest contracts."""

from __future__ import annotations

from datetime import datetime
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

STEP_EXECUTION_CONTENT_TYPE = "application/vnd.moonmind.step-execution+json;version=1"

StepExecutionReason = Literal[
    "initial_execution",
    "quality_gate_failed",
    "tests_failed",
    "runtime_recovered",
    "recover_from_failed_step",
    "remediation_context",
    "operator_requested",
    "dependency_invalidated",
    "policy_revalidation",
]
StepExecutionStatus = Literal[
    "pending",
    "preparing",
    "executing",
    "checking",
    "completed",
    "failed",
    "blocked",
    "canceled",
    "superseded",
]
StepExecutionTerminalDisposition = Literal[
    "accepted",
    "retryable",
    "blocked",
    "needs_human",
    "discarded",
    "superseded",
    "failed_unrecoverable",
    "failed_with_remaining_work",
]
MemoryEffectState = Literal[
    "proposed",
    "accepted_for_run_context",
    "applied_to_repo",
    "rejected",
    "superseded",
]
MemoryPolicyDecision = Literal[
    "reject",
    "accept_for_run_context",
    "approve_repo_application",
    "supersede",
    "blocked",
]
MemoryApplicationOutcome = Literal["applied", "blocked", "failed", "skipped"]

_UNSAFE_INLINE_PATTERNS = (
    re.compile(r"diff --git", re.IGNORECASE),
    re.compile(r"raw stdout", re.IGNORECASE),
    re.compile(r"raw stderr", re.IGNORECASE),
    re.compile(r"raw logs?", re.IGNORECASE),
    re.compile(r"provider payload", re.IGNORECASE),
    re.compile(r"verification report", re.IGNORECASE),
    re.compile(r"credential value", re.IGNORECASE),
    re.compile(r"ghp_[A-Za-z0-9_]+"),
    re.compile(r"github_pat_[A-Za-z0-9_]+"),
    re.compile(r"AIza[A-Za-z0-9_-]+"),
    re.compile(r"AKIA[A-Z0-9]{16}"),
    re.compile(r"(?i)token\s*=\s*[^/\s&]+"),
    re.compile(r"(?i)password\s*=\s*[^/\s&]+"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)


def _required_text(value: Any, *, field_name: str = "field") -> str:
    candidate = str(value or "").strip()
    if not candidate:
        raise ValueError(f"{field_name} must be a non-empty string")
    _reject_unsafe_inline_text(candidate)
    return candidate


def _required_ref(value: Any, *, field_name: str = "ref") -> str:
    candidate = _required_text(value, field_name=field_name)
    if not candidate.startswith("artifact://"):
        raise ValueError(f"{field_name} must be an artifact ref")
    return candidate


def _optional_ref(value: Any, *, field_name: str = "ref") -> str | None:
    if value is None:
        return None
    return _required_ref(value, field_name=field_name)


def _reject_unsafe_inline_text(value: str) -> None:
    if len(value) > 1000:
        raise ValueError("memory manifests must use artifact refs for large content")
    for pattern in _UNSAFE_INLINE_PATTERNS:
        if pattern.search(value):
            raise ValueError("memory manifests must not inline unsafe content")


def _reject_unsafe_inline_value(value: Any) -> None:
    if isinstance(value, str):
        _reject_unsafe_inline_text(value)
    elif isinstance(value, dict):
        for item in value.values():
            _reject_unsafe_inline_value(item)
    elif isinstance(value, list | tuple | set):
        for item in value:
            _reject_unsafe_inline_value(item)


class _MemoryManifestBase(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    @model_validator(mode="after")
    def _reject_unsafe_inline_payloads(self):
        _reject_unsafe_inline_value(self.model_dump(mode="python"))
        return self


class StepExecutionIdentityModel(BaseModel):
    """Run-scoped identity for one semantic logical-step execution."""

    model_config = ConfigDict(populate_by_name=True)

    workflow_id: str = Field(..., alias="workflowId", min_length=1)
    run_id: str = Field(..., alias="runId", min_length=1)
    logical_step_id: str = Field(..., alias="logicalStepId", min_length=1)
    execution_ordinal: int = Field(..., alias="executionOrdinal", ge=1)

    @field_validator("workflow_id", "run_id", "logical_step_id", mode="before")
    @classmethod
    def _strip_required_text(cls, value: Any) -> str:
        candidate = str(value or "").strip()
        if not candidate:
            raise ValueError("field must be a non-empty string")
        return candidate

    @property
    def step_execution_id(self) -> str:
        return (
            f"{self.workflow_id}:{self.run_id}:{self.logical_step_id}:"
            f"execution:{self.execution_ordinal}"
        )


class MemoryProposalManifest(_MemoryManifestBase):
    """Artifact-backed proposal for a Step Execution memory effect."""

    schema_version: Literal["v1"] = Field("v1", alias="schemaVersion")
    proposal_id: str = Field(..., alias="proposalId", min_length=1)
    source: StepExecutionIdentityModel
    target: str = Field(..., min_length=1)
    state: MemoryEffectState
    kind: str = Field(..., min_length=1)
    reason: str = Field(..., min_length=1)
    content_ref: str = Field(..., alias="contentRef", min_length=1)
    evidence_refs: list[str] = Field(default_factory=list, alias="evidenceRefs")
    supersedes_proposal_ref: str | None = Field(
        None,
        alias="supersedesProposalRef",
    )
    created_at: datetime = Field(..., alias="createdAt")

    @field_validator("proposal_id", "target", "kind", "reason", mode="before")
    @classmethod
    def _strip_required_manifest_text(cls, value: Any) -> str:
        return _required_text(value)

    @field_validator("content_ref", mode="before")
    @classmethod
    def _validate_content_ref(cls, value: Any) -> str:
        return _required_ref(value, field_name="contentRef")

    @field_validator("evidence_refs", mode="before")
    @classmethod
    def _validate_evidence_refs(cls, value: Any) -> list[str]:
        return [_required_ref(item, field_name="evidenceRefs") for item in value or []]

    @field_validator("supersedes_proposal_ref", mode="before")
    @classmethod
    def _validate_supersedes_ref(cls, value: Any) -> str | None:
        return _optional_ref(value, field_name="supersedesProposalRef")


class MemoryPolicyDecisionManifest(_MemoryManifestBase):
    """Reviewable policy decision for a memory promotion attempt."""

    schema_version: Literal["v1"] = Field("v1", alias="schemaVersion")
    decision_id: str = Field(..., alias="decisionId", min_length=1)
    proposal_ref: str = Field(..., alias="proposalRef", min_length=1)
    source: StepExecutionIdentityModel
    target: str = Field(..., min_length=1)
    reason: str = Field(..., min_length=1)
    decision: MemoryPolicyDecision
    decision_ref: str = Field(..., alias="decisionRef", min_length=1)
    evidence_refs: list[str] = Field(default_factory=list, alias="evidenceRefs")
    gate_status: dict[str, Any] = Field(default_factory=dict, alias="gateStatus")
    created_at: datetime = Field(..., alias="createdAt")

    @field_validator("decision_id", "target", "reason", mode="before")
    @classmethod
    def _strip_required_manifest_text(cls, value: Any) -> str:
        return _required_text(value)

    @field_validator("proposal_ref", "decision_ref", mode="before")
    @classmethod
    def _validate_required_refs(cls, value: Any) -> str:
        return _required_ref(value)

    @field_validator("evidence_refs", mode="before")
    @classmethod
    def _validate_evidence_refs(cls, value: Any) -> list[str]:
        return [_required_ref(item, field_name="evidenceRefs") for item in value or []]

    @model_validator(mode="after")
    def _promotion_requires_gate_evidence(self) -> "MemoryPolicyDecisionManifest":
        if self.decision == "approve_repo_application":
            accepted = self.gate_status.get("terminalDisposition") == "accepted"
            publication = self.gate_status.get("publicationGate") is True
            policy = self.gate_status.get("policyGate", True) is True
            if not (accepted and publication and policy):
                raise ValueError("repo memory approval requires accepted gates")
        return self


class MemoryApplicationResultManifest(_MemoryManifestBase):
    """Result of applying or attempting to apply an approved memory effect."""

    schema_version: Literal["v1"] = Field("v1", alias="schemaVersion")
    application_id: str = Field(..., alias="applicationId", min_length=1)
    proposal_ref: str = Field(..., alias="proposalRef", min_length=1)
    decision_ref: str = Field(..., alias="decisionRef", min_length=1)
    source: StepExecutionIdentityModel
    target: str = Field(..., min_length=1)
    outcome: MemoryApplicationOutcome
    result_ref: str | None = Field(None, alias="resultRef")
    failure_reason: str | None = Field(None, alias="failureReason")
    gate_status: dict[str, Any] = Field(default_factory=dict, alias="gateStatus")
    created_at: datetime = Field(..., alias="createdAt")

    @field_validator("application_id", "target", mode="before")
    @classmethod
    def _strip_required_manifest_text(cls, value: Any) -> str:
        return _required_text(value)

    @field_validator("proposal_ref", "decision_ref", mode="before")
    @classmethod
    def _validate_required_refs(cls, value: Any) -> str:
        return _required_ref(value)

    @field_validator("result_ref", mode="before")
    @classmethod
    def _validate_result_ref(cls, value: Any) -> str | None:
        return _optional_ref(value, field_name="resultRef")

    @field_validator("failure_reason", mode="before")
    @classmethod
    def _validate_failure_reason(cls, value: Any) -> str | None:
        if value is None:
            return None
        return _required_text(value, field_name="failureReason")

    @model_validator(mode="after")
    def _application_result_evidence_matches_outcome(
        self,
    ) -> "MemoryApplicationResultManifest":
        if self.outcome == "applied" and self.result_ref is None:
            raise ValueError("applied memory result requires resultRef")
        if self.outcome != "applied" and not self.failure_reason:
            raise ValueError("non-applied memory result requires failureReason")
        if self.target.startswith("repo://") and self.outcome == "applied":
            accepted = self.gate_status.get("terminalDisposition") == "accepted"
            publication = self.gate_status.get("publicationGate") is True
            policy = self.gate_status.get("policyGate", True) is True
            if not (accepted and publication and policy):
                raise ValueError("applied repo memory result requires accepted gates")
        return self


class MemorySideEffectSummary(_MemoryManifestBase):
    """Compact terminal Step Execution projection for a memory effect."""

    state: MemoryEffectState
    target: str = Field(..., min_length=1)
    reason: str = Field(..., min_length=1)
    proposal_ref: str = Field(..., alias="proposalRef", min_length=1)
    decision_ref: str | None = Field(None, alias="decisionRef")
    application_result_ref: str | None = Field(None, alias="applicationResultRef")
    source: StepExecutionIdentityModel
    privileged_action: dict[str, Any] | None = Field(None, alias="privilegedAction")

    @field_validator("target", "reason", mode="before")
    @classmethod
    def _strip_required_manifest_text(cls, value: Any) -> str:
        return _required_text(value)

    @field_validator("proposal_ref", mode="before")
    @classmethod
    def _validate_proposal_ref(cls, value: Any) -> str:
        return _required_ref(value, field_name="proposalRef")

    @field_validator("decision_ref", "application_result_ref", mode="before")
    @classmethod
    def _validate_optional_refs(cls, value: Any) -> str | None:
        return _optional_ref(value)


class StepExecutionManifestModel(BaseModel):
    """Artifact-backed contract for one Step Execution."""

    model_config = ConfigDict(populate_by_name=True)

    schema_version: Literal["v1"] = Field("v1", alias="schemaVersion")
    content_type: Literal[STEP_EXECUTION_CONTENT_TYPE] = Field(
        STEP_EXECUTION_CONTENT_TYPE,
        alias="contentType",
    )
    step_execution_id: str | None = Field(None, alias="stepExecutionId")
    workflow_id: str = Field(..., alias="workflowId", min_length=1)
    run_id: str = Field(..., alias="runId", min_length=1)
    logical_step_id: str = Field(..., alias="logicalStepId", min_length=1)
    execution_ordinal: int = Field(..., alias="executionOrdinal", ge=1)
    execution_scope: Literal["run"] = Field("run", alias="executionScope")
    lineage: dict[str, Any] | None = Field(None, alias="lineage")
    reason: StepExecutionReason = Field(..., alias="reason")
    status: StepExecutionStatus = Field(..., alias="status")
    terminal_disposition: StepExecutionTerminalDisposition | None = Field(
        None,
        alias="terminalDisposition",
    )
    started_at: datetime = Field(..., alias="startedAt")
    updated_at: datetime = Field(..., alias="updatedAt")
    input: dict[str, Any] = Field(default_factory=dict, alias="input")
    context: dict[str, Any] = Field(default_factory=dict, alias="context")
    workspace: dict[str, Any] = Field(default_factory=dict, alias="workspace")
    execution: dict[str, Any] = Field(default_factory=dict, alias="execution")
    outputs: dict[str, Any] = Field(default_factory=dict, alias="outputs")
    checks: list[dict[str, Any]] = Field(default_factory=list, alias="checks")
    side_effects: dict[str, Any] = Field(default_factory=dict, alias="sideEffects")
    dependency_effects: dict[str, Any] = Field(
        default_factory=dict,
        alias="dependencyEffects",
    )
    recovery_source: dict[str, Any] = Field(
        default_factory=dict,
        alias="recoverySource",
    )
    budget: dict[str, Any] = Field(default_factory=dict, alias="budget")

    @field_validator("workflow_id", "run_id", "logical_step_id", mode="before")
    @classmethod
    def _strip_required_text(cls, value: Any) -> str:
        candidate = str(value or "").strip()
        if not candidate:
            raise ValueError("field must be a non-empty string")
        return candidate

    @model_validator(mode="after")
    def _derive_step_execution_id(self) -> "StepExecutionManifestModel":
        expected = StepExecutionIdentityModel(
            workflowId=self.workflow_id,
            runId=self.run_id,
            logicalStepId=self.logical_step_id,
            executionOrdinal=self.execution_ordinal,
        ).step_execution_id
        if self.step_execution_id is None:
            self.step_execution_id = expected
        elif self.step_execution_id != expected:
            raise ValueError("stepExecutionId must match Step Execution identity")
        return self
