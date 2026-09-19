"""Minimal verified drain-and-delete path for the obsolete direct-Codex lane.

Source issue: MoonLadderStudios/MoonMind#3931.

This module is the single deployment-owned cutoff and bounded drain procedure
the issue requires. It deliberately adds no rollout state machine: rollout
authority stays in ``moonmind.omnigent.cutover`` (``CutoverPhase``) and
retirement authority stays in ``moonmind.omnigent.legacy_retirement``
(``RetirementClass``). This module only:

* names the one deployment-owned retirement instant
  (``MOONMIND_CODEX_DIRECT_RETIRED_AT``) that closes the direct lane to new
  work while preserving the usable path until a qualified replacement exists;
* requires exact, linked generic support evidence for a selected combination
  (image, runtime pack, materializer, ownership mode, capabilities) and never
  falls back to different credentials, a different runtime, or a
  less-constrained path;
* inventories durable workflow and resource authority with four-state
  reporting (clean/active/unknown/blocked) without terminating workflows,
  erasing evidence, or deleting profile-owned credential volumes;
* records the explicit retained-history disposition: every retained branch
  names its consumer, its removal condition, and whether the mechanism is the
  minimum actual implementation or an explicit supported worker routing path.

All helpers are pure and side-effect-free. The report never authorizes
deletion itself (``authorizesDeletion`` is always ``False``); deletion is a
separate operator action taken only when the report is fully clean.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import os
from typing import Any, Mapping, Sequence

#: The single deployment-owned cutoff. ISO-8601 instant after which no new
#: work may enter the retired direct-Codex lane. Unset means the lane is not
#: retired and the usable path is preserved.
DEPLOYMENT_CUTOFF_ENV = "MOONMIND_CODEX_DIRECT_RETIRED_AT"

#: Runtime ids that count as the retired direct lane.
RETIRED_DIRECT_RUNTIME_IDS = frozenset({"codex_cli", "codex-direct", "direct"})

#: Bounded drain inventory. Covers durable workflow and resource authority,
#: not only an OPEN visibility query.
DRAIN_INVENTORY_CATEGORIES: tuple[str, ...] = (
    "schedules",
    "queued_starts",
    "open_parent_workflows",
    "open_child_workflows",
    "retries",
    "pending_interventions",
    "serialized_launch_inputs",
    "resource_leases",
    "publication_work",
    "cleanup_work",
)

#: The only permitted per-resource states. Unknown visibility is never
#: permission to delete.
RESOURCE_STATES: tuple[str, ...] = ("clean", "active", "unknown", "blocked")

#: Exact support dimensions a generic selection must prove. Registry
#: membership or a shared-image build alone proves none of these.
REQUIRED_SUPPORT_DIMENSIONS: tuple[str, ...] = (
    "image",
    "runtime_pack",
    "materializer",
    "ownership_mode",
    "capabilities",
)

#: Retained compatibility branches. Each names the real consumer that still
#: needs it, the condition that allows its removal, and the mechanism that
#: serves it until then. A fixture alone is never the mechanism.
RETAINED_BRANCHES: tuple[dict[str, str], ...] = (
    {
        "branch": "temporal_history_decoders",
        "consumer": "open and retained Temporal histories that still schedule or retry direct-Codex bindings",
        "removal_condition": "no open history can schedule/retry the binding and retention policy permits removal",
        "mechanism": "retain_minimum_implementation",
    },
    {
        "branch": "persisted_input_decoders",
        "consumer": "old serialized launch inputs awaiting drain or replay",
        "removal_condition": "all retained inputs drained or retention elapsed with lossless migration proven",
        "mechanism": "retain_minimum_implementation",
    },
    {
        "branch": "historical_read_model",
        "consumer": "persisted sessions, provenance, event journal, artifacts and checkpoints rendered without a live worker",
        "removal_condition": "retention elapsed and lossless migration or approved archival proven",
        "mechanism": "retain_minimum_implementation",
    },
    {
        "branch": "supported_worker_routing",
        "consumer": "supported reset/replay operations that must execute on a worker build that replays the same histories",
        "removal_condition": "worker fleet no longer serves pre-cutover histories and rollback no longer routes to them",
        "mechanism": "supported_worker_routing",
    },
)

_BRANCH_INDEX: dict[str, dict[str, str]] = {
    branch["branch"]: dict(branch) for branch in RETAINED_BRANCHES
}

#: Changed-boundary coverage. Each boundary maps to the drain/disposition
#: evidence that exercises it.
BOUNDARY_COVERAGE: dict[str, str] = {
    "restart": "open_parent_workflows/open_child_workflows drain states plus temporal_history_decoders disposition",
    "cancellation": "pending_interventions drain states plus supported_worker_routing disposition",
    "retry": "retries drain states plus temporal_history_decoders disposition",
    "completed_workflow_reset": "supported_worker_routing disposition with reset-policy removal condition",
    "credential_preservation": "resource_leases drain states; profile-owned credential volumes are never deleted to make a report green",
    "publication_recovery": "publication_work drain states plus pending finalization drainage",
    "final_cleanup": "cleanup_work drain states gated on a fully clean report",
}


def parse_deployment_cutoff(value: Any) -> datetime | None:
    """Parse the deployment-owned cutoff instant; blank means not retired."""

    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(
            f"deployment_cutoff_invalid:{text!r}: expected ISO-8601 instant"
        ) from exc
    if parsed.tzinfo is None:
        raise ValueError(
            f"deployment_cutoff_invalid:{text!r}: timezone-aware instant required"
        )
    return parsed


def direct_retired_by_cutoff(
    *,
    env: Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> bool:
    """Return True only when the deployment-owned cutoff has passed."""

    values = os.environ if env is None else env
    cutoff = parse_deployment_cutoff(values.get(DEPLOYMENT_CUTOFF_ENV))
    if cutoff is None:
        return False
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current >= cutoff


def _is_direct_runtime(runtime_id: str) -> bool:
    return str(runtime_id or "").strip().lower() in RETIRED_DIRECT_RUNTIME_IDS


def assert_new_admission_allowed(
    runtime_id: str,
    *,
    env: Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> None:
    """Reject new work that selects the retired direct lane.

    Already-recorded plans are unaffected: this boundary only guards new
    admission. The surviving Omnigent path always passes.
    """

    if not _is_direct_runtime(runtime_id):
        return
    if direct_retired_by_cutoff(env=env, now=now):
        raise ValueError(
            "codex_direct_retired_by_deployment_cutoff:"
            f"{str(runtime_id).strip().lower()}: new work cannot enter a retired lane"
        )


def require_exact_generic_support(
    selection: Mapping[str, Any],
    qualification: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Bind a generic selection to its linked qualification evidence.

    Both the selected combination and the qualification must carry the exact
    dimensions (image, runtime pack, materializer, ownership mode,
    capabilities). Qualification must link independently resolvable evidence
    (``linked_qualification_ref`` plus a SHA-256 ``qualification_digest`` over
    an observed ``passed`` result). Registry membership or a shared-image
    build is never sufficient, and this function never returns an alternate
    credential set, runtime, or path: mismatches raise.
    """

    if not isinstance(selection, Mapping):
        raise ValueError("support_selection_invalid: selection must be an object")
    missing = [
        dimension
        for dimension in REQUIRED_SUPPORT_DIMENSIONS
        if not selection.get(dimension)
    ]
    if missing:
        raise ValueError(
            f"support_selection_incomplete: missing dimensions: {sorted(missing)}"
        )
    capabilities = selection.get("capabilities")
    if not isinstance(capabilities, (list, tuple)) or not [
        item for item in capabilities if str(item).strip()
    ]:
        raise ValueError("support_selection_incomplete: capabilities must be nonempty")
    if not isinstance(qualification, Mapping):
        raise ValueError(
            "linked_qualification_ref_required: replacement qualification must be linked, "
            "not inferred from registry membership or a shared image build"
        )
    ref = str(qualification.get("linked_qualification_ref") or "").strip()
    if not ref:
        raise ValueError(
            "linked_qualification_ref_required: replacement qualification must be linked, "
            "not inferred from registry membership or a shared image build"
        )
    digest = str(qualification.get("qualification_digest") or "").strip().lower()
    if len(digest) != 64 or any(
        character not in "0123456789abcdef" for character in digest
    ):
        raise ValueError("linked_qualification_digest_required: lowercase SHA-256 required")
    if qualification.get("observed_result") != "passed":
        raise ValueError("linked_qualification_not_observed: observed_result must be passed")
    dimensions = qualification.get("dimensions")
    if not isinstance(dimensions, Mapping):
        raise ValueError("support_dimension_mismatch: qualification dimensions missing")
    mismatched = []
    for dimension in REQUIRED_SUPPORT_DIMENSIONS:
        expected = selection.get(dimension)
        observed = dimensions.get(dimension)
        if isinstance(expected, (list, tuple)):
            if sorted(str(item) for item in expected) != sorted(
                str(item) for item in (observed if isinstance(observed, (list, tuple)) else [])
            ):
                mismatched.append(dimension)
        elif str(observed or "").strip() != str(expected or "").strip():
            mismatched.append(dimension)
    if mismatched:
        raise ValueError(
            f"support_dimension_mismatch: dimensions differ: {sorted(mismatched)}"
        )
    return {
        "image": str(selection["image"]),
        "runtime_pack": str(selection["runtime_pack"]),
        "materializer": str(selection["materializer"]),
        "ownership_mode": str(selection["ownership_mode"]),
        "capabilities": [str(item) for item in capabilities],
        "qualification_ref": ref,
        "qualification_digest": digest,
    }


