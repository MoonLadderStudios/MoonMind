"""Bind a pool-selected grant once, before it has a runtime consumer."""

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any


def selected_pool_grant_error(
    row: Any, payload: dict[str, Any], *, runtime_id: str, now=None
):
    """Check the existing authority before reading or compiling its inputs."""
    required = ("owner_id", "profile_id", "runId")
    if any(not str(payload.get(key) or "").strip() for key in required):
        return "incomplete_identity"
    fence = payload.get("fencing_generation")
    if (
        row is None
        or str(row.runtime_id) != runtime_id
        or str(row.owner_id) != str(payload["owner_id"])
        or str(row.workflow_id) != str(payload["owner_id"])
        or str(row.profile_id) != str(payload["profile_id"])
        or type(fence) is not int
        or fence < 1
        or fence != row.fencing_generation
        or row.owner_is_workflow is not True
        or str(row.owner_kind or "") != "workflow"
        or str(row.purpose or "") != "execution_direct"
        or str(row.lease_state or "held") != "held"
        or row.oauth_session_id
        or str((row.safe_metadata_json or {}).get("runId") or "")
        != str(payload["runId"])
    ):
        return "grant_identity_conflict"
    expiry = row.expires_at
    if expiry is not None and expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    if expiry is None or expiry <= (now or datetime.now(timezone.utc)):
        return "grant_expired"
    return None


def bind_selected_pool_plan(
    row: Any, payload: dict[str, Any], *, runtime_id: str, now=None
):
    """Apply one fenced enrichment; the caller owns the database transaction."""
    reason = selected_pool_grant_error(row, payload, runtime_id=runtime_id, now=now)
    if reason:
        return {"bound": False, "reason": reason}
    required = ("executionPlanRef", "stepExecutionId", "idempotencyKey")
    if any(not str(payload.get(key) or "").strip() for key in required):
        return {"bound": False, "reason": "incomplete_identity"}
    generation = payload.get("credential_generation")
    if (
        type(generation) is not int
        or generation < 1
        or (
            row.credential_generation is not None
            and row.credential_generation != generation
        )
    ):
        return {"bound": False, "reason": "grant_identity_conflict"}
    selection = {
        "execution_plan_ref": str(payload["executionPlanRef"]),
        "step_execution_id": str(payload["stepExecutionId"]),
        "idempotency_key": str(payload["idempotencyKey"]),
    }
    for column, value in selection.items():
        existing = getattr(row, column, None)
        if existing is not None and str(existing) != value:
            return {"bound": False, "reason": "plan_identity_conflict"}
    prepared = payload.get("preparedPoolPlan")
    previous = (row.safe_metadata_json or {}).get("preparedPoolPlan")
    if prepared is not None and previous is not None and prepared != previous:
        return {"bound": False, "reason": "prepared_plan_conflict"}
    if row.execution_plan_ref is not None:
        if any(
            getattr(row, column, None) != value for column, value in selection.items()
        ):
            return {"bound": False, "reason": "plan_identity_conflict"}
        return {"bound": True, "duplicate": True}
    for column, value in selection.items():
        setattr(row, column, value)
    row.credential_generation = generation
    if prepared is not None:
        row.safe_metadata_json = {
            **(row.safe_metadata_json or {}),
            "preparedPoolPlan": deepcopy(prepared),
        }
    return {"bound": True, "duplicate": False}
