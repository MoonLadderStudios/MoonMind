from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from moonmind.schemas.temporal_models import STEP_ATTEMPT_MANIFEST_CONTENT_TYPE
from moonmind.workflows.temporal.workflows import run as run_module
from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow

def _configure_workflow_runtime(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    workflow_info = SimpleNamespace(
        namespace="default",
        workflow_id="wf-run-1",
        run_id="run-1",
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
    memo_updates: list[dict] = []
    search_updates: list[object] = []
    monkeypatch.setattr(run_module.workflow, "info", lambda: workflow_info)
    monkeypatch.setattr(run_module.workflow, "logger", logger)
    monkeypatch.setattr(
        run_module.workflow,
        "now",
        lambda: datetime(2026, 4, 7, 12, 0, tzinfo=UTC),
    )
    monkeypatch.setattr(run_module.workflow, "upsert_memo", memo_updates.append)
    monkeypatch.setattr(
        run_module.workflow,
        "upsert_search_attributes",
        search_updates.append,
    )
    monkeypatch.setattr(run_module.workflow, "patched", lambda _patch_id: False)
    return memo_updates

def _ordered_nodes() -> list[dict]:
    return [
        {
            "id": "prepare",
            "tool": {"type": "skill", "name": "repo.prepare", "version": "1"},
            "inputs": {"title": "Prepare workspace"},
        },
        {
            "id": "run-tests",
            "tool": {"type": "skill", "name": "repo.run_tests", "version": "1"},
            "inputs": {"title": "Run tests"},
        },
    ]

def _dependency_map() -> dict[str, list[str]]:
    return {"prepare": [], "run-tests": ["prepare"]}

def _approval_policy_plan_payload() -> dict[str, Any]:
    return {
        "plan_version": "1.0",
        "metadata": {
            "title": "Approval policy plan",
            "created_at": "2026-04-08T00:00:00Z",
            "registry_snapshot": {
                "digest": "reg:sha256:" + ("a" * 64),
                "artifact_ref": "artifact://registry/1",
            },
        },
        "policy": {
            "failure_mode": "FAIL_FAST",
            "max_concurrency": 1,
            "approval_policy": {
                "enabled": True,
                "max_review_attempts": 1,
                "reviewer_model": "default",
                "review_timeout_seconds": 120,
                "skip_tool_types": [],
            },
        },
        "nodes": [
            {
                "id": "apply-patch",
                "tool": {
                    "type": "skill",
                    "name": "repo.apply_patch",
                    "version": "1.0.0",
                },
                "inputs": {"instruction": "Apply the patch"},
                "options": {},
            }
        ],
        "edges": [],
    }

def _registry_payload() -> dict[str, Any]:
    return {
        "skills": [
            {
                "name": "repo.apply_patch",
                "version": "1.0.0",
                "description": "Apply patch",
                "inputs": {"schema": {"type": "object"}},
                "outputs": {"schema": {"type": "object"}},
                "executor": {
                    "activity_type": "mm.skill.execute",
                    "selector": {"mode": "by_capability"},
                },
                "requirements": {"capabilities": ["sandbox"]},
                "policies": {
                    "timeouts": {
                        "start_to_close_seconds": 1800,
                        "schedule_to_close_seconds": 3600,
                    },
                    "retries": {"max_attempts": 1},
                },
            }
        ]
    }

def test_run_initializes_latest_run_step_ledger(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindRunWorkflow()
    workflow._owner_id = "owner-1"
    now = datetime(2026, 4, 7, 12, 0, tzinfo=UTC)

    workflow._initialize_step_ledger(
        ordered_nodes=_ordered_nodes(),
        dependency_map=_dependency_map(),
        updated_at=now,
    )

    ledger = workflow.get_step_ledger()
    progress = workflow.get_progress()

    assert ledger["workflowId"] == "wf-run-1"
    assert ledger["runId"] == "run-1"
    assert ledger["runScope"] == "latest"
    assert [step["logicalStepId"] for step in ledger["steps"]] == [
        "prepare",
        "run-tests",
    ]
    assert ledger["steps"][0]["status"] == "ready"
    assert ledger["steps"][1]["status"] == "pending"
    assert progress["total"] == 2
    assert progress["ready"] == 1
    assert progress["pending"] == 1

def test_run_progress_query_exposes_current_run_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindRunWorkflow()
    now = datetime(2026, 4, 7, 12, 0, tzinfo=UTC)

    workflow._initialize_step_ledger(
        ordered_nodes=_ordered_nodes(),
        dependency_map=_dependency_map(),
        updated_at=now,
    )

    progress = workflow.get_progress()

    assert progress["runId"] == "run-1"
    assert progress["total"] == 2

def test_first_step_running_stamps_mm_started_at_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_mark_step_running`` is the closest existing semantic boundary for
    "real work began" — when a logical step first transitions to running, the
    workflow must stamp ``mm_started_at``. Subsequent step transitions and
    retries must not move the timestamp."""
    _configure_workflow_runtime(monkeypatch)
    monkeypatch.setattr(run_module.workflow, "patched", lambda _patch_id: True)
    upserts: list[object] = []
    monkeypatch.setattr(
        run_module.workflow, "upsert_search_attributes", upserts.append
    )
    workflow = MoonMindRunWorkflow()
    first_now = datetime(2026, 4, 7, 12, 0, tzinfo=UTC)
    later = datetime(2026, 4, 7, 12, 1, tzinfo=UTC)

    workflow._initialize_step_ledger(
        ordered_nodes=_ordered_nodes(),
        dependency_map=_dependency_map(),
        updated_at=first_now,
    )
    upserts.clear()

    workflow._mark_step_running(
        "prepare", updated_at=first_now, summary="Preparing"
    )
    assert workflow._started_at == first_now

    # Find the mm_started_at upsert. _set_state-driven upserts do not include
    # the semantic timestamp; the dedicated upsert from _mark_real_work_started
    # contains exactly one pair carrying the value.
    started_at_upserts = [
        pairs
        for pairs in upserts
        if any(
            getattr(p.key, "name", None) == run_module.MM_STARTED_AT_SEARCH_ATTRIBUTE
            for p in (pairs if isinstance(pairs, list) else [])
        )
    ]
    assert len(started_at_upserts) == 1

    workflow._mark_step_terminal(
        "prepare", status="succeeded", updated_at=first_now, summary="Done"
    )
    workflow._refresh_step_readiness(updated_at=later)
    workflow._mark_step_running(
        "run-tests", updated_at=later, summary="Running tests"
    )
    # mm_started_at is set exactly once; later step transitions never overwrite it.
    assert workflow._started_at == first_now
    started_at_upserts = [
        pairs
        for pairs in upserts
        if any(
            getattr(p.key, "name", None) == run_module.MM_STARTED_AT_SEARCH_ATTRIBUTE
            for p in (pairs if isinstance(pairs, list) else [])
        )
    ]
    assert len(started_at_upserts) == 1

def test_run_tracks_status_transitions_and_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindRunWorkflow()
    now = datetime(2026, 4, 7, 12, 0, tzinfo=UTC)

    workflow._initialize_step_ledger(
        ordered_nodes=_ordered_nodes(),
        dependency_map=_dependency_map(),
        updated_at=now,
    )
    workflow._mark_step_running("prepare", updated_at=now, summary="Preparing workspace")
    workflow._mark_step_terminal(
        "prepare",
        status="succeeded",
        updated_at=now,
        summary="Workspace ready",
    )
    workflow._refresh_step_readiness(updated_at=now)
    workflow._mark_step_running("run-tests", updated_at=now, summary="Running tests")
    workflow._mark_step_waiting(
        "run-tests",
        status="reviewing",
        updated_at=now,
        waiting_reason="Awaiting structured review result",
        summary="Structured review in progress",
    )
    workflow._mark_step_terminal(
        "run-tests",
        status="failed",
        updated_at=now,
        summary="Tests failed",
        last_error="pytest failed",
    )
    workflow._mark_step_running("run-tests", updated_at=now, summary="Retrying tests")
    workflow._mark_step_waiting(
        "run-tests",
        status="awaiting_external",
        updated_at=now,
        waiting_reason="Awaiting child workflow progress",
        summary="Child runtime launched",
    )
    workflow._mark_step_terminal(
        "run-tests",
        status="canceled",
        updated_at=now,
        summary="Canceled by operator",
    )

    ledger = workflow.get_step_ledger()
    step = ledger["steps"][1]
    progress = workflow.get_progress()

    assert step["attempt"] == 2
    assert step["status"] == "canceled"
    assert step["waitingReason"] is None
    assert step["lastError"] == "pytest failed"
    assert progress["succeeded"] == 1
    assert progress["canceled"] == 1

def test_run_terminal_success_clears_previous_last_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindRunWorkflow()
    now = datetime(2026, 4, 7, 12, 0, tzinfo=UTC)

    workflow._initialize_step_ledger(
        ordered_nodes=_ordered_nodes(),
        dependency_map=_dependency_map(),
        updated_at=now,
    )
    workflow._mark_step_running("prepare", updated_at=now, summary="Preparing workspace")
    workflow._mark_step_terminal(
        "prepare",
        status="failed",
        updated_at=now,
        summary="Workspace failed",
        last_error="pytest failed",
    )
    workflow._mark_step_running("prepare", updated_at=now, summary="Retrying workspace")
    workflow._mark_step_terminal(
        "prepare",
        status="succeeded",
        updated_at=now,
        summary="Workspace ready",
        last_error=None,
    )

    step = workflow.get_step_ledger()["steps"][0]

    assert step["status"] == "succeeded"
    assert step["lastError"] is None

def test_run_missing_step_ledger_updates_do_not_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindRunWorkflow()
    now = datetime(2026, 4, 7, 12, 0, tzinfo=UTC)

    workflow._initialize_step_ledger(
        ordered_nodes=_ordered_nodes(),
        dependency_map=_dependency_map(),
        updated_at=now,
    )

    workflow._mark_step_running("missing-step", updated_at=now, summary="Ignored")
    workflow._mark_step_waiting(
        "missing-step",
        status="reviewing",
        updated_at=now,
        waiting_reason="Ignored",
        summary="Ignored",
    )
    workflow._mark_step_terminal(
        "missing-step",
        status="failed",
        updated_at=now,
        summary="Ignored",
        last_error="ignored",
    )

    progress = workflow.get_progress()
    assert progress["ready"] == 1
    assert progress["pending"] == 1
    assert progress["running"] == 0

def test_plan_dependency_map_rewrites_bundled_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindRunWorkflow()
    bundle_id = "wf-run-1:jules-bundle:step-1:step-2"

    dependency_map = workflow._plan_dependency_map(
        ordered_nodes=[
            {
                "id": "prepare",
                "tool": {"type": "skill", "name": "repo.prepare", "version": "1"},
            },
            {
                "id": bundle_id,
                "tool": {"type": "agent_runtime", "name": "jules", "version": ""},
                "inputs": {"bundledNodeIds": ["step-1", "step-2"]},
            },
            {
                "id": "publish",
                "tool": {"type": "skill", "name": "repo.publish", "version": "1"},
            },
        ],
        edges=(
            SimpleNamespace(from_node="prepare", to_node="step-1"),
            SimpleNamespace(from_node="step-1", to_node="step-2"),
            SimpleNamespace(from_node="step-2", to_node="publish"),
        ),
    )

    assert dependency_map == {
        "prepare": [],
        bundle_id: ["prepare"],
        "publish": [bundle_id],
    }

def test_run_queries_remain_available_after_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindRunWorkflow()
    now = datetime(2026, 4, 7, 12, 0, tzinfo=UTC)

    workflow._initialize_step_ledger(
        ordered_nodes=_ordered_nodes(),
        dependency_map=_dependency_map(),
        updated_at=now,
    )
    workflow._mark_step_running("prepare", updated_at=now, summary="Preparing workspace")
    workflow._mark_step_terminal(
        "prepare",
        status="skipped",
        updated_at=now,
        summary="Skipped after reuse",
    )
    workflow._state = run_module.STATE_COMPLETED

    assert workflow.get_progress()["skipped"] == 1
    assert workflow.get_step_ledger()["steps"][0]["status"] == "skipped"

def test_run_memo_updates_remain_compact(monkeypatch: pytest.MonkeyPatch) -> None:
    memo_updates = _configure_workflow_runtime(monkeypatch)
    monkeypatch.setattr(run_module.workflow, "patched", lambda _patch_id: True)
    workflow = MoonMindRunWorkflow()
    now = datetime(2026, 4, 7, 12, 0, tzinfo=UTC)

    workflow._title = "Ledger run"
    workflow._initialize_step_ledger(
        ordered_nodes=_ordered_nodes(),
        dependency_map=_dependency_map(),
        updated_at=now,
    )
    workflow._summary = "Executing step ledger tests."
    workflow._update_memo()
    workflow._update_search_attributes()

    latest_memo = next(memo for memo in reversed(memo_updates) if "title" in memo)
    assert latest_memo["title"] == "Ledger run"
    assert latest_memo["summary"] == "Executing step ledger tests."
    assert "steps" not in latest_memo
    assert "progress" not in latest_memo
    assert "checks" not in latest_memo

def test_run_memo_includes_current_step_order_when_step_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    memo_updates = _configure_workflow_runtime(monkeypatch)
    monkeypatch.setattr(run_module.workflow, "patched", lambda _patch_id: True)
    workflow = MoonMindRunWorkflow()

    workflow._title = "Ledger run"
    workflow._summary = "Executing plan step 2/3"
    workflow._update_memo()
    assert "mm_current_step_order" not in memo_updates[-1]

    workflow._step_count = 2
    workflow._update_memo()
    assert memo_updates[-1]["mm_current_step_order"] == 2

def test_run_memo_surfaces_runtime_and_skill_visibility(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    memo_updates = _configure_workflow_runtime(monkeypatch)
    monkeypatch.setattr(run_module.workflow, "patched", lambda _patch_id: True)
    workflow = MoonMindRunWorkflow()

    workflow._title = "Resolve PR #1633"
    workflow._summary = "Execution initialized."
    workflow._target_runtime = "codex_cli"
    workflow._target_skill = "pr-resolver"
    workflow._update_memo()

    assert memo_updates[-1]["targetRuntime"] == "codex_cli"
    assert memo_updates[-1]["targetSkill"] == "pr-resolver"

def test_run_groups_child_lineage_and_evidence_into_step_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindRunWorkflow()
    now = datetime(2026, 4, 7, 12, 0, tzinfo=UTC)

    workflow._initialize_step_ledger(
        ordered_nodes=[
            {
                "id": "delegate-agent",
                "tool": {"type": "agent_runtime", "name": "codex", "version": ""},
                "inputs": {"title": "Delegate agent"},
            }
        ],
        dependency_map={"delegate-agent": []},
        updated_at=now,
    )
    workflow._mark_step_running(
        "delegate-agent",
        updated_at=now,
        summary="Launching child runtime",
    )
    workflow._record_step_result_evidence(
        "delegate-agent",
        execution_result={
            "status": "COMPLETED",
            "outputs": {
                "childWorkflowId": "wf-child-1",
                "childRunId": "run-child-1",
                "taskRunId": "550e8400-e29b-41d4-a716-446655440000",
                "outputSummaryRef": "art_summary_1",
                "outputAgentResultRef": "art_primary_1",
                "stdoutArtifactRef": "art_stdout_1",
                "stderrArtifactRef": "art_stderr_1",
                "mergedLogArtifactRef": "art_merged_1",
                "diagnosticsRef": "art_diag_1",
                "providerSnapshotRef": "art_provider_1",
                "outputRefs": [
                    "art_stdout_1",
                    "art_stderr_1",
                    "art_diag_1",
                    "art_primary_1",
                ],
            },
        },
        updated_at=now,
    )

    step = workflow.get_step_ledger()["steps"][0]

    assert step["refs"] == {
        "childWorkflowId": "wf-child-1",
        "childRunId": "run-child-1",
        "taskRunId": "550e8400-e29b-41d4-a716-446655440000",
        "latestAttemptManifestRef": None,
        "attemptManifestRefs": [],
    }
    assert step["artifacts"] == {
        "outputSummary": "art_summary_1",
        "outputPrimary": "art_primary_1",
        "runtimeStdout": "art_stdout_1",
        "runtimeStderr": "art_stderr_1",
        "runtimeMergedLogs": "art_merged_1",
        "runtimeDiagnostics": "art_diag_1",
        "providerSnapshot": "art_provider_1",
        "attemptManifestRef": None,
        "attemptManifestRefs": [],
    }

def test_run_waiting_state_captures_child_workflow_lineage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindRunWorkflow()
    now = datetime(2026, 4, 7, 12, 0, tzinfo=UTC)

    workflow._initialize_step_ledger(
        ordered_nodes=[
            {
                "id": "delegate-agent",
                "tool": {"type": "agent_runtime", "name": "codex", "version": ""},
                "inputs": {"title": "Delegate agent"},
            }
        ],
        dependency_map={"delegate-agent": []},
        updated_at=now,
    )
    workflow._mark_step_running(
        "delegate-agent",
        updated_at=now,
        summary="Launching child runtime",
    )
    workflow._mark_step_waiting(
        "delegate-agent",
        status="awaiting_external",
        updated_at=now,
        waiting_reason="Awaiting child workflow progress",
        summary="Child runtime launched",
        refs={"childWorkflowId": "wf-child-1"},
    )

    step = workflow.get_step_ledger()["steps"][0]

    assert step["status"] == "awaiting_external"
    assert step["refs"] == {
        "childWorkflowId": "wf-child-1",
        "childRunId": None,
        "taskRunId": None,
        "latestAttemptManifestRef": None,
        "attemptManifestRefs": [],
    }


@pytest.mark.asyncio
async def test_run_records_step_attempt_manifest_ref_when_work_begins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindRunWorkflow()
    now = datetime(2026, 4, 7, 12, 0, tzinfo=UTC)
    writes: list[dict[str, Any]] = []

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
                "id": "delegate-agent",
                "tool": {"type": "agent_runtime", "name": "codex", "version": ""},
                "inputs": {"title": "Delegate agent"},
            }
        ],
        dependency_map={"delegate-agent": []},
        updated_at=now,
    )

    workflow._mark_step_running(
        "delegate-agent",
        updated_at=now,
        summary="Launching child runtime",
    )
    await workflow._record_step_attempt_manifest_start(
        "delegate-agent",
        updated_at=now,
        reason="initial_execution",
    )
    workflow._record_step_result_evidence(
        "delegate-agent",
        execution_result={
            "status": "FAILED",
            "outputs": {
                "childWorkflowId": "wf-child-1",
                "childRunId": "run-child-1",
                "outputSummaryRef": "artifact://summary/attempt-1",
            },
        },
        updated_at=now,
    )
    workflow._mark_step_terminal(
        "delegate-agent",
        status="failed",
        updated_at=now,
        summary="Runtime failed",
    )
    workflow._mark_step_running(
        "delegate-agent",
        updated_at=now,
        summary="Retrying child runtime",
    )
    await workflow._record_step_attempt_manifest_start(
        "delegate-agent",
        updated_at=now,
        reason="runtime_recovered",
    )

    step = workflow.get_step_ledger()["steps"][0]
    assert step["attempt"] == 2
    assert step["refs"]["latestAttemptManifestRef"] == "artifact-attempt-2"
    assert step["refs"]["attemptManifestRefs"] == [
        "artifact-attempt-1",
        "artifact-attempt-2",
    ]
    assert writes[0]["content_type"] == (
        "application/vnd.moonmind.step-attempt+json;version=1"
    )
    assert writes[0]["payload"]["stepAttemptId"] == (
        "wf-run-1:run-1:delegate-agent:attempt:1"
    )
    assert writes[0]["metadata_json"]["idempotencyKey"] == (
        "wf-run-1:run-1:delegate-agent:1:manifest"
    )
    assert writes[0]["payload"]["reason"] == "initial_execution"
    assert writes[0]["payload"]["execution"] == {}
    assert writes[0]["payload"]["outputs"] == {}
    assert writes[1]["payload"]["reason"] == "runtime_recovered"
    assert writes[1]["payload"]["execution"] == {}
    assert writes[1]["payload"]["status"] == "blocked"
    assert writes[1]["payload"]["terminalDisposition"] == "blocked"
    assert writes[1]["payload"]["outputs"] == {
        "summary": "Workspace policy rejected before launch."
    }
    assert writes[1]["payload"]["workspace"]["policy"] == (
        "continue_from_previous_attempt"
    )
    assert writes[1]["payload"]["workspace"]["sourceAttempt"] == {
        "workflowId": "wf-run-1",
        "runId": "run-1",
        "logicalStepId": "delegate-agent",
        "attempt": 1,
    }
    assert "lineage" not in writes[1]["payload"]


@pytest.mark.asyncio
async def test_run_records_terminal_step_attempt_manifest_with_result_refs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindRunWorkflow()
    now = datetime(2026, 4, 7, 12, 0, tzinfo=UTC)
    writes: list[dict[str, Any]] = []

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
                "id": "run-tests",
                "tool": {"type": "skill", "name": "repo.run_tests", "version": "1"},
                "inputs": {"title": "Run tests"},
            }
        ],
        dependency_map={"run-tests": []},
        updated_at=now,
    )

    workflow._mark_step_running("run-tests", updated_at=now, summary="Run tests")
    await workflow._record_step_attempt_manifest_start(
        "run-tests",
        updated_at=now,
        reason="initial_execution",
    )
    workflow._record_step_result_evidence(
        "run-tests",
        execution_result={
            "status": "COMPLETED",
            "outputs": {
                "taskRunId": "task-run-1",
                "outputSummaryRef": "artifact://summary/attempt-1",
                "stdoutArtifactRef": "artifact://stdout/attempt-1",
                "diagnosticsRef": "artifact://diagnostics/attempt-1",
            },
        },
        updated_at=now,
    )
    workflow._mark_step_terminal(
        "run-tests",
        status="succeeded",
        updated_at=now,
        summary="Done",
    )
    await workflow._record_step_attempt_manifest_terminal(
        "run-tests",
        updated_at=now,
        reason="initial_execution",
        status="succeeded",
        terminal_disposition="accepted",
    )

    assert writes[0]["payload"]["status"] == "running"
    assert writes[0]["payload"]["execution"] == {}
    assert writes[0]["payload"]["outputs"] == {}
    assert writes[1]["payload"]["status"] == "succeeded"
    assert writes[1]["payload"]["terminalDisposition"] == "accepted"
    assert writes[1]["payload"]["execution"] == {
        "taskRunId": "task-run-1",
        "diagnosticsRef": "artifact://diagnostics/attempt-1",
    }
    assert writes[1]["payload"]["outputs"] == {
        "summaryRef": "artifact://summary/attempt-1",
        "stdoutRef": "artifact://stdout/attempt-1",
    }


@pytest.mark.asyncio
async def test_write_step_attempt_manifest_requires_real_artifact_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindRunWorkflow()
    workflow._owner_id = "owner-1"

    async def fake_execute_activity(
        activity_type: str,
        _payload: Any,
        **_kwargs: Any,
    ) -> tuple[dict[str, str], dict[str, str]]:
        assert activity_type == "artifact.create"
        return ({}, {"upload_url": "unused"})

    async def fake_execute_typed_activity(
        activity_type: str,
        _payload: Any,
        **_kwargs: Any,
    ) -> dict[str, bool]:
        raise AssertionError(f"unexpected typed activity: {activity_type}")

    monkeypatch.setattr(run_module.workflow, "execute_activity", fake_execute_activity)
    monkeypatch.setattr(run_module, "execute_typed_activity", fake_execute_typed_activity)

    with pytest.raises(
        ValueError,
        match="artifact.create returned no artifact_id",
    ):
        await workflow._write_json_artifact(
            name="reports/step_attempts/run-tests_attempt_1.json",
            payload={"schemaVersion": "v1"},
            content_type=STEP_ATTEMPT_MANIFEST_CONTENT_TYPE,
        )


def test_run_uses_deterministic_output_primary_fallback_for_generic_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindRunWorkflow()
    now = datetime(2026, 4, 7, 12, 0, tzinfo=UTC)

    workflow._initialize_step_ledger(
        ordered_nodes=[
            {
                "id": "run-tests",
                "tool": {"type": "skill", "name": "repo.run_tests", "version": "1"},
                "inputs": {"title": "Run tests"},
            }
        ],
        dependency_map={"run-tests": []},
        updated_at=now,
    )
    workflow._record_step_result_evidence(
        "run-tests",
        execution_result={
            "status": "FAILED",
            "outputs": {
                "outputRefs": [
                    "art_stdout_1",
                    "art_primary_1",
                    "art_secondary_1",
                ],
                "outputAgentResultRef": "art_agent_result_1",
                "stdoutArtifactRef": "art_stdout_1",
                "diagnosticsRef": "art_diag_1",
            },
        },
        updated_at=now,
    )

    step = workflow.get_step_ledger()["steps"][0]

    assert step["artifacts"]["outputPrimary"] == "art_primary_1"
    assert step["artifacts"]["runtimeStdout"] == "art_stdout_1"
    assert step["artifacts"]["runtimeDiagnostics"] == "art_diag_1"

def test_run_projects_workload_artifacts_and_metadata_from_tool_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindRunWorkflow()
    now = datetime(2026, 4, 7, 12, 0, tzinfo=UTC)

    workflow._initialize_step_ledger(
        ordered_nodes=[
            {
                "id": "workload-step",
                "tool": {
                    "type": "skill",
                    "name": "container.run_workload",
                    "version": "1",
                },
                "inputs": {"title": "Run workload"},
            }
        ],
        dependency_map={"workload-step": []},
        updated_at=now,
    )
    workflow._record_step_result_evidence(
        "workload-step",
        execution_result={
            "status": "FAILED",
            "outputs": {
                "stdoutRef": "art_stdout_1",
                "stderrRef": "art_stderr_1",
                "diagnosticsRef": "art_diag_1",
                "outputRefs": {
                    "runtime.stdout": "art_stdout_1",
                    "runtime.stderr": "art_stderr_1",
                    "runtime.diagnostics": "art_diag_1",
                    "output.summary": "art_summary_1",
                    "test.report": "art_report_1",
                },
                "workloadMetadata": {
                    "taskRunId": "wf-1",
                    "stepId": "workload-step",
                    "attempt": 1,
                    "toolName": "container.run_workload",
                    "profileId": "local-python",
                    "imageRef": "python:3.12-slim",
                    "status": "failed",
                    "exitCode": 7,
                    "durationSeconds": 4.25,
                    "sessionContext": {
                        "sessionId": "session-1",
                        "sessionEpoch": 3,
                    },
                },
            },
        },
        updated_at=now,
    )

    step = workflow.get_step_ledger()["steps"][0]

    assert step["refs"]["taskRunId"] == "wf-1"
    assert step["artifacts"]["runtimeStdout"] == "art_stdout_1"
    assert step["artifacts"]["runtimeStderr"] == "art_stderr_1"
    assert step["artifacts"]["runtimeDiagnostics"] == "art_diag_1"
    assert step["artifacts"]["outputSummary"] == "art_summary_1"
    assert step["artifacts"]["outputPrimary"] == "art_report_1"
    assert step["workload"]["profileId"] == "local-python"
    assert step["workload"]["imageRef"] == "python:3.12-slim"
    assert step["workload"]["sessionContext"] == {
        "sessionId": "session-1",
        "sessionEpoch": 3,
    }

def test_run_accepts_tuple_output_refs_and_ignores_string_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindRunWorkflow()
    now = datetime(2026, 4, 7, 12, 0, tzinfo=UTC)

    workflow._initialize_step_ledger(
        ordered_nodes=[
            {
                "id": "run-tests",
                "tool": {"type": "skill", "name": "repo.run_tests", "version": "1"},
                "inputs": {"title": "Run tests"},
            }
        ],
        dependency_map={"run-tests": []},
        updated_at=now,
    )
    workflow._record_step_result_evidence(
        "run-tests",
        execution_result={
            "status": "FAILED",
            "outputs": {
                "outputRefs": "art_primary_1",
                "output_refs": (
                    "art_stdout_1",
                    "art_primary_1",
                    "",
                    7,
                ),
                "stdoutArtifactRef": "art_stdout_1",
            },
        },
        updated_at=now,
    )

    step = workflow.get_step_ledger()["steps"][0]

    assert step["artifacts"]["outputPrimary"] == "art_primary_1"
    assert step["artifacts"]["runtimeStdout"] == "art_stdout_1"


def test_run_records_prepared_refs_and_idempotent_checkpoint_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindRunWorkflow()
    now = datetime(2026, 4, 7, 12, 30, tzinfo=UTC)
    task_payload = {
        "inputAttachments": [{"artifactId": "objective-artifact"}],
        "steps": [
            {
                "id": "implement",
                "inputAttachments": [{"artifactId": "step-artifact"}],
            }
        ],
    }

    workflow._initialize_step_ledger(
        ordered_nodes=[
            {
                "id": "implement",
                "tool": {"type": "agent_runtime", "name": "codex"},
                "inputs": {"title": "Implement"},
            }
        ],
        dependency_map={"implement": []},
        updated_at=now,
    )
    workflow._capture_prepared_input_refs({"task": task_payload})
    workflow._record_step_result_evidence(
        "implement",
        execution_result={
            "status": "COMPLETED",
            "outputs": {
                "outputRefs": ["artifact://output"],
                "latestCheckpointRef": "artifact://runtime/checkpoint",
            },
        },
        updated_at=now,
    )
    workflow._mark_step_terminal(
        "implement",
        status="succeeded",
        updated_at=now,
        summary="Implemented",
    )

    first_ref = workflow._record_step_checkpoint_evidence(
        "implement",
        updated_at=now,
    )
    second_ref = workflow._record_step_checkpoint_evidence(
        "implement",
        updated_at=now,
    )

    step = workflow.get_step_ledger()["steps"][0]
    assert workflow.get_step_ledger()["preparedArtifactRefs"] == [
        "prepared-context://objective/objective-artifact",
        "artifact://objective-artifact",
        "prepared-context://steps/implement/step-artifact",
        "artifact://step-artifact",
    ]
    assert first_ref == second_ref == "artifact://runtime/checkpoint"
    assert step["stateCheckpointRef"] == "artifact://runtime/checkpoint"
    assert step["resumePreservation"]["eligible"] is True
    assert step["resumePreservation"]["reason"] == "complete"


def test_run_marks_completed_step_without_checkpoint_ineligible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindRunWorkflow()
    now = datetime(2026, 4, 7, 12, 35, tzinfo=UTC)

    workflow._initialize_step_ledger(
        ordered_nodes=[{"id": "plan", "inputs": {"title": "Plan"}}],
        dependency_map={"plan": []},
        updated_at=now,
    )
    workflow._mark_step_terminal(
        "plan",
        status="succeeded",
        updated_at=now,
        summary="Planned",
    )
    workflow._record_step_result_evidence(
        "plan",
        execution_result={
            "status": "COMPLETED",
            "outputs": {
                "outputPrimaryRef": "artifact://plan-output",
            },
        },
        updated_at=now,
    )
    workflow._record_step_checkpoint_evidence("plan", updated_at=now)

    step = workflow.get_step_ledger()["steps"][0]
    assert step["resumePreservation"]["eligible"] is False
    assert step["resumePreservation"]["reason"] == "missing_state_checkpoint"
    assert step.get("stateCheckpointRef") is None


def test_run_clears_stale_checkpoint_ref_before_successful_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindRunWorkflow()
    now = datetime(2026, 4, 7, 12, 36, tzinfo=UTC)

    workflow._initialize_step_ledger(
        ordered_nodes=[{"id": "implement", "inputs": {"title": "Implement"}}],
        dependency_map={"implement": []},
        updated_at=now,
    )
    workflow._mark_step_running("implement", updated_at=now, summary="Implementing")
    workflow._record_step_result_evidence(
        "implement",
        execution_result={
            "status": "FAILED",
            "outputs": {
                "outputPrimaryRef": "artifact://failed-output",
                "stateCheckpointRef": "artifact://stale-checkpoint",
            },
        },
        updated_at=now,
    )
    workflow._mark_step_terminal(
        "implement",
        status="failed",
        updated_at=now,
        summary="Failed",
        last_error="execution_error",
    )

    workflow._mark_step_running("implement", updated_at=now, summary="Retrying")
    workflow._record_step_result_evidence(
        "implement",
        execution_result={
            "status": "COMPLETED",
            "outputs": {
                "outputPrimaryRef": "artifact://successful-output",
            },
        },
        updated_at=now,
    )
    workflow._mark_step_terminal(
        "implement",
        status="succeeded",
        updated_at=now,
        summary="Implemented",
    )
    workflow._record_step_checkpoint_evidence("implement", updated_at=now)

    step = workflow.get_step_ledger()["steps"][0]
    assert step.get("stateCheckpointRef") is None
    assert step["artifacts"]["outputPrimary"] == "artifact://successful-output"
    assert step["resumePreservation"]["eligible"] is False
    assert step["resumePreservation"]["reason"] == "missing_state_checkpoint"


def test_run_reads_nested_workload_metadata_from_legacy_workload_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindRunWorkflow()
    now = datetime(2026, 4, 7, 12, 0, tzinfo=UTC)

    workflow._initialize_step_ledger(
        ordered_nodes=[
            {
                "id": "workload-step",
                "tool": {"type": "skill", "name": "container.run_workload"},
                "inputs": {"title": "Run workload"},
            }
        ],
        dependency_map={"workload-step": []},
        updated_at=now,
    )
    workflow._record_step_result_evidence(
        "workload-step",
        execution_result={
            "status": "COMPLETED",
            "outputs": {
                "workloadResult": {
                    "metadata": {
                        "stdout": "large bounded stdout must not be ledger metadata",
                        "workload": {
                            "taskRunId": "wf-legacy",
                            "stepId": "workload-step",
                            "profileId": "local-python",
                        },
                    }
                }
            },
        },
        updated_at=now,
    )

    step = workflow.get_step_ledger()["steps"][0]

    assert step["refs"]["taskRunId"] == "wf-legacy"
    assert step["workload"]["taskRunId"] == "wf-legacy"
    assert step["workload"]["stepId"] == "workload-step"
    assert step["workload"]["profileId"] == "local-python"
    assert "stdout" not in step["workload"]

@pytest.mark.asyncio
async def test_run_execution_stage_marks_step_reviewing_and_records_passed_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindRunWorkflow()
    workflow._owner_id = "owner-1"
    review_snapshots: list[dict[str, Any]] = []
    written_review_payloads: list[dict[str, Any]] = []
    review_artifact_ids = iter(("art_review_1",))
    step_attempt_artifact_ids = iter(
        ("art_attempt_1", "art_attempt_1_terminal")
    )

    async def fake_execute_activity(
        activity_type: str,
        payload: Any,
        **_kwargs: Any,
    ) -> Any:
        if activity_type == "provider_profile.list":
            return {"profiles": []}
        if activity_type == "artifact.create":
            if str(payload.get("name") or "").startswith("reports/step_attempt"):
                return (
                    {"artifact_id": next(step_attempt_artifact_ids)},
                    {"upload_url": "unused"},
                )
            return ({"artifact_id": next(review_artifact_ids)}, {"upload_url": "unused"})
        if activity_type == "mm.skill.execute":
            return {
                "status": "COMPLETED",
                "summary": "Patch applied cleanly",
                "outputs": {"outputSummaryRef": "art_summary_1"},
            }
        if activity_type == "step.review":
            step = workflow.get_step_ledger()["steps"][0]
            review_snapshots.append(step)
            return {
                "verdict": "PASS",
                "confidence": 0.91,
                "feedback": None,
                "issues": [],
            }
        raise AssertionError(f"unexpected activity: {activity_type}")

    async def fake_execute_typed_activity(
        activity_type: str,
        payload: Any,
        **_kwargs: Any,
    ) -> Any:
        if activity_type == "artifact.read":
            artifact_ref = getattr(payload, "artifact_ref", None)
            if artifact_ref == "art_plan_1":
                return json.dumps(_approval_policy_plan_payload()).encode("utf-8")
            if artifact_ref == "artifact://registry/1":
                return json.dumps(_registry_payload()).encode("utf-8")
        if activity_type == "artifact.write_complete":
            if getattr(payload, "content_type", "") == (
                "application/vnd.moonmind.step-attempt+json;version=1"
            ):
                return {"ok": True}
            written_review_payloads.append(json.loads(payload.payload.decode("utf-8")))
            return {"ok": True}
        raise AssertionError(f"unexpected typed activity: {activity_type}")

    workflow_info = SimpleNamespace(
        namespace="default",
        workflow_id="wf-run-review-1",
        run_id="run-review-1",
        task_queue="mm.workflow",
        search_attributes={"mm_owner_type": ["user"], "mm_owner_id": ["owner-1"]},
    )
    monkeypatch.setattr(run_module.workflow, "info", lambda: workflow_info)
    monkeypatch.setattr(run_module.workflow, "upsert_memo", lambda _memo: None)
    monkeypatch.setattr(
        run_module.workflow,
        "upsert_search_attributes",
        lambda _attributes: None,
    )
    monkeypatch.setattr(
        run_module.workflow,
        "execute_activity",
        fake_execute_activity,
    )
    monkeypatch.setattr(
        run_module,
        "execute_typed_activity",
        fake_execute_typed_activity,
    )
    monkeypatch.setattr(run_module.workflow, "patched", lambda _patch_id: True)

    await workflow._run_execution_stage(
        parameters={"repo": "MoonLadderStudios/MoonMind"},
        plan_ref="art_plan_1",
    )

    assert review_snapshots
    assert review_snapshots[0]["status"] == "reviewing"
    assert review_snapshots[0]["checks"] == [
        {
            "kind": "approval_policy",
            "status": "pending",
            "summary": "Structured review in progress",
            "retryCount": 0,
            "artifactRef": None,
        }
    ]
    step = workflow.get_step_ledger()["steps"][0]
    assert step["status"] == "succeeded"
    assert step["checks"] == [
        {
            "kind": "approval_policy",
            "status": "passed",
            "summary": "Approved by structured review",
            "retryCount": 0,
            "artifactRef": "art_review_1",
        }
    ]
    review_payloads = [
        payload for payload in written_review_payloads if "verdict" in payload
    ]
    assert review_payloads[0]["verdict"]["verdict"] == "PASS"
    attempt_payloads = [
        payload for payload in written_review_payloads if payload.get("contentType")
    ]
    assert attempt_payloads[0]["stepAttemptId"] == (
        "wf-run-review-1:run-review-1:apply-patch:attempt:1"
    )
    assert step["artifacts"]["attemptManifestRef"] == "art_attempt_1"

@pytest.mark.asyncio
async def test_run_execution_stage_retries_failed_reviews_with_feedback_and_retry_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindRunWorkflow()
    workflow._owner_id = "owner-1"
    written_review_payloads: list[dict[str, Any]] = []
    skill_inputs: list[dict[str, Any]] = []
    review_artifact_ids = iter(("art_review_1", "art_review_2"))
    step_attempt_artifact_ids = iter(
        (
            "art_attempt_1",
            "art_attempt_2",
            "art_attempt_2_terminal",
        )
    )
    review_verdicts = iter(
        (
            {
                "verdict": "FAIL",
                "confidence": 0.84,
                "feedback": "Tests still fail because the import is missing.",
                "issues": [
                    {
                        "severity": "error",
                        "description": "Missing import",
                        "evidence": "stderr tail",
                    }
                ],
            },
            {
                "verdict": "PASS",
                "confidence": 0.93,
                "feedback": None,
                "issues": [],
            },
        )
    )

    async def fake_execute_activity(
        activity_type: str,
        payload: Any,
        **_kwargs: Any,
    ) -> Any:
        if activity_type == "provider_profile.list":
            return {"profiles": []}
        if activity_type == "artifact.create":
            if str(payload.get("name") or "").startswith("reports/step_attempt"):
                return (
                    {"artifact_id": next(step_attempt_artifact_ids)},
                    {"upload_url": "unused"},
                )
            return ({"artifact_id": next(review_artifact_ids)}, {"upload_url": "unused"})
        if activity_type == "mm.skill.execute":
            invocation_payload = payload["invocation_payload"]
            skill_inputs.append(dict(invocation_payload["inputs"]))
            return {
                "status": "COMPLETED",
                "summary": "Patch applied cleanly",
                "outputs": {
                    "outputSummaryRef": f"art_summary_{len(skill_inputs)}",
                    "stateCheckpointRef": f"art_checkpoint_{len(skill_inputs)}",
                },
            }
        if activity_type == "step.review":
            return next(review_verdicts)
        raise AssertionError(f"unexpected activity: {activity_type}")

    async def fake_execute_typed_activity(
        activity_type: str,
        payload: Any,
        **_kwargs: Any,
    ) -> Any:
        if activity_type == "artifact.read":
            artifact_ref = getattr(payload, "artifact_ref", None)
            if artifact_ref == "art_plan_1":
                return json.dumps(_approval_policy_plan_payload()).encode("utf-8")
            if artifact_ref == "artifact://registry/1":
                return json.dumps(_registry_payload()).encode("utf-8")
        if activity_type == "artifact.write_complete":
            if getattr(payload, "content_type", "") == (
                "application/vnd.moonmind.step-attempt+json;version=1"
            ):
                return {"ok": True}
            written_review_payloads.append(json.loads(payload.payload.decode("utf-8")))
            return {"ok": True}
        raise AssertionError(f"unexpected typed activity: {activity_type}")

    workflow_info = SimpleNamespace(
        namespace="default",
        workflow_id="wf-run-review-2",
        run_id="run-review-2",
        task_queue="mm.workflow",
        search_attributes={"mm_owner_type": ["user"], "mm_owner_id": ["owner-1"]},
    )
    monkeypatch.setattr(run_module.workflow, "info", lambda: workflow_info)
    monkeypatch.setattr(run_module.workflow, "upsert_memo", lambda _memo: None)
    monkeypatch.setattr(
        run_module.workflow,
        "upsert_search_attributes",
        lambda _attributes: None,
    )
    monkeypatch.setattr(
        run_module.workflow,
        "execute_activity",
        fake_execute_activity,
    )
    monkeypatch.setattr(
        run_module,
        "execute_typed_activity",
        fake_execute_typed_activity,
    )
    monkeypatch.setattr(run_module.workflow, "patched", lambda _patch_id: True)

    await workflow._run_execution_stage(
        parameters={"repo": "MoonLadderStudios/MoonMind"},
        plan_ref="art_plan_1",
    )

    assert len(skill_inputs) == 2
    assert "_review_feedback" not in skill_inputs[0]
    assert skill_inputs[1]["_review_feedback"] == {
        "attempt": 1,
        "feedback": "Tests still fail because the import is missing.",
        "issues": [
            {
                "severity": "error",
                "description": "Missing import",
                "evidence": "stderr tail",
            }
        ],
    }
    step = workflow.get_step_ledger()["steps"][0]
    assert step["attempt"] == 2
    assert step["status"] == "succeeded"
    assert step["checks"] == [
        {
            "kind": "approval_policy",
            "status": "passed",
            "summary": "Approved after 1 retry",
            "retryCount": 1,
            "artifactRef": "art_review_2",
        }
    ]
    review_payloads = [
        payload for payload in written_review_payloads if "verdict" in payload
    ]
    assert review_payloads[0]["verdict"]["verdict"] == "FAIL"
    assert review_payloads[1]["verdict"]["verdict"] == "PASS"
    assert step["artifacts"]["attemptManifestRef"] == "art_attempt_2"
    assert step["artifacts"]["attemptManifestRefs"] == [
        "art_attempt_1",
        "art_attempt_2",
    ]

@pytest.mark.asyncio
async def test_run_execution_stage_retries_agent_runtime_reviews_with_feedback_in_instruction_ref(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindRunWorkflow()
    workflow._owner_id = "owner-1"
    written_review_payloads: list[dict[str, Any]] = []
    child_requests: list[Any] = []
    review_artifact_ids = iter(("art_review_1", "art_review_2"))
    step_attempt_artifact_ids = iter(
        (
            "art_attempt_1",
            "art_attempt_2",
            "art_attempt_2_terminal",
        )
    )
    review_verdicts = iter(
        (
            {
                "verdict": "FAIL",
                "confidence": 0.84,
                "feedback": "Add the missing validation before retrying.",
                "issues": [],
            },
            {
                "verdict": "PASS",
                "confidence": 0.93,
                "feedback": None,
                "issues": [],
            },
        )
    )

    plan_payload = _approval_policy_plan_payload()
    plan_payload["nodes"] = [
        {
            "id": "delegate-agent",
            "tool": {"type": "agent_runtime", "name": "jules", "version": ""},
            "inputs": {
                "targetRuntime": "jules",
                "instructions": "Implement the requested change.",
            },
            "options": {},
        }
    ]

    async def fake_execute_activity(
        activity_type: str,
        payload: Any,
        **_kwargs: Any,
    ) -> Any:
        if activity_type == "provider_profile.list":
            return {"profiles": []}
        if activity_type == "artifact.create":
            if str(payload.get("name") or "").startswith("reports/step_attempt"):
                return (
                    {"artifact_id": next(step_attempt_artifact_ids)},
                    {"upload_url": "unused"},
                )
            return ({"artifact_id": next(review_artifact_ids)}, {"upload_url": "unused"})
        if activity_type == "step.review":
            return next(review_verdicts)
        raise AssertionError(f"unexpected activity: {activity_type}")

    async def fake_execute_typed_activity(
        activity_type: str,
        payload: Any,
        **_kwargs: Any,
    ) -> Any:
        if activity_type == "artifact.read":
            artifact_ref = getattr(payload, "artifact_ref", None)
            if artifact_ref == "art_plan_1":
                return json.dumps(plan_payload).encode("utf-8")
        if activity_type == "artifact.write_complete":
            if getattr(payload, "content_type", "") == (
                "application/vnd.moonmind.step-attempt+json;version=1"
            ):
                return {"ok": True}
            written_review_payloads.append(json.loads(payload.payload.decode("utf-8")))
            return {"ok": True}
        raise AssertionError(f"unexpected typed activity: {activity_type}")

    async def fake_execute_child_workflow(
        workflow_name: str,
        request: Any,
        **_kwargs: Any,
    ) -> Any:
        assert workflow_name == "MoonMind.AgentRun"
        child_requests.append(request)
        return {
            "summary": "Agent run completed",
            "output_refs": ["art_output_1"],
            "failure_class": None,
            "metadata": {"stateCheckpointRef": f"art_checkpoint_{len(child_requests)}"},
        }

    workflow_info = SimpleNamespace(
        namespace="default",
        workflow_id="wf-run-review-agent-1",
        run_id="run-review-agent-1",
        task_queue="mm.workflow",
        search_attributes={"mm_owner_type": ["user"], "mm_owner_id": ["owner-1"]},
    )
    monkeypatch.setattr(run_module.workflow, "info", lambda: workflow_info)
    monkeypatch.setattr(run_module.workflow, "upsert_memo", lambda _memo: None)
    monkeypatch.setattr(
        run_module.workflow,
        "upsert_search_attributes",
        lambda _attributes: None,
    )
    monkeypatch.setattr(
        run_module.workflow,
        "execute_activity",
        fake_execute_activity,
    )
    monkeypatch.setattr(
        run_module,
        "execute_typed_activity",
        fake_execute_typed_activity,
    )
    monkeypatch.setattr(
        run_module.workflow,
        "execute_child_workflow",
        fake_execute_child_workflow,
    )
    monkeypatch.setattr(run_module.workflow, "patched", lambda _patch_id: True)

    await workflow._run_execution_stage(
        parameters={"repo": "MoonLadderStudios/MoonMind"},
        plan_ref="art_plan_1",
    )

    assert len(child_requests) == 2
    assert child_requests[0].instruction_ref == "Implement the requested change."
    assert "REVIEW FEEDBACK (attempt 1)" in child_requests[1].instruction_ref
    assert (
        "Add the missing validation before retrying."
        in child_requests[1].instruction_ref
    )
    step = workflow.get_step_ledger()["steps"][0]
    assert step["attempt"] == 2
    assert step["status"] == "succeeded"
    assert step["checks"] == [
        {
            "kind": "approval_policy",
            "status": "passed",
            "summary": "Approved after 1 retry",
            "retryCount": 1,
            "artifactRef": "art_review_2",
        }
    ]
    review_payloads = [
        payload for payload in written_review_payloads if "verdict" in payload
    ]
    assert review_payloads[0]["attempt"] == 1
    assert review_payloads[1]["attempt"] == 2
    assert step["artifacts"]["attemptManifestRef"] == "art_attempt_2"
