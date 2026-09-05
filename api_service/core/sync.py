"""Projection synchronization logic for Temporal executions."""

import json
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified
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
from moonmind.statuses.compat import (
    canonicalize_finish_outcome_code_alias,
    canonicalize_workflow_state_alias,
)
from moonmind.statuses.workflow import (
    PRE_WORKFLOW_STATES,
    WORKFLOW_STATE_TO_CLOSE_STATUS,
)

logger = logging.getLogger(__name__)

WORKFLOW_ENTRY_BY_TYPE = {
    TemporalWorkflowType.USER_WORKFLOW: "user_workflow",
    TemporalWorkflowType.MANIFEST_INGEST: "manifest",
    TemporalWorkflowType.PROVIDER_PROFILE_MANAGER: "provider_profile",
}

CORE_TEMPORAL_SYNC_FIELDS = (
    "run_id",
    "state",
    "close_status",
    "started_at",
    "updated_at",
    "closed_at",
    "workflow_id",
    "namespace",
    "workflow_type",
)

LOCAL_ONLY_EXECUTION_FIELDS = (
    "create_idempotency_key",
    "last_update_idempotency_key",
    "last_update_response",
    "finish_outcome_code",
    "finish_summary_json",
)

# Lifecycle states where the workflow has not yet begun real work. When a
# workflow is in one of these states and has not stamped mm_started_at, the
# projection must not synthesize a started_at from Temporal's workflow
# start_time / execution_time — those fire as soon as the workflow is
# scheduled, even while it is awaiting capacity. ``mm_started_at`` is the
# canonical source for "real work began"; see
# moonmind.workflows.temporal.workflows.run.MoonMindRunWorkflow._mark_real_work_started.
PRE_WORK_STATES = PRE_WORKFLOW_STATES
TERMINAL_DOMAIN_STATE_TO_CLOSE_STATUS = WORKFLOW_STATE_TO_CLOSE_STATUS

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

def _finish_summary_from_memo(memo: dict[str, Any]) -> dict[str, Any] | None:
    finish_summary = memo.get("finishSummary") or memo.get("finish_summary")
    if isinstance(finish_summary, dict):
        sanitized = _sanitize_for_json(dict(finish_summary))
        return sanitized if isinstance(sanitized, dict) else None
    return None

def _artifact_ref_from_memo(memo: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = memo.get(key)
        if isinstance(value, str):
            candidate = value.strip()
            if candidate:
                return candidate
        if isinstance(value, dict):
            for ref_key in (
                "artifactRef",
                "artifact_ref",
                "artifactId",
                "artifact_id",
                "id",
                "ref",
            ):
                ref_val = value.get(ref_key)
                if isinstance(ref_val, str):
                    candidate = ref_val.strip()
                    if candidate:
                        return candidate
    return None

def _finish_outcome_code_from_summary(
    finish_summary: dict[str, Any] | None,
) -> str | None:
    if not isinstance(finish_summary, dict):
        return None
    finish_outcome = finish_summary.get("finishOutcome") or finish_summary.get(
        "finish_outcome"
    )
    if not isinstance(finish_outcome, dict):
        return None
    return canonicalize_finish_outcome_code_alias(
        finish_outcome.get("code"),
        logger=logger,
    )

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

def _coerce_mm_state(search_attributes: dict[str, Any]) -> MoonMindWorkflowState | None:
    raw_state = _coerce_temporal_scalar(search_attributes.get("mm_state"))
    if raw_state is None:
        return None
    canonical_state = canonicalize_workflow_state_alias(raw_state, logger=logger)
    if canonical_state is None:
        return None
    try:
        return MoonMindWorkflowState(canonical_state)
    except ValueError:
        logger.warning("Invalid value for mm_state search attribute: '%s'", raw_state)
        return None

def _parse_temporal_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if isinstance(value, (list, tuple)) and value and isinstance(value[0], datetime):
        val = value[0]
        return val if val.tzinfo is not None else val.replace(tzinfo=UTC)
    scalar = _coerce_temporal_scalar(value)
    if not scalar:
        return None
    try:
        parsed = datetime.fromisoformat(scalar.replace("Z", "+00:00"))
    except ValueError:
        logger.warning("Invalid datetime search attribute value: %r", value)
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)

def merged_parameters_for_projection(
    payload: dict[str, Any],
    canonical: TemporalExecutionCanonicalRecord | None,
) -> dict[str, Any]:
    """Merge creation-time parameters from the canonical DB row with memo-derived parameters.

    ``map_temporal_state_to_projection`` sets ``parameters`` from workflow memo only; memo
    typically does not repeat ``targetRuntime`` or task tool snapshots. The canonical row
    in ``temporal_execution_sources`` holds those creation-time fields.

    Both ``sync_execution_projection`` and the ``GET /api/executions?source=temporal`` list
    path must apply the same merge so ``_serialize_execution`` can populate Runtime/Skill.
    """
    synced_params = payload.get("parameters") or {}
    if canonical is None:
        return dict(synced_params)
    canonical_params = canonical.parameters or {}
    return {**canonical_params, **synced_params}

