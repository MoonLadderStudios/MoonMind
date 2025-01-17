import logging
import time
from typing import Generator, List, Optional

from llama_index.core import Document as LlamaDocument
from llama_index.core import StorageContext, VectorStoreIndex
from llama_index.core.base.response.schema import Response as QueryResult
from llama_index.vector_stores.qdrant import QdrantVectorStore
from moonai.embeddings.gte_large_en_v15 import GTELargeEnV15Embedding
from moonai.embeddings.gte_qwen2_small_instruct import \
    GTEQwen2SmallInstructEmbedding
from moonai.embeddings.nv_embed_v2 import NVEmbedV2Embedding
from qdrant_client import QdrantClient
from qdrant_client.http.exceptions import UnexpectedResponse
from qdrant_client.models import Distance, VectorParams
from urllib3.exceptions import TimeoutError

from .base_connector import BaseConnector, BaseDocument


class QdrantConnector(BaseConnector):
    def __init__(
        self,
        qdrant_host='localhost',
        qdrant_port=6333,
        collection_name_prefix='vectors',
        logger=None,
        timeout: int = 300,
        retries: int = 3,
        retry_delay: int = 5
    ):
        super().__init__(logger)
        self.logger.info(f"Initializing QdrantConnector with host={qdrant_host}, port={qdrant_port}, collection_name_prefix={collection_name_prefix}")

        self.timeout = timeout
        self.retries = retries
        self.retry_delay = retry_delay

        # Initialize client with timeout
        self.client = QdrantClient(
            host=qdrant_host,
            port=qdrant_port,
            timeout=timeout
        )
        self.collection_name_prefix = collection_name_prefix
        self.collection_name1 = f"{collection_name_prefix}_model1"
        self.collection_name2 = f"{collection_name_prefix}_model2"

        self.models_loaded = False

        success, error_msg = self.test_connection()
        if not success:
            raise ConnectionError(f"Failed to connect to Qdrant: {error_msg}")

    def load_models(self):
        if self.models_loaded:
            return

        self.model1 = GTEQwen2SmallInstructEmbedding(logger=self.logger)
        self.model2 = GTELargeEnV15Embedding(logger=self.logger)

        # Create/verify collections for both models
        collections_to_check = [(self.collection_name1, self.model1), (self.collection_name2, self.model2)]

        for coll_name, model in collections_to_check:
            if not self._collection_exists(coll_name):
                self.logger.info(f"Collection '{coll_name}' does not exist, creating...")
                self._create_collection(coll_name)
            else:
                self.logger.info(f"Using existing collection: {coll_name}")

        # Initialize vector stores and indices for both models
        self.vector_store1 = QdrantVectorStore(
            collection_name=self.collection_name1,
            client=self.client,
            batch_size=20,
            embed_model=self.model1
        )

        self.vector_store2 = QdrantVectorStore(
            collection_name=self.collection_name2,
            client=self.client,
            batch_size=20,
            embed_model=self.model2
        )

        # Create separate indices for each model
        self.index1 = VectorStoreIndex.from_vector_store(
            vector_store=self.vector_store1,
            embed_model=self.model1,
            llm=None
        )

        self.index2 = VectorStoreIndex.from_vector_store(
            vector_store=self.vector_store2,
            embed_model=self.model2,
            llm=None
        )

        self.logger.info("QdrantConnector initialization complete")

        self.models_loaded = True

    def _retry_operation(self, operation, *args, **kwargs):
        """
        Generic retry wrapper for Qdrant operations.
        """
        for attempt in range(self.retries):
            try:
                return operation(*args, **kwargs)
            except (TimeoutError, UnexpectedResponse) as e:
                if attempt == self.retries - 1:
                    self.logger.error(f"Operation failed after {self.retries} attempts")
                    raise
                self.logger.warning(f"Operation attempt {attempt + 1} failed, retrying in {self.retry_delay} seconds...")
                time.sleep(self.retry_delay)

    def _collection_exists(self, collection_name) -> bool:
        """Check if a collection exists with retry logic."""
        return self._retry_operation(self._check_collection_exists, collection_name)

    def _check_collection_exists(self, collection_name) -> bool:
        """Internal method to check collection existence."""
        collections = self.list_collections()
        return collection_name in collections

    def _create_collection(self, collection_name: str):
        """Creates a collection for the specified model."""
        model = self.model1 if collection_name.endswith("model1") else self.model2
        try:
            self.client.recreate_collection(
                collection_name=collection_name,
                vectors_config=VectorParams(size=model.embed_dim, distance=Distance.COSINE)
            )
            # Add verification that collection was created
            if not self._collection_exists(collection_name):
                raise Exception(f"Failed to create collection {collection_name}")
            self.logger.info(f"Successfully created collection: {collection_name}")
        except Exception as e:
            self.logger.error(f"Error creating collection {collection_name}: {str(e)}")
            raise

    def delete_collection(self, collection_name: str):
        self.client.delete_collection(collection_name)

    def index_documents(self, documents: List[BaseDocument], batch_size=10):
        """Indexes the provided documents in batches using both embedding models."""
        if not self.models_loaded:
            self.load_models()

        self.logger.info(f"Indexing {len(documents)} documents in batches of {batch_size}")

        for i in range(0, len(documents), batch_size):
            batch = documents[i:i + batch_size]
            self.logger.info(f"Processing batch {i // batch_size + 1} ({len(batch)} documents)")

            for doc in batch:
                # Convert to integer for Qdrant
                try:
                    doc_id = int(doc.id)
                except (ValueError, TypeError):
                    raise ValueError(f"Document ID must be a valid integer. Got: {doc.id}")

                # Create LlamaDocument with string ID for embedding
                if isinstance(doc, LlamaDocument):
                    llama_doc = doc
                else:
                    llama_doc = LlamaDocument(
                        text=doc.text,
                        metadata=doc.metadata,
                        id_=str(doc_id)  # LlamaDocument needs string
                    )

                # Get embeddings from both models
                embedding1 = self.model1.get_text_embedding(llama_doc.text)
                embedding2 = self.model2.get_text_embedding(llama_doc.text)

                # Upload to both collections
                for collection_name, embedding in [
                    (self.collection_name1, embedding1),
                    (self.collection_name2, embedding2)
                ]:
                    self.client.upsert(
                        collection_name=collection_name,
                        points=[{
                            'id': doc_id,  # Use integer ID for Qdrant
                            'vector': embedding,
                            'payload': {
                                'text': doc.text,
                                'metadata': doc.metadata
                            }
                        }]
                    )

                self.logger.debug(f"Indexed document with ID: {doc_id} in both collections")

        self.logger.info("Document indexing complete for both models")

    def dual_query(self, query_string: str, top_k: int = 5):
        if not self.models_loaded:
            self.load_models()

        self.logger.info(f"Executing vector similarity query: '{query_string}' with top_k={top_k}")

        # Get the retriever from the index
        retriever1 = self.index1.as_retriever(
            similarity_top_k=top_k,
            vector_store_query_mode="default"
        )

        # Use the retriever to fetch raw documents
        retrieved_documents1 = retriever1.retrieve(query_string)
        self.logger.info(f"Retrieved {len(retrieved_documents1)} documents from model1.")

        retriever2 = self.index2.as_retriever(
            similarity_top_k=top_k,
            vector_store_query_mode="default"
        )

        retrieved_documents2 = retriever2.retrieve(query_string)
        self.logger.info(f"Retrieved {len(retrieved_documents2)} documents from model2.")
        return retrieved_documents1, retrieved_documents2

    def list_collections(self):
        """Lists ALL collections in Qdrant."""
        self.logger.info("Listing all collections")
        collections = self.client.get_collections()
        collection_names = [collection.name for collection in collections.collections]
        self.logger.info(f"Found collections: {collection_names}")
        return collection_names

    # def stream_documents(self, **kwargs) -> Generator[BaseDocument, None, None]:
    #     """
    #     Stream documents from Qdrant. This implements the abstract method from BaseConnector.
    #     Note: Qdrant is primarily for storing/querying vectors, not streaming documents.
    #     """
    #     try:
    #         self.logger.info("Streaming documents from Qdrant")
    #         documents = self.client.scroll(
    #             collection_name=self.collection_name1,
    #             limit=100  # Adjust batch size as needed
    #         )[0]  # scroll returns tuple (docs, next_page_offset)

    #         for doc in documents:
    #             # Qdrant returns integer IDs, convert to string for consistency
    #             doc_id = str(doc.id)
    #             yield BaseDocument(
    #                 text=doc.payload.get("text", ""),
    #                 metadata=doc.payload.get("metadata", {}),
    #                 id=doc_id
    #             )

    #     except Exception as e:
    #         self.logger.error(f"Error streaming documents: {str(e)}")
    #         raise

    def test_connection(self) -> tuple[bool, Optional[str]]:
        """Test basic connectivity to Qdrant server."""
        for attempt in range(self.retries):
            try:
                # Just check if we can reach the server
                self.client.get_collections()
                return True, None
            except Exception as e:
                if attempt == self.retries - 1:
                    return False, str(e)
                time.sleep(self.retry_delay)