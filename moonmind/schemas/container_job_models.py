"""Compact, versioned contracts for the Temporal-owned container-job lifecycle."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ContainerJobState = Literal[
    "queued",
    "resolving_workspace",
    "acquiring_image",
    "starting",
    "running",
    "canceling",
    "publishing_evidence",
    "cleaning_up",
    "succeeded",
    "failed",
    "timed_out",
    "canceled",
]
TERMINAL_CONTAINER_JOB_STATES = frozenset(
    {"succeeded", "failed", "timed_out", "canceled"}
)


class ContainerJobInput(BaseModel):
    """Authorized references only; secrets, host paths, and logs are forbidden."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    contract_version: Literal[1] = Field(1, alias="contractVersion")
    job_id: str = Field(..., alias="jobId", min_length=1, max_length=200)
    request_ref: str = Field(..., alias="requestRef", min_length=1, max_length=500)
    workspace_ref: str = Field(..., alias="workspaceRef", min_length=1, max_length=500)
    image_ref: str = Field(..., alias="imageRef", min_length=1, max_length=500)
    timeout_seconds: int = Field(..., alias="timeoutSeconds", ge=1, le=604800)
    observe_interval_seconds: int = Field(
        10, alias="observeIntervalSeconds", ge=1, le=300
    )

    @property
    def ownership_token(self) -> str:
        return f"container-job:{self.job_id}:v{self.contract_version}"


class ContainerJobActivityRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    contract_version: Literal[1] = Field(1, alias="contractVersion")
    job_id: str = Field(..., alias="jobId", min_length=1, max_length=200)
    ownership_token: str = Field(
        ..., alias="ownershipToken", min_length=1, max_length=300
    )
    request_ref: str = Field(..., alias="requestRef", min_length=1, max_length=500)
    workspace_ref: str | None = Field(None, alias="workspaceRef", max_length=500)
    image_ref: str | None = Field(None, alias="imageRef", max_length=500)
    container_ref: str | None = Field(None, alias="containerRef", max_length=500)
    state: ContainerJobState | None = None
    terminal_state: ContainerJobState | None = Field(None, alias="terminalState")
    projection_sequence: int = Field(0, alias="projectionSequence", ge=0)
    publication_token: str | None = Field(None, alias="publicationToken", max_length=300)


class ContainerJobActivityResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    contract_version: Literal[1] = Field(1, alias="contractVersion")
    workspace_ref: str | None = Field(None, alias="workspaceRef", max_length=500)
    image_ref: str | None = Field(None, alias="imageRef", max_length=500)
    container_ref: str | None = Field(None, alias="containerRef", max_length=500)
    running: bool | None = None
    terminal_state: ContainerJobState | None = Field(None, alias="terminalState")
    exit_code: int | None = Field(None, alias="exitCode")
    evidence_refs: tuple[str, ...] = Field(
        default_factory=tuple, alias="evidenceRefs", max_length=100
    )
    diagnostic_codes: tuple[str, ...] = Field(
        default_factory=tuple, alias="diagnosticCodes", max_length=100
    )
    projection_sequence: int | None = Field(
        None, alias="projectionSequence", ge=0
    )

    @model_validator(mode="after")
    def terminal_is_terminal(self) -> "ContainerJobActivityResult":
        if (
            self.terminal_state is not None
            and self.terminal_state not in TERMINAL_CONTAINER_JOB_STATES
        ):
            raise ValueError("terminalState must be terminal")
        return self


class ContainerJobSnapshot(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    contract_version: Literal[1] = Field(1, alias="contractVersion")
    job_id: str = Field(..., alias="jobId")
    state: ContainerJobState
    cancellation_requested: bool = Field(False, alias="cancellationRequested")
    container_ref: str | None = Field(None, alias="containerRef")
    primary_outcome: ContainerJobState | None = Field(None, alias="primaryOutcome")
    evidence_refs: tuple[str, ...] = Field(default_factory=tuple, alias="evidenceRefs")
    publication_diagnostics: tuple[str, ...] = Field(
        default_factory=tuple, alias="publicationDiagnostics"
    )
    cleanup_diagnostics: tuple[str, ...] = Field(
        default_factory=tuple, alias="cleanupDiagnostics"
    )
    projection_sequence: int = Field(0, alias="projectionSequence", ge=0)
    projection_repair_required: bool = Field(
        False, alias="projectionRepairRequired"
    )


def container_job_workflow_id(job_id: str) -> str:
    """Return the one canonical workflow identity for a durable job ID."""
    normalized = str(job_id or "").strip()
    if not normalized or len(normalized) > 200:
        raise ValueError("jobId must contain 1..200 characters")
    return f"container-job:{normalized}"
