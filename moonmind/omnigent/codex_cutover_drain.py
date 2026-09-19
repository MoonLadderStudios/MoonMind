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
import importlib
import json
import os
from typing import Any, Mapping, Sequence

#: The single deployment-owned cutoff. ISO-8601 instant after which no new
#: work may enter the retired direct-Codex lane. Unset means the lane is not
#: retired and the usable path is preserved.
DEPLOYMENT_CUTOFF_ENV = "MOONMIND_CODEX_DIRECT_RETIRED_AT"

#: Deployment-declared linked qualification for the generic Codex combination
#: (MoonLadderStudios/MoonMind#3931 R1). The reference plus SHA-256 digest name
#: independently resolvable observed evidence; the dimensions document carries
#: the exact qualified combination. When none of these are configured, generic
#: promotion defers to the existing boolean qualification chain so the usable
#: path is preserved until its qualified replacement is declared.
LINKED_QUALIFICATION_REF_ENV = "MOONMIND_CODEX_GENERIC_QUALIFICATION_REF"
LINKED_QUALIFICATION_DIGEST_ENV = "MOONMIND_CODEX_GENERIC_QUALIFICATION_DIGEST"
LINKED_QUALIFICATION_RESULT_ENV = "MOONMIND_CODEX_GENERIC_QUALIFICATION_RESULT"
LINKED_QUALIFICATION_DIMENSIONS_ENV = (
    "MOONMIND_CODEX_GENERIC_QUALIFICATION_DIMENSIONS"
)

#: Deployment-declared selected generic Codex combination (JSON object with the
#: exact dimensions in :data:`REQUIRED_SUPPORT_DIMENSIONS`). Read by
#: :func:`resolve_generic_codex_selection` so the rollout policy can bind the
#: declared selection to its linked qualification evidence.
GENERIC_SELECTION_ENV = "MOONMIND_CODEX_GENERIC_SELECTION"

#: Boolean qualification switch owned by #3832. Read here with the same
#: fail-closed default (false) as ``settings.generic_codex_qualified`` so this
#: module never loosens the existing gate; it only adds exactness once linked
#: evidence is declared.
GENERIC_CODEX_QUALIFIED_ENV = "MOONMIND_OMNIGENT_GENERIC_CODEX_QUALIFIED"

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

