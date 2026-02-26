from unittest.mock import MagicMock, patch

import pytest

from moonmind.config.settings import AppSettings
from moonmind.factories.embed_model_factory import build_embed_model


@pytest.fixture
def mock_settings():
    settings = MagicMock(spec=AppSettings)
    settings.default_embedding_provider = "openai"
    settings.openai = MagicMock()
    settings.openai.openai_api_key = "placeholder-openai-key"
    settings.openai.openai_embedding_model = "text-embedding-3-small"
    settings.openai.openai_embedding_dimensions = 1536
    return settings


def test_build_embed_model_openai(mock_settings):
    """Test that build_embed_model correctly initializes OpenAIEmbedding."""
    with patch(
        "moonmind.factories.embed_model_factory.OpenAIEmbedding"
    ) as MockOpenAIEmbedding:
        mock_instance = MagicMock()
        MockOpenAIEmbedding.return_value = mock_instance

        embed_model, dimensions = build_embed_model(mock_settings)

        # Verify OpenAIEmbedding was called with correct parameters
        MockOpenAIEmbedding.assert_called_once_with(
            model="text-embedding-3-small",
            api_key="placeholder-openai-key",
            dimensions=1536,
        )

        assert embed_model == mock_instance
        assert dimensions == 1536


def test_build_embed_model_openai_with_explicit_key(mock_settings):
    """Test that build_embed_model uses explicitly provided OpenAI API key."""
    with patch(
        "moonmind.factories.embed_model_factory.OpenAIEmbedding"
    ) as MockOpenAIEmbedding:
        explicit_key = "override-openai-key"

        build_embed_model(mock_settings, openai_api_key=explicit_key)

        MockOpenAIEmbedding.assert_called_once_with(
            model="text-embedding-3-small",
            api_key=explicit_key,
            dimensions=1536,
        )


def test_build_embed_model_openai_missing_key(mock_settings):
    """Test that build_embed_model raises ValueError when OpenAI API key is missing."""
    mock_settings.openai.openai_api_key = None

    with pytest.raises(
        ValueError, match="OpenAI API key is not configured for OpenAI embeddings."
    ):
        build_embed_model(mock_settings)
