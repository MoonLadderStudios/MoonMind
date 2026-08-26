"""Caller-facing executable tool for unrestricted container execution.

This module owns the one generic container surface a repository-owned workflow
or skill can name while ``MOONMIND_WORKFLOW_DOCKER_MODE=unrestricted``:
``container.run_container`` (#3775, epic #3774).

The ownership split is the issue's: the caller supplies the image, the
entrypoint, the command, environment values, the requested GPU resources, a
workspace-relative workdir, generic cache volumes, declared outputs, and the
timeout. MoonMind supplies validation of the generic request shape, resolution
of the current authorized workspace, dispatch through the trusted Docker
worker, and the lifecycle. Host paths, run correlation, and the tool name are
MoonMind-owned and are never accepted from the caller, so the tool schema is
closed against them the same way the canonical container-job contract is.

The canonical asynchronous Container Jobs contract lives in
``moonmind.workloads.tool_bridge`` and is unchanged by this module.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Mapping

from pydantic import ValidationError

from moonmind.schemas.workload_models import (
    UnrestrictedContainerRequest,
    WorkloadGpuCapability,
    WorkloadGpuVendor,
    WorkloadResult,
)
from moonmind.workflow_docker_mode import (
    WorkflowDockerMode,
    normalize_workflow_docker_mode,
)
from moonmind.workloads.registry import RunnerProfileRegistry, WorkloadPolicyError

if TYPE_CHECKING:  # pragma: no cover - import graph hygiene
    # The plan-contract types live under ``moonmind.workflows``, which imports
    # the Temporal activity runtime that routes discovery to this module. They
    # are imported lazily at call time so the two packages stay acyclic.
    from moonmind.workflows.skills.tool_plan_contracts import ToolFailure, ToolResult

CONTAINER_RUN_CONTAINER_TOOL = "container.run_container"
UNRESTRICTED_CONTAINER_TOOL_NAMES = frozenset({CONTAINER_RUN_CONTAINER_TOOL})

#: Key under the tool-execution ``context`` that carries the parent workflow's
#: injected run authority. Callers cannot set it: it is built by the workflow,
#: not by the plan step's ``inputs``.
CONTAINER_EXECUTION_CONTEXT_KEY = "container_execution"

# MoonMind-owned fields on the request model. A caller that names one of them is
# attempting authority injection, not configuring its own container.
_MOONMIND_OWNED_REQUEST_FIELDS = (
    "toolName",
    "agentRunId",
    "stepId",
    "attempt",
    "repoDir",
    "artifactsDir",
    "scratchDir",
    "sessionId",
    "sessionEpoch",
    "sourceTurnId",
)

_GPU_VENDORS = tuple(WorkloadGpuVendor.__args__)  # type: ignore[attr-defined]
_GPU_CAPABILITIES = tuple(WorkloadGpuCapability.__args__)  # type: ignore[attr-defined]

_WORKLOAD_STATUS_TO_TOOL_STATUS = {
    "succeeded": "COMPLETED",
    "failed": "FAILED",
    "timed_out": "FAILED",
    "canceled": "CANCELLED",
    "ready": "COMPLETED",
    "unhealthy": "FAILED",
    "stopped": "COMPLETED",
}


@dataclass(frozen=True, slots=True)
class UnrestrictedContainerWorkspace:
    """The current authorized workspace MoonMind resolved for one step."""

    repo_dir: str
    artifacts_dir: str
    scratch_dir: str


#: Resolves the MoonMind-owned workspace from the workflow-injected authority.
WorkspaceResolver = Callable[
    [Mapping[str, Any]],
    UnrestrictedContainerWorkspace | Awaitable[UnrestrictedContainerWorkspace],
]

ToolHandler = Callable[
    [Mapping[str, Any], Mapping[str, Any] | None],
    Awaitable["ToolResult"],
]


def is_unrestricted_container_tool(name: str) -> bool:
    return str(name or "").strip() in UNRESTRICTED_CONTAINER_TOOL_NAMES


def unrestricted_container_tool_enabled(workflow_docker_mode: object) -> bool:
    """Return whether the deployment-owned Docker mode opens this surface.

    One predicate gates both executable-tool discovery and dispatch, so the
    surface cannot be discoverable in a mode that refuses to run it.
    """

    return normalize_workflow_docker_mode(workflow_docker_mode) == "unrestricted"


def _gpu_resource_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["vendor"],
        "properties": {
            # ``enum`` rather than ``const``: the plan validator enforces enum
            # membership, so an unknown contract version fails at plan
            # validation instead of only at the trusted Activity boundary.
            "contractVersion": {"enum": ["v1"]},
            "vendor": {"enum": list(_GPU_VENDORS)},
            "count": {
                "oneOf": [
                    {"const": "all"},
                    {"type": "integer", "minimum": 1},
                ]
            },
            "capabilities": {
                "type": "array",
                "items": {"enum": list(_GPU_CAPABILITIES)},
                "maxItems": len(_GPU_CAPABILITIES),
            },
        },
        "additionalProperties": False,
    }


def build_unrestricted_container_tool_definition_payload(
    *, name: str
) -> dict[str, Any]:
    """Return the closed caller-facing schema for the unrestricted container tool.

    Run correlation and the authorized workspace are injected by the parent
    workflow and the trusted Activity. Callers provide only their own container
    request, and cannot select a Docker endpoint, ownership labels, privileged
    mode, host namespaces, or device paths.
    """

    normalized = str(name or "").strip()
    if not is_unrestricted_container_tool(normalized):
        raise ValueError(f"Unsupported unrestricted container tool: {normalized!r}")
    input_schema = {
        "type": "object",
        "required": ["image", "command"],
        "properties": {
            "image": {"type": "string", "minLength": 1, "maxLength": 512},
            "entrypoint": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
                "maxItems": 32,
            },
            "command": {
                "type": "array",
                "minItems": 1,
                "items": {"type": "string", "minLength": 1},
                "maxItems": 128,
            },
            "workdir": {
                "type": "string",
                "minLength": 1,
                "description": (
                    "Workspace-relative working directory. MoonMind resolves it "
                    "against the current authorized workspace."
                ),
            },
            "envOverrides": {
                "type": "object",
                "additionalProperties": {"type": "string"},
            },
            "cacheMounts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["source", "target"],
                    "properties": {
                        "source": {"type": "string", "minLength": 1},
                        "target": {"type": "string", "minLength": 1},
                        "readOnly": {"type": "boolean"},
                    },
                    "additionalProperties": False,
                },
            },
            "networkMode": {"type": "string", "enum": ["none", "bridge"]},
            "resources": {
                "type": "object",
                "properties": {
                    "cpu": {"type": "string", "minLength": 1},
                    "memory": {"type": "string", "minLength": 1},
                    "shmSize": {"type": "string", "minLength": 1},
                    "gpu": _gpu_resource_schema(),
                },
                "additionalProperties": False,
            },
            "timeoutSeconds": {"type": "integer", "minimum": 1, "maximum": 86400},
            "declaredOutputs": {
                "type": "object",
                "additionalProperties": {"type": "string", "minLength": 1},
            },
            "collectGlobs": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
            },
        },
        "additionalProperties": False,
    }
    return {
        "name": normalized,
        "type": "skill",
        "description": (
            "Run one caller-owned container through MoonMind's trusted Docker "
            "boundary in unrestricted workflow Docker mode."
        ),
        "inputs": {"schema": input_schema},
        "outputs": {
            "schema": {
                "type": "object",
                "required": ["workloadResult"],
                "properties": {
                    "workloadResult": {"type": "object"},
                    "requestId": {"type": "string"},
                    "workloadStatus": {"type": "string"},
                    "launchOutcome": {"type": "string"},
                    "exitCode": {"type": "integer"},
                    "stdoutRef": {"type": "string"},
                    "stderrRef": {"type": "string"},
                    "diagnosticsRef": {"type": "string"},
                    "outputRefs": {"type": "object"},
                    "workloadMetadata": {"type": "object"},
                },
                "additionalProperties": True,
            }
        },
        "executor": {
            "activity_type": "mm.tool.execute",
            "selector": {"mode": "by_capability"},
        },
        "requirements": {"capabilities": ["docker_workload"]},
        "policies": {
            "timeouts": {
                "start_to_close_seconds": 3600,
                "schedule_to_close_seconds": 3900,
            },
            "retries": {
                "max_attempts": 1,
                "non_retryable_error_codes": ["INVALID_INPUT", "PERMISSION_DENIED"],
            },
        },
        "security": {"allowed_roles": ["user", "admin"]},
    }


def build_container_execution_context(
    *,
    agent_run_id: str,
    step_id: str,
    attempt: int,
    workspace_ref: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Return the workflow-injected run authority for one container step.

    The parent workflow owns every value here. Keeping it in the execution
    context rather than in the plan step's ``inputs`` is what makes the caller
    schema closed against authority injection. An absent ``workspace_ref`` is
    carried through rather than raised in workflow code: the trusted Activity
    refuses it with a stable error, so the step fails instead of the workflow
    task.
    """

    context: dict[str, Any] = {
        "agentRunId": str(agent_run_id),
        "stepId": str(step_id),
        "attempt": int(attempt),
    }
    if workspace_ref is not None:
        context["workspaceRef"] = dict(workspace_ref)
    return context


