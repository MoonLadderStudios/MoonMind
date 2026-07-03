"""Pure helpers for workflow-owned step ledger state."""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime
from typing import Any

ACTIVE_STEP_STATUSES = ("executing", "reviewing", "awaiting_external")
TERMINAL_STEP_STATUSES = {"completed", "failed", "skipped", "canceled"}
READY_DEPENDENCY_STATUSES = {"completed", "skipped"}
LEGACY_STEP_STATUS_ALIASES = {
    "running": "executing",
    "succeeded": "completed",
}
_UNSET = object()


def _parse_iso_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _duration_ms(started_at: datetime | None, ended_at: datetime | None) -> int | None:
    if started_at is None or ended_at is None or ended_at < started_at:
        return None
    return max(0, int((ended_at - started_at).total_seconds() * 1000))


def step_timing_for_row(
    row: Mapping[str, Any],
    *,
    server_now: datetime | None = None,
) -> dict[str, Any]:
    """Return user-facing logical-step timing independent from workload timing."""

    status = _normalize_replayed_step_status(row.get("status"))
    started_at = _parse_iso_datetime(row.get("startedAt"))
    explicit_ended_at = _parse_iso_datetime(row.get("endedAt"))
    updated_at = _parse_iso_datetime(row.get("updatedAt"))
    server_now = server_now or updated_at

    ended_at = explicit_ended_at
    precision = "unavailable"
    duration_ms: int | None = None
    elapsed_ms: int | None = None

    if status in TERMINAL_STEP_STATUSES:
        if started_at is not None and ended_at is not None:
            duration_ms = _duration_ms(started_at, ended_at)
            elapsed_ms = duration_ms
            precision = "exact" if duration_ms is not None else "unavailable"
        elif started_at is not None and updated_at is not None:
            ended_at = updated_at
            duration_ms = _duration_ms(started_at, updated_at)
            elapsed_ms = duration_ms
            precision = "fallback" if duration_ms is not None else "unavailable"
    elif status in ACTIVE_STEP_STATUSES:
        elapsed_ms = _duration_ms(started_at, server_now)
        precision = "live" if elapsed_ms is not None else "unavailable"
    elif status == "ready":
        precision = "unavailable"
    elif status == "pending":
        precision = "unavailable"

    return {
        "startedAt": row.get("startedAt"),
        "endedAt": ended_at.isoformat() if ended_at is not None else None,
        "durationMs": duration_ms,
        "elapsedMs": elapsed_ms,
        "serverNow": server_now.isoformat() if server_now is not None else None,
        "precision": precision,
    }

def _normalize_replayed_step_status(status: Any) -> str:
    value = str(status or "").strip()
    return LEGACY_STEP_STATUS_ALIASES.get(value, value)

def default_step_refs() -> dict[str, Any]:
    return {
        "childWorkflowId": None,
        "childRunId": None,
        "agentRunId": None,
        "latestStepExecutionManifestRef": None,
        "stepExecutionManifestRefs": [],
        "latestStepExecutionCheckpointRef": None,
        "stepExecutionCheckpointRefs": [],
        "checkpointRefsByBoundary": {},
        "branchTurnArtifactRefs": {},
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
        "stepExecutionManifestRef": None,
        "stepExecutionManifestRefs": [],
    }

def default_step_workload() -> dict[str, Any] | None:
    return None


def _has_semantic_output_ref(row: Mapping[str, Any]) -> bool:
    artifacts = row.get("artifacts")
    if not isinstance(artifacts, Mapping):
        return False
    for key in ("outputSummary", "outputPrimary"):
        value = artifacts.get(key)
        if isinstance(value, str) and value.strip():
            return True
    return False


def _semantic_output_refs(row: Mapping[str, Any]) -> dict[str, str]:
    artifacts = row.get("artifacts")
    if not isinstance(artifacts, Mapping):
        return {}
    semantic_refs: dict[str, str] = {}
    for key in ("outputSummary", "outputPrimary"):
        value = artifacts.get(key)
        if isinstance(value, str) and value.strip():
            semantic_refs[key] = value.strip()
    return semantic_refs