def merged_memo_for_projection(
    payload: dict[str, Any],
    canonical: TemporalExecutionCanonicalRecord | None,
) -> dict[str, Any]:
    """Merge the canonical DB memo with the Temporal-derived memo.

    Temporal workflow memos are immutable after workflow start, so any key written
    to the canonical DB memo after launch (e.g. ``agentRunId`` set by
    ``_report_task_run_binding``) will never appear in Temporal's memo.  Letting
    Temporal's memo overwrite the projection memo on every sync would silently
    discard these DB-side additions.

    Strategy: Temporal wins for any key it provides (it is authoritative for
    lifecycle fields).  The canonical DB memo only fills in keys that Temporal
    does not supply.
    """
    temporal_memo = dict(payload.get("memo") or {})
    if canonical is None:
        return temporal_memo
    canonical_memo = dict(canonical.memo or {})
    # DB-only keys supplement; Temporal keys take precedence.
    return {**canonical_memo, **temporal_memo}

def preserve_local_only_fields(payload: dict[str, Any], *records: Any) -> None:
    """Keep DB-managed helpers when Temporal payloads omit them."""
    for field in LOCAL_ONLY_EXECUTION_FIELDS:
        if payload.get(field) is not None:
            continue
        for record in records:
            if record is None:
                continue
            preserved = getattr(record, field, None)
            if preserved is not None:
                payload[field] = preserved
                break

async def map_temporal_state_to_projection(
    desc: WorkflowExecutionDescription,
) -> dict[str, Any]:
    """Map Temporal workflow execution description to projection payload."""
    # desc.memo() is an async coroutine in the Temporal SDK and must be awaited
    memo_loaded = False
    try:
        raw_memo = await desc.memo()
        memo = dict(raw_memo) if raw_memo else {}
        memo_loaded = True
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

    from moonmind.workflows.temporal.workflow_registry import require_product_projection

    require_product_projection(desc.workflow_type)
    workflow_type = TemporalWorkflowType(desc.workflow_type)

    entry = str(
        memo.get("entry") or WORKFLOW_ENTRY_BY_TYPE.get(workflow_type, "user_workflow")
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

    mm_state = _coerce_mm_state(search_attributes)
    if mm_state is not None:
        if desc.status == WorkflowExecutionStatus.RUNNING:
            state_value = mm_state
        elif desc.status == WorkflowExecutionStatus.COMPLETED:
            domain_close_status = TERMINAL_DOMAIN_STATE_TO_CLOSE_STATUS.get(mm_state)
            if domain_close_status is not None:
                state_value = mm_state
                close_status = domain_close_status

    artifact_refs = memo.get("artifact_refs", [])
    if not isinstance(artifact_refs, list):
        artifact_refs = []

    waiting_reason = memo.get("waiting_reason")
    if not waiting_reason and state_value == MoonMindWorkflowState.AWAITING_EXTERNAL:
        waiting_reason = "external_completion"

    canonical_updated_at = _parse_temporal_datetime(
        search_attributes.get("mm_updated_at")
    )
    scheduled_for = _parse_temporal_datetime(
        search_attributes.get("mm_scheduled_for")
    )
    semantic_started_at = _parse_temporal_datetime(
        search_attributes.get("mm_started_at")
    )
    # Pre-work lifecycle states must not surface a started_at, even on the
    # legacy fallback path. Once the workflow is running real work, the
    # workflow stamps mm_started_at; that value wins for all current
    # workflows. Older in-flight workflows that pre-date the search attribute
    # fall back to the legacy Temporal lifecycle timestamps.
    if semantic_started_at is not None:
        started_at = semantic_started_at
    elif state_value in PRE_WORK_STATES:
        started_at = None
    else:
        started_at = desc.execution_time or desc.start_time
        if scheduled_for is not None:
            if started_at is not None and started_at < scheduled_for:
                started_at = scheduled_for
    sanitized_memo = _sanitize_for_json(dict(memo))
    finish_summary = _finish_summary_from_memo(sanitized_memo)
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
        "finish_outcome_code": _finish_outcome_code_from_summary(finish_summary),
        "finish_summary_json": finish_summary,
        "input_ref": _artifact_ref_from_memo(
            memo,
            "input_ref",
            "input_artifact_ref",
            "inputArtifactRef",
        ),
        "plan_ref": _artifact_ref_from_memo(
            memo,
            "plan_ref",
            "plan_artifact_ref",
            "planArtifactRef",
        ),
        "manifest_ref": _artifact_ref_from_memo(
            memo,
            "manifest_ref",
            "manifest_artifact_ref",
            "manifestArtifactRef",
        ),
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
        "created_at": desc.start_time,
        "started_at": started_at,
        "updated_at": canonical_updated_at,
        "closed_at": desc.close_time,
        "scheduled_for": scheduled_for,
        "_temporal_memo_loaded": memo_loaded,
    }

