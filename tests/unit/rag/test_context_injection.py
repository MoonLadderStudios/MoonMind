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
    moonmind_meta = mock_request.parameters["metadata"]["moonmind"]
    assert moonmind_meta["retrievalMode"] == "disabled"
    assert moonmind_meta["retrievalDisabledReason"] == "auto_context_disabled"
    assert moonmind_meta["retrievalInitiationMode"] == "automatic"

@pytest.mark.asyncio
@patch("moonmind.rag.context_injection.ContextInjectionService._retrieve_context_pack")
async def test_inject_context_enabled_with_items(
    mock_retrieve,
    mock_request: AgentExecutionRequest,
    tmp_path,
) -> None:
    service = ContextInjectionService(env={"MOONMIND_RAG_AUTO_CONTEXT": "true"})
    mock_request.parameters["rag"] = {"localFallbackAuthorized": True}

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
    assert result.artifact_path is not None
    assert result.instruction == "Original instruction"
    assert mock_request.instruction_ref == "Original instruction"
    moonmind_meta = mock_request.parameters["metadata"]["moonmind"]
    assert moonmind_meta["retrievedContextArtifactPath"].startswith("artifacts/context/")
    assert moonmind_meta["retrievedContextTransport"] == "test-transport"
    assert moonmind_meta["retrievedContextItemCount"] == 0

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
    mock_request.parameters["rag"] = {"localFallbackAuthorized": True}
    mock_request.instruction_ref = (
        "Task details should show the provider profile selected for the workflow run"
    )
    (tmp_path / "docs").mkdir()
    (tmp_path / "frontend").mkdir()

    mock_retrieve.side_effect = RuntimeError("qdrant unavailable")
    mock_popen.return_value = FakePopen(
        [
            f"{tmp_path / 'docs' / 'WorkflowDetails.md'}:12:Task details view shows workflow metadata\n",
            f"frontend/TaskView.tsx:8:providerProfile is rendered in the details panel\n",
        ]
    )

    result = await service.inject_context(
        request=mock_request,
        workspace_path=tmp_path,
    )

    assert result.items_count == 2
    assert "BEGIN_RETRIEVED_CONTEXT" in result.instruction
    assert "Retrieved context mode: degraded local fallback" in result.instruction
    assert "docs/WorkflowDetails.md" in result.instruction
    assert str(tmp_path) not in result.instruction
    assert "providerProfile is rendered in the details panel" in result.instruction
    assert mock_request.instruction_ref == result.instruction
    moonmind_meta = mock_request.parameters["metadata"]["moonmind"]
    assert moonmind_meta["retrievedContextTransport"] == "local_fallback"
    assert moonmind_meta["retrievalMode"] == "degraded_local_fallback"
    assert moonmind_meta["retrievalDegradedReason"] == "local_fallback_after_retrieval_error"


@pytest.mark.asyncio
@patch("moonmind.rag.context_injection.ContextInjectionService._build_local_fallback_pack")
@patch("moonmind.rag.context_injection.ContextInjectionService._retrieve_context_pack")
async def test_local_fallback_requires_authored_authorization(
    mock_retrieve,
    mock_build_fallback,
    mock_request: AgentExecutionRequest,
    tmp_path,
) -> None:
    mock_retrieve.return_value = (None, "retrieval_gateway_unavailable")

    result = await ContextInjectionService(
        env={"MOONMIND_RAG_AUTO_CONTEXT": "true"}
    ).inject_context(request=mock_request, workspace_path=tmp_path)

    mock_build_fallback.assert_not_called()
    assert result.instruction == "Original instruction"
    metadata = mock_request.parameters["metadata"]["moonmind"]
    assert metadata["retrievalMode"] == "unavailable"
    assert metadata["retrievalDisabledReason"] == "retrieval_gateway_unavailable"

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


@patch("moonmind.rag.context_injection.ContextRetrievalService.retrieve")
def test_retrieve_context_pack_handles_non_dict_parameters(
    mock_retrieve,
    mock_request: AgentExecutionRequest,
) -> None:
    mock_request.parameters = None  # type: ignore[assignment]
    mock_request.workspace_spec = {"repository": "workspace-repo"}
    service = ContextInjectionService(
        env={
            "MOONMIND_RAG_AUTO_CONTEXT": "true",
            "QDRANT_ENABLED": "true",
            "GOOGLE_API_KEY": "test",
        }
    )
    mock_retrieve.return_value = ContextPack(
        items=[],
        filters={},
        budgets={},
        usage={},
        transport="direct",
        context_text="",
        retrieved_at="2026-04-24T00:00:00Z",
        telemetry_id="tid",
    )

    pack, reason = service._retrieve_context_pack(mock_request)

    assert pack is mock_retrieve.return_value
    assert reason is None
    call_kwargs = mock_retrieve.call_args.kwargs
    assert call_kwargs["filters"]["repo"] == "workspace-repo"
    assert call_kwargs["planning_ref"] is None

