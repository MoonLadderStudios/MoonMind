from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from moonmind.schemas.managed_session_models import CodexManagedSessionHandle
from moonmind.workflows.temporal.activity_runtime import (
    TemporalActivityRuntimeError,
    TemporalAgentRuntimeActivities,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]

async def test_claude_launch_session_shapes_oauth_home_environment(tmp_path: Path) -> None:
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

    await activities.agent_runtime_launch_session(
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

async def test_claude_launch_session_redacts_auth_path_failures(tmp_path: Path) -> None:
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
