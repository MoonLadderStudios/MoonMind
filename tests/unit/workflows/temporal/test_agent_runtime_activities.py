"""TDD tests for managed runtime activities — Phase 3 canonical return types.

Validates that agent_runtime_status, agent_runtime_cancel, and
agent_runtime_publish_artifacts return typed Pydantic contracts
(AgentRunStatus, AgentRunResult) instead of dict[str, Any] / None.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel

from moonmind.schemas.agent_runtime_models import (
    AgentRunResult,
    AgentRunStatus,
    ManagedRunRecord,
)
from moonmind.schemas.managed_session_models import (
    CodexManagedSessionArtifactsPublication,
    CodexManagedSessionBinding,
    CodexManagedSessionHandle,
    CodexManagedSessionRecord,
    CodexManagedSessionSnapshot,
    CodexManagedSessionSummary,
    CodexManagedSessionTurnResponse,
    LaunchCodexManagedSessionRequest,
)
from moonmind.schemas.temporal_activity_models import (
    AgentRuntimeCancelInput,
    AgentRuntimeStatusInput,
)
from moonmind.workflows.temporal import activity_runtime as activity_runtime_module
from moonmind.workflows.temporal import client as temporal_client_module
from moonmind.workflows.temporal import activity_runtime as activity_runtime_module
from moonmind.workflows.temporal.activity_runtime import (
    TemporalActivityRuntimeError,
    TemporalAgentRuntimeActivities,
)
from moonmind.workflows.temporal.runtime.store import ManagedRunStore

pytestmark = [pytest.mark.asyncio]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_store(tmp_path: Path) -> ManagedRunStore:
    return ManagedRunStore(tmp_path / "run_store")


def _save_record(
    store: ManagedRunStore,
    *,
    run_id: str,
    status: str,
    runtime_id: str = "codex_cli",
    failure_class: str | None = None,
    error_message: str | None = None,
    workspace_path: str | None = None,
) -> None:
    store.save(
        ManagedRunRecord(
            runId=run_id,
            agentId=runtime_id,
            runtimeId=runtime_id,
            status=status,
            startedAt=datetime.now(tz=UTC),
            workspacePath=workspace_path,
            failureClass=failure_class,
            errorMessage=error_message,
        )
    )


def _session_record(session_id: str, *, status: str) -> dict[str, Any]:
    return CodexManagedSessionRecord(
        sessionId=session_id,
        sessionEpoch=1,
        taskRunId="wf-run-1",
        containerId=f"container-{session_id}",
        threadId=f"thread-{session_id}",
        runtimeId="codex_cli",
        imageRef="moonmind:latest",
        controlUrl="http://session-control",
        status=status,
        workspacePath="/work/agent_jobs/wf-run-1/repo",
        sessionWorkspacePath="/work/agent_jobs/wf-run-1/session",
        artifactSpoolPath="/work/agent_jobs/wf-run-1/artifacts",
        startedAt=datetime.now(tz=UTC),
    ).model_dump(mode="json", by_alias=True)


# ---------------------------------------------------------------------------
# T1: agent_runtime_status — typed AgentRunStatus return
# ---------------------------------------------------------------------------


async def test_status_running_record_returns_typed_model(tmp_path: Path) -> None:
    """T1.1 — running record yields typed AgentRunStatus."""
    store = _make_store(tmp_path)
    _save_record(store, run_id="run-1", status="running", runtime_id="codex_cli")

    activities = TemporalAgentRuntimeActivities(run_store=store)
    result = await activities.agent_runtime_status({"run_id": "run-1", "agent_id": "codex_cli"})

    assert isinstance(result, AgentRunStatus), f"Expected AgentRunStatus, got {type(result)}"
    assert result.status == "running"
    assert result.agent_kind == "managed"


async def test_status_completed_record_returns_typed_model(tmp_path: Path) -> None:
    """T1.2 — completed record yields typed AgentRunStatus with correct status."""
    store = _make_store(tmp_path)
    _save_record(store, run_id="run-2", status="completed")

    activities = TemporalAgentRuntimeActivities(run_store=store)
    result = await activities.agent_runtime_status({"run_id": "run-2", "agent_id": "codex_cli"})

    assert isinstance(result, AgentRunStatus)
    assert result.status == "completed"


async def test_status_failed_record_returns_typed_model_with_metadata(tmp_path: Path) -> None:
    """T1.3 — failed record yields typed AgentRunStatus with runtimeId in metadata."""
    store = _make_store(tmp_path)
    _save_record(
        store,
        run_id="run-3",
        status="failed",
        runtime_id="gemini_cli",
        failure_class="execution_error",
        error_message="Process exited with code 1",
    )

    activities = TemporalAgentRuntimeActivities(run_store=store)
    result = await activities.agent_runtime_status({"run_id": "run-3", "agent_id": "gemini_cli"})

    assert isinstance(result, AgentRunStatus)
    assert result.status == "failed"
    assert result.metadata is not None
    assert result.metadata.get("runtimeId") == "gemini_cli"


async def test_status_no_record_returns_optimistic_running(tmp_path: Path) -> None:
    """T1.4 — missing record in store yields stub AgentRunStatus with status='running'."""
    store = _make_store(tmp_path)

    activities = TemporalAgentRuntimeActivities(run_store=store)
    result = await activities.agent_runtime_status({"run_id": "no-such-run", "agent_id": "codex_cli"})

    assert isinstance(result, AgentRunStatus)
    assert result.status == "running"
    assert result.agent_kind == "managed"


async def test_status_missing_run_id_raises_error(tmp_path: Path) -> None:
    """T1.5 — missing run_id raises TemporalActivityRuntimeError."""
    store = _make_store(tmp_path)
    activities = TemporalAgentRuntimeActivities(run_store=store)

    with pytest.raises(TemporalActivityRuntimeError):
        await activities.agent_runtime_status({"agent_id": "codex_cli"})


async def test_status_accepts_typed_request_model(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    _save_record(store, run_id="typed-status-1", status="running")
    activities = TemporalAgentRuntimeActivities(run_store=store)

    result = await activities.agent_runtime_status(
        AgentRuntimeStatusInput(runId="typed-status-1", agentId="codex_cli")
    )

    assert isinstance(result, AgentRunStatus)
    assert result.run_id == "typed-status-1"


async def test_fetch_result_validates_legacy_dict_to_typed_request(
    tmp_path: Path,
) -> None:
    store = _make_store(tmp_path)
    _save_record(store, run_id="typed-fetch-1", status="completed")
    activities = TemporalAgentRuntimeActivities(run_store=store)

    result = await activities.agent_runtime_fetch_result(
        {
            "run_id": "typed-fetch-1",
            "agent_id": "codex_cli",
            "publish_mode": "none",
            "pr_resolver_expected": True,
        }
    )

    assert isinstance(result, AgentRunResult)


async def test_cancel_accepts_typed_request_model() -> None:
    activities = TemporalAgentRuntimeActivities()

    result = await activities.agent_runtime_cancel(
        AgentRuntimeCancelInput(agentKind="external", runId="external-run-1")
    )

    assert isinstance(result, AgentRunStatus)
    assert result.run_id == "external-run-1"


async def test_external_agent_run_activity_wrapper_rejects_unknown_fields() -> None:
    activities = TemporalAgentRuntimeActivities()

    with pytest.raises(Exception):
        await activities.integration_jules_status(
            {"runId": "jules-1", "rawProviderPayload": {"status": "done"}}
        )


# ---------------------------------------------------------------------------
# T2: agent_runtime_cancel — typed AgentRunStatus return (not None)
# ---------------------------------------------------------------------------


async def test_cancel_with_supervisor_returns_typed_status(tmp_path: Path) -> None:
    """T2.1 — cancel with supervisor returns AgentRunStatus with status='canceled'."""
    mock_supervisor = AsyncMock()
    mock_supervisor.cancel = AsyncMock()

    activities = TemporalAgentRuntimeActivities(
        run_supervisor=mock_supervisor,
    )
    result = await activities.agent_runtime_cancel({"agent_kind": "managed", "run_id": "run-x"})

    assert isinstance(result, AgentRunStatus), f"Expected AgentRunStatus, got {type(result)}"
    assert result.status == "canceled"
    assert result.agent_kind == "managed"


async def test_cancel_supervisor_exception_still_returns_typed_status(tmp_path: Path) -> None:
    """T2.2 — supervisor.cancel raising an exception still yields AgentRunStatus."""
    mock_supervisor = AsyncMock()
    mock_supervisor.cancel = AsyncMock(side_effect=RuntimeError("supervisor failed"))

    activities = TemporalAgentRuntimeActivities(
        run_supervisor=mock_supervisor,
    )
    result = await activities.agent_runtime_cancel({"agent_kind": "managed", "run_id": "run-y"})

    assert isinstance(result, AgentRunStatus)
    assert result.status == "canceled"


async def test_cancel_no_supervisor_store_path_returns_typed_status(tmp_path: Path) -> None:
    """T2.3 — no supervisor but store update still returns AgentRunStatus."""
    store = _make_store(tmp_path)
    _save_record(store, run_id="run-cancel-store", status="running")

    activities = TemporalAgentRuntimeActivities(run_store=store)
    result = await activities.agent_runtime_cancel({"agent_kind": "managed", "run_id": "run-cancel-store"})

    assert isinstance(result, AgentRunStatus)
    assert result.status == "canceled"


async def test_cancel_external_kind_returns_typed_status(tmp_path: Path) -> None:
    """T2.4 — external/unknown kind path still returns AgentRunStatus (best-effort)."""
    activities = TemporalAgentRuntimeActivities()
    result = await activities.agent_runtime_cancel({"agent_kind": "external", "run_id": "ext-run"})

    assert isinstance(result, AgentRunStatus)
    assert result.status == "canceled"


# ---------------------------------------------------------------------------
# T3: agent_runtime_publish_artifacts — typed AgentRunResult return
# ---------------------------------------------------------------------------


async def test_publish_artifacts_no_service_returns_result_unchanged() -> None:
    """T3.1 — no artifact service configured → passthrough (returns input model)."""
    original = AgentRunResult(summary="done", failure_class=None)
    activities = TemporalAgentRuntimeActivities()  # no artifact_service

    result = await activities.agent_runtime_publish_artifacts(original)

    assert isinstance(result, AgentRunResult)
    assert result.summary == "done"


async def test_publish_artifacts_none_input_returns_none() -> None:
    """T3.3 — None input returns None."""
    activities = TemporalAgentRuntimeActivities()
    result = await activities.agent_runtime_publish_artifacts(None)
    assert result is None


async def test_publish_artifacts_stamps_step_metadata_when_context_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_metadata: list[dict[str, object] | None] = []

    async def fake_write_json_artifact(
        _service: object,
        *,
        principal: str,
        payload: object,
        execution_ref: object = None,
        metadata_json: dict[str, object] | None = None,
    ) -> SimpleNamespace:
        del principal, payload, execution_ref
        captured_metadata.append(metadata_json)
        return SimpleNamespace(artifact_id=f"art_{len(captured_metadata)}")

    monkeypatch.setattr(
        activity_runtime_module,
        "_write_json_artifact",
        fake_write_json_artifact,
    )

    activities = TemporalAgentRuntimeActivities(artifact_service=object())
    result = await activities.agent_runtime_publish_artifacts(
        AgentRunResult(
            summary="done",
            metadata={
                "moonmind": {
                    "stepLedger": {
                        "logicalStepId": "delegate-agent",
                        "attempt": 2,
                        "scope": "step",
                    }
                }
            },
        )
    )

    assert isinstance(result, AgentRunResult)
    assert len(captured_metadata) == 2
    assert captured_metadata[0]["step_id"] == "delegate-agent"
    assert captured_metadata[0]["attempt"] == 2
    assert captured_metadata[0]["scope"] == "step"
    assert captured_metadata[1]["step_id"] == "delegate-agent"


async def test_fetch_result_exposes_task_run_and_runtime_artifact_metadata(
    tmp_path: Path,
) -> None:
    store = _make_store(tmp_path)
    store.save(
        ManagedRunRecord(
            runId="550e8400-e29b-41d4-a716-446655440000",
            workflowId="wf-parent-1",
            agentId="codex_cli",
            runtimeId="codex_cli",
            status="completed",
            startedAt=datetime.now(tz=UTC),
            stdoutArtifactRef="art_stdout_1",
            stderrArtifactRef="art_stderr_1",
            mergedLogArtifactRef="art_merged_1",
            diagnosticsRef="art_diag_1",
        )
    )

    activities = TemporalAgentRuntimeActivities(run_store=store)
    result = await activities.agent_runtime_fetch_result(
        {"run_id": "550e8400-e29b-41d4-a716-446655440000", "agent_id": "codex_cli"}
    )

    assert isinstance(result, AgentRunResult)
    assert result.metadata["taskRunId"] == "550e8400-e29b-41d4-a716-446655440000"
    assert result.metadata["stdoutArtifactRef"] == "art_stdout_1"
    assert result.metadata["stderrArtifactRef"] == "art_stderr_1"
    assert result.metadata["mergedLogArtifactRef"] == "art_merged_1"
    assert result.metadata["diagnosticsRef"] == "art_diag_1"


# ---------------------------------------------------------------------------
# T4: session-oriented agent_runtime activities — typed managed-session returns
# ---------------------------------------------------------------------------


async def test_launch_session_requires_session_controller() -> None:
    activities = TemporalAgentRuntimeActivities()

    with pytest.raises(
        TemporalActivityRuntimeError,
        match="session_controller is required for agent_runtime.launch_session",
    ):
        await activities.agent_runtime_launch_session(
            {
                "taskRunId": "task-1",
                "sessionId": "sess-1",
                "threadId": "thread-1",
                "workspacePath": "/work/task/repo",
                "sessionWorkspacePath": "/work/task/session",
                "artifactSpoolPath": "/work/task/artifacts",
                "codexHomePath": "/work/task/codex-home",
                "imageRef": "moonmind:latest",
            }
        )


async def test_launch_session_delegates_to_remote_session_controller() -> None:
    controller = AsyncMock()
    controller.launch_session = AsyncMock(
        return_value=CodexManagedSessionHandle(
            sessionState={
                "sessionId": "sess-1",
                "sessionEpoch": 1,
                "containerId": "ctr-1",
                "threadId": "thread-1",
            },
            status="ready",
            imageRef="moonmind:latest",
        )
    )
    activities = TemporalAgentRuntimeActivities(session_controller=controller)

    result = await activities.agent_runtime_launch_session(
        {
            "taskRunId": "task-1",
            "sessionId": "sess-1",
            "threadId": "thread-1",
            "workspacePath": "/work/task/repo",
            "sessionWorkspacePath": "/work/task/session",
            "artifactSpoolPath": "/work/task/artifacts",
            "codexHomePath": "/work/task/codex-home",
            "imageRef": "moonmind:latest",
        }
    )

    assert isinstance(result, CodexManagedSessionHandle)
    assert result.session_state.container_id == "ctr-1"
    controller.launch_session.assert_awaited_once()


async def test_launch_session_uses_github_descriptor_from_activity_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "ghp-ambient-token")
    controller = AsyncMock()
    controller.launch_session = AsyncMock(
        return_value=CodexManagedSessionHandle(
            sessionState={
                "sessionId": "sess-1",
                "sessionEpoch": 1,
                "containerId": "ctr-1",
                "threadId": "thread-1",
            },
            status="ready",
            imageRef="moonmind:latest",
        )
    )
    activities = TemporalAgentRuntimeActivities(session_controller=controller)

    await activities.agent_runtime_launch_session(
        {
            "taskRunId": "task-1",
            "sessionId": "sess-1",
            "threadId": "thread-1",
            "workspacePath": "/work/task/repo",
            "sessionWorkspacePath": "/work/task/session",
            "artifactSpoolPath": "/work/task/artifacts",
            "codexHomePath": "/work/task/codex-home",
            "imageRef": "moonmind:latest",
            "environment": {"PATH": "/usr/bin"},
            "workspaceSpec": {"repository": "MoonLadderStudios/private-repo"},
        }
    )

    launched_request = controller.launch_session.await_args.args[0]
    assert launched_request.environment["PATH"] == "/usr/bin"
    assert "GITHUB_TOKEN" not in launched_request.environment
    assert "GIT_TERMINAL_PROMPT" not in launched_request.environment
    assert launched_request.github_credential is not None
    assert launched_request.github_credential.source == "environment"
    assert launched_request.github_credential.env_var == "GITHUB_TOKEN"


async def test_launch_session_preserves_request_scoped_github_token_for_controller() -> None:
    token = "ghp_request_scoped_token_12345678901234567890"
    controller = AsyncMock()
    controller.launch_session = AsyncMock(
        return_value=CodexManagedSessionHandle(
            sessionState={
                "sessionId": "sess-1",
                "sessionEpoch": 1,
                "containerId": "ctr-1",
                "threadId": "thread-1",
            },
            status="ready",
            imageRef="moonmind:latest",
        )
    )
    activities = TemporalAgentRuntimeActivities(session_controller=controller)

    await activities.agent_runtime_launch_session(
        {
            "taskRunId": "task-1",
            "sessionId": "sess-1",
            "threadId": "thread-1",
            "workspacePath": "/work/task/repo",
            "sessionWorkspacePath": "/work/task/session",
            "artifactSpoolPath": "/work/task/artifacts",
            "codexHomePath": "/work/task/codex-home",
            "imageRef": "moonmind:latest",
            "environment": {"GITHUB_TOKEN": token},
            "workspaceSpec": {"repository": "MoonLadderStudios/private-repo"},
        }
    )

    launched_request = controller.launch_session.await_args.args[0]
    assert launched_request.environment["GITHUB_TOKEN"] == token
    assert "GIT_TERMINAL_PROMPT" not in launched_request.environment
    assert launched_request.github_credential is not None
    assert launched_request.github_credential.source == "environment"
    assert launched_request.github_credential.env_var == "GITHUB_TOKEN"


async def test_launch_session_injects_moonmind_url_from_activity_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MOONMIND_URL", "http://api:8000")
    controller = AsyncMock()
    controller.launch_session = AsyncMock(
        return_value=CodexManagedSessionHandle(
            sessionState={
                "sessionId": "sess-1",
                "sessionEpoch": 1,
                "containerId": "ctr-1",
                "threadId": "thread-1",
            },
            status="ready",
            imageRef="moonmind:latest",
        )
    )
    activities = TemporalAgentRuntimeActivities(session_controller=controller)

    await activities.agent_runtime_launch_session(
        {
            "taskRunId": "task-1",
            "sessionId": "sess-1",
            "threadId": "thread-1",
            "workspacePath": "/work/task/repo",
            "sessionWorkspacePath": "/work/task/session",
            "artifactSpoolPath": "/work/task/artifacts",
            "codexHomePath": "/work/task/codex-home",
            "imageRef": "moonmind:latest",
            "environment": {"PATH": "/usr/bin"},
        }
    )

    launched_request = controller.launch_session.await_args.args[0]
    assert launched_request.environment["PATH"] == "/usr/bin"
    assert launched_request.environment["MOONMIND_URL"] == "http://api:8000"


async def test_launch_session_uses_github_descriptor_for_managed_secret_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    from moonmind.config.settings import settings as app_settings

    monkeypatch.setattr(app_settings.github, "github_token_secret_ref", None)
    controller = AsyncMock()
    controller.launch_session = AsyncMock(
        return_value=CodexManagedSessionHandle(
            sessionState={
                "sessionId": "sess-1",
                "sessionEpoch": 1,
                "containerId": "ctr-1",
                "threadId": "thread-1",
            },
            status="ready",
            imageRef="moonmind:latest",
        )
    )
    activities = TemporalAgentRuntimeActivities(session_controller=controller)

    await activities.agent_runtime_launch_session(
        {
            "taskRunId": "task-1",
            "sessionId": "sess-1",
            "threadId": "thread-1",
            "workspacePath": "/work/task/repo",
            "sessionWorkspacePath": "/work/task/session",
            "artifactSpoolPath": "/work/task/artifacts",
            "codexHomePath": "/work/task/codex-home",
            "imageRef": "moonmind:latest",
            "workspaceSpec": {"repository": "MoonLadderStudios/private-repo"},
        }
    )

    launched_request = controller.launch_session.await_args.args[0]
    assert "GITHUB_TOKEN" not in launched_request.environment
    assert "GIT_TERMINAL_PROMPT" not in launched_request.environment
    assert launched_request.github_credential is not None
    assert launched_request.github_credential.source == "managed_secret"
    assert launched_request.github_credential.required is False


async def test_launch_session_preserves_explicit_github_secret_ref_descriptor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from moonmind.config.settings import settings as app_settings

    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(
        app_settings.github,
        "github_token_secret_ref",
        "env://MM320_GITHUB_PAT",
    )
    controller = AsyncMock()
    controller.launch_session = AsyncMock(
        return_value=CodexManagedSessionHandle(
            sessionState={
                "sessionId": "sess-1",
                "sessionEpoch": 1,
                "containerId": "ctr-1",
                "threadId": "thread-1",
            },
            status="ready",
            imageRef="moonmind:latest",
        )
    )
    activities = TemporalAgentRuntimeActivities(session_controller=controller)

    await activities.agent_runtime_launch_session(
        {
            "taskRunId": "task-1",
            "sessionId": "sess-1",
            "threadId": "thread-1",
            "workspacePath": "/work/task/repo",
            "sessionWorkspacePath": "/work/task/session",
            "artifactSpoolPath": "/work/task/artifacts",
            "codexHomePath": "/work/task/codex-home",
            "imageRef": "moonmind:latest",
            "workspaceSpec": {"repository": "MoonLadderStudios/private-repo"},
        }
    )

    launched_request = controller.launch_session.await_args.args[0]
    assert launched_request.github_credential is not None
    assert launched_request.github_credential.source == "secret_ref"
    assert (
        launched_request.github_credential.secret_ref
        == "env://MM320_GITHUB_PAT"
    )
    assert "GITHUB_TOKEN" not in launched_request.environment


async def test_launch_session_redacts_github_token_in_failure_details() -> None:
    controller = AsyncMock()
    controller.launch_session = AsyncMock(
        side_effect=RuntimeError(
            "docker run -e GITHUB_TOKEN=ghp_inline_secret_token_12345678901234567890 failed"
        )
    )
    activities = TemporalAgentRuntimeActivities(session_controller=controller)

    with pytest.raises(
        TemporalActivityRuntimeError,
        match="agent_runtime\\.launch_session failed:",
    ) as exc_info:
        await activities.agent_runtime_launch_session(
            {
                "taskRunId": "task-1",
                "sessionId": "sess-1",
                "threadId": "thread-1",
                "workspacePath": "/work/task/repo",
                "sessionWorkspacePath": "/work/task/session",
                "artifactSpoolPath": "/work/task/artifacts",
                "codexHomePath": "/work/task/codex-home",
                "imageRef": "moonmind:latest",
                "environment": {"PATH": "/usr/bin"},
            }
        )

    message = str(exc_info.value)
    assert "ghp_inline_secret_token_12345678901234567890" not in message
    assert "[REDACTED]" in message


async def test_launch_session_materializes_profile_into_request_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-123")
    controller = AsyncMock()
    controller.launch_session = AsyncMock(
        return_value=CodexManagedSessionHandle(
            sessionState={
                "sessionId": "sess-1",
                "sessionEpoch": 1,
                "containerId": "ctr-1",
                "threadId": "thread-1",
            },
            status="ready",
            imageRef="moonmind:latest",
        )
    )
    activities = TemporalAgentRuntimeActivities(session_controller=controller)
    codex_home_path = tmp_path / "task-1" / ".moonmind" / "codex-home"

    await activities.agent_runtime_launch_session(
        {
            "request": {
                "taskRunId": "task-1",
                "sessionId": "sess-1",
                "threadId": "thread-1",
                "workspacePath": str(tmp_path / "task-1" / "repo"),
                "sessionWorkspacePath": str(tmp_path / "task-1" / "session"),
                "artifactSpoolPath": str(tmp_path / "task-1" / "artifacts"),
                "codexHomePath": str(codex_home_path),
                "imageRef": "moonmind:latest",
                "environment": {"MANAGED_ACCOUNT_LABEL": "Codex CLI via OpenRouter"},
            },
            "profile": {
                "runtimeId": "codex_cli",
                "profileId": "codex_openrouter_qwen36_plus",
                "providerId": "openrouter",
                "credentialSource": "secret_ref",
                "envTemplate": {
                    "OPENAI_BASE_URL": "https://openrouter.ai/api/v1",
                    "OPENROUTER_API_KEY": {
                        "from_secret_ref": "provider_api_key"
                    },
                },
                "secretRefs": {
                    "provider_api_key": "env://OPENROUTER_API_KEY"
                },
                "homePathOverrides": {
                    "CODEX_HOME": "{{runtime_support_dir}}/codex-home"
                },
                "fileTemplates": [
                    {
                        "path": "{{runtime_support_dir}}/codex-home/config.toml",
                        "contentTemplate": {"model": "qwen/qwen3.6-plus"},
                        "format": "toml",
                    }
                ],
            },
        }
    )

    launched_request = controller.launch_session.await_args.args[0]
    assert launched_request.environment["OPENROUTER_API_KEY"] == "sk-or-123"
    assert launched_request.environment["OPENAI_BASE_URL"] == "https://openrouter.ai/api/v1"
    assert launched_request.environment["CODEX_HOME"] == str(codex_home_path)
    assert launched_request.environment["MANAGED_ACCOUNT_LABEL"] == "Codex CLI via OpenRouter"
    assert (codex_home_path / "config.toml").is_file()
    assert "qwen/qwen3.6-plus" in (codex_home_path / "config.toml").read_text(
        encoding="utf-8"
    )


async def test_launch_session_returns_safe_auth_diagnostics_metadata(
    tmp_path: Path,
) -> None:
    controller = AsyncMock()
    controller.launch_session = AsyncMock(
        return_value=CodexManagedSessionHandle(
            sessionState={
                "sessionId": "sess-1",
                "sessionEpoch": 1,
                "containerId": "ctr-1",
                "threadId": "thread-1",
            },
            status="ready",
            imageRef="moonmind:latest",
            metadata={"vendorThreadId": "vendor-thread-1"},
        )
    )
    activities = TemporalAgentRuntimeActivities(session_controller=controller)
    codex_home_path = tmp_path / "task-1" / ".moonmind" / "codex-home"

    result = await activities.agent_runtime_launch_session(
        {
            "request": {
                "taskRunId": "task-1",
                "sessionId": "sess-1",
                "threadId": "thread-1",
                "workspacePath": str(tmp_path / "task-1" / "repo"),
                "sessionWorkspacePath": str(tmp_path / "task-1" / "session"),
                "artifactSpoolPath": str(tmp_path / "task-1" / "artifacts"),
                "codexHomePath": str(codex_home_path),
                "imageRef": "moonmind:latest",
                "environment": {"MANAGED_AUTH_VOLUME_PATH": "/home/app/.codex-auth"},
            },
            "profile": {
                "runtimeId": "codex_cli",
                "profileId": "codex-oauth",
                "providerId": "openai",
                "credentialSource": "oauth_volume",
                "runtimeMaterializationMode": "oauth_home",
                "volumeRef": "codex_auth_volume",
                "volumeMountPath": "/home/app/.codex-auth",
            },
        }
    )

    diagnostics = result.metadata["authDiagnostics"]
    assert result.metadata["vendorThreadId"] == "vendor-thread-1"
    assert diagnostics == {
        "component": "managed_session_controller",
        "readiness": "ready",
        "profileRef": "codex-oauth",
        "runtimeId": "codex_cli",
        "providerId": "openai",
        "credentialSource": "oauth_volume",
        "runtimeMaterializationMode": "oauth_home",
        "volumeRef": "codex_auth_volume",
        "authMountTarget": "/home/app/.codex-auth",
        "codexHomePath": str(codex_home_path),
    }
    assert "auth.json" not in str(result.metadata)
    assert "token=" not in str(result.metadata)


async def test_launch_session_accepts_mapping_response_before_auth_diagnostics(
    tmp_path: Path,
) -> None:
    controller = AsyncMock()
    controller.launch_session = AsyncMock(
        return_value={
            "sessionState": {
                "sessionId": "sess-1",
                "sessionEpoch": 1,
                "containerId": "ctr-1",
                "threadId": "thread-1",
            },
            "status": "ready",
            "imageRef": "moonmind:latest",
            "metadata": {"vendorThreadId": "vendor-thread-1"},
        }
    )
    activities = TemporalAgentRuntimeActivities(session_controller=controller)
    codex_home_path = tmp_path / "task-1" / ".moonmind" / "codex-home"

    result = await activities.agent_runtime_launch_session(
        {
            "request": {
                "taskRunId": "task-1",
                "sessionId": "sess-1",
                "threadId": "thread-1",
                "workspacePath": str(tmp_path / "task-1" / "repo"),
                "sessionWorkspacePath": str(tmp_path / "task-1" / "session"),
                "artifactSpoolPath": str(tmp_path / "task-1" / "artifacts"),
                "codexHomePath": str(codex_home_path),
                "imageRef": "moonmind:latest",
            },
            "profile": {
                "runtimeId": "codex_cli",
                "profileId": "codex-oauth",
                "providerId": "openai",
                "credentialSource": "oauth_volume",
                "runtimeMaterializationMode": "oauth_home",
                "volumeRef": "codex_auth_volume",
                "volumeMountPath": "/home/app/.codex-auth",
            },
        }
    )

    assert isinstance(result, CodexManagedSessionHandle)
    assert result.metadata["vendorThreadId"] == "vendor-thread-1"
    assert result.metadata["authDiagnostics"]["profileRef"] == "codex-oauth"
    assert result.metadata["authDiagnostics"]["readiness"] == "ready"


async def test_launch_session_failure_reports_sanitized_auth_diagnostics(
    tmp_path: Path,
) -> None:
    controller = AsyncMock()
    controller.launch_session = AsyncMock(
        side_effect=RuntimeError(
            "MANAGED_AUTH_VOLUME_PATH /home/app/.codex-auth/auth.json token=sk-test-secret failed"
        )
    )
    activities = TemporalAgentRuntimeActivities(session_controller=controller)

    with pytest.raises(
        TemporalActivityRuntimeError,
        match="component=managed_session_controller",
    ) as exc_info:
        await activities.agent_runtime_launch_session(
            {
                "request": {
                    "taskRunId": "task-1",
                    "sessionId": "sess-1",
                    "threadId": "thread-1",
                    "workspacePath": str(tmp_path / "task-1" / "repo"),
                    "sessionWorkspacePath": str(tmp_path / "task-1" / "session"),
                    "artifactSpoolPath": str(tmp_path / "task-1" / "artifacts"),
                    "codexHomePath": str(
                        tmp_path / "task-1" / ".moonmind" / "codex-home"
                    ),
                    "imageRef": "moonmind:latest",
                    "environment": {"MANAGED_AUTH_VOLUME_PATH": "/home/app/.codex-auth"},
                },
                "profile": {
                    "runtimeId": "codex_cli",
                    "profileId": "codex-oauth",
                    "providerId": "openai",
                    "credentialSource": "oauth_volume",
                    "runtimeMaterializationMode": "oauth_home",
                    "volumeRef": "codex_auth_volume",
                    "volumeMountPath": "/home/app/.codex-auth",
                },
            }
        )

    message = str(exc_info.value)
    assert "sk-test-secret" not in message
    assert "/home/app/.codex-auth/auth.json" not in message
    assert "[REDACTED]" in message
    assert "[REDACTED_AUTH_PATH]" in message


async def test_launch_session_rejects_structured_secret_ref_values(
    tmp_path: Path,
) -> None:
    controller = AsyncMock()
    controller.launch_session = AsyncMock()
    activities = TemporalAgentRuntimeActivities(session_controller=controller)

    with pytest.raises(
        ValueError,
        match="profile.secretRefs.provider_api_key must be a string secret reference",
    ):
        await activities.agent_runtime_launch_session(
            {
                "request": {
                    "taskRunId": "task-1",
                    "sessionId": "sess-1",
                    "threadId": "thread-1",
                    "workspacePath": str(tmp_path / "task-1" / "repo"),
                    "sessionWorkspacePath": str(tmp_path / "task-1" / "session"),
                    "artifactSpoolPath": str(tmp_path / "task-1" / "artifacts"),
                    "codexHomePath": str(
                        tmp_path / "task-1" / ".moonmind" / "codex-home"
                    ),
                    "imageRef": "moonmind:latest",
                },
                "profile": {
                    "runtimeId": "codex_cli",
                    "envTemplate": {
                        "OPENROUTER_API_KEY": {
                            "from_secret_ref": "provider_api_key"
                        }
                    },
                    "secretRefs": {
                        "provider_api_key": {
                            "ref": "env://OPENROUTER_API_KEY"
                        }
                    },
                },
            }
        )

    controller.launch_session.assert_not_awaited()


async def test_load_session_snapshot_queries_session_workflow_via_client_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow_handle = AsyncMock()
    workflow_handle.query = AsyncMock(
        return_value=CodexManagedSessionSnapshot(
            binding=CodexManagedSessionBinding(
                workflowId="wf-task-1:session:codex_cli",
                taskRunId="wf-task-1",
                sessionId="sess:wf-task-1:codex_cli",
                sessionEpoch=1,
                runtimeId="codex_cli",
                executionProfileRef="codex-default",
            ),
            status="active",
            containerId="ctr-1",
            threadId="thread-1",
            activeTurnId=None,
            terminationRequested=False,
        ).model_dump(mode="json", by_alias=True)
    )
    created_adapters: list[object] = []

    class _FakeTemporalClientAdapter:
        def __init__(self) -> None:
            created_adapters.append(self)

        async def get_workflow_handle(self, workflow_id: str) -> AsyncMock:
            assert workflow_id == "wf-task-1:session:codex_cli"
            return workflow_handle

    monkeypatch.setattr(
        temporal_client_module,
        "TemporalClientAdapter",
        _FakeTemporalClientAdapter,
    )

    activities = TemporalAgentRuntimeActivities()
    result = await activities.agent_runtime_load_session_snapshot(
        {
            "workflowId": "wf-task-1:session:codex_cli",
            "taskRunId": "wf-task-1",
            "sessionId": "sess:wf-task-1:codex_cli",
            "sessionEpoch": 1,
            "runtimeId": "codex_cli",
            "executionProfileRef": "codex-default",
        }
    )

    assert isinstance(result, CodexManagedSessionSnapshot)
    assert result.binding.workflow_id == "wf-task-1:session:codex_cli"
    assert result.container_id == "ctr-1"
    assert len(created_adapters) == 1
    workflow_handle.query.assert_awaited_once_with("get_status")


async def test_load_session_snapshot_reuses_client_adapter_across_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow_handle = AsyncMock()
    workflow_handle.query = AsyncMock(
        return_value=CodexManagedSessionSnapshot(
            binding=CodexManagedSessionBinding(
                workflowId="wf-task-1:session:codex_cli",
                taskRunId="wf-task-1",
                sessionId="sess:wf-task-1:codex_cli",
                sessionEpoch=1,
                runtimeId="codex_cli",
                executionProfileRef="codex-default",
            ),
            status="active",
            containerId="ctr-1",
            threadId="thread-1",
            activeTurnId=None,
            terminationRequested=False,
        ).model_dump(mode="json", by_alias=True)
    )
    created_adapters: list[object] = []

    class _FakeTemporalClientAdapter:
        def __init__(self) -> None:
            created_adapters.append(self)

        async def get_workflow_handle(self, workflow_id: str) -> AsyncMock:
            assert workflow_id == "wf-task-1:session:codex_cli"
            return workflow_handle

    monkeypatch.setattr(
        temporal_client_module,
        "TemporalClientAdapter",
        _FakeTemporalClientAdapter,
    )

    activities = TemporalAgentRuntimeActivities()
    payload = {
        "workflowId": "wf-task-1:session:codex_cli",
        "taskRunId": "wf-task-1",
        "sessionId": "sess:wf-task-1:codex_cli",
        "sessionEpoch": 1,
        "runtimeId": "codex_cli",
        "executionProfileRef": "codex-default",
    }

    await activities.agent_runtime_load_session_snapshot(payload)
    await activities.agent_runtime_load_session_snapshot(payload)

    assert len(created_adapters) == 1
    assert workflow_handle.query.await_count == 2


async def test_session_status_delegates_to_remote_session_controller() -> None:
    controller = AsyncMock()
    controller.session_status = AsyncMock(
        return_value=CodexManagedSessionHandle(
            sessionState={
                "sessionId": "sess-1",
                "sessionEpoch": 1,
                "containerId": "ctr-1",
                "threadId": "thread-1",
            },
            status="busy",
        )
    )
    activities = TemporalAgentRuntimeActivities(session_controller=controller)

    result = await activities.agent_runtime_session_status(
        {
            "sessionId": "sess-1",
            "sessionEpoch": 1,
            "containerId": "ctr-1",
            "threadId": "thread-1",
        }
    )

    assert isinstance(result, CodexManagedSessionHandle)
    assert result.status == "busy"


async def test_send_turn_accepts_base_model_payloads_and_preserves_concrete_type() -> None:
    class _SendTurnEnvelope(BaseModel):
        session_id: str
        session_epoch: int
        container_id: str
        thread_id: str
        instructions: str

    controller = AsyncMock()
    controller.send_turn = AsyncMock(
        return_value=CodexManagedSessionTurnResponse(
            sessionState={
                "sessionId": "sess-1",
                "sessionEpoch": 1,
                "containerId": "ctr-1",
                "threadId": "thread-1",
                "activeTurnId": "turn-1",
            },
            turnId="turn-1",
            status="running",
        )
    )
    activities = TemporalAgentRuntimeActivities(session_controller=controller)

    result = await activities.agent_runtime_send_turn(
        _SendTurnEnvelope(
            session_id="sess-1",
            session_epoch=1,
            container_id="ctr-1",
            thread_id="thread-1",
            instructions="Inspect the workspace",
        )
    )

    assert isinstance(result, CodexManagedSessionTurnResponse)
    validated_request = controller.send_turn.await_args.args[0]
    assert validated_request.__class__.__name__ == "SendCodexManagedSessionTurnRequest"
    assert validated_request.instructions == "Inspect the workspace"
    assert result.turn_id == "turn-1"


async def test_send_turn_delegates_to_remote_session_controller() -> None:
    controller = AsyncMock()
    controller.send_turn = AsyncMock(
        return_value=CodexManagedSessionTurnResponse(
            sessionState={
                "sessionId": "sess-1",
                "sessionEpoch": 1,
                "containerId": "ctr-1",
                "threadId": "thread-1",
                "activeTurnId": "turn-1",
            },
            turnId="turn-1",
            status="running",
        )
    )
    activities = TemporalAgentRuntimeActivities(session_controller=controller)

    result = await activities.agent_runtime_send_turn(
        {
            "sessionId": "sess-1",
            "sessionEpoch": 1,
            "containerId": "ctr-1",
            "threadId": "thread-1",
            "instructions": "Inspect the workspace",
        }
    )

    assert isinstance(result, CodexManagedSessionTurnResponse)
    assert result.turn_id == "turn-1"


async def test_send_turn_heartbeats_while_waiting_for_remote_session_controller(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from temporalio import activity as temporal_activity

    heartbeats: list[dict[str, Any]] = []

    async def _slow_send_turn(
        _request: Any,
    ) -> CodexManagedSessionTurnResponse:
        await asyncio.sleep(0.03)
        return CodexManagedSessionTurnResponse(
            sessionState={
                "sessionId": "sess-1",
                "sessionEpoch": 1,
                "containerId": "ctr-1",
                "threadId": "thread-1",
                "activeTurnId": "turn-1",
            },
            turnId="turn-1",
            status="completed",
        )

    monkeypatch.setattr(
        activity_runtime_module,
        "_SESSION_CONTROLLER_HEARTBEAT_INTERVAL_SECONDS",
        0.01,
    )
    monkeypatch.setattr(temporal_activity, "in_activity", lambda: True)
    monkeypatch.setattr(temporal_activity, "heartbeat", heartbeats.append)

    controller = AsyncMock()
    controller.send_turn = AsyncMock(side_effect=_slow_send_turn)
    activities = TemporalAgentRuntimeActivities(session_controller=controller)

    result = await activities.agent_runtime_send_turn(
        {
            "sessionId": "sess-1",
            "sessionEpoch": 1,
            "containerId": "ctr-1",
            "threadId": "thread-1",
            "instructions": "Inspect the workspace",
        }
    )

    assert isinstance(result, CodexManagedSessionTurnResponse)
    assert result.status == "completed"
    assert heartbeats
    assert all(
        heartbeat["activityType"] == "agent_runtime.send_turn"
        for heartbeat in heartbeats
    )


async def test_await_with_activity_heartbeats_accepts_existing_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from temporalio import activity as temporal_activity

    monkeypatch.setattr(temporal_activity, "in_activity", lambda: False)

    async def _complete() -> str:
        await asyncio.sleep(0)
        return "done"

    task = asyncio.create_task(_complete())
    result = await activity_runtime_module._await_with_activity_heartbeats(
        task,
        heartbeat_payload={"activityType": "agent_runtime.send_turn"},
    )

    assert result == "done"


async def test_steer_turn_delegates_to_remote_session_controller() -> None:
    controller = AsyncMock()
    controller.steer_turn = AsyncMock(
        return_value=CodexManagedSessionTurnResponse(
            sessionState={
                "sessionId": "sess-1",
                "sessionEpoch": 1,
                "containerId": "ctr-1",
                "threadId": "thread-1",
                "activeTurnId": "turn-1",
            },
            turnId="turn-1",
            status="running",
        )
    )
    activities = TemporalAgentRuntimeActivities(session_controller=controller)

    result = await activities.agent_runtime_steer_turn(
        {
            "sessionId": "sess-1",
            "sessionEpoch": 1,
            "containerId": "ctr-1",
            "threadId": "thread-1",
            "turnId": "turn-1",
            "instructions": "Focus on the failing test",
        }
    )

    assert isinstance(result, CodexManagedSessionTurnResponse)
    assert result.status == "running"


async def test_interrupt_turn_delegates_to_remote_session_controller() -> None:
    controller = AsyncMock()
    controller.interrupt_turn = AsyncMock(
        return_value=CodexManagedSessionTurnResponse(
            sessionState={
                "sessionId": "sess-1",
                "sessionEpoch": 1,
                "containerId": "ctr-1",
                "threadId": "thread-1",
            },
            turnId="turn-1",
            status="interrupted",
        )
    )
    activities = TemporalAgentRuntimeActivities(session_controller=controller)

    result = await activities.agent_runtime_interrupt_turn(
        {
            "sessionId": "sess-1",
            "sessionEpoch": 1,
            "containerId": "ctr-1",
            "threadId": "thread-1",
            "turnId": "turn-1",
        }
    )

    assert isinstance(result, CodexManagedSessionTurnResponse)
    assert result.status == "interrupted"


async def test_clear_session_delegates_to_remote_session_controller() -> None:
    controller = AsyncMock()
    controller.clear_session = AsyncMock(
        return_value=CodexManagedSessionHandle(
            sessionState={
                "sessionId": "sess-1",
                "sessionEpoch": 2,
                "containerId": "ctr-1",
                "threadId": "thread-2",
            },
            status="ready",
        )
    )
    activities = TemporalAgentRuntimeActivities(session_controller=controller)

    result = await activities.agent_runtime_clear_session(
        {
            "sessionId": "sess-1",
            "sessionEpoch": 1,
            "containerId": "ctr-1",
            "threadId": "thread-1",
            "newThreadId": "thread-2",
        }
    )

    assert isinstance(result, CodexManagedSessionHandle)
    assert result.session_state.session_epoch == 2


async def test_terminate_session_delegates_to_remote_session_controller() -> None:
    controller = AsyncMock()
    controller.terminate_session = AsyncMock(
        return_value=CodexManagedSessionHandle(
            sessionState={
                "sessionId": "sess-1",
                "sessionEpoch": 2,
                "containerId": "ctr-1",
                "threadId": "thread-2",
            },
            status="terminated",
        )
    )
    activities = TemporalAgentRuntimeActivities(session_controller=controller)

    result = await activities.agent_runtime_terminate_session(
        {
            "sessionId": "sess-1",
            "sessionEpoch": 2,
            "containerId": "ctr-1",
            "threadId": "thread-2",
        }
    )

    assert isinstance(result, CodexManagedSessionHandle)
    assert result.status == "terminated"


async def test_fetch_session_summary_delegates_to_remote_session_controller() -> None:
    controller = AsyncMock()
    controller.fetch_session_summary = AsyncMock(
        return_value=CodexManagedSessionSummary(
            sessionState={
                "sessionId": "sess-1",
                "sessionEpoch": 2,
                "containerId": "ctr-1",
                "threadId": "thread-2",
            },
            latestSummaryRef="art-summary",
            latestCheckpointRef="art-checkpoint",
            latestControlEventRef="art-control",
        )
    )
    activities = TemporalAgentRuntimeActivities(session_controller=controller)

    result = await activities.agent_runtime_fetch_session_summary(
        {
            "sessionId": "sess-1",
            "sessionEpoch": 2,
            "containerId": "ctr-1",
            "threadId": "thread-2",
        }
    )

    assert isinstance(result, CodexManagedSessionSummary)
    assert result.latest_summary_ref == "art-summary"
    assert result.latest_checkpoint_ref == "art-checkpoint"
    assert result.latest_control_event_ref == "art-control"


async def test_publish_session_artifacts_delegates_to_remote_session_controller() -> None:
    controller = AsyncMock()
    controller.publish_session_artifacts = AsyncMock(
        return_value=CodexManagedSessionArtifactsPublication(
            sessionState={
                "sessionId": "sess-1",
                "sessionEpoch": 2,
                "containerId": "ctr-1",
                "threadId": "thread-2",
            },
            publishedArtifactRefs=("art-summary", "art-checkpoint"),
            latestSummaryRef="art-summary",
            latestCheckpointRef="art-checkpoint",
            latestControlEventRef="art-control",
        )
    )
    activities = TemporalAgentRuntimeActivities(session_controller=controller)

    result = await activities.agent_runtime_publish_session_artifacts(
        {
            "sessionId": "sess-1",
            "sessionEpoch": 2,
            "containerId": "ctr-1",
            "threadId": "thread-2",
        }
    )

    assert isinstance(result, CodexManagedSessionArtifactsPublication)
    assert result.published_artifact_refs == ("art-summary", "art-checkpoint")
    assert result.latest_checkpoint_ref == "art-checkpoint"
    assert result.latest_control_event_ref == "art-control"


# ---------------------------------------------------------------------------
# T5: agent_runtime_fetch_result — typed AgentRunResult return
# ---------------------------------------------------------------------------


async def test_fetch_result_completed_returns_typed_model(tmp_path: Path) -> None:
    """T5.1 — completed run returns typed AgentRunResult with failure_class=None."""
    store = _make_store(tmp_path)
    _save_record(store, run_id="fr-1", status="completed")

    activities = TemporalAgentRuntimeActivities(run_store=store)
    result = await activities.agent_runtime_fetch_result({"run_id": "fr-1"})

    assert isinstance(result, AgentRunResult), f"Expected AgentRunResult, got {type(result)}"
    assert result.failure_class is None


async def test_fetch_result_failed_returns_typed_model(tmp_path: Path) -> None:
    """T5.2 — failed run returns typed AgentRunResult with correct failure_class."""
    store = _make_store(tmp_path)
    _save_record(
        store,
        run_id="fr-2",
        status="failed",
        failure_class="execution_error",
        error_message="Process exited with code 1",
    )

    activities = TemporalAgentRuntimeActivities(run_store=store)
    result = await activities.agent_runtime_fetch_result({"run_id": "fr-2"})

    assert isinstance(result, AgentRunResult)
    assert result.failure_class == "execution_error"


async def test_fetch_result_forwards_pr_resolver_expected_flag(tmp_path: Path) -> None:
    """T5.3 — pr-resolver expectation reaches the managed adapter."""
    from unittest.mock import patch

    store = _make_store(tmp_path)
    _save_record(store, run_id="fr-pr", status="completed")

    activities = TemporalAgentRuntimeActivities(run_store=store)
    with patch(
        "moonmind.workflows.temporal.activity_runtime.ManagedAgentAdapter",
        autospec=True,
    ) as mock_adapter_cls:
        adapter = mock_adapter_cls.return_value
        adapter.fetch_result = AsyncMock(
            return_value=AgentRunResult(
                summary="blocked",
                failure_class="user_error",
            )
        )

        result = await activities.agent_runtime_fetch_result(
            {"run_id": "fr-pr", "pr_resolver_expected": True}
        )

        adapter.fetch_result.assert_awaited_once_with(
            "fr-pr", pr_resolver_expected=True
        )
        assert result.failure_class == "user_error"


async def test_fetch_result_reverifies_and_clears_pr_not_found_when_merged(
    tmp_path: Path,
) -> None:
    """Regression: when pr-resolver reports pr_not_found but the PR is
    actually merged on GitHub, the activity must re-verify and clear
    the failure rather than surfacing execution_error.

    Guards against the managed-session auth gap where gh inside the
    codex container can't authenticate and misreports pr_not_found.
    """
    from unittest.mock import patch

    store = _make_store(tmp_path)
    _save_record(store, run_id="fr-reverify", status="completed")

    activities = TemporalAgentRuntimeActivities(run_store=store)
    with patch(
        "moonmind.workflows.temporal.activity_runtime.ManagedAgentAdapter",
        autospec=True,
    ) as mock_adapter_cls:
        adapter = mock_adapter_cls.return_value
        adapter.fetch_result = AsyncMock(
            return_value=AgentRunResult(
                summary=(
                    "pr-resolver reported status 'failed'; pr_not_found; "
                    "next_step=manual_review"
                ),
                failure_class="execution_error",
            )
        )

        with patch.object(
            activities,
            "_reverify_pr_merged_state",
            return_value={
                "number": 1543,
                "state": "MERGED",
                "url": "https://github.com/org/repo/pull/1543",
                "mergedAt": "2026-04-17T23:48:24Z",
            },
        ) as mock_reverify:
            result = await activities.agent_runtime_fetch_result(
                {
                    "run_id": "fr-reverify",
                    "pr_resolver_expected": True,
                    "target_branch": "main",
                    "head_branch": "mm-398-e3573b0c",
                }
            )

    mock_reverify.assert_called_once_with(
        run_id="fr-reverify",
        head_branch="mm-398-e3573b0c",
        base_branch="main",
    )
    assert result.failure_class is None, (
        "PR re-verified as merged: failure_class must be cleared"
    )
    assert "#1543" in (result.summary or "")
    assert result.metadata.get("prResolverReverified") is True
    assert result.metadata.get("mergeAutomationDisposition") == "already_merged"
    assert (
        result.metadata.get("pull_request_url")
        == "https://github.com/org/repo/pull/1543"
    )
    assert "pr_not_found" in (
        result.metadata.get("prResolverStaleSummary") or ""
    )


async def test_fetch_result_preserves_failure_when_reverify_returns_none(
    tmp_path: Path,
) -> None:
    """When re-verify does not confirm a merged PR (e.g. PR open,
    lookup failed, or no target_branch), the original failure must
    be preserved unchanged."""
    from unittest.mock import patch

    store = _make_store(tmp_path)
    _save_record(store, run_id="fr-preserve", status="completed")

    activities = TemporalAgentRuntimeActivities(run_store=store)
    original_result = AgentRunResult(
        summary=(
            "pr-resolver reported status 'failed'; pr_not_found; "
            "next_step=manual_review"
        ),
        failure_class="execution_error",
    )
    with patch(
        "moonmind.workflows.temporal.activity_runtime.ManagedAgentAdapter",
        autospec=True,
    ) as mock_adapter_cls:
        adapter = mock_adapter_cls.return_value
        adapter.fetch_result = AsyncMock(return_value=original_result)

        with patch.object(
            activities, "_reverify_pr_merged_state", return_value=None,
        ) as mock_reverify:
            result = await activities.agent_runtime_fetch_result(
                {
                    "run_id": "fr-preserve",
                    "pr_resolver_expected": True,
                    "target_branch": "main",
                    "head_branch": "mm-398-e3573b0c",
                }
            )

    mock_reverify.assert_called_once()
    assert result.failure_class == "execution_error"
    assert "pr_not_found" in (result.summary or "")
    assert result.metadata.get("prResolverReverified") is None


async def test_fetch_result_reverifies_blocked_resolver_by_pr_number_when_merged(
    tmp_path: Path,
) -> None:
    """Regression: resolver runs can omit head_branch in fetch_result input.

    When the stable run id carries the PR number and GitHub confirms that PR is
    merged, stale resolver states such as ci_running must not fail merge
    automation.
    """
    from unittest.mock import patch

    run_id = "resolver:pr:1727:head:623c3697e576:h:54dc00462b516f8d:1"
    store = _make_store(tmp_path)
    _save_record(store, run_id=run_id, status="completed")

    activities = TemporalAgentRuntimeActivities(run_store=store)
    with patch(
        "moonmind.workflows.temporal.activity_runtime.ManagedAgentAdapter",
        autospec=True,
    ) as mock_adapter_cls:
        adapter = mock_adapter_cls.return_value
        adapter.fetch_result = AsyncMock(
            return_value=AgentRunResult(
                summary=(
                    "pr-resolver reported status 'blocked'; ci_running; "
                    "next_step=retry_finalize_after_backoff"
                ),
                failure_class="user_error",
            )
        )

        with patch.object(
            activities,
            "_reverify_pr_merged_state",
            return_value={
                "number": 1727,
                "state": "MERGED",
                "url": "https://github.com/org/repo/pull/1727",
                "mergedAt": "2026-04-24T00:55:48Z",
            },
        ) as mock_reverify:
            result = await activities.agent_runtime_fetch_result(
                {
                    "run_id": run_id,
                    "pr_resolver_expected": True,
                }
            )

    mock_reverify.assert_called_once_with(
        run_id=run_id,
        head_branch=None,
        base_branch=None,
    )
    assert result.failure_class is None
    assert "#1727" in (result.summary or "")
    assert result.metadata.get("prResolverReverified") is True
    assert result.metadata.get("mergeAutomationDisposition") == "already_merged"
    assert "ci_running" in (
        result.metadata.get("prResolverStaleSummary") or ""
    )


async def test_fetch_result_skips_reverify_without_head_branch(
    tmp_path: Path,
) -> None:
    """No head_branch means re-verify has no source PR key — skip
    the call entirely rather than waste a gh subprocess invocation."""
    from unittest.mock import patch

    store = _make_store(tmp_path)
    _save_record(store, run_id="fr-skip", status="completed")

    activities = TemporalAgentRuntimeActivities(run_store=store)
    with patch(
        "moonmind.workflows.temporal.activity_runtime.ManagedAgentAdapter",
        autospec=True,
    ) as mock_adapter_cls:
        adapter = mock_adapter_cls.return_value
        adapter.fetch_result = AsyncMock(
            return_value=AgentRunResult(
                summary="pr-resolver reported status 'failed'; pr_not_found",
                failure_class="execution_error",
            )
        )

        with patch.object(
            activities, "_reverify_pr_merged_state",
        ) as mock_reverify:
            result = await activities.agent_runtime_fetch_result(
                {
                    "run_id": "fr-skip",
                    "pr_resolver_expected": True,
                    "target_branch": "main",
                }
            )

    mock_reverify.assert_not_called()
    assert result.failure_class == "execution_error"


async def test_reverify_pr_merged_state_queries_pr_number_from_run_id(
    tmp_path: Path,
) -> None:
    """The activity can recover when fetch_result omitted the head branch."""
    import subprocess
    from unittest.mock import patch

    run_id = "resolver:pr:1727:head:623c3697e576:h:54dc00462b516f8d:1"
    workspace = tmp_path / "repo"
    workspace.mkdir()
    store = _make_store(tmp_path)
    _save_record(
        store,
        run_id=run_id,
        status="completed",
        workspace_path=str(workspace),
    )
    activities = TemporalAgentRuntimeActivities(run_store=store)
    calls: list[list[str]] = []

    def _mock_run(args: list[str], **_kwargs: Any) -> subprocess.CompletedProcess:
        calls.append(args)
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout=(
                '{"number":1727,"state":"MERGED",'
                '"url":"https://github.com/org/repo/pull/1727",'
                '"mergedAt":"2026-04-24T00:55:48Z",'
                '"baseRefName":"main","headRefName":"mm-491-d125a4e3"}'
            ),
            stderr="",
        )

    with (
        patch.object(
            activities, "_detect_repo_from_workspace", return_value="org/repo",
        ),
        patch("subprocess.run", side_effect=_mock_run),
    ):
        merged_pr = activities._reverify_pr_merged_state(
            run_id=run_id,
            head_branch=None,
            base_branch=None,
        )

    assert merged_pr is not None
    assert merged_pr["number"] == 1727
    assert calls[0][:4] == ["gh", "pr", "view", "1727"]


async def test_reverify_pr_merged_state_queries_head_and_base_branch(
    tmp_path: Path,
) -> None:
    """Regression: re-verify must query the PR source branch and constrain
    by the expected base branch so a merged PR is not missed or confused
    with another PR from the same source branch."""
    import subprocess
    from unittest.mock import patch

    workspace = tmp_path / "repo"
    workspace.mkdir()
    store = _make_store(tmp_path)
    _save_record(
        store,
        run_id="fr-direct-reverify",
        status="completed",
        workspace_path=str(workspace),
    )
    activities = TemporalAgentRuntimeActivities(run_store=store)
    calls: list[list[str]] = []

    def _mock_run(args: list[str], **_kwargs: Any) -> subprocess.CompletedProcess:
        calls.append(args)
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout=(
                '[{"number":1543,"state":"MERGED",'
                '"url":"https://github.com/org/repo/pull/1543",'
                '"mergedAt":"2026-04-17T23:48:24Z",'
                '"baseRefName":"main","headRefName":"mm-398-e3573b0c"}]'
            ),
            stderr="",
        )

    with (
        patch.object(
            activities, "_detect_repo_from_workspace", return_value="org/repo",
        ),
        patch("subprocess.run", side_effect=_mock_run),
    ):
        merged_pr = activities._reverify_pr_merged_state(
            run_id="fr-direct-reverify",
            head_branch="mm-398-e3573b0c",
            base_branch="main",
        )

    assert merged_pr is not None
    assert merged_pr["number"] == 1543
    gh_args = calls[0]
    assert gh_args[gh_args.index("--head") + 1] == "mm-398-e3573b0c"
    assert gh_args[gh_args.index("--base") + 1] == "main"


async def test_reverify_pr_merged_state_rejects_malformed_json(
    tmp_path: Path,
) -> None:
    """Malformed gh stdout is a parse failure, not an unhandled activity error."""
    import subprocess
    from unittest.mock import patch

    workspace = tmp_path / "repo"
    workspace.mkdir()
    store = _make_store(tmp_path)
    _save_record(
        store,
        run_id="fr-bad-json",
        status="completed",
        workspace_path=str(workspace),
    )
    activities = TemporalAgentRuntimeActivities(run_store=store)

    with (
        patch.object(
            activities, "_detect_repo_from_workspace", return_value="org/repo",
        ),
        patch(
            "subprocess.run",
            return_value=subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout="not json",
                stderr="",
            ),
        ),
    ):
        merged_pr = activities._reverify_pr_merged_state(
            run_id="fr-bad-json",
            head_branch="feature/source",
            base_branch="main",
        )

    assert merged_pr is None


async def test_fetch_result_string_request_defaults_pr_resolver_expected_false(
    tmp_path: Path,
) -> None:
    """T5.4 — string request path must not call mapping-only accessors."""
    from unittest.mock import patch

    store = _make_store(tmp_path)
    _save_record(store, run_id="fr-string", status="completed")

    activities = TemporalAgentRuntimeActivities(run_store=store)
    with patch(
        "moonmind.workflows.temporal.activity_runtime.ManagedAgentAdapter",
        autospec=True,
    ) as mock_adapter_cls:
        adapter = mock_adapter_cls.return_value
        adapter.fetch_result = AsyncMock(return_value=AgentRunResult(summary="ok"))

        result = await activities.agent_runtime_fetch_result("fr-string")

        adapter.fetch_result.assert_awaited_once_with(
            "fr-string", pr_resolver_expected=False
        )
        assert result.summary == "ok"


async def test_fetch_result_no_record_returns_empty_typed_model(tmp_path: Path) -> None:
    """T5.5 — no record in store returns empty AgentRunResult (not None, not dict)."""
    store = _make_store(tmp_path)

    activities = TemporalAgentRuntimeActivities(run_store=store)
    result = await activities.agent_runtime_fetch_result({"run_id": "no-such"})

    assert isinstance(result, AgentRunResult)


async def test_fetch_result_missing_run_id_raises_error(tmp_path: Path) -> None:
    """T5.6 — missing run_id raises TemporalActivityRuntimeError."""
    store = _make_store(tmp_path)
    activities = TemporalAgentRuntimeActivities(run_store=store)

    with pytest.raises(TemporalActivityRuntimeError):
        await activities.agent_runtime_fetch_result({"agent_id": "codex_cli"})


# ---------------------------------------------------------------------------
# Boundary & Serialization tests
# ---------------------------------------------------------------------------

from datetime import timedelta
from temporalio import workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker, UnsandboxedWorkflowRunner

@workflow.defn(name="AgentRuntimeStatusBoundaryTest")
class AgentRuntimeStatusBoundaryTest:
    @workflow.run
    async def run(self, input_dict: dict) -> AgentRunStatus:
        return await workflow.execute_activity(
            "agent_runtime.status",
            input_dict,
            start_to_close_timeout=timedelta(minutes=1),
        )

@workflow.defn(name="AgentRuntimeFetchResultBoundaryTest")
class AgentRuntimeFetchResultBoundaryTest:
    @workflow.run
    async def run(self, input_dict: dict) -> AgentRunResult:
        return await workflow.execute_activity(
            "agent_runtime.fetch_result",
            input_dict,
            start_to_close_timeout=timedelta(minutes=1),
        )


@workflow.defn(name="AgentRuntimeBuildLaunchContextBoundaryTest")
class AgentRuntimeBuildLaunchContextBoundaryTest:
    @workflow.run
    async def run(self, input_dict: dict) -> dict[str, Any]:
        return await workflow.execute_activity(
            "agent_runtime.build_launch_context",
            input_dict,
            start_to_close_timeout=timedelta(minutes=1),
        )

@workflow.defn(name="AgentRuntimeCancelBoundaryTest")
class AgentRuntimeCancelBoundaryTest:
    @workflow.run
    async def run(self, input_dict: dict) -> AgentRunStatus:
        return await workflow.execute_activity(
            "agent_runtime.cancel",
            input_dict,
            start_to_close_timeout=timedelta(minutes=1),
        )

@workflow.defn(name="AgentRuntimePublishArtifactsBoundaryTest")
class AgentRuntimePublishArtifactsBoundaryTest:
    @workflow.run
    async def run(self, input_dict: dict) -> AgentRunResult | None:
        return await workflow.execute_activity(
            "agent_runtime.publish_artifacts",
            input_dict,
            start_to_close_timeout=timedelta(minutes=1),
        )

async def test_agent_runtime_status_temporal_boundary(tmp_path: Path) -> None:
    """Validate Temporal boundary serialization for typed Pydantic return matches contract."""
    from moonmind.workflows.temporal.activity_catalog import TemporalActivityCatalog

    store = _make_store(tmp_path)
    _save_record(store, run_id="boundary-1", status="completed")

    activities_impl = TemporalAgentRuntimeActivities(run_store=store)
    from temporalio import activity

    @activity.defn(name="agent_runtime.status")
    async def _agent_runtime_status_wrapper(request: dict) -> AgentRunStatus:
        return await activities_impl.agent_runtime_status(request)

    handlers = [_agent_runtime_status_wrapper]

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="boundary-test-queue",
            workflows=[AgentRuntimeStatusBoundaryTest],
            activities=handlers,
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            result = await env.client.execute_workflow(
                AgentRuntimeStatusBoundaryTest.run,
                {"run_id": "boundary-1", "agent_id": "codex_cli"},
                id="boundary-test-status",
                task_queue="boundary-test-queue",
            )

            assert isinstance(result, AgentRunStatus)
            assert result.status == "completed"


@pytest.mark.asyncio
async def test_agent_runtime_build_launch_context_temporal_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "ghs_test_token")
    monkeypatch.setenv("MOONMIND_ALLOW_LOCAL_ENCRYPTION_KEY_GENERATION", "1")
    activities_impl = TemporalAgentRuntimeActivities()
    from temporalio import activity

    @activity.defn(name="agent_runtime.build_launch_context")
    async def _agent_runtime_build_launch_context_wrapper(
        request: dict,
    ) -> dict[str, Any]:
        return await activities_impl.agent_runtime_build_launch_context(request)

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="boundary-test-queue-build-launch-context",
            workflows=[AgentRuntimeBuildLaunchContextBoundaryTest],
            activities=[_agent_runtime_build_launch_context_wrapper],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            result = await env.client.execute_workflow(
                AgentRuntimeBuildLaunchContextBoundaryTest.run,
                {
                    "profile": {
                        "profile_id": "proxy-prof",
                        "credential_source": "secret_ref",
                        "tags": ["proxy-first"],
                        "provider_id": "anthropic",
                        "secret_refs": {"anthropic_api_key": "db://123"},
                    },
                    "runtime_for_profile": "claude_code",
                    "workflow_id": "wf-boundary",
                    "default_credential_source": "secret_ref",
                },
                id="boundary-test-build-launch-context",
                task_queue="boundary-test-queue-build-launch-context",
            )

            assert result["profile_id"] == "proxy-prof"
            assert "MOONMIND_PROXY_TOKEN" in result["delta_env_overrides"]
            assert "GITHUB_TOKEN" in result["passthrough_env_keys"]


@pytest.mark.asyncio
async def test_agent_runtime_fetch_result_temporal_boundary(tmp_path: Path) -> None:
    from unittest.mock import patch

    store = _make_store(tmp_path)
    _save_record(store, run_id="boundary-1", status="completed")

    activities_impl = TemporalAgentRuntimeActivities(run_store=store)
    from temporalio import activity

    @activity.defn(name="agent_runtime.fetch_result")
    async def _agent_runtime_fetch_wrapper(request: dict) -> AgentRunResult:
        res = await activities_impl.agent_runtime_fetch_result(request)
        if hasattr(res, "model_copy"):
            return res.model_copy()
        return res

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="boundary-test-queue-fetch",
            workflows=[AgentRuntimeFetchResultBoundaryTest],
            activities=[_agent_runtime_fetch_wrapper],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            with patch("moonmind.workflows.temporal.activity_runtime.ManagedAgentAdapter", autospec=True) as MockAdapter:
                instance = MockAdapter.return_value
                instance.fetch_result = AsyncMock(return_value=AgentRunResult(summary="ok", failure_class=None))

                result = await env.client.execute_workflow(
                    AgentRuntimeFetchResultBoundaryTest.run,
                    {
                        "run_id": "boundary-1",
                        "agent_id": "claude",
                        "pr_resolver_expected": True,
                    },
                    id="boundary-test-fetch",
                    task_queue="boundary-test-queue-fetch",
                )

                assert isinstance(result, AgentRunResult)
                assert result.summary == "ok"
                instance.fetch_result.assert_awaited_once_with(
                    "boundary-1", pr_resolver_expected=True
                )


@pytest.mark.asyncio
async def test_agent_runtime_cancel_temporal_boundary() -> None:
    from unittest.mock import MagicMock
    mock_supervisor = AsyncMock()
    mock_supervisor.cancel = AsyncMock()
    activities_impl = TemporalAgentRuntimeActivities(
        run_store=MagicMock(),
        run_supervisor=mock_supervisor,
    )
    from temporalio import activity

    @activity.defn(name="agent_runtime.cancel")
    async def _agent_runtime_cancel_wrapper(request: dict) -> AgentRunStatus:
        return await activities_impl.agent_runtime_cancel(request)

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="boundary-test-queue-cancel",
            workflows=[AgentRuntimeCancelBoundaryTest],
            activities=[_agent_runtime_cancel_wrapper],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            result = await env.client.execute_workflow(
                AgentRuntimeCancelBoundaryTest.run,
                {"run_id": "c-1", "agent_id": "c"},
                id="boundary-test-cancel",
                task_queue="boundary-test-queue-cancel",
            )

            assert isinstance(result, AgentRunStatus)
            assert result.status == "canceled"


@pytest.mark.asyncio
async def test_agent_runtime_publish_temporal_boundary() -> None:
    from unittest.mock import MagicMock
    activities_impl = TemporalAgentRuntimeActivities(run_store=MagicMock())
    from temporalio import activity

    @activity.defn(name="agent_runtime.publish_artifacts")
    async def _agent_runtime_publish_wrapper(request: dict) -> AgentRunResult | None:
        return await activities_impl.agent_runtime_publish_artifacts(None)

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="boundary-test-queue-pub",
            workflows=[AgentRuntimePublishArtifactsBoundaryTest],
            activities=[_agent_runtime_publish_wrapper],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            result = await env.client.execute_workflow(
                AgentRuntimePublishArtifactsBoundaryTest.run,
                {},
                id="boundary-test-pub",
                task_queue="boundary-test-queue-pub",
            )

            assert result is None


@workflow.defn(name="AgentRuntimeLaunchSessionBoundaryTest")
class AgentRuntimeLaunchSessionBoundaryTest:
    @workflow.run
    async def run(self, input_dict: dict) -> CodexManagedSessionHandle:
        return await workflow.execute_activity(
            "agent_runtime.launch_session",
            input_dict,
            start_to_close_timeout=timedelta(minutes=1),
        )


@workflow.defn(name="AgentRuntimeSendTurnBoundaryTest")
class AgentRuntimeSendTurnBoundaryTest:
    @workflow.run
    async def run(self, input_dict: dict) -> CodexManagedSessionTurnResponse:
        return await workflow.execute_activity(
            "agent_runtime.send_turn",
            input_dict,
            start_to_close_timeout=timedelta(minutes=1),
        )


@workflow.defn(name="AgentRuntimePrepareTurnInstructionsBoundaryTest")
class AgentRuntimePrepareTurnInstructionsBoundaryTest:
    @workflow.run
    async def run(self, input_dict: dict) -> str:
        return await workflow.execute_activity(
            "agent_runtime.prepare_turn_instructions",
            input_dict,
            start_to_close_timeout=timedelta(minutes=1),
        )


@pytest.mark.asyncio
async def test_agent_runtime_launch_session_temporal_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from temporalio import activity

    monkeypatch.setenv("GITHUB_TOKEN", "ghs-boundary-token")
    captured_request: dict[str, Any] = {}

    async def _capture_launch_session(
        request: LaunchCodexManagedSessionRequest,
    ) -> CodexManagedSessionHandle:
        captured_request["request"] = request
        return CodexManagedSessionHandle(
            sessionState={
                "sessionId": "sess-boundary",
                "sessionEpoch": 1,
                "containerId": "ctr-boundary",
                "threadId": "thread-1",
            },
            status="ready",
            imageRef="moonmind:latest",
        )

    controller = AsyncMock()
    controller.launch_session = AsyncMock(side_effect=_capture_launch_session)
    activities_impl = TemporalAgentRuntimeActivities(session_controller=controller)

    @activity.defn(name="agent_runtime.launch_session")
    async def _agent_runtime_launch_session_wrapper(
        request: dict,
    ) -> CodexManagedSessionHandle:
        return await activities_impl.agent_runtime_launch_session(request)

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="boundary-test-queue-launch-session",
            workflows=[AgentRuntimeLaunchSessionBoundaryTest],
            activities=[_agent_runtime_launch_session_wrapper],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            result = await env.client.execute_workflow(
                AgentRuntimeLaunchSessionBoundaryTest.run,
                {
                    "taskRunId": "task-1",
                    "sessionId": "sess-boundary",
                    "threadId": "thread-1",
                    "workspacePath": "/work/task/repo",
                    "sessionWorkspacePath": "/work/task/session",
                    "artifactSpoolPath": "/work/task/artifacts",
                    "codexHomePath": "/work/task/codex-home",
                    "imageRef": "moonmind:latest",
                    "workspaceSpec": {"repository": "MoonLadderStudios/private-repo"},
                },
                id="boundary-test-launch-session",
                task_queue="boundary-test-queue-launch-session",
            )

            assert isinstance(result, CodexManagedSessionHandle)
            assert result.session_state.container_id == "ctr-boundary"
            launch_request = captured_request["request"]
            assert "GITHUB_TOKEN" not in launch_request.environment
            assert "GIT_TERMINAL_PROMPT" not in launch_request.environment
            assert launch_request.github_credential is not None
            assert launch_request.github_credential.source == "environment"
            assert launch_request.github_credential.env_var == "GITHUB_TOKEN"


@pytest.mark.asyncio
async def test_agent_runtime_send_turn_temporal_boundary() -> None:
    from temporalio import activity

    controller = AsyncMock()
    controller.send_turn = AsyncMock(
        return_value=CodexManagedSessionTurnResponse(
            sessionState={
                "sessionId": "sess-boundary",
                "sessionEpoch": 1,
                "containerId": "ctr-boundary",
                "threadId": "thread-1",
                "activeTurnId": "turn-1",
            },
            turnId="turn-1",
            status="running",
        )
    )
    activities_impl = TemporalAgentRuntimeActivities(session_controller=controller)

    @activity.defn(name="agent_runtime.send_turn")
    async def _agent_runtime_send_turn_wrapper(
        request: dict,
    ) -> CodexManagedSessionTurnResponse:
        return await activities_impl.agent_runtime_send_turn(request)

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="boundary-test-queue-send-turn",
            workflows=[AgentRuntimeSendTurnBoundaryTest],
            activities=[_agent_runtime_send_turn_wrapper],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            result = await env.client.execute_workflow(
                AgentRuntimeSendTurnBoundaryTest.run,
                {
                    "sessionId": "sess-boundary",
                    "sessionEpoch": 1,
                    "containerId": "ctr-boundary",
                    "threadId": "thread-1",
                    "instructions": "Inspect the repo state",
                },
                id="boundary-test-send-turn",
                task_queue="boundary-test-queue-send-turn",
            )

            assert isinstance(result, CodexManagedSessionTurnResponse)
            assert result.turn_id == "turn-1"


@pytest.mark.asyncio
async def test_agent_runtime_prepare_turn_instructions_injects_context(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class _FakeContextInjectionService:
        async def inject_context(
            self,
            *,
            request: Any,
            workspace_path: Path,
        ) -> None:
            assert workspace_path == tmp_path
            request.instruction_ref = "Injected context instruction"

    monkeypatch.setattr(
        "moonmind.rag.context_injection.ContextInjectionService",
        _FakeContextInjectionService,
    )
    activities = TemporalAgentRuntimeActivities()

    result = await activities.agent_runtime_prepare_turn_instructions(
        {
            "request": {
                "agentKind": "managed",
                "agentId": "codex",
                "correlationId": "corr-1",
                "idempotencyKey": "idem-1",
                "instructionRef": "artifact:instructions",
                "parameters": {"publishMode": "none"},
            },
            "workspacePath": str(tmp_path),
        }
    )

    assert result.startswith("Injected context instruction")
    assert "Managed Codex CLI note:" in result


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "skill_parameters",
    [
        {
            "selectedSkill": "jira-issue-creator",
        },
        {
            "metadata": {
                "moonmind": {
                    "selectedSkill": "jira-issue-creator",
                },
            },
        },
    ],
)
async def test_agent_runtime_prepare_turn_instructions_adds_jira_tool_hint(
    skill_parameters: dict[str, Any],
) -> None:
    activities = TemporalAgentRuntimeActivities()

    result = await activities.agent_runtime_prepare_turn_instructions(
        {
            "request": {
                "agentKind": "managed",
                "agentId": "codex",
                "correlationId": "corr-1",
                "idempotencyKey": "idem-1",
                "parameters": {
                    "instructions": "Create Jira stories from the breakdown.",
                    "publishMode": "none",
                    "storyBreakdownPath": "docs/tmp/story-breakdowns/demo/stories.json",
                    **skill_parameters,
                },
            },
        }
    )

    assert "MoonMind trusted Jira tools:" in result
    assert "docs/tmp/story-breakdowns/demo/stories.json" in result
    assert "`$MOONMIND_URL`" in result
    assert "POST $MOONMIND_URL/mcp/tools/call" in result
    assert "jira.create_issue" in result
    assert "Managed Codex CLI note:" in result


@pytest.mark.asyncio
async def test_agent_runtime_prepare_turn_instructions_adds_jira_pr_verify_tool_hint() -> None:
    activities = TemporalAgentRuntimeActivities()

    result = await activities.agent_runtime_prepare_turn_instructions(
        {
            "request": {
                "agentKind": "managed",
                "agentId": "codex",
                "correlationId": "corr-1",
                "idempotencyKey": "idem-1",
                "parameters": {
                    "instructions": "Verify KANDY-2558 against PR #635.",
                    "publishMode": "none",
                    "metadata": {
                        "moonmind": {
                            "selectedSkill": "jira-pr-verify",
                        },
                    },
                },
            },
        }
    )

    assert "MoonMind trusted Jira tools:" in result
    assert "`$MOONMIND_URL`" in result
    assert "POST $MOONMIND_URL/mcp/tools/call" in result
    assert "jira.get_issue" in result
    assert "Verify KANDY-2558 against PR #635." in result
    assert "Managed Codex CLI note:" in result


@pytest.mark.asyncio
async def test_agent_runtime_prepare_turn_instructions_adds_jira_verify_tool_hint() -> None:
    activities = TemporalAgentRuntimeActivities()

    result = await activities.agent_runtime_prepare_turn_instructions(
        {
            "request": {
                "agentKind": "managed",
                "agentId": "codex",
                "correlationId": "corr-1",
                "idempotencyKey": "idem-1",
                "parameters": {
                    "instructions": "Verify KANDY-3607 against this branch.",
                    "publishMode": "none",
                    "metadata": {
                        "moonmind": {
                            "selectedSkill": "jira-verify",
                        },
                    },
                },
            },
        }
    )

    assert "MoonMind trusted Jira tools:" in result
    assert "`$MOONMIND_URL`" in result
    assert "POST $MOONMIND_URL/mcp/tools/call" in result
    assert "jira.get_issue" in result
    assert "jira.add_comment" in result
    assert "PASS, PARTIAL, FAIL, or BLOCKED" in result
    assert "Verify KANDY-3607 against this branch." in result
    assert "Managed Codex CLI note:" in result


@pytest.mark.asyncio
async def test_agent_runtime_prepare_turn_instructions_requires_workspace_for_instruction_ref() -> None:
    activities = TemporalAgentRuntimeActivities()

    with pytest.raises(
        TemporalActivityRuntimeError,
        match=(
            "payload.workspace_path or payload.workspacePath is required "
            "when request.instructionRef is set"
        ),
    ):
        await activities.agent_runtime_prepare_turn_instructions(
            {
                "request": {
                    "agentKind": "managed",
                    "agentId": "codex",
                    "correlationId": "corr-1",
                    "idempotencyKey": "idem-1",
                    "instructionRef": "artifact:instructions",
                    "parameters": {"publishMode": "none"},
                }
            }
        )


@pytest.mark.asyncio
async def test_agent_runtime_prepare_turn_instructions_temporal_boundary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from temporalio import activity

    class _FakeContextInjectionService:
        async def inject_context(
            self,
            *,
            request: Any,
            workspace_path: Path,
        ) -> None:
            assert workspace_path == tmp_path
            request.instruction_ref = "Injected context instruction"

    monkeypatch.setattr(
        "moonmind.rag.context_injection.ContextInjectionService",
        _FakeContextInjectionService,
    )
    activities_impl = TemporalAgentRuntimeActivities()

    @activity.defn(name="agent_runtime.prepare_turn_instructions")
    async def _agent_runtime_prepare_turn_instructions_wrapper(
        request: dict,
    ) -> str:
        return await activities_impl.agent_runtime_prepare_turn_instructions(request)

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="boundary-test-queue-prepare-turn-instructions",
            workflows=[AgentRuntimePrepareTurnInstructionsBoundaryTest],
            activities=[_agent_runtime_prepare_turn_instructions_wrapper],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            result = await env.client.execute_workflow(
                AgentRuntimePrepareTurnInstructionsBoundaryTest.run,
                {
                    "request": {
                        "agentKind": "managed",
                        "agentId": "codex",
                        "correlationId": "corr-1",
                        "idempotencyKey": "idem-1",
                        "instructionRef": "artifact:instructions",
                        "parameters": {"publishMode": "none"},
                    },
                    "workspacePath": str(tmp_path),
                },
                id="boundary-test-prepare-turn-instructions",
                task_queue="boundary-test-queue-prepare-turn-instructions",
            )

            assert result.startswith("Injected context instruction")
            assert "Managed Codex CLI note:" in result


async def test_agent_runtime_reconcile_managed_sessions_returns_bounded_summary() -> None:
    class _Controller:
        async def reconcile(self) -> list[dict[str, Any]]:
            return [
                _session_record("sess-ready", status="ready"),
                _session_record("sess-stale-degraded", status="degraded"),
                _session_record("sess-orphaned-container", status="degraded"),
            ]

    activities = TemporalAgentRuntimeActivities(session_controller=_Controller())

    result = await activities.agent_runtime_reconcile_managed_sessions({})

    assert result == {
        "managedSessionRecordsReconciled": 3,
        "degradedSessionRecords": 2,
        "sessionIds": [
            "sess-ready",
            "sess-stale-degraded",
            "sess-orphaned-container",
        ],
        "truncated": False,
    }


@pytest.mark.asyncio
async def test_agent_runtime_reconcile_managed_sessions_uses_bounded_heartbeating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Controller:
        async def reconcile(self) -> list[dict[str, Any]]:
            return [
                _session_record(f"sess-{index}", status="degraded")
                for index in range(60)
            ]

    heartbeat_payloads: list[dict[str, Any]] = []

    async def _fake_await_with_activity_heartbeats(
        awaitable: Any,
        *,
        heartbeat_payload: dict[str, Any],
        interval_seconds: float | None = None,
    ) -> Any:
        del interval_seconds
        heartbeat_payloads.append(dict(heartbeat_payload))
        return await awaitable

    monkeypatch.setattr(
        activity_runtime_module,
        "_await_with_activity_heartbeats",
        _fake_await_with_activity_heartbeats,
    )
    activities = TemporalAgentRuntimeActivities(session_controller=_Controller())

    result = await activities.agent_runtime_reconcile_managed_sessions({})

    assert heartbeat_payloads == [
        {"activityType": "agent_runtime.reconcile_managed_sessions"}
    ]
    assert result["managedSessionRecordsReconciled"] == 60
    assert result["degradedSessionRecords"] == 60
    assert len(result["sessionIds"]) == 50
    assert result["truncated"] is True


async def test_agent_runtime_session_request_logs_bounded_telemetry_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log_contexts: list[dict[str, Any]] = []
    monkeypatch.setattr(
        activity_runtime_module.logger,
        "info",
        lambda _message, **kwargs: log_contexts.append(
            dict(kwargs.get("extra", {}).get("managed_session", {}))
        ),
    )

    validated = TemporalAgentRuntimeActivities._validate_session_request(
        {
            "sessionId": "sess:wf-run-1:codex_cli",
            "sessionEpoch": 1,
            "containerId": "container-1",
            "threadId": "thread-1",
            "instructions": "Write a private implementation plan",
        },
        activity_type="agent_runtime.send_turn",
        model_type=activity_runtime_module.SendCodexManagedSessionTurnRequest,
    )
    raw_context = activity_runtime_module._managed_session_telemetry_context(
        {
            "sessionId": "sess:wf-run-1:codex_cli",
            "sessionEpoch": 1,
            "containerId": "container-1",
            "threadId": "thread-1",
            "instructions": "Write a private implementation plan",
            "rawLog": "terminal scrollback",
            "token": "ghp_secret_token",
        },
        activity_type="agent_runtime.send_turn",
    )

    assert validated.session_id == "sess:wf-run-1:codex_cli"
    assert log_contexts == [
        {
            "activityType": "agent_runtime.send_turn",
            "sessionId": "sess:wf-run-1:codex_cli",
            "sessionEpoch": 1,
            "containerId": "container-1",
            "threadId": "thread-1",
        }
    ]
    assert raw_context == log_contexts[0]
    rendered = str(log_contexts)
    assert "Write a private implementation plan" not in rendered
    assert "terminal scrollback" not in rendered
    assert "ghp_secret_token" not in rendered


async def test_managed_session_telemetry_context_uses_trusted_activity_type() -> None:
    raw_context = activity_runtime_module._managed_session_telemetry_context(
        {
            "activityType": "payload.controlled_activity",
            "sessionId": "sess:wf-run-1:codex_cli",
        },
        activity_type="agent_runtime.send_turn",
    )

    assert raw_context == {
        "activityType": "agent_runtime.send_turn",
        "sessionId": "sess:wf-run-1:codex_cli",
    }


async def test_launch_session_materializes_claude_oauth_home_environment(
    tmp_path: Path,
) -> None:
    controller = AsyncMock()
    controller.launch_session = AsyncMock(
        return_value=CodexManagedSessionHandle(
            sessionState={
                "sessionId": "sess-claude-1",
                "sessionEpoch": 1,
                "containerId": "ctr-claude-1",
                "threadId": "thread-claude-1",
            },
            status="ready",
            imageRef="moonmind:latest",
            metadata={"vendorThreadId": "vendor-thread-claude-1"},
        )
    )
    activities = TemporalAgentRuntimeActivities(session_controller=controller)
    codex_home_path = tmp_path / "task-1" / ".moonmind" / "codex-home"

    result = await activities.agent_runtime_launch_session(
        {
            "request": {
                "taskRunId": "task-1",
                "sessionId": "sess-claude-1",
                "threadId": "thread-claude-1",
                "workspacePath": str(tmp_path / "task-1" / "repo"),
                "sessionWorkspacePath": str(tmp_path / "task-1" / "session"),
                "artifactSpoolPath": str(tmp_path / "task-1" / "artifacts"),
                "codexHomePath": str(codex_home_path),
                "imageRef": "moonmind:latest",
                "environment": {
                    "ANTHROPIC_API_KEY": "ambient-anthropic-key",
                    "CLAUDE_API_KEY": "ambient-claude-key",
                    "OPENAI_API_KEY": "ambient-openai-key",
                },
            },
            "profile": {
                "runtimeId": "claude_code",
                "profileId": "claude_anthropic",
                "providerId": "anthropic",
                "credentialSource": "oauth_volume",
                "runtimeMaterializationMode": "oauth_home",
                "volumeRef": "claude_auth_volume",
                "volumeMountPath": "/home/app/.claude",
                "clearEnvKeys": [
                    "ANTHROPIC_API_KEY",
                    "CLAUDE_API_KEY",
                    "OPENAI_API_KEY",
                ],
            },
        }
    )

    validated_request = controller.launch_session.await_args.args[0]
    environment = validated_request.environment
    assert environment["MANAGED_AUTH_VOLUME_PATH"] == "/home/app/.claude"
    assert environment["CLAUDE_HOME"] == "/home/app/.claude"
    assert environment["CLAUDE_VOLUME_PATH"] == "/home/app/.claude"
    assert "ANTHROPIC_API_KEY" not in environment
    assert "CLAUDE_API_KEY" not in environment
    assert "OPENAI_API_KEY" not in environment
    diagnostics = result.metadata["authDiagnostics"]
    assert diagnostics["profileRef"] == "claude_anthropic"
    assert diagnostics["runtimeId"] == "claude_code"
    assert diagnostics["volumeRef"] == "claude_auth_volume"
    assert diagnostics["authMountTarget"] == "/home/app/.claude"


async def test_launch_session_failure_redacts_claude_auth_paths(
    tmp_path: Path,
) -> None:
    controller = AsyncMock()
    controller.launch_session = AsyncMock(
        side_effect=RuntimeError(
            "/home/app/.claude/credentials.json token=claude-secret failed"
        )
    )
    activities = TemporalAgentRuntimeActivities(session_controller=controller)

    with pytest.raises(
        TemporalActivityRuntimeError,
        match="component=managed_session_controller",
    ) as exc_info:
        await activities.agent_runtime_launch_session(
            {
                "request": {
                    "taskRunId": "task-1",
                    "sessionId": "sess-claude-1",
                    "threadId": "thread-claude-1",
                    "workspacePath": str(tmp_path / "task-1" / "repo"),
                    "sessionWorkspacePath": str(tmp_path / "task-1" / "session"),
                    "artifactSpoolPath": str(tmp_path / "task-1" / "artifacts"),
                    "codexHomePath": str(
                        tmp_path / "task-1" / ".moonmind" / "codex-home"
                    ),
                    "imageRef": "moonmind:latest",
                },
                "profile": {
                    "runtimeId": "claude_code",
                    "profileId": "claude_anthropic",
                    "providerId": "anthropic",
                    "credentialSource": "oauth_volume",
                    "runtimeMaterializationMode": "oauth_home",
                    "volumeRef": "claude_auth_volume",
                    "volumeMountPath": "/home/app/.claude",
                },
            }
        )

    message = str(exc_info.value)
    assert "claude-secret" not in message
    assert "/home/app/.claude/credentials.json" not in message
    assert "[REDACTED]" in message
    assert "[REDACTED_AUTH_PATH]" in message


async def test_launch_session_claude_auth_diagnostics_do_not_alias_workspace_or_artifacts(
    tmp_path: Path,
) -> None:
    controller = AsyncMock()
    controller.launch_session = AsyncMock(
        return_value=CodexManagedSessionHandle(
            sessionState={
                "sessionId": "sess-claude-2",
                "sessionEpoch": 1,
                "containerId": "ctr-claude-2",
                "threadId": "thread-claude-2",
            },
            status="ready",
            imageRef="moonmind:latest",
            metadata={},
        )
    )
    activities = TemporalAgentRuntimeActivities(session_controller=controller)
    workspace_path = str(tmp_path / "task-2" / "repo")
    artifact_spool_path = str(tmp_path / "task-2" / "artifacts")

    result = await activities.agent_runtime_launch_session(
        {
            "request": {
                "taskRunId": "task-2",
                "sessionId": "sess-claude-2",
                "threadId": "thread-claude-2",
                "workspacePath": workspace_path,
                "sessionWorkspacePath": str(tmp_path / "task-2" / "session"),
                "artifactSpoolPath": artifact_spool_path,
                "codexHomePath": str(tmp_path / "task-2" / ".moonmind" / "codex-home"),
                "imageRef": "moonmind:latest",
            },
            "profile": {
                "runtimeId": "claude_code",
                "profileId": "claude_anthropic",
                "providerId": "anthropic",
                "credentialSource": "oauth_volume",
                "runtimeMaterializationMode": "oauth_home",
                "volumeRef": "claude_auth_volume",
                "volumeMountPath": "/home/app/.claude",
            },
        }
    )

    diagnostics = result.metadata["authDiagnostics"]
    assert diagnostics["authMountTarget"] == "/home/app/.claude"
    assert diagnostics["authMountTarget"] != workspace_path
    assert diagnostics["authMountTarget"] != artifact_spool_path
    assert diagnostics["volumeRef"] == "claude_auth_volume"
