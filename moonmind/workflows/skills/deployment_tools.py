"""Canonical deployment executable tool contracts."""

from __future__ import annotations

from typing import Any

from moonmind.schemas.agent_runtime_models import moonmind_ops_runtime_contract

DEPLOYMENT_UPDATE_TOOL_NAME = "deployment.update_compose_stack"
DEPLOYMENT_UPDATE_TOOL_VERSION = "1.0.0"
OPS_DIAGNOSE_STACK_TOOL_NAME = "moonmind.ops_diagnose_stack"
OPS_DIAGNOSE_STACK_TOOL_VERSION = "1.0.0"

_MOONMIND_REPOSITORY = "ghcr.io/moonladderstudios/moonmind"
_NON_RETRYABLE_DEPLOYMENT_ERRORS = (
    "INVALID_INPUT",
    "PERMISSION_DENIED",
    "POLICY_VIOLATION",
    "DEPLOYMENT_LOCKED",
)
_NON_RETRYABLE_DIAGNOSIS_ERRORS = (
    "INVALID_INPUT",
    "PERMISSION_DENIED",
    "POLICY_VIOLATION",
)
_OPS_DIAGNOSIS_INCLUDES = [
    "compose_ps",
    "compose_images",
    "container_health",
    "container_inspect_summary",
    "recent_logs",
    "api_health",
    "worker_health",
    "temporal_connectivity",
    "artifact_store_health",
    "disk_memory_cpu",
]
_DEFAULT_OPS_DIAGNOSIS_INCLUDES = [
    "compose_ps",
    "container_health",
    "recent_logs",
    "api_health",
    "worker_health",
    "temporal_connectivity",
]


def build_deployment_update_tool_definition_payload() -> dict[str, Any]:
    """Build the MM-519 deployment update tool registry definition."""

    return {
        "name": DEPLOYMENT_UPDATE_TOOL_NAME,
        "type": "skill",
        "description": (
            "Update an allowlisted Docker Compose stack to a desired MoonMind "
            "image reference."
        ),
        "inputs": {
            "schema": {
                "type": "object",
                "required": ["stack", "image"],
                "additionalProperties": False,
                "properties": {
                    "stack": {"type": "string", "enum": ["moonmind"]},
                    "image": {
                        "type": "object",
                        "required": ["repository", "reference"],
                        "additionalProperties": False,
                        "properties": {
                            "repository": {
                                "type": "string",
                                "enum": [_MOONMIND_REPOSITORY],
                            },
                            "reference": {"type": "string"},
                            "resolvedDigest": {"type": "string"},
                        },
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["changed_services", "force_recreate"],
                    },
                    "removeOrphans": {"type": "boolean"},
                    "wait": {"type": "boolean"},
                    "runSmokeCheck": {"type": "boolean"},
                    "pauseWork": {"type": "boolean"},
                    "pruneOldImages": {"type": "boolean"},
                    "reason": {"type": "string"},
                    "operationKind": {
                        "type": "string",
                        "enum": ["update", "rollback"],
                    },
                    "rollbackSourceActionId": {"type": "string"},
                    "confirmation": {"type": "string"},
                },
            }
        },
        "outputs": {
            "schema": {
                "type": "object",
                "required": [
                    "status",
                    "stack",
                    "requestedImage",
                    "updatedServices",
                    "runningServices",
                ],
                "additionalProperties": False,
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["SUCCEEDED", "FAILED", "PARTIALLY_VERIFIED"],
                    },
                    "stack": {"type": "string"},
                    "requestedImage": {"type": "string"},
                    "resolvedDigest": {"type": "string"},
                    "afterBuildId": {"type": "string"},
                    "updatedServices": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "runningServices": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": True,
                        },
                    },
                    "beforeStateArtifactRef": {"type": "string"},
                    "afterStateArtifactRef": {"type": "string"},
                    "commandLogArtifactRef": {"type": "string"},
                    "verificationArtifactRef": {"type": "string"},
                    "audit": {
                        "type": "object",
                        "additionalProperties": True,
                    },
                    "failure": {
                        "type": "object",
                        "required": ["class", "reason", "retryable"],
                        "additionalProperties": False,
                        "properties": {
                            "class": {"type": "string"},
                            "reason": {"type": "string"},
                            "retryable": {"type": "boolean"},
                        },
                    },
                },
            }
        },
        "executor": {
            "activity_type": "mm.tool.execute",
            "selector": {"mode": "by_capability"},
        },
        "requirements": {"capabilities": ["deployment_control", "docker_admin"]},
        "policies": {
            "timeouts": {
                "start_to_close_seconds": 900,
                "schedule_to_close_seconds": 1800,
            },
            "retries": {
                "max_attempts": 1,
                "non_retryable_error_codes": list(_NON_RETRYABLE_DEPLOYMENT_ERRORS),
            },
        },
        "security": {
            "allowed_roles": ["admin"],
            "opsRuntime": moonmind_ops_runtime_contract().model_dump(
                by_alias=True, mode="json"
            ),
        },
    }


