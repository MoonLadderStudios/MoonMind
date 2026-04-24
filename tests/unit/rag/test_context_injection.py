"""Unit tests for RAG ContextInjectionService."""

from __future__ import annotations

import io
from unittest.mock import MagicMock, patch

import pytest

from moonmind.rag.context_injection import ContextInjectionService
from moonmind.rag.context_pack import ContextItem, ContextPack
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest

class FakePopen:
    def __init__(self, lines: list[str], *, returncode: int = 0) -> None:
        self.stdout = io.StringIO("".join(lines))
        self.stderr = io.StringIO("")
        self.returncode = returncode
        self.terminated = False
        self.killed = False
        self._wait_calls: list[float | None] = []

    def __enter__(self) -> "FakePopen":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15

    def wait(self, timeout: float | None = None) -> int:
        self._wait_calls.append(timeout)
        return self.returncode

    def poll(self) -> int | None:
        return self.returncode

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

@pytest.fixture
def mock_request() -> AgentExecutionRequest:
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
async def test_inject_context_disabled(mock_request: AgentExecutionRequest, tmp_path) -> None:
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
async def test_inject_context_enabled_with_items(
    mock_retrieve,
    mock_request: AgentExecutionRequest,
    tmp_path,
) -> None:
    service = ContextInjectionService(env={"MOONMIND_RAG_AUTO_CONTEXT": "true"})

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
    assert "BEGIN_RETRIEVED_CONTEXT\nRetrieved context snippet\nEND_RETRIEVED_CONTEXT" in result.instruction
    assert "Original instruction" in result.instruction
    assert mock_request.instruction_ref == result.instruction

@pytest.mark.asyncio
@patch("moonmind.rag.context_injection.ContextInjectionService._retrieve_context_pack")
async def test_inject_context_no_items(
    mock_retrieve,
    mock_request: AgentExecutionRequest,
    tmp_path,
) -> None:
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
@patch("moonmind.rag.context_injection.subprocess.Popen")
@patch("moonmind.rag.context_injection.ContextInjectionService._retrieve_context_pack")
async def test_inject_context_uses_local_fallback_when_retrieval_fails(
    mock_retrieve,
    mock_popen,
    mock_request: AgentExecutionRequest,
    tmp_path,
) -> None:
    service = ContextInjectionService(env={"MOONMIND_RAG_AUTO_CONTEXT": "true"})
    mock_request.instruction_ref = (
        "Task details should show the provider profile selected for the workflow run"
    )
    (tmp_path / "docs").mkdir()
    (tmp_path / "frontend").mkdir()

    mock_retrieve.side_effect = RuntimeError("qdrant unavailable")
    mock_popen.return_value = FakePopen(
        [
            f"{tmp_path / 'docs' / 'TaskDetails.md'}:12:Task details view shows workflow metadata\n",
            f"frontend/TaskView.tsx:8:providerProfile is rendered in the details panel\n",
        ]
    )

    result = await service.inject_context(
        request=mock_request,
        workspace_path=tmp_path,
    )

    assert result.items_count == 2
    assert "BEGIN_RETRIEVED_CONTEXT" in result.instruction
    assert "docs/TaskDetails.md" in result.instruction
    assert str(tmp_path) not in result.instruction
    assert "providerProfile is rendered in the details panel" in result.instruction
    assert mock_request.instruction_ref == result.instruction

@pytest.mark.asyncio
@patch("moonmind.rag.context_injection.ContextInjectionService._build_local_fallback_pack")
@patch("moonmind.rag.context_injection.ContextInjectionService._retrieve_context_pack")
async def test_inject_context_skips_local_fallback_for_explicit_disable_reason(
    mock_retrieve,
    mock_build_fallback,
    mock_request: AgentExecutionRequest,
    tmp_path,
) -> None:
    service = ContextInjectionService(env={"MOONMIND_RAG_AUTO_CONTEXT": "true"})
    mock_retrieve.return_value = (None, "qdrant_disabled")

    result = await service.inject_context(
        request=mock_request,
        workspace_path=tmp_path,
    )

    mock_build_fallback.assert_not_called()
    assert result.instruction == "Original instruction"
    assert result.items_count == 0
    assert result.artifact_path is None

@patch("moonmind.rag.context_injection.subprocess.Popen")
def test_build_local_fallback_pack_stops_after_max_items(mock_popen, tmp_path) -> None:
    service = ContextInjectionService(env={"MOONMIND_RAG_AUTO_CONTEXT": "true"})
    (tmp_path / "docs").mkdir()
    process = FakePopen(
        [f"docs/file-{idx}.md:{idx}:match {idx}\n" for idx in range(1, 20)]
    )
    mock_popen.return_value = process

    pack = service._build_local_fallback_pack(
        instruction="provider profile workflow details context",
        workspace_path=tmp_path,
    )

    assert pack is not None
    assert len(pack.items) == 8
    assert process.terminated

def test_parse_rg_match_line_normalizes_absolute_source(tmp_path) -> None:
    source, line_number, snippet = ContextInjectionService._parse_rg_match_line(
        f"{tmp_path / 'docs' / 'guide.md'}:14:matched text",
        workspace_path=tmp_path,
    )

    assert source == "docs/guide.md"
    assert line_number == 14
    assert snippet == "matched text"

def test_extract_query_terms_keeps_domain_words() -> None:
    terms = ContextInjectionService._extract_query_terms(
        "Task details should show the provider profile selected for the workflow run"
    )

    assert "task" in terms
    assert "details" in terms
    assert "provider" in terms
    assert "profile" in terms
    assert "workflow" in terms
