"""Executable-tool bridge for Docker-backed workload containers."""

from __future__ import annotations

import posixpath
from typing import Any, Awaitable, Callable, Mapping

from pydantic import ValidationError

from moonmind.schemas.workload_models import WorkloadResult, parse_workload_request
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
CONTAINER_START_HELPER_TOOL = "container.start_helper"
CONTAINER_STOP_HELPER_TOOL = "container.stop_helper"
CONTAINER_RUN_CONTAINER_TOOL = "container.run_container"
CONTAINER_RUN_DOCKER_TOOL = "container.run_docker"
INTEGRATION_CI_TOOL = "moonmind.integration_ci"
INTEGRATION_CI_PROFILE_ID = "moonmind-integration-ci"
UNREAL_RUN_TESTS_TOOL = "unreal.run_tests"
DEFAULT_UNREAL_PROFILE_ID = "unreal-5_3-linux"
CURATED_DOOD_TOOL_NAMES = frozenset(
    {
        CONTAINER_RUN_WORKLOAD_TOOL,
        CONTAINER_START_HELPER_TOOL,
        CONTAINER_STOP_HELPER_TOOL,
        INTEGRATION_CI_TOOL,
        UNREAL_RUN_TESTS_TOOL,
    }
)
UNRESTRICTED_DOOD_TOOL_NAMES = frozenset(
    {
        CONTAINER_RUN_CONTAINER_TOOL,
        CONTAINER_RUN_DOCKER_TOOL,
    }
)
DOOD_TOOL_NAMES = frozenset({*CURATED_DOOD_TOOL_NAMES, *UNRESTRICTED_DOOD_TOOL_NAMES})


def normalize_workflow_docker_mode(value: str | None) -> str:
    normalized = str(value or "profiles").strip().lower() or "profiles"
    if normalized not in {"disabled", "profiles", "unrestricted"}:
        raise ValueError(
            "workflow_docker_mode must be one of: disabled, profiles, unrestricted"
        )
    return normalized


def tool_allowed_for_workflow_docker_mode(*, tool_name: str, workflow_docker_mode: str) -> bool:
    normalized_mode = normalize_workflow_docker_mode(workflow_docker_mode)
    normalized_tool = str(tool_name or "").strip()
    if normalized_mode == "disabled":
        return False
    if normalized_mode == "profiles":
        return normalized_tool in CURATED_DOOD_TOOL_NAMES
    return normalized_tool in DOOD_TOOL_NAMES


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


