from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from moonmind.schemas.agent_runtime_models import AgentRunResult
from moonmind.schemas.managed_session_models import (
    CodexManagedSessionState,
    CodexManagedSessionTurnResponse,
)
from moonmind.schemas.temporal_models import IntegrationCallbackRequest
from moonmind.schemas.temporal_payload_policy import (
    MAX_TEMPORAL_METADATA_STRING_CHARS,
    compact_temporal_ref_metadata,
)
from moonmind.schemas.temporal_signal_contracts import ExternalEventSignal

def test_agent_run_result_metadata_rejects_nested_raw_bytes() -> None:
    with pytest.raises(ValidationError, match="must not contain raw bytes"):
        AgentRunResult(metadata={"diagnostics": {"payload": b"binary"}})

def test_agent_run_result_metadata_requires_artifact_ref_for_large_text() -> None:
    with pytest.raises(ValidationError, match="store large payloads in artifacts"):
        AgentRunResult(
            metadata={"transcript": "x" * (MAX_TEMPORAL_METADATA_STRING_CHARS + 1)}
        )

def test_compact_temporal_ref_metadata_replaces_inline_content_with_diagnostics() -> None:
    metadata = compact_temporal_ref_metadata(
        "instructionRef",
        "Implement this feature\n" + ("details " * 2000),
    )

    assert metadata["instructionRefOmitted"] is True
    assert metadata["instructionRefLengthChars"] > MAX_TEMPORAL_METADATA_STRING_CHARS
    assert len(metadata["instructionRefSha256"]) == 64
    assert "instructionRef" not in metadata
    AgentRunResult(metadata=metadata)

def test_managed_session_turn_response_metadata_allows_compact_artifact_refs() -> None:
    response = CodexManagedSessionTurnResponse(
        sessionState=CodexManagedSessionState(
            sessionId="sess-1",
            sessionEpoch=1,
            containerId="container-1",
            threadId="thread-1",
        ),
        turnId="turn-1",
        status="completed",
        outputRefs=("art-output",),
        metadata={"summaryRef": "art-summary", "checkpointRef": "art-checkpoint"},
    )

    assert response.metadata == {
        "summaryRef": "art-summary",
        "checkpointRef": "art-checkpoint",
    }
    assert response.model_dump(mode="json", by_alias=True)["metadata"] == {
        "summaryRef": "art-summary",
        "checkpointRef": "art-checkpoint",
    }

def test_integration_provider_summary_rejects_large_provider_body() -> None:
    with pytest.raises(ValidationError, match="store large payloads in artifacts"):
        IntegrationCallbackRequest(
            eventType="provider.update",
            providerSummary={
                "body": "x" * (MAX_TEMPORAL_METADATA_STRING_CHARS + 1),
            },
            payloadArtifactRef="art-provider-payload",
        )

def test_external_event_provider_summary_accepts_compact_refs() -> None:
    signal = ExternalEventSignal(
        source="jules",
        eventType="completed",
        observedAt=datetime.now(tz=UTC),
        providerSummary={"resultRef": "art-result", "status": "done"},
    )

    assert signal.provider_summary == {"resultRef": "art-result", "status": "done"}
