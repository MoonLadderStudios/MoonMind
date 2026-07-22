"""Workflow-boundary tests for failed-run recovery manifest emission (MM-881).

These exercise the real ``MoonMindRunWorkflow`` methods that emit the recovery
manifest so that every failed run produces it (durable artifact + compact,
execution-linked reference) before terminal failure is reported.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from moonmind.schemas.temporal_models import (
    FAILED_RUN_RECOVERY_MANIFEST_CONTENT_TYPE,
)
from moonmind.workflows.executions.runtime_capabilities import (
    resolve_runtime_execution_capabilities,
)
from moonmind.workflows.temporal.workflows import run as run_module
from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow

_NOW = datetime(2026, 6, 24, 12, 0, tzinfo=UTC)


def _configure(monkeypatch: pytest.MonkeyPatch, *, patched: bool = True) -> None:
    workflow_info = SimpleNamespace(
        namespace="default",
        workflow_id="wf-run-1",
        run_id="run-1",
        task_queue="mm.workflow",
        start_time=datetime(2026, 6, 24, 11, 0, tzinfo=UTC),
        search_attributes={"mm_owner_type": ["user"], "mm_owner_id": ["user-1"]},
    )
    logger = SimpleNamespace(
        info=lambda *a, **k: None,
        warning=lambda *a, **k: None,
        error=lambda *a, **k: None,
        debug=lambda *a, **k: None,
        isEnabledFor=lambda *_a, **_k: False,
    )
    monkeypatch.setattr(run_module.workflow, "info", lambda: workflow_info)
    monkeypatch.setattr(run_module.workflow, "logger", logger)
    monkeypatch.setattr(run_module.workflow, "now", lambda: _NOW)
    monkeypatch.setattr(run_module.workflow, "upsert_memo", lambda *_a, **_k: None)
    monkeypatch.setattr(
        run_module.workflow, "upsert_search_attributes", lambda *_a, **_k: None
    )
    monkeypatch.setattr(run_module.workflow, "patched", lambda _patch_id: patched)


def _seed_failed_run(workflow: MoonMindRunWorkflow) -> None:
    workflow._owner_id = "user-1"
    workflow._owner_type = "user"
    workflow._step_ledger_rows = [
        {
            "logicalStepId": "prepare",
            "order": 1,
            "status": "succeeded",
            "executionOrdinal": 1,
            "terminalDisposition": "accepted",
            "title": "Prepare workspace",
        },
        {
            "logicalStepId": "run-tests",
            "order": 2,
            "status": "failed",
            "executionOrdinal": 1,
            "title": "Run tests",
        },
    ]
    workflow._step_terminal_dispositions = {"prepare": "accepted"}
    workflow._step_checkpoint_refs_by_boundary = {
        "run-tests": {"before_execution": "artifact://checkpoint/before"}
    }
    workflow._step_side_effect_records = {
        "run-tests": [
            {
                "class": "workspace_mutation",
                "operation": "edit-file",
                "disposition": "accepted",
            }
        ]
    }
    workflow._failure_diagnostic = {
        "stepId": "run-tests",
        "stage": "executing",
        "category": "execution_error",
        "message": "tests failed",
    }


@pytest.mark.asyncio
async def test_emit_recovery_manifest_writes_artifact_and_compact_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch, patched=True)
    workflow = MoonMindRunWorkflow()
    _seed_failed_run(workflow)
    capabilities = resolve_runtime_execution_capabilities("omnigent")
    assert capabilities is not None
    workflow._step_workspace_capture_inputs["run-tests"] = {
        "kind": "external_state_ref",
        "runtimeCapabilities": capabilities.model_dump(by_alias=True, mode="json"),
    }

    writes: list[dict[str, Any]] = []

    async def fake_write_json_artifact(
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
        return f"artifact-{len(writes)}"

    monkeypatch.setattr(workflow, "_write_json_artifact", fake_write_json_artifact)

    summary, manifest_ref = await workflow._emit_failed_run_recovery_manifest()

    assert manifest_ref == "artifact-1"
    assert len(writes) == 1
    write = writes[0]
    assert write["name"] == "reports/recovery_manifest.json"
    assert write["content_type"] == FAILED_RUN_RECOVERY_MANIFEST_CONTENT_TYPE
    assert write["metadata_json"]["artifact_kind"] == "failed_run_recovery_manifest"
    assert write["metadata_json"]["resumeAllowed"] is True
    # Durable manifest payload names every required field.
    payload = write["payload"]
    assert payload["failedLogicalStepId"] == "run-tests"
    assert payload["lastAcceptedStep"]["logicalStepId"] == "prepare"
    assert payload["validation"]["result"] == "valid"
    assert payload["resumeAllowed"] is True
    assert payload["recoveryEligibility"]["checkpointKind"] == "external_state_ref"
    # Compact, execution-linked summary.
    assert summary["resumeAllowed"] is True
    assert summary["failedLogicalStepId"] == "run-tests"
    assert summary["checkpointRef"] == "artifact://checkpoint/before"
    assert summary["manifestRef"] == "artifact-1"
    assert workflow._recovery_manifest_ref == "artifact-1"


@pytest.mark.asyncio
async def test_recovery_manifest_freezes_hybrid_planes_without_inferred_session_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise the workflow boundary used to project a v3 hybrid snapshot."""

    _configure(monkeypatch, patched=True)
    workflow = MoonMindRunWorkflow()
    _seed_failed_run(workflow)
    capabilities = resolve_runtime_execution_capabilities("omnigent")
    workflow._step_workspace_capture_inputs["run-tests"] = {
        "kind": "worktree_archive",
        "runtimeCapabilities": capabilities.model_dump(by_alias=True, mode="json"),
    }
    writes: list[dict[str, Any]] = []

    async def fake_write_json_artifact(
        name: str,
        payload: dict[str, Any],
        content_type: str = "application/json",
        metadata_json: dict[str, Any] | None = None,
    ) -> str:
        writes.append(payload)
        return "artifact-hybrid-manifest"

    monkeypatch.setattr(workflow, "_write_json_artifact", fake_write_json_artifact)

    summary, manifest_ref = await workflow._emit_failed_run_recovery_manifest()

    assert manifest_ref == "artifact-hybrid-manifest"
    assert summary is not None and summary["resumeAllowed"] is True
    eligibility = writes[0]["recoveryEligibility"]
    assert eligibility["capabilitySetVersion"] == "runtime-execution-capabilities-v3"
    assert eligibility["sessionRecoverable"] is False
    assert eligibility["workspaceRecoverable"] is True
    rendered = str(writes[0])
    assert "workspacePath" not in rendered
    assert "githubToken" not in rendered


