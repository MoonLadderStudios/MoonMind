import logging
import os

import pytest
import requests
from langchain_community.document_loaders import ConfluenceLoader
from llama_index.embeddings.ollama import OllamaEmbedding
from llama_index.readers.confluence import ConfluenceReader

from moonmind.config.settings import settings
from moonmind.factories.embed_model_factory import build_embed_model

# Configure settings for tests
settings.default_embeddings_provider = "ollama"
settings.ollama.ollama_base_url = "http://ollama:11434"
settings.ollama.ollama_embedding_model = "hf.co/tensorblock/gte-Qwen2-7B-instruct-GGUF:Q6_K"
settings.ollama.ollama_embeddings_dimensions = -1
settings.ollama.ollama_keep_alive = "-1m"

# Configure logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG) # Set logger level directly

# Create a handler that goes to the console (stdout)
ch = logging.StreamHandler()
ch.setLevel(logging.DEBUG) # Set handler level

# Add the handler to your logger
logger.addHandler(ch)

@pytest.fixture(scope="session")
def ollama_running():
    """Fixture to check if Ollama is running at the specified URL."""
    try:
        response = requests.get(settings.ollama.ollama_base_url + "/api/tags")
        if response.status_code == 200:
            return True
    except requests.ConnectionError:
        pass
    return False

@pytest.fixture(scope="session")
def ollama_embeddings_instance(ollama_running):
    """Fixture to create an Ollama embeddings instance."""
    if not ollama_running:
        pytest.skip(f"Ollama is not running at {settings.ollama.ollama_base_url}. Skipping Ollama embeddings tests.")
    embed_model, _ = build_embed_model(settings)
    return embed_model


def test_ollama_embeddings_generation(ollama_embeddings_instance):
    """Test generating embeddings using the Ollama embeddings instance."""
    sample_text = "This is a sample text to generate embeddings."
    embeddings = ollama_embeddings_instance.get_text_embedding(sample_text)
    assert isinstance(embeddings, list)
    assert all(isinstance(e, float) for e in embeddings), "Embeddings should be a list of floats."
    assert len(embeddings) > 10, "Embeddings should have a reasonable length (more than 10 dimensions)." # Basic sanity check

def test_ollama_embeddings_generation_multiple_texts(ollama_embeddings_instance):
    """Test generating embeddings for multiple texts."""
    sample_texts = [
        "First sample text.",
        "Second text for embeddings.",
        "Another example sentence."
    ]
    batched_embeddings = ollama_embeddings_instance.get_text_embedding_batch(sample_texts)
    assert isinstance(batched_embeddings, list)
    assert len(batched_embeddings) == len(sample_texts)
    for embeddings in batched_embeddings:
        assert isinstance(embeddings, list)
        assert all(isinstance(e, float) for e in embeddings), "Embeddings should be a list of floats."
        assert len(embeddings) > 10, "Embeddings should have a reasonable length (more than 10 dimensions)."  # Basic sanity check

def test_ollama_embeddings_long_prompt(ollama_embeddings_instance):
    """Test generating embeddings for a long prompt (approx. 2048 tokens)."""
    # Note: This may truncate
    long_text = "This is a test sentence. " * 500  # Approximate token count, adjust as needed
    embeddings = ollama_embeddings_instance.get_text_embedding(long_text)
    assert isinstance(embeddings, list)
    assert all(isinstance(e, float) for e in embeddings), "Embeddings should be a list of floats."
    assert len(embeddings) > 10, "Embeddings should have a reasonable length (more than 10 dimensions)." #

def test_ollama_embeddings_instance_creation(ollama_embeddings_instance):
    """Test that the Ollama embeddings instance is created successfully."""
    assert ollama_embeddings_instance is not None
    assert isinstance(ollama_embeddings_instance, OllamaEmbedding)

# def test_ollama_embeddings_confluence_document(ollama_embeddings_instance):
#     """Test embedding a Confluence document."""
#     loader = ConfluenceLoader(
#         url=settings.confluence.confluence_url,
#         api_key=settings.confluence.confluence_api_key,
#         username=settings.confluence.confluence_username,
#         space_key=settings.confluence.confluence_default_space_key,
#         include_attachments=False,
#         limit=50
#     )
#     documents = loader.load()
#     logger.info(f"Loaded {len(documents)} documents from Confluence.") # Should now see this log

#     embeddings = ollama_embeddings_instance.get_text_embedding_batch([doc.page_content for doc in documents])
#     assert isinstance(embeddings, list)
#     assert len(embeddings) == len(documents)
#     for embedding in embeddings:
#         assert isinstance(embedding, list)
#         assert all(isinstance(e, float) for e in embedding), "Embeddings should be a list of floats."

def test_ollama_embeddings_confluence_document(ollama_embeddings_instance):
    """Test embedding a Confluence document."""
    loader = ConfluenceLoader(
        url=settings.confluence.confluence_url,
        api_key=settings.confluence.confluence_api_key,
        username=settings.confluence.confluence_username,
        space_key=settings.confluence.confluence_default_space_key,
        include_attachments=False,
        limit=50
    )
    documents = loader.load()
    logger.info(f"Loaded {len(documents)} documents from Confluence.") # Should now see this log

    embeddings = ollama_embeddings_instance.get_text_embedding_batch([doc.page_content for doc in documents])
    assert isinstance(embeddings, list)
    assert len(embeddings) == len(documents)
    for embedding in embeddings:
        assert isinstance(embedding, list)
        assert all(isinstance(e, float) for e in embedding), "Embeddings should be a list of floats."