def _source_execution_ordinal_value(row: Mapping[str, Any]) -> int:
    execution_ordinal = row.get("executionOrdinal", row.get("attempt"))
    if isinstance(execution_ordinal, bool):
        return 0
    if isinstance(execution_ordinal, (int, float)):
        return max(0, int(execution_ordinal))
    return 0


def _producing_attempt_identity(
    row: Mapping[str, Any],
    *,
    workflow_id: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any] | None:
    logical_step_id = str(row.get("logicalStepId") or "").strip()
    if not logical_step_id:
        return None
    preserved_from = row.get("preservedFrom")
    if isinstance(preserved_from, Mapping):
        source_workflow_id = str(preserved_from.get("workflowId") or "").strip()
        source_run_id = str(preserved_from.get("runId") or "").strip()
        source_logical_step_id = str(
            preserved_from.get("logicalStepId") or logical_step_id
        ).strip()
        source_execution_ordinal = _source_execution_ordinal_value(preserved_from)
        if source_workflow_id and source_run_id and source_execution_ordinal >= 1:
            return {
                "workflowId": source_workflow_id,
                "runId": source_run_id,
                "logicalStepId": source_logical_step_id or logical_step_id,
                "executionOrdinal": source_execution_ordinal,
            }
        return None
    attempt = _source_execution_ordinal_value(row)
    if not workflow_id or not run_id or attempt < 1:
        return None
    return {
        "workflowId": workflow_id,
        "runId": run_id,
        "logicalStepId": logical_step_id,
        "executionOrdinal": attempt,
    }


def _dependency_output_signature(
    row: Mapping[str, Any],
    *,
    workflow_id: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any] | None:
    refs = _semantic_output_refs(row)
    if not refs:
        return None
    producing_attempt = _producing_attempt_identity(
        row,
        workflow_id=workflow_id,
        run_id=run_id,
    )
    if producing_attempt is None:
        return None
    return {
        "producingAttempt": producing_attempt,
        "outputRefs": refs,
    }


def _mark_step_requires_revalidation(
    row: dict[str, Any],
    *,
    dependency_id: str,
    expected: Mapping[str, Any],
    actual: dict[str, Any] | None,
    updated_at: datetime,
) -> None:
    row["status"] = "pending"
    row["waitingReason"] = "requires_revalidation"
    row["dependencyReuseGate"] = {
        "status": "requires_revalidation",
        "dependencyId": dependency_id,
        "expected": dict(expected),
        "actual": actual,
    }
    row.pop("preservedFrom", None)
    row.pop("recoveryPreservation", None)
    row.pop("stateCheckpointRef", None)
    row["updatedAt"] = updated_at.isoformat()