# DB-owned memo fields may never be rolled back by a Temporal memo snapshot.
_SNAPSHOT_MEMO_FIELDS = frozenset({
    "task_input_snapshot_ref", "task_input_snapshot_version",
    "task_input_snapshot_source_kind",
})
_BINDING_MEMO_FIELDS = frozenset({
    "omnigent_runtime_binding_ref", "omnigent_runtime_binding_revision",
    "omnigent_runtime_binding_fencing_generation", "omnigent_runtime_binding_state",
})


def _merge_owned_memo(incoming: dict[str, Any], records: list[Any], *, owner: str) -> dict[str, Any]:
    stored = [dict(record.memo or {}) for record in records]
    memo: dict[str, Any] = {}
    if owner != "canonical":
        for item in reversed(stored):
            memo.update(item)
    memo.update(incoming)
    if owner == "snapshot":
        for key in _SNAPSHOT_MEMO_FIELDS:
            for item in stored:
                if key in incoming and item.get(key) not in (None, incoming[key]):
                    raise ValueError("execution snapshot identity is immutable")
    if owner != "snapshot":
        for key in _SNAPSHOT_MEMO_FIELDS:
            for item in stored:
                if key in item:
                    memo[key] = item[key]
                    break
    bindings = [item for item in stored if item.get("omnigent_runtime_binding_revision") is not None]
    if owner == "runtime_binding":
        revision = int(incoming["omnigent_runtime_binding_revision"])
        for item in bindings:
            previous = int(item["omnigent_runtime_binding_revision"])
            if previous > revision:
                raise ValueError("execution runtime-binding projection is ahead of authority")
            if previous == revision and item.get("omnigent_runtime_binding_ref") not in (None, incoming["omnigent_runtime_binding_ref"]):
                raise ValueError("execution has conflicting runtime binding at same revision")
    elif bindings:
        binding = max(bindings, key=lambda item: int(item["omnigent_runtime_binding_revision"]))
        for key in _BINDING_MEMO_FIELDS:
            if key in binding:
                memo[key] = binding[key]
    else:
        # Only the binding owner can introduce authoritative binding identity.
        for key in _BINDING_MEMO_FIELDS:
            memo.pop(key, None)
    return memo


def _semantic_time(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=UTC) if value is not None and value.tzinfo is None else value


def _projection_semantic_time(updated_at, search_attributes) -> datetime | None:
    # API lifecycle decisions stamp mm_updated_at with full precision. A later
    # metadata-only ORM write must not regress that time to a DB clock's lower
    # precision (notably SQLite's second-resolution onupdate clock).
    candidates = [
        _semantic_time(updated_at),
        _parse_temporal_datetime((search_attributes or {}).get("mm_updated_at")),
    ]
    return max((value for value in candidates if value is not None), default=None)