def retained_disposition(branch: str) -> dict[str, str]:
    """Return the explicit disposition for one retained branch."""

    record = _BRANCH_INDEX.get(str(branch or "").strip())
    if record is None:
        raise ValueError(f"unknown_retained_branch:{branch!r}")
    return dict(record)


@dataclass(frozen=True, slots=True)
class DrainReport:
    """Bounded four-state drain report. Pure evidence; authorizes nothing."""

    states: dict[str, int] = field(default_factory=dict)
    blockers: tuple[str, ...] = ()
    categories: tuple[str, ...] = DRAIN_INVENTORY_CATEGORIES

    @property
    def deletable(self) -> bool:
        return (
            not self.blockers
            and self.states.get("active", 0) == 0
            and self.states.get("unknown", 0) == 0
            and self.states.get("blocked", 0) == 0
        )

    def as_dict(self) -> dict[str, Any]:
        states = {state: int(self.states.get(state, 0)) for state in RESOURCE_STATES}
        return {
            "categories": list(self.categories),
            "states": states,
            "blockers": list(self.blockers),
            # The report is evidence for an operator decision. It never
            # performs termination, erases evidence, or deletes
            # profile-owned credential volumes.
            "authorizesDeletion": False,
            "deletable": self.deletable,
            "summary": (
                "Drain is clean; an operator may proceed with the separately "
                "authorized deletion."
                if self.deletable
                else "Drain is not clean: active, unknown, or blocked resources "
                "remain; unknown visibility is not permission to delete."
            ),
        }


