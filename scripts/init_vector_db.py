import os
import sys
import logging
import qdrant_client
from moonmind.indexers.confluence_indexer import ConfluenceIndexer
from moonmind.indexers.github_indexer import GitHubIndexer
from moonmind.config.settings import settings
from llama_index.core import StorageContext, ServiceContext
from llama_index.vector_stores.qdrant import QdrantVectorStore
from llama_index.embeddings.google import GoogleGenerativeAiEmbedding

# Set up basic logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

if __name__ == "__main__":
    logger.info("Starting vector database initialization script.")

    init_db = os.getenv("INIT_DATABASE", "false").lower() == "true"
    if not init_db:
        logger.info("INIT_DATABASE environment variable is not set to 'true', exiting.")
        sys.exit(1)

    logger.info("INIT_DATABASE is 'true', proceeding with database initialization.")

    try:
        # 1. Confluence Configuration Check
        logger.info("Checking Confluence configuration...")
        if not settings.confluence.confluence_enabled:
            logger.error("Confluence is not enabled in settings. Exiting.")
            sys.exit()
        if not settings.confluence.confluence_url:
            logger.error("Confluence URL is not configured. Exiting.")
            sys.exit()
        if not settings.confluence.confluence_username:
            logger.error("Confluence username is not configured. Exiting.")
            sys.exit()
        if not settings.confluence.confluence_api_key:
            logger.error("Confluence API key is not configured. Exiting.")
            sys.exit()
        if not settings.confluence.confluence_space_keys:
            logger.error("No Confluence space keys configured (CONFLUENCE_SPACE_KEYS). Exiting.")
            sys.exit()
        logger.info("Confluence configuration check passed.")

        # 2. Parse Confluence Space Keys
        space_keys_str = settings.confluence.confluence_space_keys
        space_keys = [key.strip() for key in space_keys_str.split(',') if key.strip()]
        if not space_keys:
            logger.error("No valid Confluence space keys found after parsing. Exiting.")
            sys.exit()
        logger.info(f"Found Confluence space keys to process: {space_keys}")

        # 3. Set up Qdrant client and VectorStore
        logger.info(f"Initializing Qdrant client for host: {settings.qdrant_host}, port: {settings.qdrant_port}")
        client = qdrant_client.QdrantClient(host=settings.qdrant_host, port=settings.qdrant_port)
        
        logger.info(f"Using vector store collection name: {settings.vector_store_collection_name}")
        logger.info(f"Using Google embeddings dimensions for Qdrant: {settings.google.google_embeddings_dimensions}")
        vector_store = QdrantVectorStore(
            client=client,
            collection_name=settings.vector_store_collection_name,
            embed_dim=settings.google.google_embeddings_dimensions  # Using Google's dimensions
        )
        logger.info("Qdrant client and vector store initialized successfully.")

        # 4. Set up StorageContext
        storage_context = StorageContext.from_defaults(vector_store=vector_store)
        logger.info("StorageContext initialized successfully.")

        # 5. Set up Embedding Model (Google)
        logger.info(f"Initializing Google Embeddings model: {settings.google.google_embeddings_model}")
        if not settings.google.google_api_key:
            logger.error("Google API key (GOOGLE_API_KEY) is not configured for embeddings. Exiting.")
            sys.exit()
            
        embed_model = GoogleGenerativeAiEmbedding(
            model_name=settings.google.google_embeddings_model,
            api_key=settings.google.google_api_key
        )
        logger.info("Google Embedding model initialized successfully.")

        # 6. Set up ServiceContext
        # LLM is set to None as we are only performing indexing operations.
        service_context = ServiceContext.from_defaults(embed_model=embed_model, llm=None)
        logger.info("ServiceContext initialized successfully (LLM is None for indexing).")

        # 7. Instantiate ConfluenceIndexer
        logger.info("Initializing ConfluenceIndexer...")
        confluence_indexer = ConfluenceIndexer(
            base_url=settings.confluence.confluence_url,
            user_name=settings.confluence.confluence_username,
            api_token=settings.confluence.confluence_api_key,
            logger=logger  # Pass the logger instance
        )
        logger.info("ConfluenceIndexer initialized successfully.")

        # 8. Loop through space keys and index
        logger.info(f"Starting to process {len(space_keys)} Confluence space(s).")
        for space_key in space_keys:
            logger.info(f"Processing Confluence space: {space_key}")
            try:
                # The ConfluenceIndexer's index method is expected to handle the actual indexing
                # process, including fetching documents and adding them to the vector store
                # via the provided storage_context and service_context.
                index_result = confluence_indexer.index(
                    space_key=space_key,
                    storage_context=storage_context,
                    service_context=service_context
                    # Assuming `index` method does not require page_ids or include_attachments by default
                    # or handles them internally based on some logic or further configuration.
                    # If these are mandatory, the ConfluenceIndexer logic or this call needs adjustment.
                )
                total_nodes_indexed = 0
                if index_result and isinstance(index_result, dict) and 'total_nodes_indexed' in index_result:
                    total_nodes_indexed = index_result.get('total_nodes_indexed', 0)

                logger.info(f"Successfully processed space {space_key}. Nodes indexed: {total_nodes_indexed}.")
            except Exception as e:
                logger.error(f"Error indexing space {space_key}: {e}", exc_info=True)
        
        logger.info("Finished processing all Confluence spaces.")

        # 9. GitHub Repository Indexing
        logger.info("Starting GitHub repository indexing process...")
        if not settings.github.github_enabled:
            logger.info("GitHub integration is not enabled via settings. Skipping GitHub indexing.")
        elif not settings.github.github_token:
            logger.warning("GITHUB_TOKEN is not configured. Skipping GitHub indexing.")
        elif not settings.github.github_repos:
            logger.info("No GitHub repositories configured in GITHUB_REPOS. Skipping GitHub indexing.")
        else:
            repos_str = settings.github.github_repos
            github_repo_list = [repo.strip() for repo in repos_str.split(',') if repo.strip()]
            if not github_repo_list:
                logger.info("No valid GitHub repository names found after parsing GITHUB_REPOS. Skipping GitHub indexing.")
            else:
                logger.info(f"Found GitHub repositories to index: {github_repo_list}")
                logger.info("Initializing GitHubIndexer...")
                try:
                    github_indexer = GitHubIndexer(github_token=settings.github.github_token, logger=logger)
                    logger.info("GitHubIndexer initialized.")

                    default_branch = settings.github.default_branch or "main"
                    for repo_full_name in github_repo_list:
                        logger.info(f"Processing GitHub repository: {repo_full_name} (branch: {default_branch})")
                        try:
                            # The existing storage_context and service_context should be reused
                            index_result = github_indexer.index(
                                repo_full_name=repo_full_name,
                                branch=default_branch,
                                storage_context=storage_context, # Reused from earlier in the script
                                service_context=service_context, # Reused from earlier in the script
                                filter_extensions=None # Or a default list if preferred
                            )
                            # Ensure index_result is a dict and has the key before accessing
                            nodes_indexed_count = 0
                            if isinstance(index_result, dict) and 'total_nodes_indexed' in index_result:
                                nodes_indexed_count = index_result.get('total_nodes_indexed', 0)
                            logger.info(f"Successfully indexed {nodes_indexed_count} nodes from GitHub repository {repo_full_name}.")
                        except Exception as e:
                            logger.error(f"Error indexing GitHub repository {repo_full_name}: {e}", exc_info=True)
                    logger.info("Finished processing all configured GitHub repositories.")
                except Exception as e:
                    logger.error(f"Error initializing GitHubIndexer or during repo processing setup: {e}", exc_info=True)
        
        # Check if any indexing was attempted
        confluence_skipped = not (settings.confluence.confluence_enabled and settings.confluence.confluence_space_keys)
        github_skipped = not (settings.github.github_enabled and settings.github.github_token and settings.github.github_repos)

        if confluence_skipped and github_skipped:
            logger.info("No data sources were configured or enabled for indexing. Exiting.")
        else:
            logger.info("Finished processing all configured data sources.")


    except Exception as e:
        logger.error(f"An unexpected error occurred during vector database initialization: {e}", exc_info=True)
        sys.exit(1)

    logger.info("Vector database initialization script process completed.")
