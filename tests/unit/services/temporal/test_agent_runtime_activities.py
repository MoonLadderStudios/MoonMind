"""Unit tests for TemporalAgentRuntimeActivities real implementations."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest

from moonmind.schemas.agent_runtime_models import AgentRunResult
from moonmind.workflows.temporal.artifacts import ExecutionRef
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
    input_result = AgentRunResult(summary="done", output_refs=["ref1"])
    result = await activities.agent_runtime_publish_artifacts(input_result)
    assert isinstance(result, AgentRunResult)
    assert result.summary == "done"


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
    input_result = AgentRunResult(
        summary="Completed successfully",
        output_refs=["ref1", "ref2"],
        failure_class=None,
    )

    with (
        patch("moonmind.workflows.temporal.activity_runtime._write_json_artifact") as mock_write,
        patch("temporalio.activity.info", return_value=SimpleNamespace(namespace="default", workflow_id="wf-1", workflow_run_id="run-1")),
    ):
        summary_ref = MagicMock()
        summary_ref.artifact_id = "art-summary"
        result_ref = MagicMock()
        result_ref.artifact_id = "art-result"
        mock_write.side_effect = [summary_ref, result_ref]

        result = await activities.agent_runtime_publish_artifacts(input_result)

    assert isinstance(result, AgentRunResult)
    assert result.diagnostics_ref == "art-result"
    assert result.summary == "Completed successfully"
    assert mock_write.await_count == 2
    assert mock_write.await_args_list[0].kwargs["execution_ref"] == ExecutionRef(
        namespace="default",
        workflow_id="wf-1",
        run_id="run-1",
        link_type="output.summary",
    )
    assert mock_write.await_args_list[1].kwargs["execution_ref"] == ExecutionRef(
        namespace="default",
        workflow_id="wf-1",
        run_id="run-1",
        link_type="output.agent_result",
    )


@pytest.mark.asyncio
async def test_publish_artifacts_handles_write_failure_gracefully():
    mock_service = MagicMock()

    activities = TemporalAgentRuntimeActivities(artifact_service=mock_service)
    input_result = AgentRunResult(summary="test", output_refs=[])

    with patch(
        "moonmind.workflows.temporal.activity_runtime._write_json_artifact",
        side_effect=RuntimeError("write failed"),
    ):
        result = await activities.agent_runtime_publish_artifacts(input_result)

    # Should return the original result even if write fails
    assert isinstance(result, AgentRunResult)
    assert result.summary == "test"


@pytest.mark.asyncio
async def test_publish_artifacts_writes_managed_session_input_reference_artifacts():
    activities = TemporalAgentRuntimeActivities(artifact_service=MagicMock())
    input_result = AgentRunResult(
        summary="Completed successfully",
        output_refs=["artifact:stdout", "artifact:session-summary"],
        metadata={
            "managedSession": {"sessionId": "sess-1", "sessionEpoch": 1},
            "instructionRef": "artifact:instructions",
            "resolvedSkillsetRef": "artifact:skill-snapshot",
        },
    )

    with (
        patch("moonmind.workflows.temporal.activity_runtime._write_json_artifact") as mock_write,
        patch("temporalio.activity.info", return_value=SimpleNamespace(namespace="default", workflow_id="wf-1", workflow_run_id="run-1")),
    ):
        refs = []
        for artifact_id in ("art-input", "art-skill", "art-summary", "art-result"):
            ref = MagicMock()
            ref.artifact_id = artifact_id
            refs.append(ref)
        mock_write.side_effect = refs

        result = await activities.agent_runtime_publish_artifacts(input_result)

    assert isinstance(result, AgentRunResult)
    assert result.diagnostics_ref == "art-result"
    assert [call.kwargs["execution_ref"].link_type for call in mock_write.await_args_list] == [
        "input.instructions",
        "input.skill_snapshot",
        "output.summary",
        "output.agent_result",
    ]


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
async def test_cancel_cleanup_failures_do_not_raise():
    mock_supervisor = MagicMock()
    mock_supervisor.cancel = AsyncMock()
    mock_launcher = MagicMock()
    mock_launcher.cleanup_run_support = AsyncMock(
        side_effect=RuntimeError("cleanup failed")
    )

    activities = TemporalAgentRuntimeActivities(
        run_launcher=mock_launcher,
        run_supervisor=mock_supervisor,
    )

    await activities.agent_runtime_cancel({"agent_kind": "managed", "run_id": "run-2"})

    mock_supervisor.cancel.assert_awaited_once_with("run-2")
    mock_launcher.cleanup_run_support.assert_awaited_once_with("run-2")


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
    from moonmind.schemas.agent_runtime_models import AgentRunStatus
    activities = TemporalAgentRuntimeActivities()
    # Should not raise — returns AgentRunStatus best-effort
    result = await activities.agent_runtime_cancel({"agent_kind": "unknown", "run_id": "run-5"})
    assert isinstance(result, AgentRunStatus)
    assert result.status == "canceled"


@pytest.mark.asyncio
async def test_cancel_handles_tuple_request():
    mock_supervisor = MagicMock()
    mock_supervisor.cancel = AsyncMock()

    activities = TemporalAgentRuntimeActivities(run_supervisor=mock_supervisor)
    await activities.agent_runtime_cancel(("managed", "run-6"))

    mock_supervisor.cancel.assert_awaited_once_with("run-6")


@pytest.mark.asyncio
async def test_cancel_handles_none_request():
    from moonmind.schemas.agent_runtime_models import AgentRunStatus
    activities = TemporalAgentRuntimeActivities()
    # Should not raise — returns AgentRunStatus best-effort for None/unknown kind
    result = await activities.agent_runtime_cancel(None)
    assert isinstance(result, AgentRunStatus)
    assert result.status == "canceled"


@pytest.mark.asyncio
async def test_agent_runtime_launch_binds_workflow_id_to_task_run_before_launch():
    mock_launcher = MagicMock()
    mock_launcher.launch = AsyncMock(
        return_value=(SimpleNamespace(model_dump=lambda mode="json": {"status": "launching"}), None, [], [])
    )
    mock_launcher.cleanup_run_support = AsyncMock()
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


@pytest.mark.asyncio
async def test_agent_runtime_launch_accepts_legacy_file_templates_payload():
    launch_record = SimpleNamespace(model_dump=lambda mode="json": {"status": "launching"})
    mock_launcher = MagicMock()
    mock_launcher.launch = AsyncMock(return_value=(launch_record, None, [], []))
    mock_supervisor = MagicMock()

    activities = TemporalAgentRuntimeActivities(
        run_launcher=mock_launcher,
        run_supervisor=mock_supervisor,
    )

    payload = {
        "run_id": "run-legacy-file-templates",
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
            "fileTemplates": {
                "{{runtime_support_dir}}/codex-home/config.toml": 'model = "qwen"\n',
            },
        },
    }

    result = await activities.agent_runtime_launch(payload)

    assert result == {"status": "launching"}
    profile = mock_launcher.launch.await_args.kwargs["profile"]
    assert profile.file_templates[0].path == "{{runtime_support_dir}}/codex-home/config.toml"
    assert profile.file_templates[0].content_template == 'model = "qwen"\n'


@pytest.mark.asyncio
async def test_agent_runtime_launch_defers_support_cleanup_until_fetch_result():
    class _FakeProcess:
        stdout = None
        stderr = None

    launch_record = SimpleNamespace(model_dump=lambda mode="json": {"status": "launching"})
    mock_launcher = MagicMock()
    mock_launcher.launch = AsyncMock(
        return_value=(launch_record, _FakeProcess(), [], ["/tmp/cleanup.file"])
    )
    mock_launcher.cleanup_run_support = AsyncMock()
    mock_supervisor = MagicMock()
    mock_supervisor.supervise = AsyncMock(return_value=SimpleNamespace(status="completed"))

    activities = TemporalAgentRuntimeActivities(
        run_launcher=mock_launcher,
        run_supervisor=mock_supervisor,
    )

    payload = {
        "run_id": "run-cleanup-1",
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
    assert result == {"status": "launching"}

    assert len(activities._supervision_tasks) == 1
    [task] = list(activities._supervision_tasks)
    await task

    mock_supervisor.supervise.assert_awaited_once_with(
        run_id="run-cleanup-1",
        process=ANY,
        timeout_seconds=3600,
        cleanup_paths=None,
        deferred_cleanup_paths=["/tmp/cleanup.file"],
    )
    mock_launcher.cleanup_run_support.assert_not_awaited()


@pytest.mark.asyncio
async def test_agent_runtime_launch_cleans_deferred_support_when_supervisor_fails():
    class _FakeProcess:
        stdout = None
        stderr = None

    launch_record = SimpleNamespace(model_dump=lambda mode="json": {"status": "launching"})
    mock_launcher = MagicMock()
    mock_launcher.launch = AsyncMock(
        return_value=(launch_record, _FakeProcess(), [], ["/tmp/cleanup.file"])
    )
    mock_launcher.cleanup_run_support = AsyncMock()
    mock_supervisor = MagicMock()
    mock_supervisor.supervise = AsyncMock(side_effect=RuntimeError("supervise failed"))

    activities = TemporalAgentRuntimeActivities(
        run_launcher=mock_launcher,
        run_supervisor=mock_supervisor,
    )

    await activities.agent_runtime_launch(
        {
            "run_id": "run-cleanup-2",
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
    )

    [task] = list(activities._supervision_tasks)
    await task

    mock_launcher.cleanup_run_support.assert_awaited_once_with("run-cleanup-2")
    mock_supervisor.cleanup_deferred_run_files.assert_called_once_with("run-cleanup-2")
