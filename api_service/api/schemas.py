import uuid
from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from moonmind.schemas.workflow_models import (
    OrchestratorActionPlanModel,
    OrchestratorApprovalActorModel,
    OrchestratorApprovalRequest,
    OrchestratorApprovalStatus,
    OrchestratorArtifactListResponse,
    OrchestratorCreateRunRequest,
    OrchestratorPlanStepDefinition,
    OrchestratorPlanStepStateModel,
    OrchestratorRetryRequest,
    OrchestratorRetryStep,
    OrchestratorRunArtifactModel,
    OrchestratorRunDetailModel,
    OrchestratorRunListResponse,
    OrchestratorRunSummaryModel,
)


class UserProfileBaseSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    google_api_key: Optional[str] = Field(
        default=None,
        alias="google_api_key_encrypted",
    )
    openai_api_key: Optional[str] = Field(
        default=None,
        alias="openai_api_key_encrypted",
    )
    # Add other profile fields here as they are defined in the UserProfile model


class UserProfileRead(
    UserProfileBaseSchema
):  # Renamed UserProfileSchema to UserProfileRead
    id: int  # Assuming 'id' is the primary key of UserProfile model
    user_id: uuid.UUID
    # google_api_key is inherited from UserProfileBaseSchema
    # Configuration for ORM mode is inherited
    # google_api_key and openai_api_key are inherited from UserProfileBaseSchema
    # and will be present in this schema, suitable for internal use or when keys are needed.


