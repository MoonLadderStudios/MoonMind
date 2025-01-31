from langchain_qdrant import QdrantVectorStore
from qdrant_client import QdrantClient
from qdrant_client.http.exceptions import UnexpectedResponse
from qdrant_client.http.models import Distance, VectorParams

from ..config.settings import AppSettings


def build_vector_store_provider(settings: AppSettings, embeddings):
    if settings.vector_store_provider == "qdrant":
        return build_qdrant_provider(settings, embeddings)
    else:
        raise ValueError(f"Unsupported vector store provider: {settings.vector_store_provider}")


def build_qdrant_provider(settings: AppSettings, embeddings):
    # TODO: Do we want to support multiple collections per deployment?
    # Initialize Qdrant client
    client = QdrantClient(host=settings.qdrant.qdrant_host, port=settings.qdrant.qdrant_port)

    embedding_dimensions = settings.ollama.ollama_embeddings_dimensions

    if embedding_dimensions == -1:
        # Get embedding dimensions dynamically
        test_vector = embeddings.embed_query("test")
        embedding_dimensions = len(test_vector)
        print(f"Ollama embedding dimensions: {embedding_dimensions}")

    # Define the desired distance metric
    desired_distance = Distance.COSINE

    try:
        # Attempt to fetch the collection info
        collection_info = client.get_collection(settings.vector_store_collection_name)
        # Access the config directly from collection_info
        existing_dimensions = collection_info.config.params.vectors.size
        existing_distance = collection_info.config.params.vectors.distance

        # Validate dimensions and distance
        if existing_dimensions != embedding_dimensions:
            raise ValueError(
                f"Collection {settings.vector_store_collection_name} already exists "
                f"with different embedding dimensions: {existing_dimensions} != {embedding_dimensions}"
            )
        if existing_distance != desired_distance:
            raise ValueError(
                f"Collection {settings.vector_store_collection_name} already exists "
                f"with a different distance metric: {existing_distance} != {desired_distance}"
            )

    except UnexpectedResponse as e:
        if "404" in str(e):  # Collection does not exist
            print(f"Collection {settings.vector_store_collection_name} does not exist. Creating...")
            client.create_collection(
                collection_name=settings.vector_store_collection_name,
                vectors_config=VectorParams(size=embedding_dimensions, distance=desired_distance),
            )
        else:
            raise e  # Re-raise any other errors

    # Create the vector store
    vector_store = QdrantVectorStore(
        client=client,
        collection_name=settings.vector_store_collection_name,
        embedding=embeddings,
    )

    return vector_store