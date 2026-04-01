"""Temporal execution lifecycle API router."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Optional

logger = logging.getLogger(__name__)

from functools import lru_cache

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response, status
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio.client import Client
from temporalio.service import RPCError

from api_service.auth_providers import get_current_user
from api_service.db.base import get_async_session
from api_service.db.models import (
    MoonMindWorkflowState,
    TemporalExecutionCanonicalRecord,
    TemporalExecutionCloseStatus,
    TemporalExecutionRecord,
    User,
)
from moonmind.config.settings import settings
from moonmind.workflows.tasks.routing import _coerce_bool
from moonmind.schemas.manifest_ingest_models import (
    ManifestNodePageModel,
    ManifestStatusSnapshotModel,
)
from moonmind.schemas.temporal_models import (
    CancelExecutionRequest,
    ConfigureIntegrationMonitoringRequest,
    CreateExecutionRequest,
    ExecutionActionCapabilityModel,
    ExecutionDebugFieldsModel,
    ExecutionListResponse,
    ExecutionModel,
    ExecutionRefreshEnvelope,
    PollIntegrationRequest,
    RescheduleExecutionRequest,
    ScheduleCreatedResponse,
    ScheduleParameters,
    SignalExecutionRequest,
    UpdateExecutionRequest,
    UpdateExecutionResponse,
    TASK_RUN_ID_MEMO_KEYS,
    TASK_RUN_ID_PARAM_KEYS,
    TASK_RUN_ID_SEARCH_ATTR_KEYS,
)
from moonmind.workflows.temporal import (
    TemporalExecutionNotFoundError,
    TemporalExecutionService,
    TemporalExecutionValidationError,
    build_manifest_status_snapshot,
)
from moonmind.workflows.temporal.client import TemporalClientAdapter
from api_service.api.schemas import CreateJobRequest

router = APIRouter(prefix="/api/executions", tags=["executions"])
_TEMPORAL_SOURCE = "temporal"
_ALLOWED_OWNER_TYPES = {"user", "system", "service"}
_DASHBOARD_STATUS_BY_STATE: dict[MoonMindWorkflowState, str] = {
    MoonMindWorkflowState.SCHEDULED: "queued",
    MoonMindWorkflowState.INITIALIZING: "queued",
    MoonMindWorkflowState.WAITING_ON_DEPENDENCIES: "waiting",
    MoonMindWorkflowState.PLANNING: "running",
    MoonMindWorkflowState.AWAITING_SLOT: "queued",
    MoonMindWorkflowState.EXECUTING: "running",
    MoonMindWorkflowState.PROPOSALS: "running",
    MoonMindWorkflowState.AWAITING_EXTERNAL: "awaiting_action",
    MoonMindWorkflowState.FINALIZING: "running",
    MoonMindWorkflowState.COMPLETED: "completed",
    MoonMindWorkflowState.FAILED: "failed",
    MoonMindWorkflowState.CANCELED: "canceled",
}

_MAX_TASK_TITLE_LENGTH = 120
_MAX_TASK_SUMMARY_LENGTH = 180
_TASK_SUMMARY_ELLIPSIS = "..."


def _enum_value(value: object | None) -> str | None:
    if value is None:
        return None
    return getattr(value, "value", value)


@lru_cache(maxsize=1)
def get_temporal_client_adapter() -> TemporalClientAdapter:
    return TemporalClientAdapter()


async def get_temporal_client(
    adapter: TemporalClientAdapter = Depends(get_temporal_client_adapter),
) -> Client:
    return await adapter.get_client()


def _is_execution_admin(user: User | None) -> bool:
    return bool(user and getattr(user, "is_superuser", False))


def _owner_id(user: User | None) -> str | None:
    value = getattr(user, "id", None)
    return str(value) if value is not None else None


def _canonicalize_execution_identifier(raw_identifier: str) -> tuple[str, bool]:
    canonical = TemporalExecutionRecord.canonicalize_identifier(raw_identifier)
    return canonical, canonical != raw_identifier


def _mark_execution_alias_usage(
    response: Response, *, raw_identifier: str, canonical_identifier: str
) -> None:
    if raw_identifier == canonical_identifier:
        return
    response.headers["Deprecation"] = "true"
    response.headers["X-MoonMind-Canonical-WorkflowId"] = canonical_identifier
    response.headers["X-MoonMind-Deprecated-Identifier"] = raw_identifier


def _compatibility_refreshed_at(record) -> datetime:
    updated_at = record.updated_at
    if isinstance(updated_at, str):
        updated_at = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
    if updated_at.tzinfo is not None:
        return updated_at
    return updated_at.replace(tzinfo=UTC)


def _manifest_attr(manifest_status, field: str, default=None):
    return getattr(manifest_status, field, default) if manifest_status else default


def _normalize_owner_type(record, search_attributes: dict[str, object]) -> str:
    owner_type = str(search_attributes.get("mm_owner_type") or "").strip().lower()
    if owner_type in _ALLOWED_OWNER_TYPES:
        return owner_type
    owner_id = str(record.owner_id or "").strip().lower()
    return "system" if owner_id == "system" or not owner_id else "user"


def _coerce_temporal_scalar(value: object | None) -> str:
    if isinstance(value, (list, tuple)):
        for item in value:
            text = _coerce_temporal_scalar(item)
            if text:
                return text
        return ""
    if isinstance(value, Mapping):
        return ""
    if value is None:
        return ""
    return str(value).strip()


def _normalize_entry_value(value: object | None) -> str | None:
    candidate = _coerce_temporal_scalar(value).lower()
    if not candidate:
        return None
    if candidate in {"run", "manifest"}:
        return candidate

    # Some Temporal payloads surface keyword attributes as arrays; if those are
    # later stringified we may see values like "['run']" or '["run"]'.
    if candidate.startswith("[") and candidate.endswith("]"):
        inner = candidate[1:-1].strip()
        if inner:
            first = inner.split(",", 1)[0].strip().strip("'\"")
            if first in {"run", "manifest"}:
                return first
    return None


def _resolve_execution_entry(record, search_attributes: dict[str, object]) -> str:
    entry = _normalize_entry_value(search_attributes.get("mm_entry"))
    if entry:
        return entry

    entry = _normalize_entry_value(getattr(record, "entry", ""))
    if entry:
        return entry

    workflow_type = str(
        getattr(getattr(record, "workflow_type", None), "value", "")
    ).lower()
    if workflow_type.endswith("manifestingest"):
        return "manifest"
    return "run"


async def _get_service(
    session: AsyncSession = Depends(get_async_session),
) -> TemporalExecutionService:
    return TemporalExecutionService(
        session,
        namespace=settings.temporal.namespace,
        integration_task_queue=settings.temporal.activity_integrations_task_queue,
        integration_poll_initial_seconds=(
            settings.temporal.integration_poll_initial_seconds
        ),
        integration_poll_max_seconds=settings.temporal.integration_poll_max_seconds,
        integration_poll_jitter_ratio=settings.temporal.integration_poll_jitter_ratio,
        run_continue_as_new_step_threshold=(
            settings.temporal.run_continue_as_new_step_threshold
        ),
        run_continue_as_new_wait_cycle_threshold=(
            settings.temporal.run_continue_as_new_wait_cycle_threshold
        ),
        manifest_continue_as_new_phase_threshold=(
            settings.temporal.manifest_continue_as_new_phase_threshold
        ),
    )


def _ensure_actions_enabled() -> None:
    """FastAPI dependency: raise 403 when Temporal execution actions are disabled."""
    if not settings.temporal_dashboard.actions_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "actions_disabled",
                "message": "Temporal execution actions are disabled.",
            },
        )

def _ensure_submit_enabled() -> None:
    """FastAPI dependency: raise 503 when Temporal execution submission is disabled."""
    if not settings.temporal_dashboard.submit_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "temporal_submit_disabled",
                "message": (
                    "Temporal task submission is disabled "
                    "(temporal_dashboard.submit_enabled=False). "
                    "The legacy queue execution substrate is no longer supported. "
                    "Enable Temporal submission to proceed."
                ),
            },
        )


def _serialize_execution(
    record, *, include_artifact_refs: bool = True
) -> ExecutionModel:
    temporal_status = "running"
    close_status = _enum_value(record.close_status)
    memo = dict(record.memo or {})
    search_attributes = dict(record.search_attributes or {})
    integration_state = getattr(record, "integration_state", None)
    state_value = _enum_value(record.state) or ""
    workflow_type_value = _enum_value(record.workflow_type) or ""
    continue_as_new_cause = memo.get("continue_as_new_cause") or search_attributes.get(
        "mm_continue_as_new_cause"
    )
    if record.close_status is TemporalExecutionCloseStatus.COMPLETED:
        temporal_status = "completed"
    elif record.close_status is TemporalExecutionCloseStatus.CANCELED:
        temporal_status = "canceled"
    elif record.close_status in {
        TemporalExecutionCloseStatus.FAILED,
        TemporalExecutionCloseStatus.TERMINATED,
        TemporalExecutionCloseStatus.TIMED_OUT,
    }:
        temporal_status = "failed"

    raw_state = state_value
    manifest_status = None
    if workflow_type_value == "MoonMind.ManifestIngest":
        manifest_status = build_manifest_status_snapshot(record)
    owner_type = _normalize_owner_type(record, search_attributes)
    owner_id = str(search_attributes.get("mm_owner_id") or record.owner_id or "system")
    entry = _resolve_execution_entry(record, search_attributes)
    title = str(memo.get("title") or "").strip() or workflow_type_value
    summary = str(memo.get("summary") or "").strip() or "Execution updated."
    waiting_reason = (
        str(getattr(record, "waiting_reason", "") or "").strip()
        or str(memo.get("waiting_reason") or "").strip()
        or (
            str(memo.get("summary") or "").strip()
            if raw_state == "awaiting_external"
            else ""
        )
    )
    attention_required = bool(
        getattr(record, "attention_required", False)
        or memo.get("attention_required")
        or False
    )
    if raw_state == "awaiting_external":
        attention_required = True
    dashboard_status = _DASHBOARD_STATUS_BY_STATE.get(record.state, "queued")
    actions = _build_action_capabilities(record)
    debug_fields = _build_debug_fields(
        record=record,
        temporal_status=temporal_status,
        close_status=close_status,
        waiting_reason=waiting_reason,
        attention_required=attention_required,
    )

    params_raw = getattr(record, "parameters", None)
    params = dict(params_raw) if isinstance(params_raw, dict) else {}
    task_run_id = None
    # The sources are checked in order of preference.
    sources_to_check = (
        [memo.get(k) for k in TASK_RUN_ID_MEMO_KEYS]
        + [search_attributes.get(k) for k in TASK_RUN_ID_SEARCH_ATTR_KEYS]
        + [params.get(k) for k in TASK_RUN_ID_PARAM_KEYS]
    )
    for value in sources_to_check:
        if value:
            candidate = str(value).strip()
            if candidate:
                task_run_id = candidate
                break
    target_runtime, param_model, param_effort = [
        str(params.get(key) or "").strip() or None
        for key in ["targetRuntime", "model", "effort"]
    ]
    if not target_runtime:
        runtime_nested = params.get("runtime")
        if isinstance(runtime_nested, dict):
            target_runtime = str(runtime_nested.get("mode") or "").strip() or None
    if not target_runtime:
        target_runtime = (
            _coerce_temporal_scalar(search_attributes.get("mm_target_runtime"))
            or _coerce_temporal_scalar(search_attributes.get("mm_runtime"))
            or _coerce_temporal_scalar(search_attributes.get("runtime"))
        ) or None

    task_params = params.get("task") if isinstance(params.get("task"), dict) else {}
    tool_params = task_params.get("tool") if isinstance(task_params.get("tool"), dict) else {}
    skill_params = task_params.get("skill") if isinstance(task_params.get("skill"), dict) else {}
    target_skill = (
        str(tool_params.get("name") or skill_params.get("name") or "").strip() or None
    )
    if not target_skill:
        target_skill = (
            _coerce_temporal_scalar(search_attributes.get("mm_target_skill"))
            or _coerce_temporal_scalar(search_attributes.get("mm_skill_id"))
            or _coerce_temporal_scalar(search_attributes.get("mm_skill"))
            or _coerce_temporal_scalar(search_attributes.get("skillId"))
            or _coerce_temporal_scalar(search_attributes.get("skill"))
        ) or None

    task_payload = params.get("task")
    if not isinstance(task_payload, dict):
        task_payload = {}

    git_payload = task_payload.get("git")
    if not isinstance(git_payload, dict):
        git_payload = {}

    publish_payload = task_payload.get("publish")
    if not isinstance(publish_payload, dict):
        publish_payload = {}

    # Precedence: task.git.startingBranch > task.startingBranch > params.startingBranch
    starting_branch = str(
        git_payload.get("startingBranch")
        or task_payload.get("startingBranch")
        or params.get("startingBranch")
        or ""
    ).strip() or None
    # Only show the "(default)" fallback when git context exists in the payload.
    has_git_context = bool(git_payload) or any(
        task_payload.get(k) or params.get(k)
        for k in ("startingBranch", "targetBranch", "defaultBranch", "branch")
    )
    if not starting_branch and has_git_context:
        default_branch = str(
            git_payload.get("defaultBranch") or params.get("defaultBranch") or "main"
        ).strip()
        starting_branch = f"{default_branch} (default)"

    # Precedence: task.git.targetBranch > task.targetBranch > params.targetBranch
    target_branch = str(
        git_payload.get("targetBranch")
        or task_payload.get("targetBranch")
        or params.get("targetBranch")
        or ""
    ).strip() or None

    repository = (
        _coerce_temporal_scalar(git_payload.get("repository"))
        or _coerce_temporal_scalar(task_payload.get("repository"))
        or _coerce_temporal_scalar(params.get("repository"))
        or _coerce_temporal_scalar(params.get("repo"))
        or _coerce_temporal_scalar(task_payload.get("repo"))
    ) or None

    if not repository:
        repository = (
            _coerce_temporal_scalar(search_attributes.get("mm_repository"))
            or _coerce_temporal_scalar(search_attributes.get("mm_repo"))
            or _coerce_temporal_scalar(search_attributes.get("repository"))
            or _coerce_temporal_scalar(memo.get("repository"))
            or _coerce_temporal_scalar(params.get("repository"))
            or _coerce_temporal_scalar(params.get("repo"))
            or _coerce_temporal_scalar(git_payload.get("repository"))
            or _coerce_temporal_scalar(task_payload.get("repository"))
        ) or None

    _ALLOWED_PUBLISH_MODES = {"branch", "pr", "none"}
    raw_publish_mode = str(
        params.get("publishMode") or publish_payload.get("mode") or ""
    ).strip() or None
    publish_mode = raw_publish_mode if raw_publish_mode in _ALLOWED_PUBLISH_MODES else None

    return ExecutionModel(
        task_id=record.workflow_id,
        task_run_id=task_run_id,
        namespace=record.namespace,
        source=_TEMPORAL_SOURCE,
        workflow_id=record.workflow_id,
        run_id=record.run_id,
        temporal_run_id=record.run_id,
        legacy_run_id=None,
        workflow_type=workflow_type_value,
        entry=entry or record.entry,
        owner_type=owner_type,
        owner_id=owner_id,
        title=title,
        summary=summary,
        status=dashboard_status,
        dashboard_status=dashboard_status,
        state=state_value,
        raw_state=raw_state,
        temporal_status=temporal_status,
        close_status=close_status,
        waiting_reason=str(waiting_reason) if waiting_reason else None,
        attention_required=attention_required,
        search_attributes=search_attributes,
        memo=memo,
        target_runtime=target_runtime,
        target_skill=target_skill,
        model=param_model,
        effort=param_effort,
        starting_branch=starting_branch,
        target_branch=target_branch,
        repository=repository,
        publish_mode=publish_mode,
        artifact_refs=(
            list(record.artifact_refs or []) if include_artifact_refs else []
        ),
        manifest_artifact_ref=_manifest_attr(
            manifest_status,
            "manifest_artifact_ref",
            getattr(record, "manifest_ref", None),
        ),
        plan_artifact_ref=_manifest_attr(manifest_status, "plan_artifact_ref"),
        summary_artifact_ref=_manifest_attr(manifest_status, "summary_artifact_ref"),
        run_index_artifact_ref=_manifest_attr(
            manifest_status, "run_index_artifact_ref"
        ),
        checkpoint_artifact_ref=_manifest_attr(
            manifest_status, "checkpoint_artifact_ref"
        ),
        requested_by=_manifest_attr(manifest_status, "requested_by"),
        execution_policy=_manifest_attr(manifest_status, "execution_policy"),
        phase=_manifest_attr(manifest_status, "phase"),
        paused=_manifest_attr(manifest_status, "paused"),
        counts=_manifest_attr(manifest_status, "counts"),
        artifacts_count=len(record.artifact_refs or []),
        scheduled_for=getattr(record, "scheduled_for", None),
        created_at=getattr(record, "created_at", None) or record.started_at,
        actions=actions,
        debug_fields=debug_fields,
        redirect_path=f"/tasks/{record.workflow_id}?source=temporal",
        integration=(
            dict(integration_state) if isinstance(integration_state, dict) else None
        ),
        latest_run_view=True,
        continue_as_new_cause=continue_as_new_cause,
        started_at=record.started_at,
        updated_at=record.updated_at,
        closed_at=record.closed_at,
        detail_href=f"/tasks/{record.workflow_id}",
        ui_query_model="compatibility_adapter",
        stale_state=False,
        refreshed_at=_compatibility_refreshed_at(record),
    )


def _build_action_capabilities(record) -> ExecutionActionCapabilityModel:
    raw_state = str(record.state.value).strip().lower()
    if not settings.temporal_dashboard.actions_enabled:
        return ExecutionActionCapabilityModel(
            disabled_reasons={
                action: "actions_disabled"
                for action in (
                    "setTitle",
                    "updateInputs",
                    "rerun",
                    "approve",
                    "pause",
                    "resume",
                    "cancel",
                )
            }
        )

    state_actions = {
        "scheduled": {"can_set_title", "can_update_inputs", "can_cancel"},
        "initializing": {"can_set_title", "can_update_inputs", "can_cancel"},
        "waiting_on_dependencies": {"can_set_title", "can_update_inputs", "can_cancel"},
        "awaiting_slot": {"can_set_title", "can_update_inputs", "can_cancel"},
        "planning": {"can_set_title", "can_update_inputs", "can_cancel"},
        "executing": {
            "can_set_title",
            "can_update_inputs",
            "can_pause",
            "can_cancel",
        },
        "proposals": {
            "can_set_title",
            "can_update_inputs",
            "can_cancel",
        },
        "awaiting_external": {"can_approve", "can_pause", "can_resume", "can_cancel"},
        "finalizing": {"can_cancel"},
        "completed": {"can_rerun"},
        "failed": {"can_rerun"},
        "canceled": {"can_rerun"},
    }
    enabled = state_actions.get(raw_state, set())
    capability_values = {
        "can_set_title": "canSetTitle",
        "can_update_inputs": "canUpdateInputs",
        "can_rerun": "canRerun",
        "can_approve": "canApprove",
        "can_pause": "canPause",
        "can_resume": "canResume",
        "can_cancel": "canCancel",
    }
    disabled_reasons = {
        alias: "state_not_eligible"
        for field_name, alias in capability_values.items()
        if field_name not in enabled
    }
    return ExecutionActionCapabilityModel(
        can_set_title="can_set_title" in enabled,
        can_update_inputs="can_update_inputs" in enabled,
        can_rerun="can_rerun" in enabled,
        can_approve="can_approve" in enabled,
        can_pause="can_pause" in enabled,
        can_resume="can_resume" in enabled,
        can_cancel="can_cancel" in enabled,
        disabled_reasons=disabled_reasons,
    )


def _build_debug_fields(
    *,
    record,
    temporal_status: str,
    close_status: str | None,
    waiting_reason: str | None,
    attention_required: bool,
) -> ExecutionDebugFieldsModel | None:
    if not settings.temporal_dashboard.debug_fields_enabled:
        return None
    return ExecutionDebugFieldsModel(
        workflow_id=record.workflow_id,
        temporal_run_id=record.run_id,
        legacy_run_id=None,
        namespace=record.namespace,
        temporal_status=temporal_status,
        raw_state=record.state.value,
        close_status=close_status,
        waiting_reason=waiting_reason,
        attention_required=attention_required,
    )


def _coerce_artifact_ref(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None
    if isinstance(value, dict):
        for key in ("artifactId", "artifact_id", "id"):
            candidate = str(value.get(key) or "").strip()
            if candidate:
                return candidate
    return None


def _invalid_task_request(message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={
            "code": "invalid_execution_request",
            "message": message,
        },
    )


def _coerce_string_list(value: Any, *, field_name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise _invalid_task_request(f"{field_name} must be a JSON array of strings.")

    normalized: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise _invalid_task_request(
                f"{field_name} must be a JSON array of strings."
            )
        candidate = item.strip()
        if candidate:
            normalized.append(candidate)
    return normalized


def _coerce_step_count(value: Any) -> int:
    if value is None:
        return 0
    if not isinstance(value, list):
        raise _invalid_task_request("payload.task.steps must be a JSON array.")
    return len(value)


def _normalize_task_tool(task_payload: dict[str, Any]) -> dict[str, Any] | None:
    tool_payload = (
        task_payload.get("tool") if isinstance(task_payload.get("tool"), dict) else {}
    )
    skill_payload = (
        task_payload.get("skill") if isinstance(task_payload.get("skill"), dict) else {}
    )
    selected_payload = tool_payload or skill_payload
    if not selected_payload:
        return None

    tool_type = str(
        selected_payload.get("type") or selected_payload.get("kind") or "skill"
    ).strip()
    if tool_type and tool_type.lower() != "skill":
        raise _invalid_task_request(
            "payload.task.tool.type must be 'skill' for Temporal-backed submit."
        )

    name = str(selected_payload.get("name") or selected_payload.get("id") or "").strip()
    if not name:
        return None
    version = str(selected_payload.get("version") or "").strip() or "1.0"
    normalized: dict[str, Any] = {
        "type": "skill",
        "name": name,
        "version": version,
    }

    inline_inputs = selected_payload.get("inputs")
    if not isinstance(inline_inputs, dict):
        inline_inputs = selected_payload.get("args")
    if isinstance(inline_inputs, dict) and inline_inputs:
        normalized["inputs"] = dict(inline_inputs)
    return normalized


def _validate_task_runtime_requirements(
    *,
    task_payload: dict[str, Any],
    normalized_tool: dict[str, Any] | None,
    normalized_task_for_planner: dict[str, Any],
) -> None:
    instructions = str(task_payload.get("instructions") or "").strip()
    if instructions:
        return

    tool_name = str((normalized_tool or {}).get("name") or "").strip().lower()
    if tool_name != "pr-resolver":
        return

    task_inputs = (
        normalized_task_for_planner.get("inputs")
        if isinstance(normalized_task_for_planner.get("inputs"), dict)
        else {}
    )
    task_git = (
        normalized_task_for_planner.get("git")
        if isinstance(normalized_task_for_planner.get("git"), dict)
        else {}
    )

    pr_selector = str(task_inputs.get("pr") or "").strip()
    branch_selector = str(
        task_git.get("startingBranch")
        or normalized_task_for_planner.get("startingBranch")
        or task_git.get("branch")
        or normalized_task_for_planner.get("branch")
        or task_inputs.get("startingBranch")
        or task_inputs.get("branch")
        or ""
    ).strip()
    if pr_selector or branch_selector:
        return

    raise _invalid_task_request(
        "pr-resolver task requires payload.task.instructions, payload.task.inputs.pr, "
        "or payload.task.git.startingBranch."
    )


def _derive_task_title(task_payload: dict[str, Any]) -> str | None:
    explicit = str(task_payload.get("title") or "").strip()
    if explicit:
        return explicit
    instructions = str(task_payload.get("instructions") or "").strip()
    if not instructions:
        return None
    first_line = instructions.splitlines()[0].strip()
    if not first_line:
        return None
    return first_line[:_MAX_TASK_TITLE_LENGTH]


def _derive_task_summary(
    task_payload: dict[str, Any], input_artifact_ref: str | None
) -> str:
    instructions = str(task_payload.get("instructions") or "").strip()
    if instructions:
        normalized = " ".join(instructions.split())
        if len(normalized) > _MAX_TASK_SUMMARY_LENGTH:
            preview_length = _MAX_TASK_SUMMARY_LENGTH - len(_TASK_SUMMARY_ELLIPSIS)
            return f"{normalized[:preview_length]}{_TASK_SUMMARY_ELLIPSIS}"
        return normalized
    if input_artifact_ref:
        return f"Task instructions stored in artifact {input_artifact_ref}."
    return "Execution initialized."


async def _create_execution_from_task_request(
    *,
    request: CreateJobRequest,
    service: TemporalExecutionService,
    user: User,
    session: Any = None,
) -> ExecutionModel | ScheduleCreatedResponse:
    if str(request.type).strip().lower() != "task":
        raise _invalid_task_request(
            "Only task-shaped submit requests can be mapped to Temporal executions."
        )

    payload = request.payload if isinstance(request.payload, dict) else {}
    task_payload = payload.get("task") if isinstance(payload.get("task"), dict) else {}
    if not task_payload:
        raise _invalid_task_request(
            "Task-shaped Temporal submit requests require payload.task."
        )

    # --- Schedule routing ---
    schedule: ScheduleParameters | None = None
    raw_schedule = getattr(request, "schedule", None) or payload.get("schedule")
    if isinstance(raw_schedule, dict):
        schedule = ScheduleParameters.model_validate(raw_schedule)
    elif isinstance(raw_schedule, ScheduleParameters):
        schedule = raw_schedule

    route = await _resolve_schedule_routing(
        schedule,
        request_payload=payload,
        user=user,
        session=session,
    )
    if route.recurring_response is not None:
        return route.recurring_response

    start_delay = route.start_delay
    scheduled_for_dt = route.scheduled_for

    required_capabilities = _coerce_string_list(
        payload.get("requiredCapabilities"),
        field_name="payload.requiredCapabilities",
    )

    if "dependsOn" in task_payload:
        depends_on_source = task_payload.get("dependsOn")
        field_name = "payload.task.dependsOn"
    else:
        depends_on_source = payload.get("dependsOn")
        field_name = "payload.dependsOn"

    raw_depends_on = _coerce_string_list(
        depends_on_source,
        field_name=field_name
    )

    depends_on = list(dict.fromkeys(d.strip() for d in raw_depends_on if d.strip()))

    if len(depends_on) > 10:
        raise _invalid_task_request(f"{field_name} can have a maximum of 10 items.")
    step_count = _coerce_step_count(task_payload.get("steps"))

    repository = str(payload.get("repository") or "").strip() or None
    integration = (
        str(
            payload.get("integration")
            or (payload.get("metadata") or {}).get("integration")
            or ""
        ).strip()
        or None
    )
    input_artifact_ref = _coerce_artifact_ref(
        task_payload.get("inputArtifactRef") or payload.get("inputArtifactRef")
    )
    plan_artifact_ref = _coerce_artifact_ref(
        task_payload.get("planArtifactRef") or payload.get("planArtifactRef")
    )
    manifest_artifact_ref = _coerce_artifact_ref(
        task_payload.get("manifestArtifactRef") or payload.get("manifestArtifactRef")
    )
    runtime_payload = (
        task_payload.get("runtime")
        if isinstance(task_payload.get("runtime"), dict)
        else {}
    )
    normalized_tool = _normalize_task_tool(task_payload)
    normalized_task_for_planner: dict[str, Any] = {}
    instructions = str(task_payload.get("instructions") or "").strip()
    if depends_on:
        normalized_task_for_planner["dependsOn"] = depends_on
    if instructions:
        normalized_task_for_planner["instructions"] = instructions
    if normalized_tool is not None:
        normalized_task_for_planner["tool"] = normalized_tool
        # Keep legacy shape for compatibility while tool is canonical.
        normalized_task_for_planner["skill"] = {
            "name": normalized_tool["name"],
            "version": normalized_tool["version"],
        }
        if isinstance(normalized_tool.get("inputs"), dict):
            normalized_task_for_planner["inputs"] = dict(normalized_tool["inputs"])
    if isinstance(task_payload.get("inputs"), dict):
        normalized_task_for_planner["inputs"] = dict(task_payload["inputs"])
    if runtime_payload:
        normalized_task_for_planner["runtime"] = dict(runtime_payload)
    git_payload = (
        task_payload.get("git") if isinstance(task_payload.get("git"), dict) else {}
    )
    if git_payload:
        normalized_git_payload: dict[str, str] = {}
        for git_key in ("startingBranch", "targetBranch", "branch"):
            git_value = git_payload.get(git_key)
            if isinstance(git_value, str) and git_value.strip():
                normalized_git_payload[git_key] = git_value.strip()
        if normalized_git_payload:
            normalized_task_for_planner["git"] = normalized_git_payload
    for key in ("repoRef", "startingBranch", "targetBranch", "branch"):
        value = task_payload.get(key)
        if isinstance(value, str) and value.strip():
            normalized_task_for_planner[key] = value.strip()

    _validate_task_runtime_requirements(
        task_payload=task_payload,
        normalized_tool=normalized_tool,
        normalized_task_for_planner=normalized_task_for_planner,
    )

    initial_parameters = {
        "requestType": request.type,
        "repository": repository,
        "requiredCapabilities": required_capabilities,
        "priority": request.priority,
        "maxAttempts": request.max_attempts,
        "targetRuntime": payload.get("targetRuntime") or runtime_payload.get("mode"),
        "model": runtime_payload.get("model"),
        "effort": runtime_payload.get("effort"),
        "publishMode": ((task_payload.get("publish") or {}).get("mode")),
        "proposeTasks": _coerce_bool(task_payload.get("proposeTasks"), default=False),
        "stepCount": step_count,
    }
    if instructions:
        initial_parameters["instructions"] = instructions
    if normalized_task_for_planner:
        initial_parameters["task"] = normalized_task_for_planner

    try:
        record = await service.create_execution(
            workflow_type="MoonMind.Run",
            owner_id=user.id,
            title=_derive_task_title(task_payload),
            input_artifact_ref=input_artifact_ref,
            plan_artifact_ref=plan_artifact_ref,
            manifest_artifact_ref=manifest_artifact_ref,
            failure_policy=None,
            initial_parameters=initial_parameters,
            idempotency_key=task_payload.get("idempotencyKey")
            or payload.get("idempotencyKey"),
            repository=repository,
            integration=integration,
            summary=_derive_task_summary(task_payload, input_artifact_ref),
            start_delay=start_delay,
            scheduled_for=scheduled_for_dt,
        )
    except TemporalExecutionValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "invalid_execution_request",
                "message": str(exc),
            },
        ) from exc

    return _serialize_execution(record)


async def _create_execution_from_manifest_request(
    *,
    request: CreateJobRequest,
    service: TemporalExecutionService,
    user: User,
) -> ExecutionModel:
    if str(request.type).strip().lower() != "manifest":
        raise _invalid_task_request(
            "Only manifest-shaped submit requests can be mapped to Temporal manifest executions."
        )

    payload = request.payload if isinstance(request.payload, dict) else {}
    manifest_payload = (
        payload.get("manifest") if isinstance(payload.get("manifest"), dict) else {}
    )
    if not manifest_payload:
        raise _invalid_task_request(
            "Manifest-shaped Temporal submit requests require payload.manifest."
        )

    name = str(manifest_payload.get("name", "inline")).strip()
    action = str(manifest_payload.get("action", "run")).strip()
    options = manifest_payload.get("options", {})
    idempotency_key = str(payload.get("idempotencyKey") or "").strip() or None

    try:
        record = await service.create_execution(
            workflow_type="MoonMind.ManifestIngest",
            owner_id=user.id,
            title=f"Manifest: {name}",
            summary=f"Manifest execution for {name} ({action})",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={
                "manifestName": name,
                "action": action,
                "options": options,
                "systemPayload": {"manifest": manifest_payload},
            },
            idempotency_key=idempotency_key,
        )
    except TemporalExecutionValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "invalid_execution_request",
                "message": str(exc),
            },
        ) from exc

    return _serialize_execution(record)


async def _get_owned_execution(
    *,
    service: TemporalExecutionService,
    workflow_id: str,
    user: User,
    include_orphaned_projection: bool = False,
):
    try:
        record = await service.describe_execution(
            workflow_id,
            include_orphaned=include_orphaned_projection,
        )
    except TemporalExecutionNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "execution_not_found",
                "message": str(exc),
            },
        ) from exc

    if _is_execution_admin(user):
        return record

    search_attributes = dict(getattr(record, "search_attributes", None) or {})
    record_owner_type = _enum_value(getattr(record, "owner_type", None))
    if record_owner_type is None:
        record_owner_type = _normalize_owner_type(
            record, search_attributes
        )
    record_owner_id = str(getattr(record, "owner_id", "") or "").strip()
    if not record_owner_id:
        record_owner_id = _coerce_temporal_scalar(search_attributes.get("mm_owner_id"))

    if record_owner_type != "user" or record_owner_id != _owner_id(user):
        # Fallback to parent workflow ownership for child workflows missing search_attributes
        if not record_owner_id:
            parent_id = None
            if ":agent:" in workflow_id:
                parent_id = workflow_id.split(":agent:")[0]
            else:
                parts = workflow_id.split(":")
                if workflow_id.startswith("mm:") and len(parts) >= 2:
                    parent_id = f"{parts[0]}:{parts[1]}"
                elif len(parts) >= 1:
                    parent_id = parts[0]
            
            if parent_id and parent_id != workflow_id:
                try:
                    parent_record = await service.describe_execution(
                        parent_id,
                        include_orphaned=include_orphaned_projection,
                    )
                    parent_attrs = dict(getattr(parent_record, "search_attributes", None) or {})
                    p_type = _enum_value(getattr(parent_record, "owner_type", None))
                    if p_type is None:
                        p_type = _normalize_owner_type(parent_record, parent_attrs)
                    p_id = str(getattr(parent_record, "owner_id", "") or "").strip()
                    if not p_id:
                        p_id = _coerce_temporal_scalar(parent_attrs.get("mm_owner_id"))
                    
                    if p_type == "user" and p_id == _owner_id(user):
                        return record
                except Exception:
                    pass

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "execution_not_found",
                "message": f"Workflow execution {workflow_id} was not found",
            },
        )

    return record


def _compute_schedule_delay(
    scheduled_for: datetime,
) -> timedelta:
    """Return a positive timedelta from *now* to *scheduled_for*.

    Raises ``HTTPException`` if the target is in the past or negative.
    """
    now = datetime.now(UTC)
    delay = scheduled_for - now
    if delay.total_seconds() < 1:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "schedule_in_past",
                "message": "scheduledFor must be a future datetime.",
            },
        )
    return delay


def _build_recurring_target(request_payload: dict[str, Any]) -> dict[str, Any]:
    """Transform a task request payload into a RecurringTasksService target.

    Constructs the ``kind=queue_task`` envelope expected by
    ``RecurringTasksService.create_definition()``.
    """
    return {
        "kind": "queue_task",
        "job": {
            "type": "task",
            "payload": request_payload,
        },
    }


async def _handle_recurring_schedule(
    *,
    schedule: ScheduleParameters,
    request_payload: dict[str, Any],
    user: User,
    session: Any,
) -> ScheduleCreatedResponse:
    """Delegate recurring schedule creation to RecurringTasksService."""
    from api_service.services.recurring_tasks_service import RecurringTasksService

    svc = RecurringTasksService(session)
    target = _build_recurring_target(request_payload)
    definition = await svc.create_definition(
        name=schedule.name or "Inline schedule",
        description=None,
        enabled=True,
        schedule_type="cron",
        cron=schedule.cron,
        timezone=schedule.timezone or "UTC",
        scope_type="personal",
        scope_ref=None,
        owner_user_id=user.id,
        target=target,
        policy=None,
    )
    return ScheduleCreatedResponse(
        definitionId=str(definition.id),
        cron=definition.cron,
        nextRunAt=definition.next_run_at,
        redirectPath=f"/tasks/schedules/{definition.id}",
    )


class _ScheduleRouteResult:
    """Result of ``_resolve_schedule_routing``."""

    __slots__ = ("start_delay", "scheduled_for", "recurring_response")

    def __init__(
        self,
        *,
        start_delay: timedelta | None = None,
        scheduled_for: datetime | None = None,
        recurring_response: ScheduleCreatedResponse | None = None,
    ) -> None:
        self.start_delay = start_delay
        self.scheduled_for = scheduled_for
        self.recurring_response = recurring_response


async def _resolve_schedule_routing(
    schedule: ScheduleParameters | None,
    *,
    request_payload: dict[str, Any],
    user: User,
    session: Any | None,
) -> _ScheduleRouteResult:
    """Shared schedule routing for both Temporal and task-shaped requests.

    Returns a ``_ScheduleRouteResult`` with either:
    * ``recurring_response`` populated (caller should return it),
    * ``start_delay`` / ``scheduled_for`` populated (caller passes to service), or
    * all ``None`` (immediate execution, no schedule).
    """
    if schedule is None:
        return _ScheduleRouteResult()

    if schedule.mode == "recurring":
        if session is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={
                    "code": "invalid_execution_request",
                    "message": "Recurring schedules require a database session.",
                },
            )
        response = await _handle_recurring_schedule(
            schedule=schedule,
            request_payload=request_payload,
            user=user,
            session=session,
        )
        return _ScheduleRouteResult(recurring_response=response)

    if schedule.mode == "once":
        if schedule.scheduled_for is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={
                    "code": "invalid_execution_request",
                    "message": "scheduledFor is required when schedule.mode is 'once'.",
                },
            )
        scheduled_for_dt = schedule.scheduled_for
        delay = _compute_schedule_delay(scheduled_for_dt)
        return _ScheduleRouteResult(
            start_delay=delay,
            scheduled_for=scheduled_for_dt,
        )

    return _ScheduleRouteResult()


@router.post("", response_model=ExecutionModel | ScheduleCreatedResponse, status_code=status.HTTP_201_CREATED)
async def create_execution(
    payload: dict[str, Any] = Body(...),
    service: TemporalExecutionService = Depends(_get_service),
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(get_current_user()),
    _submit_enabled: None = Depends(_ensure_submit_enabled),
) -> ExecutionModel | ScheduleCreatedResponse:
    from moonmind.config.settings import settings

    if not settings.temporal_dashboard.submit_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "temporal_submit_disabled",
                "message": "Temporal task submission is disabled (temporal_dashboard.submit_enabled=False). "
                "The legacy queue execution substrate is no longer supported. "
                "Enable Temporal submission to proceed.",
            },
        )

    try:
        if "type" in payload and "payload" in payload:
            request = CreateJobRequest.model_validate(payload)
            return await _create_execution_from_task_request(
                request=request,
                service=service,
                user=user,
                session=session,
            )

        request = CreateExecutionRequest.model_validate(payload)

        # --- Schedule routing ---
        route = await _resolve_schedule_routing(
            request.schedule,
            request_payload=payload,
            user=user,
            session=session,
        )
        if route.recurring_response is not None:
            return route.recurring_response

        record = await service.create_execution(
            workflow_type=request.workflow_type,
            owner_id=user.id,
            owner_type="user",
            title=request.title,
            input_artifact_ref=request.input_artifact_ref,
            plan_artifact_ref=request.plan_artifact_ref,
            manifest_artifact_ref=request.manifest_artifact_ref,
            failure_policy=request.failure_policy,
            initial_parameters=request.initial_parameters,
            idempotency_key=request.idempotency_key,
            start_delay=route.start_delay,
            scheduled_for=route.scheduled_for,
        )
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "invalid_execution_request",
                "message": str(exc),
            },
        ) from exc
    except TemporalExecutionValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "invalid_execution_request",
                "message": str(exc),
            },
        ) from exc

    return _serialize_execution(record)


@router.get("", response_model=ExecutionListResponse)
async def list_executions(
    *,
    workflow_type: Optional[str] = Query(None, alias="workflowType"),
    owner_type: Optional[str] = Query(None, alias="ownerType"),
    state: Optional[str] = Query(None, alias="state"),
    owner_id: Optional[str] = Query(None, alias="ownerId"),
    entry: Optional[str] = Query(None, alias="entry"),
    repo: Optional[str] = Query(None, alias="repo"),
    integration: Optional[str] = Query(None, alias="integration"),
    page_size: int = Query(50, alias="pageSize", ge=1, le=200),
    next_page_token: Optional[str] = Query(None, alias="nextPageToken"),
    source: Optional[str] = Query(None),
    service: TemporalExecutionService = Depends(_get_service),
    user: User = Depends(get_current_user()),
    session: AsyncSession = Depends(get_async_session),
    temporal_client: Client = Depends(get_temporal_client),
) -> ExecutionListResponse:
    if _is_execution_admin(user):
        effective_owner_type = owner_type
        effective_owner = owner_id
    else:
        normalized_owner_type = str(owner_type or "").strip().lower()
        if owner_type is not None and owner_type != "user":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "execution_forbidden",
                    "message": "Cannot list non-user executions.",
                },
            )
        if owner_id is not None and owner_id != _owner_id(user):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "execution_forbidden",
                    "message": "Cannot list executions for another user.",
                },
            )
        effective_owner = _owner_id(user)
        effective_owner_type = "user" if normalized_owner_type == "user" else None

    if source == "temporal":
        try:
            from api_service.core.sync import (
                map_temporal_state_to_projection,
                merged_parameters_for_projection,
            )

            client = temporal_client

            def escape_val(v: str) -> str:
                return v.replace('"', '\\"')

            query_parts = []
            if workflow_type:
                query_parts.append(f'WorkflowType="{escape_val(workflow_type)}"')
            if state:
                query_parts.append(f'mm_state="{escape_val(state)}"')
            if entry:
                query_parts.append(f'mm_entry="{escape_val(entry)}"')
            if effective_owner_type:
                query_parts.append(
                    f'mm_owner_type="{escape_val(effective_owner_type)}"'
                )
            if effective_owner:
                query_parts.append(f'mm_owner_id="{escape_val(effective_owner)}"')
            if repo:
                query_parts.append(f'mm_repo="{escape_val(repo)}"')
            if integration:
                query_parts.append(f'mm_integration="{escape_val(integration)}"')

            query_str = " AND ".join(query_parts) if query_parts else ""

            import base64
            token_bytes = base64.b64decode(next_page_token) if next_page_token else None

            count_info = await client.count_workflows(query=query_str)

            iterator = client.list_workflows(
                query=query_str,
                page_size=page_size,
                next_page_token=token_bytes,
            )
            await iterator.fetch_next_page()

            page = iterator.current_page or []
            canonical_map: dict[str, TemporalExecutionCanonicalRecord] = {}
            if page:
                workflow_ids = [wf.id for wf in page]
                stmt = select(TemporalExecutionCanonicalRecord).where(
                    TemporalExecutionCanonicalRecord.workflow_id.in_(workflow_ids)
                )
                canonical_rows = (await session.execute(stmt)).scalars().all()
                canonical_map = {row.workflow_id: row for row in canonical_rows}

            items = []
            if page:
                for wf in page:
                    payload = await map_temporal_state_to_projection(wf)
                    payload["parameters"] = merged_parameters_for_projection(
                        payload, canonical_map.get(wf.id)
                    )
                    # We need a record-like object for serialization
                    from types import SimpleNamespace

                    record_obj = SimpleNamespace(**payload)
                    if not hasattr(record_obj, "updated_at"):
                        record_obj.updated_at = datetime.now(UTC)
                    items.append(
                        _serialize_execution(record_obj, include_artifact_refs=False)
                    )

            new_token_str = None
            if iterator.next_page_token:
                new_token_str = base64.b64encode(iterator.next_page_token).decode("utf-8")

            return ExecutionListResponse(
                items=items,
                next_page_token=new_token_str,
                count=count_info.count,
                count_mode="exact",
                degraded_count=False,
                refreshed_at=datetime.now(UTC),
            )
        except RPCError as exc:
            logger.warning(
                "Failed to list Temporal executions directly: %s", exc, exc_info=True
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "temporal_unavailable",
                    "message": "Temporal service unavailable.",
                },
            ) from exc

    try:
        result = await service.list_executions(
            workflow_type=workflow_type,
            state=state,
            entry=entry,
            owner_type=effective_owner_type,
            owner_id=effective_owner,
            repo=repo,
            integration=integration,
            page_size=page_size,
            next_page_token=next_page_token,
        )

        if settings.temporal.temporal_authoritative_read_enabled and result.items:
            from api_service.core.sync import sync_temporal_executions_safely

            try:
                client = temporal_client
                result.items = await sync_temporal_executions_safely(
                    session, result.items, client
                )
            except Exception as exc:
                logger.warning(
                    "Failed to sync executions from Temporal: %s", exc, exc_info=True
                )

    except TemporalExecutionValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "invalid_execution_query",
                "message": str(exc),
            },
        ) from exc

    return ExecutionListResponse(
        items=[
            _serialize_execution(item, include_artifact_refs=False)
            for item in result.items
        ],
        next_page_token=result.next_page_token,
        count=result.count,
        count_mode="exact",
        degraded_count=False,
        refreshed_at=max(
            (_compatibility_refreshed_at(item) for item in result.items),
            default=None,
        ),
    )


@router.get("/{workflow_id}", response_model=ExecutionModel)
async def describe_execution(
    workflow_id: str,
    response: Response,
    source: Optional[str] = Query(None),
    service: TemporalExecutionService = Depends(_get_service),
    user: User = Depends(get_current_user()),
    session: AsyncSession = Depends(get_async_session),
    temporal_client: Client = Depends(get_temporal_client),
) -> ExecutionModel:
    canonical_workflow_id, alias_used = _canonicalize_execution_identifier(workflow_id)

    from api_service.core.sync import fetch_and_sync_execution

    source_is_temporal = source == "temporal"
    use_projection_read = source_is_temporal
    temporal_sync_unavailable = False

    if settings.temporal.temporal_authoritative_read_enabled or source_is_temporal:
        try:
            client = temporal_client
            await fetch_and_sync_execution(session, canonical_workflow_id, client)
            await session.commit()
            # Return the synced projection to avoid clobbering it with stale source data.
            use_projection_read = True
        except RPCError as exc:
            temporal_sync_unavailable = True
            logger.warning(
                "Failed to sync execution %s from Temporal: %s",
                canonical_workflow_id,
                exc,
                exc_info=True,
            )
        except Exception as exc:
            logger.warning(
                "Failed to sync execution %s from Temporal: %s",
                canonical_workflow_id,
                exc,
                exc_info=True,
            )

    try:
        record = await _get_owned_execution(
            service=service,
            workflow_id=workflow_id,
            user=user,
            include_orphaned_projection=use_projection_read,
        )
    except HTTPException as exc:
        if (
            source_is_temporal
            and temporal_sync_unavailable
            and exc.status_code == status.HTTP_404_NOT_FOUND
        ):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "temporal_unavailable",
                    "message": "Temporal service unavailable.",
                },
            ) from exc
        raise
    if alias_used:
        _mark_execution_alias_usage(
            response,
            raw_identifier=workflow_id,
            canonical_identifier=canonical_workflow_id,
        )
    return _serialize_execution(record)


@router.post(
    "/{workflow_id}/update",
    response_model=UpdateExecutionResponse,
    response_model_exclude_none=True,
)
async def update_execution(
    workflow_id: str,
    payload: UpdateExecutionRequest,
    response: Response,
    service: TemporalExecutionService = Depends(_get_service),
    user: User = Depends(get_current_user()),
    _actions_enabled: None = Depends(_ensure_actions_enabled),
) -> UpdateExecutionResponse:
    record = await _get_owned_execution(
        service=service,
        workflow_id=workflow_id,
        user=user,
    )

    try:
        update_result = await service.update_execution(
            workflow_id=workflow_id,
            update_name=payload.update_name,
            input_artifact_ref=payload.input_artifact_ref,
            plan_artifact_ref=payload.plan_artifact_ref,
            parameters_patch=payload.parameters_patch,
            title=payload.title,
            new_manifest_artifact_ref=payload.new_manifest_artifact_ref,
            mode=payload.mode,
            max_concurrency=payload.max_concurrency,
            node_ids=payload.node_ids,
            idempotency_key=payload.idempotency_key,
        )
    except TemporalExecutionValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "invalid_update_request",
                "message": str(exc),
            },
        ) from exc

    refreshed_record = await service.describe_execution(record.workflow_id)
    canonical_workflow_id, alias_used = _canonicalize_execution_identifier(workflow_id)
    if alias_used:
        _mark_execution_alias_usage(
            response,
            raw_identifier=workflow_id,
            canonical_identifier=canonical_workflow_id,
        )

    return UpdateExecutionResponse(
        **update_result,
        execution=_serialize_execution(refreshed_record),
        refresh=ExecutionRefreshEnvelope(
            patched_execution=True,
            list_stale=True,
            refetch_suggested=True,
            refreshed_at=_compatibility_refreshed_at(refreshed_record),
        ),
    )


@router.get(
    "/{workflow_id}/manifest-status",
    response_model=ManifestStatusSnapshotModel,
)
async def describe_manifest_status(
    workflow_id: str,
    service: TemporalExecutionService = Depends(_get_service),
    user: User = Depends(get_current_user()),
) -> ManifestStatusSnapshotModel:
    await _get_owned_execution(service=service, workflow_id=workflow_id, user=user)
    try:
        return await service.describe_manifest_status(workflow_id)
    except TemporalExecutionValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "invalid_manifest_status_request",
                "message": str(exc),
            },
        ) from exc


@router.get(
    "/{workflow_id}/manifest-nodes",
    response_model=ManifestNodePageModel,
)
async def list_manifest_node_page(
    workflow_id: str,
    state: Optional[str] = Query(None, alias="state"),
    cursor: Optional[str] = Query(None, alias="cursor"),
    limit: int = Query(50, alias="limit", ge=1, le=200),
    service: TemporalExecutionService = Depends(_get_service),
    user: User = Depends(get_current_user()),
) -> ManifestNodePageModel:
    await _get_owned_execution(service=service, workflow_id=workflow_id, user=user)
    try:
        return await service.list_manifest_nodes(
            workflow_id,
            state=state,
            cursor=cursor,
            limit=limit,
        )
    except TemporalExecutionValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "invalid_manifest_nodes_request",
                "message": str(exc),
            },
        ) from exc


@router.post(
    "/{workflow_id}/integration",
    response_model=ExecutionModel,
    status_code=status.HTTP_202_ACCEPTED,
)
async def configure_integration_monitoring(
    workflow_id: str,
    payload: ConfigureIntegrationMonitoringRequest,
    service: TemporalExecutionService = Depends(_get_service),
    user: User = Depends(get_current_user()),
) -> ExecutionModel:
    await _get_owned_execution(service=service, workflow_id=workflow_id, user=user)

    try:
        record = await service.configure_integration_monitoring(
            workflow_id=workflow_id,
            integration_name=payload.integration_name,
            correlation_id=payload.correlation_id,
            external_operation_id=payload.external_operation_id,
            normalized_status=payload.normalized_status,
            provider_status=payload.provider_status,
            callback_supported=payload.callback_supported,
            callback_correlation_key=payload.callback_correlation_key,
            recommended_poll_seconds=payload.recommended_poll_seconds,
            external_url=payload.external_url,
            provider_summary=payload.provider_summary,
            result_refs=payload.result_refs,
        )
    except TemporalExecutionValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "invalid_integration_monitoring_request",
                "message": str(exc),
            },
        ) from exc

    return _serialize_execution(record)


@router.post(
    "/{workflow_id}/integration/poll",
    response_model=ExecutionModel,
    status_code=status.HTTP_202_ACCEPTED,
)
async def record_integration_poll(
    workflow_id: str,
    payload: PollIntegrationRequest,
    service: TemporalExecutionService = Depends(_get_service),
    user: User = Depends(get_current_user()),
) -> ExecutionModel:
    await _get_owned_execution(service=service, workflow_id=workflow_id, user=user)

    try:
        record = await service.record_integration_poll(
            workflow_id=workflow_id,
            normalized_status=payload.normalized_status,
            provider_status=payload.provider_status,
            observed_at=payload.observed_at,
            recommended_poll_seconds=payload.recommended_poll_seconds,
            external_url=payload.external_url,
            provider_summary=payload.provider_summary,
            result_refs=payload.result_refs,
            completed_wait_cycles=payload.completed_wait_cycles,
        )
    except TemporalExecutionValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "invalid_integration_poll_request",
                "message": str(exc),
            },
        ) from exc

    return _serialize_execution(record)


@router.post(
    "/{workflow_id}/signal",
    response_model=ExecutionModel,
    status_code=status.HTTP_202_ACCEPTED,
)
async def signal_execution(
    workflow_id: str,
    payload: SignalExecutionRequest,
    response: Response,
    service: TemporalExecutionService = Depends(_get_service),
    user: User = Depends(get_current_user()),
    _actions_enabled: None = Depends(_ensure_actions_enabled),
) -> ExecutionModel:
    await _get_owned_execution(service=service, workflow_id=workflow_id, user=user)

    try:
        record = await service.signal_execution(
            workflow_id=workflow_id,
            signal_name=payload.signal_name,
            payload=payload.payload,
            payload_artifact_ref=payload.payload_artifact_ref,
        )
    except TemporalExecutionValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "signal_rejected",
                "message": str(exc),
            },
        ) from exc

    canonical_workflow_id, alias_used = _canonicalize_execution_identifier(workflow_id)
    if alias_used:
        _mark_execution_alias_usage(
            response,
            raw_identifier=workflow_id,
            canonical_identifier=canonical_workflow_id,
        )
    return _serialize_execution(record)


@router.post(
    "/{workflow_id}/cancel",
    response_model=ExecutionModel,
    status_code=status.HTTP_202_ACCEPTED,
)
async def cancel_execution(
    workflow_id: str,
    response: Response,
    payload: CancelExecutionRequest | None = None,
    service: TemporalExecutionService = Depends(_get_service),
    user: User = Depends(get_current_user()),
    _actions_enabled: None = Depends(_ensure_actions_enabled),
) -> ExecutionModel:
    await _get_owned_execution(service=service, workflow_id=workflow_id, user=user)

    request = payload or CancelExecutionRequest()
    record = await service.cancel_execution(
        workflow_id=workflow_id,
        reason=request.reason,
        graceful=request.graceful,
    )
    canonical_workflow_id, alias_used = _canonicalize_execution_identifier(workflow_id)
    if alias_used:
        _mark_execution_alias_usage(
            response,
            raw_identifier=workflow_id,
            canonical_identifier=canonical_workflow_id,
        )
    return _serialize_execution(record)


@router.post(
    "/{workflow_id}/reschedule",
    response_model=ExecutionModel,
    status_code=status.HTTP_202_ACCEPTED,
)
async def reschedule_execution(
    workflow_id: str,
    payload: RescheduleExecutionRequest,
    response: Response,
    service: TemporalExecutionService = Depends(_get_service),
    user: User = Depends(get_current_user()),
    _actions_enabled: None = Depends(_ensure_actions_enabled),
) -> ExecutionModel:
    record = await _get_owned_execution(service=service, workflow_id=workflow_id, user=user)

    if record.state != MoonMindWorkflowState.SCHEDULED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "reschedule_rejected",
                "message": f"Cannot reschedule workflow in state {record.state.value}",
            },
        )
    
    try:
        await get_temporal_client_adapter().send_reschedule_signal(
            record.workflow_id, payload.scheduled_for
        )
    except Exception as exc:
        logger.warning("Failed to send reschedule signal for workflow %s", record.workflow_id, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "signal_failed",
                "message": "Failed to send reschedule signal to Temporal.",
            },
        ) from exc

    canonical_workflow_id, alias_used = _canonicalize_execution_identifier(workflow_id)
    if alias_used:
        _mark_execution_alias_usage(
            response,
            raw_identifier=workflow_id,
            canonical_identifier=canonical_workflow_id,
        )
    
    # We shouldn't rely strictly on describing the Execution immediately because Temporal signal is async.
    # But let's return the updated record locally updated for the user.
    if isinstance(record, TemporalExecutionRecord):
        record.scheduled_for = payload.scheduled_for
        await service._session.commit()
        await service._session.refresh(record)

    return _serialize_execution(record)


@router.post(
    "/{workflow_id}/rerun",
    response_model=ExecutionModel,
    status_code=status.HTTP_201_CREATED,
)
async def rerun_execution(
    workflow_id: str,
    response: Response,
    service: TemporalExecutionService = Depends(_get_service),
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(get_current_user()),
    _submit_enabled: None = Depends(_ensure_submit_enabled),
) -> ExecutionModel:
    """Rerun an existing failed/completed workflow with the same parameters.

    This endpoint fetches the original execution's parameters and creates
    a new workflow execution with identical settings.
    """
    from uuid import uuid4 as _uuid4

    # Fetch the original execution
    original = await _get_owned_execution(service=service, workflow_id=workflow_id, user=user)

    # Fetch the canonical record to get full initial_parameters
    canonical = await session.get(TemporalExecutionCanonicalRecord, workflow_id)

    if canonical is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "canonical_record_not_found",
                "message": f"Canonical record not found for workflow {workflow_id}. Cannot rerun.",
            },
        )

    # Use canonical parameters which have targetRuntime, targetSkill, etc.
    initial_params = dict(canonical.parameters or {})

    # Generate a new idempotency key based on the original workflow ID
    new_idempotency_key = f"rerun:{workflow_id}:{_uuid4()}"

    try:
        record = await service.create_execution(
            workflow_type=canonical.workflow_type.value,
            owner_id=canonical.owner_id or user.id,
            owner_type=canonical.owner_type.value if canonical.owner_type else "user",
            title=canonical.memo.get("title") if canonical.memo else None,
            input_artifact_ref=canonical.input_ref,
            plan_artifact_ref=canonical.plan_ref,
            manifest_artifact_ref=canonical.manifest_ref,
            failure_policy=None,
            initial_parameters=initial_params,
            idempotency_key=new_idempotency_key,
            repository=None,
            integration=None,
        )
    except TemporalExecutionValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "rerun_validation_failed",
                "message": str(exc),
            },
        ) from exc

    canonical_workflow_id, alias_used = _canonicalize_execution_identifier(workflow_id)
    if alias_used:
        _mark_execution_alias_usage(
            response,
            raw_identifier=workflow_id,
            canonical_identifier=canonical_workflow_id,
        )

    return _serialize_execution(record)


__all__ = ["router"]
