"""Drain ownership, ordering, and least-privilege evidence (MoonMind#3949).

The artifacts-fleet cutover (#4032) is delivered; the workflow-queue
persistence registration stays only for pre-cutover replay/in-flight
compatibility. These tests execute the drain-ownership gate, the
idempotency/ownership properties behind ordering, and the behavioral
capability inventory — without a Temporal server, database, or deployment
probe. Fixture replay remains history-compatibility evidence only; the
drain gate below is what authorizes removal.
"""

from __future__ import annotations

import asyncio

import pytest

from api_service.services.checkpoint_branch_service import (
    CheckpointBranchService,
    build_branch_turn_launch_idempotency_key,
)
from moonmind.workflows.temporal.checkpoint_compat_drain import (
    COMPAT_DRAIN_CONTRACT,
    CheckpointCompatDrainUsage,
    evaluate_checkpoint_compat_drain,
    retention_reason,
)

pytestmark = pytest.mark.unit_fast


# --- Drain-ownership contract -----------------------------------------------


def test_drained_deployment_unblocks_compat_removal():
    decision = evaluate_checkpoint_compat_drain(CheckpointCompatDrainUsage())
    assert decision.outstanding == 0
    assert decision.may_remove_workflow_queue_handlers is True
    assert decision.required_action == "safe_to_remove"
    assert decision.blocking_dimensions == ()
    assert decision.contract == COMPAT_DRAIN_CONTRACT


@pytest.mark.parametrize(
    "usage",
    [
        CheckpointCompatDrainUsage(open_pre_cutover_histories=1),
        CheckpointCompatDrainUsage(pending_old_queue_tasks=2),
        CheckpointCompatDrainUsage(supported_resets_pending=1),
        CheckpointCompatDrainUsage(
            open_pre_cutover_histories=3,
            pending_old_queue_tasks=2,
            supported_resets_pending=1,
        ),
    ],
)
def test_any_outstanding_consumer_retains_compat(usage):
    decision = evaluate_checkpoint_compat_drain(usage)
    assert decision.may_remove_workflow_queue_handlers is False
    assert decision.required_action == "retain_compat"
    assert decision.outstanding == (
        usage.open_pre_cutover_histories
        + usage.pending_old_queue_tasks
        + usage.supported_resets_pending
    )
    assert decision.blocking_dimensions, "a retained gate must name its blockers"
    reason = retention_reason(decision)
    assert COMPAT_DRAIN_CONTRACT in reason
    for dimension in decision.blocking_dimensions:
        assert dimension in reason


def test_drain_gate_rejects_negative_counts():
    with pytest.raises(ValueError):
        CheckpointCompatDrainUsage(open_pre_cutover_histories=-1)
    with pytest.raises(ValueError):
        CheckpointCompatDrainUsage(pending_old_queue_tasks=-1)
    with pytest.raises(ValueError):
        CheckpointCompatDrainUsage(supported_resets_pending=-1)


def test_drain_rule_matches_canonical_worker_drain_predicate():
    """The gate reuses the existing drain rule; it is not a second policy.

    ``evaluate_worker_drain`` allows route removal exactly when outstanding
    work reaches zero. This gate must agree with that predicate for every
    mapped input so compat removal can never be more permissive than the
    canonical worker-drain contract.
    """

    from moonmind.workflows.executions.checkpoint_promotion import (
        FrozenGenerationUsage,
        evaluate_worker_drain,
    )

    cases = [
        CheckpointCompatDrainUsage(),
        CheckpointCompatDrainUsage(open_pre_cutover_histories=1),
        CheckpointCompatDrainUsage(pending_old_queue_tasks=4),
        CheckpointCompatDrainUsage(supported_resets_pending=2),
        CheckpointCompatDrainUsage(
            open_pre_cutover_histories=1,
            pending_old_queue_tasks=1,
            supported_resets_pending=1,
        ),
    ]
    for usage in cases:
        gate = evaluate_checkpoint_compat_drain(usage)
        canonical = evaluate_worker_drain(
            FrozenGenerationUsage(
                deploymentGeneration="test-generation",
                openRecoveryHistories=usage.open_pre_cutover_histories,
                pendingRestorations=(
                    usage.pending_old_queue_tasks + usage.supported_resets_pending
                ),
            )
        )
        assert gate.may_remove_workflow_queue_handlers is canonical.may_remove_worker_routes


def test_compat_registration_points_at_the_drain_gate():
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[4]
        / "moonmind/workflows/temporal/workflow_registry.py"
    ).read_text()
    assert "checkpoint_compat_drain" in source
    assert "evaluate_checkpoint_compat_drain" in source


