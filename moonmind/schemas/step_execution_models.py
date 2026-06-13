"""Typed Step Execution manifest contracts."""

from __future__ import annotations

from datetime import datetime
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
    "running",
    "checking",
    "succeeded",
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
HandoffDecision = Literal["allow", "block"]


def _strip_text(value: Any) -> str:
    candidate = str(value or "").strip()
    if not candidate:
        raise ValueError("field must be a non-empty string")
    return candidate


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
        return _strip_text(value)

    @property
    def step_execution_id(self) -> str:
        return (
            f"{self.workflow_id}:{self.run_id}:{self.logical_step_id}:"
            f"execution:{self.execution_ordinal}"
        )


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
    budget: dict[str, Any] = Field(default_factory=dict, alias="budget")

    @field_validator("workflow_id", "run_id", "logical_step_id", mode="before")
    @classmethod
    def _strip_required_text(cls, value: Any) -> str:
        return _strip_text(value)

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


class ProducingStepExecutionEvidenceModel(BaseModel):
    """Compact source Step Execution evidence used to authorize handoffs."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    workflow_id: str = Field(..., alias="workflowId", min_length=1)
    run_id: str = Field(..., alias="runId", min_length=1)
    logical_step_id: str = Field(..., alias="logicalStepId", min_length=1)
    execution_ordinal: int = Field(..., alias="executionOrdinal", ge=1)
    step_execution_id: str | None = Field(None, alias="stepExecutionId")
    terminal_disposition: str | None = Field(None, alias="terminalDisposition")
    manifest_ref: str | None = Field(None, alias="manifestRef")
    output_refs: dict[str, Any] | None = Field(None, alias="outputRefs")

    @field_validator("workflow_id", "run_id", "logical_step_id", mode="before")
    @classmethod
    def _strip_required_text(cls, value: Any) -> str:
        return _strip_text(value)

    @field_validator("terminal_disposition", "manifest_ref", mode="before")
    @classmethod
    def _strip_optional_text(cls, value: Any) -> str | None:
        candidate = str(value or "").strip()
        return candidate or None

    @model_validator(mode="after")
    def _derive_step_execution_id(self) -> "ProducingStepExecutionEvidenceModel":
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


class StructuredGateSourceModel(BaseModel):
    """Compact structured source for the gate that authorizes a handoff."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    gate_type: str = Field(..., alias="gateType", min_length=1)
    verdict: str | None = None
    passed: bool = False
    logical_step_id: str | None = Field(None, alias="logicalStepId")
    evidence_ref: str | None = Field(None, alias="evidenceRef")
    diagnostics_ref: str | None = Field(None, alias="diagnosticsRef")

    @field_validator("gate_type", mode="before")
    @classmethod
    def _strip_required_text(cls, value: Any) -> str:
        return _strip_text(value)

    @field_validator(
        "verdict",
        "logical_step_id",
        "evidence_ref",
        "diagnostics_ref",
        mode="before",
    )
    @classmethod
    def _strip_optional_text(cls, value: Any) -> str | None:
        candidate = str(value or "").strip()
        return candidate or None

    @model_validator(mode="after")
    def _fail_closed_for_blank_verdict(self) -> "StructuredGateSourceModel":
        if self.passed and not self.verdict:
            raise ValueError("passed gate source requires a non-empty verdict")
        return self


class HandoffGateInput(BaseModel):
    """Workflow/activity boundary input for privileged external handoff gates."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    workflow_id: str = Field(..., alias="workflowId", min_length=1)
    run_id: str = Field(..., alias="runId", min_length=1)
    handoff_class: str = Field(..., alias="handoffClass", min_length=1)
    actor: dict[str, Any] | str
    action: str = Field(..., min_length=1)
    target: dict[str, Any] | str | None = None
    idempotency_key: str | None = Field(None, alias="idempotencyKey")
    requires_idempotency_key: bool = Field(True, alias="requiresIdempotencyKey")
    requires_gate: bool = Field(True, alias="requiresGate")
    requires_explicit_policy_approval: bool = Field(
        False,
        alias="requiresExplicitPolicyApproval",
    )
    explicit_policy_approval: bool = Field(False, alias="explicitPolicyApproval")
    producing_step_execution: ProducingStepExecutionEvidenceModel = Field(
        ...,
        alias="producingStepExecution",
    )
    gate_source: StructuredGateSourceModel | None = Field(None, alias="gateSource")
    outbound_payload_refs: list[str] = Field(
        default_factory=list,
        alias="outboundPayloadRefs",
    )
    evidence_refs: list[str] = Field(default_factory=list, alias="evidenceRefs")

    @field_validator("workflow_id", "run_id", "handoff_class", "action", mode="before")
    @classmethod
    def _strip_required_text(cls, value: Any) -> str:
        return _strip_text(value)

    @field_validator("idempotency_key", mode="before")
    @classmethod
    def _strip_optional_text(cls, value: Any) -> str | None:
        candidate = str(value or "").strip()
        return candidate or None


class HandoffGateDecision(BaseModel):
    """Deterministic compact decision for a privileged/external handoff."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    allowed: bool
    decision: HandoffDecision
    handoff_class: str = Field(..., alias="handoffClass")
    actor: dict[str, Any] | str
    action: str
    target: dict[str, Any] | str | None = None
    idempotency_key: str | None = Field(None, alias="idempotencyKey")
    policy_decision: dict[str, Any] = Field(..., alias="policyDecision")
    gate_source: dict[str, Any] | None = Field(None, alias="gateSource")
    disposition_source: dict[str, Any] = Field(..., alias="dispositionSource")
    outbound_scan: dict[str, Any] | None = Field(None, alias="outboundScan")
    evidence_refs: list[str] = Field(default_factory=list, alias="evidenceRefs")
    diagnostics: list[str] = Field(default_factory=list)
