"""Projection synchronization logic for Temporal executions."""

import json
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from temporalio.client import WorkflowExecutionDescription, WorkflowExecutionStatus

from api_service.db.models import (
    MoonMindWorkflowState,
    TemporalExecutionCanonicalRecord,
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
    TemporalWorkflowType.AUTH_PROFILE_MANAGER: "auth_profile",
}


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _sanitize_for_json(obj: Any) -> Any:
    """Recursively convert non-JSON-serializable objects (e.g. datetime) to JSON-safe types."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_for_json(item) for item in obj]
    return obj


def _coerce_temporal_scalar(value: Any) -> str | None:
    if isinstance(value, list):
        for item in value:
            candidate = _coerce_temporal_scalar(item)
            if candidate:
                return candidate
        return None
    if value is None:
        return None
    text = str(value).strip()
    return text or None


async def map_temporal_state_to_projection(
    desc: WorkflowExecutionDescription,
) -> dict[str, Any]:
    """Map Temporal workflow execution description to projection payload."""
    # desc.memo() is an async coroutine in the Temporal SDK and must be awaited
    try:
        raw_memo = await desc.memo()
        memo = dict(raw_memo) if raw_memo else {}
    except Exception:
        logger.exception("Failed to decode Temporal memo for %s", desc.id)
        memo = {}

    status_map = {
        WorkflowExecutionStatus.COMPLETED: (
            MoonMindWorkflowState.COMPLETED,
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
            MoonMindWorkflowState.COMPLETED,
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

    entry = str(
        memo.get("entry") or WORKFLOW_ENTRY_BY_TYPE.get(workflow_type, "run")
    ).strip()
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

    owner_id = _coerce_temporal_scalar(memo.get("owner_id")) or _coerce_temporal_scalar(
        search_attributes.get("mm_owner_id")
    )
    owner_type_raw = _coerce_temporal_scalar(
        memo.get("owner_type")
    ) or _coerce_temporal_scalar(search_attributes.get("mm_owner_type"))
    try:
        owner_type = (
            TemporalExecutionOwnerType(owner_type_raw)
            if owner_type_raw
            else TemporalExecutionOwnerType.USER
        )
    except ValueError:
        owner_type = TemporalExecutionOwnerType.USER

    if desc.status == WorkflowExecutionStatus.RUNNING:
        mm_state = search_attributes.get("mm_state")
        if isinstance(mm_state, list) and mm_state:
            mm_state = mm_state[0]
        if mm_state:
            try:
                state_value = MoonMindWorkflowState(str(mm_state))
            except ValueError:
                logger.warning(
                    "Invalid value for mm_state search attribute: '%s'", mm_state
                )

    artifact_refs = memo.get("artifact_refs", [])
    if not isinstance(artifact_refs, list):
        artifact_refs = []

    waiting_reason = memo.get("waiting_reason")
    if not waiting_reason and state_value == MoonMindWorkflowState.AWAITING_EXTERNAL:
        waiting_reason = "external_completion"

    sanitized_memo = _sanitize_for_json(dict(memo))
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
        "search_attributes": _sanitize_for_json(search_attributes),
        "memo": sanitized_memo,
        "artifact_refs": artifact_refs,
        "input_ref": memo.get("input_ref"),
        "plan_ref": memo.get("plan_ref"),
        "manifest_ref": memo.get("manifest_ref"),
        "parameters": _sanitize_for_json(memo.get("parameters", {}) or {}),
        "integration_state": _sanitize_for_json(memo.get("integration_state")),
        "pending_parameters_patch": _sanitize_for_json(
            memo.get("pending_parameters_patch")
        ),
        "paused": bool(memo.get("paused", False)),
        "awaiting_external": state_value == MoonMindWorkflowState.AWAITING_EXTERNAL,
        "waiting_reason": waiting_reason,
        "attention_required": bool(memo.get("attention_required", False)),
        "step_count": int(memo.get("step_count", 0) or 0),
        "wait_cycle_count": int(memo.get("wait_cycle_count", 0) or 0),
        "rerun_count": int(memo.get("rerun_count", 0) or 0),
        "create_idempotency_key": memo.get("create_idempotency_key"),
        "last_update_idempotency_key": memo.get("last_update_idempotency_key"),
        "last_update_response": _sanitize_for_json(memo.get("last_update_response")),
        "started_at": desc.execution_time or desc.start_time,
        "updated_at": _utc_now(),
        "closed_at": desc.close_time,
    }


async def sync_execution_projection(
    session: AsyncSession,
    desc: WorkflowExecutionDescription,
) -> TemporalExecutionRecord:
    """Upsert the Temporal workflow state to the local projection database."""
    payload = await map_temporal_state_to_projection(desc)

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
        projection.source_mode = (
            TemporalExecutionProjectionSourceMode.TEMPORAL_AUTHORITATIVE
        )

    # Also sync the canonical record (temporal_execution_sources) so that
    # service.describe_execution doesn't read stale state and overwrite the projection
    # with it. Only update state/close_status if they differ from what Temporal reports.
    state_value: MoonMindWorkflowState = (
        payload.get("state") or MoonMindWorkflowState.INITIALIZING
    )
    close_status_value: TemporalExecutionCloseStatus | None = payload.get(
        "close_status"
    )
    canonical = await session.get(TemporalExecutionCanonicalRecord, desc.id)

    # Preserve canonical parameters (targetRuntime, model, effort, etc.) that were
    # set at execution creation time and are not present in the Temporal memo.
    # The memo-derived parameters from map_temporal_state_to_projection are typically
    # empty; the canonical record is the source of truth for creation-time parameters.
    if canonical is not None:
        canonical_params = canonical.parameters or {}
        synced_params = payload.get("parameters") or {}
        projection.parameters = {**canonical_params, **synced_params}

    if canonical is not None and canonical.state != state_value:
        canonical.state = state_value
        canonical.close_status = close_status_value
        if payload.get("closed_at") and canonical.closed_at is None:
            canonical.closed_at = payload["closed_at"]
        logger.info(
            "Synced canonical record %s: state=%s close_status=%s",
            desc.id,
            state_value,
            close_status_value,
        )

    return projection


async def fetch_and_sync_execution(
    session: AsyncSession,
    workflow_id: str,
    client: Any,
) -> TemporalExecutionRecord:
    """Fetch execution from Temporal and sync to local projection database."""
    from moonmind.workflows.temporal.client import fetch_workflow_execution

    desc = await fetch_workflow_execution(client, workflow_id)
    return await sync_execution_projection(session, desc)


async def sync_temporal_executions_safely(
    session: AsyncSession,
    items: list[Any],
    client: Any,
) -> list[Any]:
    import asyncio

    async def fetch_and_sync(item):
        try:
            return await fetch_and_sync_execution(session, item.workflow_id, client)
        except Exception as exc:
            logger.warning(
                "Failed to sync execution %s from Temporal: %s",
                item.workflow_id,
                exc,
            )
            return item

    tasks = [fetch_and_sync(item) for item in items]
    updated_items = list(await asyncio.gather(*tasks))
    await session.commit()
    return updated_items


async def sync_single_temporal_execution_safely(
    session: AsyncSession,
    workflow_id: str,
    client: Any,
) -> Any:
    try:
        record = await fetch_and_sync_execution(session, workflow_id, client)
        await session.commit()
        return record
    except Exception as exc:
        logger.warning(
            "Failed to sync execution %s from Temporal: %s",
            workflow_id,
            exc,
            exc_info=True,
        )
        return None
