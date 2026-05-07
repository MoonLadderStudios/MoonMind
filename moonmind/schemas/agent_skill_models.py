import enum
from datetime import datetime
from typing import Any

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
