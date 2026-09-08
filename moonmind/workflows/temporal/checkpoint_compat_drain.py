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
feeds the counts from the existing mechanisms — ``get_drain_metrics``
scoped to the workflow task queue, pending-activity inspection for old-queue
tasks, and the supported reset ledger — rather than from fixture replay or
failed probes. Missing visibility or failed probes are not a clean drain:
unobservable dimensions must be reported as outstanding.

This module depends on the standard library only so the gate stays
importable from workflow-adjacent and tooling contexts without pulling
Temporal, database, or settings dependencies.
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

    Counts must come from live deployment probes, never from fixture replay:

    - ``open_pre_cutover_histories``: open workflow histories recorded
      without the artifacts-fleet patch marker (``get_drain_metrics``
      scoped to the workflow task queue, filtered to pre-marker histories).
    - ``pending_old_queue_tasks``: pending activity tasks still addressed
      to the workflow queue for ``checkpoint_branch.turn.*`` types.
    - ``supported_resets_pending``: retained histories with a supported
      reset obligation that has not been discharged.
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
    dimension is unobservable (missing visibility, failed probe, or an
    explicitly unsupported reset ledger). ``None`` is fail-closed: an
    unobservable dimension retains the compat registration, so fixture
    replay or a partial probe can never authorize removal.

    Source dimensions (MoonLadderStudios/MoonMind#3949 scope 3):

    - ``open_pre_cutover_histories``: ``get_drain_metrics`` scoped to the
      workflow task queue, filtered to histories recorded without the
      ``checkpoint-branch-artifact-fleet-v1`` marker.
    - ``pending_old_queue_tasks``: pending-activity inspection for
      ``checkpoint_branch.turn.*`` tasks still addressed to the workflow
      queue.
    - ``supported_resets_pending``: retained histories with a supported
      reset obligation that has not been discharged.
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
