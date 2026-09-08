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
(:meth:`OmnigentBridgeSessionStore.active_host_protocol_modes` for sessions,
:meth:`OmnigentBridgeSessionStore.list_embedded_host_readiness` plus
:meth:`OmnigentBridgeSessionStore.count_active_embedded_host_leases` for
leases, and
:meth:`OmnigentBridgeSessionStore.cleanup_required_host_lease_refs` for
outstanding janitor cleanup authority); this module owns only the
aggregation rule so workflow code can carry it without embedding launch
behavior.

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
    pending_cleanup_host_leases: int = 0,
    lease_list_truncated: bool = False,
) -> dict[str, Any]:
    """Aggregate durable embedded counts into a bounded drain disposition.

    ``drained`` is true only when no active embedded session, no active
    embedded host lease, and no pending janitor cleanup authority remains
    *and* read-only historical decoding is available for the retained rows.
    Every other shape names its blockers; missing evidence is a blocker,
    never an implicit drain.

    ``active_embedded_leases`` must be the uncapped durable total (the
    bounded readiness list is capped for payload safety, so it cannot serve
    as the total). ``lease_list_truncated`` records that the bounded list
    the probe observed was shorter than that total, so operators can tell a
    complete observation from a truncated one.
    """

    for name, value in (
        ("active_embedded_sessions", active_embedded_sessions),
        ("active_embedded_leases", active_embedded_leases),
        ("pending_cleanup_host_leases", pending_cleanup_host_leases),
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
    if pending_cleanup_host_leases > 0:
        blockers.append(
            f"pending_cleanup_host_leases:{pending_cleanup_host_leases}"
        )
    if not historical_decoding_available:
        blockers.append("historical_decoding_unavailable")
    return {
        "drained": not blockers,
        "blockers": tuple(blockers),
        "activeEmbeddedSessions": active_embedded_sessions,
        "activeEmbeddedLeases": active_embedded_leases,
        "pendingCleanupHostLeases": pending_cleanup_host_leases,
        "leaseListTruncated": bool(lease_list_truncated),
        "historicalDecodingAvailable": bool(historical_decoding_available),
        "retirementPathId": EMBEDDED_TRANSPORT_RETIREMENT_PATH_ID,
        "contractVersion": EMBEDDED_DRAIN_CONTRACT_VERSION,
    }


__all__ = [
    "EMBEDDED_DRAIN_CONTRACT_VERSION",
    "EMBEDDED_LAUNCH_MODULES",
    "EmbeddedDrainError",
    "decode_embedded_retained_session",
    "probe_embedded_drain",
    "summarize_embedded_drain",
]


async def probe_embedded_drain(store: Any) -> dict[str, Any]:
    """Wire durable embedded counts from the owning store into the summary.

    Source issue: MoonLadderStudios/MoonMind#3955 (plan step 2, acceptance
    "in-flight cleanup and retained historical reads have a verified
    disposition").

    ``store`` is duck-typed to the canonical durable readers owned by
    :class:`moonmind.omnigent.bridge_store.OmnigentBridgeSessionStore` —
    ``active_host_protocol_modes()`` for non-terminal sessions,
    ``list_embedded_host_readiness()`` for the bounded active host-lease
    list, ``count_active_embedded_host_leases()`` for the uncapped lease
    total, and ``cleanup_required_host_lease_refs()`` for outstanding
    janitor cleanup authority — so this module still imports no store,
    channel, launch, evidence, or DB modules. Only counts flow into
    :func:`summarize_embedded_drain`; provider session ids, host ids,
    endpoints, and credentials are never projected.

    The bounded readiness list is payload-capped, so the uncapped aggregate
    count is the drain total whenever the store offers it; otherwise the
    list length is used and the observation is reported as untruncated.
    Terminal sessions that still carry janitor cleanup authority are
    invisible to both readers above, so their refs are counted separately:
    the probe reports ``drained`` only when that set is empty.

    A store failure propagates instead of implying drain: missing evidence
    is a blocker, never an implicit drain, and the caller observes the
    failure directly.
    """

    modes = await store.active_host_protocol_modes()
    if not isinstance(modes, Mapping):
        raise EmbeddedDrainError(
            "active_host_protocol_modes must return a mapping, "
            f"got {type(modes).__name__}"
        )
    leases = await store.list_embedded_host_readiness()
    if not isinstance(leases, list):
        raise EmbeddedDrainError(
            "list_embedded_host_readiness must return a list, "
            f"got {type(leases).__name__}"
        )
    count_reader = getattr(store, "count_active_embedded_host_leases", None)
    if callable(count_reader):
        raw_total = await count_reader()
        try:
            active_leases = int(raw_total or 0)
        except (TypeError, ValueError) as exc:
            raise EmbeddedDrainError(
                "active embedded lease count must be an int, "
                f"got {raw_total!r}"
            ) from exc
        lease_list_truncated = len(leases) < active_leases
    else:
        active_leases = len(leases)
        lease_list_truncated = False
    cleanup_reader = getattr(store, "cleanup_required_host_lease_refs", None)
    if callable(cleanup_reader):
        pending_cleanup_refs = await cleanup_reader()
        try:
            pending_cleanup = len(pending_cleanup_refs)
        except TypeError as exc:
            raise EmbeddedDrainError(
                "cleanup_required_host_lease_refs must return a sized "
                f"collection, got {type(pending_cleanup_refs).__name__}"
            ) from exc
    else:
        pending_cleanup = 0
    try:
        active_sessions = int(modes.get(HOST_PROTOCOL_MODE_EMBEDDED, 0) or 0)
    except (TypeError, ValueError) as exc:
        raise EmbeddedDrainError(
            "active embedded session count must be an int, "
            f"got {modes.get(HOST_PROTOCOL_MODE_EMBEDDED)!r}"
        ) from exc
    return summarize_embedded_drain(
        active_embedded_sessions=active_sessions,
        active_embedded_leases=active_leases,
        pending_cleanup_host_leases=pending_cleanup,
        lease_list_truncated=lease_list_truncated,
    )
