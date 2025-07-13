import logging
import os
from typing import Dict, Union

from llama_index.core import Settings, StorageContext, VectorStoreIndex
from llama_index.core.node_parser import SimpleNodeParser

# ---------------------------------------------------------------------------
# Import SimpleDirectoryReader with fallbacks to support multiple versions of
# llama_index. The class location changed after v0.12.
# ---------------------------------------------------------------------------

try:
    # Pre-v0.12 path (still works in some minor versions)
    from llama_index.readers.file import SimpleDirectoryReader  # type: ignore
except ImportError:  # pragma: no cover – fall back for newer versions
    try:
        # v0.12+ path – relocated to a sub-module
        from llama_index.readers.file.base import SimpleDirectoryReader  # type: ignore
    except ImportError:
        try:
            # Some versions expose it via core.readers
            from llama_index.core.readers import SimpleDirectoryReader  # type: ignore
        except ImportError:
            # Final fallback: dynamic loader utility
            from llama_index.core import download_loader  # type: ignore

            SimpleDirectoryReader = download_loader("SimpleDirectoryReader")  # type: ignore


class LocalDataIndexer:
    def __init__(self, data_dir: str, logger: logging.Logger = None):
        self.logger = logger or logging.getLogger(__name__)
        if not data_dir:
            raise ValueError("Data directory is required to set up LocalDataIndexer")

        self.data_dir = data_dir
        self.logger.info(f"LocalDataIndexer initialized with data_dir: {self.data_dir}")

    def index(
        self,
        storage_context: StorageContext,
        service_context: Settings,
    ) -> Dict[str, Union[VectorStoreIndex, int]]:
        """
        Reads documents from the specified local directory, converts them into nodes,
        and builds a vector index using the provided storage and service contexts.
        """
        self.logger.info(
            f"Starting indexing of local data from directory: {self.data_dir}"
        )

        if not os.path.exists(self.data_dir) or not os.listdir(self.data_dir):
            self.logger.warning(
                f"Data directory '{self.data_dir}' is empty or does not exist. Skipping indexing."
            )
            return {"index": None, "total_nodes_indexed": 0}

        try:
            self.logger.info(
                f"Loading documents from {self.data_dir} using SimpleDirectoryReader (recursive)."
            )
            # Explicitly require text files for now, can be expanded later.
            # Update: Let SimpleDirectoryReader handle default file types.
            reader = SimpleDirectoryReader(input_dir=self.data_dir, recursive=True)
            documents = reader.load_data()
        except Exception as e:
            self.logger.error(
                f"Failed to load documents from {self.data_dir}: {e}", exc_info=True
            )
            return {"index": None, "total_nodes_indexed": 0}

        if not documents:
            self.logger.info(
                f"No documents found in {self.data_dir}. Skipping indexing."
            )
            return {"index": None, "total_nodes_indexed": 0}

        self.logger.info(f"Loaded {len(documents)} documents from {self.data_dir}.")

        try:
            node_parser = service_context.node_parser
        except AttributeError:
            self.logger.info(
                "Service context does not have a node_parser, using default SimpleNodeParser."
            )
            node_parser = SimpleNodeParser.from_defaults()

        self.logger.info(f"Converting {len(documents)} documents to nodes.")
        nodes = node_parser.get_nodes_from_documents(documents)
        self.logger.info(f"Converted to {len(nodes)} nodes.")

        if not nodes:
            self.logger.info(
                "No nodes were generated from the documents. Skipping index creation."
            )
            return {"index": None, "total_nodes_indexed": 0}

        self.logger.info(f"Creating VectorStoreIndex with {len(nodes)} nodes.")
        # Use the embed_model from Settings instead of passing the whole service_context to from_documents
        index = VectorStoreIndex(
            nodes=nodes,  # Pass nodes directly
            storage_context=storage_context,
            embed_model=service_context.embed_model,  # Explicitly pass embed_model
        )
        total_nodes_indexed = len(nodes)

        # Persist storage (though VectorStoreIndex might do this already depending on its setup)
        # For consistency with other indexers, explicitly call persist.
        # storage_context.persist() # This might be redundant if index creation persists.
        # After reviewing LlamaIndex docs, VectorStoreIndex constructor when given nodes
        # and a storage_context will handle adding to the vector store within that context.
        # The storage_context.persist() is important if the vector store needs explicit saving.
        # For Qdrant, this usually means the data is already sent.
        # However, for consistency with ConfluenceIndexer:
        # self.logger.info("Persisting storage context...")
        # storage_context.persist()
        # Let's rely on the index construction to handle persistence as per modern LlamaIndex.
        # If issues arise, we can re-add explicit persistence.

        self.logger.info(
            f"Indexing complete for {self.data_dir}. Total nodes indexed: {total_nodes_indexed}."
        )
        return {"index": index, "total_nodes_indexed": total_nodes_indexed}
