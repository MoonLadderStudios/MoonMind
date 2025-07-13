import logging
from typing import Dict, List, Optional, Union

from llama_index.core import Settings, StorageContext, VectorStoreIndex
from llama_index.core.schema import TextNode
from llama_index.readers.confluence import ConfluenceReader

# from llama_index.vector_stores import VectorStore # This line will be removed


class ConfluenceIndexer:
    def __init__(
        self,
        base_url: str,
        api_token: str,
        user_name: str,
        cloud: bool = True,
        logger: logging.Logger = None,
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
            base_url=base_url, user_name=user_name, password=api_token, cloud=cloud
        )

    def index(
        self,
        storage_context: StorageContext,
        service_context: Settings,
        space_key: Optional[str] = None,
        page_id: Optional[str] = None,
        page_title: Optional[str] = None,
        cql_query: Optional[str] = None,
        max_pages_to_fetch: Optional[int] = 100,  # Default for space/CQL pagination
    ) -> Dict[str, Union[VectorStoreIndex, int]]:
        """
        Reads Confluence pages based on provided identifiers (page_id, space_key/page_title, cql_query, or space_key for all pages),
        converts them into nodes, and incrementally builds a vector index.
        """
        index = VectorStoreIndex.from_documents(
            [], storage_context=storage_context, embed_model=service_context.embed_model
        )
        total_nodes_indexed = 0
        docs: List[TextNode] = []

        try:
            node_parser = service_context.node_parser
        except AttributeError:
            from llama_index.core.node_parser import SimpleNodeParser

            node_parser = SimpleNodeParser.from_defaults()

        effective_cql = None
        load_args = {}

        if page_id:
            self.logger.info(
                f"Fetching specific page from Confluence by page_id: {page_id}"
            )
            load_args = {"page_ids": [page_id]}
        elif space_key and page_title:
            self.logger.info(
                f"Fetching specific page from Confluence by space_key '{space_key}' and title '{page_title}'"
            )
            # ConfluenceReader uses 'password' for the API token.
            # CQL requires escaping for special characters in title if any, but Confluence API usually handles this.
            # Titles with quotes might need specific handling if issues arise.
            effective_cql = f'space = "{space_key}" AND title = "{page_title}"'
            load_args = {"cql": effective_cql}
        elif cql_query:
            self.logger.info(
                f"Fetching documents from Confluence using CQL query: {cql_query}"
            )
            effective_cql = cql_query
            load_args = {"cql": effective_cql}
        elif space_key:  # Process entire space with pagination
            self.logger.info(
                f"Fetching all documents from Confluence space '{space_key}' using pagination."
            )
            # Pagination logic will be handled below for space_key or CQL queries that might return many results
            load_args = {"space_key": space_key}
        else:
            # This case should be prevented by the Pydantic model validation in the API layer
            self.logger.error("No valid parameters provided for Confluence indexing.")
            raise ValueError(
                "Must provide page_id, space_key (with or without page_title), or cql_query."
            )

        # Handle fetching logic (single page/CQL vs. space pagination)
        if load_args.get("page_ids") or (
            effective_cql and not space_key and not page_title
        ):  # Single page ID or direct CQL for a specific item
            # For single page_id or a CQL query expected to return one/few items,
            # pagination parameters (start, max_num_results) might not be directly applicable in the same way,
            # or ConfluenceReader handles it if the result is unexpectedly large.
            # The ConfluenceReader's load_data with page_ids or cql fetches all matching.
            # max_pages_to_fetch is more for limiting bulk loads.
            docs = self.reader.load_data(**load_args)
            self.logger.info(
                f"Fetched {len(docs)} documents based on provided arguments: {load_args}."
            )

        elif (
            load_args.get("space_key") or effective_cql
        ):  # Fetching entire space or a potentially multi-result CQL
            self.logger.info(
                f"Fetching documents using pagination with arguments: {load_args}"
            )
            start = 0
            # Define a standard batch size for each API request to Confluence
            api_batch_size = 50  # A common default, can be tuned. Confluence Cloud limit is often higher.

            current_load_args = load_args.copy()  # To add pagination params

            while True:
                # Determine how many to request in this batch:
                # It's api_batch_size, unless max_pages_to_fetch imposes a smaller remaining number.
                num_to_request_this_batch = api_batch_size
                if max_pages_to_fetch is not None:
                    remaining_to_fetch = max_pages_to_fetch - len(docs)
                    if remaining_to_fetch <= 0:
                        self.logger.info(
                            f"Already fetched {len(docs)} docs, which meets or exceeds max_pages_to_fetch ({max_pages_to_fetch}). Stopping."
                        )
                        break
                    num_to_request_this_batch = min(api_batch_size, remaining_to_fetch)

                if (
                    num_to_request_this_batch <= 0
                ):  # Should be caught by remaining_to_fetch check, but as safeguard
                    break

                current_load_args["start"] = start
                current_load_args["max_num_results"] = num_to_request_this_batch

                self.logger.info(f"Fetching batch with: {current_load_args}")
                batch_docs = self.reader.load_data(**current_load_args)

                if not batch_docs:
                    self.logger.info("No more documents returned; ending batch fetch.")
                    break

                docs.extend(batch_docs)
                self.logger.info(
                    f"Fetched {len(batch_docs)} documents in this batch. Total docs so far: {len(docs)}"
                )

                if max_pages_to_fetch is not None and len(docs) >= max_pages_to_fetch:
                    self.logger.info(
                        f"Reached max_pages_to_fetch ({max_pages_to_fetch}). Stopping."
                    )
                    docs = docs[:max_pages_to_fetch]  # Trim if over
                    break

                if (
                    len(batch_docs)
                    < num_to_request_this_batch  # Corrected: compare with the number requested
                ):  # Assuming this means last page from Confluence itself
                    self.logger.info(
                        "Final batch fetched based on returned count vs limit (requested less than batch size or last page)."
                    )
                    break
                start += num_to_request_this_batch  # Prepare for next batch, increment by actual requested

        if docs:
            self.logger.info(f"Converting {len(docs)} fetched documents to nodes.")
            nodes = node_parser.get_nodes_from_documents(docs)
            self.logger.info(f"Converted to {len(nodes)} nodes; inserting into index.")
            if nodes:
                index.insert_nodes(nodes)
                total_nodes_indexed = len(nodes)
        else:
            self.logger.info(
                "No documents found or fetched from Confluence based on the criteria."
            )

        if total_nodes_indexed == 0:
            self.logger.info("No documents were indexed overall.")

        storage_context.persist()
        self.logger.info(
            f"Indexing complete. Total nodes indexed: {total_nodes_indexed}. Storage context persisted."
        )
        return {"index": index, "total_nodes_indexed": total_nodes_indexed}
