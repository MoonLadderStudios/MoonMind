"""Workflow-boundary tests for incident reconstruction emission (MM-884).

These exercise the real ``MoonMindRunWorkflow`` methods that emit the incident
reconstruction manifest (durable artifact + compact, execution-linked summary)
before terminal failure is reported, stamp trace refs onto step manifests, and
capture sanitized provider failure + observed cost evidence.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from moonmind.schemas.incident_reconstruction_models import (
    INCIDENT_RECONSTRUCTION_CONTENT_TYPE,
)
from moonmind.workflows.temporal.incident_reconstruction import (
    derive_incident_trace_id,
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
    workflow._target_runtime = "codex_cli"
    workflow._correlation_id = "ext-abc"
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
            "logicalStepId": "publish",
            "order": 2,
            "status": "failed",
            "executionOrdinal": 2,
            "title": "Publish PR",
        },
    ]
    workflow._step_terminal_dispositions = {"prepare": "accepted"}
    workflow._step_checkpoint_refs_by_boundary = {
        "publish": {"after_gate": "artifact://checkpoint/after-gate"}
    }
    workflow._step_side_effect_records = {
        "publish": [
            {
                "class": "workspace_mutation",
                "operation": "edit-file",
                "disposition": "accepted",
            },
            {
                "class": "publication",
                "operation": "open-pr",
                "disposition": "blocked",
                "target": "repo#1",
            },
        ]
    }
    workflow._failure_diagnostic = {
        "stepId": "publish",
        "stage": "publishing",
        "category": "integration_error",
        "message": "publish blocked",
    }
    workflow._resilience_policy_ref = {
        "policyId": "resilience-policy-abc",
        "policyVersion": 1,
        "digest": "d" * 64,
        "contentType": "application/vnd.moonmind.resilience-policy+json;version=1",
        "envelopeRef": "artifact://policy/env-1",
    }
    workflow._run_resilience_profile_id = "profile-9"
    workflow._cost_attribution_settings = {
        "runtimeId": "codex_cli",
        "model": "gpt-4o",
        "effort": "high",
    }
    workflow._logs_ref = "artifact://logs/spool-1"
    workflow._provider_failure_envelope = {
        "providerErrorClass": "rate_limit",
        "providerErrorCode": "429",
        "retryAfterSeconds": 30,
        "rawErrorRef": "artifact://provider/raw-1",
        "sanitizedSummary": (
            "Provider rate limit reached; the run will retry after a profile cooldown."
        ),
    }


@pytest.mark.asyncio
async def test_emit_incident_manifest_writes_artifact_and_compact_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch, patched=True)
    workflow = MoonMindRunWorkflow()
    _seed_failed_run(workflow)
    # The recovery manifest must be built first so the incident path reuses it.
    workflow._recovery_manifest_ref = "artifact://recovery/manifest-1"
    workflow._recovery_manifest_model = run_module.build_failed_run_recovery_manifest(
        workflow_id="wf-run-1",
        run_id="run-1",
        created_at=_NOW,
        step_ledger_rows=workflow._step_ledger_rows,
        terminal_dispositions=workflow._step_terminal_dispositions,
        checkpoint_refs_by_boundary=workflow._step_checkpoint_refs_by_boundary,
        side_effect_records=workflow._step_side_effect_records,
        failure_diagnostic=workflow._failure_diagnostic,
    )

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
        return f"incident-artifact-{len(writes)}"

    monkeypatch.setattr(workflow, "_write_json_artifact", fake_write_json_artifact)

    summary, manifest_ref = await workflow._emit_incident_reconstruction_manifest()

    assert manifest_ref == "incident-artifact-1"
    write = writes[0]
    assert write["name"] == "reports/incident_reconstruction.json"
    assert write["content_type"] == INCIDENT_RECONSTRUCTION_CONTENT_TYPE
    assert write["metadata_json"]["artifact_kind"] == "incident_reconstruction_manifest"
    # Durable manifest correlates every category and shares one trace id.
    payload = write["payload"]
    expected_trace = derive_incident_trace_id("wf-run-1", "run-1")
    assert payload["trace"]["traceId"] == expected_trace
    assert payload["failedLogicalStepId"] == "publish"
    present = {item["kind"] for item in payload["evidence"] if item["present"]}
    assert {"policy", "provider", "side_effects", "checkpoint", "cost", "trace"} <= present
    # Compact, execution-linked summary.
    assert summary["traceId"] == expected_trace
    assert summary["providerErrorClass"] == "rate_limit"
    assert summary["manifestRef"] == "incident-artifact-1"
    assert workflow._incident_reconstruction_ref == "incident-artifact-1"


@pytest.mark.asyncio
async def test_emit_incident_manifest_is_patch_gated(
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

    summary, manifest_ref = await workflow._emit_incident_reconstruction_manifest()

    assert summary is None
    assert manifest_ref is None
    assert writes == []


@pytest.mark.asyncio
async def test_incident_manifest_emitted_before_terminal_failure_in_finalizing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The finalizing stage embeds the incident manifest in the finish summary
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
        return f"artifact:{name}"

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
        error="publish blocked",
    )

    # Incident reconstruction is embedded in the execution-linked finish summary.
    assert workflow._finish_summary is not None
    incident_block = workflow._finish_summary.get("incidentReconstruction")
    assert incident_block is not None
    assert incident_block["traceId"] == derive_incident_trace_id("wf-run-1", "run-1")
    assert incident_block["manifestRef"] == "artifact:reports/incident_reconstruction.json"
    assert "trace" in incident_block["evidencePresent"]

    # The durable incident manifest artifact is written before the run-summary
    # artifact create, i.e. before terminal failure is reported.
    incident_event = "manifest:reports/incident_reconstruction.json"
    run_summary_event = "activity:artifact.create"
    assert incident_event in events
    assert run_summary_event in events
    assert events.index(incident_event) < events.index(run_summary_event)
    # And the recovery manifest precedes the incident manifest.
    recovery_event = "manifest:reports/recovery_manifest.json"
    assert events.index(recovery_event) < events.index(incident_event)


@pytest.mark.asyncio
async def test_workflow_gate_finalization_preserves_auxiliary_failures_and_redacts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch, patched=True)
    workflow = MoonMindRunWorkflow()
    _seed_failed_run(workflow)
    workflow._publish_status = "failed"
    workflow._publish_reason = "branch publication failed"
    workflow._workflow_control_stop = {
        "kind": "workflow_gate",
        "reasonCode": "semantic_no_progress_exhausted",
        "logicalStepId": "verify-final",
        "verdict": "ADDITIONAL_WORK_NEEDED",
        "terminalDisposition": "failed_with_remaining_work",
        "gateResultRef": "artifact://gate/final",
        "remainingWorkRef": "artifact://remaining/final",
        "workspaceHeadRef": "artifact://workspace/final",
        "publicationFeasible": True,
        "publicationAttempted": True,
        "metrics": {"failureKind": "workflow_gate"},
        "auxiliaryOutcomes": {
            "gitPublication": {"status": "failed", "reason": "push rejected"},
            "hostCleanup": {"status": "pending"},
            "providerProfileRelease": {
                "status": "failed",
                "reason": "token=provider-secret",
            },
            "janitorRequired": True,
        },
    }

    async def fail_cleanup(*, reason: str) -> None:
        raise RuntimeError(f"cleanup failed: token=cleanup-secret ({reason})")

    writes: list[dict[str, Any]] = []

    async def fake_write_json_artifact(
        name: str,
        payload: dict[str, Any],
        content_type: str = "application/json",
        metadata_json: dict[str, Any] | None = None,
    ) -> str:
        writes.append({"name": name, "payload": payload})
        return f"artifact:{name}"

    async def fake_execute_activity(activity_type: str, *_a: Any, **_k: Any) -> Any:
        if activity_type == "artifact.create":
            return ({"artifact_id": "run-summary-artifact"}, "desc")
        return None

    async def fake_execute_typed_activity(*_a: Any, **_k: Any) -> None:
        return None

    monkeypatch.setattr(workflow, "_terminate_workflow_scoped_sessions", fail_cleanup)
    monkeypatch.setattr(workflow, "_write_json_artifact", fake_write_json_artifact)
    monkeypatch.setattr(run_module.workflow, "execute_activity", fake_execute_activity)
    monkeypatch.setattr(run_module, "execute_typed_activity", fake_execute_typed_activity)

    await workflow._run_finalizing_stage(
        parameters={"publishMode": "pr", "runtime": {"mode": "codex_cli"}},
        status="failed",
        error="quality gate stopped the workflow",
    )

    finish = workflow._finish_summary
    assert finish is not None
    assert finish["finishOutcome"]["code"] == "FAILED"
    assert finish["primaryOutcome"]["kind"] == "workflow_gate"
    assert finish["primaryOutcome"]["verdict"] == "ADDITIONAL_WORK_NEEDED"
    auxiliary = finish["primaryOutcome"]["auxiliaryOutcomes"]
    assert auxiliary["gitPublication"]["status"] == "failed"
    assert auxiliary["hostCleanup"]["status"] == "failed"
    assert auxiliary["providerProfileRelease"]["status"] == "failed"
    assert auxiliary["janitorRequired"] is True
    assert finish["controlStop"]["remainingWorkRef"] == "artifact://remaining/final"
    assert (
        finish["recoveryManifest"]["controlStop"]["remainingWorkRef"]
        == "artifact://remaining/final"
    )
    assert (
        finish["incidentReconstruction"]["controlStop"]["remainingWorkRef"]
        == "artifact://remaining/final"
    )
    rendered = str(finish)
    assert "provider-secret" not in rendered
    assert "cleanup-secret" not in rendered

    incident = next(
        write["payload"]
        for write in writes
        if write["name"] == "reports/incident_reconstruction.json"
    )
    assert incident["artifactRefs"]["remainingWork"] == "artifact://remaining/final"
    assert "provider-secret" not in str(incident)
    assert "cleanup-secret" not in str(incident)


@pytest.mark.asyncio
async def test_incident_manifest_linked_in_memo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch, patched=True)
    workflow = MoonMindRunWorkflow()
    workflow._incident_reconstruction_ref = "artifact://incident/manifest-1"

    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        run_module.workflow,
        "upsert_memo",
        lambda memo: captured.update(memo),
    )

    workflow._update_memo()

    assert captured["incident_reconstruction_ref"] == "artifact://incident/manifest-1"


def test_step_manifest_stamps_trace_ref(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch, patched=True)
    ref = run_module.build_incident_trace_ref(
        workflow_id="wf-run-1",
        run_id="run-1",
        logical_step_id="publish",
        execution_ordinal=2,
    )
    payload = ref.model_dump(by_alias=True, mode="json", exclude_none=True)
    assert payload["traceId"] == derive_incident_trace_id("wf-run-1", "run-1")
    assert payload["logicalStepId"] == "publish"
    assert payload["executionOrdinal"] == 2


def test_capture_incident_failure_evidence_drops_raw_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch, patched=True)
    workflow = MoonMindRunWorkflow()
    workflow._capture_incident_failure_evidence(
        {
            "providerFailure": {
                "providerErrorClass": "rate_limit",
                "providerErrorCode": "429",
                "reason": "raw provider traceback with secret",
                "sanitizedSummary": "Provider rate limit reached.",
            },
            "turnMetadata": {"totalTokens": 321, "costEstimateUsd": 0.02},
        }
    )
    assert workflow._provider_failure_envelope is not None
    assert "reason" not in workflow._provider_failure_envelope
    assert workflow._provider_failure_envelope["providerErrorClass"] == "rate_limit"
    assert workflow._observed_cost == {"totalTokens": 321, "costEstimateUsd": 0.02}


def test_capture_incident_failure_evidence_first_failure_wins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch, patched=True)
    workflow = MoonMindRunWorkflow()
    workflow._capture_incident_failure_evidence(
        {"providerFailure": {"providerErrorClass": "auth"}}
    )
    workflow._capture_incident_failure_evidence(
        {"providerFailure": {"providerErrorClass": "rate_limit"}}
    )
    assert workflow._provider_failure_envelope["providerErrorClass"] == "auth"


def test_capture_incident_failure_evidence_patch_gated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch, patched=False)
    workflow = MoonMindRunWorkflow()
    workflow._capture_incident_failure_evidence(
        {"providerFailure": {"providerErrorClass": "auth"}}
    )
    assert workflow._provider_failure_envelope is None
    assert workflow._observed_cost is None