# New schema for sanitized output, excluding sensitive API keys.
class UserProfileReadSanitized(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: uuid.UUID
    # Exclude sensitive fields by not including them in the schema


class UserProfileUpdate(UserProfileBaseSchema):
    # Inherits fields from UserProfileBaseSchema, e.g., google_api_key
    # No additional fields needed for update beyond what's in base, unless specified
    pass


# UserProfileCreateSchema remains as is, it was already defined and seems okay.
class UserProfileCreateSchema(UserProfileBaseSchema):
    # This schema might be used if creation requires specific fields or is different from update.
    # For now, it's similar to UserProfileBaseSchema.
    pass


class ApiKeyStatus(BaseModel):
    """Schema for displaying API key status."""

    model_config = ConfigDict(from_attributes=True)

    openai_api_key_set: bool = False
    # anthropic_api_key_set: bool = False # Example for other keys
    # Add other keys as needed


class ManifestStateModel(BaseModel):
    """Manifest checkpoint state metadata."""

    model_config = ConfigDict(populate_by_name=True)

    state_json: dict[str, Any] | None = Field(None, alias="stateJson")
    state_updated_at: datetime | None = Field(None, alias="stateUpdatedAt")


class ManifestRunMetadataModel(BaseModel):
    """Last run summary for a manifest registry entry."""

    model_config = ConfigDict(populate_by_name=True)

    job_id: Optional[uuid.UUID] = Field(None, alias="jobId")
    status: Optional[str] = Field(None, alias="status")
    started_at: Optional[datetime] = Field(None, alias="startedAt")
    finished_at: Optional[datetime] = Field(None, alias="finishedAt")


class ManifestSummaryModel(BaseModel):
    """List item representing one manifest registry entry."""

    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(..., alias="name")
    version: Optional[str] = Field(None, alias="version")
    content_hash: str = Field(..., alias="contentHash")
    updated_at: Optional[datetime] = Field(None, alias="updatedAt")
    last_run_job_id: Optional[uuid.UUID] = Field(None, alias="lastRunJobId")
    last_run_status: Optional[str] = Field(None, alias="lastRunStatus")
    state_updated_at: Optional[datetime] = Field(None, alias="stateUpdatedAt")


class ManifestDetailModel(BaseModel):
    """Detail response for one manifest registry entry."""

    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(..., alias="name")
    version: Optional[str] = Field(None, alias="version")
    content: str = Field(..., alias="content")
    content_hash: str = Field(..., alias="contentHash")
    updated_at: Optional[datetime] = Field(None, alias="updatedAt")
    last_run: Optional[ManifestRunMetadataModel] = Field(None, alias="lastRun")
    state: ManifestStateModel = Field(default_factory=ManifestStateModel, alias="state")


class ManifestListResponse(BaseModel):
    """Envelope for manifest list responses."""

    model_config = ConfigDict(populate_by_name=True)

    items: list[ManifestSummaryModel] = Field(default_factory=list, alias="items")


class ManifestUpsertRequest(BaseModel):
    """Request payload for manifest upsert."""

    model_config = ConfigDict(populate_by_name=True)

    content: str = Field(..., alias="content", min_length=1)


class ManifestRunOptions(BaseModel):
    """Optional queue overrides for manifest runs."""

    model_config = ConfigDict(populate_by_name=True)

    dry_run: Optional[bool] = Field(None, alias="dryRun")
    force_full: Optional[bool] = Field(None, alias="forceFull")
    max_docs: Optional[int] = Field(None, alias="maxDocs")

    @field_validator("max_docs")
    @classmethod
    def _validate_max_docs(cls, value: Optional[int]) -> Optional[int]:
        if value is not None and value < 1:
            raise ValueError("maxDocs must be >= 1 when provided")
        return value

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if self.dry_run is not None:
            payload["dryRun"] = self.dry_run
        if self.force_full is not None:
            payload["forceFull"] = self.force_full
        if self.max_docs is not None:
            payload["maxDocs"] = self.max_docs
        return payload


class ManifestRunRequest(BaseModel):
    """Request payload for registry-backed manifest runs."""

    model_config = ConfigDict(populate_by_name=True)

    action: Optional[str] = Field("run", alias="action")
    options: Optional[ManifestRunOptions] = Field(None, alias="options")


class ManifestRunQueueMetadata(BaseModel):
    """Returned queue metadata after submitting a manifest run."""

    model_config = ConfigDict(populate_by_name=True)

    type: str = Field(..., alias="type")
    required_capabilities: list[str] = Field(
        default_factory=list, alias="requiredCapabilities"
    )
    manifest_hash: Optional[str] = Field(None, alias="manifestHash")


class ManifestRunResponse(BaseModel):
    """Response payload when submitting registry manifest runs."""

    model_config = ConfigDict(populate_by_name=True)

    job_id: uuid.UUID = Field(..., alias="jobId")
    queue: ManifestRunQueueMetadata = Field(..., alias="queue")


class TaskTemplateInputSchema(BaseModel):
    """Input definition used by task template versions."""

    model_config = ConfigDict(populate_by_name=True)

    name: str
    label: str
    type: Literal[
        "text", "textarea", "markdown", "enum", "boolean", "user", "team", "repo_path"
    ]
    required: bool = False
    default: Any = None
    options: list[str] = Field(default_factory=list)


class TaskTemplateStepSkillSchema(BaseModel):
    """Skill payload attached to a template step."""

    model_config = ConfigDict(populate_by_name=True)

    id: str = "auto"
    args: dict[str, Any] = Field(default_factory=dict)
    required_capabilities: list[str] = Field(
        default_factory=list, alias="requiredCapabilities"
    )


class TaskTemplateStepBlueprintSchema(BaseModel):
    """Template step blueprint definition."""

    model_config = ConfigDict(populate_by_name=True)

    slug: Optional[str] = None
    title: Optional[str] = None
    instructions: str
    skill: Optional[TaskTemplateStepSkillSchema] = None
    annotations: dict[str, Any] = Field(default_factory=dict)


class TaskTemplateSummarySchema(BaseModel):
    """List response model for task templates."""

    model_config = ConfigDict(populate_by_name=True)

    slug: str
    scope: Literal["global", "team", "personal"]
    scope_ref: Optional[str] = Field(None, alias="scopeRef")
    title: str
    description: str
    latest_version: str = Field(..., alias="latestVersion")
    version: str
    tags: list[str] = Field(default_factory=list)
    is_favorite: bool = Field(False, alias="isFavorite")
    recent_applied_at: Optional[str] = Field(None, alias="recentAppliedAt")
    required_capabilities: list[str] = Field(
        default_factory=list, alias="requiredCapabilities"
    )
    release_status: str = Field("draft", alias="releaseStatus")


class TaskTemplateListResponseSchema(BaseModel):
    """Envelope for template list responses."""

    model_config = ConfigDict(populate_by_name=True)

    items: list[TaskTemplateSummarySchema] = Field(default_factory=list)


class TaskTemplateResponseSchema(TaskTemplateSummarySchema):
    """Detail response model for one template version."""

    inputs: list[TaskTemplateInputSchema] = Field(default_factory=list)
    steps: list[TaskTemplateStepBlueprintSchema] = Field(default_factory=list)
    annotations: dict[str, Any] = Field(default_factory=dict)
    reviewed_by: Optional[str] = Field(None, alias="reviewedBy")
    reviewed_at: Optional[str] = Field(None, alias="reviewedAt")


class TaskTemplateCreateRequestSchema(BaseModel):
    """Request model for creating templates."""

    model_config = ConfigDict(populate_by_name=True)

    slug: Optional[str] = None
    title: str
    description: str
    scope: Literal["team", "personal", "global"] = "personal"
    scope_ref: Optional[str] = Field(None, alias="scopeRef")
    tags: list[str] = Field(default_factory=list)
    inputs: list[TaskTemplateInputSchema] = Field(default_factory=list)
    steps: list[TaskTemplateStepBlueprintSchema] = Field(default_factory=list)
    annotations: dict[str, Any] = Field(default_factory=dict)
    required_capabilities: list[str] = Field(
        default_factory=list, alias="requiredCapabilities"
    )


class TaskTemplateExpandOptionsSchema(BaseModel):
    """Optional expansion flags."""

    model_config = ConfigDict(populate_by_name=True)

    enforce_step_limit: bool = Field(True, alias="enforceStepLimit")


class TaskTemplateExpandRequestSchema(BaseModel):
    """Request model for template expansion."""

    model_config = ConfigDict(populate_by_name=True)

    version: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)
    options: TaskTemplateExpandOptionsSchema = Field(
        default_factory=TaskTemplateExpandOptionsSchema
    )


