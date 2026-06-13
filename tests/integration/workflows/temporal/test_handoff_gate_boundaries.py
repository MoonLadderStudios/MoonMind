from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from moonmind.workflows.temporal.step_executions import (
    evaluate_handoff_gate,
    handoff_gate_side_effect_record,
)
from moonmind.workflows.temporal.workflows import run as run_module
from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]


def _configure_workflow_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    workflow_info = SimpleNamespace(
        namespace="default",
        workflow_id="wf-boundary",
        run_id="run-boundary",
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


def _gate_input(disposition: str | None = "accepted") -> dict[str, Any]:
    return {
        "workflowId": "wf-boundary",
        "runId": "run-boundary",
        "handoffClass": "repo_pr",
        "actor": {"type": "workflow", "id": "MoonMindRunWorkflow"},
        "action": "repo.create_pull_request",
        "target": {"type": "repository", "id": "MoonLadderStudios/MoonMind"},
        "idempotencyKey": (
            "wf-boundary:run-boundary:verify:execution:1:repo.create_pull_request"
        ),
        "producingStepExecution": {
            "workflowId": "wf-boundary",
            "runId": "run-boundary",
            "logicalStepId": "verify",
            "executionOrdinal": 1,
            "stepExecutionId": "wf-boundary:run-boundary:verify:execution:1",
            "terminalDisposition": disposition,
            "manifestRef": "artifact://step-executions/verify-1-terminal",
        },
        "gateSource": {
            "gateType": "moonspec_verify",
            "verdict": "FULLY_IMPLEMENTED",
            "passed": True,
            "logicalStepId": "verify",
            "evidenceRef": "artifact://verify/report",
        },
        "evidenceRefs": ["artifact://verify/report"],
    }


async def test_accepted_handoff_decision_records_terminal_governance_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindRunWorkflow()
    writes: list[dict[str, Any]] = []
    now = datetime(2026, 6, 13, 12, 0, tzinfo=UTC)

    async def fake_write_json_artifact(
        *,
        name: str,
        payload: dict[str, Any],
        content_type: str = "application/json",
        metadata_json: dict[str, Any] | None = None,
    ) -> str:
        writes.append({"name": name, "payload": payload})
        return f"artifact://written/{len(writes)}"

    monkeypatch.setattr(workflow, "_write_json_artifact", fake_write_json_artifact)
    workflow._initialize_step_ledger(
        ordered_nodes=[{"id": "publish", "title": "Publish"}],
        dependency_map={"publish": []},
        updated_at=now,
    )
    workflow._mark_step_running("publish", updated_at=now, summary="Publishing")

    decision = evaluate_handoff_gate(_gate_input("accepted"))
    workflow._step_side_effect_records.setdefault("publish", []).append(
        handoff_gate_side_effect_record(decision, effect_class="publication")
    )
    workflow._mark_step_terminal(
        "publish",
        status="succeeded",
        updated_at=now,
        summary="Published",
    )

    await workflow._record_step_execution_manifest(
        "publish",
        phase="terminal",
        updated_at=now,
        reason="initial_execution",
        status="succeeded",
        terminal_disposition="accepted",
    )

    terminal = writes[-1]["payload"]
    record = terminal["sideEffects"]["records"][0]
    assert record["disposition"] == "accepted"
    assert record["policyDecision"]["decision"] == "allow"
    assert record["gateSource"]["passed"] is True
    assert record["dispositionSource"]["terminalDisposition"] == "accepted"
    assert record["evidenceRefs"]


@pytest.mark.parametrize("disposition", ["candidate", "blocked", "needs_human", None])
async def test_unaccepted_handoff_decision_records_blocked_attempt(
    monkeypatch: pytest.MonkeyPatch,
    disposition: str | None,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindRunWorkflow()
    writes: list[dict[str, Any]] = []
    now = datetime(2026, 6, 13, 12, 0, tzinfo=UTC)

    async def fake_write_json_artifact(
        *,
        name: str,
        payload: dict[str, Any],
        content_type: str = "application/json",
        metadata_json: dict[str, Any] | None = None,
    ) -> str:
        writes.append({"name": name, "payload": payload})
        return f"artifact://written/{len(writes)}"

    monkeypatch.setattr(workflow, "_write_json_artifact", fake_write_json_artifact)
    workflow._initialize_step_ledger(
        ordered_nodes=[{"id": "publish", "title": "Publish"}],
        dependency_map={"publish": []},
        updated_at=now,
    )
    workflow._mark_step_running("publish", updated_at=now, summary="Publishing")

    decision = evaluate_handoff_gate(_gate_input(disposition))
    workflow._step_side_effect_records.setdefault("publish", []).append(
        handoff_gate_side_effect_record(decision, effect_class="publication")
    )
    workflow._mark_step_terminal(
        "publish",
        status="blocked",
        updated_at=now,
        summary="Blocked",
    )

    await workflow._record_step_execution_manifest(
        "publish",
        phase="terminal",
        updated_at=now,
        reason="policy_revalidation",
        status="blocked",
        terminal_disposition="blocked",
    )

    record = writes[-1]["payload"]["sideEffects"]["records"][0]
    assert record["disposition"] == "blocked"
    assert record["policyDecision"]["decision"] == "block"
    assert record["policyDecision"]["reason"] == (
        "producing_step_execution_not_accepted"
    )
    assert "terminal disposition" in str(record["diagnosticMessages"])
