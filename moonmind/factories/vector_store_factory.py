import logging

from llama_index.vector_stores.qdrant import QdrantVectorStore
from qdrant_client import QdrantClient
from qdrant_client.http.exceptions import UnexpectedResponse
from qdrant_client.http.models import Distance

from ..config.settings import AppSettings

logger = logging.getLogger(__name__)


def build_vector_store(settings: AppSettings, embed_model, embed_dimensions: int = -1):
    """
    High-level factory for building a vector store based on provider settings.
    `embed_model` is any embedding model that implements a `.embed()` method.
    """
    if settings.vector_store_provider == "qdrant":
        return build_qdrant(settings, embed_model, embed_dimensions)
    else:
        raise ValueError(
            f"Unsupported vector store provider: {settings.vector_store_provider}"
        )


def build_qdrant(settings: AppSettings, embed_model, embed_dimensions: int = -1):
    """
    Builds and returns a Qdrant-based vector store. Allows the user to configure
    different embed models by passing `embed_model` directly into this function.
    """

    client = QdrantClient(
        host=settings.qdrant.qdrant_host,  # Corrected access
        port=settings.qdrant.qdrant_port,  # Corrected access
        check_compatibility=False,  # Add this line
    )

    if embed_dimensions == -1:
        # Use a public API method to determine embedding dimensions
        test_vector = embed_model.get_query_embedding("test")
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
    except UnexpectedResponse:
        # If the collection doesn't exist, we can create it.
        # This is a design choice. For now, we assume it should exist.
        logger.warning(
            f"Qdrant collection '{settings.vector_store_collection_name}' not found. "
            "Please ensure it is initialized if you intend to use vector search. "
            "Vector store functionality will be disabled."
        )
        return None  # Return None to indicate the vector store is not available

    except Exception as e:
        logger.error(
            f"An unexpected error occurred while building Qdrant vector store: {e}"
        )
        return None  # Return None for any other unexpected errors

    vector_store = QdrantVectorStore(
        client=client,
        collection_name=settings.vector_store_collection_name,
    )

    return vector_store
