import logging
from typing import List

from llama_index.core import Settings, VectorStoreIndex
from llama_index.core.retrievers import VectorIndexRetriever
from llama_index.core.schema import NodeWithScore

logger = logging.getLogger(__name__)


class QdrantRAG:
    def __init__(
        self,
        index: VectorStoreIndex,
        service_settings: Settings,
        similarity_top_k: int = 3,
    ):
        """
        Initializes the RAG component.
        - index: The LlamaIndex VectorStoreIndex to retrieve from.
        - service_settings: The global LlamaIndex Settings object (contains embed_model, llm, etc.).
        - similarity_top_k: The number of top similar documents to retrieve.
        """
        if not index:
            raise ValueError("VectorStoreIndex must be provided for QdrantRAG.")
        self.index = index
        self.service_settings = service_settings
        self.similarity_top_k = similarity_top_k

        self.retriever = VectorIndexRetriever(
            index=self.index,
            similarity_top_k=self.similarity_top_k,
            # embed_model can be implicitly taken from global Settings if index was built with it
        )

    def retrieve_context(self, query_text: str) -> List[NodeWithScore]:
        """Retrieves relevant context nodes for a given query."""
        logger.info(
            f"Retrieving context for query: '{query_text}' with top_k={self.similarity_top_k}"
        )
        if not query_text:
            logger.warning("Empty query text received for retrieval.")
            return []
        try:
            retrieved_nodes = self.retriever.retrieve(query_text)
            logger.debug(
                f"Retrieved {len(retrieved_nodes)} nodes. First node score (if any): {retrieved_nodes[0].score if retrieved_nodes else 'N/A'}"
            )
        except Exception as e:
            logger.exception(
                f"Error during context retrieval for query '{query_text}': {e}"
            )
            return []
        return retrieved_nodes
