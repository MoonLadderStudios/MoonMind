import logging
import os
from typing import Dict, List, Optional, Union

from github import Github
from llama_index.core import Settings, StorageContext, VectorStoreIndex
from llama_index.core.node_parser import \
    SimpleNodeParser  # Default node parser
from llama_index.readers.github import GithubRepositoryReader

from fastapi import HTTPException


class GitHubIndexer:
    def __init__(
        self,
        github_token: Optional[str] = None,
        logger: Optional[logging.Logger] = None,
    ):
        self.logger = logger or logging.getLogger(__name__)
        self.github_token = github_token
        # GithubRepositoryReader is initialized in the index method as it needs owner/repo.

    def index(
        self,
        repo_full_name: str,  # e.g., "owner/repo_name"
        storage_context: StorageContext,
        service_context: Settings,
        filter_extensions: Optional[List[str]] = None,
        branch: Optional[str] = None,
    ) -> Dict[str, Union[VectorStoreIndex, int]]:
        self.logger.info(f"Starting GitHub indexing for repo: {repo_full_name} on branch: {branch or 'default'}")

        try:
            owner, repo_name = repo_full_name.split('/')
            if not owner or not repo_name: # Ensure neither part is empty
                raise ValueError("Owner and repo_name must not be empty.")
        except ValueError as e:
            self.logger.error(f"Invalid repo_full_name format: {repo_full_name}. Expected 'owner/repo_name'. Error: {e}")
            raise ValueError(f"Invalid repo_full_name format: {repo_full_name}. Expected 'owner/repo_name'. Error: {e}")

        # Determine the branch to use
        if not branch:
            self.logger.info(f"No branch specified for {repo_full_name}. Attempting to fetch default branch.")
            try:
                g = Github(self.github_token)
                repo = g.get_repo(f"{owner}/{repo_name}")
                branch = repo.default_branch
                self.logger.info(f"Default branch for {repo_full_name} is '{branch}'.")
            except Exception as e:
                self.logger.warning(f"Failed to fetch default branch for {repo_full_name}: {e}. Falling back to 'main'.")
                branch = "main"
        else:
            self.logger.info(f"Using specified branch '{branch}' for {repo_full_name}.")


        reader_filter_extensions_tuple = None
        if filter_extensions:
            # Ensure filter_extensions is a list of strings, e.g. [".py", ".md"]
            if not isinstance(filter_extensions, list) or not all(isinstance(ext, str) for ext in filter_extensions):
                self.logger.warning(f"Invalid filter_extensions format: {filter_extensions}. Should be List[str]. Ignoring.")
            else:
                reader_filter_extensions_tuple = (filter_extensions, "INCLUDE")
                self.logger.info(f"Filtering for extensions: {filter_extensions}")


        self.logger.info(f"Initializing GithubRepositoryReader for {owner}/{repo_name}")
        try:
            reader = GithubRepositoryReader(
                owner=owner,
                repo=repo_name,
                github_token=self.github_token,
                filter_file_extensions=reader_filter_extensions_tuple,
                # filter_directories=None, # Example: (["lib", "docs"], FilterType.INCLUDE)
                verbose=False, # Set to False to avoid duplicate logging if our logger is sufficient
                concurrent_requests=5
            )
        except Exception as e:
            self.logger.error(f"Failed to initialize GithubRepositoryReader for {repo_full_name}: {e}")
            # This could be due to various reasons, including issues with underlying git command if not installed
            raise HTTPException(status_code=500, detail=f"Failed to initialize GitHub reader: {e}") from e

        docs = []
        try:
            self.logger.info(f"Loading documents from branch: {branch}")
            docs = reader.load_data(branch=branch)
        except Exception as e:
            self.logger.error(f"Error loading data from GitHub repo {repo_full_name} on branch {branch}: {e}")
            # This can include errors like repo not found, branch not found, auth issues, rate limits.
            # Re-raise as HTTPException to be caught by FastAPI error handling.
            raise HTTPException(status_code=500, detail=f"Failed to load repository content from {repo_full_name} branch {branch}: {e}") from e

        # Initialize an empty index first, this will also be returned if no docs are found.
        index = VectorStoreIndex.from_documents(
            [], # No initial documents
            storage_context=storage_context,
            embed_model=service_context.embed_model
        )
        total_nodes_indexed = 0

        if not docs:
            self.logger.info("No documents found in the repository matching criteria.")
            # Persist storage context even if no documents, for consistency
            storage_context.persist()
            self.logger.info("GitHub indexing complete (no documents found) and storage context persisted.")
            return {"index": index, "total_nodes_indexed": total_nodes_indexed}

        self.logger.info(f"Loaded {len(docs)} documents from GitHub. Converting to nodes.")

        try:
            node_parser = service_context.node_parser
        except AttributeError:
            self.logger.info("No node_parser found in service_context, using SimpleNodeParser.from_defaults().")
            node_parser = SimpleNodeParser.from_defaults()

        nodes = node_parser.get_nodes_from_documents(docs)
        total_nodes_indexed = len(nodes)
        self.logger.info(f"Converted to {total_nodes_indexed} nodes.")

        # Insert the new nodes into the existing (empty) index
        if nodes:
            index.insert_nodes(nodes)
            self.logger.info(f"Successfully inserted {total_nodes_indexed} nodes into the index from {repo_full_name}.")
        else:
            self.logger.info(f"No nodes were generated from the loaded documents for {repo_full_name}.")


        storage_context.persist()
        self.logger.info("GitHub indexing complete and storage context persisted.")

        return {"index": index, "total_nodes_indexed": total_nodes_indexed}
