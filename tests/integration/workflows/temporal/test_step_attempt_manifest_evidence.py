from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

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


async def test_step_attempt_manifest_refs_are_append_only_for_reexecution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindRunWorkflow()
    writes: list[dict[str, Any]] = []
    now = datetime(2026, 5, 17, 12, 0, tzinfo=UTC)

    async def fake_write_json_artifact(
        *,
        name: str,
        payload: dict[str, Any],
        content_type: str = "application/json",
        metadata_json: dict[str, Any] | None = None,
    ) -> str:
        writes.append(
            {
                "name": name,
                "payload": payload,
                "content_type": content_type,
                "metadata_json": metadata_json,
            }
        )
        return f"artifact-attempt-{len(writes)}"

    monkeypatch.setattr(workflow, "_write_json_artifact", fake_write_json_artifact)
    workflow._initialize_step_ledger(
        ordered_nodes=[
            {
                "id": "implement",
                "tool": {"type": "agent_runtime", "name": "codex", "version": ""},
                "inputs": {"title": "Implement"},
            }
        ],
        dependency_map={"implement": []},
        updated_at=now,
    )

    workflow._mark_step_running("implement", updated_at=now, summary="Initial")
    await workflow._record_step_attempt_manifest_start(
        "implement",
        updated_at=now,
        reason="initial_execution",
    )
    workflow._mark_step_terminal(
        "implement",
        status="failed",
        updated_at=now,
        summary="Failed gate",
    )
    workflow._mark_step_running("implement", updated_at=now, summary="Retry")
    await workflow._record_step_attempt_manifest_start(
        "implement",
        updated_at=now,
        reason="quality_gate_failed",
    )

    step = workflow.get_step_ledger()["steps"][0]
    assert step["refs"]["latestAttemptManifestRef"] == "artifact-attempt-2"
    assert step["refs"]["attemptManifestRefs"] == [
        "artifact-attempt-1",
        "artifact-attempt-2",
    ]
    assert writes[0]["payload"]["workspace"]["policy"] == "fresh_branch_from_source"
    assert writes[0]["payload"]["workspace"]["evidenceAccepted"] is True
    assert writes[1]["payload"]["workspace"]["policy"] == "continue_from_previous_attempt"
    assert writes[1]["payload"]["workspace"]["evidenceAccepted"] is False
    assert writes[1]["payload"]["workspace"]["rejectionReason"] == (
        "missing_required_checkpoint_evidence"
    )
    assert writes[1]["payload"]["workspace"]["sourceAttempt"] == {
        "workflowId": "wf-boundary",
        "runId": "run-boundary",
        "logicalStepId": "implement",
        "attempt": 1,
    }


async def test_resume_attempt_manifest_carries_lineage_without_large_payloads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindRunWorkflow()
    workflow._resume_source = {
        "sourceWorkflowId": "wf-source",
        "sourceRunId": "run-source",
        "sourceTaskInputSnapshotRef": "artifact://snapshot/source",
        "sourcePlanDigest": "sha256:source-plan",
        "failedStepId": "implement",
        "failedStepAttempt": 2,
        "resumeCheckpointRef": "artifact://resume/checkpoint",
        "resumeWorkspace": {
            "checkpointRef": "artifact://workspace/before-implement",
        },
        "preservedSteps": [],
    }
    writes: list[dict[str, Any]] = []
    now = datetime(2026, 5, 17, 12, 0, tzinfo=UTC)

    async def fake_write_json_artifact(
        *,
        name: str,
        payload: dict[str, Any],
        content_type: str = "application/json",
        metadata_json: dict[str, Any] | None = None,
    ) -> str:
        writes.append(
            {
                "name": name,
                "payload": payload,
                "content_type": content_type,
                "metadata_json": metadata_json,
            }
        )
        return f"artifact-attempt-{len(writes)}"

    monkeypatch.setattr(workflow, "_write_json_artifact", fake_write_json_artifact)
    workflow._initialize_step_ledger(
        ordered_nodes=[{"id": "implement", "title": "Implement"}],
        dependency_map={"implement": []},
        updated_at=now,
    )

    workflow._mark_step_running("implement", updated_at=now, summary="Resume")
    await workflow._record_step_attempt_manifest_start(
        "implement",
        updated_at=now,
        reason="resume_from_failed_step",
    )

    manifest = writes[0]["payload"]
    assert manifest["lineage"]["sourceWorkflowId"] == "wf-source"
    assert manifest["lineage"]["sourceRunId"] == "run-source"
    assert manifest["lineage"]["sourceAttempt"] == 2
    assert manifest["lineage"]["lineageAttemptOrdinal"] == 3
    assert manifest["workspace"]["policy"] == "start_from_last_passed_commit"
    assert manifest["workspace"]["checkpointRef"] == (
        "artifact://workspace/before-implement"
    )
    assert manifest["workspace"]["evidenceAccepted"] is True
    assert "payload" not in manifest
