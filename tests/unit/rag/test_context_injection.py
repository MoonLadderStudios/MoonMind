"""Unit tests for RAG ContextInjectionService."""

import subprocess
import pytest
from unittest.mock import MagicMock, patch

from moonmind.rag.context_injection import ContextInjectionService
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.rag.context_pack import ContextPack, ContextItem


@pytest.fixture
def mock_request():
    return AgentExecutionRequest(
        agentKind="managed",
        agentId="test-agent",
        executionProfileRef="test-profile",
        correlationId="test-correlation-id",
        idempotencyKey="test-idempotency-key",
        instructionRef="Original instruction",
        parameters={"repository": "test-repo"},
    )


@pytest.mark.asyncio
async def test_inject_context_disabled(mock_request, tmp_path):
    """Test injection when MOONMIND_RAG_AUTO_CONTEXT is false."""
    service = ContextInjectionService(env={"MOONMIND_RAG_AUTO_CONTEXT": "false"})
    
    result = await service.inject_context(
        request=mock_request,
        workspace_path=tmp_path,
    )
    
    assert result.instruction == "Original instruction"
    assert result.items_count == 0
    assert result.artifact_path is None
    assert mock_request.instruction_ref == "Original instruction"


@pytest.mark.asyncio
@patch("moonmind.rag.context_injection.ContextInjectionService._retrieve_context_pack")
async def test_inject_context_enabled_with_items(mock_retrieve, mock_request, tmp_path):
    """Test successful context injection with retrieved items."""
    service = ContextInjectionService(env={"MOONMIND_RAG_AUTO_CONTEXT": "true"})
    
    # Mock retrieval returning a pack with 1 item
    mock_pack = MagicMock(spec=ContextPack)
    mock_pack.items = [MagicMock(spec=ContextItem)]
    mock_pack.context_text = "Retrieved context snippet"
    mock_pack.transport = "test-transport"
    mock_pack.to_json.return_value = '{"test": "json"}'
    
    mock_retrieve.return_value = (mock_pack, None)
    
    result = await service.inject_context(
        request=mock_request,
        workspace_path=tmp_path,
    )
    
    assert result.items_count == 1
    assert result.artifact_path is not None
    assert result.artifact_path.exists()
    
    expected_instruction_part = "BEGIN_RETRIEVED_CONTEXT\nRetrieved context snippet\nEND_RETRIEVED_CONTEXT"
    assert expected_instruction_part in result.instruction
    assert "Original instruction" in result.instruction
    
    # Verify request was mutated
    assert mock_request.instruction_ref == result.instruction


@pytest.mark.asyncio
@patch("moonmind.rag.context_injection.ContextInjectionService._retrieve_context_pack")
async def test_inject_context_no_items(mock_retrieve, mock_request, tmp_path):
    """Test when retrieval succeeds but returns 0 items."""
    service = ContextInjectionService(env={"MOONMIND_RAG_AUTO_CONTEXT": "true"})
    
    mock_pack = MagicMock(spec=ContextPack)
    mock_pack.items = []
    mock_pack.context_text = ""
    mock_pack.transport = "test-transport"
    mock_pack.to_json.return_value = '{"test": "json"}'
    
    mock_retrieve.return_value = (mock_pack, None)
    
    result = await service.inject_context(
        request=mock_request,
        workspace_path=tmp_path,
    )
    
    assert result.items_count == 0
    assert result.instruction == "Original instruction"
    assert mock_request.instruction_ref == "Original instruction"


@pytest.mark.asyncio
@patch("moonmind.rag.context_injection.ContextInjectionService._retrieve_context_pack")
@patch("moonmind.rag.context_injection.subprocess.run")
async def test_inject_context_uses_local_fallback_when_retrieval_fails(
    mock_run,
    mock_retrieve,
    mock_request,
    tmp_path,
):
    service = ContextInjectionService(env={"MOONMIND_RAG_AUTO_CONTEXT": "true"})
    mock_request.instruction_ref = (
        "Task details should show the provider profile selected for the workflow run"
    )
    (tmp_path / "docs").mkdir()

    mock_retrieve.side_effect = RuntimeError("qdrant unavailable")
    mock_run.return_value = subprocess.CompletedProcess(
        args=["rg"],
        returncode=0,
        stdout=(
            f"{tmp_path / 'docs' / 'TaskDetails.md'}:12:Task details view shows workflow metadata\n"
            f"{tmp_path / 'frontend' / 'TaskView.tsx'}:8:providerProfile is rendered in the details panel\n"
        ),
        stderr="",
    )

    result = await service.inject_context(
        request=mock_request,
        workspace_path=tmp_path,
    )

    assert result.items_count == 2
    assert "BEGIN_RETRIEVED_CONTEXT" in result.instruction
    assert "TaskDetails.md" in result.instruction
    assert "providerProfile is rendered in the details panel" in result.instruction
    assert mock_request.instruction_ref == result.instruction


def test_extract_query_terms_keeps_domain_words():
    terms = ContextInjectionService._extract_query_terms(
        "Task details should show the provider profile selected for the workflow run"
    )

    assert "task" in terms
    assert "details" in terms
    assert "provider" in terms
    assert "profile" in terms
    assert "workflow" in terms
