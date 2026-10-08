"""Drain-ownership contract for workflow-queue checkpoint compatibility.

Source issue: MoonLadderStudios/MoonMind#3949 (remaining scope item 3).

The ``checkpoint-branch-artifact-fleet-v1`` cutover (#4032) moved new
checkpoint persistence writes to the artifacts fleet, but the same handler
implementations stay registered on the workflow fleet so pre-cutover
histories recorded without a queue override can replay and drain. Fixture
replay (``test_checkpoint_queue_replay.py``) proves history compatibility
only; it is never deployed-drainage evidence.

This module defines what counts as *drained* for that compatibility
registration and gates its removal. It intentionally reuses the repository's
existing drain rule instead of inventing another topology mode or service:
the decision predicate mirrors
``moonmind.workflows.executions.checkpoint_promotion.evaluate_worker_drain``
(``outstanding == 0`` → safe to remove, otherwise retain). The deployment
collects the counts automatically through the existing Temporal service
route, ``TemporalExecutionService.observe_checkpoint_compat_drain`` (backed
by ``TemporalClientAdapter.observe_checkpoint_compat_drain``), which lists
``MoonMind.CheckpointBranchTurn`` executions through Visibility and scans
their histories for persistence scheduled off the artifacts queue — never
from fixture replay or failed probes. Missing visibility or failed probes
are not a clean drain: unobservable dimensions are reported as unknown and
retain compat. :func:`render_checkpoint_compat_drain_report` renders the
probe definitions and the removal checklist for a collected verdict.

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

#: Versioned contract name for the workflow-queue checkpoint drain gate.
#: Bump only with an explicit cutover plan; the gate is closed by default.
COMPAT_DRAIN_CONTRACT = "checkpoint-branch-artifact-fleet-drain-v1"

#: The patch whose pre-marker histories own the retained registration.
COMPAT_PATCH_ID = "checkpoint-branch-artifact-fleet-v1"

#: Removal requires every outstanding dimension to reach zero, mirroring
#: ``evaluate_worker_drain`` (``mayRemoveWorkerRoutes == (outstanding == 0)``).
REQUIRED_ACTION_RETAIN = "retain_compat"
REQUIRED_ACTION_REMOVE = "safe_to_remove"


@dataclass(frozen=True, slots=True)
class CheckpointCompatDrainUsage:
    """Deployment-observed drain inputs for the compat registration.

    Counts must come from live deployment probes, never from fixture replay.
    The dimensions are those produced by
    ``TemporalClientAdapter.observe_checkpoint_compat_drain`` (see
    :func:`collect_checkpoint_compat_drain_observations`): a
    ``MoonMind.CheckpointBranchTurn`` history is a compat consumer when it
    scheduled ``checkpoint_branch.turn.*`` persistence on a queue other
    than the artifacts queue. A missing patch marker alone does not make a
    history a consumer.

    - ``open_pre_cutover_histories``: running consumer histories.
    - ``pending_old_queue_tasks``: those running histories'
      ``checkpoint_branch.turn.*`` activities still unclosed on the old
      queue.
    - ``supported_resets_pending``: closed consumer histories Visibility
      still returns; resetting one replays onto the old route.
    """

    open_pre_cutover_histories: int = 0
    pending_old_queue_tasks: int = 0
    supported_resets_pending: int = 0

    def __post_init__(self) -> None:
        for dimension in (
            "open_pre_cutover_histories",
            "pending_old_queue_tasks",
            "supported_resets_pending",
        ):
            value = getattr(self, dimension)
            if not isinstance(value, int) or value < 0:
                raise ValueError(
                    f"{dimension} must be a non-negative int, got {value!r}"
                )


@dataclass(frozen=True, slots=True)
class CheckpointCompatDrainDecision:
    """Ownership verdict for the workflow-queue persistence registration."""

    contract: str = COMPAT_DRAIN_CONTRACT
    outstanding: int = 0
    may_remove_workflow_queue_handlers: bool = False
    required_action: Literal["retain_compat", "safe_to_remove"] = REQUIRED_ACTION_RETAIN
    blocking_dimensions: tuple[str, ...] = field(default_factory=tuple)


def evaluate_checkpoint_compat_drain(
    usage: CheckpointCompatDrainUsage,
) -> CheckpointCompatDrainDecision:
    """Decide whether the compat registration may be removed.

    The predicate is deliberately the same shape as
    ``evaluate_worker_drain``: removal is allowed only when every
    outstanding dimension is zero. Any nonzero — or unobservable —
    dimension retains the registration.
    """

    blocking = tuple(
        dimension
        for dimension, value in (
            ("open_pre_cutover_histories", usage.open_pre_cutover_histories),
            ("pending_old_queue_tasks", usage.pending_old_queue_tasks),
            ("supported_resets_pending", usage.supported_resets_pending),
        )
        if value > 0
    )
    outstanding = (
        usage.open_pre_cutover_histories
        + usage.pending_old_queue_tasks
        + usage.supported_resets_pending
    )
    may_remove = outstanding == 0
    return CheckpointCompatDrainDecision(
        outstanding=outstanding,
        may_remove_workflow_queue_handlers=may_remove,
        required_action=REQUIRED_ACTION_REMOVE if may_remove else REQUIRED_ACTION_RETAIN,
        blocking_dimensions=blocking,
    )


@dataclass(frozen=True, slots=True)
class CheckpointCompatDrainObservations:
    """Deployment probe observations feeding the drain gate.

    Each dimension is a live deployment count or ``None`` when that
    dimension is unobservable (a failed Visibility listing or an
    unreadable history). ``None`` is fail-closed: an unobservable
    dimension retains the compat registration, so fixture replay or a
    partial probe can never authorize removal.

    The dimensions have the meanings defined on
    :class:`CheckpointCompatDrainUsage`, as collected by
    ``TemporalClientAdapter.observe_checkpoint_compat_drain``: running and
    closed ``MoonMind.CheckpointBranchTurn`` histories that scheduled
    ``checkpoint_branch.turn.*`` persistence off the artifacts queue, plus
    the running histories' persistence activities still unclosed there.
    """

    open_pre_cutover_histories: int | None = None
    pending_old_queue_tasks: int | None = None
    supported_resets_pending: int | None = None

    def __post_init__(self) -> None:
        for dimension in (
            "open_pre_cutover_histories",
            "pending_old_queue_tasks",
            "supported_resets_pending",
        ):
            value = getattr(self, dimension)
            if value is None:
                continue
            if not isinstance(value, int) or value < 0:
                raise ValueError(
                    f"{dimension} must be a non-negative int or None, got {value!r}"
                )


def evaluate_checkpoint_compat_drain_observations(
    observations: CheckpointCompatDrainObservations,
) -> CheckpointCompatDrainDecision:
    """Evaluate the drain gate from possibly-unobservable probe results.

    Fully observable inputs delegate to :func:`evaluate_checkpoint_compat_drain`
    so the removal predicate stays identical to the canonical
    ``evaluate_worker_drain`` rule. Any ``None`` (unobservable/failed probe)
    dimension retains compat and is named in ``blocking_dimensions``; each
    such dimension contributes one unit to ``outstanding`` so the verdict is
    never zero-outstanding while unobservable.
    """

    pairs = (
        ("open_pre_cutover_histories", observations.open_pre_cutover_histories),
        ("pending_old_queue_tasks", observations.pending_old_queue_tasks),
        ("supported_resets_pending", observations.supported_resets_pending),
    )
    unobservable = tuple(name for name, value in pairs if value is None)
    if not unobservable:
        return evaluate_checkpoint_compat_drain(
            CheckpointCompatDrainUsage(
                open_pre_cutover_histories=(
                    observations.open_pre_cutover_histories or 0
                ),
                pending_old_queue_tasks=(observations.pending_old_queue_tasks or 0),
                supported_resets_pending=(observations.supported_resets_pending or 0),
            )
        )
    known_outstanding = sum(value for _, value in pairs if isinstance(value, int))
    outstanding = known_outstanding + len(unobservable)
    blocking = unobservable + tuple(
        name for name, value in pairs if isinstance(value, int) and value > 0
    )
    return CheckpointCompatDrainDecision(
        outstanding=outstanding,
        may_remove_workflow_queue_handlers=False,
        required_action=REQUIRED_ACTION_RETAIN,
        blocking_dimensions=blocking,
    )


def retention_reason(decision: CheckpointCompatDrainDecision) -> str:
    """Return a stable human-readable reason for the drain verdict."""

    if decision.may_remove_workflow_queue_handlers:
        return (
            f"{COMPAT_DRAIN_CONTRACT}: drained "
            f"(outstanding={decision.outstanding}); compat removal is unblocked, "
            "delete the workflow-queue handlers, dead DI, and permissions together"
        )
    blocking = ", ".join(decision.blocking_dimensions) or "unknown"
    return (
        f"{COMPAT_DRAIN_CONTRACT}: retain workflow-queue checkpoint handlers "
        f"(outstanding={decision.outstanding}; blocking: {blocking}); "
        "fixture replay is history compatibility, not deployed drainage"
    )


def collect_checkpoint_compat_drain_observations(
    *,
    open_pre_cutover_histories: int | None,
    pending_old_queue_tasks: int | None,
    supported_resets_pending: int | None,
) -> CheckpointCompatDrainObservations:
    """Build drain-gate observations from live deployment probe outputs.

    ``TemporalClientAdapter.observe_checkpoint_compat_drain`` is the
    automated producer of these inputs:

    - ``open_pre_cutover_histories``: running ``MoonMind.CheckpointBranchTurn``
      histories that scheduled ``checkpoint_branch.turn.*`` persistence off
      the artifacts queue (``None`` when Visibility or a history read fails).
    - ``pending_old_queue_tasks``: those histories' persistence activities
      still unclosed on the old queue (``None`` on the same failures).
    - ``supported_resets_pending``: closed consumer histories Visibility
      still returns; a reset of one replays onto the old route (``None``
      when the closed listing or a history read fails).

    ``None`` is fail-closed downstream: unobservable dimensions retain the
    compat registration. Negative or non-integer counts raise ``ValueError``
    so a malformed probe can never authorize removal.
    """

    return CheckpointCompatDrainObservations(
        open_pre_cutover_histories=open_pre_cutover_histories,
        pending_old_queue_tasks=pending_old_queue_tasks,
        supported_resets_pending=supported_resets_pending,
    )


def render_checkpoint_compat_drain_report(
    decision: CheckpointCompatDrainDecision,
    *,
    observations: CheckpointCompatDrainObservations | None = None,
    workflow_task_queue: str = "mm.workflow",
) -> str:
    """Render a drain verdict with its probe definitions and removal checklist.

    The report names the dimensions
    ``TemporalExecutionService.observe_checkpoint_compat_drain`` collects
    automatically, the observed inputs when supplied, and, when the gate is
    open, the removal checklist that retires the workflow-queue
    registration. It renders a verdict already produced by
    :func:`evaluate_checkpoint_compat_drain_observations`; it does not run
    probes or perform removal.
    """

    blocking = ", ".join(decision.blocking_dimensions) or "none"
    lines = [
        f"{COMPAT_DRAIN_CONTRACT}: {decision.required_action}",
        f"outstanding={decision.outstanding}; blocking: {blocking}",
        "",
        "Probes (collected by TemporalExecutionService.",
        "observe_checkpoint_compat_drain; None = unobservable = retain):",
        "1. open_pre_cutover_histories: running MoonMind.CheckpointBranchTurn",
        "   histories that scheduled checkpoint_branch.turn.* persistence on",
        f"   an old queue such as '{workflow_task_queue}' (before or without",
        f"   the '{COMPAT_PATCH_ID}' route). A missing marker alone is not",
        "   enough: new runs record it at their first persistence call.",
        "2. pending_old_queue_tasks: those histories' persistence activities",
        "   still unclosed on the old queue.",
        "3. supported_resets_pending: closed consumer histories Visibility",
        "   still returns; resetting one replays onto the old route.",
        "",
    ]
    if observations is not None:
        lines.extend(
            [
                "Observed inputs:",
                f"  open_pre_cutover_histories={observations.open_pre_cutover_histories}",
                f"  pending_old_queue_tasks={observations.pending_old_queue_tasks}",
                f"  supported_resets_pending={observations.supported_resets_pending}",
                "",
            ]
        )
    if decision.may_remove_workflow_queue_handlers:
        lines.extend(
            [
                "Removal checklist (all dimensions observed at zero):",
                "- delete the checkpoint-branch handlers from the workflow",
                "  fleet registration together with their dead DI bindings",
                "  and permissions in the same change;",
                "- keep fixture replay tests as history-compatibility",
                "  evidence; they never gate removal;",
                "- re-run this gate after removal to confirm the decision",
                "  stays drained.",
            ]
        )
    else:
        lines.extend(
            [
                "Retain the workflow-queue checkpoint handlers; re-probe on",
                "the next maintenance window. Fixture replay is history",
                "compatibility, not deployed drainage.",
            ]
        )
    return "\n".join(lines)