def _mode_refusal(
    *, workflow_docker_mode: WorkflowDockerMode, tool_name: str
) -> ToolFailure:
    from moonmind.workflows.skills.tool_plan_contracts import ToolFailure

    if workflow_docker_mode == "disabled":
        return ToolFailure(
            error_code="PERMISSION_DENIED",
            message=(
                "Docker-backed workflow tools are disabled by "
                "MOONMIND_WORKFLOW_DOCKER_MODE=disabled "
                "(docker_workflows_disabled)"
            ),
            retryable=False,
            details={"reason": "docker_workflows_disabled", "toolName": tool_name},
        )
    return ToolFailure(
        error_code="PERMISSION_DENIED",
        message=(
            f"{tool_name} requires MOONMIND_WORKFLOW_DOCKER_MODE=unrestricted "
            f"but the deployment selected {workflow_docker_mode} "
            "(docker_workflow_mode_forbidden)"
        ),
        retryable=False,
        details={
            "reason": "docker_workflow_mode_forbidden",
            "workflowDockerMode": workflow_docker_mode,
            "toolName": tool_name,
        },
    )


def _container_execution_authority(
    context: Mapping[str, Any] | None, *, tool_name: str
) -> Mapping[str, Any]:
    from moonmind.workflows.skills.tool_plan_contracts import ToolFailure

    execution = (
        context.get(CONTAINER_EXECUTION_CONTEXT_KEY)
        if isinstance(context, Mapping)
        else None
    )
    if not isinstance(execution, Mapping):
        raise ToolFailure(
            error_code="INVALID_INPUT",
            message=(
                f"{tool_name} requires the parent workflow's container execution "
                "context (container_execution_context_required)"
            ),
            retryable=False,
            details={
                "reason": "container_execution_context_required",
                "toolName": tool_name,
            },
        )
    return execution


