"""Shared view-model helpers for the task dashboard UI."""

from __future__ import annotations

import os
from copy import deepcopy
from typing import Any

from moonmind.config.settings import settings

_POLL_INTERVALS_MS = {
    "list": 5000,
    "detail": 2000,
    "events": 1000,
}

_SUPPORTED_WORKER_RUNTIMES = ("codex", "gemini", "claude", "universal")
_SUPPORTED_TASK_RUNTIMES = ("codex", "gemini", "claude")
_DEFAULT_TASK_RUNTIME = "codex"
_DEFAULT_CODEX_MODEL = "gpt-5.3-codex"
_DEFAULT_CODEX_EFFORT = "high"
_DEFAULT_REPOSITORY = "MoonLadderStudios/MoonMind"

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


def build_runtime_config(initial_path: str) -> dict[str, Any]:
    """Build runtime config consumed by dashboard JavaScript."""

    configured_runtime = (
        str(os.environ.get("MOONMIND_WORKER_RUNTIME", "")).strip().lower()
    )
    default_task_runtime = (
        configured_runtime
        if configured_runtime in _SUPPORTED_TASK_RUNTIMES
        else _DEFAULT_TASK_RUNTIME
    )
    codex_default_model = (
        str(settings.spec_workflow.codex_model or "").strip() or _DEFAULT_CODEX_MODEL
    )
    codex_default_effort = (
        str(settings.spec_workflow.codex_effort or "").strip() or _DEFAULT_CODEX_EFFORT
    )
    default_task_model_by_runtime = {"codex": codex_default_model}
    default_task_effort_by_runtime = {"codex": codex_default_effort}
    default_task_model = default_task_model_by_runtime.get(default_task_runtime, "")
    default_task_effort = default_task_effort_by_runtime.get(default_task_runtime, "")
    default_repository = (
        str(settings.spec_workflow.github_repository or "").strip()
        or _DEFAULT_REPOSITORY
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
                "list": "/api/queue/jobs",
                "create": "/api/queue/jobs",
                "detail": "/api/queue/jobs/{id}",
                "cancel": "/api/queue/jobs/{id}/cancel",
                "events": "/api/queue/jobs/{id}/events",
                "eventsStream": "/api/queue/jobs/{id}/events/stream",
                "artifacts": "/api/queue/jobs/{id}/artifacts",
                "artifactDownload": "/api/queue/jobs/{id}/artifacts/{artifactId}/download",
                "migrationTelemetry": "/api/queue/telemetry/migration",
                "skills": "/api/tasks/skills",
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
                "list": "/orchestrator/runs",
                "create": "/orchestrator/runs",
                "detail": "/orchestrator/runs/{id}",
                "artifacts": "/orchestrator/runs/{id}/artifacts",
                "approve": "/orchestrator/runs/{id}/approvals",
                "retry": "/orchestrator/runs/{id}/retry",
            },
            "proposals": {
                "list": "/api/proposals",
                "detail": "/api/proposals/{id}",
                "promote": "/api/proposals/{id}/promote",
                "dismiss": "/api/proposals/{id}/dismiss",
                "priority": "/api/proposals/{id}/priority",
                "snooze": "/api/proposals/{id}/snooze",
                "unsnooze": "/api/proposals/{id}/unsnooze",
            },
        },
        "system": {
            "defaultQueue": settings.spec_workflow.codex_queue
            or settings.celery.default_queue,
            "defaultRepository": default_repository,
            "defaultTaskRuntime": default_task_runtime,
            "defaultTaskModel": default_task_model,
            "defaultTaskEffort": default_task_effort,
            "defaultTaskModelByRuntime": default_task_model_by_runtime,
            "defaultTaskEffortByRuntime": default_task_effort_by_runtime,
            "queueEnv": "MOONMIND_QUEUE",
            "workerRuntimeEnv": "MOONMIND_WORKER_RUNTIME",
            "supportedTaskRuntimes": list(_SUPPORTED_TASK_RUNTIMES),
            "supportedWorkerRuntimes": list(_SUPPORTED_WORKER_RUNTIMES),
            "taskTemplateCatalog": {
                "enabled": bool(settings.feature_flags.task_template_catalog),
                "templateSaveEnabled": bool(
                    settings.feature_flags.task_template_catalog
                ),
            },
        },
    }


__all__ = ["build_runtime_config", "normalize_status", "status_maps"]
