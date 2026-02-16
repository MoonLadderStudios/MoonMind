"""Shared view-model helpers for the task dashboard UI."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

_POLL_INTERVALS_MS = {
    "list": 5000,
    "detail": 2000,
    "events": 1000,
}

_STATUS_MAPS: dict[str, dict[str, str]] = {
    "queue": {
        "queued": "queued",
        "running": "running",
        "succeeded": "succeeded",
        "failed": "failed",
        "cancelled": "cancelled",
        "dead_letter": "failed",
    },
    "speckit": {
        "pending": "queued",
        "retrying": "queued",
        "running": "running",
        "succeeded": "succeeded",
        "no_work": "succeeded",
        "failed": "failed",
        "cancelled": "cancelled",
    },
    "orchestrator": {
        "pending": "queued",
        "running": "running",
        "awaiting_approval": "awaiting_action",
        "succeeded": "succeeded",
        "rolled_back": "succeeded",
        "failed": "failed",
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
                "events": "/api/queue/jobs/{id}/events",
                "artifacts": "/api/queue/jobs/{id}/artifacts",
                "artifactDownload": "/api/queue/jobs/{id}/artifacts/{artifactId}/download",
            },
            "speckit": {
                "list": "/api/workflows/speckit/runs",
                "create": "/api/workflows/speckit/runs",
                "detail": "/api/workflows/speckit/runs/{id}",
                "tasks": "/api/workflows/speckit/runs/{id}/tasks",
                "artifacts": "/api/workflows/speckit/runs/{id}/artifacts",
            },
            "orchestrator": {
                "list": "/orchestrator/runs",
                "create": "/orchestrator/runs",
                "detail": "/orchestrator/runs/{id}",
                "artifacts": "/orchestrator/runs/{id}/artifacts",
                "approve": "/orchestrator/runs/{id}/approvals",
                "retry": "/orchestrator/runs/{id}/retry",
            },
        },
    }


__all__ = ["build_runtime_config", "normalize_status", "status_maps"]