def _resolve_workspace_relative_workdir(value: Any, *, repo_dir: str) -> str:
    candidate = str(value or "").strip().replace("\\", "/")
    path = PurePosixPath(candidate)
    if not candidate or path.is_absolute() or any(
        part in {"", ".", ".."} for part in path.parts
    ):
        raise ValueError(
            "workdir must be a normalized workspace-relative path without traversal"
        )
    return str(PurePosixPath(repo_dir) / path)


def build_unrestricted_container_request_payload(
    *,
    tool_name: str,
    inputs: Mapping[str, Any],
    execution: Mapping[str, Any],
    workspace: UnrestrictedContainerWorkspace,
) -> dict[str, Any]:
    """Merge caller-owned inputs with MoonMind-owned authority.

    The caller's image, entrypoint, command, environment, caches, resources,
    timeout, and declared outputs pass through unchanged; only the fields
    MoonMind owns are added.
    """

    if not isinstance(inputs, Mapping):
        raise ValueError("container tool inputs must be an object")
    injected = sorted(
        field for field in _MOONMIND_OWNED_REQUEST_FIELDS if field in inputs
    )
    if injected:
        raise ValueError(
            "container tool inputs must not set MoonMind-owned fields: "
            + ", ".join(injected)
        )
    payload = dict(inputs)
    payload["toolName"] = tool_name
    payload["agentRunId"] = str(execution.get("agentRunId") or "").strip()
    payload["stepId"] = str(execution.get("stepId") or "").strip()
    payload["attempt"] = int(execution.get("attempt") or 1)
    payload["repoDir"] = workspace.repo_dir
    payload["artifactsDir"] = workspace.artifacts_dir
    payload["scratchDir"] = workspace.scratch_dir
    if "workdir" in payload:
        payload["workdir"] = _resolve_workspace_relative_workdir(
            payload["workdir"], repo_dir=workspace.repo_dir
        )
    return payload


