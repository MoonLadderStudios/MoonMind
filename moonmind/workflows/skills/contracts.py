"""Typed contracts for skills-first stage execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .tool_plan_contracts import (
    ARTIFACT_REF_PREFIX,
    REGISTRY_DIGEST_PREFIX,
    SUPPORTED_FAILURE_MODES,
    SUPPORTED_PLAN_VERSIONS,
    ArtifactRef,
    ContractValidationError,
    PlanDefinition,
    PlanEdge,
    PlanMetadata,
    PlanPolicy,
    PlanRegistrySnapshot,
    ToolDefinition,
    ToolExecutorBinding,
    ToolFailure,
    Step,
    SkillPolicies,
    SkillPolicyRetries,
    SkillPolicyTimeouts,
    ToolResult,
    parse_plan_definition,
    parse_tool_definition,
    parse_step,
)


@dataclass(frozen=True, slots=True)
class StageExecutionDecision:
    """Resolved execution policy for one workflow stage."""

    stage_name: str
    selected_skill: str
    adapter_id: str | None
    use_skills: bool
    execution_path: str
    fallback_enabled: bool
    shadow_mode: bool


@dataclass(frozen=True, slots=True)
class StageExecutionOutcome:
    """Execution result metadata for a workflow stage."""

    stage_name: str
    selected_skill: str
    adapter_id: str | None
    execution_path: str
    used_fallback: bool
    used_skills: bool
    shadow_mode_requested: bool
    result: Any

    def to_payload(self) -> dict[str, Any]:
        """Return a serializable payload fragment for logs/task payloads."""

        return {
            "selectedTool": self.selected_skill,
            "adapterId": self.adapter_id,
            "executionPath": self.execution_path,
            "usedTools": self.used_skills,
            "usedFallback": self.used_fallback,
            "shadowModeRequested": self.shadow_mode_requested,
        }


__all__ = [
    "ARTIFACT_REF_PREFIX",
    "ArtifactRef",
    "ContractValidationError",
    "PlanDefinition",
    "PlanEdge",
    "PlanMetadata",
    "PlanPolicy",
    "PlanRegistrySnapshot",
    "REGISTRY_DIGEST_PREFIX",
    "ToolDefinition",
    "ToolExecutorBinding",
    "ToolFailure",
    "Step",
    "SkillPolicies",
    "SkillPolicyRetries",
    "SkillPolicyTimeouts",
    "ToolResult",
    "StageExecutionDecision",
    "StageExecutionOutcome",
    "SUPPORTED_FAILURE_MODES",
    "SUPPORTED_PLAN_VERSIONS",
    "parse_plan_definition",
    "parse_tool_definition",
    "parse_step",
]
