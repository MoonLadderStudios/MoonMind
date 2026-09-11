"""Regression coverage for MoonLadderStudios/MoonMind#4227.

A validated ``reenter_gate``/``request_review`` handoff (or a validated
``manual_review``/``failed`` verdict) legitimately publishes nothing. The run
must record publication as not_required with no finalization retry, while a
``merged``/``review_clean`` claim without publish evidence must still fail
with FINALIZATION_PUBLICATION_FAILED / auto_publish_evidence_missing.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

pytest.importorskip("temporalio")

from moonmind.workflows.temporal.workflows import run as run_workflow_module
from moonmind.workflows.temporal.workflows.run import (
    FINALIZATION_PUBLICATION_FAILED,
    RUN_DEFER_PUBLICATION_ON_TERMINAL_CONTINUATION_PATCH,
    MoonMindRunWorkflow,
)


@pytest.fixture
def deferred_workflow(monkeypatch: pytest.MonkeyPatch) -> MoonMindRunWorkflow:
    workflow = MoonMindRunWorkflow()
    workflow._owner_id = "owner-1"
    workflow._repo = "org/repo"
    monkeypatch.setattr(run_workflow_module.workflow, "upsert_memo", lambda _memo: None)
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "patched",
        lambda patch_id: patch_id
        == RUN_DEFER_PUBLICATION_ON_TERMINAL_CONTINUATION_PATCH,
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "now",
        lambda: datetime.now(timezone.utc),
    )
    workflow_info = type(
        "WorkflowInfo",
        (),
        {
            "namespace": "default",
            "workflow_id": "wf-4227",
            "run_id": "run-4227",
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


def _reenter_gate_outputs() -> dict[str, Any]:
    """Replay-shaped outputs for resolver:pr:4207:head:0b64408a897f:…:1."""

    return {
        "summary": (
            "Workflow completed an authoritative durable continuation "
            "handoff to merge automation."
        ),
        "mergeAutomationDisposition": "reenter_gate",
        "terminalContractId": "pr_resolver_terminal.v1",
        "terminalContractOutcome": "continuation_requested",
        "terminalContractExecutionRef": "resolver:pr:4207:head:0b64408a897f:h:1360697d01305a39:1",
        "terminalContractRecoveryOutcome": "durable_parent_handoff",
        "gatedContinuation": {
            "schemaVersion": "gated-continuation/v1",
            "gateType": "merge_automation",
            "action": "reenter_gate",
            "reason": "ci_signal_degraded",
            "retryAfterSeconds": 60,
        },
        "headSha": "0b64408a897f",
    }


def test_reenter_gate_defers_publication_before_evidence_check(
    deferred_workflow: MoonMindRunWorkflow,
) -> None:
    deferred_workflow._record_execution_context(
        node_id="resolve-pr",
        execution_result={"outputs": _reenter_gate_outputs()},
    )
    deferred_workflow._record_auto_publish_result(
        {"outputs": _reenter_gate_outputs()},
    )

    assert deferred_workflow._publish_status == "not_required"
    assert "reenter_gate" in (deferred_workflow._publish_reason or "")
    assert "continuation_requested" in (deferred_workflow._publish_reason or "")


def test_reenter_gate_writes_deferred_finalization_outcome(
    deferred_workflow: MoonMindRunWorkflow,
) -> None:
    deferred_workflow._step_ledger_rows = [
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
    deferred_workflow._rebuild_step_ledger_index()
    deferred_workflow._record_execution_context(
        node_id="resolve-pr",
        execution_result={"outputs": _reenter_gate_outputs()},
    )
    deferred_workflow._record_auto_publish_result(
        {"outputs": _reenter_gate_outputs()},
    )
    deferred_workflow._apply_deferred_publication(
        reason=deferred_workflow._publish_reason or "deferred",
        logical_step_id="resolve-pr",
    )

    row = deferred_workflow._step_ledger_row_for("resolve-pr")
    assert row is not None
    outcome = row["finalizationOutcome"]
    assert outcome["status"] == "unsupported"
    assert outcome["criticality"] == "unsupported"
    assert outcome["phase"] == "publication"
    assert outcome["retryCount"] == 0
    assert outcome.get("failureCode") is None


def test_rejected_continuation_does_not_defer_publication(
    deferred_workflow: MoonMindRunWorkflow,
) -> None:
    """A rejected/unowned continuation must fail closed, not defer."""

    for recovery in (
        "continuation_rejected_unowned",
        "continuation_rejected_failure_provenance",
        "continuation_rejected_ownership",
        "continuation_rejected_schema",
    ):
        outputs = {
            "mergeAutomationDisposition": "reenter_gate",
            "terminalContractOutcome": "continuation_requested",
            "terminalContractRecoveryOutcome": recovery,
        }
        assert (
            deferred_workflow._terminal_publication_deferred_reason(outputs) is None
        ), recovery

    # Missing recovery or disallowed disposition also fails closed.
    assert (
        deferred_workflow._terminal_publication_deferred_reason(
            {
                "mergeAutomationDisposition": "reenter_gate",
                "terminalContractOutcome": "continuation_requested",
            }
        )
        is None
    )
    assert (
        deferred_workflow._terminal_publication_deferred_reason(
            {
                "mergeAutomationDisposition": "merged",
                "terminalContractOutcome": "continuation_requested",
                "terminalContractRecoveryOutcome": "durable_parent_handoff",
            }
        )
        is None
    )


def test_deferred_ledger_outcome_validates_against_schema(
    deferred_workflow: MoonMindRunWorkflow,
) -> None:
    """The deferred ledger row must pass StepFinalizationOutcomeModel validation."""

    from moonmind.schemas.temporal_models import StepFinalizationOutcomeModel

    deferred_workflow._step_ledger_rows = [
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
    deferred_workflow._rebuild_step_ledger_index()
    deferred_workflow._record_execution_context(
        node_id="resolve-pr",
        execution_result={"outputs": _reenter_gate_outputs()},
    )
    deferred_workflow._apply_deferred_publication(
        reason=deferred_workflow._publish_reason or "deferred",
        logical_step_id="resolve-pr",
    )
    row = deferred_workflow._step_ledger_row_for("resolve-pr")
    assert row is not None
    StepFinalizationOutcomeModel.model_validate(row["finalizationOutcome"])


def test_reenter_gate_completion_is_not_a_publication_failure(
    deferred_workflow: MoonMindRunWorkflow,
) -> None:
    """Projection boundary: the handed-off resolver is not a failure."""

    deferred_workflow._record_execution_context(
        node_id="resolve-pr",
        execution_result={"outputs": _reenter_gate_outputs()},
    )
    deferred_workflow._record_auto_publish_result(
        {"outputs": _reenter_gate_outputs()},
    )

    status, _message, failed = deferred_workflow._determine_publish_completion(
        parameters={"publishMode": "auto"},
    )

    assert failed is False
    assert status in {"success", "no_commit"}
    assert deferred_workflow._publish_status == "not_required"


@pytest.mark.parametrize("disposition", ["manual_review", "failed"])
def test_validated_skill_verdict_defers_publication(
    deferred_workflow: MoonMindRunWorkflow, disposition: str
) -> None:
    outputs = {
        "summary": f"pr-resolver reported {disposition}",
        "mergeAutomationDisposition": disposition,
        "terminalContractId": "pr_resolver_terminal.v1",
        "terminalContractOutcome": "terminal_failure",
        "terminalContractRecoveryOutcome": "skill_terminal_verdict",
    }
    deferred_workflow._record_execution_context(
        node_id="resolve-pr",
        execution_result={"outputs": outputs},
    )
    deferred_workflow._record_auto_publish_result({"outputs": outputs})

    assert deferred_workflow._publish_status == "not_required"
    assert disposition in (deferred_workflow._publish_reason or "")


def test_request_review_defers_publication(
    deferred_workflow: MoonMindRunWorkflow,
) -> None:
    outputs = {
        "mergeAutomationDisposition": "request_review",
        "terminalContractOutcome": "continuation_requested",
        "terminalContractRecoveryOutcome": "durable_parent_handoff",
    }
    deferred_workflow._record_auto_publish_result({"outputs": outputs})

    assert deferred_workflow._publish_status == "not_required"
    assert "request_review" in (deferred_workflow._publish_reason or "")


def test_merged_without_evidence_still_fails(
    deferred_workflow: MoonMindRunWorkflow,
) -> None:
    outputs = {
        "mergeAutomationDisposition": "merged",
        "terminalContractId": "pr_resolver_terminal.v1",
        "terminalContractOutcome": "terminal_success",
    }
    deferred_workflow._record_auto_publish_result({"outputs": outputs})

    assert deferred_workflow._publish_status == "failed"
    assert deferred_workflow._publish_reason == "auto_publish_evidence_missing"


def test_deferred_publication_does_not_spend_finalization_retry(
    deferred_workflow: MoonMindRunWorkflow,
) -> None:
    deferred_workflow._step_ledger_rows = [
        {
            "logicalStepId": "resolve-pr",
            "finalizationOutcome": {
                "status": "succeeded",
                "phase": "after_execution_checkpoint",
                "criticality": "required",
                "retryCount": 0,
            },
        }
    ]
    deferred_workflow._rebuild_step_ledger_index()
    deferred_workflow._record_execution_context(
        node_id="resolve-pr",
        execution_result={"outputs": _reenter_gate_outputs()},
    )

    # The old failure path must become a deferred outcome with no retry.
    deferred_workflow._record_publication_finalization_failure(
        "resolve-pr",
        exc=RuntimeError("auto_publish_evidence_missing"),
        updated_at=datetime.now(timezone.utc),
    )

    row = deferred_workflow._step_ledger_row_for("resolve-pr")
    assert row is not None
    outcome = row["finalizationOutcome"]
    assert outcome["status"] == "unsupported"
    assert outcome["criticality"] == "unsupported"
    assert outcome["retryCount"] == 0
    assert outcome.get("failureCode") != FINALIZATION_PUBLICATION_FAILED
    assert deferred_workflow._publish_status == "not_required"


def test_deferred_handoff_preserves_required_finalization_failure(
    deferred_workflow: MoonMindRunWorkflow,
) -> None:
    """A required checkpoint failure survives a later validated continuation."""

    deferred_workflow._step_ledger_rows = [
        {
            "logicalStepId": "resolve-pr",
            "finalizationOutcome": {
                "status": "failed",
                "phase": "after_execution_checkpoint",
                "criticality": "required",
                "failureCode": "FINALIZATION_CHECKPOINT_FAILED",
                "terminalFailureCode": "FINALIZATION_RETRY_EXHAUSTED",
                "retryCount": 1,
                "message": "Execution succeeded; finalization failed during the "
                "after-execution checkpoint.",
                "updatedAt": datetime.now(timezone.utc).isoformat(),
            },
        }
    ]
    deferred_workflow._rebuild_step_ledger_index()
    deferred_workflow._publish_status = "failed"
    deferred_workflow._publish_reason = (
        "Execution succeeded; finalization failed during the "
        "after-execution checkpoint."
    )

    deferred_workflow._record_auto_publish_result(
        {"outputs": _reenter_gate_outputs()},
    )
    assert deferred_workflow._publish_status == "failed"

    deferred_workflow._apply_deferred_publication(
        reason="publication deferred to gate owner for reenter_gate terminal "
        "(continuation_requested)",
        logical_step_id="resolve-pr",
    )
    row = deferred_workflow._step_ledger_row_for("resolve-pr")
    assert row is not None
    outcome = row["finalizationOutcome"]
    assert outcome["status"] == "failed"
    assert outcome["criticality"] == "required"
    assert outcome["phase"] == "after_execution_checkpoint"

    status, _message, failed = deferred_workflow._determine_publish_completion(
        parameters={"publishMode": "auto"},
    )
    assert status == "failed"
    assert failed is True
