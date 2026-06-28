from __future__ import annotations

from typing import get_args

import pytest

from moonmind.schemas.agent_runtime_models import ObservabilityEventKind
from moonmind.workflows.temporal.runtime.log_streamer import RuntimeLogStreamer
from moonmind.workflows.temporal.runtime.session_observability_bridge import (
    ManagedSessionObservabilityBridge,
)


class _MemoryArtifactStorage:
    def write_artifact(
        self,
        *,
        job_id: str,
        artifact_name: str,
        data: bytes,
    ) -> tuple[object, str]:
        return object(), f"{job_id}/{artifact_name}"


def _streamer() -> RuntimeLogStreamer:
    return RuntimeLogStreamer(_MemoryArtifactStorage())


def test_mm_983_observability_vocabulary_includes_required_mm_976_kinds() -> None:
    required = {
        "user_message_submitted",
        "assistant_message_delta",
        "assistant_message_completed",
        "assistant_message",
        "tool_call_started",
        "tool_call_output",
        "tool_call_completed",
        "tool_call_failed",
        "turn_failed",
        "runtime_status",
        "model_status",
        "intervention_requested",
        "session_started",
        "session_resumed",
        "session_cleared",
        "session_terminated",
        "session_reset_boundary",
        "approval_requested",
        "approval_resolved",
        "system_annotation",
        "summary_published",
        "checkpoint_published",
        "reset_boundary_published",
    }

    assert required.issubset(set(get_args(ObservabilityEventKind)))


def test_bridge_helpers_delegate_to_runtime_log_streamer_and_keep_sequence() -> None:
    log_streamer = _streamer()
    bridge = ManagedSessionObservabilityBridge(
        log_streamer=log_streamer,
        run_id="run-mm-983",
        workspace_path=None,
        session_id="sess-1",
        session_epoch=1,
        container_id="ctr-1",
        thread_id="thread-1",
        active_turn_id="turn-1",
    )

    bridge.user_message(text="implement MM-983", turn_id="turn-1")
    bridge.assistant_message(
        text="working",
        kind="assistant_message_delta",
        turn_id="turn-1",
    )
    bridge.tool_event(
        kind="tool_call_started",
        text="pytest started",
        turn_id="turn-1",
        metadata={"toolName": "pytest"},
    )

    events = log_streamer.consume_observability_events("run-mm-983")
    assert [event["sequence"] for event in events] == [1, 2, 3]
    assert [event["kind"] for event in events] == [
        "user_message_submitted",
        "assistant_message_delta",
        "tool_call_started",
    ]
    assert events[0]["sessionId"] == "sess-1"
    assert events[0]["activeTurnId"] == "turn-1"
    assert events[2]["metadata"]["toolName"] == "pytest"


def test_bridge_supports_missing_optional_session_identity_fields() -> None:
    log_streamer = _streamer()
    bridge = ManagedSessionObservabilityBridge(
        log_streamer=log_streamer,
        run_id="run-no-session",
        workspace_path=None,
    )

    bridge.approval_event(
        kind="intervention_requested",
        text="Operator intervention requested.",
    )

    events = log_streamer.consume_observability_events("run-no-session")
    assert events == [
        {
            "runId": "run-no-session",
            "sequence": 1,
            "stream": "session",
            "timestamp": events[0]["timestamp"],
            "text": "Operator intervention requested.",
            "kind": "intervention_requested",
            "metadata": {},
        }
    ]


def test_bridge_keeps_provider_native_event_names_as_metadata_only() -> None:
    log_streamer = _streamer()
    bridge = ManagedSessionObservabilityBridge(
        log_streamer=log_streamer,
        run_id="run-provider-metadata",
        workspace_path=None,
        session_id="sess-1",
    )

    bridge.assistant_message(
        text="delta",
        kind="assistant_message_delta",
        provider_native_event_name="response.output_text.delta",
    )

    [event] = log_streamer.consume_observability_events("run-provider-metadata")
    assert event["kind"] == "assistant_message_delta"
    assert event["metadata"] == {
        "providerNativeEventName": "response.output_text.delta"
    }


def test_bridge_redacts_secret_like_metadata() -> None:
    log_streamer = _streamer()
    bridge = ManagedSessionObservabilityBridge(
        log_streamer=log_streamer,
        run_id="run-redaction",
        workspace_path=None,
    )

    bridge.session_event(
        kind="runtime_status",
        text="Runtime status updated.",
        metadata={
            "apiToken": "raw-token-value",
            "detail": "Bearer samplevalue",
            "artifactRef": "artifact://safe-ref",
            "author": "John Doe",
            "authority": "admin",
            "authorization": "Bearer raw-auth-value",
        },
    )

    [event] = log_streamer.consume_observability_events("run-redaction")
    assert event["metadata"]["apiToken"] == "[REDACTED]"
    assert event["metadata"]["detail"] == "[REDACTED_AUTHORIZATION]"
    assert event["metadata"]["artifactRef"] == "artifact://safe-ref"
    assert event["metadata"]["author"] == "John Doe"
    assert event["metadata"]["authority"] == "admin"
    assert event["metadata"]["authorization"] == "[REDACTED]"


def test_bridge_preserves_tuple_metadata_shape_when_redacting() -> None:
    metadata = ManagedSessionObservabilityBridge.safe_metadata(
        {
            "items": (
                {"apiToken": "raw-token-value"},
                {"artifactRef": "artifact://safe-ref"},
            )
        }
    )

    assert isinstance(metadata["items"], tuple)
    assert metadata["items"] == (
        {"apiToken": "[REDACTED]"},
        {"artifactRef": "artifact://safe-ref"},
    )


def test_bridge_rejects_oversized_metadata() -> None:
    bridge = ManagedSessionObservabilityBridge(
        log_streamer=_streamer(),
        run_id="run-oversized",
        workspace_path=None,
    )

    with pytest.raises(ValueError, match="store large payloads in artifacts"):
        bridge.session_event(
            kind="model_status",
            text="Model status updated.",
            metadata={"body": "x" * 9000},
        )
