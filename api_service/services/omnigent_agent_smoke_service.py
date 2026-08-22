"""Operator-triggered bounded smoke validation for Omnigent agent profiles.

Section 7 of MoonLadderStudios/MoonMind#3517 requires a bounded preflight that
checks endpoint reachability, the exact resolved source, capabilities, Provider
Profile readiness, compiled policy, and the strongest *safe* session-start
capability before an operator relies on a profile version. Diagnostics are
bounded and secret-scanned, and cancellation/failure/timeout release only
validation-owned leases and resources. A pass is readiness evidence, not a
workflow-success guarantee.

The readiness-check core (:func:`run_profile_readiness_checks`) is shared with
the activation validator so both surfaces evaluate identical semantics; only
the packaging (timeout budget, session-start probe, diagnostics scrubbing, and
lease cleanup) is specific to smoke validation.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db.models import (
    ManagedAgentProviderProfile,
    OmnigentUpstreamAgentProjection,
    TemporalArtifact,
)
from api_service.services.omnigent_agent_bundle_service import (
    BundleValidationError,
    validate_agent_bundle,
)
from api_service.services.omnigent_agent_profile_service import (
    projection_identity,
    projection_readiness,
)
from api_service.services.provider_profile_readiness import provider_profile_launch_ready
from moonmind.security.outbound_scan import scan_outbound_text

SMOKE_SCHEMA_VERSION = "moonmind.omnigent-agent-profile-smoke.v1"
DEFAULT_SMOKE_TIMEOUT_SECONDS = 30.0
_MAX_DIAGNOSTIC_LINES = 32
_MAX_DIAGNOSTIC_CHARS = 512
_SAFE_BUNDLE_TYPES = {
    "application/zip",
    "application/x-tar",
    "application/gzip",
    "application/vnd.moonmind.omnigent-agent-bundle+zip",
}
_MAX_BUNDLE_ARTIFACT_BYTES = 50 * 1024 * 1024


@dataclass(slots=True)
class ReadinessOutcome:
    """Result of the shared profile readiness-check core."""

    checks: list[dict[str, Any]] = field(default_factory=list)
    upstream_snapshot: dict[str, Any] | None = None

    @property
    def ready(self) -> bool:
        return bool(self.checks) and all(check["ready"] for check in self.checks)


@dataclass(slots=True)
class SmokeResult:
    """Bounded smoke-validation evidence for one profile version."""

    ready: bool
    checks: list[dict[str, Any]]
    diagnostics: list[str]
    duration_ms: int
    timed_out: bool
    upstream_snapshot: dict[str, Any] | None = None

    def as_dict(self, *, profile_id: str, version: int) -> dict[str, Any]:
        return {
            "schemaVersion": SMOKE_SCHEMA_VERSION,
            "profileId": profile_id,
            "version": version,
            "ready": self.ready,
            "timedOut": self.timed_out,
            "durationMs": self.duration_ms,
            "checks": self.checks,
            "diagnostics": self.diagnostics,
        }


async def run_profile_readiness_checks(
    session: AsyncSession,
    *,
    document: Mapping[str, Any],
    refresh_upstream: Callable[[], Awaitable[None]],
    read_bundle_bytes: Callable[[str], Awaitable[bytes]],
) -> ReadinessOutcome:
    """Evaluate credential-free readiness for a resolved profile document.

    Shared by activation validation and smoke validation so both surfaces
    apply identical semantics. ``refresh_upstream`` synchronizes the bounded
    upstream projection through the authenticated bridge; ``read_bundle_bytes``
    reads an artifact-backed bundle through the artifact authorization boundary
    (raising on unauthorized/unavailable content).
    """

    if document.get("schemaVersion") == "moonmind.omnigent-agent-profile.v2":
        return await _run_v2_profile_readiness_checks(
            session,
            document=document,
            refresh_upstream=refresh_upstream,
            read_bundle_bytes=read_bundle_bytes,
        )

    checks: list[dict[str, Any]] = []
    upstream_snapshot: dict[str, Any] | None = None
    source = document["source"]
    if source.get("upstreamId"):
        await refresh_upstream()
        projection = await session.get(
            OmnigentUpstreamAgentProjection,
            projection_identity(
                document["endpointRef"],
                source["upstreamId"],
                source.get("upstreamVersion"),
            ),
        )
        readiness = projection_readiness(
            projection,
            bridge_mode=document["bridgeMode"],
            harness=document["harness"],
            required_capabilities=document.get("requiredCapabilities", []),
        )
        checks.append({"name": "upstream_identity", **readiness})
        upstream_snapshot = projection.metadata_snapshot if projection else None
    else:
        artifact_id = source["bundleArtifactRef"].removeprefix("artifact:")
        artifact = await session.get(TemporalArtifact, artifact_id)
        expected = source["bundleDigest"].removeprefix("sha256:")
        bundle_ready = bool(
            artifact
            and artifact.sha256 == expected
            and artifact.content_type in _SAFE_BUNDLE_TYPES
            and artifact.size_bytes is not None
            and 0 < artifact.size_bytes <= _MAX_BUNDLE_ARTIFACT_BYTES
            and artifact.created_by_principal
        )
        checks.append(
            {
                "name": "bundle_provenance",
                "ready": bundle_ready,
                "reason": None
                if bundle_ready
                else (
                    "bundle must resolve to a creator-attributed artifact with "
                    "matching digest, safe media type, and bounded size"
                ),
            }
        )
        if bundle_ready:
            try:
                bundle_bytes = await read_bundle_bytes(artifact_id)
                bundle_metadata = validate_agent_bundle(
                    bundle_bytes, artifact.content_type or ""
                )
                declared = set(bundle_metadata["capabilities"])
                required = set(document.get("requiredCapabilities", []))
                content_ready = (
                    bundle_metadata["harness"] == document["harness"]
                    and required.issubset(declared)
                )
                checks.append(
                    {
                        "name": "bundle_contents",
                        "ready": content_ready,
                        "reason": None
                        if content_ready
                        else "bundle harness or capabilities do not satisfy the profile",
                        "metadata": bundle_metadata,
                    }
                )
                upstream_snapshot = {
                    "source": "artifact",
                    "artifactRef": source["bundleArtifactRef"],
                    "digest": source["bundleDigest"],
                    **bundle_metadata,
                }
            except BundleValidationError as exc:
                checks.append(
                    {"name": "bundle_contents", "ready": False, "reason": str(exc)}
                )
            except Exception:
                # Artifact authorization/storage details are not exposed.
                checks.append(
                    {
                        "name": "bundle_contents",
                        "ready": False,
                        "reason": "bundle content could not be read through the artifact boundary",
                    }
                )
    requirements = document["providerRequirements"]
    providers = list(
        (
            await session.execute(
                select(ManagedAgentProviderProfile).where(
                    ManagedAgentProviderProfile.runtime_id == requirements["runtimeId"],
                    ManagedAgentProviderProfile.enabled.is_(True),
                )
            )
        ).scalars()
    )
    compatible_provider = any(
        provider_profile_launch_ready(row)
        and row.credential_source.value == requirements["credentialSource"]
        and row.runtime_materialization_mode.value == requirements["materializationMode"]
        and (
            not requirements.get("providerIds")
            or row.provider_id in requirements["providerIds"]
        )
        for row in providers
    )
    checks.append(
        {
            "name": "provider_profile",
            "ready": compatible_provider,
            "reason": None
            if compatible_provider
            else "no enabled compatible Provider Profile",
        }
    )
    return ReadinessOutcome(checks=checks, upstream_snapshot=upstream_snapshot)


def _profile_snapshot_digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _profile_model_ids(value: Any) -> set[str]:
    if isinstance(value, str):
        return {value} if "/" in value else set()
    if isinstance(value, Mapping):
        return {
            model
            for item in value.values()
            for model in _profile_model_ids(item)
        }
    if isinstance(value, list):
        return {model for item in value for model in _profile_model_ids(item)}
    return set()


async def _run_v2_profile_readiness_checks(
    session: AsyncSession,
    *,
    document: Mapping[str, Any],
    refresh_upstream: Callable[[], Awaitable[None]],
    read_bundle_bytes: Callable[[str], Awaitable[bytes]],
) -> ReadinessOutcome:
    """Evaluate v2 authority without projecting it through the v1 schema."""

    from api_service.db.models import (
        OmnigentHarnessCatalogSnapshotRecord,
        OmnigentHarnessTrustRecord,
    )
    from moonmind.omnigent.harness_platform.agent_profile import (
        BundleSource,
        validate_agent_profile,
    )
    from moonmind.omnigent.harness_platform.catalog import (
        HarnessCatalogSnapshot,
        TrustState,
        assert_catalog_refresh_attests,
    )
    from moonmind.omnigent.harness_platform.host_classes import (
        OmnigentHostClassSelector,
        get_launch_policy,
    )
    from moonmind.omnigent.harness_platform.materializers import (
        materializer_ref_for_provider,
    )

    checks: list[dict[str, Any]] = []
    upstream_snapshot: dict[str, Any] | None = None
    try:
        profile = validate_agent_profile(dict(document))
    except Exception as exc:
        return ReadinessOutcome(
            checks=[
                {
                    "name": "profile_schema",
                    "ready": False,
                    "reason": f"invalid v2 Agent Profile: {exc}",
                }
            ]
        )

    if not isinstance(profile.source, BundleSource):
        await refresh_upstream()
        projection = await session.get(
            OmnigentUpstreamAgentProjection,
            projection_identity(
                profile.endpointRef,
                profile.source.upstreamId,
                profile.source.upstreamVersion,
            ),
        )
        source_ready = bool(
            projection
            and projection.available
            and projection.compatible
            and _profile_snapshot_digest(dict(projection.metadata_snapshot or {}))
            == profile.source.upstreamSnapshotDigest
        )
        checks.append(
            {
                "name": "upstream_identity",
                "ready": source_ready,
                "reason": None
                if source_ready
                else "the exact upstream projection is unavailable or changed",
            }
        )
        upstream_snapshot = (
            dict(projection.metadata_snapshot or {})
            if projection is not None
            else None
        )
    else:
        artifact_id = profile.source.bundleArtifactRef.removeprefix("artifact:")
        artifact = await session.get(TemporalArtifact, artifact_id)
        provenance_ready = bool(
            artifact
            and artifact.sha256 == profile.source.bundleDigest.removeprefix("sha256:")
            and artifact.content_type in _SAFE_BUNDLE_TYPES
            and artifact.size_bytes is not None
            and 0 < artifact.size_bytes <= _MAX_BUNDLE_ARTIFACT_BYTES
            and artifact.created_by_principal
        )
        checks.append(
            {
                "name": "bundle_provenance",
                "ready": provenance_ready,
                "reason": None
                if provenance_ready
                else "the immutable bundle artifact is unavailable or invalid",
            }
        )
        if provenance_ready:
            try:
                bundle_metadata = validate_agent_bundle(
                    await read_bundle_bytes(artifact_id), artifact.content_type or ""
                )
                content_ready = (
                    bundle_metadata["harness"] == profile.harness.id
                )
                checks.append(
                    {
                        "name": "bundle_contents",
                        "ready": content_ready,
                        "reason": None
                        if content_ready
                        else "bundle harness differs from profile authority",
                    }
                )
            except Exception:
                checks.append(
                    {
                        "name": "bundle_contents",
                        "ready": False,
                        "reason": "bundle content could not be validated",
                    }
                )

    authority_row = await session.get(
        OmnigentHarnessCatalogSnapshotRecord, profile.harness.catalogRef
    )
    latest_row = (
        await session.execute(
            select(OmnigentHarnessCatalogSnapshotRecord)
            .where(
                OmnigentHarnessCatalogSnapshotRecord.endpoint_ref
                == profile.endpointRef
            )
            .order_by(OmnigentHarnessCatalogSnapshotRecord.observed_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    authority = (
        HarnessCatalogSnapshot.model_validate(authority_row.snapshot_json)
        if authority_row is not None
        else None
    )
    observation = (
        HarnessCatalogSnapshot.model_validate(latest_row.snapshot_json)
        if latest_row is not None
        else None
    )
    catalog_ready = False
    harness = None
    if authority is not None and observation is not None:
        try:
            assert_catalog_refresh_attests(
                authority=authority,
                observation=observation,
                harness_id=profile.harness.id,
                implementation_ref=profile.harness.implementationRef,
            )
            harness = next(
                item
                for item in authority.harnesses
                if item.id == profile.harness.id
            )
            trust = await session.get(
                OmnigentHarnessTrustRecord, profile.harness.implementationRef
            )
            catalog_ready = bool(
                trust
                and trust.trust_state
                in {TrustState.core_trusted.value, TrustState.plugin_approved.value}
            )
        except Exception:
            catalog_ready = False
    checks.append(
        {
            "name": "harness_catalog",
            "ready": catalog_ready,
            "reason": None
            if catalog_ready
            else "fresh trusted catalog evidence does not attest immutable authority",
        }
    )

    providers = list(
        (
            await session.execute(
                select(ManagedAgentProviderProfile).where(
                    ManagedAgentProviderProfile.enabled.is_(True)
                )
            )
        ).scalars()
    )
    accepted_ids = {
        provider_id
        for slot in profile.credentialSlots
        for provider_id in slot.acceptedProviderIds
    }
    compatible_provider = False
    for provider in providers:
        if (
            not provider_profile_launch_ready(provider)
            or accepted_ids
            and provider.provider_id not in accepted_ids
            or harness is None
            or authority is None
        ):
            continue
        try:
            materializer_ref = materializer_ref_for_provider(
                provider.runtime_id, provider.provider_id
            )
            selected_classes = [
                OmnigentHostClassSelector().select(
                    harness=harness,
                    omnigent_version=authority.omnigentVersion,
                    omnigent_build_digest=authority.omnigentBuildDigest,
                    integration_mode=harness.capabilities.integrationMode
                    or "native-server",
                    materializer_refs=[materializer_ref],
                    requested_host_mode=get_launch_policy(policy_ref).hostMode,
                )
                for policy_ref in profile.allowedLaunchPolicyRefs
            ]
            evidence = dict(provider.model_catalog_evidence_json or {})
            model = str(profile.model.get("qualifiedId") or "").strip()
            if (
                int(evidence.get("credentialGeneration") or 0)
                != int(provider.credential_generation)
                or str(evidence.get("imageRef") or "")
                not in {item.imageRef for item in selected_classes}
                or not model
                or model not in _profile_model_ids(evidence.get("models", []))
            ):
                continue
            compatible_provider = True
            break
        except Exception:
            continue
    checks.append(
        {
            "name": "provider_profile",
            "ready": compatible_provider,
            "reason": None
            if compatible_provider
            else "no enabled compatible Provider Profile and Host Class",
        }
    )
    return ReadinessOutcome(checks=checks, upstream_snapshot=upstream_snapshot)


def scrub_diagnostics(lines: Iterable[str]) -> list[str]:
    """Bound and secret-scan smoke diagnostics before they are recorded.

    Each line is scanned in high-security mode; any line carrying secret-like
    material is replaced with a redacted marker so diagnostics never leak
    credentials into audit evidence.
    """

    scrubbed: list[str] = []
    for raw in list(lines)[:_MAX_DIAGNOSTIC_LINES]:
        text = str(raw)[:_MAX_DIAGNOSTIC_CHARS]
        result = scan_outbound_text(text, high_security_mode=True)
        if result.allowed:
            scrubbed.append(text)
        else:
            categories = ", ".join(sorted({f.category for f in result.findings}))
            scrubbed.append(f"[redacted smoke diagnostic: {categories}]")
    return scrubbed


async def run_smoke_validation(
    *,
    preflight: Callable[[], Awaitable[ReadinessOutcome]],
    session_start_probe: Callable[[], Awaitable[dict[str, Any]]],
    cleanup: Callable[[], Awaitable[None]] | None = None,
    diagnostics: Iterable[str] = (),
    timeout_seconds: float = DEFAULT_SMOKE_TIMEOUT_SECONDS,
    monotonic: Callable[[], float] | None = None,
    profile_id: str,
    version: int,
) -> dict[str, Any]:
    """Run a bounded smoke preflight, guaranteeing lease/resource cleanup.

    ``preflight`` runs the shared readiness checks; ``session_start_probe`` is
    the strongest *safe* session-start capability check (bridge reachability,
    never a real launch). The whole preflight runs under ``timeout_seconds``;
    on success, failure, cancellation, or timeout ``cleanup`` is always invoked
    exactly once so only validation-owned leases and resources are released.
    """

    clock = monotonic or time.monotonic
    started = clock()
    extra: list[str] = []
    checks: list[dict[str, Any]] = []
    upstream_snapshot: dict[str, Any] | None = None
    timed_out = False

    async def _run() -> ReadinessOutcome:
        outcome = await preflight()
        probe = await session_start_probe()
        outcome.checks.append({"name": "session_start", **probe})
        return outcome

    try:
        outcome = await asyncio.wait_for(_run(), timeout=timeout_seconds)
        checks = outcome.checks
        upstream_snapshot = outcome.upstream_snapshot
    except asyncio.TimeoutError:
        timed_out = True
        checks = [
            {
                "name": "smoke_timeout",
                "ready": False,
                "reason": "smoke validation exceeded the bounded time budget",
            }
        ]
        extra.append(f"smoke validation timed out after {timeout_seconds:g}s")
    except Exception as exc:  # noqa: BLE001 - surfaced as a failed check, not a 500
        checks = [
            {
                "name": "smoke_error",
                "ready": False,
                "reason": "smoke validation could not complete",
            }
        ]
        extra.append(f"smoke validation error: {type(exc).__name__}")
    finally:
        if cleanup is not None:
            await cleanup()

    # Snapshot diagnostics after the preflight so probe/preflight appends to a
    # caller-owned mutable sink are captured before scrubbing.
    collected = list(diagnostics) + extra
    duration_ms = int((clock() - started) * 1000)
    ready = (not timed_out) and bool(checks) and all(c["ready"] for c in checks)
    result = SmokeResult(
        ready=ready,
        checks=checks,
        diagnostics=scrub_diagnostics(collected),
        duration_ms=duration_ms,
        timed_out=timed_out,
        upstream_snapshot=upstream_snapshot,
    )
    return result.as_dict(profile_id=profile_id, version=version)


__all__ = [
    "DEFAULT_SMOKE_TIMEOUT_SECONDS",
    "SMOKE_SCHEMA_VERSION",
    "ReadinessOutcome",
    "SmokeResult",
    "run_profile_readiness_checks",
    "run_smoke_validation",
    "scrub_diagnostics",
]
