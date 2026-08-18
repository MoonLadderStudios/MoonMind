"""Bounded concurrency/fencing telemetry for the Omnigent control plane.

Source: MoonLadderStudios/MoonMind#3704 ([Omnigent control plane 3/11]).

The control plane emits low-cardinality counters for revision conflicts, fencing
conflicts, duplicate-command suppression, delivery-unknown reconciliation, stale
observation retention, and cleanup-claim conflicts. Every label value is drawn
from a closed vocabulary (:class:`ControlPlaneOutcome`, :class:`FencingScope`,
and the fixed :data:`CONFLICT_METRICS` names): no workflow, run, session, turn,
host, lease, profile, user, or credential identity is ever used as a label, so a
high-cardinality identity can never enter the metric label space.

The registry is process-local and additive; a host exporter reads
:func:`snapshot` and maps the bounded names/labels onto its metric backend. The
module also emits a structured, secret-free log line per event so operators can
correlate a conflict without a metrics pipeline.
"""

from __future__ import annotations

import logging
import threading
from collections import Counter
from typing import Mapping, Optional

from .records import ControlPlaneOutcome, FencingScope

logger = logging.getLogger("moonmind.omnigent.control_plane.concurrency")

# Closed set of bounded counter names. Anything outside this set is a bug, not a
# runtime input, so recording an unknown name fails closed.
REVISION_CONFLICTS = "revision_conflicts"
FENCING_CONFLICTS = "fencing_conflicts"
DUPLICATE_COMMAND_SUPPRESSED = "duplicate_command_suppressed"
# Delivery-ambiguity is counted in two distinct phases so operational metrics do
# not report an unresolved ambiguity as a successful reconciliation (#3704):
#   * ``delivery_unknown_created``    - a command was parked as delivery-ambiguous
#     (the side effect *may* have occurred); reconciliation has not confirmed it.
#   * ``delivery_unknown_reconciled`` - a previously parked delivery-ambiguous
#     command was confirmed at the authoritative delivery boundary.
DELIVERY_UNKNOWN_CREATED = "delivery_unknown_created"
DELIVERY_UNKNOWN_RECONCILED = "delivery_unknown_reconciled"
STALE_OBSERVATION_RETAINED = "stale_observation_retained"
CLEANUP_CLAIM_CONFLICTS = "cleanup_claim_conflicts"

CONFLICT_METRICS: frozenset[str] = frozenset(
    {
        REVISION_CONFLICTS,
        FENCING_CONFLICTS,
        DUPLICATE_COMMAND_SUPPRESSED,
        DELIVERY_UNKNOWN_CREATED,
        DELIVERY_UNKNOWN_RECONCILED,
        STALE_OBSERVATION_RETAINED,
        CLEANUP_CLAIM_CONFLICTS,
    }
)

_lock = threading.Lock()
_counts: Counter[tuple[str, str]] = Counter()


def _record(name: str, *, scope: Optional[FencingScope] = None) -> None:
    if name not in CONFLICT_METRICS:  # pragma: no cover - programming error
        raise KeyError(f"unknown control-plane concurrency metric: {name!r}")
    label = scope.value if scope is not None else "session"
    with _lock:
        _counts[(name, label)] += 1
    logger.info(
        "omnigent.control_plane.concurrency",
        extra={"metric": name, "fencing_scope": label},
    )


def record_revision_conflict(*, scope: FencingScope = FencingScope.SESSION_SUPERVISOR) -> None:
    _record(REVISION_CONFLICTS, scope=scope)


def record_fencing_conflict(*, scope: FencingScope) -> None:
    _record(FENCING_CONFLICTS, scope=scope)


def record_duplicate_command_suppressed() -> None:
    _record(DUPLICATE_COMMAND_SUPPRESSED)


def record_delivery_unknown_created() -> None:
    _record(DELIVERY_UNKNOWN_CREATED)


def record_delivery_unknown_reconciled() -> None:
    _record(DELIVERY_UNKNOWN_RECONCILED)


def record_stale_observation_retained() -> None:
    _record(STALE_OBSERVATION_RETAINED)


def record_cleanup_claim_conflict() -> None:
    _record(CLEANUP_CLAIM_CONFLICTS, scope=FencingScope.CLEANUP)


def record_outcome(outcome: ControlPlaneOutcome, *, scope: FencingScope) -> None:
    """Route a stable :class:`ControlPlaneOutcome` to the matching counter.

    Only conflict outcomes are counted; ``applied`` / ``already_applied`` are the
    healthy path and are not emitted as conflict telemetry.
    """

    if outcome is ControlPlaneOutcome.REVISION_CONFLICT:
        record_revision_conflict(scope=scope)
    elif outcome is ControlPlaneOutcome.FENCING_CONFLICT:
        record_fencing_conflict(scope=scope)
    elif outcome is ControlPlaneOutcome.DELIVERY_UNKNOWN:
        # Routing a fresh DELIVERY_UNKNOWN outcome records its *creation*; the
        # reconciled counter is emitted only when a parked command is later
        # confirmed at the authoritative delivery boundary (#3704).
        record_delivery_unknown_created()


def snapshot() -> Mapping[tuple[str, str], int]:
    """Return a copy of the current bounded counters (for exporters/tests)."""

    with _lock:
        return dict(_counts)


def reset() -> None:
    """Reset counters. Test-only; production counters are monotonic."""

    with _lock:
        _counts.clear()
