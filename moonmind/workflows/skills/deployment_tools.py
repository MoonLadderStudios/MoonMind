"""Canonical deployment executable tool contracts."""

from __future__ import annotations

from typing import Any

from moonmind.schemas.agent_runtime_models import moonmind_ops_runtime_contract

DEPLOYMENT_UPDATE_TOOL_NAME = "deployment.update_compose_stack"
DEPLOYMENT_UPDATE_TOOL_VERSION = "1.0.0"
OPS_DIAGNOSE_STACK_TOOL_NAME = "moonmind.ops_diagnose_stack"
OPS_DIAGNOSE_STACK_TOOL_VERSION = "1.0.0"
DEPLOYMENT_OVERVIEW_TOOL_NAME = "moonmind.deployment_overview"
DEPLOYMENT_OVERVIEW_TOOL_VERSION = "1.0.0"

# One owner for the release budget. The detached updater's deadline and the
# Activity budget that supervises it are derived from the same number, so
# they cannot drift apart: a supervisor that expires first reports a failure
# for a release that is still running.
RELEASE_JOB_BUDGET_SECONDS = 7200
# How long one supervising attempt watches the detached job. Timing out is
# not a failure: promotion recreates every worker fleet, including the one
# running the supervising Activity, so the supervisor is expected to be
# replaced mid-release and ``execute_detached`` re-attaches on the next
# attempt. The window therefore only bounds how long a replaced supervisor
# goes unnoticed, which is why it is far shorter than the budget.
#
# It must still outlast one pre-launch compose command with room to spare.
# ``HostDockerComposeRunner`` reads its own command timeout from here, and
# lets the updater pull consume all of it; the Activity clock additionally
# starts before that subprocess does, and after the pull the image is still
# inspected and ``request.json`` published. A window that merely equalled the
# command timeout therefore cancelled a pull that had in fact succeeded,
# forcing another pull on retry and spending the durable budget on repeated
# pre-launch work.
RELEASE_RUNNER_COMMAND_TIMEOUT_SECONDS = 900
RELEASE_PRELAUNCH_HEADROOM_SECONDS = 300
RELEASE_SUPERVISION_WINDOW_SECONDS = (
    RELEASE_RUNNER_COMMAND_TIMEOUT_SECONDS + RELEASE_PRELAUNCH_HEADROOM_SECONDS
)
# Enough attempts to re-attach across the whole budget, so the job's own
# deadline - never the attempt count - decides when a release stops.
RELEASE_SUPERVISION_MAX_ATTEMPTS = (
    -(-RELEASE_JOB_BUDGET_SECONDS // RELEASE_SUPERVISION_WINDOW_SECONDS) + 1
)
# One window of margin lets the final re-attachment read the terminal
# receipt that the job wrote as its own deadline arrived.
RELEASE_SUPERVISION_SCHEDULE_TO_CLOSE_SECONDS = (
    RELEASE_JOB_BUDGET_SECONDS + RELEASE_SUPERVISION_WINDOW_SECONDS
)

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
                "start_to_close_seconds": RELEASE_SUPERVISION_WINDOW_SECONDS,
                "schedule_to_close_seconds": (
                    RELEASE_SUPERVISION_SCHEDULE_TO_CLOSE_SECONDS
                ),
            },
            "retries": {
                # The release runs detached and survives its supervisor, so a
                # supervision timeout re-attaches instead of failing the run.
                # Terminal release failures stay terminal: they surface as a
                # non-retryable ToolFailure, not as a timeout.
                "max_attempts": RELEASE_SUPERVISION_MAX_ATTEMPTS,
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


def build_deployment_overview_tool_definition_payload() -> dict[str, Any]:
    """Build the MM-424 read-only deployment overview tool registry definition.

    The overview answers exactly four canonical questions through the
    existing ``mm.tool.execute`` / by-capability boundary on the
    deployment-control worker. Sources are server-supplied via handler
    context (never client claims); per-field owner/permission/timestamp /
    freshness is preserved by ``deployment_overview.answer_question``.
    Ordinary users may call this tool: the handler scopes them to their
    own workflows and denies deployment-wide diagnostics.
    """

    return {
        "name": DEPLOYMENT_OVERVIEW_TOOL_NAME,
        "type": "skill",
        "description": (
            "Answer read-only deployment overview questions (running, waiting, "
            "recent failure, deployment checks) with linked evidence. "
            "Performs no workflow-control, deployment, credential, or "
            "publication mutation."
        ),
        "inputs": {
            "schema": {
                "type": "object",
                "required": ["question"],
                "additionalProperties": False,
                "properties": {
                    "question": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 2000,
                    },
                },
            }
        },
        "outputs": {
            "schema": {
                "type": "object",
                "required": ["status", "question", "answer", "audit"],
                "additionalProperties": False,
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["SUCCEEDED", "REFUSED"],
                    },
                    "question": {"type": "string"},
                    "answer": {
                        "type": "object",
                        "additionalProperties": True,
                    },
                    "audit": {
                        "type": "object",
                        "additionalProperties": True,
                    },
                },
            }
        },
        "executor": {
            "activity_type": "mm.tool.execute",
            "selector": {"mode": "by_capability"},
        },
        "requirements": {"capabilities": ["deployment_control"]},
        "policies": {
            "timeouts": {
                "start_to_close_seconds": 60,
                "schedule_to_close_seconds": 120,
            },
            "retries": {
                "max_attempts": 1,
                "non_retryable_error_codes": list(_NON_RETRYABLE_DIAGNOSIS_ERRORS),
            },
        },
        "security": {
            # Registry-level roles are intentionally open: ordinary users may
            # ask about their own workflows. Deployment-wide disclosure is
            # denied inside the handler by server-resolved principal.
            "allowed_roles": [],
        },
    }


__all__ = [
    "DEPLOYMENT_OVERVIEW_TOOL_NAME",
    "DEPLOYMENT_OVERVIEW_TOOL_VERSION",
    "DEPLOYMENT_UPDATE_TOOL_NAME",
    "DEPLOYMENT_UPDATE_TOOL_VERSION",
    "OPS_DIAGNOSE_STACK_TOOL_NAME",
    "OPS_DIAGNOSE_STACK_TOOL_VERSION",
    "build_deployment_update_tool_definition_payload",
    "build_deployment_overview_tool_definition_payload",
    "build_ops_diagnose_stack_tool_definition_payload",
]
