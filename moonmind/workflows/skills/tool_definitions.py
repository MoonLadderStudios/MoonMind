"""Server-owned executable tool definitions shared by planning and dispatch."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from moonmind.workflows.skills.deployment_tools import (
    DEPLOYMENT_OVERVIEW_TOOL_NAME,
    DEPLOYMENT_UPDATE_TOOL_NAME,
    OPS_DIAGNOSE_STACK_TOOL_NAME,
    build_deployment_overview_tool_definition_payload,
    build_deployment_update_tool_definition_payload,
    build_ops_diagnose_stack_tool_definition_payload,
)
from moonmind.workflows.skills.tool_plan_contracts import (
    ToolDefinition,
    parse_tool_definition,
)
from moonmind.workflows.skills.tool_registry import ToolRegistryError
from moonmind.workloads.tool_bridge import (
    build_container_job_tool_definition_payload,
    is_container_job_tool,
)

JIRA_CHECK_BLOCKERS_TOOL_NAME = "jira.check_blockers"
JIRA_LOAD_PRESET_BRIEF_TOOL_NAME = "jira.load_preset_brief"
JIRA_UPDATE_ISSUE_STATUS_TOOL_NAME = "jira.update_issue_status"


def default_registry_tool_payload(*, name: str) -> dict[str, Any]:
    if is_container_job_tool(name):
        return build_container_job_tool_definition_payload(name=name)

    if name == DEPLOYMENT_UPDATE_TOOL_NAME:
        return build_deployment_update_tool_definition_payload()

    if name == OPS_DIAGNOSE_STACK_TOOL_NAME:
        return build_ops_diagnose_stack_tool_definition_payload()

    if name == DEPLOYMENT_OVERVIEW_TOOL_NAME:
        return build_deployment_overview_tool_definition_payload()

    if name == "document.discover":
        return {
            "name": name,
            "description": (
                "Discover document paths in the available workspace or the "
                "selected repository without launching an agent."
            ),
            "inputs": {
                "schema": {
                    "type": "object",
                    "properties": {
                        "directory": {"type": "string"},
                        "path": {"type": "string"},
                        "extensions": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    # Workspace/repository selection is resolved by the
                    # existing handler. Either directory or path is accepted;
                    # absent selection retains its truthful FAILED result.
                    "additionalProperties": True,
                }
            },
            "outputs": {
                "schema": {
                    "type": "object",
                    "required": ["documentPaths"],
                    "properties": {
                        "documentPaths": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "documentCount": {"type": "integer"},
                        "directory": {"type": "string"},
                        "source": {
                            "type": "string",
                            "enum": ["filesystem", "github"],
                        },
                        "extensions": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "repository": {"type": "string"},
                        "ref": {"type": "string"},
                        "truncated": {"type": "boolean"},
                        "error": {"type": "string"},
                    },
                    "additionalProperties": True,
                }
            },
            "executor": {
                "activity_type": "mm.tool.execute",
                "selector": {"mode": "by_capability"},
            },
            "requirements": {"capabilities": ["sandbox"]},
            # Preserve the existing generic contract's execution policy.
            "policies": {
                "timeouts": {
                    "start_to_close_seconds": 3600,
                    "schedule_to_close_seconds": 3900,
                },
                "retries": {"max_attempts": 1},
            },
        }

    if name == JIRA_CHECK_BLOCKERS_TOOL_NAME:
        return {
            "name": name,
            "description": (
                "Check whether a Jira issue is blocked by unresolved inbound "
                "Blocks links using trusted Jira data."
            ),
            "inputs": {
                "schema": {
                    "type": "object",
                    "properties": {
                        "targetIssueKey": {"type": "string"},
                        "issueKey": {"type": "string"},
                        "jiraIssueKey": {"type": "string"},
                        "blockerPreflight": {"type": "object"},
                        "assessmentArtifactPath": {"type": "string"},
                        "assessment_artifact_path": {"type": "string"},
                        "assessmentVerdict": {"type": "string"},
                        "assessment_verdict": {"type": "string"},
                    },
                    "additionalProperties": True,
                }
            },
            "outputs": {
                "schema": {
                    "type": "object",
                    "required": ["targetIssueKey", "decision", "summary"],
                    "properties": {
                        "targetIssueKey": {"type": "string"},
                        "decision": {"type": "string", "enum": ["continue", "blocked"]},
                        "blockingIssues": {"type": "array"},
                        "resolvedBlockingIssues": {"type": "array"},
                        "assessmentVerdict": {"type": "string"},
                        "summary": {"type": "string"},
                    },
                    "additionalProperties": True,
                }
            },
            "executor": {
                "activity_type": "mm.tool.execute",
                "selector": {"mode": "by_capability"},
            },
            "requirements": {"capabilities": ["integration:jira"]},
            "policies": {
                "timeouts": {
                    "start_to_close_seconds": 60,
                    "schedule_to_close_seconds": 120,
                },
                "retries": {"max_attempts": 1},
            },
        }

    if name == JIRA_LOAD_PRESET_BRIEF_TOOL_NAME:
        return {
            "name": name,
            "description": (
                "Load a compact Jira preset brief through MoonMind's trusted "
                "Jira service."
            ),
            "inputs": {
                "schema": {
                    "type": "object",
                    "properties": {
                        "issueKey": {"type": "string"},
                        "issue_key": {"type": "string"},
                        "jiraIssueKey": {"type": "string"},
                        "jira_issue_key": {"type": "string"},
                        "artifactPath": {"type": "string"},
                        "artifact_path": {"type": "string"},
                        "briefArtifactPath": {"type": "string"},
                        "brief_artifact_path": {"type": "string"},
                        "jira": {"type": "object"},
                        "issue": {"type": "object"},
                    },
                    "additionalProperties": True,
                }
            },
            "outputs": {
                "schema": {
                    "type": "object",
                    "required": [
                        "trustedSource",
                        "jiraIssueKey",
                        "jiraPresetBrief",
                        "summary",
                    ],
                    "properties": {
                        "trustedSource": {"type": "string"},
                        "jiraIssueKey": {"type": "string"},
                        "jiraPresetBrief": {"type": "string"},
                        "presetBrief": {"type": "string"},
                        "jiraStepInstructions": {"type": "string"},
                        "artifactPath": {"type": "string"},
                        "resolvedSourceDesignPath": {"type": "string"},
                        "sourceResolution": {"type": "object"},
                        "jiraIssue": {"type": "object"},
                        "summary": {"type": "string"},
                    },
                    "additionalProperties": True,
                }
            },
            "executor": {
                "activity_type": "mm.tool.execute",
                "selector": {"mode": "by_capability"},
            },
            "requirements": {"capabilities": ["integration:jira"]},
            "policies": {
                "timeouts": {
                    "start_to_close_seconds": 60,
                    "schedule_to_close_seconds": 120,
                },
                "retries": {"max_attempts": 1},
            },
        }

    if name == JIRA_UPDATE_ISSUE_STATUS_TOOL_NAME:
        return {
            "name": name,
            "description": (
                "Move a Jira issue to a named status through MoonMind's "
                "trusted Jira transition path."
            ),
            "inputs": {
                "schema": {
                    "type": "object",
                    "properties": {
                        "issueKey": {"type": "string"},
                        "issue_key": {"type": "string"},
                        "jiraIssueKey": {"type": "string"},
                        "jira_issue_key": {"type": "string"},
                        "targetStatus": {"type": "string"},
                        "target_status": {"type": "string"},
                        "statusName": {"type": "string"},
                        "status_name": {"type": "string"},
                        "mode": {"type": "string"},
                        "repository": {"type": "string"},
                        "completionTargetRef": {"type": "string"},
                        "verificationArtifactPath": {"type": "string"},
                        "verificationPayload": {"type": "object"},
                        "pullRequestUrl": {"type": "string"},
                        "requireVerification": {"type": "boolean"},
                        "assessmentArtifactPath": {"type": "string"},
                        "assessment_artifact_path": {"type": "string"},
                        "assessmentVerdict": {"type": "string"},
                        "assessment_verdict": {"type": "string"},
                        "fields": {"type": "object"},
                        "update": {"type": "object"},
                        "jira": {"type": "object"},
                        "issue": {"type": "object"},
                    },
                    "additionalProperties": True,
                }
            },
            "outputs": {
                "schema": {
                    "type": "object",
                    "required": ["issueKey", "targetStatus", "decision", "summary"],
                    "properties": {
                        "issueKey": {"type": "string"},
                        "targetStatus": {"type": "string"},
                        "decision": {"type": "string"},
                        "transitioned": {"type": "boolean"},
                        "transitionId": {"type": "string"},
                        "currentStatus": {"type": "object"},
                        "confirmedStatus": {"type": "object"},
                        "summary": {"type": "string"},
                    },
                    "additionalProperties": True,
                }
            },
            "executor": {
                "activity_type": "mm.tool.execute",
                "selector": {"mode": "by_capability"},
            },
            "requirements": {"capabilities": ["integration:jira"]},
            "policies": {
                "timeouts": {
                    "start_to_close_seconds": 60,
                    "schedule_to_close_seconds": 120,
                },
                "retries": {"max_attempts": 1},
            },
        }

    if name == "story.create_jira_issues":
        return {
            "name": name,
            "description": "Create Jira issues from MoonSpec story breakdown output.",
            "inputs": {
                "schema": {
                    "type": "object",
                    "properties": {
                        "stories": {"type": "array"},
                        "storyOutput": {"type": "object"},
                        "storyBreakdownPath": {"type": "string"},
                        "storyBreakdownJson": {"type": "string"},
                        "repository": {"type": "string"},
                        "targetBranch": {"type": "string"},
                        "branch": {"type": "string"},
                    },
                    "additionalProperties": True,
                }
            },
            "outputs": {
                "schema": {
                    "type": "object",
                    "additionalProperties": True,
                }
            },
            "executor": {
                "activity_type": "mm.tool.execute",
                "selector": {"mode": "by_capability"},
            },
            "requirements": {"capabilities": ["integration:jira"]},
            "policies": {
                "timeouts": {
                    "start_to_close_seconds": 300,
                    "schedule_to_close_seconds": 600,
                },
                "retries": {"max_attempts": 1},
            },
        }

    if name == "story.create_github_issues":
        return {
            "name": name,
            "description": "Create GitHub issues from MoonSpec story breakdown output.",
            "inputs": {
                "schema": {
                    "type": "object",
                    "properties": {
                        "stories": {"type": "array"},
                        "storyOutput": {"type": "object"},
                        "storyBreakdownPath": {"type": "string"},
                        "storyBreakdownJson": {"type": "string"},
                        "repository": {"type": "string"},
                        "targetBranch": {"type": "string"},
                        "branch": {"type": "string"},
                    },
                    "additionalProperties": True,
                }
            },
            "outputs": {
                "schema": {
                    "type": "object",
                    "additionalProperties": True,
                }
            },
            "executor": {
                "activity_type": "mm.tool.execute",
                "selector": {"mode": "by_capability"},
            },
            "requirements": {"capabilities": ["integration:github"]},
            "policies": {
                "timeouts": {
                    "start_to_close_seconds": 300,
                    "schedule_to_close_seconds": 600,
                },
                "retries": {"max_attempts": 1},
            },
        }

    if name in {
        "story.create_github_issue_implement_workflows",
        "story.create_github_issue_orchestrate_workflows",
    }:
        return {
            "name": name,
            "description": (
                "Create downstream MoonMind workflows from GitHub issue mappings."
            ),
            "inputs": {
                "schema": {
                    "type": "object",
                    "properties": {
                        "github": {"type": "object"},
                        "issueMappings": {"type": "array"},
                        "githubOrchestration": {"type": "object"},
                        "traceability": {"type": "object"},
                    },
                    "additionalProperties": True,
                }
            },
            "outputs": {
                "schema": {
                    "type": "object",
                    "additionalProperties": True,
                }
            },
            "executor": {
                "activity_type": "mm.tool.execute",
                "selector": {"mode": "by_capability"},
            },
            "requirements": {"capabilities": ["integration:github"]},
            "policies": {
                "timeouts": {
                    "start_to_close_seconds": 300,
                    "schedule_to_close_seconds": 600,
                },
                "retries": {"max_attempts": 1},
            },
        }

    description = (
        "Execute generic runtime CLI instructions."
        if name == "auto"
        else f"Execute '{name}' via the generic runtime CLI handler."
    )
    # 3600s gives the sandbox worker enough headroom to exhaust the full
    # Gemini capacity-retry backoff cycle (up to 8 attempts with max 600s
    # delay each) before Temporal cancels the activity.
    start_to_close_seconds = 3600
    schedule_to_close_seconds = 3900
    if name in {
        "pr-resolver",
        "batch-pr-resolver",
        "fix-comments",
        "fix-ci",
        "fix-merge-conflicts",
    }:
        # Resolver/fix skills can run longer due bounded retry loops and CI waits.
        start_to_close_seconds = 7200
        schedule_to_close_seconds = 7500

    return {
        "name": name,
        "description": description,
        "inputs": {
            "schema": {
                "type": "object",
                "properties": {
                    "instructions": {"type": "string"},
                    "runtime": {"type": "object"},
                },
                "additionalProperties": True,
            }
        },
        "outputs": {
            "schema": {
                "type": "object",
                "additionalProperties": True,
            }
        },
        "executor": {
            "activity_type": "mm.tool.execute",
            "selector": {"mode": "by_capability"},
        },
        "requirements": {"capabilities": ["sandbox"]},
        "policies": {
            "timeouts": {
                "start_to_close_seconds": start_to_close_seconds,
                "schedule_to_close_seconds": schedule_to_close_seconds,
            },
            "retries": {"max_attempts": 1},
        },
    }


def validate_tool_dispatch_authority(definition: ToolDefinition) -> None:
    """A pinned contract preserves history, but cannot invent an execution route.

    Schemas, timeouts, and retries may evolve independently of the deployment.
    Compare only the authority-bearing binding and required capabilities. The
    historical mm.skill spelling is the same dispatcher as mm.tool.
    """
    canonical = parse_tool_definition(
        default_registry_tool_payload(name=definition.name)
    )
    actual_binding = definition.executor.to_payload()
    expected_binding = canonical.executor.to_payload()
    if actual_binding["activity_type"] == "mm.skill.execute":
        actual_binding["activity_type"] = "mm.tool.execute"
    if actual_binding != expected_binding or set(
        definition.required_capabilities
    ) != set(canonical.required_capabilities):
        raise ToolRegistryError(
            f"Tool '{definition.name}' binding or capabilities differ from the trusted registry"
        )


def validate_machine_tool_authority(
    definition: ToolDefinition, principal: Mapping[str, Any] | None
) -> None:
    """Keep deployment effects outside bounded child-create machine grants."""
    if not isinstance(principal, Mapping) or principal.get("kind") != "workflow":
        return
    scopes = principal.get("scopes")
    granted = set(scopes) if isinstance(scopes, (list, tuple)) else set()
    required = set(definition.required_capabilities) & {
        "deployment_control",
        "docker_admin",
    }
    if not required.issubset(granted):
        raise ToolRegistryError(
            f"Tool '{definition.name}' capabilities are not admitted for this machine principal"
        )
