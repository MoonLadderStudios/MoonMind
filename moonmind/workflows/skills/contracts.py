"""Compatibility aliases for skill and tool plan contracts."""

from __future__ import annotations


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
    SkillPolicies,
    SkillSurfaceContract,
    SkillPolicyRetries,
    SkillPolicyTimeouts,
    Step,
    ToolDefinition,
    ToolExecutorBinding,
    ToolFailure,
    ToolPolicies,
    PublishAuthorityMetadata,
    ToolPolicyRetries,
    ToolPolicyTimeouts,
    ToolResult,
    parse_plan_definition,
    parse_step,
    parse_tool_definition,
)

SkillDefinition = ToolDefinition
SkillExecutorBinding = ToolExecutorBinding
SkillFailure = ToolFailure
SkillInvocation = Step
SkillResult = ToolResult
parse_skill_definition = parse_tool_definition
parse_skill_invocation = parse_step

__all__ = [
    "ARTIFACT_REF_PREFIX",
    "ArtifactRef",
    "ContractValidationError",
    "PlanDefinition",
    "PlanEdge",
    "PlanMetadata",
    "PlanPolicy",
    "PlanRegistrySnapshot",
    "PublishAuthorityMetadata",
    "REGISTRY_DIGEST_PREFIX",
    "SkillDefinition",
    "SkillExecutorBinding",
    "SkillFailure",
    "SkillInvocation",
    "SkillPolicies",
    "SkillPolicyRetries",
    "SkillPolicyTimeouts",
    "SkillResult",
    "SkillSurfaceContract",
    "SUPPORTED_FAILURE_MODES",
    "SUPPORTED_PLAN_VERSIONS",
    "ToolPolicies",
    "ToolPolicyRetries",
    "ToolPolicyTimeouts",
    "parse_plan_definition",
    "parse_skill_definition",
    "parse_skill_invocation",
]
