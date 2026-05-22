"""Contracts for validating hard-switch Temporal cutover readiness."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class CutoverContractCategory(StrEnum):
    WORKFLOW = "workflow"
    ACTIVITY_PAYLOAD = "activity_payload"
    SIGNAL = "signal"
    UPDATE = "update"


class ContractCutoverStrategy(StrEnum):
    VERSION_MARKER = "version_marker"
    NEW_WORKFLOW_TYPE = "new_workflow_type"
    WORKER_TASK_QUEUE_SPLIT = "worker_task_queue_split"
    DRAIN = "drain"
    PAUSE_RESUME = "pause_resume"
    TERMINATE_RESTART = "terminate_restart"
    DOCUMENTED_EQUIVALENT = "documented_equivalent"


class NewStartsBoundary(StrEnum):
    RENAMED_CONTRACT_WORKER = "renamed_contract_worker"
    NEW_WORKFLOW_TYPE = "new_workflow_type"
    DOCUMENTED_EQUIVALENT = "documented_equivalent"
    SAME_WORKER = "same_worker"


class EnvironmentCutoverDecision(StrEnum):
    DRAIN = "drain"
    PAUSE_RESUME = "pause_resume"
    TERMINATE_RESTART = "terminate_restart"
    DOCUMENTED_EQUIVALENT = "documented_equivalent"


class CompatibilityMode(StrEnum):
    NONE = "none"
    EXPLICIT_VERSIONED = "explicit_versioned"
    ALIAS = "alias"
    REDIRECT = "redirect"
    TRANSLATION_LAYER = "translation_layer"


class CutoverFindingSeverity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


class AffectedContract(BaseModel):
    model_config = ConfigDict(populate_by_name=True, use_enum_values=False)

    name: str = Field(..., min_length=1)
    strategy: ContractCutoverStrategy
    category: CutoverContractCategory | None = None
    notes: str | None = None

    @field_validator("name", "notes")
    @classmethod
    def _strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip()


class AffectedContracts(BaseModel):
    model_config = ConfigDict(populate_by_name=True, use_enum_values=False)

    workflows: list[AffectedContract] = Field(default_factory=list)
    activity_payloads: list[AffectedContract] = Field(
        default_factory=list, alias="activityPayloads"
    )
    signals: list[AffectedContract] = Field(default_factory=list)
    updates: list[AffectedContract] = Field(default_factory=list)

    @model_validator(mode="after")
    def _assign_categories(self) -> "AffectedContracts":
        for contract in self.workflows:
            contract.category = CutoverContractCategory.WORKFLOW
        for contract in self.activity_payloads:
            contract.category = CutoverContractCategory.ACTIVITY_PAYLOAD
        for contract in self.signals:
            contract.category = CutoverContractCategory.SIGNAL
        for contract in self.updates:
            contract.category = CutoverContractCategory.UPDATE
        return self


class WorkerRoutingBoundary(BaseModel):
    model_config = ConfigDict(populate_by_name=True, use_enum_values=False)

    previous_worker_build: str = Field(..., alias="previousWorkerBuild", min_length=1)
    previous_task_queue: str = Field(..., alias="previousTaskQueue", min_length=1)
    new_worker_build: str = Field(..., alias="newWorkerBuild", min_length=1)
    new_task_queue: str = Field(..., alias="newTaskQueue", min_length=1)
    new_starts_boundary: NewStartsBoundary = Field(..., alias="newStartsBoundary")
    single_build_serves_both_shapes: bool = Field(
        False, alias="singleBuildServesBothShapes"
    )
    version_boundary: str | None = Field(None, alias="versionBoundary")

    @field_validator(
        "previous_worker_build",
        "previous_task_queue",
        "new_worker_build",
        "new_task_queue",
        "version_boundary",
    )
    @classmethod
    def _strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip()


class CutoverEnvironmentDecision(BaseModel):
    model_config = ConfigDict(populate_by_name=True, use_enum_values=False)

    environment: str = Field(..., min_length=1)
    decision: EnvironmentCutoverDecision
    record_ref: str | None = Field(None, alias="recordRef")

    @field_validator("environment", "record_ref")
    @classmethod
    def _strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip()


class CutoverReleaseNotes(BaseModel):
    model_config = ConfigDict(populate_by_name=True, use_enum_values=False)

    text: str = Field(..., min_length=1)
    record_ref: str | None = Field(None, alias="recordRef")

    @field_validator("text", "record_ref")
    @classmethod
    def _strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip()


class HardSwitchCutoverRecord(BaseModel):
    model_config = ConfigDict(populate_by_name=True, use_enum_values=False)

    release_name: str = Field(..., alias="releaseName", min_length=1)
    coordinated_release: bool = Field(..., alias="coordinatedRelease")
    affected_contracts: AffectedContracts = Field(..., alias="affectedContracts")
    worker_routing: WorkerRoutingBoundary = Field(..., alias="workerRouting")
    environment_decisions: list[CutoverEnvironmentDecision] = Field(
        default_factory=list, alias="environmentDecisions"
    )
    release_notes: CutoverReleaseNotes = Field(..., alias="releaseNotes")
    compatibility_mode: CompatibilityMode = Field(
        CompatibilityMode.NONE, alias="compatibilityMode"
    )

    @field_validator("release_name")
    @classmethod
    def _strip_release_name(cls, value: str) -> str:
        return value.strip()


class CutoverValidationFinding(BaseModel):
    model_config = ConfigDict(populate_by_name=True, use_enum_values=False)

    code: str
    severity: CutoverFindingSeverity = CutoverFindingSeverity.ERROR
    subject: str
    message: str
    source: str | None = None


class CutoverValidationResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True, use_enum_values=False)

    ready: bool
    findings: list[CutoverValidationFinding] = Field(default_factory=list)
