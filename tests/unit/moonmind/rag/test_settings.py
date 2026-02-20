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
    assert settings.overlay_collection_name("run-123").startswith(
        "repo-main__overlay__run-123"
    )
    assert settings.resolved_transport(None) == "direct"