def _unreal_report_paths_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "primary": {"type": "string", "minLength": 1},
            "summary": {"type": "string", "minLength": 1},
            "junit": {"type": "string", "minLength": 1},
        },
        "additionalProperties": False,
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
    elif normalized == CONTAINER_START_HELPER_TOOL:
        input_schema = {
            "type": "object",
            "required": [
                "profileId",
                "repoDir",
                "artifactsDir",
                "command",
                "ttlSeconds",
            ],
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
                "ttlSeconds": {"type": "integer", "minimum": 1},
                "timeoutSeconds": {"type": "integer", "minimum": 1},
                "resources": _resources_schema(),
                "declaredOutputs": _declared_outputs_schema(),
                "sessionId": {"type": "string", "minLength": 1},
                "sessionEpoch": {"type": "integer", "minimum": 1},
                "sourceTurnId": {"type": "string", "minLength": 1},
            },
            "additionalProperties": False,
        }
        description = "Start one policy-gated bounded helper container."
    elif normalized == CONTAINER_STOP_HELPER_TOOL:
        input_schema = {
            "type": "object",
            "required": ["profileId", "repoDir", "artifactsDir", "ttlSeconds"],
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
                "reason": {"type": "string", "minLength": 1},
                "ttlSeconds": {"type": "integer", "minimum": 1},
                "timeoutSeconds": {"type": "integer", "minimum": 1},
                "sessionId": {"type": "string", "minLength": 1},
                "sessionEpoch": {"type": "integer", "minimum": 1},
                "sourceTurnId": {"type": "string", "minLength": 1},
            },
            "additionalProperties": False,
        }
        description = "Stop one policy-gated bounded helper container."
    elif normalized == CONTAINER_RUN_CONTAINER_TOOL:
        input_schema = {
            "type": "object",
            "required": ["image", "repoDir", "artifactsDir", "scratchDir", "command"],
            "properties": {
                "taskRunId": {"type": "string", "minLength": 1},
                "stepId": {"type": "string", "minLength": 1},
                "attempt": {"type": "integer", "minimum": 1},
                "repoDir": {"type": "string", "minLength": 1},
                "artifactsDir": {"type": "string", "minLength": 1},
                "scratchDir": {"type": "string", "minLength": 1},
                "image": {"type": "string", "minLength": 1},
                "entrypoint": {"type": "array", "items": {"type": "string", "minLength": 1}},
                "command": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}},
                "workdir": {"type": "string", "minLength": 1},
                "envOverrides": {"type": "object", "additionalProperties": {"type": "string"}},
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
                "timeoutSeconds": {"type": "integer", "minimum": 1},
                "resources": _resources_schema(),
                "declaredOutputs": _declared_outputs_schema(),
                "sessionId": {"type": "string", "minLength": 1},
                "sessionEpoch": {"type": "integer", "minimum": 1},
                "sourceTurnId": {"type": "string", "minLength": 1},
            },
            "additionalProperties": False,
        }
        description = "Run one unrestricted runtime-selected container through MoonMind."
    elif normalized == CONTAINER_RUN_DOCKER_TOOL:
        input_schema = {
            "type": "object",
            "required": ["repoDir", "artifactsDir", "command"],
            "properties": {
                "taskRunId": {"type": "string", "minLength": 1},
                "stepId": {"type": "string", "minLength": 1},
                "attempt": {"type": "integer", "minimum": 1},
                "repoDir": {"type": "string", "minLength": 1},
                "artifactsDir": {"type": "string", "minLength": 1},
                "command": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}},
                "envOverrides": {"type": "object", "additionalProperties": {"type": "string"}},
                "timeoutSeconds": {"type": "integer", "minimum": 1},
                "resources": _resources_schema(),
                "declaredOutputs": _declared_outputs_schema(),
                "sessionId": {"type": "string", "minLength": 1},
                "sessionEpoch": {"type": "integer", "minimum": 1},
                "sourceTurnId": {"type": "string", "minLength": 1},
            },
            "additionalProperties": False,
        }
        description = "Run one unrestricted Docker CLI command through MoonMind."
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
                "reportPaths": _unreal_report_paths_schema(),
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
    elif normalized == INTEGRATION_CI_TOOL:
        input_schema = {
            "type": "object",
            "required": ["repoDir", "artifactsDir"],
            "properties": {
                "taskRunId": {"type": "string", "minLength": 1},
                "stepId": {"type": "string", "minLength": 1},
                "attempt": {"type": "integer", "minimum": 1},
                "repoDir": {"type": "string", "minLength": 1},
                "artifactsDir": {"type": "string", "minLength": 1},
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
        description = (
            "Run MoonMind hermetic integration tests through a curated "
            "Docker-backed runner profile."
        )
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
    workflow_docker_mode: str = "profiles",
) -> None:
    normalized_mode = normalize_workflow_docker_mode(workflow_docker_mode)
    for tool_name in sorted(DOOD_TOOL_NAMES):
        if not tool_allowed_for_workflow_docker_mode(
            tool_name=tool_name,
            workflow_docker_mode=normalized_mode,
        ):
            continue
        handler = build_workload_tool_handler(
            tool_name=tool_name,
            registry=registry,
            launcher=launcher,
            workflow_docker_mode=normalized_mode,
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
    workflow_docker_mode: str = "profiles",
) -> WorkloadToolHandler:
    normalized = str(tool_name or "").strip()
    normalized_mode = normalize_workflow_docker_mode(workflow_docker_mode)
    if not is_dood_tool(normalized):
        raise ValueError(f"unknown Docker workload tool: {tool_name}")

    async def _handler(
        inputs: Mapping[str, Any],
        context: Mapping[str, Any] | None,
    ) -> SkillResult:
        if not tool_allowed_for_workflow_docker_mode(
            tool_name=normalized,
            workflow_docker_mode=normalized_mode,
        ):
            if normalized_mode == "disabled":
                raise ToolFailure(
                    error_code="PERMISSION_DENIED",
                    message=(
                        "Docker-backed workflow tools are disabled by "
                        "MOONMIND_WORKFLOW_DOCKER_MODE=disabled "
                        "(docker_workflows_disabled)"
                    ),
                    retryable=False,
                    details={"reason": "docker_workflows_disabled"},
                )
            raise ToolFailure(
                error_code="PERMISSION_DENIED",
                message=(
                    f"Docker-backed workflow tool {normalized} is not allowed when "
                    f"MOONMIND_WORKFLOW_DOCKER_MODE={normalized_mode} "
                    "(docker_workflow_mode_forbidden)"
                ),
                retryable=False,
                details={
                    "reason": "docker_workflow_mode_forbidden",
                    "workflowDockerMode": normalized_mode,
                    "toolName": normalized,
                },
            )
        try:
            request = _build_workload_request(
                tool_name=normalized,
                inputs=inputs,
                context=context,
            )
            validated = registry.validate_request(request)
            if normalized == CONTAINER_START_HELPER_TOOL:
                result = await launcher.start_helper(validated)
            elif normalized == CONTAINER_STOP_HELPER_TOOL:
                result = await launcher.stop_helper(
                    validated,
                    reason=str(
                        _request_payload_reason(inputs) or "bounded_window_complete"
                    ),
                )
            else:
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
) -> Any:
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
    if tool_name == CONTAINER_STOP_HELPER_TOOL:
        request_payload.pop("reason", None)
        if "command" not in request_payload:
            request_payload["command"] = ["stop"]

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
        request_payload["profileId"] = DEFAULT_UNREAL_PROFILE_ID
        report_paths = _unreal_report_paths(request_payload.get("reportPaths"))
        request_payload["command"] = _unreal_command(
            request_payload,
            report_paths=report_paths,
        )
        request_payload["declaredOutputs"] = {
            **dict(request_payload.get("declaredOutputs") or {}),
            **_unreal_declared_outputs(report_paths),
        }
        request_payload["envOverrides"] = _unreal_env_overrides(
            request_payload,
            report_paths=report_paths,
        )
        for curated_only_key in (
            "projectPath",
            "target",
            "testSelector",
            "reportPaths",
        ):
            request_payload.pop(curated_only_key, None)
    elif tool_name == INTEGRATION_CI_TOOL:
        request_payload["profileId"] = INTEGRATION_CI_PROFILE_ID
        request_payload["command"] = ("./tools/test_integration.sh",)

    return parse_workload_request(request_payload)


