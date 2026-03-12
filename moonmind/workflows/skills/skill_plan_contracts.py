"""Legacy compatibility exports for skill-named plan contracts."""

from __future__ import annotations

from .tool_plan_contracts import (
    ARTIFACT_REF_PREFIX,
    REGISTRY_DIGEST_PREFIX,
    SUPPORTED_FAILURE_MODES,
    SUPPORTED_PLAN_VERSIONS,
    ActivityExecutionContext,
    ActivityInvocationEnvelope,
    ArtifactRef,
    CompactActivityResult,
    ContractValidationError,
    ObservabilitySummary,
    PlanDefinition,
    PlanEdge,
    PlanMetadata,
    PlanPolicy,
    PlanRegistrySnapshot,
    SkillPolicies,
    SkillPolicyRetries,
    SkillPolicyTimeouts,
)
from .tool_plan_contracts import Step as SkillInvocation
from .tool_plan_contracts import ToolDefinition as SkillDefinition
from .tool_plan_contracts import ToolExecutorBinding as SkillExecutorBinding
from .tool_plan_contracts import ToolFailure as SkillFailure
from .tool_plan_contracts import ToolResult as SkillResult
from .tool_plan_contracts import parse_plan_definition
from .tool_plan_contracts import parse_step as parse_skill_invocation
from .tool_plan_contracts import parse_tool_definition as parse_skill_definition

__all__ = [
    "ARTIFACT_REF_PREFIX",
    "REGISTRY_DIGEST_PREFIX",
    "SUPPORTED_FAILURE_MODES",
    "SUPPORTED_PLAN_VERSIONS",
    "ArtifactRef",
    "ContractValidationError",
    "SkillDefinition",
    "SkillExecutorBinding",
    "SkillFailure",
    "SkillInvocation",
    "SkillPolicies",
    "SkillPolicyRetries",
    "SkillPolicyTimeouts",
    "SkillResult",
    "ActivityInvocationEnvelope",
    "CompactActivityResult",
    "ActivityExecutionContext",
    "ObservabilitySummary",
    "PlanRegistrySnapshot",
    "PlanMetadata",
    "PlanPolicy",
    "PlanEdge",
    "PlanDefinition",
    "parse_skill_invocation",
    "parse_plan_definition",
    "parse_skill_definition",
]
