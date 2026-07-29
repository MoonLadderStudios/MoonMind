"""Server-owned synchronization and validation for Omnigent agent profiles."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db.models import OmnigentUpstreamAgentProjection

_SUPPORTED_HARNESSES = {"codex-native"}
_MAX_INVENTORY = 500
_INVENTORY_FRESHNESS_TTL = timedelta(minutes=5)
_METADATA_TEXT_LIMIT = 512
_METADATA_LIST_LIMIT = 64


def projection_identity(endpoint_ref: str, upstream_id: str, version: str | None) -> str:
    """Build a bounded stable key without trusting a display name."""
    raw = json.dumps(
        [endpoint_ref, upstream_id, version or ""],
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()
    return "upstream:" + hashlib.sha256(raw).hexdigest()


def projection_readiness(
    projection: OmnigentUpstreamAgentProjection | None,
    *,
    now: datetime | None = None,
    bridge_mode: str | None = None,
    harness: str | None = None,
    required_capabilities: Sequence[str] = (),
) -> dict[str, Any]:
    """Return one explicit, server-owned launch readiness classification."""
    if projection is None:
        return {
            "ready": False,
            "freshness": "missing",
            "reason": "stable upstream identity has not been synchronized",
        }

    observed_at = now or datetime.now(timezone.utc)
    last_success = projection.last_successful_sync_at
    if last_success is not None and last_success.tzinfo is None:
        last_success = last_success.replace(tzinfo=timezone.utc)
    stale = (
        last_success is None
        or observed_at - last_success > _INVENTORY_FRESHNESS_TTL
        or projection.error is not None
    )
    metadata = projection.metadata_snapshot
    projected_harness = _text(metadata, "harness", "harnessId", "harness_id")
    projected_capabilities = metadata.get("capabilities", [])
    capability_values = (
        {str(value) for value in projected_capabilities}
        if isinstance(projected_capabilities, list)
        else set()
    )
    contract_mismatch = (
        (bridge_mode is not None and projection.bridge_mode != bridge_mode)
        or (harness is not None and projected_harness != harness)
        or not set(required_capabilities).issubset(capability_values)
    )
    if not projection.available:
        reason = "stable upstream identity is unavailable"
    elif not projection.compatible:
        reason = "stable upstream identity is incompatible"
    elif contract_mismatch:
        reason = "upstream metadata does not satisfy the requested profile contract"
    elif stale:
        reason = "upstream inventory is stale"
    else:
        reason = None
    return {
        "ready": reason is None,
        "freshness": "stale" if stale else "fresh",
        "reason": reason,
        "lastSuccessfulSyncAt": last_success.isoformat() if last_success else None,
        "lastAttemptAt": (
            projection.last_attempt_at.isoformat()
            if projection.last_attempt_at
            else None
        ),
    }


def _text(row: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _bounded_metadata(item: Mapping[str, Any]) -> dict[str, Any]:
    """Retain only compact, non-authoritative compatibility evidence."""
    result: dict[str, Any] = {}
    for target, keys in {
        "id": ("id", "agentId", "agent_id"),
        "version": ("version", "agentVersion", "agent_version"),
        "harness": ("harness", "harnessId", "harness_id"),
        "name": ("name", "displayName"),
        "provenance": ("provenance",),
        "health": ("health", "status"),
    }.items():
        value = _text(item, *keys)
        if value:
            result[target] = value[:_METADATA_TEXT_LIMIT]
    capabilities = item.get("capabilities")
    if isinstance(capabilities, list):
        result["capabilities"] = sorted({
            str(value)[:_METADATA_TEXT_LIMIT]
            for value in capabilities[:_METADATA_LIST_LIMIT]
            if isinstance(value, str) and value
        })
    return result


async def synchronize_upstream_inventory(
    session: AsyncSession,
    *,
    endpoint_ref: str,
    bridge_mode: str,
    inventory: Sequence[Mapping[str, Any]],
    now: datetime | None = None,
) -> int:
    """Upsert one bounded last-known projection and mark disappearances unavailable."""
    observed_at = now or datetime.now(timezone.utc)
    rows = list(inventory[:_MAX_INVENTORY])
    seen: set[str] = set()
    for item in rows:
        upstream_id = _text(item, "id", "agentId", "agent_id")
        if not upstream_id:
            continue
        version = _text(item, "version", "agentVersion", "agent_version") or None
        projection_id = projection_identity(endpoint_ref, upstream_id, version)
        seen.add(projection_id)
        harness = _text(item, "harness", "harnessId", "harness_id")
        capabilities = item.get("capabilities")
        capability_values = (
            {str(value) for value in capabilities}
            if isinstance(capabilities, list)
            else set()
        )
        compatible = harness in _SUPPORTED_HARNESSES or (
            not harness and "codex-native" in capability_values
        )
        projection = await session.get(OmnigentUpstreamAgentProjection, projection_id)
        if projection is None:
            projection = OmnigentUpstreamAgentProjection(
                projection_id=projection_id,
                endpoint_ref=endpoint_ref,
                bridge_mode=bridge_mode,
                upstream_id=upstream_id,
                upstream_version=version,
                metadata_snapshot=_bounded_metadata(item),
                available=True,
                compatible=compatible,
                last_successful_sync_at=observed_at,
                last_attempt_at=observed_at,
            )
            session.add(projection)
        else:
            projection.metadata_snapshot = _bounded_metadata(item)
            projection.available = True
            projection.compatible = compatible
            projection.last_successful_sync_at = observed_at
            projection.last_attempt_at = observed_at
            projection.error = None

    existing = list((await session.execute(
        select(OmnigentUpstreamAgentProjection).where(
            OmnigentUpstreamAgentProjection.endpoint_ref == endpoint_ref,
            OmnigentUpstreamAgentProjection.bridge_mode == bridge_mode,
        )
    )).scalars())
    for projection in existing:
        if len(inventory) <= _MAX_INVENTORY and projection.projection_id not in seen:
            projection.available = False
            projection.last_attempt_at = observed_at
            projection.error = "upstream identity absent from latest successful sync"
    await session.commit()
    return len(seen)


async def record_upstream_sync_failure(
    session: AsyncSession,
    *,
    endpoint_ref: str,
    bridge_mode: str,
    error: str,
    now: datetime | None = None,
) -> int:
    """Retain last-known metadata while explicitly recording stale error state.

    Returns the number of retained projections marked with the failure so the
    caller can report a bounded, observable degraded outcome.
    """
    attempted_at = now or datetime.now(timezone.utc)
    safe_error = error.replace("\n", " ")[:512]
    rows = list((await session.execute(
        select(OmnigentUpstreamAgentProjection).where(
            OmnigentUpstreamAgentProjection.endpoint_ref == endpoint_ref,
            OmnigentUpstreamAgentProjection.bridge_mode == bridge_mode,
        )
    )).scalars())
    for projection in rows:
        projection.last_attempt_at = attempted_at
        projection.error = safe_error
    await session.commit()
    return len(rows)


async def _count_projections(
    session: AsyncSession, endpoint_ref: str, bridge_mode: str
) -> int:
    return int((await session.scalar(
        select(func.count())
        .select_from(OmnigentUpstreamAgentProjection)
        .where(
            OmnigentUpstreamAgentProjection.endpoint_ref == endpoint_ref,
            OmnigentUpstreamAgentProjection.bridge_mode == bridge_mode,
        )
    )) or 0)


async def synchronize_endpoint_inventory(
    session: AsyncSession,
    *,
    endpoint_ref: str,
    bridge_mode: str,
    list_agents: Callable[[], Awaitable[Sequence[Mapping[str, Any]]]],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Run one bounded, retry-safe, observable upstream synchronization.

    A successful list upserts the last-known projection and marks disappearances
    unavailable. A transient outage records an explicit failure while retaining
    prior metadata as stale, so ``projection_readiness`` blocks new launches
    without erasing historical evidence. Both outcomes commit an idempotent
    result that is safe to retry, and both return a compact observable summary.
    """
    observed_at = now or datetime.now(timezone.utc)
    try:
        inventory = await list_agents()
    except Exception as exc:  # bounded transient failure -> degrade safely
        safe_error = str(exc).replace("\n", " ")[:512] or exc.__class__.__name__
        retained = await record_upstream_sync_failure(
            session,
            endpoint_ref=endpoint_ref,
            bridge_mode=bridge_mode,
            error=safe_error,
            now=observed_at,
        )
        return {
            "status": "degraded",
            "endpointRef": endpoint_ref,
            "bridgeMode": bridge_mode,
            "attemptedAt": observed_at.isoformat(),
            "error": safe_error,
            "retainedStaleProjections": retained,
        }
    synced = await synchronize_upstream_inventory(
        session,
        endpoint_ref=endpoint_ref,
        bridge_mode=bridge_mode,
        inventory=inventory,
        now=observed_at,
    )
    total = await _count_projections(session, endpoint_ref, bridge_mode)
    return {
        "status": "synced",
        "endpointRef": endpoint_ref,
        "bridgeMode": bridge_mode,
        "syncedAt": observed_at.isoformat(),
        "syncedCount": synced,
        "projectionCount": total,
    }
