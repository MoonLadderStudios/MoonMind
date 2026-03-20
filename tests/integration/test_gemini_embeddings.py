import os
import sys
from pathlib import Path

import pytest

from moonmind.config.settings import (
    DEFAULT_GOOGLE_EMBEDDING_DIMENSIONS,
    GoogleSettings,
    settings,
)


def _resolve_google_api_key() -> str | None:
    return os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")


def _schema_default_google_embedding() -> tuple[str, int]:
    """Canonical defaults from GoogleSettings (not process env overrides)."""
    g = GoogleSettings.model_fields
    model = g["google_embedding_model"].default
    dims = g["google_embedding_dimensions"].default
    assert isinstance(model, str) and model
    assert isinstance(dims, int) and dims > 0
    return model, dims


def _avoid_local_workflows_package_shadowing() -> None:
    """Remove `<repo>/moonmind` from sys.path to avoid `workflows` shadowing."""
    moonmind_src_path = str(Path(__file__).resolve().parents[2] / "moonmind")
    while moonmind_src_path in sys.path:
        sys.path.remove(moonmind_src_path)


@pytest.mark.integration
def test_default_gemini_embedding_model_live(monkeypatch):
    """Live Gemini call using the schema default embedding model when a key is set."""
    api_key = _resolve_google_api_key()
    if not api_key:
        pytest.skip("GOOGLE_API_KEY or GEMINI_API_KEY is not set.")

    default_model, default_dims = _schema_default_google_embedding()

    _avoid_local_workflows_package_shadowing()
    from moonmind.factories.embed_model_factory import build_embed_model

    monkeypatch.setattr(settings, "default_embedding_provider", "google", raising=False)
    monkeypatch.setattr(settings.google, "google_api_key", api_key, raising=False)
    monkeypatch.setattr(
        settings.google, "google_embedding_model", default_model, raising=False
    )
    monkeypatch.setattr(
        settings.google, "google_embedding_dimensions", default_dims, raising=False
    )

    embed_model, configured_dimensions = build_embed_model(settings)
    embedding = embed_model.get_text_embedding("MoonMind Gemini embedding test prompt.")

    assert getattr(embed_model, "model_name", None) == default_model
    assert configured_dimensions == default_dims
    assert isinstance(embedding, list)
    assert len(embedding) == configured_dimensions
    assert all(isinstance(value, float) for value in embedding)
