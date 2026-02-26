import os
import sys
from pathlib import Path

import pytest


def _avoid_local_workflows_package_shadowing() -> None:
    """Remove `<repo>/moonmind` from sys.path to avoid `workflows` shadowing."""
    moonmind_src_path = str(Path(__file__).resolve().parents[2] / "moonmind")
    while moonmind_src_path in sys.path:
        sys.path.remove(moonmind_src_path)


def _resolve_openai_api_key() -> str | None:
    return os.getenv("OPENAI_API_KEY")


_avoid_local_workflows_package_shadowing()
from llama_index.embeddings.openai import OpenAIEmbedding

from moonmind.config.settings import settings
from moonmind.factories.embed_model_factory import build_embed_model


def test_openai_embeddings_generation(monkeypatch):
    """Verify OpenAI embedding instantiation and basic structure (integration)."""
    api_key = _resolve_openai_api_key()
    if not api_key:
        pytest.skip("OPENAI_API_KEY is not set.")

    monkeypatch.setattr(settings, "default_embedding_provider", "openai", raising=False)
    monkeypatch.setattr(settings.openai, "openai_api_key", api_key, raising=False)
    monkeypatch.setattr(
        settings.openai,
        "openai_embedding_model",
        "text-embedding-3-small",
        raising=False,
    )
    monkeypatch.setattr(
        settings.openai, "openai_embedding_dimensions", 1536, raising=False
    )

    embed_model, configured_dimensions = build_embed_model(settings)

    # We don't necessarily want to make a real network call in all CI environments,
    # but we can verify the object is correctly constructed.
    # If the user really wants to test the API, they can provide a key.

    assert configured_dimensions == 1536

    # Basic sanity check of the instance
    assert isinstance(embed_model, OpenAIEmbedding)
    assert (
        getattr(embed_model, "model", None) == "text-embedding-3-small"
        or getattr(embed_model, "model_name", None) == "text-embedding-3-small"
    )
