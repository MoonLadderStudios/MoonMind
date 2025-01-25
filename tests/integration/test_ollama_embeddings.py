import logging
import os

import pytest
import requests
from moonmind.embeddings.ollama_embeddings import OllamaEmbeddings

# Set logging level for debugging requests
logging.basicConfig(level=logging.DEBUG)
requests_log = logging.getLogger("requests.packages.urllib3")
requests_log.setLevel(logging.DEBUG)
requests_log.propagate = True


OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL")
if not OLLAMA_BASE_URL:
    OLLAMA_BASE_URL = "http://ollama:11434"
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL")
if not OLLAMA_MODEL:
    OLLAMA_MODEL = "hf.co/tensorblock/gte-Qwen2-7B-instruct-GGUF:Q6_K"

def is_ollama_available(base_url: str) -> bool:
    """Check if Ollama is available at the given URL."""
    try:
        response = requests.get(base_url + "/api/tags", timeout=5)
        response.raise_for_status()
        return True
    except requests.exceptions.RequestException:
        return False


ollama_available = is_ollama_available(OLLAMA_BASE_URL)
needs_ollama = pytest.mark.skipif(not ollama_available, reason="Ollama not available at {}".format(OLLAMA_BASE_URL))


@pytest.fixture(scope="module")
def ollama_embeddings_instance():
    """Fixture to create an OllamaEmbeddings instance for tests."""
    return OllamaEmbeddings(ollama_base_url=OLLAMA_BASE_URL, model_name="llama2") # Using llama2 as a common model for testing


@needs_ollama
def test_ollama_connection():
    """Test basic connection to the Ollama server."""
    assert is_ollama_available(OLLAMA_BASE_URL), "Ollama is not reachable at {}".format(OLLAMA_BASE_URL)


@needs_ollama
def test_embed_documents_valid_model(ollama_embeddings_instance):
    """Test embedding multiple documents with a valid model."""
    texts = ["This is a document.", "Another document here."]
    embeddings = ollama_embeddings_instance.embed_documents(texts)
    assert isinstance(embeddings, list)
    assert len(embeddings) == len(texts)
    for emb in embeddings:
        assert isinstance(emb, list)
        assert all(isinstance(val, float) for val in emb)
        assert ollama_embeddings_instance.embed_dim is not None  # Dimension should be detected


@needs_ollama
def test_embed_query_valid_model(ollama_embeddings_instance):
    """Test embedding a single query with a valid model."""
    query = "This is a query."
    embedding = ollama_embeddings_instance.embed_query(query)
    assert isinstance(embedding, list)
    assert all(isinstance(val, float) for val in embedding)
    assert ollama_embeddings_instance.embed_dim is not None  # Dimension should be detected


@needs_ollama
def test_get_text_embedding_valid_model(ollama_embeddings_instance):
    """Test embedding a single text using get_text_embedding."""
    text = "This is a single text."
    embedding = ollama_embeddings_instance.get_text_embedding(text)
    assert isinstance(embedding, list)
    assert all(isinstance(val, float) for val in embedding)
    assert ollama_embeddings_instance.embed_dim is not None  # Dimension should be detected


@needs_ollama
def test_embed_documents_empty_list(ollama_embeddings_instance):
    """Test embedding an empty list of documents."""
    texts: List[str] = []
    embeddings = ollama_embeddings_instance.embed_documents(texts)
    assert isinstance(embeddings, list)
    assert not embeddings  # Should be an empty list


@needs_ollama
def test_embed_documents_batching(ollama_embeddings_instance, caplog):
    """Test document embedding with batching (even though it's still one request per doc)."""
    ollama_embeddings_instance.evaluation_batch_size = 2
    texts = ["Text 1", "Text 2", "Text 3", "Text 4", "Text 5"]
    embeddings = ollama_embeddings_instance.embed_documents(texts)
    assert isinstance(embeddings, list)
    assert len(embeddings) == len(texts)
    # No specific batching behavior to assert in this class, but test should run without errors.


