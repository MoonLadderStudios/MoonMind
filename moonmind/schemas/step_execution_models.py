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
