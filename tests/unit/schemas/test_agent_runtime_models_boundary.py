"""Unit tests for the canonical return boundary helpers."""

import pytest
from datetime import UTC, datetime

from moonmind.schemas.agent_runtime_models import (
    AgentRunHandle,
    AgentRunStatus,
    AgentRunResult,
    UnsupportedStatusError,
    build_canonical_start_handle,
    build_canonical_status,
    build_canonical_result,
    raise_unsupported_status,
)


def test_raise_unsupported_status_error():
    with pytest.raises(UnsupportedStatusError) as exc_info:
        raise_unsupported_status("bizarre_state", context="test_provider")
    assert "Unsupported status: 'bizarre_state'" in str(exc_info.value)
    assert "test_provider" in str(exc_info.value)

def test_build_canonical_start_handle_valid():
    payload = {
        "runId": "r-123",
        "agentKind": "external",
        "agentId": "jules",
        "status": "launching",
        "startedAt": datetime(2025, 1, 1, tzinfo=UTC),
    }
    handle = build_canonical_start_handle(payload)
    assert isinstance(handle, AgentRunHandle)
    assert handle.run_id == "r-123"
    assert handle.status == "launching"

def test_build_canonical_start_handle_strips_top_level_provider_fields():
    payload = {
        "external_id": "r-123",
        "agentKind": "external",
        "agentId": "jules",
        "status": "launching",
        "tracking_ref": "tr-456",
        "provider_status": "StartingUp",
        "startedAt": datetime(2025, 1, 1, tzinfo=UTC),
    }
    handle = build_canonical_start_handle(payload)
    
    # external_id -> run_id, tracking_ref goes to metadata, provider_status to metadata
    assert handle.run_id == "r-123"
    assert "trackingRef" in handle.metadata
    assert handle.metadata["trackingRef"] == "tr-456"
    assert "providerStatus" in handle.metadata
    assert handle.metadata["providerStatus"] == "StartingUp"

def test_build_canonical_status_strips_top_level_external_url():
    payload = {
        "run_id": "r-123",
        "agentKind": "external",
        "agentId": "jules",
        "status": "running",
        "url": "https://foo.com/bar",
        "external_url": "https://foo.com/bar",
        "normalized_status": "running",
    }
    status = build_canonical_status(payload)
    
    assert isinstance(status, AgentRunStatus)
    assert status.run_id == "r-123"
    assert status.status == "running"
    assert "externalUrl" in status.metadata
    assert status.metadata["externalUrl"] == "https://foo.com/bar"
    assert "normalizedStatus" in status.metadata

def test_build_canonical_status_raises_unsupported_status():
    payload = {
        "run_id": "r-123",
        "agentKind": "external",
        "agentId": "jules",
        "status": "unknown_crazy_state",
    }
    with pytest.raises(UnsupportedStatusError):
        build_canonical_status(payload)

def test_build_canonical_result_valid():
    payload = {
        "outputRefs": ["a", "b"],
        "summary": "Done",
        "failureClass": "user_error",
        "metadata": {"some_extra": "value"},
    }
    result = build_canonical_result(payload)
    assert isinstance(result, AgentRunResult)
    assert result.summary == "Done"
    assert result.failure_class == "user_error"
