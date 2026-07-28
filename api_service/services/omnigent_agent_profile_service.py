"""Server-owned synchronization and validation for Omnigent agent profiles."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db.models import OmnigentUpstreamAgentProjection

_SUPPORTED_HARNESSES = {"codex-native"}
_MAX_INVENTORY = 500


def projection_identity(endpoint_ref: str, upstream_id: str, version: str | None) -> str:
    """Build a bounded stable key without trusting a display name."""
    raw = json.dumps(
        [endpoint_ref, upstream_id, version or ""],
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()
    return "upstream:" + hashlib.sha256(raw).hexdigest()


def _text(row: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


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
                metadata_snapshot=dict(item),
                available=True,
                compatible=compatible,
                last_successful_sync_at=observed_at,
                last_attempt_at=observed_at,
            )
            session.add(projection)
        else:
            projection.metadata_snapshot = dict(item)
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
        if projection.projection_id not in seen:
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
) -> None:
    """Retain last-known metadata while explicitly recording stale error state."""
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

