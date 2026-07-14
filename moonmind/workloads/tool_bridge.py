"""Agent-visible workflow contract for durable container jobs.

Container execution is owned by ``MoonMind.ContainerJob``.  This module only
describes the generic plan tool; it deliberately contains no Docker launcher,
host-path, helper-lifecycle, or raw-CLI dispatch code.
"""

from __future__ import annotations

from typing import Any

CONTAINER_RUN_JOB_TOOL = "container.run_job"
CONTAINER_JOB_TOOL_NAMES = frozenset({CONTAINER_RUN_JOB_TOOL})


def is_container_job_tool(name: str) -> bool:
    return str(name or "").strip() in CONTAINER_JOB_TOOL_NAMES


def build_container_job_tool_definition_payload(*, name: str) -> dict[str, Any]:
    """Return the closed workflow-facing schema for a canonical container job.

    Ownership and workflow/run/step correlation are injected by the parent
    workflow.  Callers provide the remaining canonical submission fields and
    cannot select backend authority or host paths.
    """

    normalized = str(name or "").strip()
    if normalized != CONTAINER_RUN_JOB_TOOL:
        raise ValueError(f"Unsupported container-job tool: {normalized!r}")
    locator_schema = {
        "oneOf": [
            {
                "type": "object",
                "required": ["kind", "artifactRef"],
                "properties": {
                    "kind": {"const": "external_state"},
                    "artifactRef": {"type": "string", "minLength": 1},
                },
                "additionalProperties": False,
            },
            {
                "type": "object",
                "required": ["kind", "workspaceId"],
                "properties": {
                    "kind": {"const": "sandbox"},
                    "workspaceId": {"type": "string", "minLength": 1},
                },
                "additionalProperties": False,
            },
            {
                "type": "object",
                "required": ["kind", "runtimeId", "agentRunId"],
                "properties": {
                    "kind": {"const": "managed"},
                    "runtimeId": {"type": "string", "minLength": 1},
                    "agentRunId": {"type": "string", "minLength": 1},
                },
                "additionalProperties": False,
            },
        ]
    }
    spec_schema = {
        "type": "object",
        "required": ["image", "workspaceRef", "resources"],
        "properties": {
            "image": {"type": "string", "minLength": 1, "maxLength": 512},
            "workspaceRef": locator_schema,
            "command": {"type": "array", "items": {"type": "string"}, "maxItems": 128},
            "entrypoint": {"type": "array", "items": {"type": "string"}, "maxItems": 32},
            "workdir": {"type": "string", "pattern": "^/workspace(?:/[^/]+)*$"},
            "environment": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["name"],
                    "properties": {
                        "name": {"type": "string"},
                        "value": {"type": "string"},
                        "secretRef": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
            },
            "caches": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["cacheRef", "target"],
                    "properties": {
                        "cacheRef": {"type": "string", "minLength": 1},
                        "target": {"type": "string", "minLength": 1},
                        "readOnly": {"type": "boolean"},
                    },
                    "additionalProperties": False,
                },
            },
            "networkMode": {"type": "string", "enum": ["none", "bridge"]},
            "resources": {
                "type": "object",
                "required": ["cpuMillis", "memoryMiB"],
                "properties": {
                    "cpuMillis": {"type": "integer", "minimum": 1},
                    "memoryMiB": {"type": "integer", "minimum": 16},
                    "pids": {"type": "integer", "minimum": 16},
                },
                "additionalProperties": False,
            },
            "timeoutSeconds": {"type": "integer", "minimum": 1, "maximum": 86400},
            "pullPolicy": {"type": "string", "enum": ["if-missing", "always", "never"]},
            "registryCredentialRef": {"type": "string", "minLength": 1},
            "outputs": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["name", "relativePath"],
                    "properties": {
                        "name": {"type": "string", "minLength": 1},
                        "relativePath": {"type": "string", "minLength": 1},
                    },
                    "additionalProperties": False,
                },
            },
        },
        "additionalProperties": False,
    }
    return {
        "name": normalized,
        "type": "skill",
        "description": "Submit and durably await one API-owned container job.",
        "inputs": {
            "schema": {
                "type": "object",
                "required": ["idempotencyKey", "spec"],
                "properties": {
                    "contractVersion": {"const": "v1"},
                    "idempotencyKey": {"type": "string", "minLength": 1, "maxLength": 255},
                    "callerRequestId": {"type": "string", "minLength": 1, "maxLength": 255},
                    "spec": spec_schema,
                },
                "additionalProperties": False,
            }
        },
        "outputs": {
            "schema": {
                "type": "object",
                "required": ["jobId", "state"],
                "properties": {
                    "jobId": {"type": "string"},
                    "state": {"type": "string"},
                    "logsRef": {"type": "string"},
                    "artifactsRef": {"type": "string"},
                    "exitCode": {"type": "integer"},
                    "failureClass": {"type": "string"},
                },
                "additionalProperties": False,
            }
        },
        "executor": {"activity_type": "mm.tool.execute"},
        "requirements": {"capabilities": ["docker_workload"]},
        "policies": {
            "timeouts": {
                "start_to_close_seconds": 86400,
                "schedule_to_close_seconds": 86700,
            },
            "retries": {
                "max_attempts": 1,
                "backoff": "exponential",
                "non_retryable_error_codes": ["INVALID_INPUT", "PERMISSION_DENIED"],
            },
        },
    }


__all__ = [
    "CONTAINER_JOB_TOOL_NAMES",
    "CONTAINER_RUN_JOB_TOOL",
    "build_container_job_tool_definition_payload",
    "is_container_job_tool",
]