@pytest.mark.asyncio
@patch("moonmind.rag.context_injection.ContextInjectionService._build_local_fallback_pack")
@patch("moonmind.rag.context_injection.ContextInjectionService._retrieve_context_pack")
async def test_inject_context_uses_local_fallback_when_gateway_auth_missing(
    mock_retrieve,
    mock_build_fallback,
    mock_request: AgentExecutionRequest,
    tmp_path,
) -> None:
    service = ContextInjectionService(env={"MOONMIND_RAG_AUTO_CONTEXT": "true"})
    mock_request.parameters["rag"] = {"localFallbackAuthorized": True}
    mock_pack = MagicMock(spec=ContextPack)
    mock_pack.items = [MagicMock(spec=ContextItem)]
    mock_pack.context_text = "Local fallback snippet"
    mock_pack.transport = "local_fallback"
    mock_pack.to_json.return_value = '{"test": "json"}'
    mock_retrieve.return_value = (None, "retrieval_gateway_auth_missing")
    mock_build_fallback.return_value = mock_pack

    result = await service.inject_context(
        request=mock_request,
        workspace_path=tmp_path,
    )

    assert result.items_count == 1
    assert "Local fallback snippet" in result.instruction
    mock_build_fallback.assert_called_once()
    moonmind_meta = mock_request.parameters["metadata"]["moonmind"]
    assert moonmind_meta["retrievalMode"] == "degraded_local_fallback"
    assert moonmind_meta["retrievalDegradedReason"] == "retrieval_gateway_auth_missing"


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

def test_record_context_metadata_marks_durable_authority(
    mock_request: AgentExecutionRequest,
) -> None:
    ContextInjectionService._record_context_metadata(
        request=mock_request,
        artifact_ref="artifacts/context/rag-context-abc123.json",
        transport="direct",
        items_count=2,
    )

    moonmind_meta = mock_request.parameters["metadata"]["moonmind"]
    assert moonmind_meta["retrievedContextArtifactPath"] == "artifacts/context/rag-context-abc123.json"
    assert moonmind_meta["latestContextPackRef"] == "artifacts/context/rag-context-abc123.json"
    assert moonmind_meta["retrievalDurabilityAuthority"] == "artifact_ref"
    assert moonmind_meta["sessionContinuityCacheStatus"] == "advisory_only"
    assert moonmind_meta["retrievalMode"] == "semantic"
    assert moonmind_meta["retrievalInitiationMode"] == "automatic"
    assert moonmind_meta["retrievalContextTruncated"] is False


def test_record_context_metadata_marks_degraded_local_fallback_reason(
    mock_request: AgentExecutionRequest,
) -> None:
    ContextInjectionService._record_context_metadata(
        request=mock_request,
        artifact_ref="artifacts/context/rag-context-fallback.json",
        transport="local_fallback",
        items_count=3,
        degraded_reason="collection_unavailable",
    )

    moonmind_meta = mock_request.parameters["metadata"]["moonmind"]
    assert moonmind_meta["retrievedContextTransport"] == "local_fallback"
    assert moonmind_meta["retrievalMode"] == "degraded_local_fallback"
    assert moonmind_meta["retrievalDegradedReason"] == "collection_unavailable"


def test_persisted_context_artifact_uses_workspace_context_directory(mock_request: AgentExecutionRequest, tmp_path) -> None:
    service = ContextInjectionService(env={"MOONMIND_RAG_AUTO_CONTEXT": "true"})
    pack = ContextPack(
        items=[ContextItem(score=0.9, source="docs/spec.md", text="retrieved text")],
        filters={"repo": "test-repo"},
        budgets={},
        usage={},
        transport="direct",
        context_text="### Retrieved Context\n1. docs/spec.md (score: 0.900, trust: canonical)\n    retrieved text",
        retrieved_at="2026-04-24T00:00:00Z",
        telemetry_id="telemetry-1",
        initiation_mode="automatic",
        truncated=False,
    )

    artifact_path = service._persist_context_pack(
        request=mock_request,
        pack=pack,
        workspace_path=tmp_path,
    )

    assert artifact_path.parent == tmp_path / "artifacts" / "context"
    assert artifact_path.read_text(encoding="utf-8").strip().startswith("{")
    assert '"transport": "direct"' in artifact_path.read_text(encoding="utf-8")


