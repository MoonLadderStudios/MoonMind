"""Native Manifest/RAG product removal assertions for #4192 (MR5).

The native Manifest product (registry API, ManifestIngest Temporal workflow,
vector-free pipeline), the RAG retrieval backend (embedding + Qdrant), and
their configuration surface are removed. Omitted values and old operator
configuration must exercise the same production path: old ``QDRANT_*``,
``RAG_*``, ``VECTOR_STORE_*``, and ``*_EMBEDDING_*`` keys are inert
(``extra="ignore"``) and cannot reactivate the feature, and no disable flag
is required. This module replaces the #4115-era vector-free-defaults
coverage, which pinned the transitional disabled-but-present settings.
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest
import yaml

from moonmind.config.settings import AppSettings

REPO_ROOT = Path(__file__).resolve().parents[3]

REMOVED_MODULES = (
    "moonmind.rag",
    "moonmind.rag.service",
    "moonmind.rag.settings",
    "moonmind.rag.guardrails",
    "moonmind.rag.context_injection",
    "moonmind.rag.qdrant_client",
    "moonmind.rag.embedding",
    "moonmind.manifest",
    "moonmind.schemas.manifest_models",
    "moonmind.schemas.manifest_v0_models",
    "moonmind.schemas.manifest_ingest_models",
    "moonmind.workflows.temporal.manifest_ingest",
    "moonmind.workflows.temporal.workflows.manifest_ingest",
    "moonmind.workflows.executions.manifest_contract",
    "moonmind.workflows.executions.manifest_errors",
)


@pytest.mark.parametrize("module", REMOVED_MODULES)
def test_retired_product_modules_are_gone(module: str) -> None:
    with pytest.raises(ImportError):
        importlib.import_module(module)


def _assert_no_manifest_rag_vector_fields(settings: AppSettings) -> None:
    for field in (
        "qdrant",
        "rag",
        "google_drive",
        "default_embedding_provider",
        "vector_store_provider",
        "vector_store_collection_name",
    ):
        assert not hasattr(settings, field), field
    assert not hasattr(settings.google, "google_embedding_model")
    assert not hasattr(settings.google, "google_embedding_dimensions")
    assert not hasattr(settings.openai, "openai_embedding_model")
    assert not hasattr(settings.openai, "openai_embedding_dimensions")
    assert not hasattr(settings.temporal, "manifest_continue_as_new_phase_threshold")
    assert not hasattr(settings.workflow, "allow_manifest_path_source")
    assert not hasattr(settings.workflow, "manifest_required_capabilities")


def test_app_settings_carry_no_manifest_rag_vector_fields() -> None:
    _assert_no_manifest_rag_vector_fields(AppSettings(_env_file=None))


def test_old_vector_env_is_inert_and_cannot_reactivate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old_env = {
        "QDRANT_URL": "http://qdrant:6333",
        "QDRANT_HOST": "qdrant",
        "QDRANT_PORT": "6333",
        "QDRANT_ENABLED": "true",
        "QDRANT_API_KEY": "redacted",
        "VECTOR_STORE_PROVIDER": "qdrant",
        "VECTOR_STORE_COLLECTION_NAME": "moonmind",
        "RAG_ENABLED": "true",
        "RAG_SIMILARITY_TOP_K": "5",
        "RAG_MAX_CONTEXT_LENGTH_CHARS": "8000",
        "DEFAULT_EMBEDDING_PROVIDER": "google",
        "GOOGLE_EMBEDDING_MODEL": "gemini-embedding-2-preview",
        "GOOGLE_EMBEDDING_DIMENSIONS": "3072",
        "OPENAI_EMBEDDING_MODEL": "text-embedding-3-small",
        "OPENAI_EMBEDDING_DIMENSIONS": "1536",
        "TEMPORAL_MANIFEST_CONTINUE_AS_NEW_PHASE_THRESHOLD": "5",
        "MOONMIND_ALLOW_MANIFEST_PATH_SOURCE": "true",
        "WORKFLOW_MANIFEST_REQUIRED_CAPABILITIES": "manifest",
    }
    for key, value in old_env.items():
        monkeypatch.setenv(key, value)
    # Must parse without error and expose no retired surface.
    _assert_no_manifest_rag_vector_fields(AppSettings(_env_file=None))


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


def test_env_template_advertises_no_active_vector_backend() -> None:
    template = (REPO_ROOT / ".env-template").read_text(encoding="utf-8")
    assert not re.search(r'(?m)^QDRANT_ENABLED="true"', template)
    assert not re.search(r'(?m)^QDRANT_ENABLED=', template)
    assert not re.search(r'(?m)^RAG_ENABLED=', template)
    assert not re.search(r'(?m)^VECTOR_STORE_PROVIDER=', template)
    assert not re.search(r'(?m)^VECTOR_STORE_COLLECTION_NAME=', template)
    assert not re.search(r'(?m)^VECTOR_STORE_PROVIDER="qdrant"', template)
    assert not re.search(r'(?m)^QDRANT_URL=', template)
    assert not re.search(r'(?m)^GOOGLE_EMBEDDING_MODEL=', template)
    assert not re.search(r'(?m)^OPENAI_EMBEDDING_MODEL=', template)
    assert not re.search(r'(?m)^DEFAULT_EMBEDDING_PROVIDER=', template)