def _request_payload_reason(inputs: Mapping[str, Any]) -> str | None:
    value = inputs.get("reason")
    normalized = str(value or "").strip()
    return normalized or None


def _unreal_report_paths(value: Any) -> dict[str, str]:
    if value is None:
        raw: Mapping[str, Any] = {}
    elif isinstance(value, Mapping):
        raw = value
    else:
        raise ValueError("unreal.run_tests reportPaths must be an object")
    return {
        "primary": _normalize_unreal_report_path(
            raw.get("primary") or "unreal/reports/results.json"
        ),
        "summary": _normalize_unreal_report_path(
            raw.get("summary") or "unreal/reports/summary.json"
        ),
        "junit": _normalize_unreal_report_path(
            raw.get("junit") or "unreal/reports/junit.xml"
        ),
    }


def _normalize_unreal_report_path(value: Any) -> str:
    normalized = str(value).strip().replace("\\", "/")
    normalized = posixpath.normpath(normalized)
    if normalized in {"", "."}:
        raise ValueError("unreal.run_tests reportPaths values must be relative paths")
    if (
        normalized.startswith("/")
        or normalized == ".."
        or normalized.startswith("../")
    ):
        raise ValueError(
            "unreal.run_tests reportPaths values must stay under artifactsDir"
        )
    return normalized


def _unreal_declared_outputs(report_paths: Mapping[str, str]) -> dict[str, str]:
    return {
        "output.primary": report_paths["primary"],
        "output.summary": report_paths["summary"],
        "output.logs.junit": report_paths["junit"],
    }


def _unreal_env_overrides(
    inputs: Mapping[str, Any],
    *,
    report_paths: Mapping[str, str],
) -> dict[str, str]:
    raw_env = inputs.get("envOverrides") or {}
    if not isinstance(raw_env, Mapping):
        raise ValueError("unreal.run_tests envOverrides must be an object")
    env = dict(raw_env)
    project_path = str(inputs.get("projectPath") or "").strip()
    target = str(inputs.get("target") or "").strip()
    test_selector = str(inputs.get("testSelector") or "").strip()
    env["UE_PROJECT_PATH"] = project_path
    if target:
        env["UE_TARGET"] = target
    if test_selector:
        env["UE_TEST_SELECTOR"] = test_selector
    env["UE_REPORT_PATH"] = report_paths["primary"]
    env["UE_SUMMARY_PATH"] = report_paths["summary"]
    env["UE_JUNIT_PATH"] = report_paths["junit"]
    return {str(key): str(value) for key, value in env.items()}


def _unreal_command(
    inputs: Mapping[str, Any],
    *,
    report_paths: Mapping[str, str],
) -> tuple[str, ...]:
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
    command.extend(["--report", report_paths["primary"]])
    command.extend(["--summary", report_paths["summary"]])
    command.extend(["--junit", report_paths["junit"]])
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
        "ready": "COMPLETED",
        "unhealthy": "FAILED",
        "stopped": "COMPLETED",
    }[result.status]
    workload_metadata = dict(result.metadata.get("workload") or {})
    if not workload_metadata and "helper" in result.metadata:
        workload_metadata["helper"] = result.metadata["helper"]
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
    "CONTAINER_RUN_CONTAINER_TOOL",
    "CONTAINER_RUN_DOCKER_TOOL",
    "CONTAINER_START_HELPER_TOOL",
    "CONTAINER_STOP_HELPER_TOOL",
    "DEFAULT_UNREAL_PROFILE_ID",
    "DOOD_TOOL_NAMES",
    "CURATED_DOOD_TOOL_NAMES",
    "INTEGRATION_CI_PROFILE_ID",
    "INTEGRATION_CI_TOOL",
    "UNREAL_RUN_TESTS_TOOL",
    "build_dood_tool_definition_payload",
    "build_workload_tool_handler",
    "is_dood_tool",
    "normalize_workflow_docker_mode",
    "register_workload_tool_handlers",
    "tool_allowed_for_workflow_docker_mode",
]