@pytest.mark.asyncio
@patch("moonmind.rag.context_injection.ContextInjectionService._retrieve_context_pack")
async def test_retry_reuses_persisted_context_pack_before_first_message_digest(
    mock_retrieve,
    mock_request: AgentExecutionRequest,
    tmp_path,
) -> None:
    service = ContextInjectionService(env={"MOONMIND_RAG_AUTO_CONTEXT": "true"})
    pack = ContextPack(
        items=[ContextItem(score=0.9, source="docs/spec.md", text="stable context")],
        filters={"repo": "test-repo"},
        budgets={"tokens": 500},
        usage={"tokens": 20},
        transport="gateway",
        context_text="### Retrieved Context\nstable context",
        retrieved_at="2026-07-26T00:00:00Z",
        telemetry_id="telemetry-stable",
        initiation_mode="automatic",
        truncated=False,
    )
    mock_retrieve.return_value = (pack, None)

    first = await service.inject_context(
        request=mock_request,
        workspace_path=tmp_path,
    )
    original_instruction = "Original instruction"
    retry_request = mock_request.model_copy(
        deep=True,
        update={"instruction_ref": original_instruction, "parameters": {}},
    )
    second = await service.inject_context(
        request=retry_request,
        workspace_path=tmp_path,
    )

    assert first.instruction == second.instruction
    assert mock_retrieve.call_count == 1
    retry_metadata = retry_request.parameters["metadata"]["moonmind"]
    assert retry_metadata["retrievalReusedPersistedContext"] is True
    assert retry_metadata["retrievedContextDigest"].startswith("sha256:")
    assert retry_metadata["retrievedContextSources"] == ["docs/spec.md"]


@pytest.mark.asyncio
@patch(
    "moonmind.rag.context_injection.ContextInjectionService._retrieve_context_pack",
    side_effect=RuntimeError("gateway unavailable"),
)
async def test_required_retrieval_fails_before_message_preparation(
    _mock_retrieve,
    mock_request: AgentExecutionRequest,
    tmp_path,
) -> None:
    mock_request.parameters["rag"] = {"required": True}
    service = ContextInjectionService(env={"MOONMIND_RAG_AUTO_CONTEXT": "true"})

    with pytest.raises(
        RuntimeError,
        match="required initial context retrieval unavailable",
    ):
        await service.inject_context(
            request=mock_request,
            workspace_path=tmp_path,
        )

    metadata = mock_request.parameters["metadata"]["moonmind"]
    assert metadata["retrievalMode"] == "unavailable"
    assert metadata["retrievalDisabledReason"] == "retrieval_gateway_unavailable"


def test_authored_query_override_is_used_without_replacing_instruction(
    mock_request: AgentExecutionRequest,
) -> None:
    mock_request.parameters["rag"] = {"queryOverride": "bounded retrieval query"}

    assert (
        ContextInjectionService._retrieval_query(mock_request)
        == "bounded retrieval query"
    )
    assert mock_request.instruction_ref == "Original instruction"


def test_agent_request_accepts_numeric_rag_token_budget_but_not_token_secret() -> None:
    request = AgentExecutionRequest(
        agentKind="managed",
        agentId="test-agent",
        correlationId="correlation",
        idempotencyKey="idempotency",
        parameters={"rag": {"budgets": {"tokens": 800}}},
    )
    assert request.parameters["rag"]["budgets"]["tokens"] == 800

    with pytest.raises(ValueError, match="raw credential keys"):
        AgentExecutionRequest(
            agentKind="managed",
            agentId="test-agent",
            correlationId="correlation",
            idempotencyKey="idempotency",
            parameters={"rag": {"budgets": {"tokens": "secret-value"}}},
        )


@patch("moonmind.rag.context_injection.ContextRetrievalService.retrieve")
def test_authored_policy_is_compiled_into_bounded_retrieval_request(
    mock_retrieve,
    mock_request: AgentExecutionRequest,
) -> None:
    mock_request.parameters["rag"] = {
        "collections": ["canonical", "run-context"],
        "scope": {
            "tenant": "tenant-1",
            "workspace": "workspace-1",
            "run": "run-1",
        },
        "overlayPolicy": "skip",
        "budgets": {"tokens": 800, "latency_ms": 1200},
        "transport": "gateway",
        "topK": 4,
        "staleAllowed": True,
    }
    service = ContextInjectionService(
        env={
            "MOONMIND_RAG_AUTO_CONTEXT": "true",
            "QDRANT_ENABLED": "true",
            "MOONMIND_RETRIEVAL_URL": "https://retrieval.invalid",
            "MOONMIND_RETRIEVAL_TOKEN": "test-token",
            "VECTOR_STORE_COLLECTION_NAME": "canonical",
            "VECTOR_STORE_COLLECTION_NAMES": "canonical,run-context",
        }
    )
    mock_retrieve.return_value = ContextPack(
        items=[],
        filters={},
        budgets={},
        usage={},
        transport="gateway",
        context_text="",
        retrieved_at="2026-07-27T00:00:00Z",
        telemetry_id="telemetry",
    )

    service._retrieve_context_pack(mock_request)

    kwargs = mock_retrieve.call_args.kwargs
    assert kwargs["collections"] == ("canonical", "run-context")
    assert kwargs["filters"] == {
        "job_id": "test-correlation-id",
        "tenant": "tenant-1",
        "workspace": "workspace-1",
        "run_id": "run-1",
        "repo": "test-repo",
        "repository": "test-repo",
    }
    assert kwargs["overlay_policy"] == "skip"
    assert kwargs["budgets"] == {"tokens": 800, "latency_ms": 1200}
    assert kwargs["transport"] == "gateway"
    assert kwargs["top_k"] == 4
    assert kwargs["stale_allowed"] is True


