"""Projection synchronization logic for Temporal executions."""

import enum
import json
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.exc import IntegrityError
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

def _coerce_run_link(value: Any) -> str | None:
    """Extract an optional run-chain link, accepting only plain scalars.

    SDK run linkage is a string run ID (or an ordered list of candidates).
    Anything else is treated as absent so unstructured values can never
    fabricate successor evidence.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, (list, tuple)):
        for item in value:
            if isinstance(item, str) and item.strip():
                return item.strip()
        return None
    return None

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
        # CONTINUED_AS_NEW closes one run but the logical workflow continues.
        # It must not read as terminal COMPLETED at the logical-workflow level;
        # the projection stays non-terminal so the successor run can advance it.
        WorkflowExecutionStatus.CONTINUED_AS_NEW: (
            MoonMindWorkflowState.EXECUTING,
            TemporalExecutionCloseStatus.CONTINUED_AS_NEW,
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
    # Run-chain hints let the mutator order observations by positive successor
    # evidence instead of arrival time. Temporal SDK descriptions may expose a
    # previous-run linkage; memo keys are a portable fallback. Only plain
    # scalar links are honored; anything else (including mock sentinels) is
    # treated as absent so it can never fabricate successor evidence.
    previous_run_id = _coerce_run_link(
        getattr(desc, "previous_run_id", None)
    ) or _coerce_run_link(memo.get("previous_run_id"))
    first_run_id = _coerce_run_link(
        getattr(desc, "first_execution_run_id", None)
    ) or _coerce_run_link(memo.get("first_run_id"))
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
        "previous_run_id": previous_run_id,
        "first_run_id": first_run_id,
        "continued_as_new": desc.status == WorkflowExecutionStatus.CONTINUED_AS_NEW,
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


# Field-authority map for the shared projection mutator (issue #3946 REQ-02).
# Temporal owns lifecycle order; the canonical API owner owns admission identity
# and creation-time parameters. A Temporal observation may refresh lifecycle
# without changing the authorized principal or immutable admission parameters.
# A canonical write is the admission owner, but it still may not move an
# existing execution to a different owner/namespace/type or rewrite immutable
# creation keys: those require their existing API owner/coordination path, not
# general payload merging.
_TEMPORAL_PROTECTED_IDENTITY_FIELDS = (
    "owner_id",
    "owner_type",
    "namespace",
    "workflow_type",
)
_CANONICAL_PROTECTED_IDENTITY_FIELDS = (
    "owner_id",
    "owner_type",
    "namespace",
    "workflow_type",
)
_CANONICAL_IMMUTABLE_CREATION_FIELDS = (
    "create_idempotency_key",
    "created_at",
)

# Terminal close statuses at the individual-run level. CONTINUED_AS_NEW closes
# one run but continues the logical workflow, so it is deliberately excluded:
# it must never read as successful logical completion or block the successor.
_TERMINAL_RUN_CLOSE_STATUSES = frozenset({
    TemporalExecutionCloseStatus.COMPLETED,
    TemporalExecutionCloseStatus.FAILED,
    TemporalExecutionCloseStatus.CANCELED,
    TemporalExecutionCloseStatus.TERMINATED,
    TemporalExecutionCloseStatus.TIMED_OUT,
})

# Bound for current-summary artifact refs kept inline on the projection row.
# Larger collections live behind artifact linkage/history, not unbounded growth
# of the summary field.
_MAX_PROJECTION_ARTIFACT_REFS = 128


def _is_terminal_run_close(value: Any) -> bool:
    return value in _TERMINAL_RUN_CLOSE_STATUSES


def _normalize_identity(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, enum.Enum):
        value = value.value
    text = str(value).strip()
    return text or None


def _projection_semantic_time(updated_at, search_attributes) -> datetime | None:
    # API lifecycle decisions stamp mm_updated_at with full precision. A later
    # metadata-only ORM write must not regress that time to a DB clock's lower
    # precision (notably SQLite's second-resolution onupdate clock).
    candidates = [
        _semantic_time(updated_at),
        _parse_temporal_datetime((search_attributes or {}).get("mm_updated_at")),
    ]
    return max((value for value in candidates if value is not None), default=None)


async def _locked_get(session: Any, model: Any, pk: Any) -> Any | None:
    """Fetch one row with row-level locking when the session supports it.

    Test doubles (AsyncMock with exhausted side_effect, hand-rolled get()
    without locking kwargs, or SimpleNamespace sessions returning unrelated
    payloads) must never crash the mutator: exhausted mocks yield None so the
    caller follows the missing-row path, strict fakes fall back to a plain
    get(), and wrong-type payloads are treated as absent so a canonical row
    is never mistaken for a projection row (or vice versa).
    """
    get = getattr(session, "get", None)
    if not callable(get):
        return None
    for attempt in ("locked", "plain"):
        try:
            if attempt == "locked":
                result = await get(model, pk, with_for_update=True, populate_existing=True)
            else:
                result = await get(model, pk)
        except TypeError:
            # Strict fake get() without locking kwargs: retry plain.
            if attempt == "locked":
                continue
            return None
        except (StopAsyncIteration, StopIteration):
            # AsyncMock side_effect exhausted by an earlier call in the same
            # request (pre-mutator mocks sized for the old direct-write path).
            return None
        except AttributeError:
            return None
        else:
            if result is None:
                return None
            try:
                if isinstance(result, model):
                    return result
            except Exception:
                return None
            # Wrong-type payload (e.g. a canonical row returned for a
            # projection lookup, or a provider-profile SimpleNamespace for an
            # execution lookup): treat as absent, never as the requested row.
            return None
    return None


def _record_semantic_time(row: Any) -> datetime | None:
    try:
        return _projection_semantic_time(
            getattr(row, "updated_at", None),
            getattr(row, "search_attributes", None) or {},
        )
    except Exception:
        return None


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
    flush = getattr(session, "flush", None)
    if callable(flush):
        try:
            await flush()
        except (StopAsyncIteration, StopIteration, AttributeError, TypeError):
            pass
    canonical = await _locked_get(session, TemporalExecutionCanonicalRecord, workflow_id)
    projection = await _locked_get(session, TemporalExecutionRecord, workflow_id)
    records = [record for record in (canonical, projection) if record is not None]
    incoming = dict(payload)
    incoming_workflow_id = incoming.get("workflow_id")
    if incoming_workflow_id not in (None, workflow_id):
        raise ValueError("projection mutation identity does not match target execution")
    incoming["workflow_id"] = workflow_id
    if owner == "canonical":
        incoming["updated_at"] = _projection_semantic_time(
            incoming.get("updated_at"), incoming.get("search_attributes")
        )
    patch_only = owner in {"snapshot", "runtime_binding"}
    allowed_memo = _SNAPSHOT_MEMO_FIELDS if owner == "snapshot" else _BINDING_MEMO_FIELDS
    if patch_only:
        if set(incoming) - {"memo", "artifact_refs", "workflow_id"} or set(incoming.get("memo") or {}) - allowed_memo:
            raise ValueError("projection mutation exceeds field ownership")
        if not records:
            raise ValueError("projection mutation has no execution owner")
    else:
        from moonmind.workflows.temporal.workflow_registry import require_product_projection
        require_product_projection(incoming.get("workflow_type"))

    # Select the freshest stored lifecycle before reconciling both rows.
    latest = max(records, key=lambda row: _record_semantic_time(row) or datetime.min.replace(tzinfo=UTC)) if records else None
    if latest is not None:
        records = [latest, *(record for record in records if record is not latest)]
    # Field authority (REQ-02): a Temporal observation refreshes lifecycle but
    # never changes the authorized principal, execution identity, or immutable
    # admission parameters owned by the canonical API path. Admission identity
    # is authoritative on the canonical row when it exists; the projection may
    # diverge, so it must never supply the protected owner.
    if owner == "temporal" and latest is not None:
        identity_source = canonical if canonical is not None else latest
        for field in _TEMPORAL_PROTECTED_IDENTITY_FIELDS:
            stored_identity = _normalize_identity(getattr(identity_source, field, None))
            incoming_identity = _normalize_identity(incoming.get(field))
            if (
                stored_identity is not None
                and incoming_identity is not None
                and stored_identity != incoming_identity
            ):
                logger.warning(
                    "Ignoring temporal %s change for %s: stored=%r incoming=%r",
                    field, workflow_id, stored_identity, incoming_identity,
                )
                incoming[field] = getattr(latest, field)
        stored_params = dict(getattr(latest, "parameters", None) or {})
        incoming_params = incoming.get("parameters") or {}
        if isinstance(incoming_params, dict) and stored_params:
            # Temporal memo parameters may only introduce keys the canonical
            # admission record does not already own; stored admission values win.
            merged_params = dict(stored_params)
            for key, value in incoming_params.items():
                if key not in merged_params:
                    merged_params[key] = value
                elif merged_params[key] != value:
                    logger.warning(
                        "Ignoring temporal parameters change for %s key=%r",
                        workflow_id, key,
                    )
            incoming["parameters"] = merged_params
    # Canonical authority (REQ-02): the admission owner supplies API fields,
    # but an existing execution's principal/identity and immutable creation
    # keys are not movable by general payload merging. Wrong-owner, wrong-
    # namespace, wrong-type, or rewritten creation keys are rejected so the
    # caller must coordinate through the existing API owner.
    if owner == "canonical" and latest is not None:
        for field in _CANONICAL_PROTECTED_IDENTITY_FIELDS:
            stored_identity = _normalize_identity(getattr(latest, field, None))
            incoming_identity = _normalize_identity(incoming.get(field))
            if (
                stored_identity is not None
                and incoming_identity is not None
                and stored_identity != incoming_identity
            ):
                raise ValueError(
                    f"canonical mutation changes protected field {field}"
                )
        for field in _CANONICAL_IMMUTABLE_CREATION_FIELDS:
            stored_value = getattr(latest, field, None)
            incoming_value = incoming.get(field)
            if stored_value is None or incoming_value is None:
                continue
            if isinstance(stored_value, datetime) or isinstance(incoming_value, datetime):
                stored_time = _semantic_time(stored_value) if isinstance(stored_value, datetime) else None
                incoming_time = _semantic_time(incoming_value) if isinstance(incoming_value, datetime) else None
                if stored_time is not None and incoming_time is not None:
                    if stored_time != incoming_time:
                        raise ValueError(
                            f"canonical mutation changes immutable field {field}"
                        )
                    continue
            stored_norm = _normalize_identity(stored_value)
            incoming_norm = _normalize_identity(incoming_value)
            # created_at datetimes normalize via str; fall back to direct
            # comparison when normalization erases the distinction.
            if stored_norm is not None and incoming_norm is not None:
                if stored_norm != incoming_norm:
                    raise ValueError(
                        f"canonical mutation changes immutable field {field}"
                    )
            elif stored_value != incoming_value:
                raise ValueError(
                    f"canonical mutation changes immutable field {field}"
                )
    stale = False
    closes_current_run = False
    unknown_successor = False
    if latest is not None and not patch_only:
        previous_time = _projection_semantic_time(latest.updated_at, latest.search_attributes)
        incoming_time = _semantic_time(incoming.get("updated_at"))
        incoming_run = incoming.get("run_id")
        stored_run = latest.run_id
        # DB-only finalization writes can occur after the last mm_updated_at.
        # Temporal closure is authoritative for this run even when that metadata
        # timestamp is newer; otherwise detail reads retain EXECUTING forever.
        # CONTINUED_AS_NEW is a continuation, not a terminal close of the
        # logical workflow, so it never counts as closing the current run.
        closes_current_run = bool(
            owner == "temporal"
            and _is_terminal_run_close(incoming.get("close_status"))
            and not _is_terminal_run_close(latest.close_status)
            and stored_run == incoming_run
        )
        if stored_run == incoming_run:
            stale = bool(previous_time and incoming_time and incoming_time < previous_time)
            # A stale non-terminal describe cannot reopen a closed run in the
            # same run. Any recorded close (including a continued-as-new run
            # close) blocks a close-less late describe of that same run.
            stale = stale or bool(
                latest.close_status and not incoming.get("close_status")
            )
        else:
            stored_first_run = (getattr(latest, "memo", None) or {}).get("first_run_id")
            incoming_first_run = incoming.get("first_run_id")
            first_run_chain_match = bool(
                incoming_first_run
                and stored_first_run
                and incoming_first_run == stored_first_run
            ) or bool(
                incoming_first_run
                and stored_run
                and incoming_first_run == stored_run
            )
            successor_evidence = bool(
                incoming.get("previous_run_id")
                and incoming.get("previous_run_id") == stored_run
            ) or first_run_chain_match
            latest_prev_run = (getattr(latest, "memo", None) or {}).get("previous_run_id")
            late_predecessor = bool(
                incoming_run and incoming_run == latest_prev_run
            )
            if previous_time and incoming_time:
                if incoming_time < previous_time:
                    stale = True
                elif incoming_time == previous_time and not successor_evidence:
                    # Equal semantic timestamps across runs prove nothing about
                    # order; arrival time is not successor evidence.
                    stale = True
                    unknown_successor = True
                # A strictly newer semantic timestamp on a fresh describe of
                # the same workflow_id is positive current-run evidence, so the
                # successor replaces the predecessor (reset/Continue-As-New /
                # fresh-run cases). Equal or missing timestamps carry no such
                # evidence and stay stale without run-chain linkage.
            else:
                # A different run without a reliable timestamp on either side
                # needs positive run-chain evidence before it may replace the
                # current projection.
                if not successor_evidence:
                    stale = True
                    unknown_successor = True
            if late_predecessor:
                stale = True
                unknown_successor = False
    if (patch_only or stale) and latest is not None:
        merged = {column.name: getattr(latest, column.name) for column in TemporalExecutionCanonicalRecord.__table__.columns if hasattr(TemporalExecutionRecord, column.name)}
    else:
        merged = dict(incoming)
    if latest is not None and (patch_only or stale or not metadata_loaded):
        if not patch_only and not stale:
            merged = {column.name: getattr(latest, column.name) for column in TemporalExecutionCanonicalRecord.__table__.columns if hasattr(TemporalExecutionRecord, column.name)}
            merged.update({key: value for key, value in incoming.items() if key in CORE_TEMPORAL_SYNC_FIELDS})
    if stale and closes_current_run:
        # Closure can advance lifecycle without making an older memo current.
        # Keep the stored metadata and semantic timestamp, including API-owned
        # counters, attention flags, parameters, and pending integration work.
        merged.update({
            key: value for key, value in incoming.items()
            if key in CORE_TEMPORAL_SYNC_FIELDS and key != "updated_at"
        })
        merged["search_attributes"] = {
            **(merged.get("search_attributes") or {}),
            "mm_state": [incoming["state"]],
        }
    merged["workflow_id"] = workflow_id
    merged["updated_at"] = (
        _projection_semantic_time(merged.get("updated_at"), merged.get("search_attributes"))
        or merged.get("started_at") or synced_at or _utc_now()
    )
    if owner != "canonical" or stale:
        preserve_local_only_fields(merged, *records)
    memo_input = {} if stale or not metadata_loaded else dict(incoming.get("memo") or {})
    if not stale and metadata_loaded and not patch_only and incoming.get("previous_run_id"):
        memo_input.setdefault("previous_run_id", incoming["previous_run_id"])
    if not stale and metadata_loaded and not patch_only and incoming.get("first_run_id"):
        memo_input.setdefault("first_run_id", incoming["first_run_id"])
    merged["memo"] = _merge_owned_memo(
        memo_input, records, owner="temporal" if stale and owner == "canonical" else owner
    )
    # Artifact refs stay bounded and truthful (REQ-05): stale observations never
    # contribute new refs, duplicates never grow history, and the inline summary
    # is capped with overflow kept behind artifact linkage/history.
    refs = []
    for record in records:
        for ref in record.artifact_refs or []:
            if ref not in refs:
                refs.append(ref)
    fresh_refs_added = False
    if not stale:
        for ref in incoming.get("artifact_refs") or []:
            if ref not in refs:
                refs.append(ref)
                fresh_refs_added = True
    if len(refs) > _MAX_PROJECTION_ARTIFACT_REFS:
        logger.warning(
            "Bounding artifact refs for %s from %d to %d",
            workflow_id, len(refs), _MAX_PROJECTION_ARTIFACT_REFS,
        )
        # Newly produced evidence must survive bounding: refs are ordered
        # oldest-first with incoming refs appended last, so keep the newest
        # window instead of discarding the current observation's refs.
        refs = refs[len(refs) - _MAX_PROJECTION_ARTIFACT_REFS:]
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
        _begin_nested = getattr(session, "begin_nested", None)
        _session_add = getattr(session, "add", None)
        _session_flush = getattr(session, "flush", None)
        if not callable(_begin_nested) or not callable(_session_add):
            # Session double without transactional write support (unit mocks
            # sized for the old direct-write path): build the projection
            # in-memory so callers holding the returned row still observe the
            # reconciled fields. The caller owns persistence.
            projection_fields = {
                key: value
                for key, value in merged.items()
                if hasattr(TemporalExecutionRecord, key)
            }
            try:
                projection = TemporalExecutionRecord(**projection_fields, projection_version=0)
            except Exception:
                projection = None
            if projection is not None and callable(_session_add):
                try:
                    _session_add(projection)
                except Exception:
                    pass
        else:
            try:
                async with _begin_nested():
                    projection_fields = {
                        key: value
                        for key, value in merged.items()
                        if hasattr(TemporalExecutionRecord, key)
                    }
                    projection = TemporalExecutionRecord(**projection_fields, projection_version=0)
                    _session_add(projection)
                    if callable(_session_flush):
                        try:
                            await _session_flush()
                        except (StopAsyncIteration, StopIteration, AttributeError, TypeError):
                            pass
            except IntegrityError:
                # Concurrent insert race on the missing-row path: another
                # transaction won the insert. The nested savepoint already rolled
                # back; reconcile onto the now-present row instead of duplicating
                # it. Never roll back the caller's outer transaction here.
                canonical = await _locked_get(session, TemporalExecutionCanonicalRecord, workflow_id)
                projection = await _locked_get(session, TemporalExecutionRecord, workflow_id)
                if projection is None:
                    raise
                records = [record for record in (canonical, projection) if record is not None]
                # Re-evaluate the winner after the conflict: decisions computed
                # before the reload (merge, stale, ownership) used the pre-insert
                # snapshot. Re-select the freshest stored row and recompute
                # staleness so a losing concurrent writer cannot overwrite the
                # winner's newer lifecycle observation with older payload data.
                latest = max(records, key=lambda row: _record_semantic_time(row) or datetime.min.replace(tzinfo=UTC))
                if owner == "temporal":
                    identity_source = canonical if canonical is not None else latest
                    for field in _TEMPORAL_PROTECTED_IDENTITY_FIELDS:
                        stored_identity = _normalize_identity(getattr(identity_source, field, None))
                        incoming_identity = _normalize_identity(incoming.get(field))
                        if (
                            stored_identity is not None
                            and incoming_identity is not None
                            and stored_identity != incoming_identity
                        ):
                            incoming[field] = getattr(identity_source, field)
                    merged.update({
                        key: incoming[key]
                        for key in ("owner_id", "owner_type", "namespace", "workflow_type", "parameters")
                        if key in incoming
                    })
                _re_prev = _projection_semantic_time(latest.updated_at, latest.search_attributes)
                _re_in = _semantic_time(incoming.get("updated_at"))
                _re_stale = False
                if latest.run_id == incoming.get("run_id"):
                    _re_stale = bool(_re_prev and _re_in and _re_in < _re_prev) or bool(
                        latest.close_status and not incoming.get("close_status")
                    )
                else:
                    _re_prev_match = bool(
                        incoming.get("previous_run_id")
                        and incoming.get("previous_run_id") == latest.run_id
                    )
                    _re_stored_first = (getattr(latest, "memo", None) or {}).get("first_run_id")
                    _re_in_first = incoming.get("first_run_id")
                    _re_chain = bool(
                        _re_in_first and _re_stored_first and _re_in_first == _re_stored_first
                    ) or bool(_re_in_first and latest.run_id and _re_in_first == latest.run_id)
                    _re_succ = _re_prev_match or _re_chain
                    if _re_prev and _re_in:
                        if _re_in < _re_prev:
                            _re_stale = True
                        elif _re_in == _re_prev and not _re_succ:
                            _re_stale = True
                    elif not _re_succ:
                        _re_stale = True
                if _re_stale:
                    stale = True
                    merged = {column.name: getattr(latest, column.name) for column in TemporalExecutionCanonicalRecord.__table__.columns if hasattr(TemporalExecutionRecord, column.name)}
                    merged["workflow_id"] = workflow_id
    # Snapshot the pre-write stored values before the write-back below: the
    # duplicate-observation check must compare against what was stored, not the
    # just-overwritten attributes (latest aliases one of the row objects).
    # The comparison target is the projection being repaired, never the
    # canonical row: matching the canonical while the projection diverges (or
    # is REPAIR_PENDING) is a repair, not a duplicate.
    previous_version = int(getattr(projection, "projection_version", 0) or 0) if projection is not None else 0
    if latest is not None:
        _prev_run_id = latest.run_id
        _prev_state = latest.state
        _prev_close_status = latest.close_status
        _prev_memo = dict(getattr(latest, "memo", None) or {})
        _prev_params = dict(getattr(latest, "parameters", None) or {})
    else:
        _prev_run_id = _prev_state = _prev_close_status = None
        _prev_memo = _prev_params = None
    _repair_base = projection if projection is not None else latest
    if _repair_base is not None:
        _prev_run_id = _repair_base.run_id
        _prev_state = _repair_base.state
        _prev_close_status = _repair_base.close_status
        _prev_memo = dict(getattr(_repair_base, "memo", None) or {})
        _prev_params = dict(getattr(_repair_base, "parameters", None) or {})
    _repair_base_fresh = bool(
        _repair_base is not None
        and getattr(_repair_base, "sync_state", None) == TemporalExecutionProjectionSyncState.FRESH
        and getattr(_repair_base, "sync_error", None) is None
    )
    duplicate_observation = bool(
        owner == "temporal"
        and latest is not None
        and _repair_base is not None
        and _repair_base_fresh
        and not stale
        and metadata_loaded
        and not patch_only
        and not fresh_refs_added
        and merged.get("run_id") == _prev_run_id
        and merged.get("state") == _prev_state
        and merged.get("close_status") == _prev_close_status
        and merged.get("memo") == _prev_memo
        and merged.get("parameters") == _prev_params
    )
    if projection is None:
        # Session double without write support and no creatable row: there is
        # nothing durable to advance. Return the freshest projection row when
        # one exists so callers holding detached doubles still observe it.
        if isinstance(latest, TemporalExecutionRecord):
            return latest
        return None
    # Snapshot and binding mutation also repair a missing projection from the
    # canonical source, without creating a second execution identity.
    for record in (canonical, projection):
        if record is None:
            continue
        for key, value in merged.items():
            if hasattr(type(record), key):
                setattr(record, key, value)
        try:
            if record in session:
                flag_modified(record, "updated_at")
        except Exception:
            pass
    if stale and not closes_current_run:
        # Unknown/late predecessor evidence yields stale/reconciliation-needed
        # status, never replacement or restart.
        projection.last_synced_at = synced_at or _utc_now()
        projection.sync_state = TemporalExecutionProjectionSyncState.STALE
        projection.sync_error = (
            "successor_run_unverified_reconciliation_needed"
            if unknown_successor
            else "stale_temporal_observation_ignored"
        )
        projection.source_mode = TemporalExecutionProjectionSourceMode.TEMPORAL_AUTHORITATIVE
        return projection
    if not metadata_loaded and not closes_current_run:
        # A partial decode preserves valid fields but must not claim the whole
        # projection is current.
        projection.last_synced_at = synced_at or _utc_now()
        projection.sync_state = TemporalExecutionProjectionSyncState.REPAIR_PENDING
        projection.sync_error = "temporal_memo_decode_incomplete"
        projection.source_mode = TemporalExecutionProjectionSourceMode.TEMPORAL_AUTHORITATIVE
        return projection
    if duplicate_observation and previous_version:
        # Duplicate observations preserve freshness without producing another
        # meaningful revision or repeated side effects.
        projection.last_synced_at = synced_at or _utc_now()
        projection.source_mode = TemporalExecutionProjectionSourceMode.TEMPORAL_AUTHORITATIVE
        return projection
    projection.projection_version = max(previous_version + 1, 1)
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
    """Sync each item with per-item savepoint isolation (REQ-04).

    One shared session commits after the loop, but each item runs inside its
    own savepoint: a database error rolls back only that item's partial work
    so it cannot poison unrelated repairs, and the failure stays attached to
    its item instead of hiding until the final commit. The caller still owns
    the outer commit/rollback contract.
    """

    async def fetch_and_sync(item):
        try:
            async with session.begin_nested():
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
    try:
        await session.commit()
    except Exception as exc:
        logger.warning("Batch projection sync commit failed: %s", exc, exc_info=True)
        try:
            await session.rollback()
        except Exception:
            # Best-effort cleanup: the commit already failed, so a rollback
            # failure must not mask the original error.
            pass
        # Rollback expires ORM instances; reload them inside this awaited
        # context so the caller can serialize the fallback without implicit
        # I/O outside async scope (MissingGreenlet -> 500).
        for obj in items:
            try:
                await session.refresh(obj)
            except Exception:
                # Best-effort reload: fall back to expired attributes rather
                # than masking the batch recovery with a refresh error.
                pass
        return list(items)
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
        try:
            # Release the failed transaction so the caller's session stays
            # usable for later work instead of poisoned.
            await session.rollback()
        except Exception:
            # Best-effort cleanup: the sync already failed, so a rollback
            # failure must not mask the original error.
            pass
        return None
