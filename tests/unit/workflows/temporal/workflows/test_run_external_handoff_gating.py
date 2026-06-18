"""Workflow-boundary gating for external handoffs (MM-826).

Exercises ``MoonMindRunWorkflow._external_handoff_gate`` which authorizes an
external handoff only when the producing logical step reached an ``accepted``
terminal disposition AND the controlling MoonSpec verification gate is a
passing verdict. On denial the workflow records exactly one blocked side
effect at the call site and refuses to supply handoff evidence; the existing
MoonSpec gate-verdict block is preserved (both gates apply).

Covers FR-003, FR-005, FR-006, FR-007 and acceptance scenarios 1, 3, 5.
"""

from __future__ import annotations

import pytest

from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow


def _records(workflow, logical_step_id):
    return list(workflow._step_side_effect_records.get(logical_step_id, ()))


def test_handoff_allowed_when_accepted_and_passing_verdict() -> None:
    workflow = MoonMindRunWorkflow()
    workflow._step_terminal_dispositions["implement"] = "accepted"
    workflow._moonspec_gate_verdict = "FULLY_IMPLEMENTED"

    decision = workflow._external_handoff_gate(
        "implement", operation="repo.create_pr", effect_class="publication"
    )

    assert decision["allowed"] is True
    assert decision["handoffGate"]["terminalDisposition"] == "accepted"
    assert decision["handoffGate"]["gateApproved"] is True
    # No blocked side effect recorded for an allowed handoff.
    assert _records(workflow, "implement") == []


def test_handoff_denied_records_one_blocked_side_effect() -> None:
    workflow = MoonMindRunWorkflow()
    workflow._step_terminal_dispositions["implement"] = "candidate"
    workflow._moonspec_gate_verdict = "FULLY_IMPLEMENTED"

    decision = workflow._external_handoff_gate(
        "implement", operation="repo.merge", effect_class="publication"
    )

    assert decision["allowed"] is False
    assert decision["reason"] == "terminal_disposition_not_accepted"
    records = _records(workflow, "implement")
    assert len(records) == 1
    assert records[0]["class"] == "publication"
    assert records[0]["disposition"] == "blocked"
    assert records[0]["operation"] == "repo.merge"


def test_handoff_denied_when_verdict_not_passing_even_if_accepted() -> None:
    # FR-006: the MoonSpec gate-verdict block is preserved alongside the
    # terminal-disposition check. Accepted disposition is not enough.
    workflow = MoonMindRunWorkflow()
    workflow._step_terminal_dispositions["implement"] = "accepted"
    workflow._moonspec_gate_verdict = "ADDITIONAL_WORK_NEEDED"

    decision = workflow._external_handoff_gate(
        "implement", operation="repo.create_pr", effect_class="publication"
    )

    assert decision["allowed"] is False
    assert decision["reason"] == "gate_not_approved"
    assert len(_records(workflow, "implement")) == 1


@pytest.mark.parametrize(
    "disposition",
    ["candidate", "discarded", "superseded", "blocked", "failed_with_remaining_work"],
)
def test_handoff_gate_never_opens_for_failed_or_partial(disposition: str) -> None:
    workflow = MoonMindRunWorkflow()
    workflow._step_terminal_dispositions["implement"] = disposition
    workflow._moonspec_gate_verdict = "FULLY_IMPLEMENTED"

    decision = workflow._external_handoff_gate(
        "implement", operation="repo.merge", effect_class="publication"
    )

    assert decision["allowed"] is False
    assert _records(workflow, "implement")[0]["disposition"] == "blocked"


def test_handoff_fails_closed_when_no_disposition_recorded() -> None:
    workflow = MoonMindRunWorkflow()
    # No terminal disposition recorded for the step, no gate verdict.
    decision = workflow._external_handoff_gate(
        "implement", operation="provider_account.acquire", effect_class="provider_account"
    )

    assert decision["allowed"] is False
    assert decision["reason"] == "unknown_terminal_disposition"
    assert _records(workflow, "implement")[0]["disposition"] == "blocked"
