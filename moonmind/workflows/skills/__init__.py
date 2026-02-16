"""Skills-first workflow execution helpers.

This package provides a lightweight policy and execution adapter that lets
workflow stages resolve a selected skill while preserving compatibility with the
existing direct execution path.
"""

from .contracts import StageExecutionDecision, StageExecutionOutcome
from .materializer import (
    MaterializedSkill,
    MaterializedSkillWorkspace,
    SkillMaterializationError,
    materialize_run_skill_workspace,
)
from .resolver import (
    ResolvedSkill,
    RunSkillSelection,
    SkillResolutionError,
    resolve_run_skill_selection,
)
from .runner import execute_stage
from .workspace_links import (
    SkillWorkspaceError,
    SkillWorkspaceLinks,
    ensure_shared_skill_links,
    validate_shared_skill_links,
)

__all__ = [
    "StageExecutionDecision",
    "StageExecutionOutcome",
    "execute_stage",
    "ResolvedSkill",
    "RunSkillSelection",
    "SkillResolutionError",
    "resolve_run_skill_selection",
    "MaterializedSkill",
    "MaterializedSkillWorkspace",
    "SkillMaterializationError",
    "materialize_run_skill_workspace",
    "SkillWorkspaceLinks",
    "SkillWorkspaceError",
    "ensure_shared_skill_links",
    "validate_shared_skill_links",
]
