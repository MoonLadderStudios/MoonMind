"""Drain-ownership contract for the retired ManifestIngest workflow type.

Source issue: MoonLadderStudios/MoonMind#4192 (MR5 manifest removal).

The native Manifest/RAG ingestion product is retired: new
``MoonMind.ManifestIngest`` launches are rejected actionably at the schema
boundary (``CreateExecutionRequest``), the recurring boundary
(``RecurringWorkflowsService`` pauses existing ManifestIngest-targeted
schedules instead of recreating their Temporal actions), and the service
boundary (``TemporalExecutionService.create_execution`` / ``send_update``).
The workflow type is intentionally absent from the production worker
registration so no new worker polls for it, and ``manifest.compile`` /
``manifest.write_summary`` activity bindings are removed with it.

This module defines what counts as *drained* for that removal and gates
deployment of the removal. It mirrors the repository's existing drain rule
instead of inventing another topology mode: the decision predicate mirrors
``moonmind.workflows.executions.checkpoint_promotion.evaluate_worker_drain``
(``outstanding == 0`` → safe to deploy the removal, otherwise retain and
drain on the old release first). The deployment feeds the counts from live
probes — open ManifestIngest histories, pending manifest activity tasks,
and existing ManifestIngest-targeted schedules — rather than from fixture
replay or failed probes. Missing visibility or failed probes are not a
clean drain: unobservable dimensions must be reported as outstanding.

This module depends on the standard library only and lives in the
lightweight ``moonmind.gates`` namespace (whose ``__init__`` chain imports
nothing) so the gate stays importable from workflow-adjacent and tooling
contexts without pulling Temporal, database, or settings dependencies.
Do not add heavy imports here and do not re-export this module through a
heavy package ``__init__``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

#: Versioned contract name for the ManifestIngest removal drain gate.
#: Bump only with an explicit cutover plan; the gate is closed by default.
MANIFEST_INGEST_DRAIN_CONTRACT = "manifest-ingest-removal-drain-v1"

#: The retired Temporal workflow type whose open histories own the gate.
MANIFEST_INGEST_WORKFLOW_TYPE = "MoonMind.ManifestIngest"

#: Retired activity types whose pending tasks own the gate.
MANIFEST_INGEST_ACTIVITY_TYPES = (
    "manifest.compile",
    "manifest.write_summary",
)

#: Removal requires every outstanding dimension to reach zero, mirroring
#: ``evaluate_worker_drain`` (``mayRemove == (outstanding == 0)``).
REQUIRED_ACTION_RETAIN = "retain_and_drain"
REQUIRED_ACTION_REMOVE = "safe_to_remove"


@dataclass(frozen=True, slots=True)
class ManifestIngestDrainUsage:
    """Deployment-observed drain inputs for the ManifestIngest removal.

    Counts must come from live deployment probes, never from fixture replay:

    - ``open_manifest_ingest_histories``: open workflow histories of type
      ``MoonMind.ManifestIngest`` (Temporal visibility filtered to
      ``WorkflowType="MoonMind.ManifestIngest"`` and
      ``ExecutionStatus="Running"``).
    - ``pending_manifest_tasks``: pending activity tasks of type
      ``manifest.compile`` / ``manifest.write_summary`` still addressed to
      a live task queue.
    - ``existing_manifest_schedules``: enabled recurring definitions (or
      Temporal Schedules) still targeting ``MoonMind.ManifestIngest``.
    """

    open_manifest_ingest_histories: int = 0
    pending_manifest_tasks: int = 0
    existing_manifest_schedules: int = 0

    def __post_init__(self) -> None:
        for dimension in (
            "open_manifest_ingest_histories",
            "pending_manifest_tasks",
            "existing_manifest_schedules",
        ):
            value = getattr(self, dimension)
            if not isinstance(value, int) or value < 0:
                raise ValueError(
                    f"{dimension} must be a non-negative int, got {value!r}"
                )


@dataclass(frozen=True, slots=True)
class ManifestIngestDrainDecision:
    """Ownership verdict for the ManifestIngest removal."""

    contract: str = MANIFEST_INGEST_DRAIN_CONTRACT
    outstanding: int = 0
    may_deploy_removal: bool = False
    required_action: Literal["retain_and_drain", "safe_to_remove"] = (
        REQUIRED_ACTION_RETAIN
    )
    blocking_dimensions: tuple[str, ...] = field(default_factory=tuple)


def evaluate_manifest_ingest_drain(
    usage: ManifestIngestDrainUsage,
) -> ManifestIngestDrainDecision:
    """Decide whether the ManifestIngest removal may be deployed.

    The predicate is deliberately the same shape as
    ``evaluate_worker_drain``: deployment of the removal is allowed only
    when every outstanding dimension is zero. Any nonzero — or
    unobservable — dimension retains the old release for draining.
    """

    blocking = tuple(
        dimension
        for dimension, value in (
            ("open_manifest_ingest_histories", usage.open_manifest_ingest_histories),
            ("pending_manifest_tasks", usage.pending_manifest_tasks),
            ("existing_manifest_schedules", usage.existing_manifest_schedules),
        )
        if value > 0
    )
    outstanding = (
        usage.open_manifest_ingest_histories
        + usage.pending_manifest_tasks
        + usage.existing_manifest_schedules
    )
    may_remove = outstanding == 0
    return ManifestIngestDrainDecision(
        outstanding=outstanding,
        may_deploy_removal=may_remove,
        required_action=REQUIRED_ACTION_REMOVE if may_remove else REQUIRED_ACTION_RETAIN,
        blocking_dimensions=blocking,
    )


@dataclass(frozen=True, slots=True)
class ManifestIngestDrainObservations:
    """Deployment probe observations feeding the drain gate.

    Each dimension is a live deployment count or ``None`` when that
    dimension is unobservable (missing visibility, failed probe, or an
    explicitly unsupported schedule ledger). ``None`` is fail-closed: an
    unobservable dimension retains the old release, so fixture replay or a
    partial probe can never authorize the removal.

    Source dimensions (MoonLadderStudios/MoonMind#4192 MR5):

    - ``open_manifest_ingest_histories``: running workflows with
      ``WorkflowType="MoonMind.ManifestIngest"``.
    - ``pending_manifest_tasks``: pending ``manifest.compile`` /
      ``manifest.write_summary`` activity tasks.
    - ``existing_manifest_schedules``: enabled recurring definitions or
      Temporal Schedules still targeting the retired type.
    """

    open_manifest_ingest_histories: int | None = None
    pending_manifest_tasks: int | None = None
    existing_manifest_schedules: int | None = None

    def __post_init__(self) -> None:
        for dimension in (
            "open_manifest_ingest_histories",
            "pending_manifest_tasks",
            "existing_manifest_schedules",
        ):
            value = getattr(self, dimension)
            if value is None:
                continue
            if not isinstance(value, int) or value < 0:
                raise ValueError(
                    f"{dimension} must be a non-negative int or None, got {value!r}"
                )


def evaluate_manifest_ingest_drain_observations(
    observations: ManifestIngestDrainObservations,
) -> ManifestIngestDrainDecision:
    """Evaluate the drain gate from possibly-unobservable probe results.

    Fully observable inputs delegate to :func:`evaluate_manifest_ingest_drain`
    so the removal predicate stays identical to the canonical
    ``evaluate_worker_drain`` rule. Any ``None`` (unobservable/failed probe)
    dimension retains the old release and is named in
    ``blocking_dimensions``; each such dimension contributes one unit to
    ``outstanding`` so the verdict is never zero-outstanding while
    unobservable.
    """

    pairs = (
        ("open_manifest_ingest_histories", observations.open_manifest_ingest_histories),
        ("pending_manifest_tasks", observations.pending_manifest_tasks),
        ("existing_manifest_schedules", observations.existing_manifest_schedules),
    )
    unobservable = tuple(name for name, value in pairs if value is None)
    if not unobservable:
        return evaluate_manifest_ingest_drain(
            ManifestIngestDrainUsage(
                open_manifest_ingest_histories=(
                    observations.open_manifest_ingest_histories or 0
                ),
                pending_manifest_tasks=(observations.pending_manifest_tasks or 0),
                existing_manifest_schedules=(
                    observations.existing_manifest_schedules or 0
                ),
            )
        )
    known_outstanding = sum(value for _, value in pairs if isinstance(value, int))
    outstanding = known_outstanding + len(unobservable)
    blocking = unobservable + tuple(
        name for name, value in pairs if isinstance(value, int) and value > 0
    )
    return ManifestIngestDrainDecision(
        outstanding=outstanding,
        may_deploy_removal=False,
        required_action=REQUIRED_ACTION_RETAIN,
        blocking_dimensions=blocking,
    )


def retention_reason(decision: ManifestIngestDrainDecision) -> str:
    """Return a stable human-readable reason for the drain verdict."""

    if decision.may_deploy_removal:
        return (
            f"{MANIFEST_INGEST_DRAIN_CONTRACT}: drained "
            f"(outstanding={decision.outstanding}); ManifestIngest removal "
            "is unblocked, deploy the unregistered release"
        )
    blocking = ", ".join(decision.blocking_dimensions) or "unknown"
    return (
        f"{MANIFEST_INGEST_DRAIN_CONTRACT}: retain the old release for drain "
        f"(outstanding={decision.outstanding}; blocking: {blocking}); "
        "fixture replay is history compatibility, not deployed drainage"
    )


def collect_manifest_ingest_drain_observations(
    *,
    open_manifest_ingest_histories: int | None,
    pending_manifest_tasks: int | None,
    existing_manifest_schedules: int | None,
) -> ManifestIngestDrainObservations:
    """Build drain-gate observations from live deployment probe outputs.

    This is the production entrypoint that binds the gate to authoritative
    probes instead of fixture replay:

    - ``open_manifest_ingest_histories``: running workflows with
      ``WorkflowType="MoonMind.ManifestIngest"`` (``None`` when visibility
      is unavailable or the query failed).
    - ``pending_manifest_tasks``: pending ``manifest.compile`` /
      ``manifest.write_summary`` tasks (``None`` when activity inspection
      is unavailable).
    - ``existing_manifest_schedules``: enabled recurring definitions or
      Temporal Schedules targeting the retired type (``None`` when the
      schedule ledger is unsupported or unreadable).

    ``None`` is fail-closed downstream: unobservable dimensions retain the
    old release. Negative or non-integer counts raise ``ValueError`` so a
    malformed probe can never authorize removal.
    """

    return ManifestIngestDrainObservations(
        open_manifest_ingest_histories=open_manifest_ingest_histories,
        pending_manifest_tasks=pending_manifest_tasks,
        existing_manifest_schedules=existing_manifest_schedules,
    )


def render_manifest_ingest_drain_report(
    decision: ManifestIngestDrainDecision,
    *,
    observations: ManifestIngestDrainObservations | None = None,
) -> str:
    """Render the operator procedure that produced (or must produce) a verdict.

    The report names the exact live-deployment probes behind each dimension
    and, when the gate is open, the removal checklist that retires the
    ManifestIngest registration. It is the executable counterpart to the
    decision predicate: operators (or deployment tooling importing only this
    stdlib module) collect the three probe outputs, feed them through
    :func:`collect_manifest_ingest_drain_observations` and
    :func:`evaluate_manifest_ingest_drain_observations`, and follow the
    checklist below once ``may_deploy_removal`` is true.
    """

    blocking = ", ".join(decision.blocking_dimensions) or "none"
    lines = [
        f"{MANIFEST_INGEST_DRAIN_CONTRACT}: {decision.required_action}",
        f"outstanding={decision.outstanding}; blocking: {blocking}",
        "",
        "Probes (live deployment evidence; None = unobservable = retain):",
        "1. open_manifest_ingest_histories: list running workflows with",
        '   WorkflowType="MoonMind.ManifestIngest" AND ExecutionStatus="Running".',
        "2. pending_manifest_tasks: describe each running workflow and count",
        "   pendingActivities with activityType 'manifest.compile' or",
        "   'manifest.write_summary' still addressed to a live task queue.",
        "3. existing_manifest_schedules: count enabled recurring definitions",
        "   or Temporal Schedules whose target is MoonMind.ManifestIngest;",
        "   report None when the schedule ledger is unsupported or unreadable.",
        "",
    ]
    if observations is not None:
        lines.extend(
            [
                "Observed inputs:",
                f"  open_manifest_ingest_histories={observations.open_manifest_ingest_histories}",
                f"  pending_manifest_tasks={observations.pending_manifest_tasks}",
                f"  existing_manifest_schedules={observations.existing_manifest_schedules}",
                "",
            ]
        )
    if decision.may_deploy_removal:
        lines.extend(
            [
                "Removal checklist (all dimensions observed at zero):",
                "- deploy the release whose workflow fleet does not register",
                "  MoonMind.ManifestIngest and whose recurring reconciliation",
                "  pauses (never recreates) ManifestIngest-targeted schedules;",
                "- keep historical projection rows readable as old-release",
                "  replay/drain evidence; they never gate removal;",
                "- re-run this gate after removal to confirm the decision",
                "  stays drained.",
            ]
        )
    else:
        lines.extend(
            [
                "Retain the old release for drain; re-probe on the next",
                "maintenance window. Fixture replay is history compatibility,",
                "not deployed drainage.",
            ]
        )
    return "\n".join(lines)
