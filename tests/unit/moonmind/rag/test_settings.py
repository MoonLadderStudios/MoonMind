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
