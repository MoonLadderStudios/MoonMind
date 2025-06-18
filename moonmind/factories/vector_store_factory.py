from llama_index.core import Settings, StorageContext
from llama_index.vector_stores.qdrant import QdrantVectorStore
from qdrant_client import QdrantClient
from qdrant_client.http.exceptions import UnexpectedResponse
from qdrant_client.http.models import Distance, VectorParams

from ..config.settings import AppSettings


def build_vector_store(settings: AppSettings, embed_model, embed_dimensions: int = -1):
    """
    High-level factory for building a vector store based on provider settings.
    `embed_model` is any embedding model that implements a `.embed()` method.
    """
    if settings.vector_store_provider == "qdrant":
        return build_qdrant(settings, embed_model, embed_dimensions)
    else:
        raise ValueError(f"Unsupported vector store provider: {settings.vector_store_provider}")


def build_qdrant(settings: AppSettings, embed_model, embed_dimensions: int = -1):
    """
    Builds and returns a Qdrant-based vector store. Allows the user to configure
    different embed models by passing `embed_model` directly into this function.
    """

    client = QdrantClient(
        host=settings.qdrant.qdrant_host, # Corrected access
        port=settings.qdrant.qdrant_port,  # Corrected access
        check_compatibility=False # Add this line
    )

    if embed_dimensions == -1:
        # Use a public API method to determine embedding dimensions
        test_vector = embed_model.embed_query("test")
        embed_dimensions = len(test_vector)
        print(f"Embedding dimensions set to: {embed_dimensions}")

    desired_distance = Distance.COSINE
    try:
        collection_info = client.get_collection(settings.vector_store_collection_name)
        existing_dimensions = collection_info.config.params.vectors.size
        existing_distance = collection_info.config.params.vectors.distance
        if existing_dimensions != embed_dimensions:
            raise ValueError(
                f"Collection '{settings.vector_store_collection_name}' already exists with "
                f"different embedding dimensions: {existing_dimensions} vs {embed_dimensions}"
            )
        if existing_distance != desired_distance:
            raise ValueError(
                f"Collection '{settings.vector_store_collection_name}' already exists with "
                f"a different distance metric: {existing_distance} vs {desired_distance}"
            )
    except UnexpectedResponse as e:
        if "404" in str(e):
            raise RuntimeError(
                f"Qdrant collection '{settings.vector_store_collection_name}' not found. "
                "Please ensure it is initialized by running the init_vector_db.py script "
                "before starting the API service."
            )
        else:
            raise e

    vector_store = QdrantVectorStore(
        client=client,
        collection_name=settings.vector_store_collection_name,
    )

    return vector_store

