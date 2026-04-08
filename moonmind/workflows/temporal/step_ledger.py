"""Pure helpers for workflow-owned step ledger state."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any

ACTIVE_STEP_STATUSES = ("running", "reviewing", "awaiting_external", "ready")
TERMINAL_STEP_STATUSES = {"succeeded", "failed", "skipped", "canceled"}
READY_DEPENDENCY_STATUSES = {"succeeded", "skipped"}
_UNSET = object()


def default_step_refs() -> dict[str, Any]:
    return {
        "childWorkflowId": None,
        "childRunId": None,
        "taskRunId": None,
    }


def default_step_artifacts() -> dict[str, Any]:
    return {
        "outputSummary": None,
        "outputPrimary": None,
        "runtimeStdout": None,
        "runtimeStderr": None,
        "runtimeMergedLogs": None,
        "runtimeDiagnostics": None,
        "providerSnapshot": None,
    }


def build_initial_step_rows(
    *,
    ordered_nodes: list[dict[str, Any]],
    dependency_map: dict[str, list[str]],
    updated_at: datetime,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    updated_at_iso = updated_at.isoformat()
    for order, node in enumerate(ordered_nodes, start=1):
        node_id = str(node.get("id") or "").strip()
        tool = dict(node.get("tool") or node.get("skill") or {})
        inputs = dict(node.get("inputs") or {})
        title = (
            str(node.get("title") or inputs.get("title") or tool.get("name") or node_id)
            .strip()
        )
        depends_on = list(dependency_map.get(node_id) or [])
        rows.append(
            {
                "logicalStepId": node_id,
                "order": order,
                "title": title,
                "tool": {
                    "type": str(tool.get("type") or tool.get("kind") or "skill"),
                    "name": str(tool.get("name") or tool.get("id") or ""),
                    "version": str(tool.get("version") or ""),
                },
                "dependsOn": depends_on,
                "status": "ready" if not depends_on else "pending",
                "waitingReason": None,
                "attentionRequired": False,
                "attempt": 0,
                "startedAt": None,
                "updatedAt": updated_at_iso,
                "summary": None,
                "checks": [],
                "refs": default_step_refs(),
                "artifacts": default_step_artifacts(),
                "lastError": None,
            }
        )
    return rows


def build_step_ledger_snapshot(
    *,
    workflow_id: str,
    run_id: str,
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "workflowId": workflow_id,
        "runId": run_id,
        "runScope": "latest",
        "steps": deepcopy(rows),
    }


def build_progress_summary(
    rows: list[dict[str, Any]],
    *,
    updated_at: datetime,
) -> dict[str, Any]:
    counts = {
        "total": len(rows),
        "pending": 0,
        "ready": 0,
        "running": 0,
        "awaitingExternal": 0,
        "reviewing": 0,
        "succeeded": 0,
        "failed": 0,
        "skipped": 0,
        "canceled": 0,
    }
    current_step_title: str | None = None
    for active_status in ACTIVE_STEP_STATUSES:
        match = next((row for row in rows if row.get("status") == active_status), None)
        if match is not None:
            current_step_title = str(match.get("title") or "").strip() or None
            break

    for row in rows:
        status = str(row.get("status") or "").strip()
        if status == "awaiting_external":
            counts["awaitingExternal"] += 1
        elif status in counts:
            counts[status] += 1

    counts["currentStepTitle"] = current_step_title
    counts["updatedAt"] = updated_at.isoformat()
    return counts


def update_step_row(
    rows: list[dict[str, Any]],
    logical_step_id: str,
    *,
    updated_at: datetime,
    status: str | None = None,
    summary: str | None | object = _UNSET,
    waiting_reason: str | None | object = _UNSET,
    attention_required: bool | None = None,
    last_error: str | None | object = _UNSET,
    increment_attempt: bool = False,
    set_started_at: bool = False,
) -> dict[str, Any]:
    for row in rows:
        if row.get("logicalStepId") != logical_step_id:
            continue
        if status is not None:
            row["status"] = status
        if increment_attempt:
            row["attempt"] = int(row.get("attempt") or 0) + 1
        if set_started_at:
            row["startedAt"] = updated_at.isoformat()
        if summary is not _UNSET:
            row["summary"] = summary
        if waiting_reason is not _UNSET:
            row["waitingReason"] = waiting_reason
        if attention_required is not None:
            row["attentionRequired"] = attention_required
        if last_error is not _UNSET:
            row["lastError"] = last_error
        if status in TERMINAL_STEP_STATUSES:
            row["waitingReason"] = None
            row["attentionRequired"] = False
        row["updatedAt"] = updated_at.isoformat()
        return row
    raise KeyError(f"Unknown logical step id: {logical_step_id}")


def refresh_ready_steps(rows: list[dict[str, Any]], *, updated_at: datetime) -> None:
    statuses = {
        str(row.get("logicalStepId")): str(row.get("status") or "")
        for row in rows
    }
    for row in rows:
        if row.get("status") != "pending":
            continue
        dependencies = list(row.get("dependsOn") or [])
        if dependencies and all(
            statuses.get(dep_id) in READY_DEPENDENCY_STATUSES for dep_id in dependencies
        ):
            row["status"] = "ready"
            row["updatedAt"] = updated_at.isoformat()
