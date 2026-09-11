"""Minimized replay for MoonLadderStudios/MoonMind#4227.

A validated ``reenter_gate`` handoff legitimately publishes nothing. This
replay exercises the recorded execution-to-finalization boundary from the
packaged fixture (``replays/run-deferred-publication-4227``): execution
context -> auto-publish evidence check -> deferred ledger write ->
publish completion, under both the new patch marker (defers) and the
pre-patch replay shape (keeps the recorded publication failure).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

pytest.importorskip("temporalio")

from moonmind.schemas.temporal_models import StepLedgerSnapshotModel
from moonmind.workflows.temporal.workflows import run as run_workflow_module
from moonmind.workflows.temporal.workflows.run import (
    RUN_DEFER_PUBLICATION_ON_TERMINAL_CONTINUATION_PATCH,
    MoonMindRunWorkflow,
)
from tests.integration.reliability.helpers import load_replay

pytestmark = [
    pytest.mark.integration,
    pytest.mark.reliability_journey,
]

REPLAY_ID = "run-deferred-publication-4227"


def _workflow(patched: bool, monkeypatch: pytest.MonkeyPatch) -> MoonMindRunWorkflow:
    workflow = MoonMindRunWorkflow()
    workflow._owner_id = "owner-1"
    workflow._repo = "org/repo"
    monkeypatch.setattr(run_workflow_module.workflow, "upsert_memo", lambda _memo: None)
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "patched",
        lambda patch_id: patched
        and patch_id == RUN_DEFER_PUBLICATION_ON_TERMINAL_CONTINUATION_PATCH,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow, "now", lambda: datetime.now(timezone.utc)
    )
    workflow_info = type(
        "WorkflowInfo",
        (),
        {
            "namespace": "default",
            "workflow_id": "wf-4227-replay",
            "run_id": "run-4227-replay",
            "search_attributes": {},
        },
    )
    monkeypatch.setattr(run_workflow_module.workflow, "info", workflow_info)
    logger = type(
        "Logger",
        (),
        {"info": lambda *a, **k: None, "warning": lambda *a, **k: None},
    )
    monkeypatch.setattr(run_workflow_module.workflow, "logger", logger)
    return workflow


def test_deferred_publication_replay_defers_on_new_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = load_replay(REPLAY_ID, "manifest.json")
    expected = load_replay(REPLAY_ID, "expected-outcome.json")
    outputs = manifest["recordedHistory"]["executionResult"]["outputs"]

    workflow = _workflow(True, monkeypatch)
    workflow._step_ledger_rows = [
        {
            "logicalStepId": "resolve-pr",
            "finalizationOutcome": {
                "status": "not_started",
                "phase": "after_execution_checkpoint",
                "criticality": "required",
                "retryCount": 0,
            },
        }
    ]
    workflow._rebuild_step_ledger_index()

    # Full execution-to-finalization boundary from the recorded history.
    workflow._record_execution_context(
        node_id="resolve-pr", execution_result={"outputs": outputs}
    )
    workflow._record_auto_publish_result({"outputs": outputs})
    assert workflow._publish_status == expected["publishStatus"]
    for fragment in expected["publishReasonContains"]:
        assert fragment in (workflow._publish_reason or "")

    workflow._apply_deferred_publication(
        reason=workflow._publish_reason or "deferred",
        logical_step_id="resolve-pr",
    )
    row = workflow._step_ledger_row_for("resolve-pr")
    assert row is not None
    outcome = row["finalizationOutcome"]
    want = expected["ledgerFinalizationOutcome"]
    assert outcome["status"] == want["status"]
    assert outcome["phase"] == want["phase"]
    assert outcome["criticality"] == want["criticality"]
    assert outcome["retryCount"] == want["retryCount"]
    assert outcome.get("failureCode") == want["failureCode"]

    # Ledger must validate for dashboard/API projection.
    StepLedgerSnapshotModel.model_validate(workflow.get_step_ledger())

    status, _message, failed = workflow._determine_publish_completion(
        parameters={"publishMode": "auto"}
    )
    assert failed is expected["completionFailed"]

    # A rejected/unowned continuation must fail closed, never defer.
    rejected = manifest["recordedHistory"]["rejectedContinuation"]["outputs"]
    assert workflow._terminal_publication_deferred_reason(rejected) is None
    assert expected["rejectedContinuationDefers"] is False


def test_deferred_publication_replay_keeps_recorded_failure_pre_patch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replayed histories without the patch keep the recorded failure."""

    manifest = load_replay(REPLAY_ID, "manifest.json")
    expected = load_replay(REPLAY_ID, "expected-outcome.json")
    outputs = manifest["recordedHistory"]["executionResult"]["outputs"]

    workflow = _workflow(False, monkeypatch)
    workflow._record_execution_context(
        node_id="resolve-pr", execution_result={"outputs": outputs}
    )
    workflow._record_auto_publish_result({"outputs": outputs})

    # Without the patch there is no deferral: the recorded evidence-missing
    # failure is preserved for replay compatibility.
    assert workflow._publish_status == "failed"
    assert workflow._publish_reason == "auto_publish_evidence_missing"
    assert expected["prePatchReplayKeepsRecordedFailure"] is True
    assert expected["mergedWithoutEvidenceFails"] is True
