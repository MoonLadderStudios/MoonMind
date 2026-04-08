from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

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


def test_run_initializes_latest_run_step_ledger(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_workflow_runtime(monkeypatch)
    workflow = MoonMindRunWorkflow()
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
