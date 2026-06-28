"""Unit tests for Omnigent adapter translation helpers."""

from __future__ import annotations

from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.workflows.adapters.omnigent_agent_adapter import (
    OmnigentExternalAdapter,
    build_omnigent_first_message,
)


def test_omnigent_external_adapter_capability_is_streaming() -> None:
    adapter = OmnigentExternalAdapter()
    cap = adapter.provider_capability
    assert cap.provider_name == "omnigent"
    assert cap.execution_style == "streaming_gateway"
    assert cap.supports_callbacks is False
    assert cap.supports_result_fetch is False


def test_build_omnigent_first_message_prefers_omnigent_prompt_text() -> None:
    req = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        executionProfileRef="profile:test",
        correlationId="corr-1",
        idempotencyKey="idem-1",
        instructionRef="artifact://instruction",
        parameters={
            "description": "fallback description",
            "omnigent": {"prompt": {"text": "Use this exact prompt"}},
        },
    )

    event = build_omnigent_first_message(req)

    assert event["type"] == "message"
    assert event["data"]["role"] == "user"
    assert event["data"]["content"][0]["text"] == "Use this exact prompt"


def test_build_omnigent_first_message_keeps_raw_input_refs_only() -> None:
    req = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        executionProfileRef="profile:test",
        correlationId="corr-1",
        idempotencyKey="idem-1",
        inputRefs=["artifact://objective-image"],
        parameters={"title": "Inspect"},
        workspaceSpec={"branch": "main"},
    )

    text = build_omnigent_first_message(req)["data"]["content"][0]["text"]

    assert "Input refs: artifact://objective-image" in text
    assert "prepared-context://" not in text