def dependency_input_signatures(
    rows: list[dict[str, Any]],
    logical_step_id: str,
    *,
    workflow_id: str | None = None,
    run_id: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Return exact producing attempts and output refs for direct dependencies."""

    row_by_id = {
        step_id: row
        for row in rows
        if (step_id := str(row.get("logicalStepId") or "").strip())
    }
    target_row = row_by_id.get(logical_step_id)
    if target_row is None:
        raise KeyError(f"Unknown logical step id: {logical_step_id}")

    signatures: dict[str, dict[str, Any]] = {}
    for dependency_id in target_row.get("dependsOn") or []:
        dep_id = str(dependency_id or "").strip()
        dependency_row = row_by_id.get(dep_id)
        if dependency_row is None:
            continue
        signature = _dependency_output_signature(
            dependency_row,
            workflow_id=workflow_id,
            run_id=run_id,
        )
        if signature is not None:
            signatures[dep_id] = signature
    return signatures


def preserved_outputs_for_dependencies(
    rows: list[dict[str, Any]],
    logical_step_id: str,
) -> dict[str, dict[str, Any]]:
    """Return semantic output refs from preserved direct dependencies."""

    row_by_id = {
        step_id: row
        for row in rows
        if (step_id := str(row.get("logicalStepId") or "").strip())
    }
    target_row = row_by_id.get(logical_step_id)
    if target_row is None:
        raise KeyError(f"Unknown logical step id: {logical_step_id}")

    outputs: dict[str, dict[str, str]] = {}
    for dependency_id in target_row.get("dependsOn") or []:
        dep_id = str(dependency_id or "").strip()
        dependency_row = row_by_id.get(dep_id)
        if dependency_row is None or "preservedFrom" not in dependency_row:
            continue
        semantic_refs = _semantic_output_refs(dependency_row)
        if semantic_refs:
            output_payload: dict[str, Any] = dict(semantic_refs)
            producing_attempt = _producing_attempt_identity(dependency_row)
            if producing_attempt is not None:
                output_payload["producingAttempt"] = producing_attempt
            outputs[dep_id] = output_payload
    return outputs


def validate_preserved_dependency_outputs(
    rows: list[dict[str, Any]],
    *,
    updated_at: datetime,
) -> None:
    """Gate preserved downstream output reuse against exact dependency signatures."""

    row_by_id = {
        step_id: row
        for row in rows
        if (step_id := str(row.get("logicalStepId") or "").strip())
    }
    for row in rows:
        if not isinstance(row.get("preservedFrom"), Mapping):
            continue
        expected = row.get("dependencyInputs")
        if expected is None:
            continue
        if not isinstance(expected, Mapping):
            raise ValueError(
                f"preserved step {row.get('logicalStepId')} has invalid dependency input metadata"
            )
        dependencies = [
            str(dep or "").strip() for dep in row.get("dependsOn") or []
        ]
        for dependency_id in dependencies:
            if not dependency_id:
                continue
            expected_signature = expected.get(dependency_id)
            if not isinstance(expected_signature, Mapping):
                raise ValueError(
                    f"preserved step {row.get('logicalStepId')} requires "
                    f"dependency input metadata for {dependency_id}"
                )
            dependency_row = row_by_id.get(dependency_id)
            if dependency_row is None:
                raise ValueError(
                    f"preserved step {row.get('logicalStepId')} references "
                    f"unknown dependency {dependency_id}"
                )
            actual_signature = _dependency_output_signature(dependency_row)
            if actual_signature != dict(expected_signature):
                _mark_step_requires_revalidation(
                    row,
                    dependency_id=dependency_id,
                    expected=expected_signature,
                    actual=actual_signature,
                    updated_at=updated_at,
                )
                raise ValueError(
                    f"preserved step {row.get('logicalStepId')} dependency "
                    f"{dependency_id} requires revalidation"
                )
        row["dependencyReuseGate"] = {
            "status": "valid",
            "checkedAt": updated_at.isoformat(),
        }


def record_dependency_inputs_for_step(
    rows: list[dict[str, Any]],
    logical_step_id: str,
    *,
    workflow_id: str,
    run_id: str,
    updated_at: datetime,
) -> dict[str, dict[str, Any]]:
    """Record the exact producing attempts a step consumed as dependencies."""

    signatures = dependency_input_signatures(
        rows,
        logical_step_id,
        workflow_id=workflow_id,
        run_id=run_id,
    )
    for row in rows:
        if row.get("logicalStepId") == logical_step_id:
            row["dependencyInputs"] = deepcopy(signatures)
            row["updatedAt"] = updated_at.isoformat()
            return signatures
    raise KeyError(f"Unknown logical step id: {logical_step_id}")


def invalidate_downstream_steps_for_changed_output(
    rows: list[dict[str, Any]],
    logical_step_id: str,
    *,
    workflow_id: str,
    run_id: str,
    updated_at: datetime,
) -> list[str]:
    """Mark downstream rows for revalidation when an accepted producer changed."""

    producer_row = next(
        (row for row in rows if row.get("logicalStepId") == logical_step_id),
        None,
    )
    if producer_row is None:
        raise KeyError(f"Unknown logical step id: {logical_step_id}")
    current_signature = _dependency_output_signature(
        producer_row,
        workflow_id=workflow_id,
        run_id=run_id,
    )
    if current_signature is None:
        return []

    invalidated: list[str] = []
    pending = deque([logical_step_id])
    seen: set[str] = set()
    while pending:
        dependency_id = pending.popleft()
        if dependency_id in seen:
            continue
        seen.add(dependency_id)
        for row in rows:
            row_id = str(row.get("logicalStepId") or "").strip()
            if not row_id or row_id in seen:
                continue
            depends_on = [str(dep or "").strip() for dep in row.get("dependsOn") or []]
            if dependency_id not in depends_on:
                continue
            dependency_inputs = row.get("dependencyInputs")
            expected = (
                dependency_inputs.get(logical_step_id)
                if isinstance(dependency_inputs, Mapping)
                else None
            )
            should_invalidate = (
                expected is not None and dict(expected) != current_signature
            )
            if should_invalidate:
                _mark_step_requires_revalidation(
                    row,
                    dependency_id=logical_step_id,
                    expected=expected,
                    actual=current_signature,
                    updated_at=updated_at,
                )
                if row_id not in invalidated:
                    invalidated.append(row_id)
            pending.append(row_id)
    return invalidated


def _recovery_preservation(
    *,
    eligible: bool,
    reason: str,
    message: str,
) -> dict[str, Any]:
    return {
        "eligible": eligible,
        "reason": reason,
        "message": message,
    }


def build_initial_step_rows(
    *,
    ordered_nodes: list[dict[str, Any]],
    dependency_map: dict[str, list[str]],
    updated_at: datetime,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    updated_at_iso = updated_at.isoformat()
    for node in ordered_nodes:
        node_id = str(node.get("id") or "").strip()
        if not node_id:
            continue
        order = len(rows) + 1
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
                },
                "dependsOn": depends_on,
                "status": "ready" if not depends_on else "pending",
                "waitingReason": None,
                "attentionRequired": False,
                "attempt": 0,
                "executionOrdinal": 0,
                "startedAt": None,
                "updatedAt": updated_at_iso,
                "summary": None,
                "checks": [],
                "refs": default_step_refs(),
                "artifacts": default_step_artifacts(),
                "workload": default_step_workload(),
                "lastError": None,
            }
        )
    return rows

def build_step_ledger_snapshot(
    *,
    workflow_id: str,
    run_id: str,
    rows: list[dict[str, Any]],
    prepared_artifact_refs: list[str] | None = None,
    server_now: datetime | None = None,
) -> dict[str, Any]:
    snapshot_rows = deepcopy(rows)
    for row in snapshot_rows:
        row["timing"] = step_timing_for_row(row, server_now=server_now)
    return {
        "workflowId": workflow_id,
        "runId": run_id,
        "runScope": "latest",
        "preparedArtifactRefs": list(prepared_artifact_refs or []),
        "steps": snapshot_rows,
    }


def materialize_preserved_steps(
    rows: list[dict[str, Any]],
    *,
    source_workflow_id: str,
    source_run_id: str,
    preserved_steps: list[Mapping[str, Any]],
    updated_at: datetime,
) -> None:
    """Mark completed source steps as preserved in a resumed run ledger."""

    preserved_by_id: dict[str, Mapping[str, Any]] = {}
    for step in preserved_steps:
        logical_step_id = str(
            step.get("logicalStepId") or step.get("logical_step_id") or ""
        ).strip()
        if logical_step_id:
            preserved_by_id[logical_step_id] = step

    for row in rows:
        logical_step_id = str(row.get("logicalStepId") or "").strip()
        preserved = preserved_by_id.get(logical_step_id)
        if preserved is None:
            continue
        artifacts = default_step_artifacts()
        raw_artifacts = preserved.get("artifacts")
        if isinstance(raw_artifacts, Mapping):
            for key, value in raw_artifacts.items():
                if key in artifacts:
                    artifacts[key] = value
        state_checkpoint_ref = str(
            preserved.get("stateCheckpointRef")
            or preserved.get("state_checkpoint_ref")
            or ""
        ).strip()
        workspace_checkpoint_ref = str(
            preserved.get("workspaceCheckpointRef")
            or preserved.get("workspace_checkpoint_ref")
            or ""
        ).strip()
        step_checkpoint_ref = str(
            preserved.get("stepCheckpointRef")
            or preserved.get("step_checkpoint_ref")
            or ""
        ).strip()
        if not state_checkpoint_ref:
            raise ValueError(
                f"preserved step {logical_step_id} requires a state checkpoint ref"
            )
        try:
            source_execution_ordinal = int(
                preserved.get("sourceExecutionOrdinal") or preserved.get("source_execution_ordinal") or 1
            )
        except (TypeError, ValueError):
            source_execution_ordinal = 1
        row["status"] = _normalize_replayed_step_status(
            preserved.get("status") or "completed"
        )
        terminal_disposition = str(
            preserved.get("terminalDisposition")
            or preserved.get("terminal_disposition")
            or ""
        ).strip()
        if terminal_disposition:
            row["terminalDisposition"] = terminal_disposition
        else:
            row.pop("terminalDisposition", None)
        row["attempt"] = 0
        row["executionOrdinal"] = 0
        row["startedAt"] = preserved.get("startedAt") or preserved.get("started_at")
        row["endedAt"] = (
            preserved.get("endedAt")
            or preserved.get("ended_at")
            or preserved.get("completedAt")
            or preserved.get("completed_at")
        )
        row["summary"] = "Preserved from source run."
        row["waitingReason"] = None
        row["attentionRequired"] = False
        row["artifacts"] = artifacts
        row["stateCheckpointRef"] = state_checkpoint_ref
        if workspace_checkpoint_ref:
            row["workspaceCheckpointRef"] = workspace_checkpoint_ref
        else:
            row.pop("workspaceCheckpointRef", None)
        if step_checkpoint_ref:
            row["stepCheckpointRef"] = step_checkpoint_ref
        else:
            row.pop("stepCheckpointRef", None)
        row["recoveryPreservation"] = _recovery_preservation(
            eligible=True,
            reason="complete",
            message="Step has recoverable output refs and state checkpoint evidence.",
        )
        row["preservedFrom"] = {
            "workflowId": source_workflow_id,
            "runId": source_run_id,
            "logicalStepId": logical_step_id,
            "executionOrdinal": source_execution_ordinal,
        }
        dependency_inputs = preserved.get("dependencyInputs") or preserved.get(
            "dependency_inputs"
        )
        if isinstance(dependency_inputs, Mapping):
            row["dependencyInputs"] = deepcopy(dict(dependency_inputs))
        row["updatedAt"] = updated_at.isoformat()


def mark_step_checkpoint_evidence(
    rows: list[dict[str, Any]],
    logical_step_id: str,
    *,
    updated_at: datetime,
    state_checkpoint_ref: str | None = None,
    workspace_checkpoint_ref: str | None = None,
    step_checkpoint_ref: str | None = None,
    boundary: str | None = None,
) -> dict[str, Any]:
    """Attach checkpoint evidence and preservation eligibility to a step row."""

    for row in rows:
        if row.get("logicalStepId") != logical_step_id:
            continue
        if state_checkpoint_ref is not None:
            row["stateCheckpointRef"] = state_checkpoint_ref
        if workspace_checkpoint_ref is not None:
            row["workspaceCheckpointRef"] = workspace_checkpoint_ref
        if step_checkpoint_ref is not None:
            row["stepCheckpointRef"] = step_checkpoint_ref
        canonical_ref = str(step_checkpoint_ref or "").strip()
        canonical_boundary = str(boundary or "").strip()
        if canonical_ref:
            refs = default_step_refs()
            current_refs = row.get("refs")
            if isinstance(current_refs, Mapping):
                refs.update(current_refs)
            history = refs.get("stepExecutionCheckpointRefs")
            if not isinstance(history, list):
                history = []
            normalized_history = [
                item.strip()
                for item in history
                if isinstance(item, str) and item.strip()
            ]
            if canonical_ref not in normalized_history:
                normalized_history.append(canonical_ref)
            refs["latestStepExecutionCheckpointRef"] = canonical_ref
            refs["stepExecutionCheckpointRefs"] = normalized_history
            by_boundary = refs.get("checkpointRefsByBoundary")
            if not isinstance(by_boundary, Mapping):
                by_boundary = {}
            normalized_by_boundary = {
                str(key): str(value).strip()
                for key, value in by_boundary.items()
                if str(key).strip()
                and isinstance(value, str)
                and str(value).strip()
            }
            if canonical_boundary:
                normalized_by_boundary[canonical_boundary] = canonical_ref
            refs["checkpointRefsByBoundary"] = normalized_by_boundary
            row["refs"] = refs
        existing_checkpoint = str(row.get("stateCheckpointRef") or "").strip()
        status = _normalize_replayed_step_status(row.get("status"))
        if status not in {"completed", "skipped"}:
            preservation = _recovery_preservation(
                eligible=False,
                reason="not_completed",
                message="Step is not completed and cannot be preserved for Resume.",
            )
        elif not _has_semantic_output_ref(row):
            preservation = _recovery_preservation(
                eligible=False,
                reason="missing_output_refs",
                message="Completed step cannot be preserved because no recoverable output refs were recorded.",
            )
        elif not existing_checkpoint:
            preservation = _recovery_preservation(
                eligible=False,
                reason="missing_state_checkpoint",
                message="Completed step cannot be preserved because no state checkpoint ref was recorded.",
            )
        else:
            preservation = _recovery_preservation(
                eligible=True,
                reason="complete",
                message="Step has recoverable output refs and state checkpoint evidence.",
            )
        row["recoveryPreservation"] = preservation
        row["updatedAt"] = updated_at.isoformat()
        return row
    raise KeyError(f"Unknown logical step id: {logical_step_id}")


def mark_step_execution_manifest_evidence(
    rows: list[dict[str, Any]],
    logical_step_id: str,
    *,
    updated_at: datetime,
    step_execution_manifest_ref: str,
) -> dict[str, Any]:
    """Attach compact Step Execution manifest refs to a step row."""

    manifest_ref = str(step_execution_manifest_ref or "").strip()
    if not manifest_ref:
        raise ValueError("step_execution_manifest_ref must be a non-empty string")
    for row in rows:
        if row.get("logicalStepId") != logical_step_id:
            continue
        artifacts = default_step_artifacts()
        current_artifacts = row.get("artifacts")
        if isinstance(current_artifacts, Mapping):
            artifacts.update(current_artifacts)
        history = artifacts.get("stepExecutionManifestRefs")
        if not isinstance(history, list):
            history = []
        normalized_history = [
            item.strip()
            for item in history
            if isinstance(item, str) and item.strip()
        ]
        if manifest_ref not in normalized_history:
            normalized_history.append(manifest_ref)
        artifacts["stepExecutionManifestRef"] = manifest_ref
        artifacts["stepExecutionManifestRefs"] = normalized_history
        row["artifacts"] = artifacts
        row["updatedAt"] = updated_at.isoformat()
        return row
    raise KeyError(f"Unknown logical step id: {logical_step_id}")


def clear_step_checkpoint_evidence(
    rows: list[dict[str, Any]],
    logical_step_id: str,
    *,
    updated_at: datetime,
) -> dict[str, Any]:
    """Clear attempt-scoped checkpoint evidence before a new step execution starts."""

    for row in rows:
        if row.get("logicalStepId") != logical_step_id:
            continue
        row.pop("stateCheckpointRef", None)
        row.pop("workspaceCheckpointRef", None)
        row.pop("stepCheckpointRef", None)
        refs = row.get("refs")
        if isinstance(refs, dict):
            refs["latestStepExecutionCheckpointRef"] = None
            refs["stepExecutionCheckpointRefs"] = []
            refs["checkpointRefsByBoundary"] = {}
        row.pop("recoveryPreservation", None)
        row["updatedAt"] = updated_at.isoformat()
        return row
    raise KeyError(f"Unknown logical step id: {logical_step_id}")


def build_progress_summary(
    rows: list[dict[str, Any]],
    *,
    updated_at: datetime,
) -> dict[str, Any]:
    counts = {
        "total": len(rows),
        "pending": 0,
        "ready": 0,
        "executing": 0,
        "awaitingExternal": 0,
        "reviewing": 0,
        "completed": 0,
        "failed": 0,
        "skipped": 0,
        "canceled": 0,
    }
    current_step_title: str | None = None
    for active_status in ACTIVE_STEP_STATUSES:
        match = next(
            (
                row
                for row in rows
                if _normalize_replayed_step_status(row.get("status")) == active_status
            ),
            None,
        )
        if match is not None:
            current_step_title = str(match.get("title") or "").strip() or None
            break

    for row in rows:
        status = _normalize_replayed_step_status(row.get("status"))
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
    refs: Mapping[str, Any] | object = _UNSET,
    artifacts: Mapping[str, Any] | object = _UNSET,
    workload: Mapping[str, Any] | None | object = _UNSET,
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
            row["executionOrdinal"] = int(row["attempt"])
        if set_started_at:
            row["startedAt"] = updated_at.isoformat()
            row.pop("endedAt", None)
        if summary is not _UNSET:
            row["summary"] = summary
        if waiting_reason is not _UNSET:
            row["waitingReason"] = waiting_reason
        if attention_required is not None:
            row["attentionRequired"] = attention_required
        if last_error is not _UNSET:
            row["lastError"] = last_error
        if refs is not _UNSET:
            merged_refs = default_step_refs()
            current_refs = row.get("refs")
            if isinstance(current_refs, dict):
                merged_refs.update(current_refs)
            if isinstance(refs, Mapping):
                for key, value in refs.items():
                    if key in merged_refs:
                        merged_refs[key] = value
            if not isinstance(merged_refs.get("stepExecutionCheckpointRefs"), list):
                merged_refs["stepExecutionCheckpointRefs"] = []
            if not isinstance(merged_refs.get("checkpointRefsByBoundary"), Mapping):
                merged_refs["checkpointRefsByBoundary"] = {}
            row["refs"] = merged_refs
        if artifacts is not _UNSET:
            merged_artifacts = default_step_artifacts()
            current_artifacts = row.get("artifacts")
            if isinstance(current_artifacts, dict):
                merged_artifacts.update(current_artifacts)
            if isinstance(artifacts, Mapping):
                for key, value in artifacts.items():
                    if key in merged_artifacts or key == "externalStateRef":
                        merged_artifacts[key] = value
            history = merged_artifacts.get("stepExecutionManifestRefs")
            if not isinstance(history, list):
                merged_artifacts["stepExecutionManifestRefs"] = []
            row["artifacts"] = merged_artifacts
        if workload is not _UNSET:
            row["workload"] = dict(workload) if isinstance(workload, Mapping) else None
        if _normalize_replayed_step_status(status) in TERMINAL_STEP_STATUSES:
            row["waitingReason"] = None
            row["attentionRequired"] = False
            row["endedAt"] = updated_at.isoformat()
        row["updatedAt"] = updated_at.isoformat()
        return row
    raise KeyError(f"Unknown logical step id: {logical_step_id}")

def upsert_step_check(
    rows: list[dict[str, Any]],
    logical_step_id: str,
    *,
    kind: str,
    status: str,
    summary: str | None = None,
    retry_count: int = 0,
    artifact_ref: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    for row in rows:
        if row.get("logicalStepId") != logical_step_id:
            continue
        checks = row.get("checks")
        if not isinstance(checks, list):
            checks = []
        check_payload = {
            "kind": kind,
            "status": status,
            "summary": summary,
            "retryCount": retry_count,
            "artifactRef": artifact_ref,
        }
        if metadata:
            check_payload.update(dict(metadata))
        for index, existing in enumerate(checks):
            if isinstance(existing, Mapping) and existing.get("kind") == kind:
                checks[index] = check_payload
                row["checks"] = checks
                return check_payload
        checks.append(check_payload)
        row["checks"] = checks
        return check_payload
    raise KeyError(f"Unknown logical step id: {logical_step_id}")

def refresh_ready_steps(rows: list[dict[str, Any]], *, updated_at: datetime) -> None:
    statuses = {
        str(row.get("logicalStepId")): _normalize_replayed_step_status(
            row.get("status")
        )
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
