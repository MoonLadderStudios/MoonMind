from llama_index.vector_stores.qdrant import QdrantVectorStore
from qdrant_client import QdrantClient
from qdrant_client.http.exceptions import UnexpectedResponse
from qdrant_client.http.models import Distance, VectorParams

from ..config.settings import AppSettings


def build_vector_store(settings: AppSettings, embedder):
    if settings.vector_store_provider == "qdrant":
        return build_qdrant(settings, embedder)
    else:
        raise ValueError(f"Unsupported vector store provider: {settings.vector_store_provider}")


def build_qdrant(settings: AppSettings, embedder):
    # Initialize Qdrant client using settings.
    client = QdrantClient(
        host=settings.qdrant.qdrant_host,
        port=settings.qdrant.qdrant_port
    )

    # Determine the embedding dimensions.
    # If the dimension is set to -1, compute it by embedding a test string.
    embedding_dimensions = settings.ollama.ollama_embeddings_dimensions
    if embedding_dimensions == -1:
        test_vector = embedder("test")  # call the embedding object directly
        embedding_dimensions = len(test_vector)
        print(f"Ollama embedding dimensions: {embedding_dimensions}")

    # Define the desired distance metric.
    desired_distance = Distance.COSINE

    # Attempt to fetch the collection info to validate or create the collection.
    try:
        collection_info = client.get_collection(settings.vector_store_collection_name)
        existing_dimensions = collection_info.config.params.vectors.size
        existing_distance = collection_info.config.params.vectors.distance

        # Validate dimensions and distance metric.
        if existing_dimensions != embedding_dimensions:
            raise ValueError(
                f"Collection {settings.vector_store_collection_name} already exists with "
                f"different embedding dimensions: {existing_dimensions} vs {embedding_dimensions}"
            )
        if existing_distance != desired_distance:
            raise ValueError(
                f"Collection {settings.vector_store_collection_name} already exists with "
                f"a different distance metric: {existing_distance} vs {desired_distance}"
            )

    except UnexpectedResponse as e:
        # If the collection does not exist (typically a 404 error), create it.
        if "404" in str(e):
            print(f"Collection {settings.vector_store_collection_name} does not exist. Creating...")
            client.create_collection(
                collection_name=settings.vector_store_collection_name,
                vectors_config=VectorParams(size=embedding_dimensions, distance=desired_distance)
            )
        else:
            raise e

    # Create the Qdrant vector store using LlamaIndex's QdrantVectorStore.
    vector_store = QdrantVectorStore(
        client=client,
        collection_name=settings.vector_store_collection_name,
        embedding_fn=embedder,
        dim=embedding_dimensions,
        distance=desired_distance  # if required; some versions may infer this from Qdrant
    )

    return vector_store
