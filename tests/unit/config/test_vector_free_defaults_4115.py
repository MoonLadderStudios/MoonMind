"""Vector-free deployment defaults for #4115 (omitted == production path).

The new release ships no live or optional native Qdrant backend. Omitted
values and their documented defaults must exercise the same vector-free
production path; no test may depend on an enabled-by-default native
vector store.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from moonmind.config.settings import AppSettings, QdrantSettings
from moonmind.rag.settings import RagRuntimeSettings

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_qdrant_settings_default_disabled() -> None:
    assert QdrantSettings(_env_file=None).qdrant_enabled is False


def test_vector_store_provider_default_is_not_qdrant() -> None:
    provider = AppSettings(
        _env_file=None, qdrant={"qdrant_enabled": False}
    ).vector_store_provider
    assert provider != "qdrant"


def test_rag_runtime_defaults_vector_free_without_env() -> None:
    settings = RagRuntimeSettings.from_env({})
    assert settings.qdrant_enabled is False
    executable, reason = settings.retrieval_execution_reason(
        {"GOOGLE_API_KEY": "test-key"}, preferred_transport="direct"
    )
    assert executable is False
    assert reason == "qdrant_disabled"


def test_explicit_old_env_still_parses_for_sanitized_fixtures() -> None:
    old_env = {
        "QDRANT_URL": "http://qdrant:6333",
        "QDRANT_HOST": "qdrant",
        "QDRANT_PORT": "6333",
        "QDRANT_ENABLED": "true",
        "VECTOR_STORE_PROVIDER": "qdrant",
    }
    settings = RagRuntimeSettings.from_env(old_env)
    assert settings.qdrant_enabled is True
    assert settings.qdrant_host == "qdrant"


def test_compose_carries_no_qdrant_service_or_wiring() -> None:
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yaml").read_text())
    services = compose.get("services", {})
    assert "qdrant" not in services
    for name, service in services.items():
        env = service.get("environment", [])
        items = (
            env.values() if isinstance(env, dict)
            else (str(item) for item in env)
        )
        assert not any("QDRANT_URL" in str(item) for item in items), name
        depends = service.get("depends_on", {})
        keys = depends.keys() if isinstance(depends, dict) else list(depends)
        assert "qdrant" not in keys, name
    # The old volume is retained through the recovery window, distinctly
    # from moonmind_retrieval_state (classified by #4107).
    volumes = compose.get("volumes", {})
    assert "qdrant-storage" in volumes
    assert "moonmind_retrieval_state" in volumes


def test_env_template_advertises_no_active_qdrant_backend() -> None:
    template = (REPO_ROOT / ".env-template").read_text(encoding="utf-8")
    assert not re.search(r'(?m)^QDRANT_ENABLED="true"', template)
    assert not re.search(r'(?m)^VECTOR_STORE_PROVIDER="qdrant"', template)
    assert not re.search(r'(?m)^QDRANT_URL=', template)
