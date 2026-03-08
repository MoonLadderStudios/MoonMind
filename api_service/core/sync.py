"""Projection synchronization logic for Temporal executions."""

import json
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from temporalio.client import WorkflowExecutionDescription, WorkflowExecutionStatus

from api_service.db.models import (
    MoonMindWorkflowState,
    TemporalExecutionCloseStatus,
    TemporalExecutionOwnerType,
    TemporalExecutionProjectionSourceMode,
    TemporalExecutionProjectionSyncState,
    TemporalExecutionRecord,
    TemporalWorkflowType,
)

logger = logging.getLogger(__name__)

WORKFLOW_ENTRY_BY_TYPE = {
    TemporalWorkflowType.RUN: "run",
    TemporalWorkflowType.MANIFEST_INGEST: "manifest",
}


def _utc_now() -> datetime:
    return datetime.now(UTC)


def map_temporal_state_to_projection(
    desc: WorkflowExecutionDescription,
) -> dict[str, Any]:
    """Map Temporal workflow execution description to projection payload."""
    memo = dict(desc.memo) if desc.memo else {}

    status_map = {
        WorkflowExecutionStatus.COMPLETED: (
            MoonMindWorkflowState.SUCCEEDED,
            TemporalExecutionCloseStatus.COMPLETED,
        ),
        WorkflowExecutionStatus.FAILED: (
            MoonMindWorkflowState.FAILED,
            TemporalExecutionCloseStatus.FAILED,
        ),
        WorkflowExecutionStatus.CANCELED: (
            MoonMindWorkflowState.CANCELED,
            TemporalExecutionCloseStatus.CANCELED,
        ),
        WorkflowExecutionStatus.TERMINATED: (
            MoonMindWorkflowState.FAILED,
            TemporalExecutionCloseStatus.TERMINATED,
        ),
        WorkflowExecutionStatus.TIMED_OUT: (
            MoonMindWorkflowState.FAILED,
            TemporalExecutionCloseStatus.TIMED_OUT,
        ),
        WorkflowExecutionStatus.CONTINUED_AS_NEW: (
            MoonMindWorkflowState.SUCCEEDED,
            TemporalExecutionCloseStatus.COMPLETED,
        ),
    }

    state_value, close_status = status_map.get(
        desc.status,
        (MoonMindWorkflowState.EXECUTING, None),
    )

    try:
        workflow_type = TemporalWorkflowType(desc.workflow_type)
    except ValueError:
        workflow_type = TemporalWorkflowType.RUN

    entry = str(memo.get("entry") or WORKFLOW_ENTRY_BY_TYPE.get(workflow_type, "run")).strip()
    owner_id = memo.get("owner_id")
    owner_type_raw = memo.get("owner_type")
    
    try:
        owner_type = TemporalExecutionOwnerType(str(owner_type_raw)) if owner_type_raw else TemporalExecutionOwnerType.USER
    except ValueError:
        owner_type = TemporalExecutionOwnerType.USER

    search_attributes: dict[str, Any] = {}
    try:
        raw_search_attributes = desc.search_attributes or {}
        for key, value in raw_search_attributes.items():
            raw_value = getattr(value, "data", value)
            if isinstance(raw_value, bytes):
                try:
                    search_attributes[key] = json.loads(raw_value.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    search_attributes[key] = raw_value.decode("utf-8", errors="replace")
            else:
                search_attributes[key] = raw_value
    except Exception:
        logger.exception("Failed to decode Temporal search attributes for %s", desc.id)

    artifact_refs = memo.get("artifact_refs", [])
    if not isinstance(artifact_refs, list):
        artifact_refs = []

    return {
        "workflow_id": desc.id,
        "run_id": desc.run_id,
        "namespace": desc.namespace,
        "workflow_type": workflow_type,
        "owner_id": owner_id,
        "owner_type": owner_type,
        "state": state_value,
        "close_status": close_status,
        "entry": entry,
        "search_attributes": search_attributes,
        "memo": dict(memo),
        "artifact_refs": artifact_refs,
        "input_ref": memo.get("input_ref"),
        "plan_ref": memo.get("plan_ref"),
        "manifest_ref": memo.get("manifest_ref"),
        "parameters": memo.get("parameters", {}),
        "integration_state": memo.get("integration_state"),
        "pending_parameters_patch": memo.get("pending_parameters_patch"),
        "paused": bool(memo.get("paused", False)),
        "awaiting_external": bool(memo.get("awaiting_external", False)),
        "waiting_reason": memo.get("waiting_reason"),
        "attention_required": bool(memo.get("attention_required", False)),
        "step_count": int(memo.get("step_count", 0) or 0),
        "wait_cycle_count": int(memo.get("wait_cycle_count", 0) or 0),
        "rerun_count": int(memo.get("rerun_count", 0) or 0),
        "create_idempotency_key": memo.get("create_idempotency_key"),
        "last_update_idempotency_key": memo.get("last_update_idempotency_key"),
        "last_update_response": memo.get("last_update_response"),
        "started_at": desc.start_time,
        "updated_at": _utc_now(),
        "closed_at": desc.close_time,
    }


async def sync_execution_projection(
    session: AsyncSession,
    desc: WorkflowExecutionDescription,
) -> TemporalExecutionRecord:
    """Upsert the Temporal workflow state to the local projection database."""
    payload = map_temporal_state_to_projection(desc)
    
    projection = await session.get(TemporalExecutionRecord, desc.id)
    previous_version = int(projection.projection_version or 0) if projection else 0
    synced_at = _utc_now()
    
    if projection is None:
        projection = TemporalExecutionRecord(
            **payload,
            projection_version=1,
            last_synced_at=synced_at,
            sync_state=TemporalExecutionProjectionSyncState.FRESH,
            sync_error=None,
            source_mode=TemporalExecutionProjectionSourceMode.TEMPORAL_AUTHORITATIVE,
        )
        session.add(projection)
    else:
        for field, value in payload.items():
            setattr(projection, field, value)
        projection.projection_version = max(previous_version + 1, 1)
        projection.last_synced_at = synced_at
        projection.sync_state = TemporalExecutionProjectionSyncState.FRESH
        projection.sync_error = None
        projection.source_mode = TemporalExecutionProjectionSourceMode.TEMPORAL_AUTHORITATIVE
        
    return projection