#: Minimum runtime mechanism behind each retained branch. A fixture alone is
#: never the mechanism: every branch resolves to an importable production
#: symbol that still serves retained histories or persisted inputs until the
#: branch's removal condition is met. :func:`verify_retained_branch_runtime`
#: proves the binding; deleting a bound symbol without meeting the removal
#: condition breaks that proof instead of silently dropping compatibility.
BRANCH_RUNTIME_BINDINGS: dict[str, str] = {
    # Old conformance/promotion evidence still decodes through the cutover
    # boundary so retained histories replay.
    "temporal_history_decoders": "moonmind.omnigent.cutover:evaluate_promotion",
    # Already-recorded raw-path payloads decode only through this named
    # historical path; new authoring must use ``workspaceSource``.
    "persisted_input_decoders": (
        "moonmind.omnigent.workspace_sources:decode_legacy_workspace_path"
    ),
    # Persisted sessions/provenance/journal/artifacts/checkpoints stay
    # readable without a live worker through the control-plane repositories.
    "historical_read_model": "moonmind.omnigent.control_plane.repositories",
    # Supported reset/replay operations route by worker deployment version.
    "supported_worker_routing": (
        "moonmind.workflows.temporal.release_routing:current_version"
    ),
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


def _boolean_qualified(values: Mapping[str, Any]) -> bool:
    return str(values.get(GENERIC_CODEX_QUALIFIED_ENV) or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def resolve_generic_codex_selection(
    env: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Return the deployment-declared generic Codex selection, if any.

    Unset means the deployment has not named its exact selected combination;
    callers preserve existing behavior in that case rather than guessing.
    """

    values = os.environ if env is None else env
    raw = str(values.get(GENERIC_SELECTION_ENV) or "").strip()
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(
            "generic_selection_invalid: MOONMIND_CODEX_GENERIC_SELECTION "
            "must be a JSON object"
        ) from exc
    if not isinstance(payload, Mapping):
        raise ValueError(
            "generic_selection_invalid: MOONMIND_CODEX_GENERIC_SELECTION "
            "must be a JSON object"
        )
    return dict(payload)


def resolve_linked_generic_qualification(
    env: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Return the deployment-declared linked qualification, if any.

    ``None`` means no linked evidence is declared and the caller defers to the
    existing boolean qualification chain. A declared reference with a bad
    digest, a non-pass observed result, or missing dimensions raises: declared
    evidence is verified exactly, never inferred.
    """

    values = os.environ if env is None else env
    ref = str(values.get(LINKED_QUALIFICATION_REF_ENV) or "").strip()
    if not ref:
        return None
    digest = str(values.get(LINKED_QUALIFICATION_DIGEST_ENV) or "").strip().lower()
    if len(digest) != 64 or any(
        character not in "0123456789abcdef" for character in digest
    ):
        raise ValueError("linked_qualification_digest_required: lowercase SHA-256 required")
    if str(values.get(LINKED_QUALIFICATION_RESULT_ENV) or "").strip() != "passed":
        raise ValueError("linked_qualification_not_observed: observed_result must be passed")
    raw_dimensions = str(values.get(LINKED_QUALIFICATION_DIMENSIONS_ENV) or "").strip()
    try:
        dimensions = json.loads(raw_dimensions) if raw_dimensions else None
    except json.JSONDecodeError as exc:
        raise ValueError(
            "support_dimension_mismatch: qualification dimensions missing"
        ) from exc
    if not isinstance(dimensions, Mapping):
        raise ValueError(
            "support_dimension_mismatch: qualification dimensions missing"
        )
    return {
        "linked_qualification_ref": ref,
        "qualification_digest": digest,
        "observed_result": "passed",
        "dimensions": dict(dimensions),
    }


def generic_codex_promotion_permitted(
    selection: Mapping[str, Any] | None,
    env: Mapping[str, Any] | None = None,
) -> bool:
    """Return whether the generic Codex row may promote for new defaults.

    The boolean qualification switch stays fail-closed: false never promotes.
    While no linked evidence is declared, a true boolean preserves the existing
    promotion so the usable path survives until its qualified replacement is
    declared. Once linked evidence is declared, the declared selection must
    match it exactly via :func:`require_exact_generic_support`; mismatches,
    missing selections, and invalid declarations fail closed to ``False``
    (explicit-only, never a fallback to different credentials, a different
    runtime, or a less-constrained path).
    """

    values = os.environ if env is None else env
    if not _boolean_qualified(values):
        return False
    try:
        qualification = resolve_linked_generic_qualification(values)
    except ValueError:
        return False
    if qualification is None:
        return True
    if not isinstance(selection, Mapping):
        return False
    try:
        require_exact_generic_support(selection, qualification)
    except ValueError:
        return False
    return True


def collect_drain_inventory(
    sources: Mapping[str, Any] | None,
) -> dict[str, list[dict[str, Any]]]:
    """Collect one drain inventory from injectable per-category sources.

    Each category maps to either a ready item list or a zero-argument callable
    returning one. A raising source becomes a single ``unknown`` item: unknown
    visibility is reported, never treated as permission to delete. Categories
    with no source are left missing so :func:`build_drain_report` fails closed
    with ``drain_inventory_category_missing``. Live Temporal visibility,
    schedule, lease, publication, and cleanup queries are injected here by the
    operator's drain procedure; this collector owns the failure semantics, not
    the live clients.
    """

    data = dict(sources or {})
    inventory: dict[str, list[dict[str, Any]]] = {}
    for category in DRAIN_INVENTORY_CATEGORIES:
        if category not in data:
            continue
        source = data[category]
        if callable(source):
            try:
                items = source()
            except Exception:
                inventory[category] = [
                    {
                        "id": f"{category}-unreachable",
                        "state": "unknown",
                    }
                ]
                continue
        else:
            items = source
        inventory[category] = list(items)
    return inventory


def _resolve_binding_target(target: str) -> dict[str, Any]:
    module_name, _, attribute = target.partition(":")
    try:
        module = importlib.import_module(module_name)
        if attribute:
            getattr(module, attribute)
        return {"resolved": True, "target": target}
    except (ImportError, AttributeError):
        return {"resolved": False, "target": target}


def verify_retained_branch_runtime(
    branch: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Prove every retained branch still has its runtime mechanism.

    Returns per-branch ``{resolved, target}`` statuses. With a branch name,
    verifies only that branch; unknown names raise. A ``resolved: False``
    status means the minimum implementation or routing path is gone while its
    consumer/removal condition still names it, so removal work must restore
    the mechanism or meet the removal condition first.
    """

    names = [branch] if branch is not None else list(BRANCH_RUNTIME_BINDINGS)
    statuses: dict[str, dict[str, Any]] = {}
    for name in names:
        target = BRANCH_RUNTIME_BINDINGS.get(str(name or "").strip())
        if target is None:
            raise ValueError(f"unknown_retained_branch:{branch!r}")
        statuses[str(name)] = _resolve_binding_target(target)
    return statuses


def cutover_authorities() -> dict[str, Any]:
    """Name the three cutover authorities without adding a state machine.

    Rollout authority stays in ``moonmind.omnigent.cutover`` (``CutoverPhase``),
    retirement authority stays in ``moonmind.omnigent.legacy_retirement``
    (``RetirementClass``), and deployment-owned retirement of the direct lane
    is exactly one instant (``MOONMIND_CODEX_DIRECT_RETIRED_AT``). This helper
    records that settlement so overlapping switches converge on the single
    cutoff instead of growing a competing phase machine.
    """

    return {
        "authorities": ("rollout", "retirement", "deployment_cutoff"),
        "deployment_cutoff_env": DEPLOYMENT_CUTOFF_ENV,
        "adds_state_machine": False,
    }


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
    "BRANCH_RUNTIME_BINDINGS",
    "BOUNDARY_COVERAGE",
    "GENERIC_SELECTION_ENV",
    "GENERIC_CODEX_QUALIFIED_ENV",
    "LINKED_QUALIFICATION_REF_ENV",
    "LINKED_QUALIFICATION_DIGEST_ENV",
    "LINKED_QUALIFICATION_RESULT_ENV",
    "LINKED_QUALIFICATION_DIMENSIONS_ENV",
    "DrainReport",
    "parse_deployment_cutoff",
    "direct_retired_by_cutoff",
    "assert_new_admission_allowed",
    "require_exact_generic_support",
    "resolve_generic_codex_selection",
    "resolve_linked_generic_qualification",
    "generic_codex_promotion_permitted",
    "retained_disposition",
    "verify_retained_branch_runtime",
    "cutover_authorities",
    "build_drain_report",
    "collect_drain_inventory",
]