def test_authored_collection_outside_configured_scope_is_denied(
    mock_request: AgentExecutionRequest,
) -> None:
    mock_request.parameters["rag"] = {"collections": ["other-tenant"]}
    service = ContextInjectionService(
        env={
            "MOONMIND_RAG_AUTO_CONTEXT": "true",
            "QDRANT_ENABLED": "true",
            "GOOGLE_API_KEY": "test",
            "VECTOR_STORE_COLLECTION_NAME": "canonical",
        }
    )

    with pytest.raises(ValueError, match="not configured"):
        service._retrieve_context_pack(mock_request)


def test_context_metadata_records_actual_stale_result_outcome(
    mock_request: AgentExecutionRequest,
) -> None:
    pack = ContextPack(
        items=[
            ContextItem(
                score=1.0,
                source="overlay.py",
                text="stale context",
                trust_class="workspace_overlay",
                payload={"expires_at": "2000-01-01T00:00:00Z"},
            )
        ],
        filters={},
        budgets={},
        usage={},
        transport="direct",
        context_text="stale context",
        retrieved_at="2026-07-27T00:00:00Z",
        telemetry_id="telemetry",
    )

    ContextInjectionService._record_context_metadata(
        request=mock_request,
        artifact_ref="artifacts/context/pack.json",
        transport="direct",
        items_count=1,
        pack=pack,
    )

    metadata = mock_request.parameters["metadata"]["moonmind"]
    assert metadata["retrievalFreshness"] == "stale_allowed"
    assert metadata["retrievalStaleResultCount"] == 1


def test_context_artifact_identity_includes_authored_retrieval_policy(
    mock_request: AgentExecutionRequest,
    tmp_path,
) -> None:
    first = ContextInjectionService._context_pack_path(
        request=mock_request,
        instruction="Original instruction",
        workspace_path=tmp_path,
    )
    mock_request.parameters["rag"] = {"collections": ["other"]}
    second = ContextInjectionService._context_pack_path(
        request=mock_request,
        instruction="Original instruction",
        workspace_path=tmp_path,
    )

    assert first != second


def test_record_context_metadata_marks_disabled_retrieval_state(
    mock_request: AgentExecutionRequest,
) -> None:
    ContextInjectionService._record_disabled_context_metadata(
        request=mock_request,
        reason="qdrant_disabled",
        initiation_mode="automatic",
    )

    moonmind_meta = mock_request.parameters["metadata"]["moonmind"]
    assert moonmind_meta["retrievalMode"] == "disabled"
    assert moonmind_meta["retrievalDisabledReason"] == "qdrant_disabled"
    assert moonmind_meta["retrievalInitiationMode"] == "automatic"


@pytest.mark.asyncio
@patch("moonmind.rag.context_injection.ContextInjectionService._build_local_fallback_pack")
@patch("moonmind.rag.context_injection.ContextInjectionService._retrieve_context_pack")
async def test_inject_context_records_retrieval_failure_reason_when_fallback_unavailable(
    mock_retrieve,
    mock_build_fallback,
    mock_request: AgentExecutionRequest,
    tmp_path,
) -> None:
    service = ContextInjectionService(env={"MOONMIND_RAG_AUTO_CONTEXT": "true"})
    mock_retrieve.side_effect = RuntimeError(
        "RetrievalGateway request failed due to a network error. Verify connectivity to MOONMIND_RETRIEVAL_URL."
    )
    mock_build_fallback.return_value = None

    result = await service.inject_context(
        request=mock_request,
        workspace_path=tmp_path,
    )

    assert result.instruction == "Original instruction"
    assert result.items_count == 0
    assert result.artifact_path is None
    moonmind_meta = mock_request.parameters["metadata"]["moonmind"]
    assert moonmind_meta["retrievalMode"] == "unavailable"
    assert moonmind_meta["retrievalDisabledReason"] == "retrieval_gateway_unavailable"