def _to_tool_result(result: WorkloadResult) -> ToolResult:
    from moonmind.workflows.skills.tool_plan_contracts import ToolResult

    payload = result.model_dump(mode="json", by_alias=True)
    workload_metadata = dict(result.metadata.get("workload") or {})
    workload_metadata["artifactPublication"] = result.metadata.get(
        "artifactPublication"
    )
    outputs = {
        "workloadResult": payload,
        "requestId": result.request_id,
        "workloadStatus": result.status,
        "launchOutcome": workload_metadata.get("launchOutcome"),
        "exitCode": result.exit_code,
        "stdoutRef": result.stdout_ref,
        "stderrRef": result.stderr_ref,
        "diagnosticsRef": result.diagnostics_ref,
        "outputRefs": dict(result.output_refs),
        "workloadMetadata": workload_metadata,
    }
    return ToolResult(
        status=_WORKLOAD_STATUS_TO_TOOL_STATUS[result.status],
        outputs=outputs,
        progress={
            "workloadStatus": result.status,
            "launchOutcome": workload_metadata.get("launchOutcome"),
            "labels": dict(result.labels),
            "outputRefs": dict(result.output_refs),
        },
    )


def build_unrestricted_container_tool_handler(
    *,
    tool_name: str = CONTAINER_RUN_CONTAINER_TOOL,
    registry: RunnerProfileRegistry,
    launcher: Any,
    workspace_resolver: WorkspaceResolver,
    workflow_docker_mode: object,
) -> ToolHandler:
    """Return the dispatcher handler for one unrestricted container tool."""

    normalized = str(tool_name or "").strip()
    if not is_unrestricted_container_tool(normalized):
        raise ValueError(f"unknown unrestricted container tool: {tool_name}")
    mode = normalize_workflow_docker_mode(workflow_docker_mode)

    async def _handler(
        inputs: Mapping[str, Any],
        context: Mapping[str, Any] | None,
    ) -> ToolResult:
        from moonmind.workflows.skills.tool_plan_contracts import ToolFailure

        if not unrestricted_container_tool_enabled(mode):
            raise _mode_refusal(workflow_docker_mode=mode, tool_name=normalized)
        execution = _container_execution_authority(context, tool_name=normalized)
        try:
            resolved = workspace_resolver(execution)
            if inspect.isawaitable(resolved):
                resolved = await resolved
            request_payload = build_unrestricted_container_request_payload(
                tool_name=normalized,
                inputs=inputs,
                execution=execution,
                workspace=resolved,
            )
            request = UnrestrictedContainerRequest.model_validate(request_payload)
            validated = registry.validate_request(
                request, workflow_docker_mode="unrestricted"
            )
        except WorkloadPolicyError as exc:
            raise ToolFailure(
                error_code="PERMISSION_DENIED",
                message=str(exc),
                retryable=False,
                details={"reason": exc.reason, **exc.details},
            ) from exc
        except (ValidationError, ValueError, TypeError) as exc:
            raise ToolFailure(
                error_code="INVALID_INPUT",
                message=str(exc),
                retryable=False,
                details={"toolName": normalized},
            ) from exc

        result = await launcher.run(validated)
        if not isinstance(result, WorkloadResult):
            result = WorkloadResult.model_validate(result)
        return _to_tool_result(result)

    return _handler


def register_unrestricted_container_tool_handler(
    dispatcher: Any,
    *,
    registry: RunnerProfileRegistry,
    launcher: Any,
    workspace_resolver: WorkspaceResolver,
    workflow_docker_mode: object,
) -> None:
    """Bind the unrestricted container tool on a Docker-capable fleet."""

    dispatcher.register_skill(
        skill_name=CONTAINER_RUN_CONTAINER_TOOL,
        handler=build_unrestricted_container_tool_handler(
            tool_name=CONTAINER_RUN_CONTAINER_TOOL,
            registry=registry,
            launcher=launcher,
            workspace_resolver=workspace_resolver,
            workflow_docker_mode=workflow_docker_mode,
        ),
    )


__all__ = [
    "CONTAINER_EXECUTION_CONTEXT_KEY",
    "CONTAINER_RUN_CONTAINER_TOOL",
    "UNRESTRICTED_CONTAINER_TOOL_NAMES",
    "UnrestrictedContainerWorkspace",
    "build_container_execution_context",
    "build_unrestricted_container_request_payload",
    "build_unrestricted_container_tool_definition_payload",
    "build_unrestricted_container_tool_handler",
    "is_unrestricted_container_tool",
    "register_unrestricted_container_tool_handler",
    "unrestricted_container_tool_enabled",
]
