"""Executable-tool bridge for Docker-backed workload containers."""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Mapping

from pydantic import ValidationError

from moonmind.schemas.workload_models import WorkloadRequest, WorkloadResult
from moonmind.workflows.skills.skill_plan_contracts import (
    SkillFailure as ToolFailure,
    SkillResult,
)
from moonmind.workloads.registry import RunnerProfileRegistry, WorkloadPolicyError


WorkloadToolHandler = Callable[
    [Mapping[str, Any], Mapping[str, Any] | None],
    SkillResult | Awaitable[SkillResult],
]

CONTAINER_RUN_WORKLOAD_TOOL = "container.run_workload"
UNREAL_RUN_TESTS_TOOL = "unreal.run_tests"
DEFAULT_UNREAL_PROFILE_ID = "unreal-5_3-linux"
DOOD_TOOL_NAMES = frozenset({CONTAINER_RUN_WORKLOAD_TOOL, UNREAL_RUN_TESTS_TOOL})


def is_dood_tool(name: str) -> bool:
    return str(name or "").strip() in DOOD_TOOL_NAMES


def _resources_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "cpu": {"type": "string", "minLength": 1},
            "memory": {"type": "string", "minLength": 1},
            "shmSize": {"type": "string", "minLength": 1},
        },
        "additionalProperties": False,
    }


def _declared_outputs_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": {"type": "string", "minLength": 1},
    }


