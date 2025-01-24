import logging
import time
from typing import Generator, List, Optional, Tuple

from langchain.embeddings.base import Embeddings
from langchain.vectorstores import Qdrant
from qdrant_client import QdrantClient
from qdrant_client.http.exceptions import UnexpectedResponse
from qdrant_client.models import Distance, VectorParams
from urllib3.exceptions import TimeoutError

from .base_connector import BaseConnector, BaseDocument


class CustomLangChainEmbeddings(Embeddings):
    """
    Wraps the user-provided model (with .get_text_embedding) into a LangChain Embeddings interface.
    """
    def __init__(self, model):
        """
        :param model: An object that has a method get_text_embedding(text) -> List[float]
        """
        self.model = model

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        # For batch embedding, you can either implement a batch call or just loop.
        return [self.model.get_text_embedding(t) for t in texts]

    def embed_query(self, text: str) -> List[float]:
        return self.model.get_text_embedding(text)


class QdrantConnector(BaseConnector):
    def __init__(
        self,
        model_class,  # The embedding model class to inject (e.g., GTEQwen2SmallInstructEmbedding)
        qdrant_host: str = "localhost",
        qdrant_port: int = 6333,
        collection_name_prefix: str = "vectors",
        logger=None,
        timeout: int = 300,
        retries: int = 3,
        retry_delay: int = 5,
    ):
        """
        :param model_class: The embedding model class. Example: GTEQwen2SmallInstructEmbedding
        :param qdrant_host: Host for Qdrant
        :param qdrant_port: Port for Qdrant
        :param collection_name_prefix: Prefix used for the Qdrant collection
        :param logger: Optional logger
        :param timeout: Timeout for Qdrant requests
        :param retries: Number of retries on Qdrant operations
        :param retry_delay: Delay between retries
        """
        super().__init__(logger)
        self.logger.info(
            f"Initializing QdrantConnector (LangChain) with host={qdrant_host}, "
            f"port={qdrant_port}, collection_name_prefix={collection_name_prefix}"
        )

        self.timeout = timeout
        self.retries = retries
        self.retry_delay = retry_delay

        # The user-injected model class; we will instantiate it lazily in load_model().
        self.model_class = model_class
        self.model = None  # Will hold the actual model instance once loaded
        self.model_loaded = False

        # Initialize Qdrant client
        self.client = QdrantClient(
            host=qdrant_host,
            port=qdrant_port,
            timeout=timeout
        )

        # Use only one collection name
        self.collection_name = collection_name_prefix

        # Test the Qdrant connection immediately
        success, error_msg = self.test_connection()
        if not success:
            raise ConnectionError(f"Failed to connect to Qdrant: {error_msg}")

        # Placeholder for the LangChain Qdrant VectorStore
        self.qdrant = None

    def load_model(self):
        """
        Lazily load the injected model (if not already loaded),
        create the collection if needed, and instantiate the vector store.
        """
        if self.model_loaded:
            return

        # Instantiate the model using the provided class
        self.model = self.model_class(logger=self.logger)

        # Ensure the Qdrant collection exists or create it
        if not self._collection_exists(self.collection_name):
            self.logger.info(f"Collection '{self.collection_name}' does not exist, creating...")
            self._create_collection(self.collection_name)
        else:
            self.logger.info(f"Using existing collection: {self.collection_name}")

        # Build a LangChain Embeddings object using the user-specified model
        embeddings = CustomLangChainEmbeddings(self.model)

        # Initialize the Qdrant VectorStore (LangChain)
        # You can pass an existing QdrantClient or just pass the host/port in kwargs
        self.qdrant = Qdrant(
            client=self.client,
            collection_name=self.collection_name,
            embeddings=embeddings,
            # The following keys can be used or changed as needed:
            content_key="text",
            metadata_key="metadata",
            distance_func="Cosine",  # or "Dot" or "Euclid", depending on your use case
        )

        self.logger.info("QdrantConnector (LangChain) model initialization complete.")
        self.model_loaded = True

    def _retry_operation(self, operation, *args, **kwargs):
        """
        Generic retry wrapper for Qdrant operations.
        """
        for attempt in range(self.retries):
            try:
                return operation(*args, **kwargs)
            except (TimeoutError, UnexpectedResponse) as e:
                if attempt == self.retries - 1:
                    self.logger.error(f"Operation failed after {self.retries} attempts: {e}")
                    raise
                self.logger.warning(
                    f"Operation attempt {attempt + 1} failed ({e}), retrying in {self.retry_delay} seconds..."
                )
                time.sleep(self.retry_delay)

    def _collection_exists(self, collection_name: str) -> bool:
        """Check if a collection exists with retry logic."""
        return self._retry_operation(self._check_collection_exists, collection_name)

    def _check_collection_exists(self, collection_name: str) -> bool:
        """Internal method to check collection existence."""
        collections = self.list_collections()
        return collection_name in collections

    def _create_collection(self, collection_name: str):
        """
        Create a new Qdrant collection using the embedding dimension
        from the currently loaded model.
        """
        try:
            # We need the dimension, so ensure the model is loaded
            if not self.model:
                raise ValueError("Embedding model is not loaded. Call load_model first.")

            self.client.recreate_collection(
                collection_name=collection_name,
                vectors_config=VectorParams(
                    size=self.model.embed_dim,   # your model must have an attribute embed_dim
                    distance=Distance.COSINE
                )
            )
            # Verify the collection was actually created
            if not self._collection_exists(collection_name):
                raise Exception(f"Failed to create collection {collection_name}")
            self.logger.info(f"Successfully created collection: {collection_name}")
        except Exception as e:
            self.logger.error(f"Error creating collection {collection_name}: {str(e)}")
            raise

    def delete_collection(self, collection_name: str):
        self._retry_operation(self.client.delete_collection, collection_name)

    def index_documents(self, documents: List[BaseDocument], batch_size=10):
        """
        Index the provided documents in batches using the single embedding model.
        """
        if not self.model_loaded:
            self.load_model()

        self.logger.info(f"Indexing {len(documents)} documents in batches of {batch_size}...")

        for i in range(0, len(documents), batch_size):
            batch = documents[i : i + batch_size]
            self.logger.info(f"Processing batch {i // batch_size + 1} ({len(batch)} documents)")

            # Prepare texts, metadatas, and IDs
            texts = [doc.text for doc in batch]
            metadatas = [doc.metadata for doc in batch]
            ids = []
            for doc in batch:
                try:
                    # Convert to integer if you prefer; Qdrant can also handle string IDs
                    doc_id = str(int(doc.id))
                except (ValueError, TypeError):
                    raise ValueError(f"Document ID must be a valid integer. Got: {doc.id}")
                ids.append(doc_id)

            # Use LangChain's add_texts method to upsert
            self.qdrant.add_texts(texts=texts, metadatas=metadatas, ids=ids)
            self.logger.debug(
                f"Indexed {len(batch)} documents into collection '{self.collection_name}'."
            )

        self.logger.info("Document indexing complete.")

    def query(self, query_string: str, top_k: int = 5):
        """
        Perform a vector similarity query against the Qdrant vector store.
        """
        if not self.model_loaded:
            self.load_model()

        self.logger.info(
            f"Executing vector similarity query: '{query_string}' with top_k={top_k}."
        )

        # You can either use similarity_search or .as_retriever() in LangChain
        retrieved_docs = self.qdrant.similarity_search(query_string, k=top_k)
        self.logger.info(f"Retrieved {len(retrieved_docs)} documents from the vector store.")
        return retrieved_docs

    def list_collections(self) -> List[str]:
        """Lists all collections in Qdrant."""
        self.logger.info("Listing all collections.")
        collections = self.client.get_collections()
        collection_names = [collection.name for collection in collections.collections]
        self.logger.info(f"Found collections: {collection_names}")
        return collection_names

    def test_connection(self) -> Tuple[bool, Optional[str]]:
        """
        Test basic connectivity to Qdrant server.
        """
        for attempt in range(self.retries):
            try:
                self.client.get_collections()
                return True, None
            except Exception as e:
                if attempt == self.retries - 1:
                    return False, str(e)
                time.sleep(self.retry_delay)

    def stream_documents(self, **kwargs) -> Generator[BaseDocument, None, None]:
        """
        Implement the abstract method from BaseConnector.
        This will be used to stream documents from Qdrant.
        """
        if not self.model_loaded:
            self.load_model()

        # Get search parameters from kwargs
        query = kwargs.get('query', '')
        top_k = kwargs.get('top_k', 5)

        # Get documents from Qdrant
        results = self.qdrant.similarity_search(query, k=top_k)

        # Convert LangChain documents to BaseDocuments
        for doc in results:
            yield BaseDocument.from_langchain_document(doc)
