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
        vector_collections=("test_collection",),
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
        memory_enabled=True,
        memory_planning="off",
        memory_history="off",
        memory_long_term="off",
        memory_fail_open=True,
        memory_context_budget_tokens=4096,
        planning_workspace_root=None,
        beads_command="bd",
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

def test_from_env_defaults_vector_collections_to_primary() -> None:
    settings = RagRuntimeSettings.from_env(
        {
            "VECTOR_STORE_COLLECTION_NAME": "primary",
            "DEFAULT_EMBEDDING_PROVIDER": "google",
        }
    )
    assert settings.vector_collection == "primary"
    assert settings.vector_collections == ("primary",)

def test_from_env_parses_multiple_vector_collections_with_primary_first() -> None:
    settings = RagRuntimeSettings.from_env(
        {
            "VECTOR_STORE_COLLECTION_NAME": "primary",
            "VECTOR_STORE_COLLECTION_NAMES": "docs, primary, support",
            "DEFAULT_EMBEDDING_PROVIDER": "google",
        }
    )
    assert settings.vector_collection == "primary"
    assert settings.vector_collections == ("primary", "docs", "support")

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

def test_memory_plane_helpers_respect_master_toggle() -> None:
    settings = _settings(
        memory_enabled=True,
        memory_planning="beads",
        memory_history="digest",
        memory_long_term="mem0",
    )

    assert settings.memory_planning_enabled is True
    assert settings.memory_history_enabled is True
    assert settings.memory_long_term_enabled is True

    disabled = _settings(
        memory_enabled=False,
        memory_planning="beads",
        memory_history="digest",
        memory_long_term="mem0",
    )

    assert disabled.memory_planning_enabled is False
    assert disabled.memory_history_enabled is False
    assert disabled.memory_long_term_enabled is False

def test_planning_memory_enabled_requires_global_and_plane_switch() -> None:
    assert _settings(memory_planning="beads").planning_memory_enabled()
    assert not _settings(
        memory_enabled=False,
        memory_planning="beads",
    ).planning_memory_enabled()
    assert not _settings(memory_planning="off").planning_memory_enabled()


def test_from_env_reads_memory_planning_controls() -> None:
    settings = RagRuntimeSettings.from_env(
        {
            "MEMORY_ENABLED": "1",
            "MEMORY_PLANNING": "beads",
            "MEMORY_FAIL_OPEN": "0",
            "MEMORY_CONTEXT_BUDGET_TOKENS": "123",
            "MOONMIND_PLANNING_REPOSITORY_ROOT": "/tmp/repo",
            "BEADS_COMMAND": "bd-test",
        }
    )

    assert settings.planning_memory_enabled()
    assert settings.memory_fail_open is False
    assert settings.memory_context_budget_tokens == 123
    assert settings.planning_workspace_root == "/tmp/repo"
    assert settings.beads_command == "bd-test"


def test_from_env_rejects_unknown_memory_planning_mode() -> None:
    try:
        RagRuntimeSettings.from_env({"MEMORY_PLANNING": "unknown"})
    except ValueError as exc:
        assert "MEMORY_PLANNING" in str(exc)
        assert "beads" in str(exc)
    else:
        raise AssertionError("expected ValueError")
