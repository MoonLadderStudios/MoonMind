"""Server-owned synchronization and validation for Omnigent agent profiles."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db.models import OmnigentUpstreamAgentProjection

_MAX_INVENTORY = 500
_INVENTORY_FRESHNESS_TTL = timedelta(minutes=5)
_METADATA_TEXT_LIMIT = 512
_METADATA_LIST_LIMIT = 64
_IMMUTABLE_IMAGE = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")
_INVENTORY_REFRESH_TIMEOUT_SECONDS = 15
logger = logging.getLogger(__name__)


class UpstreamInventoryRefreshError(RuntimeError):
    """The configured endpoint could not supply fresh launch evidence."""


async def refresh_upstream_inventory(*, endpoint_ref: str = "default") -> None:
    """Refresh discovery independently of authoring and credential maintenance.

    Only the configured endpoint is authorized. The separate transaction must
    never commit a caller's partially authored workflow or immutable selection.
    The deadline covers pagination and persistence, not just each HTTP page.
    """
    from api_service.db.base import async_session_maker
    from moonmind.omnigent.bridge_config import (
        HOST_PROTOCOL_MODE_PROXY,
        resolve_bridge_config,
    )
    from moonmind.omnigent.settings import resolved_api_token, resolved_server_url
    from moonmind.workflows.adapters.omnigent_client import OmnigentHttpClient

    if endpoint_ref != "default":
        raise UpstreamInventoryRefreshError(
            "upstream inventory refresh requires the configured default endpoint"
        )
    config = resolve_bridge_config()
    if not config.enabled or config.host_protocol_mode != HOST_PROTOCOL_MODE_PROXY:
        raise UpstreamInventoryRefreshError("Omnigent inventory bridge is unavailable")
    attempted_at = datetime.now(timezone.utc)
    try:
        async with asyncio.timeout(_INVENTORY_REFRESH_TIMEOUT_SECONDS):
            inventory = await OmnigentHttpClient(
                base_url=resolved_server_url(),
                api_token=resolved_api_token(),
                timeout_seconds=5,
            ).list_agents()
            async with async_session_maker() as session:
                await synchronize_upstream_inventory(
                    session,
                    endpoint_ref=endpoint_ref,
                    bridge_mode="proxy",
                    inventory=inventory,
                )
    except Exception as exc:
        # Provider exception strings can contain URLs or credentials. Retain a
        # bounded, non-sensitive failure classification, never their raw text.
        reason = f"upstream inventory refresh failed ({type(exc).__name__}); retry submission"
        try:
            async with asyncio.timeout(5):
                async with async_session_maker() as session:
                    await record_upstream_sync_failure(
                        session,
                        endpoint_ref=endpoint_ref,
                        bridge_mode="proxy",
                        error=reason,
                        now=attempted_at,
                    )
        except Exception:
            logger.warning("Could not persist upstream inventory refresh failure")
        raise UpstreamInventoryRefreshError(reason) from exc


async def computed_launchable_harnesses(session: AsyncSession) -> set[str]:
    """Return harness IDs backed by actual legacy or generic production wiring."""

    from api_service.db.models import (
        OmnigentHarnessCatalogSnapshotRecord,
        OmnigentHarnessTrustRecord,
    )
    from moonmind.omnigent.execution_profiles import PROFILES
    from moonmind.omnigent.harness_platform.catalog import HarnessCatalogSnapshot
    from moonmind.omnigent.harness_platform.failures import HarnessPlatformError
    from moonmind.omnigent.harness_platform.host_classes import (
        DEFAULT_HOST_CLASS_TEMPLATES,
        OMNIGENT_OPENCODE_HOST_IMAGE_ENV,
        get_opencode_host_image_ref,
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
                if template.image_env == OMNIGENT_OPENCODE_HOST_IMAGE_ENV:
                    try:
                        image = get_opencode_host_image_ref()
                    except HarnessPlatformError:
                        # The shared image resolver quarantines an incompatible
                        # server/host pair. Inventory must not advertise that
                        # runtime while plan compilation will reject it.
                        continue
                else:
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
    """Return one explicit, server-owned launch readiness classification.

    Freshness is observable but never blocks a launch on its own: an outage
    retains the last-known snapshot as stale, and only a missing,
    unavailable, incompatible, or contract-mismatched identity fails closed.
    This keeps the default experience working across background-sync lag
    without silently substituting a different upstream version.
    """
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


def readiness_actionable_detail(
    readiness: Mapping[str, Any],
    *,
    profile_id: str,
    version: int | None,
    endpoint_ref: str,
    upstream_id: str,
    upstream_version: str | None,
) -> str:
    """Format a 409 reason that steers before it stops.

    The leading ``reason`` string is preserved verbatim for contract
    compatibility; the suffix names the exact pinned identity, its freshness
    and sync timestamps, and the executable recovery (retry with the latest
    active version; default float already synchronizes automatically, so no
    manual catalog sync is required). Only non-sensitive identity and timing
    fields are included, never credentials or raw provider text.
    """

    reason = str(readiness.get("reason") or "upstream identity is not ready")
    freshness = str(readiness.get("freshness") or "unknown")
    last_success = readiness.get("lastSuccessfulSyncAt")
    last_attempt = readiness.get("lastAttemptAt")
    pinned_version = str(version) if version is not None else "active"
    pinned_upstream = str(upstream_version or "").strip() or "<none>"
    return (
        f"{reason} (profile {profile_id}@{pinned_version} pins "
        f"{endpoint_ref}/{upstream_id}/{pinned_upstream}; "
        f"freshness={freshness}, "
        f"lastSuccessfulSyncAt={last_success}, lastAttemptAt={last_attempt}; "
        "action: retry with the latest active profile version; if you pinned "
        "a version explicitly, omit version to use the default)"
    )


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
    # Compare at the database write boundary: an overlapping successful refresh
    # must remain authoritative even if it commits while this write waits.
    await session.execute(
        update(OmnigentUpstreamAgentProjection)
        .where(
            OmnigentUpstreamAgentProjection.endpoint_ref == endpoint_ref,
            OmnigentUpstreamAgentProjection.bridge_mode == bridge_mode,
            or_(
                OmnigentUpstreamAgentProjection.last_successful_sync_at.is_(None),
                OmnigentUpstreamAgentProjection.last_successful_sync_at < attempted_at,
            ),
        )
        .values(last_attempt_at=attempted_at, error=safe_error)
        .execution_options(synchronize_session=False)
    )
    await session.commit()


def _synthetic_opencode_implementation() -> Any:
    from moonmind.omnigent.harness_platform.catalog import HarnessImplementationIdentity

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


def _observed_claude_native_harness_row(result: Any) -> dict[str, Any] | None:
    """Describe the native wrapper only when its stock agent was observed."""

    from moonmind.omnigent.harness_platform.catalog import HarnessImplementationIdentity
    from moonmind.omnigent.stock_agents import CLAUDE_STOCK_AGENT_NAME

    stock = next(
        (
            row
            for row in result.diagnostics.get("agents", [])
            if isinstance(row, Mapping)
            and row.get("name") == CLAUDE_STOCK_AGENT_NAME
            and row.get("harness") == "claude-native"
            and str(row.get("id") or "").strip()
            and str(row.get("version") or "").strip()
        ),
        None,
    )
    if stock is None:
        return None
    identity = {
        "omnigentBuildDigest": result.snapshot.omnigentBuildDigest,
        "harnessId": "claude-native",
        "runtimePackRef": "claude-native-pack@1",
    }
    implementation = HarnessImplementationIdentity.model_validate(
        {
            "sourceKind": "core",
            "package": "omnigent",
            "version": result.snapshot.omnigentVersion,
            "digest": "sha256:"
            + hashlib.sha256(
                json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
            "pluginEntryPoint": None,
        }
    )
    return {
        "id": "claude-native",
        "label": "Claude Code",
        "aliases": [],
        "implementation": implementation.model_dump(mode="json", by_alias=True),
        "capabilities": {
            "integrationMode": "native-server",
            "authModel": "oauth_volume",
        },
        "setupSteps": [],
        "runtimeRequirements": {"runtimePackRef": "claude-native-pack@1"},
    }


def _overlay_native_harnesses(result: Any) -> Any:
    """Merge deployment-owned native harnesses into one observation.

    The upstream picker catalog omits native wrappers. OpenCode has an
    installed local runtime; Claude additionally requires its observed stock
    agent. Agent identity remains owned by authenticated ``/v1/agents``.
    """

    from moonmind.omnigent.harness_platform.catalog import (
        HarnessImplementationIdentity,
        TrustState,
        classify_harness_trust,
        create_catalog_snapshot,
    )
    from moonmind.omnigent.harness_platform.catalog_service import (
        HarnessCatalogSyncResult,
    )
    from moonmind.omnigent.settings import (
        generic_claude_qualified,
        opencode_support_enabled,
    )

    existing = {harness.id for harness in result.snapshot.harnesses}
    overlays: list[dict[str, Any]] = []
    if opencode_support_enabled() and "opencode-native" not in existing:
        overlays.append(_synthetic_opencode_harness_row())
    if generic_claude_qualified() and "claude-native" not in existing:
        claude = _observed_claude_native_harness_row(result)
        if claude is not None:
            overlays.append(claude)
    if not overlays:
        return result
    harness_rows = [
        harness.model_dump(by_alias=True, mode="json")
        for harness in result.snapshot.harnesses
    ]
    harness_rows.extend(overlays)
    # The overlay is applied before the observation is persisted, so it is the
    # only row published for this synchronization and needs no timestamp offset
    # to win ``latest()``.
    observed_at = result.snapshot.observedAt
    # The digest must cover the overlay's own authority. Hashing only the prior
    # digest and a boolean would keep the source digest stable when the
    # synthetic implementation or agent identity changes, so consumers would
    # reuse a document bound to the superseded overlay.
    merged_source = json.dumps(
        {
            "prior": result.snapshot.sourceDigest,
            "overlays": overlays,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    snapshot = create_catalog_snapshot(
        endpointRef=result.snapshot.endpointRef,
        omnigentVersion=result.snapshot.omnigentVersion,
        omnigentBuildDigest=result.snapshot.omnigentBuildDigest,
        sourceDigest="sha256:" + hashlib.sha256(merged_source.encode()).hexdigest(),
        harnesses=harness_rows,
        observedAt=observed_at,
        pluginLoadErrors=list(result.snapshot.pluginLoadErrors),
    )
    trust_records = tuple(result.trust_records) + tuple(
        classify_harness_trust(
            harnessId=harness["id"],
            implementation=HarnessImplementationIdentity.model_validate(
                harness["implementation"]
            ),
            trustState=TrustState.core_trusted,
            decidedBy="catalog-sync",
            decidedAt=snapshot.observedAt,
        )
        for harness in overlays
    )
    return HarnessCatalogSyncResult(
        snapshot=snapshot,
        trust_records=trust_records,
        diagnostics={
            **dict(result.diagnostics),
            "agents": [
                item
                for item in result.diagnostics.get("agents", [])
                if isinstance(item, dict)
            ],
            **(
                {"syntheticOpencodeOverlay": True}
                if any(row["id"] == "opencode-native" for row in overlays)
                else {}
            ),
            **(
                {"observedClaudeNativeOverlay": True}
                if any(row["id"] == "claude-native" for row in overlays)
                else {}
            ),
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

    # The overlay runs inside the catalog service, before persistence, so the
    # endpoint observation and its OpenCode overlay are published as one
    # immutable row. Persisting the raw snapshot first would let a concurrent
    # readiness or planning request select an overlay-free ``latest()`` and
    # reject an otherwise valid launch.
    services = build_generic_omnigent_execution_services(
        session_factory=async_session_maker,
        catalog_observation_overlay=_overlay_native_harnesses,
    )
    overlaid = await services.catalog_service.synchronize()
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
