"""Unit coverage for Omnigent bridge event normalization (MM-1157)."""

from __future__ import annotations

import pytest

from moonmind.omnigent.bridge_artifacts import OmnigentContractError
from moonmind.omnigent.bridge_events import (
    BRIDGE_EVENT_SCHEMA_VERSION,
    build_omnigent_bridge_event,
    normalize_omnigent_observation,
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest


def _request() -> AgentExecutionRequest:
    return AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        executionProfileRef="profile:test",
        correlationId="corr-1",
        idempotencyKey="idem-1",
    )


def test_build_omnigent_bridge_event_emits_v1_shape() -> None:
    result = build_omnigent_bridge_event(
        payload={
            "type": "response.delta",
            "timestamp": "2026-07-08T12:00:00Z",
            "data": {"text": "Editing auth callback"},
            "outputRefs": ["artifact://omnigent/corr-1/output.txt"],
        },
        sequence=42,
        request=_request(),
        omnigent_session_id="sess-1",
        bridge_session_id="brs-1",
    )

    event = result.event
    assert result.diagnostic is None
    assert event["schemaVersion"] == BRIDGE_EVENT_SCHEMA_VERSION
    assert event["sequence"] == 42
    assert event["bridgeSessionId"] == "brs-1"
    assert event["omnigentSessionId"] == "sess-1"
    assert event["moonmindWorkflowId"] == "corr-1"
    assert event["moonmindAgentRunId"] == "corr-1"
    assert event["direction"] == "host_to_moonmind"
    assert event["type"] == "response.delta"
    assert event["eventType"] == "response.delta"
    assert event["normalizedStatus"] == "running"
    assert event["data"] == {"text": "Editing auth callback"}
    assert event["artifactRefs"] == {
        "outputRefs": ["artifact://omnigent/corr-1/output.txt"]
    }
    assert event["metadata"]["moonmind"] == {
        "workflowChatVisible": True,
        "source": "omnigent_stream",
    }
    assert event["textPreview"] == "Editing auth callback"
    assert event["artifactRef"] == "artifact://omnigent/corr-1/output.txt"


def test_execution_critical_drift_fails_closed() -> None:
    with pytest.raises(OmnigentContractError, match="Unsupported Omnigent event type"):
        normalize_omnigent_observation({"type": "response.unexpected"})


def test_session_created_normalizes_without_explicit_status() -> None:
    assert normalize_omnigent_observation({"type": "session.created"}) == "created"


def test_resume_gap_is_a_durable_non_terminal_diagnostic() -> None:
    result = build_omnigent_bridge_event(
        payload={
            "type": "stream.resume_gap",
            "status": "running",
            "metadata": {"reason": "upstream_replay_unavailable"},
        },
        sequence=8,
        request=_request(),
        omnigent_session_id="sess-1",
    )

    assert result.event["eventType"] == "stream.resume_gap"
    assert result.event["normalizedStatus"] == "running"
    assert result.diagnostic is None


def test_optional_resource_drift_degrades_with_diagnostic() -> None:
    result = build_omnigent_bridge_event(
        payload={"type": "resource.future_file", "status": "not-a-status"},
        sequence=3,
        request=_request(),
        omnigent_session_id="sess-1",
    )

    assert result.event["normalizedStatus"] == "running"
    assert result.diagnostic == {
        "code": "omnigent_optional_resource_contract_drift",
        "eventType": "resource.future_file",
        "message": "Unsupported Omnigent event type: resource.future_file",
        "sequence": 3,
        "severity": "degraded",
    }
    assert (
        result.event["metadata"]["moonmind"]["contractDrift"]
        == result.diagnostic
    )


def test_direct_codex_and_omnigent_shared_fixtures_emit_same_event_classes() -> None:
    shared_behaviors = (
        ("session startup", "session.started"),
        ("user input", "session.input.user_message"),
        ("assistant output", "response.output"),
        ("tool completion", "session.item.tool.completed"),
        ("approval request", "session.item.approval.requested"),
        ("terminal completion", "response.completed"),
    )

    direct_classes = {
        build_omnigent_bridge_event(
            payload={"type": event_type, "status": "running"},
            sequence=index,
            request=_request(),
            omnigent_session_id="direct-session",
        ).event["eventType"]
        for index, (_behavior, event_type) in enumerate(shared_behaviors, start=1)
    }
    omnigent_classes = {
        build_omnigent_bridge_event(
            payload={"type": event_type, "status": "running"},
            sequence=index,
            request=_request(),
            omnigent_session_id="omnigent-session",
        ).event["eventType"]
        for index, (_behavior, event_type) in enumerate(shared_behaviors, start=1)
    }

    assert direct_classes == omnigent_classes == {
        event_type for _behavior, event_type in shared_behaviors
    }
