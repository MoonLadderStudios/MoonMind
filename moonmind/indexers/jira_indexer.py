import logging
from typing import Dict, Union, Optional

# LlamaIndex core imports
from llama_index.core import Settings, StorageContext, VectorStoreIndex
from llama_index.core.node_parser import SimpleNodeParser

# LlamaIndex Jira reader (ensure this import path is correct if it's part of an integration package)
from llama_index.readers.jira import JiraReader # Assuming it's directly available, adjust if it's like llama_index.readers.jira.base.JiraReader or from an integration package

class JiraIndexer:
    def __init__(
        self,
        jira_url: str,
        username: str,
        api_token: str,
        logger: Optional[logging.Logger] = None,
    ):
        self.logger = logger or logging.getLogger(__name__)

        if not jira_url:
            raise ValueError("Jira URL is required.")
        if not username:
            raise ValueError("Jira username is required.")
        if not api_token:
            raise ValueError("Jira API token is required.")

        self.jira_url = jira_url
        self.username = username
        self.api_token = api_token

        # Process jira_url to remove scheme for JiraReader
        # The original self.jira_url is kept as is for logging or other purposes.
        processed_jira_url = self.jira_url
        if "://" in processed_jira_url: # Check if "://" is present
            # Split by "://" and take the last part (e.g., "example.com" from "http://example.com")
            # This handles http, https, and even the unusual https://://
            processed_jira_url = processed_jira_url.split("://", 1)[-1]

        # Initialize JiraReader using Basic Authentication
        # The JiraReader expects the server_url without "https://" for basic_auth server part typically
        # but the LlamaIndex JiraReader documentation shows it prepends "https://://" if not present for the 'server' parameter.
        # For basic_auth, it might be directly `server=jira_url` (if jira_url includes https://)
        # or `server=f"https://{jira_url}"` if jira_url is just "your-domain.atlassian.net"
        # The example `ConfluenceReader` takes `base_url` which includes `https://`.
        # The JiraReader documentation for basic_auth seems to imply the `server_url` in its dict should be just the domain.
        # Let's assume `jira_url` is provided as `your-domain.atlassian.net` and construct the full URL for the reader.
        # The JiraReader's basic_auth parameter takes server_url like "https://your-domain.atlassian.net"
        # So if jira_url is "your-domain.atlassian.net", then it becomes f"https://{jira_url}"
        # However, the constructor also directly takes `email`, `api_token`, `server_url`.
        # `server_url` for the constructor is the base URL of the Jira instance.
        # As per task, removing scheme before passing to JiraReader.
        self.reader = JiraReader(
            server_url=processed_jira_url, # URL without scheme e.g. domain.atlassian.net
            email=self.username,
            api_token=self.api_token,
        )
        self.logger.info(f"JiraIndexer initialized for URL: {self.jira_url}") # Log original URL

    def index(
        self,
        jql_query: str,
        storage_context: StorageContext,
        service_context: Settings, # Changed from ServiceContext to Settings for LlamaIndex v0.10+
        jira_fetch_batch_size: int = 50,
    ) -> Dict[str, Union[VectorStoreIndex, int]]:
        self.logger.info(f"Starting Jira indexing for JQL query: '{jql_query}'")

        if not jql_query:
            raise ValueError("JQL query is required for indexing.")

        # Initialize an empty index
        index = VectorStoreIndex.from_documents(
            [],
            storage_context=storage_context,
            embed_model=service_context.embed_model, # Use embed_model from Settings
            # service_context=service_context, # Deprecated in LlamaIndex v0.10+
        )
        total_nodes_indexed = 0

        try:
            node_parser = service_context.node_parser
        except AttributeError: # Fallback if node_parser is not directly on Settings (older LlamaIndex or custom setup)
            self.logger.warning("service_context.node_parser not found, falling back to SimpleNodeParser.from_defaults()")
            node_parser = SimpleNodeParser.from_defaults()


        start_at = 0
        while True:
            self.logger.info(
                f"Fetching Jira issues with JQL: '{jql_query}', startAt: {start_at}, maxResults: {jira_fetch_batch_size}"
            )
            try:
                batch_docs = self.reader.load_data(
                    query=jql_query, start_at=start_at, max_results=jira_fetch_batch_size
                )
            except Exception as e:
                self.logger.error(f"Failed to load data from Jira: {e}", exc_info=True)
                # Depending on the error, might want to break or retry. For now, break.
                break

            if not batch_docs:
                self.logger.info("No more documents returned from Jira; ending batch fetch.")
                break

            self.logger.info(f"Fetched {len(batch_docs)} documents in this batch. Converting to nodes.")
            batch_nodes = node_parser.get_nodes_from_documents(batch_docs)
            self.logger.info(f"Converted to {len(batch_nodes)} nodes; inserting batch into index.")

            if batch_nodes:
                index.insert_nodes(batch_nodes)
                total_nodes_indexed += len(batch_nodes)

            # The condition 'if len(batch_docs) < jira_fetch_batch_size:' has been removed.
            # The loop will now only break if batch_docs is empty.
            # This ensures that even if a partial batch is received,
            # the next iteration will attempt to fetch starting from the new 'start_at',
            # and will only stop when JiraReader returns an empty list.

            start_at += jira_fetch_batch_size

        if total_nodes_indexed == 0:
            self.logger.info("No documents were indexed from Jira for the given JQL query.")
        else:
            self.logger.info(f"Persisting storage context for Jira index. Total nodes indexed: {total_nodes_indexed}")
            storage_context.persist()
            self.logger.info("Storage context persisted successfully.")

        return {"index": index, "total_nodes_indexed": total_nodes_indexed}
