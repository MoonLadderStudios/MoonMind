"""MoonLadderStudios/MoonMind#3944: one small proven patch batch.

Scope for this batch:

* Records the precise missing live evidence for history-visible patch markers
  WITHOUT claiming their removal: ``run-emit-ephemeral-step-checkpoints-v1``
  and ``run-bounded-story-loop-remediation-budget-v1`` are defined as
  constants but have no ``workflow.patched(...)`` / ``workflow.deprecate_patch``
  wiring in production code. No live Temporal history/visibility query was
  issued in this step, so consumers (active/queued work), retained histories,
  Continue-As-New chains, stored inputs, and supported resets for those marker
  IDs remain UNKNOWN. No marker branch is removed, no patch ID is reused, no
  retention is shortened, and no patch registry / dashboard / scanner / CI
  gate is introduced.

Work/history stays intact: no deployment, cancellation, history deletion, or
rolling-upgrade machinery. Repository verification vs actual deployment
authorization are reported separately by later steps.
"""

from __future__ import annotations

import re
from pathlib import Path

from moonmind.workflows.temporal.workflows import run as run_module


def _production_source() -> str:
    return Path(run_module.__file__).read_text()


def test_orphaned_patch_ids_have_no_production_wiring() -> None:
    """Pin the missing-evidence precondition for two orphaned patch IDs.

    Both constants stay defined for traceability, but neither may gain or lose
    production ``patched``/``deprecate_patch`` wiring in this batch: live
    consumer/retention/reset evidence for these marker IDs is UNKNOWN (no
    live queries issued here), so no marker retirement is claimed.
    """
    assert (
        run_module.RUN_EMIT_EPHEMERAL_STEP_CHECKPOINTS_PATCH
        == "run-emit-ephemeral-step-checkpoints-v1"
    )
    assert (
        run_module.RUN_BOUNDED_STORY_LOOP_REMEDIATION_BUDGET_PATCH
        == "run-bounded-story-loop-remediation-budget-v1"
    )
    source = _production_source()
    for constant in (
        "RUN_EMIT_EPHEMERAL_STEP_CHECKPOINTS_PATCH",
        "RUN_BOUNDED_STORY_LOOP_REMEDIATION_BUDGET_PATCH",
    ):
        for call in ("workflow.patched", "workflow.deprecate_patch"):
            pattern = re.compile(
                rf"{re.escape(call)}\(\s*{re.escape(constant)}\s*\)"
            )
            assert pattern.search(source) is None, (
                f"{constant} must have no production {call} wiring in this "
                "batch: marker retirement requires live consumer/retention/"
                "reset evidence that this step did not collect"
            )
