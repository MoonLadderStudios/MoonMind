"""Workflow-decision saturation rehearsal (MoonLadderStudios/MoonMind#3949).

Gap 2 of the #3949 verifier evidence asks for workflow-execution-level
saturation proof, or explicit reviewer acceptance that the gate-level
100-handoff ordering rehearsal plus retry/timeout budgets satisfy the
saturation clause.

Full Temporal execution-under-load (concurrent ``CheckpointBranchTurn``
workflows against a live artifacts worker) remains integration scope: it
needs a Temporal test server, database, and deployment worker, and cannot
execute in a stdlib-only sandbox. This module extends the rehearsal one
level up from the raw gate without adding those dependencies: it models
100 bounded concurrent workflow-decision handoffs, where each handoff
resolves the drain-gate decision for one turn (the same decision the
workflow's persistence routing and the consolidated worker's cleanup
release depend on), records its control verdict before its cleanup
release, and asserts per-handoff ordering plus gate decisiveness.

Stdlib-only by design: imports nothing beyond ``asyncio``, ``pytest``,
and ``checkpoint_compat_drain`` so the rehearsal executes anywhere the
gate module imports, including dependency-constrained sandboxes.

Companion: ``test_checkpoint_compat_drain_3949.py`` holds the gate-level
rehearsal (``test_consolidated_worker_retains_control_and_cleanup_progress_under_load``)
and the behavioral I/O inventory; the topology doc
(``docs/Temporal/ActivityCatalogAndWorkerTopology.md``) records that both
are rehearsals, not production saturation proof, pending reviewer
acceptance or integration-level execution-under-load evidence.
"""

from __future__ import annotations

import asyncio

import pytest

from moonmind.workflows.temporal.checkpoint_compat_drain import (
    COMPAT_DRAIN_CONTRACT,
    CheckpointCompatDrainUsage,
    evaluate_checkpoint_compat_drain,
    retention_reason,
)

pytestmark = pytest.mark.unit_fast


@pytest.mark.asyncio
async def test_decision_handoffs_retain_control_before_cleanup_under_load():
    """100 concurrent turn decisions keep control-before-cleanup ordering."""

    ledger: dict[str, list[str]] = {}
    ledger_lock = asyncio.Lock()

    usages = [
        CheckpointCompatDrainUsage(),
        CheckpointCompatDrainUsage(open_pre_cutover_histories=1),
        CheckpointCompatDrainUsage(pending_old_queue_tasks=5),
        CheckpointCompatDrainUsage(supported_resets_pending=2),
    ]

    async def _decide_turn(index: int, usage: CheckpointCompatDrainUsage) -> bool:
        # Workflow-decision step: resolve whether this turn's persistence
        # may drop the compat registration, then record control first.
        await asyncio.sleep(0)
        decision = evaluate_checkpoint_compat_drain(usage)
        assert decision.contract == COMPAT_DRAIN_CONTRACT
        async with ledger_lock:
            ledger.setdefault(f"turn-{index}", []).append(
                f"control:{decision.required_action}"
            )
        # Cleanup-release step: must never precede or displace control.
        await asyncio.sleep(0)
        async with ledger_lock:
            ledger.setdefault(f"turn-{index}", []).append("cleanup:released")
        return decision.may_remove_workflow_queue_handlers

    verdicts = await asyncio.wait_for(
        asyncio.gather(
            *(_decide_turn(index, usages[index % len(usages)]) for index in range(100))
        ),
        timeout=30,
    )
    assert verdicts == [True, False, False, False] * 25
    assert len(ledger) == 100
    for index in range(100):
        records = ledger[f"turn-{index}"]
        assert len(records) == 2, f"turn-{index} lost progress under load"
        assert records[0].startswith("control:")
        assert records[1] == "cleanup:released"


def test_no_decision_authorizes_removal_with_outstanding_consumers():
    """Every outstanding dimension blocks removal and names its blocker."""

    cases = [
        CheckpointCompatDrainUsage(open_pre_cutover_histories=1),
        CheckpointCompatDrainUsage(pending_old_queue_tasks=1),
        CheckpointCompatDrainUsage(supported_resets_pending=1),
        CheckpointCompatDrainUsage(
            open_pre_cutover_histories=2,
            pending_old_queue_tasks=3,
            supported_resets_pending=1,
        ),
    ]
    for usage in cases:
        decision = evaluate_checkpoint_compat_drain(usage)
        assert decision.may_remove_workflow_queue_handlers is False
        assert decision.required_action == "retain_compat"
        assert decision.outstanding > 0
        assert decision.blocking_dimensions
        assert COMPAT_DRAIN_CONTRACT in retention_reason(decision)


def test_drained_decision_unblocks_only_when_all_dimensions_zero():
    decision = evaluate_checkpoint_compat_drain(CheckpointCompatDrainUsage())
    assert decision.outstanding == 0
    assert decision.may_remove_workflow_queue_handlers is True
    assert decision.required_action == "safe_to_remove"
    assert decision.blocking_dimensions == ()