def build_dood_tool_definition_payload(*, name: str, version: str) -> dict[str, Any]:
    """Return the pinned ToolDefinition payload for a Docker workload tool."""

    normalized = str(name or "").strip()
    if normalized == CONTAINER_RUN_WORKLOAD_TOOL:
        input_schema = {
            "type": "object",
            "required": ["profileId", "repoDir", "artifactsDir", "command"],
            "properties": {
                "profileId": {"type": "string", "minLength": 1},
                "taskRunId": {"type": "string", "minLength": 1},
                "stepId": {"type": "string", "minLength": 1},
                "attempt": {"type": "integer", "minimum": 1},
                "repoDir": {"type": "string", "minLength": 1},
                "artifactsDir": {"type": "string", "minLength": 1},
                "command": {
                    "type": "array",
                    "minItems": 1,
                    "items": {"type": "string", "minLength": 1},
                },
                "envOverrides": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                },
                "timeoutSeconds": {"type": "integer", "minimum": 1},
                "resources": _resources_schema(),
                "declaredOutputs": _declared_outputs_schema(),
                "sessionId": {"type": "string", "minLength": 1},
                "sessionEpoch": {"type": "integer", "minimum": 1},
                "sourceTurnId": {"type": "string", "minLength": 1},
            },
            "additionalProperties": False,
        }
        description = "Run one policy-gated Docker workload through MoonMind."
    elif normalized == UNREAL_RUN_TESTS_TOOL:
        input_schema = {
            "type": "object",
            "required": ["repoDir", "artifactsDir", "projectPath"],
            "properties": {
                "profileId": {"type": "string", "minLength": 1},
                "taskRunId": {"type": "string", "minLength": 1},
                "stepId": {"type": "string", "minLength": 1},
                "attempt": {"type": "integer", "minimum": 1},
                "repoDir": {"type": "string", "minLength": 1},
                "artifactsDir": {"type": "string", "minLength": 1},
                "projectPath": {"type": "string", "minLength": 1},
                "target": {"type": "string", "minLength": 1},
                "testSelector": {"type": "string", "minLength": 1},
                "timeoutSeconds": {"type": "integer", "minimum": 1},
                "envOverrides": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                },
                "resources": _resources_schema(),
                "declaredOutputs": _declared_outputs_schema(),
                "sessionId": {"type": "string", "minLength": 1},
                "sessionEpoch": {"type": "integer", "minimum": 1},
                "sourceTurnId": {"type": "string", "minLength": 1},
            },
            "additionalProperties": False,
        }
        description = "Run Unreal Engine tests through a curated runner profile."
    else:
        raise ValueError(f"unknown Docker workload tool: {name}")

    return {
        "name": normalized,
        "version": str(version or "").strip() or "1.0",
        "type": "skill",
        "description": description,
        "inputs": {"schema": input_schema},
        "outputs": {
            "schema": {
                "type": "object",
                "required": ["workloadResult"],
                "properties": {
                    "workloadResult": {"type": "object"},
                    "requestId": {"type": "string"},
                    "profileId": {"type": "string"},
                    "workloadStatus": {"type": "string"},
                    "exitCode": {"type": ["integer", "null"]},
                    "stdoutRef": {"type": ["string", "null"]},
                    "stderrRef": {"type": ["string", "null"]},
                    "diagnosticsRef": {"type": ["string", "null"]},
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


def register_workload_tool_handlers(
    dispatcher: Any,
    *,
    registry: RunnerProfileRegistry,
    launcher: Any,
) -> None:
    for tool_name in sorted(DOOD_TOOL_NAMES):
        handler = build_workload_tool_handler(
            tool_name=tool_name,
            registry=registry,
            launcher=launcher,
        )
        for version in ("1.0", "1.0.0"):
            dispatcher.register_skill(
                skill_name=tool_name,
                version=version,
                handler=handler,
            )


def build_workload_tool_handler(
    *,
    tool_name: str,
    registry: RunnerProfileRegistry,
    launcher: Any,
) -> WorkloadToolHandler:
    normalized = str(tool_name or "").strip()
    if not is_dood_tool(normalized):
        raise ValueError(f"unknown Docker workload tool: {tool_name}")

    async def _handler(
        inputs: Mapping[str, Any],
        context: Mapping[str, Any] | None,
    ) -> SkillResult:
        try:
            request = _build_workload_request(
                tool_name=normalized,
                inputs=inputs,
                context=context,
            )
            validated = registry.validate_request(request)
            result = await launcher.run(validated)
        except WorkloadPolicyError as exc:
            raise ToolFailure(
                error_code="PERMISSION_DENIED",
                message=str(exc),
                retryable=False,
                details={"reason": exc.reason, **exc.details},
            ) from exc
        except (ValidationError, ValueError) as exc:
            raise ToolFailure(
                error_code="INVALID_INPUT",
                message=str(exc),
                retryable=False,
            ) from exc

        if not isinstance(result, WorkloadResult):
            result = WorkloadResult.model_validate(result)
        return _to_skill_result(result)

    return _handler


def _build_workload_request(
    *,
    tool_name: str,
    inputs: Mapping[str, Any],
    context: Mapping[str, Any] | None,
) -> WorkloadRequest:
    if not isinstance(inputs, Mapping):
        raise ValueError("workload tool inputs must be an object")
    request_payload = dict(inputs)
    if "taskRunId" not in request_payload:
        request_payload["taskRunId"] = _context_text(context, "workflow_id")
    if "stepId" not in request_payload:
        request_payload["stepId"] = _context_text(context, "node_id")
    if "attempt" not in request_payload:
        request_payload["attempt"] = _context_int(context, "attempt", default=1)
    request_payload["toolName"] = tool_name

    for source_key, target_key in (
        ("session_id", "sessionId"),
        ("session_epoch", "sessionEpoch"),
        ("source_turn_id", "sourceTurnId"),
    ):
        if target_key not in request_payload:
            value = (context or {}).get(source_key) if isinstance(context, Mapping) else None
            if value is not None:
                request_payload[target_key] = value

    if tool_name == UNREAL_RUN_TESTS_TOOL:
        request_payload.setdefault("profileId", DEFAULT_UNREAL_PROFILE_ID)
        request_payload["command"] = _unreal_command(request_payload)
        for curated_only_key in ("projectPath", "target", "testSelector"):
            request_payload.pop(curated_only_key, None)

    return WorkloadRequest.model_validate(request_payload)


def _unreal_command(inputs: Mapping[str, Any]) -> tuple[str, ...]:
    project_path = str(inputs.get("projectPath") or "").strip()
    if not project_path:
        raise ValueError("unreal.run_tests projectPath is required")
    command: list[str] = ["unreal-run-tests", "--project", project_path]
    target = str(inputs.get("target") or "").strip()
    if target:
        command.extend(["--target", target])
    test_selector = str(inputs.get("testSelector") or "").strip()
    if test_selector:
        command.extend(["--test", test_selector])
    return tuple(command)


def _context_text(context: Mapping[str, Any] | None, key: str) -> str:
    value = context.get(key) if isinstance(context, Mapping) else None
    normalized = str(value or "").strip()
    if normalized:
        return normalized
    raise ValueError(f"context.{key} is required for workload tool execution")


def _context_int(
    context: Mapping[str, Any] | None,
    key: str,
    *,
    default: int,
) -> int:
    value = context.get(key) if isinstance(context, Mapping) else None
    if value is None or value == "":
        return default
    return int(value)


def _to_skill_result(result: WorkloadResult) -> SkillResult:
    payload = result.model_dump(mode="json", by_alias=True)
    status = {
        "succeeded": "COMPLETED",
        "failed": "FAILED",
        "timed_out": "FAILED",
        "canceled": "CANCELLED",
    }[result.status]
    workload_metadata = dict(result.metadata.get("workload") or {})
    workload_metadata["artifactPublication"] = result.metadata.get(
        "artifactPublication"
    )
    return SkillResult(
        status=status,
        outputs={
            "workloadResult": payload,
            "requestId": result.request_id,
            "profileId": result.profile_id,
            "workloadStatus": result.status,
            "exitCode": result.exit_code,
            "stdoutRef": result.stdout_ref,
            "stderrRef": result.stderr_ref,
            "diagnosticsRef": result.diagnostics_ref,
            "outputRefs": dict(result.output_refs),
            "workloadMetadata": workload_metadata,
        },
        progress={
            "profileId": result.profile_id,
            "workloadStatus": result.status,
            "labels": dict(result.labels),
            "stdoutRef": result.stdout_ref,
            "stderrRef": result.stderr_ref,
            "diagnosticsRef": result.diagnostics_ref,
            "outputRefs": dict(result.output_refs),
            "workloadMetadata": workload_metadata,
        },
    )


__all__ = [
    "CONTAINER_RUN_WORKLOAD_TOOL",
    "DEFAULT_UNREAL_PROFILE_ID",
    "DOOD_TOOL_NAMES",
    "UNREAL_RUN_TESTS_TOOL",
    "build_dood_tool_definition_payload",
    "build_workload_tool_handler",
    "is_dood_tool",
    "register_workload_tool_handlers",
]
