"""Unit tests for RagRuntimeSettings (DOC-REQ-008)."""

from __future__ import annotations

from moonmind.rag.settings import RagRuntimeSettings

def _settings(**overrides: object) -> RagRuntimeSettings:
    defaults = dict(
        qdrant_url=None,
        qdrant_host="localhost",
        qdrant_port=6333,
        qdrant_api_key=None,
        vector_collection="test_collection",
        embedding_provider="google",
        embedding_model="test-model",
        embedding_dimensions=768,
        similarity_top_k=5,
        max_context_chars=8000,
        overlay_mode="collection",
        overlay_ttl_hours=24,
        overlay_chunk_chars=1200,
        overlay_chunk_overlap=120,
        retrieval_gateway_url=None,
        statsd_host=None,
        statsd_port=None,
        job_id="job-1",
        run_id="run-1",
        rag_enabled=True,
        qdrant_enabled=True,
        memory_enabled=False,
        memory_long_term="off",
        memory_fail_open=True,
        memory_context_budget_tokens=None,
        memory_namespace_id="default",
        mem0_api_key=None,
        mem0_user_id=None,
    )
    defaults.update(overrides)
    return RagRuntimeSettings(**defaults)

def test_overlay_collection_name_sanitizes_run_id() -> None:
    settings = _settings()
    name = settings.overlay_collection_name("run/with spaces&specials!")
    assert "__overlay__" in name
    assert "/" not in name
    assert " " not in name
    assert "&" not in name

def test_overlay_collection_name_truncates_at_128() -> None:
    settings = _settings()
    name = settings.overlay_collection_name("a" * 200)
    assert len(name) <= 128

def test_as_filter_metadata_includes_job_and_run() -> None:
    settings = _settings(job_id="j-1", run_id="r-1")
    meta = settings.as_filter_metadata()
    assert meta == {"job_id": "j-1", "run_id": "r-1"}

def test_as_filter_metadata_omits_none_values() -> None:
    settings = _settings(job_id=None, run_id=None)
    meta = settings.as_filter_metadata()
    assert meta == {}

def test_embedding_provider_supported_recognizes_valid_providers() -> None:
    for provider in ("google", "openai"):
        settings = _settings(embedding_provider=provider)
        assert settings.embedding_provider_supported()

def test_embedding_provider_supported_rejects_unknown() -> None:
    settings = _settings(embedding_provider="unknown_provider")
    assert not settings.embedding_provider_supported()

def test_retrieval_execution_reason_disabled() -> None:
    settings = _settings(rag_enabled=False)
    ok, reason = settings.retrieval_execution_reason()
    assert not ok
    assert reason == "rag_disabled"

def test_retrieval_execution_reason_unsupported_provider() -> None:
    settings = _settings(embedding_provider="bad")
    ok, reason = settings.retrieval_execution_reason()
    assert not ok
    assert reason == "embedding_provider_unsupported"

def test_retrieval_execution_reason_gateway_ok() -> None:
    settings = _settings(retrieval_gateway_url="http://gw:8000")
    ok, reason = settings.retrieval_execution_reason(
        {"MOONMIND_RETRIEVAL_TOKEN": "scoped-token"},
        preferred_transport="gateway",
    )
    assert ok
    assert reason == "ok"

def test_retrieval_execution_reason_gateway_requires_scoped_auth() -> None:
    settings = _settings(retrieval_gateway_url="http://gw:8000")
    ok, reason = settings.retrieval_execution_reason(
        preferred_transport="gateway",
    )
    assert not ok
    assert reason == "retrieval_gateway_auth_missing"

def test_retrieval_execution_reason_gateway_missing_url() -> None:
    settings = _settings(retrieval_gateway_url=None)
    ok, reason = settings.retrieval_execution_reason(preferred_transport="gateway")
    assert not ok
    assert reason == "retrieval_gateway_url_missing"

def test_retrieval_executable_mirrors_reason() -> None:
    settings = _settings(rag_enabled=False)
    assert not settings.retrieval_executable()

    settings_ok = _settings(retrieval_gateway_url="http://gw:8000")
    assert settings_ok.retrieval_executable(
        {"MOONMIND_RETRIEVAL_TOKEN": "scoped-token"},
        preferred_transport="gateway",
    )

def test_long_term_memory_disabled_by_default() -> None:
    settings = RagRuntimeSettings.from_env({})
    ok, reason = settings.long_term_memory_execution_reason()
    assert not ok
    assert reason == "memory_disabled"
    assert not settings.long_term_memory_enabled()

def test_long_term_memory_mem0_requires_api_key() -> None:
    settings = RagRuntimeSettings.from_env(
        {"MEMORY_ENABLED": "true", "MEMORY_LONG_TERM": "mem0"}
    )
    ok, reason = settings.long_term_memory_execution_reason()
    assert not ok
    assert reason == "mem0_api_key_missing"

def test_long_term_memory_mem0_enabled_with_api_key() -> None:
    settings = RagRuntimeSettings.from_env(
        {
            "MEMORY_ENABLED": "true",
            "MEMORY_LONG_TERM": "mem0",
            "MEM0_API_KEY": "mem0-secret",
            "MEMORY_CONTEXT_BUDGET_TOKENS": "600",
            "MEMORY_NAMESPACE_ID": "tenant-a",
            "MEM0_USER_ID": "mem-user",
        }
    )
    ok, reason = settings.long_term_memory_execution_reason()
    assert ok
    assert reason == "ok"
    assert settings.long_term_memory_enabled()
    assert settings.memory_context_budget_tokens == 600
    assert settings.memory_namespace_id == "tenant-a"
    assert settings.mem0_user_id == "mem-user"

def test_long_term_memory_invalid_mode_fails_closed_to_off() -> None:
    settings = RagRuntimeSettings.from_env(
        {"MEMORY_ENABLED": "true", "MEMORY_LONG_TERM": "unsupported"}
    )
    ok, reason = settings.long_term_memory_execution_reason()
    assert not ok
    assert reason == "long_term_memory_disabled"