@needs_ollama
def test_embed_dim_auto_detection():
    """Test that embed_dim is auto-detected if not provided."""
    embeddings_instance = OllamaEmbeddings(ollama_base_url=OLLAMA_BASE_URL, model_name="llama2", embed_dim=None)
    assert embeddings_instance.embed_dim is None
    texts = ["Test text for dimension detection."]
    embeddings_instance.embed_documents(texts)
    assert embeddings_instance.embed_dim is not None, "embed_dim should be auto-detected"


@needs_ollama
def test_embed_dim_provided_and_matches(caplog):
    """Test when embed_dim is provided and matches the response dimension."""
    expected_dim = 4096  # Example dimension, adjust based on your test model if needed
    embeddings_instance = OllamaEmbeddings(ollama_base_url=OLLAMA_BASE_URL, model_name="llama2", embed_dim=expected_dim)
    texts = ["Text to test provided dimension."]
    embeddings = embeddings_instance.embed_documents(texts)
    assert embeddings_instance.embed_dim == expected_dim
    for emb in embeddings:
        assert len(emb) == expected_dim


@needs_ollama
def test_embed_dim_provided_and_mismatches(caplog):
    """Test when embed_dim is provided but mismatches the response dimension (should log warning and update)."""
    initial_dim = 128  # Intentionally wrong dimension
    embeddings_instance = OllamaEmbeddings(
        ollama_base_url=OLLAMA_BASE_URL, model_name="llama2", embed_dim=initial_dim, logger=logging.getLogger(__name__)
    )
    texts = ["Text to test mismatched dimension."]
    with caplog.at_level(logging.WARNING):
        embeddings = embeddings_instance.embed_documents(texts)
    assert "Expected embedding size" in caplog.text
    assert "Updating embed_dim to match." in caplog.text
    assert embeddings_instance.embed_dim != initial_dim  # Should be updated to the correct dimension
    for emb in embeddings:
        assert len(emb) == embeddings_instance.embed_dim


@needs_ollama
def test_invalid_model_name():
    """Test behavior with an invalid model name."""
    embeddings_instance = OllamaEmbeddings(ollama_base_url=OLLAMA_BASE_URL, model_name="non-existent-model")
    texts = ["Text with invalid model."]
    with pytest.raises(requests.exceptions.HTTPError) as excinfo:
        embeddings_instance.embed_documents(texts)
    assert excinfo.value.response.status_code == 404  # Or the appropriate error code from Ollama for model not found


@needs_ollama
def test_ollama_server_error():
    """Simulate an Ollama server error (e.g., by pointing to a wrong port, though harder to simulate server-side errors)."""
    # For this test, we can try to use an invalid URL to simulate connection error.
    invalid_ollama_url = "http://localhost:99999"  # Assuming nothing is running here
    embeddings_instance = OllamaEmbeddings(ollama_base_url=invalid_ollama_url, model_name="llama2")
    texts = ["Text to trigger server error."]
    with pytest.raises(requests.exceptions.ConnectionError):  # Or appropriate exception for connection issues
        embeddings_instance.embed_documents(texts)


@needs_ollama
def test_unexpected_json_response_format(ollama_embeddings_instance, mocker):
    """Test handling of unexpected JSON response format from Ollama."""
    mock_response = mocker.Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"wrong_key": "value"}  # Simulate incorrect JSON
    mocker.patch("requests.post", return_value=mock_response)

    texts = ["Text for bad JSON response."]
    with pytest.raises(ValueError) as excinfo:
        ollama_embeddings_instance.embed_documents(texts)
    assert "Unexpected response format from Ollama embedding API" in str(excinfo.value)


@needs_ollama
def test_unexpected_embedding_type(ollama_embeddings_instance, mocker):
    """Test handling of unexpected 'embedding' type in JSON response."""
    mock_response = mocker.Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"embedding": "not_a_list"}  # Simulate incorrect embedding type
    mocker.patch("requests.post", return_value=mock_response)

    texts = ["Text for bad embedding type."]
    with pytest.raises(ValueError) as excinfo:
        ollama_embeddings_instance.embed_documents(texts)
    assert "Unexpected 'embedding' type in response. Expected a list, got: <class 'str'>" in str(excinfo.value)