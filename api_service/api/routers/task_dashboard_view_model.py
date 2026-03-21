"""Shared view-model helpers for the task dashboard UI."""

from __future__ import annotations

import os
from copy import deepcopy
from typing import Any

from moonmind.config.settings import settings
from moonmind.workflows.agent_queue.runtime_defaults import (
    DEFAULT_REPOSITORY,
    resolve_default_task_runtime,
    resolve_runtime_defaults,
)

_POLL_INTERVALS_MS = {
    "list": 5000,
    "detail": 2000,
    "events": 1000,
}

_SUPPORTED_WORKER_RUNTIMES = ("codex", "gemini_cli", "claude", "jules", "universal")

_STATUS_MAPS: dict[str, dict[str, str]] = {
    "proposals": {
        "open": "queued",
        "promoted": "succeeded",
        "dismissed": "cancelled",
        "accepted": "succeeded",
        "rejected": "failed",
    },
    "temporal": {
        "initializing": "queued",
        "planning": "running",
        "executing": "running",
        "awaiting_external": "awaiting_action",
        "finalizing": "running",
        "running": "running",
        "succeeded": "succeeded",
        "completed": "succeeded",
        "failed": "failed",
        # Accept both Temporal's raw status spelling and the normalized dashboard value.
        "canceled": "cancelled",
        "queued": "queued",
        "awaiting_action": "awaiting_action",
        "cancelled": "cancelled",
    },

}


def normalize_status(source: str, raw_status: str | None) -> str:
    """Normalize source-specific status values into dashboard display states."""

    source_key = source.strip().lower()
    status_key = (raw_status or "").strip().lower()

    # Prioritize Temporal states, allowing proposals to use their local mapping.
    map_key = "proposals" if source_key == "proposals" else "temporal"
    mapping = _STATUS_MAPS.get(map_key)
    
    if mapping and status_key in mapping:
        return mapping[status_key]

    # Fallback for unexpected values so the dashboard remains renderable.
    if "running" in status_key:
        return "running"
    if status_key in {"success", "completed", "done"}:
        return "succeeded"
    if status_key in {"error", "failed", "failure"}:
        return "failed"

    return "queued"


def status_maps() -> dict[str, dict[str, str]]:
    """Return a copy of status maps so callers can safely mutate local copies."""

    return deepcopy(_STATUS_MAPS)


def _build_supported_task_runtimes() -> list[str]:
    supported: list[str] = ["codex", "gemini_cli", "claude"]
    if settings.jules_runtime_gate.enabled:
        supported.append("jules")
    return supported


def _build_default_attachment_policy(config: "dict[str, Any]") -> dict[str, Any]:
    """Normalize attachment policy values for dashboard consumption."""

    max_count = max(1, int(config.get("agent_job_attachment_max_count", 1) or 1))
    max_bytes = max(
        1, int(config.get("agent_job_attachment_max_bytes", 10 * 1024 * 1024) or 1)
    )
    total_bytes = max(
        1, int(config.get("agent_job_attachment_total_bytes", 25 * 1024 * 1024) or 1)
    )
    allowed_types = tuple(
        config.get("agent_job_attachment_allowed_content_types") or ()
    )
    return {
        "maxCount": max_count,
        "maxBytes": max_bytes,
        "totalBytes": max(total_bytes, max_bytes),
        "allowedContentTypes": (
            list(allowed_types)
            if allowed_types
            else ["image/png", "image/jpeg", "image/webp"]
        ),
    }


