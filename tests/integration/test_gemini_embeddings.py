import os
import sys
from pathlib import Path

import pytest

from moonmind.config.settings import settings


def _resolve_google_api_key() -> str | None:
    return os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")


def _avoid_local_workflows_package_shadowing() -> None:
    """Remove `<repo>/moonmind` from sys.path to avoid `workflows` shadowing."""
    moonmind_src_path = str(Path(__file__).resolve().parents[2] / "moonmind")
    while moonmind_src_path in sys.path:
        sys.path.remove(moonmind_src_path)


def test_gemini_embeddings_generation(monkeypatch):
    """Run a live Gemini embedding call when an API key is available."""
    api_key = _resolve_google_api_key()
    if not api_key:
        pytest.skip("GOOGLE_API_KEY or GEMINI_API_KEY is not set.")

    _avoid_local_workflows_package_shadowing()
    from moonmind.factories.embed_model_factory import build_embed_model

    monkeypatch.setattr(
        settings, "default_embedding_provider", "google", raising=False
    )
    monkeypatch.setattr(settings.google, "google_api_key", api_key, raising=False)
    monkeypatch.setattr(
        settings.google,
        "google_embedding_model",
        "gemini-embedding-001",
        raising=False,
    )

    embed_model, configured_dimensions = build_embed_model(settings)
    embedding = embed_model.get_text_embedding(
        "MoonMind Gemini embedding test prompt."
    )

    assert getattr(embed_model, "model_name", None) == "gemini-embedding-001"
    assert configured_dimensions == settings.google.google_embedding_dimensions
    assert isinstance(embedding, list)
    assert len(embedding) > 10
    assert all(isinstance(value, float) for value in embedding)
