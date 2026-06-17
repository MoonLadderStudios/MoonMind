"""Workflow-boundary coverage for MM-826 external handoff accepted-disposition gate.

Exercises the producing-step accepted assertion as behavior of
``MoonMindRunWorkflow``: external handoffs (PR creation, Jira transition/comment,
merge, deploy/publish, provider-account) are permitted only when the producing
Step Execution reached an ``accepted`` terminal disposition AND the controlling
MoonSpec gate verdict passes; denials are recorded as ``blocked`` side effects.

These tests instantiate the workflow directly (no Temporal test server), matching
the existing ``test_run_step_ledger.py`` / ``test_run_reattempt_compensation.py``
pattern. The accepted-disposition strengthening of the structured-review handoff
gate is introduced behind a replay-stable patch, so an in-flight compatibility
case (patch disabled) is included.

Traceability: MM-826 / DESIGN-REQ-005, DESIGN-REQ-022.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from moonmind.workflows.temporal.step_executions import git_effect_metadata
from moonmind.workflows.temporal.workflows import run as run_module
from moonmind.workflows.temporal.workflows.run import (
    RUN_HANDOFF_ACCEPTED_DISPOSITION_GATE_PATCH,
    MoonMindRunWorkflow,
)

_EIGHT_SIDE_EFFECT_CATEGORIES = {
    "git",
    "external",
    "artifact",
    "publication",
    "compensation",
    "memory",
    "retrieval",
    "record",
}


def _configure_workflow_runtime(
    monkeypatch: pytest.MonkeyPatch,
    *,
    patched: bool = False,
) -> None:
    workflow_info = SimpleNamespace(
        namespace="default",
        workflow_id="wf-826",
        run_id="run-826",
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
    monkeypatch.setattr(
        run_module.workflow,
        "patched",
        lambda patch_id: bool(patched)
        if patch_id == RUN_HANDOFF_ACCEPTED_DISPOSITION_GATE_PATCH
        else False,
    )


_HANDOFF_NODE = {
    "id": "code-review",
    "inputs": {"annotations": {"jiraOrchestrateRole": "pull-request-handoff"}},
}


# ---------------------------------------------------------------------------
# _external_handoff_gate: the reusable producing-step accepted assertion
# ---------------------------------------------------------------------------


def test_external_handoff_gate_allows_accepted_and_gate_approved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    wf = MoonMindRunWorkflow()
    wf._step_terminal_dispositions["code-review"] = "accepted"
    wf._moonspec_gate_verdict = "FULLY_IMPLEMENTED"

    decision = wf._external_handoff_gate(
        "code-review",
        operation="repo.create_pr",
        effect_class="publication",
        target="PR-1",
    )

    assert decision["allowed"] is True
    assert decision["gateApproved"] is True
    assert decision["record"]["disposition"] == "accepted"
    assert wf._step_side_effect_records["code-review"][-1]["disposition"] == "accepted"


@pytest.mark.parametrize(
    "disposition",
    [
        "candidate",
        "retryable",
        "discarded",
        "superseded",
        "blocked",
        "needs_human",
        "failed_unrecoverable",
        "failed_with_remaining_work",
        None,
    ],
)
def test_external_handoff_gate_blocks_non_accepted_disposition(
    monkeypatch: pytest.MonkeyPatch,
    disposition: str | None,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    wf = MoonMindRunWorkflow()
    if disposition is not None:
        wf._step_terminal_dispositions["code-review"] = disposition
    # Passing gate verdict on purpose: the disposition alone must block.
    wf._moonspec_gate_verdict = "FULLY_IMPLEMENTED"

    decision = wf._external_handoff_gate(
        "code-review",
        operation="repo.merge_pr",
        effect_class="publication",
        target="PR-1",
    )

    assert decision["allowed"] is False
    assert "producing_step_not_accepted" in decision["reason"]
    recorded = wf._step_side_effect_records["code-review"]
    assert len(recorded) == 1
    assert recorded[0]["disposition"] == "blocked"


def test_external_handoff_gate_blocks_when_gate_not_approved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    wf = MoonMindRunWorkflow()
    wf._step_terminal_dispositions["code-review"] = "accepted"
    # No verdict recorded -> not gate-approved.

    decision = wf._external_handoff_gate(
        "code-review",
        operation="jira.transition_issue",
        effect_class="external_idempotent",
        target="MM-826",
        idempotency_key="wf-826:run-826:code-review:execution:1:jira-transition",
    )

    assert decision["allowed"] is False
    assert decision["reason"] == "gate_not_approved"
    assert wf._step_side_effect_records["code-review"][0]["disposition"] == "blocked"


def test_external_handoff_gate_blocks_non_idempotent_without_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    wf = MoonMindRunWorkflow()
    wf._step_terminal_dispositions["deploy"] = "accepted"
    wf._moonspec_gate_verdict = "FULLY_IMPLEMENTED"

    decision = wf._external_handoff_gate(
        "deploy",
        operation="deployment.apply",
        effect_class="external_non_idempotent",
        target="prod",
        idempotency_key="wf-826:run-826:deploy:execution:1:deploy",
    )

    assert decision["allowed"] is False
    assert decision["reason"] == "non_idempotent_without_policy"

    allowed = wf._external_handoff_gate(
        "deploy",
        operation="deployment.apply",
        effect_class="external_non_idempotent",
        target="prod",
        idempotency_key="wf-826:run-826:deploy:execution:2:deploy",
        policy_permits_non_idempotent=True,
    )
    assert allowed["allowed"] is True


# ---------------------------------------------------------------------------
# Structured-review handoff gate: accepted-disposition strengthening + in-flight
# ---------------------------------------------------------------------------


def test_structured_review_handoff_requires_accepted_disposition_under_patch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch, patched=True)
    wf = MoonMindRunWorkflow()
    # Verify gate passed, but the producing gate step is NOT accepted.
    wf._record_moonspec_verify_gate(
        node_id="verify-final",
        outputs={"verdict": "FULLY_IMPLEMENTED"},
    )
    wf._step_terminal_dispositions["verify-final"] = "failed_with_remaining_work"

    reason = wf._jira_orchestrate_external_handoff_block_reason(_HANDOFF_NODE)

    assert reason is not None
    assert "terminal disposition to be accepted" in reason
    # A blocked side effect was recorded on the controlling gate step.
    recorded = wf._step_side_effect_records["verify-final"]
    assert recorded[-1]["disposition"] == "blocked"


def test_structured_review_handoff_allows_accepted_disposition_under_patch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch, patched=True)
    wf = MoonMindRunWorkflow()
    wf._record_moonspec_verify_gate(
        node_id="verify-final",
        outputs={"verdict": "FULLY_IMPLEMENTED"},
    )
    wf._step_terminal_dispositions["verify-final"] = "accepted"

    assert wf._jira_orchestrate_external_handoff_block_reason(_HANDOFF_NODE) is None
    # Allowing the handoff must not record a spurious blocked side effect.
    assert "verify-final" not in wf._step_side_effect_records


def test_structured_review_handoff_in_flight_keeps_verdict_only_behavior(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # In-flight compatibility: with the patch disabled, a passing verdict opens
    # the handoff regardless of the producing-step disposition (prior behavior).
    _configure_workflow_runtime(monkeypatch, patched=False)
    wf = MoonMindRunWorkflow()
    wf._record_moonspec_verify_gate(
        node_id="verify-final",
        outputs={"verdict": "FULLY_IMPLEMENTED"},
    )
    wf._step_terminal_dispositions["verify-final"] = "failed_with_remaining_work"

    assert wf._jira_orchestrate_external_handoff_block_reason(_HANDOFF_NODE) is None
    assert "verify-final" not in wf._step_side_effect_records


def test_structured_review_handoff_still_blocks_failed_verdict_under_patch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Existing MoonSpec gate-verdict block must remain in force (no regression).
    _configure_workflow_runtime(monkeypatch, patched=True)
    wf = MoonMindRunWorkflow()
    wf._record_moonspec_verify_gate(
        node_id="verify-final",
        outputs={"verdict": "ADDITIONAL_WORK_NEEDED"},
    )
    wf._step_terminal_dispositions["verify-final"] = "accepted"

    reason = wf._jira_orchestrate_external_handoff_block_reason(_HANDOFF_NODE)
    assert reason is not None
    assert "ADDITIONAL_WORK_NEEDED" in reason


# ---------------------------------------------------------------------------
# Terminal manifest aggregation: 8 categories + blocked denials (AC5/AC6)
# ---------------------------------------------------------------------------


def test_terminal_accepted_manifest_aggregates_categories_and_blocked_denials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # AC5 / FR-001 / DESIGN-REQ-005: a terminal `accepted` manifest carries an
    # accepted git disposition with accepted output evidence and aggregates all
    # eight side-effect categories, including `blocked` handoff denials.
    _configure_workflow_runtime(monkeypatch)
    wf = MoonMindRunWorkflow()
    wf._step_terminal_dispositions["code-review"] = "accepted"
    wf._moonspec_gate_verdict = "FULLY_IMPLEMENTED"

    # An allowed publication handoff -> accepted side-effect record.
    allowed = wf._external_handoff_gate(
        "code-review",
        operation="repo.create_pr",
        effect_class="publication",
        target="PR-1",
    )
    assert allowed["allowed"] is True

    # A denied non-idempotent handoff (no explicit policy) -> blocked record,
    # recorded at the boundary so it reaches the terminal manifest.
    denied = wf._external_handoff_gate(
        "code-review",
        operation="deployment.apply",
        effect_class="external_non_idempotent",
        target="prod",
        idempotency_key="wf-826:run-826:code-review:execution:1:deploy",
    )
    assert denied["blocked"] is True

    git_effect = git_effect_metadata(disposition="accepted", head_commit="abc123")
    assert git_effect["disposition"] == "accepted"
    assert git_effect["acceptedOutputPresent"] is True

    payload = wf._step_execution_side_effects("code-review", git_effect=git_effect)
    summary = payload["summary"]

    # All eight categories are present and the accepted git effect is aggregated.
    assert set(summary["categories"]) == _EIGHT_SIDE_EFFECT_CATEGORIES
    assert summary["categories"]["git"] == 1
    assert summary["categories"]["publication"] == 1
    assert summary["categories"]["external"] == 1
    assert summary["categories"]["record"] == 2
    # The blocked denial is aggregated into the manifest alongside the accepted
    # git disposition and the accepted publication handoff.
    assert summary["byDisposition"]["blocked"] == 1
    assert summary["byDisposition"]["accepted"] == 2
    dispositions = [record["disposition"] for record in payload["records"]]
    assert dispositions.count("blocked") == 1
    assert dispositions.count("accepted") == 1


@pytest.mark.parametrize("disposition", ["candidate", "discarded", "superseded"])
def test_failed_or_partial_attempt_carries_records_but_opens_no_gate(
    monkeypatch: pytest.MonkeyPatch,
    disposition: str,
) -> None:
    # AC6 / FR-007: a failed/partial attempt retains its side-effect records but
    # does not open any external handoff gate.
    _configure_workflow_runtime(monkeypatch)
    wf = MoonMindRunWorkflow()
    wf._step_terminal_dispositions["code-review"] = disposition
    wf._moonspec_gate_verdict = "FULLY_IMPLEMENTED"

    decision = wf._external_handoff_gate(
        "code-review",
        operation="repo.create_pr",
        effect_class="publication",
        target="PR-1",
    )
    assert decision["allowed"] is False

    # The git disposition for a non-accepted terminal attempt is never accepted.
    git_effect = git_effect_metadata(disposition="candidate")
    assert git_effect["disposition"] != "accepted"

    payload = wf._step_execution_side_effects("code-review", git_effect=git_effect)
    # The attempt carries its (blocked) record but no gated handoff was opened.
    assert payload["records"], "failed/partial attempt should still carry records"
    assert all(record["disposition"] != "accepted" for record in payload["records"])
    assert "accepted" not in payload["summary"]["byDisposition"]


# ---------------------------------------------------------------------------
# Publication gate: native-PR-creation / managed-publish surface (FR-003 /
# DESIGN-REQ-022 / SC-001). Drives the real publish call path
# (_apply_blocking_moonspec_gate_to_publish -> _blocking_moonspec_gate_reason),
# not _external_handoff_gate directly.
# ---------------------------------------------------------------------------


def test_publication_gate_blocks_when_producing_step_not_accepted_under_patch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # AC2 for the managed-publish/native-PR surface: a passing MoonSpec verdict
    # is no longer sufficient; the controlling gate step must be `accepted`.
    _configure_workflow_runtime(monkeypatch, patched=True)
    wf = MoonMindRunWorkflow()
    wf._record_moonspec_verify_gate(
        node_id="verify-final",
        outputs={"verdict": "FULLY_IMPLEMENTED"},
    )
    wf._step_terminal_dispositions["verify-final"] = "failed_with_remaining_work"

    blocked = wf._apply_blocking_moonspec_gate_to_publish()

    assert blocked is True
    assert wf._publish_status == "not_required"
    assert "terminal disposition is not accepted" in wf._publish_reason
    assert wf._publish_context["publicationBlockedBy"] == "moonspec_verify"
    # Exactly one blocked publication side effect recorded at the boundary.
    recorded = wf._step_side_effect_records["verify-final"]
    assert len(recorded) == 1
    assert recorded[0]["disposition"] == "blocked"
    assert recorded[0]["class"] == "publication"
    assert recorded[0]["operation"] == "repo.create_pr"


def test_publication_gate_allows_accepted_and_approved_under_patch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # AC1 for the managed-publish/native-PR surface.
    _configure_workflow_runtime(monkeypatch, patched=True)
    wf = MoonMindRunWorkflow()
    wf._record_moonspec_verify_gate(
        node_id="verify-final",
        outputs={"verdict": "FULLY_IMPLEMENTED"},
    )
    wf._step_terminal_dispositions["verify-final"] = "accepted"

    assert wf._apply_blocking_moonspec_gate_to_publish() is False
    assert wf._blocking_moonspec_gate_reason() is None
    # Allowing publication must not record a spurious blocked side effect.
    assert "verify-final" not in wf._step_side_effect_records


def test_publication_gate_records_single_blocked_record_across_repeated_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # SC-002: the publish reason is recomputed at several call sites in one
    # publish pass; the terminal manifest must carry exactly one blocked record.
    _configure_workflow_runtime(monkeypatch, patched=True)
    wf = MoonMindRunWorkflow()
    wf._record_moonspec_verify_gate(
        node_id="verify-final",
        outputs={"verdict": "FULLY_IMPLEMENTED"},
    )
    wf._step_terminal_dispositions["verify-final"] = "candidate"

    for _ in range(3):
        assert wf._apply_blocking_moonspec_gate_to_publish() is True

    recorded = wf._step_side_effect_records["verify-final"]
    assert len(recorded) == 1
    assert recorded[0]["disposition"] == "blocked"


def test_publication_gate_in_flight_keeps_verdict_only_behavior(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # In-flight compatibility: with the patch disabled, a passing verdict opens
    # publication regardless of the producing-step disposition (prior behavior).
    _configure_workflow_runtime(monkeypatch, patched=False)
    wf = MoonMindRunWorkflow()
    wf._record_moonspec_verify_gate(
        node_id="verify-final",
        outputs={"verdict": "FULLY_IMPLEMENTED"},
    )
    wf._step_terminal_dispositions["verify-final"] = "failed_with_remaining_work"

    assert wf._apply_blocking_moonspec_gate_to_publish() is False
    assert wf._blocking_moonspec_gate_reason() is None
    assert "verify-final" not in wf._step_side_effect_records


def test_publication_gate_still_blocks_failed_verdict_under_patch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Existing MoonSpec gate-verdict block must remain in force (no regression).
    _configure_workflow_runtime(monkeypatch, patched=True)
    wf = MoonMindRunWorkflow()
    wf._record_moonspec_verify_gate(
        node_id="verify-final",
        outputs={"verdict": "ADDITIONAL_WORK_NEEDED"},
    )
    wf._step_terminal_dispositions["verify-final"] = "accepted"

    assert wf._apply_blocking_moonspec_gate_to_publish() is True
    assert "ADDITIONAL_WORK_NEEDED" in wf._publish_reason


# ---------------------------------------------------------------------------
# Merge-automation surface: repo.merge_pr (FR-003 / DESIGN-REQ-022 / SC-001).
# Drives the real merge call path (_merge_handoff_block_reason), the guard
# invoked immediately before the Jules branch-publish auto-merge activity.
# ---------------------------------------------------------------------------


def test_merge_handoff_blocks_when_producing_step_not_accepted_under_patch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # AC2 for the merge surface: passing verdict but non-accepted producing step.
    _configure_workflow_runtime(monkeypatch, patched=True)
    wf = MoonMindRunWorkflow()
    wf._record_moonspec_verify_gate(
        node_id="verify-final",
        outputs={"verdict": "FULLY_IMPLEMENTED"},
    )
    wf._step_terminal_dispositions["verify-final"] = "failed_with_remaining_work"

    reason = wf._merge_handoff_block_reason()

    assert reason is not None
    assert "Merge automation" in reason
    recorded = wf._step_side_effect_records["verify-final"]
    assert len(recorded) == 1
    assert recorded[0]["disposition"] == "blocked"
    assert recorded[0]["class"] == "publication"
    assert recorded[0]["operation"] == "repo.merge_pr"


def test_merge_handoff_allows_accepted_and_approved_under_patch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # AC1 for the merge surface.
    _configure_workflow_runtime(monkeypatch, patched=True)
    wf = MoonMindRunWorkflow()
    wf._record_moonspec_verify_gate(
        node_id="verify-final",
        outputs={"verdict": "FULLY_IMPLEMENTED"},
    )
    wf._step_terminal_dispositions["verify-final"] = "accepted"

    assert wf._merge_handoff_block_reason() is None
    assert "verify-final" not in wf._step_side_effect_records


def test_merge_handoff_blocks_when_gate_not_approved_under_patch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # AC3 for the merge surface: accepted disposition but the gate verdict does
    # not approve advancement.
    _configure_workflow_runtime(monkeypatch, patched=True)
    wf = MoonMindRunWorkflow()
    wf._record_moonspec_verify_gate(
        node_id="verify-final",
        outputs={"verdict": "ADDITIONAL_WORK_NEEDED"},
    )
    wf._step_terminal_dispositions["verify-final"] = "accepted"

    reason = wf._merge_handoff_block_reason()

    assert reason is not None
    assert "has not approved advancement" in reason
    recorded = wf._step_side_effect_records["verify-final"]
    assert len(recorded) == 1
    assert recorded[0]["disposition"] == "blocked"
    assert recorded[0]["operation"] == "repo.merge_pr"


def test_merge_handoff_in_flight_allows_without_patch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # In-flight compatibility: with the patch disabled, the merge proceeds with
    # prior behavior regardless of the producing-step disposition.
    _configure_workflow_runtime(monkeypatch, patched=False)
    wf = MoonMindRunWorkflow()
    wf._record_moonspec_verify_gate(
        node_id="verify-final",
        outputs={"verdict": "FULLY_IMPLEMENTED"},
    )
    wf._step_terminal_dispositions["verify-final"] = "failed_with_remaining_work"

    assert wf._merge_handoff_block_reason() is None
    assert "verify-final" not in wf._step_side_effect_records


def test_merge_handoff_allows_when_no_controlling_gate_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A Jules branch-publish flow with no controlling MoonSpec gate step keeps
    # its prior behavior (the disposition assertion has nothing to assert
    # against); the patch does not block such flows.
    _configure_workflow_runtime(monkeypatch, patched=True)
    wf = MoonMindRunWorkflow()

    assert wf._merge_handoff_block_reason() is None
    assert not wf._step_side_effect_records
