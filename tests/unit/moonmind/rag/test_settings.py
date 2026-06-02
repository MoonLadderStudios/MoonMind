import pytest

from moonmind.rag.settings import RagRuntimeSettings

def test_runtime_settings_from_env_overrides_defaults():
    env = {
        "QDRANT_HOST": "localhost",
        "QDRANT_PORT": "6333",
        "QDRANT_URL": "http://localhost:6333",
        "GOOGLE_EMBEDDING_DIMENSIONS": "3072",
        "DEFAULT_EMBEDDING_PROVIDER": "google",
        "MOONMIND_RUN_ID": "run-123",
        "VECTOR_STORE_COLLECTION_NAME": "repo-main",
    }
    settings = RagRuntimeSettings.from_env(env)
    assert settings.qdrant_host == "localhost"
    assert settings.vector_collection == "repo-main"
    assert settings.vector_collections == ("repo-main",)
    assert settings.overlay_collection_name("run-123").startswith(
        "repo-main__overlay__run-123"
    )
    assert settings.resolved_transport(None) == "direct"

def test_runtime_settings_from_env_accepts_multiple_vector_collections():
    env = {
        "VECTOR_STORE_COLLECTION_NAME": "repo-main",
        "VECTOR_STORE_COLLECTION_NAMES": "repo-main, docs-main, repo-main, specs",
    }

    settings = RagRuntimeSettings.from_env(env)

    assert settings.vector_collection == "repo-main"
    assert settings.vector_collections == ("repo-main", "docs-main", "specs")
    assert settings.resolve_collections(None) == ("repo-main", "docs-main", "specs")
    assert settings.resolve_collections([" docs-main ", "repo-main", "docs-main"]) == (
        "docs-main",
        "repo-main",
    )

def test_runtime_settings_rejects_requested_collections_outside_configured_set():
    env = {
        "VECTOR_STORE_COLLECTION_NAME": "repo-main",
        "VECTOR_STORE_COLLECTION_NAMES": "repo-main, docs-main",
    }
    settings = RagRuntimeSettings.from_env(env)

    try:
        settings.resolve_collections(["docs-main", "private-corpus"])
    except ValueError as exc:
        assert "private-corpus" in str(exc)
        assert "repo-main, docs-main" in str(exc)
    else:
        raise AssertionError("expected unconfigured collection to be rejected")

def test_runtime_settings_from_env_reads_memory_flags():
    env = {
        "MEMORY_ENABLED": "1",
        "MEMORY_PLANNING": "beads",
        "MEMORY_HISTORY": "digest",
        "MEMORY_LONG_TERM": "mem0",
        "MEMORY_FAIL_OPEN": "0",
        "MEMORY_CONTEXT_BUDGET_TOKENS": "2048",
    }
    settings = RagRuntimeSettings.from_env(env)

    assert settings.memory_enabled is True
    assert settings.memory_planning == "beads"
    assert settings.memory_history == "digest"
    assert settings.memory_long_term == "mem0"
    assert settings.memory_fail_open is False
    assert settings.memory_context_budget_tokens == 2048
    assert settings.memory_planning_enabled is True
    assert settings.memory_history_enabled is True
    assert settings.memory_long_term_enabled is True

def test_runtime_settings_memory_master_toggle_disables_planes():
    env = {
        "MEMORY_ENABLED": "false",
        "MEMORY_PLANNING": "beads",
        "MEMORY_HISTORY": "digest",
        "MEMORY_LONG_TERM": "mem0",
    }
    settings = RagRuntimeSettings.from_env(env)

    assert settings.memory_enabled is False
    assert settings.memory_planning_enabled is False
    assert settings.memory_history_enabled is False
    assert settings.memory_long_term_enabled is False

def test_runtime_settings_rejects_unknown_memory_modes():
    with pytest.raises(ValueError, match="MEMORY_HISTORY"):
        RagRuntimeSettings.from_env({"MEMORY_HISTORY": "unknown"})

def test_runtime_settings_rejects_non_positive_memory_budget():
    with pytest.raises(ValueError, match="MEMORY_CONTEXT_BUDGET_TOKENS"):
        RagRuntimeSettings.from_env({"MEMORY_CONTEXT_BUDGET_TOKENS": "0"})

def test_retrieval_executable_gateway_uses_scoped_token_not_embedding_keys():
    env = {
        "RAG_ENABLED": "1",
        "DEFAULT_EMBEDDING_PROVIDER": "google",
        "MOONMIND_RETRIEVAL_URL": "http://gateway:8080",
        "MOONMIND_RETRIEVAL_TOKEN": "scoped-token",
        "QDRANT_ENABLED": "0",
        "GOOGLE_API_KEY": "",
    }
    settings = RagRuntimeSettings.from_env(env)

    assert settings.retrieval_executable(env, preferred_transport="gateway") is True

def test_retrieval_executable_gateway_requires_url():
    env = {
        "RAG_ENABLED": "1",
        "DEFAULT_EMBEDDING_PROVIDER": "google",
        "QDRANT_ENABLED": "1",
        "GOOGLE_API_KEY": "test-key",
    }
    settings = RagRuntimeSettings.from_env(env)

    assert settings.retrieval_executable(env, preferred_transport="gateway") is False

def test_retrieval_executable_google_requires_google_api_key():
    env = {
        "RAG_ENABLED": "1",
        "QDRANT_ENABLED": "1",
        "DEFAULT_EMBEDDING_PROVIDER": "google",
        "GEMINI_API_KEY": "gemini-only",
    }
    settings = RagRuntimeSettings.from_env(env)

    assert settings.retrieval_executable(env, preferred_transport="direct") is False

def test_retrieval_execution_reason_reports_unsupported_provider():
    env = {
        "RAG_ENABLED": "1",
        "QDRANT_ENABLED": "1",
        "DEFAULT_EMBEDDING_PROVIDER": "unsupported",
    }
    settings = RagRuntimeSettings.from_env(env)

    executable, reason = settings.retrieval_execution_reason(env)

    assert executable is False
    assert reason == "embedding_provider_unsupported"