def build_ops_diagnose_stack_tool_definition_payload() -> dict[str, Any]:
    """Build the MM-925 read-only ops diagnosis tool registry definition."""

    ops_runtime = moonmind_ops_runtime_contract().model_copy(
        update={"allowed_operations": ("status", "logs")}
    )
    return {
        "name": OPS_DIAGNOSE_STACK_TOOL_NAME,
        "type": "skill",
        "description": (
            "Collect read-only, bounded MoonMind Compose stack diagnostics for "
            "policy-approved remediation workflows."
        ),
        "inputs": {
            "schema": {
                "type": "object",
                "required": ["stack", "reason"],
                "additionalProperties": False,
                "properties": {
                    "stack": {"type": "string", "enum": ["moonmind"]},
                    "include": {
                        "type": "array",
                        "items": {"type": "string", "enum": _OPS_DIAGNOSIS_INCLUDES},
                        "default": _DEFAULT_OPS_DIAGNOSIS_INCLUDES,
                    },
                    "services": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "tailLines": {
                        "type": "integer",
                        "minimum": 50,
                        "maximum": 1000,
                        "default": 300,
                    },
                    "targetWorkflowId": {"type": "string"},
                    "remediationWorkflowId": {"type": "string"},
                    "reason": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 1000,
                    },
                },
            }
        },
        "outputs": {
            "schema": {
                "type": "object",
                "required": ["status", "stack", "summary", "findings", "artifactRefs"],
                "additionalProperties": False,
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["SUCCEEDED", "FAILED", "PARTIALLY_VERIFIED"],
                    },
                    "stack": {"type": "string"},
                    "summary": {"type": "string"},
                    "findings": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["kind", "severity", "message"],
                            "additionalProperties": False,
                            "properties": {
                                "kind": {"type": "string"},
                                "severity": {
                                    "type": "string",
                                    "enum": ["info", "warning", "error"],
                                },
                                "message": {"type": "string"},
                                "service": {"type": "string"},
                                "evidenceRef": {"type": "string"},
                            },
                        },
                    },
                    "artifactRefs": {
                        "type": "object",
                        "additionalProperties": {"type": "string"},
                    },
                },
            }
        },
        "executor": {
            "activity_type": "mm.tool.execute",
            "selector": {"mode": "by_capability"},
        },
        "requirements": {"capabilities": ["deployment_control", "docker_admin"]},
        "policies": {
            "timeouts": {
                "start_to_close_seconds": 300,
                "schedule_to_close_seconds": 600,
            },
            "retries": {
                "max_attempts": 1,
                "non_retryable_error_codes": list(_NON_RETRYABLE_DIAGNOSIS_ERRORS),
            },
        },
        "security": {
            "allowed_roles": ["admin"],
            "remediationPolicyRequired": True,
            "exposedToManagedAgents": False,
            "opsRuntime": ops_runtime.model_dump(by_alias=True, mode="json"),
        },
    }


__all__ = [
    "DEPLOYMENT_UPDATE_TOOL_NAME",
    "DEPLOYMENT_UPDATE_TOOL_VERSION",
    "OPS_DIAGNOSE_STACK_TOOL_NAME",
    "OPS_DIAGNOSE_STACK_TOOL_VERSION",
    "build_deployment_update_tool_definition_payload",
    "build_ops_diagnose_stack_tool_definition_payload",
]