async def mutate_execution_projection(
    session: AsyncSession,
    *,
    workflow_id: str,
    payload: dict[str, Any],
    owner: str,
    synced_at: datetime | None = None,
    metadata_loaded: bool = True,
) -> TemporalExecutionRecord | None:
    """Apply one owner-scoped mutation under canonical-then-projection row locks.

    Temporal owns lifecycle order (semantic updated_at, terminal close evidence).
    Canonical writes supply API fields. Snapshot and binding owners only patch
    their memo keys and artifact refs. Reconciliation repairs either projection
    divergence or absence; caller owns commit/retry of the transaction. No writer
    uses wall-clock sync time as evidence that workflow state became newer.
    """
    if owner not in {"temporal", "canonical", "snapshot", "runtime_binding"}:
        raise ValueError("unknown execution projection mutation owner")
    await session.flush()
    canonical = await session.get(TemporalExecutionCanonicalRecord, workflow_id, with_for_update=True, populate_existing=True)
    projection = await session.get(TemporalExecutionRecord, workflow_id, with_for_update=True, populate_existing=True)
    records = [record for record in (canonical, projection) if record is not None]
    incoming = dict(payload)
    if owner == "canonical":
        incoming["updated_at"] = _projection_semantic_time(
            incoming.get("updated_at"), incoming.get("search_attributes")
        )
    patch_only = owner in {"snapshot", "runtime_binding"}
    allowed_memo = _SNAPSHOT_MEMO_FIELDS if owner == "snapshot" else _BINDING_MEMO_FIELDS
    if patch_only:
        if set(incoming) - {"memo", "artifact_refs"} or set(incoming.get("memo") or {}) - allowed_memo:
            raise ValueError("projection mutation exceeds field ownership")
        if not records:
            raise ValueError("projection mutation has no execution owner")
    else:
        from moonmind.workflows.temporal.workflow_registry import require_product_projection
        require_product_projection(incoming.get("workflow_type"))

    # Select the freshest stored lifecycle before reconciling both rows.
    latest = max(records, key=lambda row: _projection_semantic_time(row.updated_at, row.search_attributes) or datetime.min.replace(tzinfo=UTC)) if records else None
    if latest is not None:
        records = [latest, *(record for record in records if record is not latest)]
    stale = False
    if latest is not None and not patch_only:
        previous_time = _projection_semantic_time(latest.updated_at, latest.search_attributes)
        incoming_time = _semantic_time(incoming.get("updated_at"))
        stale = bool(previous_time and incoming_time and incoming_time < previous_time)
        # A stale RUNNING describe with no semantic timestamp cannot reopen a
        # terminal execution in the same run.
        stale = stale or bool(latest.close_status and not incoming.get("close_status") and latest.run_id == incoming.get("run_id"))
    if (patch_only or stale) and latest is not None:
        merged = {column.name: getattr(latest, column.name) for column in TemporalExecutionCanonicalRecord.__table__.columns if hasattr(TemporalExecutionRecord, column.name)}
    else:
        merged = dict(incoming)
    if latest is not None and (patch_only or stale or not metadata_loaded):
        if not patch_only and not stale:
            merged = {column.name: getattr(latest, column.name) for column in TemporalExecutionCanonicalRecord.__table__.columns if hasattr(TemporalExecutionRecord, column.name)}
            merged.update({key: value for key, value in incoming.items() if key in CORE_TEMPORAL_SYNC_FIELDS})
    merged["workflow_id"] = workflow_id
    merged["updated_at"] = (
        _projection_semantic_time(merged.get("updated_at"), merged.get("search_attributes"))
        or merged.get("started_at") or synced_at or _utc_now()
    )
    if owner != "canonical" or stale:
        preserve_local_only_fields(merged, *records)
    memo_input = {} if stale or not metadata_loaded else dict(incoming.get("memo") or {})
    merged["memo"] = _merge_owned_memo(
        memo_input, records, owner="temporal" if stale and owner == "canonical" else owner
    )
    refs = []
    for record in records:
        for ref in record.artifact_refs or []:
            if ref not in refs:
                refs.append(ref)
    for ref in incoming.get("artifact_refs") or []:
        if ref not in refs:
            refs.append(ref)
    merged["artifact_refs"] = refs
    params = {}
    for record in reversed(records):
        params.update(record.parameters or {})
    if not stale and not patch_only and metadata_loaded:
        params.update(incoming.get("parameters") or {})
    merged["parameters"] = (
        dict(incoming.get("parameters") or {})
        if owner == "canonical" and not stale
        else params
    )
    if projection is None:
        projection = TemporalExecutionRecord(**merged, projection_version=0)
        session.add(projection)
    # Snapshot and binding mutation also repair a missing projection from the
    # canonical source, without creating a second execution identity.
    for record in (canonical, projection):
        if record is None:
            continue
        for key, value in merged.items():
            if hasattr(type(record), key):
                setattr(record, key, value)
        if record in session:
            flag_modified(record, "updated_at")
    projection.projection_version = max(int(projection.projection_version or 0) + 1, 1)
    projection.last_synced_at = synced_at or _utc_now()
    projection.sync_state = TemporalExecutionProjectionSyncState.FRESH
    projection.sync_error = None
    projection.source_mode = TemporalExecutionProjectionSourceMode.TEMPORAL_AUTHORITATIVE
    return projection


async def sync_execution_projection(
    session: AsyncSession,
    desc: WorkflowExecutionDescription,
    synced_at: datetime | None = None,
) -> TemporalExecutionRecord:
    """Reconcile Temporal lifecycle evidence through the shared mutation owner."""
    payload = await map_temporal_state_to_projection(desc)
    loaded = bool(payload.pop("_temporal_memo_loaded", False)) and bool(payload.get("memo"))
    return await mutate_execution_projection(
        session, workflow_id=desc.id, payload=payload, owner="temporal",
        synced_at=synced_at, metadata_loaded=loaded,
    )

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

    updated_items = []
    for item in items:
        updated_items.append(await fetch_and_sync(item))
    await session.commit()
    for obj in updated_items:
        try:
            await session.refresh(obj)
        except Exception:
            pass  # fallback to potentially stale but accessible attributes
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
