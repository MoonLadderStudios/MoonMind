"""Unit tests for managed `agent_runtime.fetch_result` enrichment paths."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from moonmind.schemas.agent_runtime_models import ManagedRunRecord
from moonmind.workflows.temporal import activity_runtime as activity_runtime_module
from moonmind.workflows.temporal.activity_runtime import TemporalAgentRuntimeActivities
from moonmind.workflows.temporal.runtime.store import ManagedRunStore

pytestmark = [pytest.mark.asyncio]


def _save_failed_record(
    store: ManagedRunStore,
    *,
    run_id: str,
    runtime_id: str,
    started_at: datetime,
    finished_at: datetime,
    error_message: str,
    workspace_path: str = "/tmp/workspace",
) -> None:
    store.save(
        ManagedRunRecord(
            runId=run_id,
            agentId=runtime_id,
            runtimeId=runtime_id,
            status="failed",
            pid=1234,
            exitCode=1,
            startedAt=started_at,
            finishedAt=finished_at,
            workspacePath=workspace_path,
            errorMessage=error_message,
            failureClass="execution_error",
        )
    )


async def test_fetch_result_maps_gemini_quota_report_to_integration_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report_dir = tmp_path / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(activity_runtime_module, "_GEMINI_ERROR_REPORT_DIR", report_dir)

    run_store = ManagedRunStore(tmp_path / "run_store")
    now = datetime.now(tz=UTC)
    started_at = now - timedelta(minutes=3)
    finished_at = now - timedelta(minutes=2)
    _save_failed_record(
        run_store,
        run_id="gemini-run-1",
        runtime_id="gemini_cli",
        started_at=started_at,
        finished_at=finished_at,
        error_message="Process exited with code 1",
    )

    report_path = report_dir / "gemini-client-error-Turn.run-sendMessageStream-test.json"
    report_path.write_text(
        json.dumps(
            {
                "error": {
                    "message": (
                        "You have exhausted your capacity on this model. "
                        "Your quota will reset after 4h50m26s."
                    ),
                    "stack": "TerminalQuotaError: quota exhausted",
                }
            }
        ),
        encoding="utf-8",
    )
    os.utime(report_path, (finished_at.timestamp(), finished_at.timestamp()))

    activities = TemporalAgentRuntimeActivities(run_store=run_store)
    result = await activities.agent_runtime_fetch_result({"run_id": "gemini-run-1"})

    assert result["failureClass"] == "integration_error"
    assert result["providerErrorCode"] == "quota_exhausted"
    assert "exhausted your capacity" in result["summary"].lower()


async def test_fetch_result_keeps_non_generic_failure_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report_dir = tmp_path / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(activity_runtime_module, "_GEMINI_ERROR_REPORT_DIR", report_dir)

    run_store = ManagedRunStore(tmp_path / "run_store")
    now = datetime.now(tz=UTC)
    started_at = now - timedelta(minutes=3)
    finished_at = now - timedelta(minutes=2)
    _save_failed_record(
        run_store,
        run_id="gemini-run-2",
        runtime_id="gemini_cli",
        started_at=started_at,
        finished_at=finished_at,
        error_message="pr-resolver reported blocked state",
    )

    report_path = report_dir / "gemini-client-error-Turn.run-sendMessageStream-test2.json"
    report_path.write_text(
        json.dumps(
            {
                "error": {
                    "message": "You have exhausted your capacity on this model.",
                    "stack": "TerminalQuotaError: quota exhausted",
                }
            }
        ),
        encoding="utf-8",
    )
    os.utime(report_path, (finished_at.timestamp(), finished_at.timestamp()))

    activities = TemporalAgentRuntimeActivities(run_store=run_store)
    result = await activities.agent_runtime_fetch_result({"run_id": "gemini-run-2"})

    assert result["failureClass"] == "execution_error"
    assert result["summary"] == "pr-resolver reported blocked state"
    assert result["providerErrorCode"] is None


async def test_fetch_result_maps_gemini_rate_limit_to_429(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report_dir = tmp_path / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(activity_runtime_module, "_GEMINI_ERROR_REPORT_DIR", report_dir)

    run_store = ManagedRunStore(tmp_path / "run_store")
    now = datetime.now(tz=UTC)
    started_at = now - timedelta(minutes=3)
    finished_at = now - timedelta(minutes=2)
    _save_failed_record(
        run_store,
        run_id="gemini-run-429",
        runtime_id="gemini_cli",
        started_at=started_at,
        finished_at=finished_at,
        error_message="Process exited with code 1",
    )

    report_path = report_dir / "gemini-client-error-rate-limit.json"
    report_path.write_text(
        json.dumps(
            {
                "error": {
                    "message": "Error 429: Too many requests, rate limit exceeded",
                    "stack": "SomeStack: 429",
                }
            }
        ),
        encoding="utf-8",
    )
    os.utime(report_path, (finished_at.timestamp(), finished_at.timestamp()))

    activities = TemporalAgentRuntimeActivities(run_store=run_store)
    result = await activities.agent_runtime_fetch_result({"run_id": "gemini-run-429"})

    assert result["failureClass"] == "integration_error"
    assert result["providerErrorCode"] == "429"
    assert "rate limit" in result["summary"].lower()


async def test_fetch_result_prefers_report_with_matching_run_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report_dir = tmp_path / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(activity_runtime_module, "_GEMINI_ERROR_REPORT_DIR", report_dir)

    run_id = "gemini-run-match"
    workspace_path = f"/work/agent_jobs/workspaces/{run_id}/repo"
    run_store = ManagedRunStore(tmp_path / "run_store")
    now = datetime.now(tz=UTC)
    started_at = now - timedelta(minutes=3)
    finished_at = now - timedelta(minutes=2)
    _save_failed_record(
        run_store,
        run_id=run_id,
        runtime_id="gemini_cli",
        started_at=started_at,
        finished_at=finished_at,
        error_message="Process exited with code 1",
        workspace_path=workspace_path,
    )

    # Closer by mtime but unrelated to the run (generic 429).
    generic_report = report_dir / "gemini-client-error-generic.json"
    generic_report.write_text(
        json.dumps(
            {
                "error": {
                    "message": "Error 429: Too many requests",
                    "stack": "generic stack",
                }
            }
        ),
        encoding="utf-8",
    )
    os.utime(
        generic_report,
        ((finished_at + timedelta(seconds=2)).timestamp(), (finished_at + timedelta(seconds=2)).timestamp()),
    )

    # Slightly farther by mtime but includes workspace marker for this run.
    matching_report = report_dir / "gemini-client-error-matching.json"
    matching_report.write_text(
        json.dumps(
            {
                "error": {
                    "message": "You have exhausted your capacity on this model.",
                    "stack": "TerminalQuotaError: quota exhausted",
                },
                "context": [{"workspace": workspace_path}],
            }
        ),
        encoding="utf-8",
    )
    os.utime(
        matching_report,
        ((finished_at + timedelta(seconds=6)).timestamp(), (finished_at + timedelta(seconds=6)).timestamp()),
    )

    activities = TemporalAgentRuntimeActivities(run_store=run_store)
    result = await activities.agent_runtime_fetch_result({"run_id": run_id})

    assert result["failureClass"] == "integration_error"
    assert result["providerErrorCode"] == "quota_exhausted"


async def test_fetch_result_uses_diagnostics_when_report_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report_dir = tmp_path / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(activity_runtime_module, "_GEMINI_ERROR_REPORT_DIR", report_dir)
    monkeypatch.setenv("MOONMIND_AGENT_RUNTIME_ARTIFACTS", str(tmp_path))

    run_store = ManagedRunStore(tmp_path / "run_store")
    now = datetime.now(tz=UTC)
    started_at = now - timedelta(minutes=3)
    finished_at = now - timedelta(minutes=2)
    run_id = "gemini-run-diagnostics-429"
    diagnostics_ref = f"{run_id}/diagnostics.json"
    diagnostics_path = tmp_path / "artifacts" / diagnostics_ref
    diagnostics_path.parent.mkdir(parents=True, exist_ok=True)
    diagnostics_path.write_text(
        json.dumps(
            {
                "parsed_output": {
                    "rate_limited": True,
                    "error_messages": [
                        "Attempt 6 failed with status 429. Retrying with backoff...",
                        "reason: MODEL_CAPACITY_EXHAUSTED",
                    ],
                }
            }
        ),
        encoding="utf-8",
    )

    run_store.save(
        ManagedRunRecord(
            runId=run_id,
            agentId="gemini_cli",
            runtimeId="gemini_cli",
            status="failed",
            pid=1234,
            exitCode=143,
            startedAt=started_at,
            finishedAt=finished_at,
            diagnosticsRef=diagnostics_ref,
            errorMessage="Process exited with code 143",
            failureClass="integration_error",
            workspacePath=f"/work/agent_jobs/workspaces/{run_id}/repo",
        )
    )

    activities = TemporalAgentRuntimeActivities(run_store=run_store)
    result = await activities.agent_runtime_fetch_result({"run_id": run_id})

    assert result["failureClass"] == "integration_error"
    assert result["providerErrorCode"] == "429"
    assert "429" in result["summary"]
