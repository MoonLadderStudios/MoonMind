import logging
import os
from typing import Dict, List, Optional, Any, Union

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
        page_ids: Optional[List[str]] = None,
        confluence_fetch_batch_size: int = 100
    ) -> Dict[str, Union[VectorStoreIndex, int]]:
        """
        Reads Confluence pages from the specified space or specific page IDs,
        converts them into nodes, and incrementally builds a vector index using
        the provided storage and service contexts.
        """
        # Initialize an empty index (with no initial documents)
        index = VectorStoreIndex.from_documents(
            [],
            storage_context=storage_context,
            service_context=service_context
        )
        total_nodes_indexed = 0
        
        # Use the node parser from the service context if provided; otherwise create a default parser.
        try:
            node_parser = service_context.node_parser
        except AttributeError:
            from llama_index.core.node_parser import SimpleNodeParser
            node_parser = SimpleNodeParser.from_defaults()

        if page_ids:
            self.logger.info(f"Fetching {len(page_ids)} specific pages from Confluence: {page_ids}")
            docs = self.reader.load_data(page_ids=page_ids) # Fetches all at once
            self.logger.info(f"Fetched {len(docs)} documents by page_ids.")
            if docs:
                self.logger.info(f"Converting {len(docs)} documents (from page_ids) to nodes.")
                nodes = node_parser.get_nodes_from_documents(docs)
                self.logger.info(f"Converted to {len(nodes)} nodes; inserting into index.")
                if nodes: # Ensure there are nodes to insert
                    index.insert_nodes(nodes)
                    total_nodes_indexed = len(nodes) 
            else:
                self.logger.info("No documents found for the given page_ids.")
        else: # space_key processing
            self.logger.info(f"Fetching documents from Confluence space '{space_key}' using pagination.")
            start = 0
            while True:
                self.logger.info(
                    f"Fetching documents from Confluence space '{space_key}' starting at {start} with batch size {confluence_fetch_batch_size}"
                )
                batch_docs = self.reader.load_data(
                    space_key=space_key,
                    start=start,
                    max_num_results=confluence_fetch_batch_size
                )
                if not batch_docs:
                    self.logger.info("No more documents returned; ending batch fetch for space_key.")
                    break 
                
                self.logger.info(f"Fetched {len(batch_docs)} documents in this batch. Converting to nodes.")
                batch_nodes = node_parser.get_nodes_from_documents(batch_docs)
                self.logger.info(f"Converted to {len(batch_nodes)} nodes; inserting batch into index.")
                if batch_nodes: # Ensure there are nodes to insert
                    index.insert_nodes(batch_nodes)
                    total_nodes_indexed += len(batch_nodes)
                
                if len(batch_docs) < confluence_fetch_batch_size:
                    self.logger.info("Final batch fetched for space_key.")
                    break
                start += confluence_fetch_batch_size
            self.logger.info(f"Total documents processed in batches for space '{space_key}'.")

        # Common exit logic
        if total_nodes_indexed == 0:
            self.logger.info("No documents were indexed overall.")
        
        storage_context.persist()
        self.logger.info(f"Indexing complete. Total nodes indexed: {total_nodes_indexed}. Storage context persisted.")
        return {"index": index, "total_nodes_indexed": total_nodes_indexed}