def build_drain_report(
    inventory: Mapping[str, Sequence[Mapping[str, Any]]] | None,
) -> DrainReport:
    """Build a bounded drain report over durable and resource authority.

    Every category in :data:`DRAIN_INVENTORY_CATEGORIES` must be present; a
    missing category fails closed as unknown. Item states must be one of the
    four resource states. The report performs no termination or deletion.
    """

    data = dict(inventory or {})
    blockers: list[str] = []
    counts = {state: 0 for state in RESOURCE_STATES}
    for category in DRAIN_INVENTORY_CATEGORIES:
        if category not in data:
            blockers.append(f"drain_inventory_category_missing:{category}")
            continue
        items = data[category]
        if not isinstance(items, Sequence) or isinstance(items, (str, bytes)):
            blockers.append(f"drain_inventory_category_invalid:{category}")
            continue
        for item in items:
            state = str(item.get("state") if isinstance(item, Mapping) else "").strip().lower()
            if state not in RESOURCE_STATES:
                blockers.append(f"drain_inventory_state_invalid:{category}:{state!r}")
                continue
            counts[state] += 1
    return DrainReport(
        states=counts,
        blockers=tuple(dict.fromkeys(blockers)),
    )


__all__ = [
    "DEPLOYMENT_CUTOFF_ENV",
    "RETIRED_DIRECT_RUNTIME_IDS",
    "DRAIN_INVENTORY_CATEGORIES",
    "RESOURCE_STATES",
    "REQUIRED_SUPPORT_DIMENSIONS",
    "RETAINED_BRANCHES",
    "BOUNDARY_COVERAGE",
    "DrainReport",
    "parse_deployment_cutoff",
    "direct_retired_by_cutoff",
    "assert_new_admission_allowed",
    "require_exact_generic_support",
    "retained_disposition",
    "build_drain_report",
]