class TaskTemplateAppliedMetadataSchema(BaseModel):
    """Audit metadata describing one template application."""

    model_config = ConfigDict(populate_by_name=True)

    slug: str
    version: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    step_ids: list[str] = Field(default_factory=list, alias="stepIds")
    applied_at: Optional[str] = Field(None, alias="appliedAt")


class TaskTemplateExpandResponseSchema(BaseModel):
    """Response model for expanded template step payloads."""

    model_config = ConfigDict(populate_by_name=True)

    steps: list[dict[str, Any]] = Field(default_factory=list)
    applied_template: TaskTemplateAppliedMetadataSchema = Field(
        ..., alias="appliedTemplate"
    )
    capabilities: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class TaskTemplateSaveFromTaskRequestSchema(BaseModel):
    """Request model for creating templates from draft task steps."""

    model_config = ConfigDict(populate_by_name=True)

    scope: Literal["personal", "team"] = "personal"
    scope_ref: Optional[str] = Field(None, alias="scopeRef")
    slug: Optional[str] = None
    title: str
    description: str
    selected_step_ids: list[str] = Field(default_factory=list, alias="selectedStepIds")
    steps: list[TaskTemplateStepBlueprintSchema] = Field(default_factory=list)
    suggested_inputs: list[TaskTemplateInputSchema] = Field(
        default_factory=list, alias="suggestedInputs"
    )
    tags: list[str] = Field(default_factory=list)


class TaskTemplateReviewRequestSchema(BaseModel):
    """Review workflow request for release status transitions."""

    model_config = ConfigDict(populate_by_name=True)

    release_status: Literal["draft", "active", "inactive"] = Field(
        ..., alias="releaseStatus"
    )


class TaskTemplateFavoriteRequestSchema(BaseModel):
    """Favorite toggle payload."""

    model_config = ConfigDict(populate_by_name=True)

    scope: Literal["global", "team", "personal"]
    scope_ref: Optional[str] = Field(None, alias="scopeRef")


__all__ = [
    "UserProfileBaseSchema",
    "UserProfileRead",
    "UserProfileReadSanitized",
    "UserProfileUpdate",
    "UserProfileCreateSchema",
    "ApiKeyStatus",
    "OrchestratorActionPlanModel",
    "OrchestratorApprovalActorModel",
    "OrchestratorApprovalRequest",
    "OrchestratorApprovalStatus",
    "OrchestratorArtifactListResponse",
    "OrchestratorCreateRunRequest",
    "OrchestratorPlanStepDefinition",
    "OrchestratorPlanStepStateModel",
    "OrchestratorRunArtifactModel",
    "OrchestratorRunDetailModel",
    "OrchestratorRunListResponse",
    "OrchestratorRunSummaryModel",
    "OrchestratorRetryRequest",
    "OrchestratorRetryStep",
    "TaskTemplateAppliedMetadataSchema",
    "TaskTemplateCreateRequestSchema",
    "TaskTemplateExpandOptionsSchema",
    "TaskTemplateExpandRequestSchema",
    "TaskTemplateExpandResponseSchema",
    "TaskTemplateFavoriteRequestSchema",
    "TaskTemplateInputSchema",
    "TaskTemplateListResponseSchema",
    "TaskTemplateResponseSchema",
    "TaskTemplateReviewRequestSchema",
    "TaskTemplateSaveFromTaskRequestSchema",
    "TaskTemplateStepBlueprintSchema",
    "TaskTemplateStepSkillSchema",
    "TaskTemplateSummarySchema",
]
