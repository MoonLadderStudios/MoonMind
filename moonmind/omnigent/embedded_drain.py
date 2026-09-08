"""Bounded drain disposition for the retired embedded host transport.

Source issue: MoonLadderStudios/MoonMind#3955 (plan steps 2 and 6).

Stage 1 disabled new admission through the experimental embedded host/runner
transport while in-flight sessions drain through their recorded mode/endpoint
and cleanup owner. This module is the bounded, side-effect-free half of the
remaining verifier work:

* read-only decoding of retained rows (recorded mode, lifecycle state,
  launch-record and cleanup-authority presence) without importing the
  launch modules that a later removal stage will delete — historical data
  does not need a live transport;
* a bounded drain summary over durable counts (active embedded sessions,
  active embedded host leases) that names blockers instead of inferring
  absence from a default flag.

The durable counting itself stays with the adapters that own the I/O
(:meth:`OmnigentBridgeSessionStore.active_host_protocol_modes` for sessions
and :meth:`OmnigentBridgeSessionStore.list_embedded_host_readiness` for
leases); this module owns only the aggregation rule so workflow code can
carry it without embedding launch behavior.

This module deliberately imports no launch, store, channel, evidence, or DB
modules — only the declarative config constants. Anything that needs live
embedded execution still resolves the code-owned retirement row
(``omnigent.legacy.embedded_host_transport``) and its launch surfaces.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from moonmind.omnigent.bridge_config import (
    EMBEDDED_TRANSPORT_RETIREMENT_PATH_ID,
    HOST_PROTOCOL_MODE_EMBEDDED,
)

EMBEDDED_DRAIN_CONTRACT_VERSION = "moonmind.omnigent-embedded-drain/v1"

# Retained-row metadata keys, owned canonically by
# ``moonmind.omnigent.bridge_store`` (``EMBEDDED_LAUNCH_KEY`` /
# ``EMBEDDED_LIFECYCLE_KEY``) and ``moonmind.omnigent.control_plane.identities``
# (``EGRESS_CLEANUP_AUTHORITY_KEY``). Repeated here as literals so this
# read-only decoder never imports the store, channel, launch, or DB modules
# that a later removal stage will delete; a unit test pins them equal to the
# canonical constants.
_RETAINED_MODE_KEY = "hostProtocolMode"
_RETAINED_LAUNCH_KEY = "embedded_runner_launch"
_RETAINED_LIFECYCLE_KEY = "embedded_runner_lifecycle"
_RETAINED_CLEANUP_AUTHORITY_KEY = "egress_cleanup_authority"

# Launch modules that must stay importable until the drain disposition below
# reports drained and the retirement row advances past new-admission-disabled.
# Proxy-only callers must not require them (see
# ``api_service.api.routers.omnigent_bridge_composition``).
EMBEDDED_LAUNCH_MODULES: tuple[str, ...] = (
    "moonmind.omnigent.bridge_embedded",
    "moonmind.omnigent.embedded_host_channel",
    "moonmind.omnigent.embedded_evidence",
)


class EmbeddedDrainError(ValueError):
    """Raised when drain input is malformed."""


def decode_embedded_retained_session(metadata: Mapping[str, Any]) -> dict[str, Any]:
    """Explain one retained bridge row without a live transport.

    Projects only server-safe, non-secret facts: the recorded protocol mode,
    the embedded lifecycle state (when present), and whether a launch record
    and an egress cleanup authority are retained. Provider session/host/runner
    identities, endpoints, and credentials are never projected — pass a plain
    metadata mapping, never a row object.
    """

    if not isinstance(metadata, Mapping):
        raise EmbeddedDrainError(
            "retained session metadata must be a mapping, "
            f"got {type(metadata).__name__}"
        )
    mode = str(metadata.get(_RETAINED_MODE_KEY) or "").strip() or "unknown"
    lifecycle = metadata.get(_RETAINED_LIFECYCLE_KEY)
    state = (
        lifecycle.get("state")
        if isinstance(lifecycle, Mapping)
        else None
    )
    launch = metadata.get(_RETAINED_LAUNCH_KEY)
    return {
        "recordedMode": mode,
        "isEmbedded": mode == HOST_PROTOCOL_MODE_EMBEDDED,
        "lifecycleState": state if isinstance(state, str) else None,
        "hasLaunchRecord": isinstance(launch, Mapping) and bool(launch),
        "hasCleanupAuthority": _RETAINED_CLEANUP_AUTHORITY_KEY in metadata,
        "retirementPathId": EMBEDDED_TRANSPORT_RETIREMENT_PATH_ID,
    }


def summarize_embedded_drain(
    *,
    active_embedded_sessions: int,
    active_embedded_leases: int,
    historical_decoding_available: bool = True,
) -> dict[str, Any]:
    """Aggregate durable embedded counts into a bounded drain disposition.

    ``drained`` is true only when no active embedded session and no active
    embedded host lease remains *and* read-only historical decoding is
    available for the retained rows. Every other shape names its blockers;
    missing evidence is a blocker, never an implicit drain.
    """

    for name, value in (
        ("active_embedded_sessions", active_embedded_sessions),
        ("active_embedded_leases", active_embedded_leases),
    ):
        if not isinstance(value, int) or isinstance(value, bool):
            raise EmbeddedDrainError(f"{name} must be an int, got {value!r}")
        if value < 0:
            raise EmbeddedDrainError(f"{name} must not be negative, got {value}")
    blockers: list[str] = []
    if active_embedded_sessions > 0:
        blockers.append(f"active_embedded_sessions:{active_embedded_sessions}")
    if active_embedded_leases > 0:
        blockers.append(f"active_embedded_leases:{active_embedded_leases}")
    if not historical_decoding_available:
        blockers.append("historical_decoding_unavailable")
    return {
        "drained": not blockers,
        "blockers": tuple(blockers),
        "activeEmbeddedSessions": active_embedded_sessions,
        "activeEmbeddedLeases": active_embedded_leases,
        "historicalDecodingAvailable": bool(historical_decoding_available),
        "retirementPathId": EMBEDDED_TRANSPORT_RETIREMENT_PATH_ID,
        "contractVersion": EMBEDDED_DRAIN_CONTRACT_VERSION,
    }


__all__ = [
    "EMBEDDED_DRAIN_CONTRACT_VERSION",
    "EMBEDDED_LAUNCH_MODULES",
    "EmbeddedDrainError",
    "decode_embedded_retained_session",
    "summarize_embedded_drain",
]
