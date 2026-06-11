"""Workflow-boundary coverage for MM-816 reattempt compensation orchestration.

Exercises explicit, idempotent, observable cleanup/compensation orchestration
for already-occurred non-idempotent external effects during reattempts, as
behavior of ``MoonMindRunWorkflow`` -- not only as a manifest record shape.

Traceability: MM-816 (source issue MM-705).
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from moonmind.workflows.temporal.workflows import run as run_module
from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow


def _configure_workflow_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    workflow_info = SimpleNamespace(
        namespace="default",
        workflow_id="wf-816",
        run_id="run-816",
        task_queue="mm.workflow",
        search_attributes={"mm_owner_type": ["user"], "mm_owner_id": ["user-1"]},
    )
    logger = SimpleNamespace(
        info=lambda *a, **k: None,
        warning=lambda *a, **k: None,
        error=lambda *a, **k: None,
        debug=lambda *a, **k: None,
        isEnabledFor=lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(run_module.workflow, "info", lambda: workflow_info)
    monkeypatch.setattr(run_module.workflow, "logger", logger)
    monkeypatch.setattr(run_module.workflow, "patched", lambda _patch_id: False)


def test_first_attempt_has_no_compensation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    wf = MoonMindRunWorkflow()

    plan = wf._orchestrate_reattempt_compensation(
        "implement",
        execution_ordinal=1,
        updated_at=datetime(2026, 6, 10, tzinfo=UTC),
    )

    assert plan is None
    assert wf._step_reattempt_compensations == {}


def test_reattempt_orchestrates_observable_compensation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    wf = MoonMindRunWorkflow()

    # Attempt 1 performed an already-gate-approved non-idempotent Jira
    # transition against the MM-705 source issue.
    wf._record_step_side_effect(
        "implement",
        effect_class="external_non_idempotent",
        operation="jira.transition_issue",
        target="MM-705",
        idempotency_key="wf-816:run-816:implement:execution:1:jira-transition",
        workflow_state_accepted=True,
    )

    plan = wf._orchestrate_reattempt_compensation(
        "implement",
        execution_ordinal=2,
        updated_at=datetime(2026, 6, 10, tzinfo=UTC),
    )

    assert plan is not None
    assert plan["requiresCompensation"] is True
    assert plan["reattemptAllowed"] is True
    assert len(plan["compensations"]) == 1
    compensation = plan["compensations"][0]
    assert compensation["kind"] == "compensation"
    assert (
        compensation["idempotencyKey"]
        == "wf-816:run-816:implement:execution:2:compensate:jira.transition_issue"
    )

    # Observable as workflow state, not only inside a manifest payload.
    assert "implement" in wf._step_reattempt_compensations
    assert wf._step_reattempt_compensations["implement"]["reattemptExecutionOrdinal"] == 2
    # The compensation was recorded as a side effect of the new attempt.
    recorded_kinds = [
        record.get("kind")
        for record in wf._step_side_effect_records["implement"]
    ]
    assert "compensation" in recorded_kinds


def test_compensation_is_idempotent_across_successive_reattempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    wf = MoonMindRunWorkflow()

    wf._record_step_side_effect(
        "implement",
        effect_class="publication",
        operation="repo.merge_pr",
        target="PR-705",
        idempotency_key="wf-816:run-816:implement:execution:1:publish-pr",
        workflow_state_accepted=True,
    )

    first = wf._orchestrate_reattempt_compensation(
        "implement",
        execution_ordinal=2,
        updated_at=datetime(2026, 6, 10, tzinfo=UTC),
    )
    assert first is not None
    assert len(first["compensations"]) == 1

    # A later reattempt must not compensate the same external mutation again.
    second = wf._orchestrate_reattempt_compensation(
        "implement",
        execution_ordinal=3,
        updated_at=datetime(2026, 6, 10, tzinfo=UTC),
    )

    assert second is not None
    assert second["requiresCompensation"] is False
    assert second["compensations"] == []
    assert second["alreadyCompensated"][0]["operation"] == "repo.merge_pr"

    # Exactly one compensation side effect was recorded across both reattempts.
    compensation_records = [
        record
        for record in wf._step_side_effect_records["implement"]
        if record.get("kind") == "compensation"
    ]
    assert len(compensation_records) == 1
