import pytest
import requests

from moonai.embeddings.gte_qwen2_7b_instruct import GTEQwen27BInstruct


@pytest.fixture(scope="module")
def model():
    """
    Pytest fixture that returns an instance of the GTEQwen27BInstruct class
    pointing to the actual remote server.
    """
    return GTEQwen27BInstruct(
        endpoint="http://10.5.0.2:1234",  # Base URL without /v1/embeddings
        embed_dim=3584,                   # Match your actual model config
        context_length=4096,              # Match your LM Studio config if needed
        evaluation_batch_size=16          # If your server supports batch embeddings
    )


def test_embed_documents(model):
    """
    Test embedding a small list of documents to ensure:
      1) The server is called successfully.
      2) The number of returned embeddings matches the number of inputs.
      3) Each embedding vector has the expected dimension.
    """
    texts = ["Hello world", "Test text"]
    try:
        embeddings = model.embed_documents(texts)
    except requests.RequestException as e:
        pytest.fail(f"HTTP error while calling the embedding server: {e}")

    assert len(embeddings) == len(texts), (
        "Number of embeddings does not match the number of input texts."
    )
    for emb in embeddings:
        assert len(emb) == model.embed_dim, (
            f"Embedding dimension mismatch. Expected {model.embed_dim}, got {len(emb)}."
        )
        # Optionally verify that each element is a float
        for value in emb:
            assert isinstance(value, float), "Embedding values must be floats."


def test_embed_query(model):
    """
    Test embedding a single query string to ensure:
      1) No HTTP/network errors occur.
      2) Returned embedding has the expected dimension.
    """
    query = "What is the capital of France?"
    try:
        embedding = model.embed_query(query)
    except requests.RequestException as e:
        pytest.fail(f"HTTP error while calling the embedding server: {e}")

    assert len(embedding) == model.embed_dim, (
        f"Embedding dimension mismatch. Expected {model.embed_dim}, got {len(embedding)}."
    )
    for value in embedding:
        assert isinstance(value, float), "Query embedding values must be floats."
