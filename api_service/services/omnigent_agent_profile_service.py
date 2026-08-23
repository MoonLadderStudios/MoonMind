"""Server-owned synchronization and validation for Omnigent agent profiles."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db.models import OmnigentUpstreamAgentProjection

_MAX_INVENTORY = 500
_INVENTORY_FRESHNESS_TTL = timedelta(minutes=5)
_METADATA_TEXT_LIMIT = 512
_METADATA_LIST_LIMIT = 64
_IMMUTABLE_IMAGE = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")


async def computed_launchable_harnesses(session: AsyncSession) -> set[str]:
    """Return harness IDs backed by actual legacy or generic production wiring."""

    from api_service.db.models import (
        OmnigentHarnessCatalogSnapshotRecord,
        OmnigentHarnessTrustRecord,
    )
    from moonmind.omnigent.execution_profiles import PROFILES
    from moonmind.omnigent.harness_platform.catalog import HarnessCatalogSnapshot
    from moonmind.omnigent.harness_platform.host_classes import (
        DEFAULT_HOST_CLASS_TEMPLATES,
    )
    from moonmind.omnigent.settings import (
        generic_host_enabled,
        opencode_support_enabled,
    )

    launchable = {
        profile.harness
        for profile in PROFILES.values()
        if profile.provider_runtime in {"codex_cli", "claude_code"}
    }
    if not generic_host_enabled():
        return launchable
    rows = list(
        (
            await session.execute(
                select(OmnigentHarnessCatalogSnapshotRecord).order_by(
                    OmnigentHarnessCatalogSnapshotRecord.observed_at.desc()
                )
            )
        ).scalars()
    )
    latest_by_endpoint: dict[str, Any] = {}
    for row in rows:
        latest_by_endpoint.setdefault(row.endpoint_ref, row)
    trust_rows = list(
        (await session.execute(select(OmnigentHarnessTrustRecord))).scalars()
    )
    trusted = {
        row.implementation_ref
        for row in trust_rows
        if row.trust_state in {"core_trusted", "plugin_approved"}
    }
    for row in latest_by_endpoint.values():
        snapshot = HarnessCatalogSnapshot.model_validate(row.snapshot_json)
        for harness in snapshot.harnesses:
            if harness.implementation.implementation_ref() not in trusted:
                continue
            for template in DEFAULT_HOST_CLASS_TEMPLATES:
                if harness.id not in template.harness_ids:
                    continue
                if (
                    template.host_class_id == "omnigent-opencode"
                    and not opencode_support_enabled()
                ):
                    continue
                image = str(os.getenv(template.image_env) or "").strip()
                if _IMMUTABLE_IMAGE.fullmatch(image) and not image.endswith("0" * 64):
                    launchable.add(harness.id)
    return launchable


def projection_identity(
    endpoint_ref: str, upstream_id: str, version: str | None
) -> str:
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
        if isinstance(value, int) and not isinstance(value, bool):
            return str(value)
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
        result["capabilities"] = sorted(
            {
                str(value)[:_METADATA_TEXT_LIMIT]
                for value in capabilities[:_METADATA_LIST_LIMIT]
                if isinstance(value, str) and value
            }
        )
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
    launchable_harnesses = await computed_launchable_harnesses(session)
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
        compatible = harness in launchable_harnesses or (
            not harness
            and "codex-native" in capability_values
            and "codex-native" in launchable_harnesses
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

    existing = list(
        (
            await session.execute(
                select(OmnigentUpstreamAgentProjection).where(
                    OmnigentUpstreamAgentProjection.endpoint_ref == endpoint_ref,
                    OmnigentUpstreamAgentProjection.bridge_mode == bridge_mode,
                )
            )
        ).scalars()
    )
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
) -> None:
    """Retain last-known metadata while explicitly recording stale error state."""
    attempted_at = now or datetime.now(timezone.utc)
    safe_error = error.replace("\n", " ")[:512]
    rows = list(
        (
            await session.execute(
                select(OmnigentUpstreamAgentProjection).where(
                    OmnigentUpstreamAgentProjection.endpoint_ref == endpoint_ref,
                    OmnigentUpstreamAgentProjection.bridge_mode == bridge_mode,
                )
            )
        ).scalars()
    )
    for projection in rows:
        projection.last_attempt_at = attempted_at
        projection.error = safe_error
    await session.commit()


_OPENCODE_NATIVE_UI_ID = "opencode-native-ui"
_OPENCODE_NATIVE_UI_VERSION = "1"


def _synthetic_opencode_implementation() -> Any:
    from moonmind.omnigent.harness_platform.catalog import (
        HarnessImplementationIdentity,
    )

    # Stable placeholder identity for the local OpenCode overlay on stock
    # Omnigent servers that do not natively advertise the harness. It must
    # match the bootstrap qualification synthesizer so existing authority
    # bindings and trust records stay valid across observations.
    return HarnessImplementationIdentity.model_validate(
        {
            "sourceKind": "core",
            "package": "omnigent",
            "version": "1.0.0",
            "digest": "sha256:" + "a" * 64,
            "pluginEntryPoint": None,
        }
    )


def _synthetic_opencode_harness_row() -> dict[str, Any]:
    implementation = _synthetic_opencode_implementation()
    return {
        "id": "opencode-native",
        "label": "OpenCode",
        "aliases": [],
        "implementation": implementation.model_dump(mode="json", by_alias=True),
        "capabilities": {
            "integrationMode": "native-server",
            "authModel": "own-auth",
        },
        "setupSteps": [],
        "runtimeRequirements": {},
    }


def _overlay_synthetic_opencode(result: Any) -> Any:
    """Merge the local OpenCode overlay into one authenticated observation.

    Stock Omnigent endpoints do not advertise ``opencode-native``. When OpenCode
    support is enabled, each observation carries the stable overlay harness and
    upstream agent identity so freshness attestation and launch planning keep
    succeeding without forking the readiness path.
    """

    import hashlib

    from moonmind.omnigent.harness_platform.catalog import (
        TrustState,
        classify_harness_trust,
        create_catalog_snapshot,
    )
    from moonmind.omnigent.harness_platform.catalog_service import (
        HarnessCatalogSyncResult,
    )
    from moonmind.omnigent.settings import opencode_support_enabled

    if not opencode_support_enabled() or any(
        harness.id == "opencode-native" for harness in result.snapshot.harnesses
    ):
        return result
    harness_rows = [
        harness.model_dump(by_alias=True, mode="json")
        for harness in result.snapshot.harnesses
    ]
    harness_rows.append(_synthetic_opencode_harness_row())
    # Persisted after the raw observation within the same microsecond-scale
    # window; the offset guarantees ``latest()`` deterministically selects the
    # overlay-complete snapshot that readiness and planning must observe.
    observed_at = result.snapshot.observedAt + timedelta(microseconds=1)
    merged_source = json.dumps(
        {"prior": result.snapshot.sourceDigest, "overlay": True},
        sort_keys=True,
        separators=(",", ":"),
    )
    snapshot = create_catalog_snapshot(
        endpointRef=result.snapshot.endpointRef,
        omnigentVersion=result.snapshot.omnigentVersion,
        omnigentBuildDigest=result.snapshot.omnigentBuildDigest,
        sourceDigest="sha256:"
        + hashlib.sha256(merged_source.encode()).hexdigest(),
        harnesses=harness_rows,
        observedAt=observed_at,
        pluginLoadErrors=list(result.snapshot.pluginLoadErrors),
    )
    trust_records = tuple(
        classify_harness_trust(
            harnessId=harness["id"],
            implementation=(
                _synthetic_opencode_implementation()
                if harness["id"] == "opencode-native"
                else next(
                    record.implementation
                    for record in result.trust_records
                    if record.harnessId == harness["id"]
                )
            ),
            trustState=(
                TrustState.core_trusted
                if harness["id"] == "opencode-native"
                else next(
                    record.trustState
                    for record in result.trust_records
                    if record.harnessId == harness["id"]
                )
            ),
            decidedBy="catalog-sync",
            decidedAt=snapshot.observedAt,
        )
        for harness in harness_rows
    )
    return HarnessCatalogSyncResult(
        snapshot=snapshot,
        trust_records=trust_records,
        diagnostics={
            **dict(result.diagnostics),
            "agents": [
                *[item for item in result.diagnostics.get("agents", []) if isinstance(item, dict)],
                {
                    "id": _OPENCODE_NATIVE_UI_ID,
                    "version": _OPENCODE_NATIVE_UI_VERSION,
                    "harness": "opencode-native",
                },
            ],
            "syntheticOpencodeOverlay": True,
        },
    )


async def synchronize_omnigent_harness_catalog(session: AsyncSession) -> dict[str, Any]:
    """Canonical authenticated harness-catalog synchronization.

    One production path shared by the operator endpoint and the automatic
    startup/maintenance reconciliation. It turns the configured Omnigent
    endpoint inventory into immutable planner authority, refreshes the bounded
    upstream agent projections, and seeds the built-in OpenCode agent profile.
    """

    from api_service.api.routers.omnigent_agent_profiles import (
        ensure_builtin_opencode_agent_profile,
    )
    from api_service.db.base import async_session_maker
    from moonmind.omnigent.production import build_generic_omnigent_execution_services

    services = build_generic_omnigent_execution_services(
        session_factory=async_session_maker
    )
    result = await services.catalog_service.synchronize()
    overlaid = _overlay_synthetic_opencode(result)
    if overlaid is not result:
        from moonmind.omnigent.harness_platform.catalog_service import (
            DbHarnessCatalogRepository,
        )

        await DbHarnessCatalogRepository(async_session_maker).persist(overlaid)
    await synchronize_upstream_inventory(
        session,
        endpoint_ref=overlaid.snapshot.endpointRef,
        bridge_mode="proxy",
        inventory=[
            item
            for item in overlaid.diagnostics.get("agents", [])
            if isinstance(item, dict)
        ],
    )
    builtin = await ensure_builtin_opencode_agent_profile(
        session=session, catalog=overlaid
    )
    return {
        "catalogRef": overlaid.snapshot.catalogRef,
        "observedAt": overlaid.snapshot.observedAt,
        "omnigentVersion": overlaid.snapshot.omnigentVersion,
        "harnessCount": len(overlaid.snapshot.harnesses),
        "pluginLoadErrors": overlaid.snapshot.pluginLoadErrors,
        "builtinAgentProfile": builtin,
    }