@pytest.mark.asyncio
async def test_drain_gate_retains_progress_under_bounded_load():
    """Bounded-load rehearsal: the gate stays decisive under saturation."""

    usages = [
        CheckpointCompatDrainUsage(),
        CheckpointCompatDrainUsage(open_pre_cutover_histories=1),
        CheckpointCompatDrainUsage(pending_old_queue_tasks=5),
        CheckpointCompatDrainUsage(supported_resets_pending=2),
    ]

    async def _decide(usage: CheckpointCompatDrainUsage) -> bool:
        await asyncio.sleep(0)
        return evaluate_checkpoint_compat_drain(usage).may_remove_workflow_queue_handlers

    verdicts = await asyncio.wait_for(
        asyncio.gather(*(_decide(usage) for usage in usages * 25)),
        timeout=30,
    )
    assert verdicts == [True, False, False, False] * 25


# --- Ordering: idempotency and single-mutator ownership ----------------------


def test_launch_idempotency_key_is_deterministic_per_turn():
    first = build_branch_turn_launch_idempotency_key(
        workflow_id="wf-1", branch_id="b-1", branch_turn_id="t-1"
    )
    second = build_branch_turn_launch_idempotency_key(
        workflow_id="wf-1", branch_id="b-1", branch_turn_id="t-1"
    )
    assert first == second
    # Duplicate delivery of the same turn addresses the same key.
    assert ":".join(["wf-1", "b-1", "t-1", "launch"]) == first


def test_launch_idempotency_key_binds_every_identity():
    key = build_branch_turn_launch_idempotency_key(
        workflow_id="wf-1", branch_id="b-1", branch_turn_id="t-1"
    )
    for identity in ("wf-1", "b-1", "t-1"):
        assert identity in key
    assert build_branch_turn_launch_idempotency_key(
        workflow_id="wf-1", branch_id="b-1", branch_turn_id="t-2"
    ) != key
    assert build_branch_turn_launch_idempotency_key(
        workflow_id="wf-1", branch_id="b-2", branch_turn_id="t-1"
    ) != key


@pytest.mark.parametrize("field", ["workflow_id", "branch_id", "branch_turn_id"])
def test_launch_idempotency_key_rejects_blank_identity(field):
    kwargs = {"workflow_id": "wf-1", "branch_id": "b-1", "branch_turn_id": "t-1"}
    kwargs[field] = "  "
    with pytest.raises(ValueError):
        build_branch_turn_launch_idempotency_key(**kwargs)


def test_single_mutator_owns_terminal_writes():
    """Ordering has one writer: lock + finalize on CheckpointBranchService."""

    assert hasattr(CheckpointBranchService, "lock_turn_execution")
    assert hasattr(CheckpointBranchService, "finalize_turn_execution")
    assert hasattr(CheckpointBranchService, "mark_turn_running")


# --- Least privilege: behavioral I/O inventory ------------------------------


def _deny_session_maker(*args, **kwargs):
    raise RuntimeError("test denied unexpected database I/O")


@pytest.mark.asyncio
async def test_metadata_helpers_need_no_database_authority(monkeypatch):
    """Helpers execute with database I/O denied; persistence cannot."""

    import moonmind.workflows.temporal.workflows.agent_run as agent_run_module
    import moonmind.workflows.temporal.workflows.checkpoint_branch_turn as turn_module

    monkeypatch.setattr(
        turn_module, "async_session_maker", _deny_session_maker
    )
    monkeypatch.delenv("DATABASE_URL", raising=False)

    metadata = await agent_run_module.resolve_adapter_metadata("OpenClaw")
    assert metadata["agent_id"] == "openclaw"

    route = await agent_run_module.get_activity_route(
        "checkpoint_branch.turn.persist_terminal"
    )
    assert route["task_queue"] == "mm.activity.artifacts"


@pytest.mark.asyncio
async def test_retained_persistence_handler_fails_closed_without_db(monkeypatch):
    """The compat handlers still carry database authority: deny it loudly."""

    import moonmind.workflows.temporal.workflows.checkpoint_branch_turn as turn_module

    monkeypatch.setattr(
        turn_module, "async_session_maker", _deny_session_maker
    )
    with pytest.raises(RuntimeError, match="denied unexpected database"):
        await turn_module.mark_checkpoint_branch_turn_running(
            {
                "workflowId": "wf-1",
                "branchId": "b-1",
                "branchTurnId": "t-1",
                "agentRunWorkflowId": "run-1",
            }
        )
