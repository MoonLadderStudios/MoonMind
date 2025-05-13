import logging
import os
from typing import List

from llama_index import (ServiceContext, StorageContext, VectorStoreIndex,
                         load_index_from_storage)
from llama_index.core.schema import TextNode
from llama_index.embeddings import BaseEmbedding
from llama_index.readers.confluence import ConfluenceReader
from llama_index.vector_stores import VectorStore
from llama_index.vector_stores.qdrant import QdrantVectorStore

from .base_connector import BaseConnector, BaseDocument


class ConfluenceIndexer:
    def __init__(
        self,
        base_url: str,
        api_token: str,
        user_name: str,
        cloud: bool = True,
        logger: logging.Logger = None
    ):
        self.logger = logger or logging.getLogger(__name__)
        if not base_url:
            raise ValueError("Confluence URL is required to set up Confluence")
        if not api_token:
            raise ValueError("Confluence API key is required to set up Confluence")
        if not user_name:
            raise ValueError("Confluence username is required to set up Confluence")

        self.base_url = base_url
        self.api_token = api_token
        self.user_name = user_name
        self.cloud = cloud
        self.reader = ConfluenceReader(
            base_url=base_url,
            user_name=user_name,
            password=api_token,
            cloud=cloud
        )

    def index(
        self,
        space_key: str,
        storage_context: StorageContext,
        service_context: ServiceContext,
        batch_size: int = 100
    ) -> VectorStoreIndex:
        """
        Reads Confluence pages from the specified space in batches,
        converts them into nodes, and incrementally builds a vector index using
        the provided storage and service contexts.
        """
        # Initialize an empty index (with no initial documents)
        index = VectorStoreIndex.from_documents(
            [],
            storage_context=storage_context,
            service_context=service_context
        )
        start = 0
        total_nodes_indexed = 0

        # Use the node parser from the service context if provided; otherwise create a default parser.
        try:
            node_parser = service_context.node_parser
        except AttributeError:
            from llama_index.core.node_parser import SimpleNodeParser
            node_parser = SimpleNodeParser.from_defaults()

        while True:
            self.logger.info(
                f"Fetching documents from Confluence space '{space_key}' starting at {start} with batch size {batch_size}"
            )
            # Load a batch of documents using the ConfluenceReader.
            docs = self.reader.load_data(
                space_key=space_key,
                start=start,
                max_num_results=batch_size
            )
            if not docs:
                self.logger.info("No more documents returned; ending batch fetch.")
                break

            self.logger.info(f"Fetched {len(docs)} documents; converting documents to nodes.")
            # Convert the documents to nodes.
            nodes = node_parser.get_nodes_from_documents(docs)
            self.logger.info(f"Converted to {len(nodes)} nodes; inserting batch into index.")
            # Insert the batch of nodes.
            index.insert_nodes(nodes)
            total_nodes_indexed += len(nodes)

            if len(docs) < batch_size:
                self.logger.info("Final batch fetched.")
                break

            start += batch_size

        self.logger.info(f"Total nodes indexed: {total_nodes_indexed}")
        # Persist the storage context so that the index is saved.
        storage_context.persist()
        self.logger.info("Indexing complete and storage context persisted.")

        return index