def build_runtime_config(initial_path: str) -> dict[str, Any]:
    """Build runtime config consumed by dashboard JavaScript."""

    supported_task_runtimes = _build_supported_task_runtimes()
    temporal_dashboard = settings.temporal_dashboard
    configured_runtime = (
        str(os.environ.get("MOONMIND_WORKER_RUNTIME", "")).strip().lower()
    )
    if configured_runtime in supported_task_runtimes:
        default_task_runtime = configured_runtime
    else:
        configured_default = resolve_default_task_runtime(settings.workflow)
        if configured_default in supported_task_runtimes:
            default_task_runtime = configured_default
        else:
            default_task_runtime = supported_task_runtimes[0]
    default_task_model_by_runtime: dict[str, str] = {}
    default_task_effort_by_runtime: dict[str, str] = {}
    for runtime in supported_task_runtimes:
        default_model, default_effort = resolve_runtime_defaults(
            runtime,
            workflow_settings=settings.workflow,
        )
        if default_model:
            default_task_model_by_runtime[runtime] = default_model
        if default_effort:
            default_task_effort_by_runtime[runtime] = default_effort
    default_task_model = default_task_model_by_runtime.get(default_task_runtime, "")
    default_task_effort = default_task_effort_by_runtime.get(default_task_runtime, "")
    default_repository = (
        str(settings.workflow.github_repository or "").strip()
        or DEFAULT_REPOSITORY
    )
    default_publish_mode = (
        str(settings.workflow.default_publish_mode or "").strip().lower() or "pr"
    )

    return {
        "initialPath": initial_path,
        "pollIntervalsMs": {
            "list": _POLL_INTERVALS_MS["list"],
            "detail": _POLL_INTERVALS_MS["detail"],
            "events": _POLL_INTERVALS_MS["events"],
        },
        "statusMaps": status_maps(),
        "sources": {
            "proposals": {
                "list": "/api/proposals",
                "detail": "/api/proposals/{id}",
                "promote": "/api/proposals/{id}/promote",
                "dismiss": "/api/proposals/{id}/dismiss",
                "priority": "/api/proposals/{id}/priority",
            },
            "schedules": {
                "list": "/api/recurring-tasks?scope=personal",
                "create": "/api/recurring-tasks",
                "detail": "/api/recurring-tasks/{id}",
                "update": "/api/recurring-tasks/{id}",
                "runNow": "/api/recurring-tasks/{id}/run",
                "runs": "/api/recurring-tasks/{id}/runs?limit=200",
            },
            "temporal": {
                "list": temporal_dashboard.list_endpoint,
                "create": temporal_dashboard.create_endpoint,
                "detail": temporal_dashboard.detail_endpoint,
                "update": temporal_dashboard.update_endpoint,
                "manifestStatus": "/api/executions/{workflowId}/manifest-status",
                "manifestNodes": "/api/executions/{workflowId}/manifest-nodes",
                "signal": temporal_dashboard.signal_endpoint,
                "cancel": temporal_dashboard.cancel_endpoint,
                "artifacts": temporal_dashboard.artifacts_endpoint,
                "artifactCreate": temporal_dashboard.artifact_create_endpoint,
                "artifactMetadata": temporal_dashboard.artifact_metadata_endpoint,
                "artifactPresignDownload": temporal_dashboard.artifact_presign_download_endpoint,
                "artifactDownload": temporal_dashboard.artifact_download_endpoint,
                "liveSession": "/api/task-runs/{id}/live-session",
            },

        },
        "features": {
            "temporalDashboard": {
                "enabled": bool(temporal_dashboard.enabled),
                "listEnabled": bool(temporal_dashboard.list_enabled),
                "detailEnabled": bool(temporal_dashboard.detail_enabled),
                "actionsEnabled": bool(temporal_dashboard.actions_enabled),
                "submitEnabled": bool(temporal_dashboard.submit_enabled),
                "debugFieldsEnabled": bool(temporal_dashboard.debug_fields_enabled),
            },
            "logTailingEnabled": bool(
                os.environ.get("MOONMIND_LOG_TAILING_ENABLED", "true").strip().lower()
                not in ("0", "false", "no", "off")
            ),
        },
        "system": {
            "defaultQueue": "agent_jobs",
            "defaultRepository": default_repository,
            "defaultTaskRuntime": default_task_runtime,
            "defaultTaskModel": default_task_model,
            "defaultTaskEffort": default_task_effort,
            "defaultTaskModelByRuntime": default_task_model_by_runtime,
            "defaultTaskEffortByRuntime": default_task_effort_by_runtime,
            "defaultPublishMode": default_publish_mode,
            # Keep task proposals opt-in from the submit form so Temporal
            # remains the default execution substrate for new runs.
            "defaultProposeTasks": False,
            "queueEnv": "MOONMIND_QUEUE",
            "taskSourceResolver": "/api/tasks/{taskId}/source",
            "workerRuntimeEnv": "MOONMIND_WORKER_RUNTIME",
            "supportedTaskRuntimes": supported_task_runtimes,
            "supportedWorkerRuntimes": list(_SUPPORTED_WORKER_RUNTIMES),
            "taskTemplateCatalog": {
                "enabled": bool(settings.feature_flags.task_template_catalog_enabled),
                "templateSaveEnabled": bool(
                    settings.feature_flags.task_template_catalog_enabled
                ),
            },
            "workerPause": {
                "get": "/api/system/worker-pause",
                "post": "/api/system/worker-pause",
                "pollIntervalMs": 5000,
            },
            "authProfiles": {
                "list": "/api/v1/auth-profiles",
                "create": "/api/v1/auth-profiles",
                "detail": "/api/v1/auth-profiles/{profileId}",
                "update": "/api/v1/auth-profiles/{profileId}",
                "delete": "/api/v1/auth-profiles/{profileId}",
            },
            "attachmentPolicy": {
                "enabled": bool(settings.workflow.agent_job_attachment_enabled),
                **_build_default_attachment_policy(
                    {
                        "agent_job_attachment_max_count": settings.workflow.agent_job_attachment_max_count,
                        "agent_job_attachment_max_bytes": settings.workflow.agent_job_attachment_max_bytes,
                        "agent_job_attachment_total_bytes": settings.workflow.agent_job_attachment_total_bytes,
                        "agent_job_attachment_allowed_content_types": settings.workflow.agent_job_attachment_allowed_content_types,
                    }
                ),
            },
            "taskCompatibilityList": "/api/tasks/list",
            "taskCompatibilityDetail": "/api/tasks/{taskId}",
            "taskResolution": "/api/tasks/{taskId}/resolution",
            "temporalCompatibility": {
                "enabled": True,
                "uiQueryModel": "compatibility_adapter",
                "list": "/api/executions",
                "detail": "/api/executions/{workflowId}",
                "actionExecutionField": "execution",
                "actionRefreshField": "refresh",
                "staleStateField": "staleState",
                "refreshedAtField": "refreshedAt",
                "countModeField": "countMode",
                "degradedCountField": "degradedCount",
                "backgroundRefetchMs": _POLL_INTERVALS_MS["list"],
            },
        },
    }


__all__ = ["build_runtime_config", "normalize_status", "status_maps"]
