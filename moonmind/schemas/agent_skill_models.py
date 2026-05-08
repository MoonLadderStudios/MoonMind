import enum
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

class AgentSkillSourceKind(str, enum.Enum):
    """Source provenance for a resolved skill."""

    BUILT_IN = "built_in"
    DEPLOYMENT = "deployment"
    REPO = "repo"
    LOCAL = "local"

class AgentSkillFormat(str, enum.Enum):
    """Supported payload formatting inside a given skill version."""

    MARKDOWN = "markdown"
    BUNDLE = "bundle"

class RuntimeMaterializationMode(str, enum.Enum):
    """Options for how a runtime receives the resolved skill set."""

    PROMPT_BUNDLED = "prompt_bundled"
    WORKSPACE_MOUNTED = "workspace_mounted"
    HYBRID = "hybrid"
    RETRIEVAL = "retrieval"

class SkillSelectorEntry(BaseModel):
    """An explicit include rule for a single skill."""

    name: str
    version: str | None = None
    
    model_config = ConfigDict(extra="forbid")

class SkillSelector(BaseModel):
    """Selection intent expressing which agent skills should be active."""

    sets: list[str] = Field(default_factory=list)
    include: list[SkillSelectorEntry] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)
    materialization_mode: RuntimeMaterializationMode | None = None
    
    model_config = ConfigDict(extra="forbid")

class AgentSkillProvenance(BaseModel):
    """Metadata explaining where a resolved skill instance came from."""

    source_kind: AgentSkillSourceKind
    original_version: str | None = None
    source_path: str | None = None
    skill_set_name: str | None = None
    
    model_config = ConfigDict(extra="forbid")

class ResolvedSkillEntry(BaseModel):
    """An individual agent skill and the immutable version selected for a run."""

    skill_name: str
    version: str | None = None
    format: AgentSkillFormat = AgentSkillFormat.MARKDOWN
    content_ref: str | None = None
    content_digest: str | None = None
    provenance: AgentSkillProvenance
    required_skills: list[str] = Field(default_factory=list)
    selection_reason: str | None = None
    required_by: list[str] = Field(default_factory=list)
    
    model_config = ConfigDict(extra="forbid")

class ResolvedSkillSet(BaseModel):
    """The immutable, exact set of agent skills selected for a specific run or step."""

    snapshot_id: str
    deployment_id: str | None = None
    resolved_at: datetime
    skills: list[ResolvedSkillEntry] = Field(default_factory=list)
    manifest_ref: str | None = None
    source_trace: dict[str, Any] = Field(default_factory=dict)
    resolution_inputs: dict[str, Any] = Field(default_factory=dict)
    policy_summary: dict[str, Any] = Field(default_factory=dict)
    
    model_config = ConfigDict(extra="forbid")

class RuntimeSkillMaterialization(BaseModel):
    """The runtime-facing rendering of a resolved skill snapshot."""

    runtime_id: str
    materialization_mode: RuntimeMaterializationMode
    workspace_paths: list[str] = Field(default_factory=list)
    prompt_index_ref: str | None = None
    retrieval_manifest_ref: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    
    model_config = ConfigDict(extra="forbid")


SkillsOnDemandStatus = Literal["denied"]
SkillsOnDemandDeniedCode = Literal["feature_disabled"]


class SkillsOnDemandQueryRequest(BaseModel):
    """Runtime request to search the on-demand Skill catalog."""

    query: str = ""
    runtime_id: str | None = None
    current_snapshot_ref: str | None = None
    max_results: int = Field(default=20, ge=1, le=100)

    model_config = ConfigDict(extra="forbid")


class SkillsOnDemandQueryResult(BaseModel):
    """Deterministic on-demand Skill catalog query result."""

    status: SkillsOnDemandStatus
    code: SkillsOnDemandDeniedCode
    message: str
    results: list[dict[str, Any]] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


class SkillsOnDemandRequestedSkill(BaseModel):
    """A single on-demand Skill requested by a managed runtime."""

    name: str
    version: str | None = None

    model_config = ConfigDict(extra="forbid")


class SkillsOnDemandRequest(BaseModel):
    """Runtime request to activate additional Skills for the current run."""

    current_snapshot_ref: str | None = None
    requested_skills: list[SkillsOnDemandRequestedSkill] = Field(default_factory=list)
    reason: str | None = None
    runtime_id: str | None = None
    step_id: str | None = None
    active_snapshot: ResolvedSkillSet | None = None

    model_config = ConfigDict(extra="forbid")


class SkillsOnDemandRequestResult(BaseModel):
    """Deterministic on-demand Skill activation result."""

    status: SkillsOnDemandStatus
    code: SkillsOnDemandDeniedCode
    message: str
    active_snapshot_id: str | None = None
    snapshot_id: str | None = None
    resolved_skillset_ref: str | None = None

    model_config = ConfigDict(extra="forbid")


RuntimeSkillProjectionStatus = Literal[
    "created", "reused", "skipped", "blocked", "failed"
]


class RuntimeSkillProjectionDiagnostic(BaseModel):
    """Structured diagnostic for a runtime skill alias projection decision."""

    active_visible_path: str = Field(..., alias="activeVisiblePath")
    alias_path: str = Field(..., alias="aliasPath")
    event: str
    reason: str | None = None
    snapshot_id: str = Field(..., alias="snapshotId")
    status: RuntimeSkillProjectionStatus
    workspace: str

    model_config = ConfigDict(extra="forbid", populate_by_name=True)
