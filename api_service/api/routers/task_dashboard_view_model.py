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
    "queue": {
        "queued": "queued",
        "pending": "queued",
        "running": "running",
        "succeeded": "succeeded",
        "success": "succeeded",
        "completed": "succeeded",
        "failed": "failed",
        "error": "failed",
        "cancelled": "cancelled",
        "dead_letter": "failed",
    },
    "orchestrator": {
        "pending": "queued",
        "running": "running",
        "awaiting_approval": "awaiting_action",
        "succeeded": "succeeded",
        "rolled_back": "succeeded",
        "failed": "failed",
    },
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
    "external": {
        "queued": "queued",
        "running": "running",
        "completed": "succeeded",
        "succeeded": "succeeded",
        "failed": "failed",
        "cancelled": "cancelled",
        "canceled": "cancelled",
        "timed_out": "failed",
        "awaiting_callback": "awaiting_action",
        "intervention_requested": "awaiting_action",
        "unknown": "queued",
    },
}


def normalize_status(source: str, raw_status: str | None) -> str:
    """Normalize source-specific status values into dashboard display states."""

    source_key = source.strip().lower()
    status_key = (raw_status or "").strip().lower()

    mapping = _STATUS_MAPS.get(source_key)
    if mapping is None:
        return "queued"

    if status_key in mapping:
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
            "queue": {
                "list": "/api/tasks",
                "create": "/api/queue/jobs",
                "createWithAttachments": "/api/queue/jobs/with-attachments",
                "update": "/api/queue/jobs/{id}",
                "resubmit": "/api/queue/jobs/{id}/resubmit",
                "detail": "/api/queue/jobs/{id}",
                "cancel": "/api/queue/jobs/{id}/cancel",
                "events": "/api/queue/jobs/{id}/events",
                "eventsStream": "/api/queue/jobs/{id}/events/stream",
                "artifacts": "/api/queue/jobs/{id}/artifacts",
                "artifactDownload": "/api/queue/jobs/{id}/artifacts/{artifactId}/download",
                "attachments": "/api/queue/jobs/{id}/attachments",
                "attachmentDownload": "/api/queue/jobs/{id}/attachments/{attachmentId}/download",
                "migrationTelemetry": "/api/queue/telemetry/migration",
                "skills": "/api/tasks/skills",
                "runtimeCapabilities": "/api/queue/workers/runtime-capabilities",
                "liveSession": "/api/queue/jobs/{id}/live-session",
                "liveSessionGrantWrite": "/api/queue/jobs/{id}/live-session/grant-write",
                "liveSessionRevoke": "/api/queue/jobs/{id}/live-session/revoke",
                "taskControl": "/api/queue/jobs/{id}/control",
                "operatorMessages": "/api/queue/jobs/{id}/operator-messages",
                "taskStepTemplates": "/api/task-step-templates",
                "taskStepTemplateDetail": "/api/task-step-templates/{slug}",
                "taskStepTemplateExpand": "/api/task-step-templates/{slug}:expand",
                "taskStepTemplateSave": "/api/task-step-templates/save-from-task",
                "taskStepTemplateFavorite": "/api/task-step-templates/{slug}:favorite",
            },
            "orchestrator": {
                "list": "/orchestrator/tasks",
                "create": "/orchestrator/tasks",
                "detail": "/orchestrator/tasks/{id}",
                "artifacts": "/orchestrator/tasks/{id}/artifacts",
                "approve": "/orchestrator/tasks/{id}/approvals",
                "retry": "/orchestrator/tasks/{id}/retry",
            },
            "proposals": {
                "list": "/api/proposals",
                "detail": "/api/proposals/{id}",
                "promote": "/api/proposals/{id}/promote",
                "dismiss": "/api/proposals/{id}/dismiss",
                "priority": "/api/proposals/{id}/priority",
            },
            "manifests": {
                "list": "/api/queue/jobs?type=manifest&limit=200",
                "create": "/api/queue/jobs",
                "registry": "/api/manifests",
                "registryRun": "/api/manifests/{name}/runs",
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
            },
            "externalRuns": {
                "list": "/api/external-runs",
                "detail": "/api/external-runs/{workflowId}",
            },
        },
        "features": {
            "temporalDashboard": {
                "enabled": True,
                "listEnabled": True,
                "detailEnabled": True,
                "actionsEnabled": True,
                "submitEnabled": True,
                "debugFieldsEnabled": bool(temporal_dashboard.debug_fields_enabled),
            }
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
            "defaultProposeTasks": bool(settings.workflow.enable_task_proposals),
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
