"""Unit tests for OpenClaw adapter translation helpers."""

from __future__ import annotations

from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.workflows.adapters.openclaw_agent_adapter import (
    OpenClawExternalAdapter,
    build_openclaw_chat_messages,
    openclaw_success_result,
)

def test_build_openclaw_chat_messages_includes_workspace_and_description() -> None:
    req = AgentExecutionRequest(
        agentKind="external",
        agentId="openclaw",
        executionProfileRef="profile:test",
        correlationId="corr-1",
        idempotencyKey="idem-1",
        parameters={
            "title": "Fix bug",
            "description": "Patch the null check",
        },
        workspaceSpec={"branch": "main"},
    )
    messages = build_openclaw_chat_messages(req)
    assert messages[0]["role"] == "system"
    assert "MoonMind" in messages[0]["content"]
    user = messages[1]["content"]
    assert "Fix bug" in user
    assert "Patch the null check" in user
    assert "main" in user

def test_openclaw_success_result_truncates_long_summary() -> None:
    req = AgentExecutionRequest(
        agentKind="external",
        agentId="openclaw",
        executionProfileRef="profile:test",
        correlationId="c",
        idempotencyKey="i",
    )
    text = "x" * 5000
    result = openclaw_success_result(full_text=text, request=req)
    assert result.summary is not None
    assert len(result.summary) <= 4096

def test_openclaw_external_adapter_capability_is_streaming() -> None:
    adapter = OpenClawExternalAdapter()
    cap = adapter.provider_capability
    assert cap.execution_style == "streaming_gateway"
    assert cap.supports_result_fetch is False
