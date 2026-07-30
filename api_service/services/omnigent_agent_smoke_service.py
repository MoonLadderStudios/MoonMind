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
        row.credential_source.value == requirements["credentialSource"]
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