@pytest.mark.asyncio
async def test_emit_recovery_manifest_is_patch_gated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch, patched=False)
    workflow = MoonMindRunWorkflow()
    _seed_failed_run(workflow)

    writes: list[dict[str, Any]] = []

    async def fake_write_json_artifact(*args: Any, **kwargs: Any) -> str:
        writes.append({"args": args, "kwargs": kwargs})
        return "should-not-happen"

    monkeypatch.setattr(workflow, "_write_json_artifact", fake_write_json_artifact)

    summary, manifest_ref = await workflow._emit_failed_run_recovery_manifest()

    assert summary is None
    assert manifest_ref is None
    assert writes == []


@pytest.mark.asyncio
async def test_emit_recovery_manifest_blocks_resume_on_degraded_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch, patched=True)
    workflow = MoonMindRunWorkflow()
    _seed_failed_run(workflow)
    # Simulate a resume-path checkpoint validation failure captured before the
    # failure was raised (workspace policy incompatible).
    workflow._recovery_failed_step_id = "run-tests"
    workflow._recovery_checkpoint_validation_failure = {
        "failureCode": "policy_incompatible",
        "checkpointRef": "artifact://checkpoint/before",
    }

    async def fake_write_json_artifact(*_a: Any, **_k: Any) -> str:
        return "artifact-1"

    monkeypatch.setattr(workflow, "_write_json_artifact", fake_write_json_artifact)

    summary, _ = await workflow._emit_failed_run_recovery_manifest()

    assert summary is not None
    assert summary["resumeAllowed"] is False
    assert summary["validationResult"] == "incompatible"
    assert summary["blockedReason"] == "workspace_policy_incompatible"


@pytest.mark.asyncio
async def test_recovery_manifest_emitted_before_terminal_failure_in_finalizing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The finalizing stage embeds the recovery manifest in the finish summary
    and writes the durable manifest artifact before the run-summary artifact
    (which precedes terminal-state recording)."""

    _configure(monkeypatch, patched=True)
    workflow = MoonMindRunWorkflow()
    _seed_failed_run(workflow)

    events: list[str] = []

    async def fake_write_json_artifact(
        name: str,
        payload: dict[str, Any],
        content_type: str = "application/json",
        metadata_json: dict[str, Any] | None = None,
    ) -> str:
        events.append(f"manifest:{name}")
        return "manifest-artifact-1"

    monkeypatch.setattr(workflow, "_write_json_artifact", fake_write_json_artifact)

    async def fake_execute_activity(activity_type: str, *_a: Any, **_k: Any) -> Any:
        events.append(f"activity:{activity_type}")
        if activity_type == "artifact.create":
            return ({"artifact_id": "run-summary-artifact"}, "desc")
        return None

    async def fake_execute_typed_activity(activity_type: str, *_a: Any, **_k: Any) -> Any:
        events.append(f"typed:{activity_type}")
        return None

    monkeypatch.setattr(run_module.workflow, "execute_activity", fake_execute_activity)
    monkeypatch.setattr(
        run_module, "execute_typed_activity", fake_execute_typed_activity
    )

    await workflow._run_finalizing_stage(
        parameters={"publishMode": "pr", "runtime": {"mode": "codex_cli"}},
        status="failed",
        error="tests failed",
    )

    # Recovery manifest is embedded in the execution-linked finish summary.
    assert workflow._finish_summary is not None
    recovery_block = workflow._finish_summary.get("recoveryManifest")
    assert recovery_block is not None
    assert recovery_block["resumeAllowed"] is False
    assert recovery_block["failedLogicalStepId"] == "run-tests"
    assert recovery_block["manifestRef"] == "manifest-artifact-1"

    # The durable manifest artifact is written before the run-summary artifact
    # create, i.e. before terminal failure is reported.
    manifest_event = "manifest:reports/recovery_manifest.json"
    run_summary_event = "activity:artifact.create"
    assert manifest_event in events
    assert run_summary_event in events
    assert events.index(manifest_event) < events.index(run_summary_event)
