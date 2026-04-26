"""Canonical deployment executable tool contracts."""

from __future__ import annotations

from typing import Any

DEPLOYMENT_UPDATE_TOOL_NAME = "deployment.update_compose_stack"
DEPLOYMENT_UPDATE_TOOL_VERSION = "1.0.0"

_MOONMIND_REPOSITORY = "ghcr.io/moonladderstudios/moonmind"
_NON_RETRYABLE_DEPLOYMENT_ERRORS = (
    "INVALID_INPUT",
    "PERMISSION_DENIED",
    "POLICY_VIOLATION",
    "DEPLOYMENT_LOCKED",
)


def build_deployment_update_tool_definition_payload() -> dict[str, Any]:
    """Build the MM-519 deployment update tool registry definition."""

    return {
        "name": DEPLOYMENT_UPDATE_TOOL_NAME,
        "version": DEPLOYMENT_UPDATE_TOOL_VERSION,
        "type": "skill",
        "description": (
            "Update an allowlisted Docker Compose stack to a desired MoonMind "
            "image reference."
        ),
        "inputs": {
            "schema": {
                "type": "object",
                "required": ["stack", "image", "reason"],
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
        "requirements": {
            "capabilities": ["deployment_control", "docker_admin"],
        },
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
        "security": {"allowed_roles": ["admin"]},
    }


__all__ = [
    "DEPLOYMENT_UPDATE_TOOL_NAME",
    "DEPLOYMENT_UPDATE_TOOL_VERSION",
    "build_deployment_update_tool_definition_payload",
]
