"""Unit tests for TemporalAgentRuntimeActivities real implementations."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from moonmind.workflows.temporal.activity_runtime import (
    TemporalAgentRuntimeActivities,
)


# ---------------------------------------------------------------------------
# agent_runtime_publish_artifacts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publish_artifacts_returns_none_result_unchanged():
    activities = TemporalAgentRuntimeActivities()
    result = await activities.agent_runtime_publish_artifacts(None)
    assert result is None


@pytest.mark.asyncio
async def test_publish_artifacts_no_service_returns_unchanged():
    activities = TemporalAgentRuntimeActivities(artifact_service=None)
    input_result = {"summary": "done", "output_refs": ["ref1"]}
    result = await activities.agent_runtime_publish_artifacts(input_result)
    assert result == input_result


@pytest.mark.asyncio
async def test_publish_artifacts_writes_summary_artifact():
    mock_service = MagicMock()
    mock_artifact = MagicMock()
    mock_artifact.artifact_id = "art-123"
    mock_completed = MagicMock()
    mock_completed.artifact_id = "art-123"
    mock_service.create = AsyncMock(return_value=(mock_artifact, None))
    mock_service.write_complete = AsyncMock(return_value=mock_completed)

    activities = TemporalAgentRuntimeActivities(artifact_service=mock_service)
    input_result = {
        "summary": "Completed successfully",
        "output_refs": ["ref1", "ref2"],
        "failure_class": None,
    }

    with patch(
        "moonmind.workflows.temporal.activity_runtime._write_json_artifact",
    ) as mock_write:
        mock_ref = MagicMock()
        mock_ref.artifact_id = "art-456"
        mock_write.return_value = mock_ref

        result = await activities.agent_runtime_publish_artifacts(input_result)

    assert result["diagnostics_ref"] == "art-456"
    assert result["summary"] == "Completed successfully"
    mock_write.assert_called_once()


@pytest.mark.asyncio
async def test_publish_artifacts_handles_write_failure_gracefully():
    mock_service = MagicMock()

    activities = TemporalAgentRuntimeActivities(artifact_service=mock_service)
    input_result = {"summary": "test", "output_refs": []}

    with patch(
        "moonmind.workflows.temporal.activity_runtime._write_json_artifact",
        side_effect=RuntimeError("write failed"),
    ):
        result = await activities.agent_runtime_publish_artifacts(input_result)

    # Should return the original result even if write fails
    assert result == input_result


# ---------------------------------------------------------------------------
# agent_runtime_cancel
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_delegates_to_supervisor_for_managed():
    mock_supervisor = MagicMock()
    mock_supervisor.cancel = AsyncMock()

    activities = TemporalAgentRuntimeActivities(run_supervisor=mock_supervisor)
    await activities.agent_runtime_cancel({"agent_kind": "managed", "run_id": "run-1"})

    mock_supervisor.cancel.assert_awaited_once_with("run-1")


@pytest.mark.asyncio
async def test_cancel_handles_supervisor_error_gracefully():
    mock_supervisor = MagicMock()
    mock_supervisor.cancel = AsyncMock(side_effect=RuntimeError("cancel failed"))

    activities = TemporalAgentRuntimeActivities(run_supervisor=mock_supervisor)
    # Should not raise
    await activities.agent_runtime_cancel({"agent_kind": "managed", "run_id": "run-2"})

    mock_supervisor.cancel.assert_awaited_once_with("run-2")


@pytest.mark.asyncio
async def test_cancel_without_supervisor_updates_store():
    mock_store = MagicMock()

    activities = TemporalAgentRuntimeActivities(run_store=mock_store)
    await activities.agent_runtime_cancel({"agent_kind": "managed", "run_id": "run-3"})

    mock_store.update_status.assert_called_once()
    call_args = mock_store.update_status.call_args
    assert call_args.args[0] == "run-3"
    assert call_args.args[1] == "canceled"


@pytest.mark.asyncio
async def test_cancel_handles_external_kind_gracefully():
    activities = TemporalAgentRuntimeActivities()
    # Should not raise — just logs a warning
    await activities.agent_runtime_cancel({"agent_kind": "external", "run_id": "run-4"})


@pytest.mark.asyncio
async def test_cancel_handles_unknown_kind_gracefully():
    activities = TemporalAgentRuntimeActivities()
    # Should not raise
    await activities.agent_runtime_cancel({"agent_kind": "unknown", "run_id": "run-5"})


@pytest.mark.asyncio
async def test_cancel_handles_tuple_request():
    mock_supervisor = MagicMock()
    mock_supervisor.cancel = AsyncMock()

    activities = TemporalAgentRuntimeActivities(run_supervisor=mock_supervisor)
    await activities.agent_runtime_cancel(("managed", "run-6"))

    mock_supervisor.cancel.assert_awaited_once_with("run-6")


@pytest.mark.asyncio
async def test_cancel_handles_none_request():
    activities = TemporalAgentRuntimeActivities()
    # Should not raise
    await activities.agent_runtime_cancel(None)


@pytest.mark.asyncio
async def test_agent_runtime_launch_binds_workflow_id_to_task_run_before_launch():
    mock_launcher = MagicMock()
    mock_launcher.launch = AsyncMock(
        return_value=(SimpleNamespace(model_dump=lambda mode="json": {"status": "launching"}), None, [])
    )
    mock_supervisor = MagicMock()

    activities = TemporalAgentRuntimeActivities(
        run_launcher=mock_launcher,
        run_supervisor=mock_supervisor,
    )
    activities._report_task_run_binding = AsyncMock()

    payload = {
        "run_id": "6f8b6bf7-6e0c-4d71-9b08-18d489f17a8d",
        "workflow_id": "mm:workflow-123",
        "request": {
            "agentKind": "managed",
            "agentId": "codex_cli",
            "executionProfileRef": "default",
            "correlationId": "corr-1",
            "idempotencyKey": "idem-1",
        },
        "profile": {
            "runtimeId": "codex_cli",
            "commandTemplate": ["codex", "exec"],
            "defaultModel": "gpt-5.3-codex",
            "defaultEffort": "medium",
            "defaultTimeoutSeconds": 3600,
            "workspaceMode": "tempdir",
            "envOverrides": {},
        },
    }

    result = await activities.agent_runtime_launch(payload)

    activities._report_task_run_binding.assert_awaited_once_with(
        "mm:workflow-123",
        "6f8b6bf7-6e0c-4d71-9b08-18d489f17a8d",
    )
    assert result == {"status": "launching"}
