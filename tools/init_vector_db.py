import logging
import os
import sys

import qdrant_client
from google.genai.types import EmbedContentConfig
from llama_index.core import Settings, StorageContext
from llama_index.embeddings.google_genai import GoogleGenAIEmbedding
from llama_index.vector_stores.qdrant import QdrantVectorStore

from moonmind.config.settings import settings
from moonmind.factories.embed_model_factory import build_embed_model
from moonmind.indexers.confluence_indexer import ConfluenceIndexer
from moonmind.indexers.github_indexer import GitHubIndexer
from moonmind.indexers.google_drive_indexer import GoogleDriveIndexer
from moonmind.indexers.jira_indexer import JiraIndexer
from moonmind.indexers.local_data_indexer import LocalDataIndexer
from moonmind.utils.env_bool import env_to_bool

# Set up basic logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

if __name__ == "__main__":
    logger.info("Starting vector database initialization script.")

    init_db = env_to_bool(os.getenv("INIT_DATABASE"), default=False)
    if not init_db:
        logger.info("INIT_DATABASE environment variable is not set to 'true', exiting.")
        sys.exit(1) # Keep exit here as basic prerequisite

    logger.info("INIT_DATABASE is 'true', proceeding with database initialization.")
    any_actual_indexing_performed_or_attempted = False # Initialize tracking flag

    try:
        # Moved Critical Initializations Upfront
        logger.info("Building embedding model...")
        try:
            embed_model, embed_dimensions = build_embed_model(settings)
            logger.info(f"Embedding model built successfully. Dimensions: {embed_dimensions}")
        except Exception as e:
            logger.error(f"CRITICAL: Failed to build embedding model: {e}", exc_info=True)
            sys.exit(1)

        logger.info(f"Initializing Qdrant client for host: {settings.qdrant.qdrant_host}, port: {settings.qdrant.qdrant_port}")
        try:
            client = qdrant_client.QdrantClient(host=settings.qdrant.qdrant_host, port=settings.qdrant.qdrant_port)
            logger.info(f"Using vector store collection name: {settings.vector_store_collection_name}")
            logger.info(f"Using dynamic embeddings dimensions for Qdrant: {embed_dimensions}")

            # Ensure the collection exists or create it with the correct vector size/distance.
            from qdrant_client.http.exceptions import UnexpectedResponse
            from qdrant_client.http.models import Distance, VectorParams

            try:
                client.get_collection(settings.vector_store_collection_name)
                logger.info(
                    f"Qdrant collection '{settings.vector_store_collection_name}' already exists."
                )
            except UnexpectedResponse as e:
                if "404" in str(e):
                    logger.info(
                        f"Qdrant collection '{settings.vector_store_collection_name}' not found. Creating it..."
                    )
                    client.create_collection(
                        collection_name=settings.vector_store_collection_name,
                        vectors_config=VectorParams(
                            size=embed_dimensions,
                            distance=Distance.COSINE,
                        ),
                    )
                    any_actual_indexing_performed_or_attempted = True  # Collection creation counts as an action
                    logger.info(
                        f"Collection '{settings.vector_store_collection_name}' created successfully."
                    )
                else:
                    raise

            vector_store = QdrantVectorStore(
                client=client,
                collection_name=settings.vector_store_collection_name,
                embed_dim=embed_dimensions
            )
            logger.info("Qdrant client and vector store initialized successfully.")
        except Exception as e:
            logger.error(f"CRITICAL: Failed to initialize Qdrant client or VectorStore: {e}", exc_info=True)
            sys.exit(1)

        try:
            storage_context = StorageContext.from_defaults(vector_store=vector_store)
            logger.info("StorageContext initialized successfully.")
        except Exception as e:
            logger.error(f"CRITICAL: Failed to initialize StorageContext: {e}", exc_info=True)
            sys.exit(1)

        try:
            # LLM is set to None as we are only performing indexing operations.
            Settings.embed_model = embed_model
            Settings.llm = None
        except Exception as e:
            logger.error(f"CRITICAL: Failed to initialize Settings: {e}", exc_info=True)
            sys.exit(1)

        # 1. Confluence Processing Logic
        logger.info("--- Starting Confluence Processing ---")
        process_confluence_data = False
        confluence_skipped = True # Assume skipped until confirmed otherwise

        if not settings.atlassian.confluence.confluence_enabled:
            logger.info("Confluence indexing is disabled via settings (ATLASSIAN_CONFLUENCE_ENABLED=false). Skipping.")
        else:
            logger.info("Confluence is enabled. Checking required configurations...")
            missing_confluence_settings = []
            if not settings.atlassian.atlassian_url:
                missing_confluence_settings.append("ATLASSIAN_URL")
            if not settings.atlassian.atlassian_username:
                missing_confluence_settings.append("ATLASSIAN_USERNAME")
            if not settings.atlassian.confluence.confluence_space_keys:
                missing_confluence_settings.append("ATLASSIAN_CONFLUENCE_SPACE_KEYS")

            if missing_confluence_settings:
                logger.error(f"Confluence indexing will be skipped due to missing critical settings: {', '.join(missing_confluence_settings)}.")
            else:
                logger.info("All required Confluence configurations are present.")
                process_confluence_data = True
                confluence_skipped = False # Tentatively, as space keys still need parsing

        if process_confluence_data:
            any_actual_indexing_performed_or_attempted = True # Mark that we are attempting Confluence
            space_keys_str = settings.atlassian.confluence.confluence_space_keys
            space_keys = [key.strip() for key in space_keys_str.split(',') if key.strip()]

            if not space_keys:
                logger.error("No valid Confluence space keys found after parsing ATLASSIAN_CONFLUENCE_SPACE_KEYS. Skipping Confluence indexing.")
                confluence_skipped = True # Actual skipping reason
            else:
                logger.info(f"Found Confluence space keys to process: {space_keys}")
                logger.info("Initializing ConfluenceIndexer...")
                try:
                    confluence_indexer = ConfluenceIndexer(
                        base_url=settings.atlassian.atlassian_url,
                        user_name=settings.atlassian.atlassian_username,
                        api_token=settings.atlassian.atlassian_api_key, # Changed to use atlassian_api_key
                        logger=logger
                    )
                    logger.info("ConfluenceIndexer initialized successfully.")

                    logger.info(f"Starting to process {len(space_keys)} Confluence space(s).")
                    for space_key in space_keys:
                        logger.info(f"Processing Confluence space: {space_key}")
                        try:
                            index_result = confluence_indexer.index(
                                space_key=space_key,
                                storage_context=storage_context,
                                service_context=Settings
                            )
                            total_nodes_indexed = 0
                            if index_result and isinstance(index_result, dict) and 'total_nodes_indexed' in index_result:
                                total_nodes_indexed = index_result.get('total_nodes_indexed', 0)
                            logger.info(f"Successfully processed space {space_key}. Nodes indexed: {total_nodes_indexed}.")
                        except Exception as e:
                            logger.error(f"Error indexing space {space_key}: {e}", exc_info=True)
                            # Do not mark confluence_skipped = True here, as an attempt was made for at least one space.
                            # The summary can report partial success/failure if needed later.
                    logger.info("Finished processing all configured Confluence spaces.")
                except Exception as e:
                    logger.error(f"Failed to initialize ConfluenceIndexer or during space processing setup: {e}", exc_info=True)
                    confluence_skipped = True # Consider it skipped if the indexer itself fails to init

        logger.info("--- Finished Confluence Processing ---")

        # 9. GitHub Repository Indexing
        logger.info("--- Starting GitHub Processing ---")
        github_skipped = True # Assume skipped
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
                any_actual_indexing_performed_or_attempted = True # Mark that we are attempting GitHub
                github_skipped = False # Tentatively false
                logger.info(f"Found GitHub repositories to index: {github_repo_list}")
                logger.info("Initializing GitHubIndexer...")
                try:
                    github_indexer = GitHubIndexer(github_token=settings.github.github_token, logger=logger)
                    logger.info("GitHubIndexer initialized.")

                    for repo_full_name in github_repo_list:
                        logger.info(f"Processing GitHub repository: {repo_full_name} (using default branch)")
                        try:
                            index_result = github_indexer.index(
                                repo_full_name=repo_full_name,
                                storage_context=storage_context,
                                service_context=Settings,
                                filter_extensions=None, # Explicitly None
                                branch=None # Let the indexer determine the default branch
                            )
                            nodes_indexed_count = 0
                            if isinstance(index_result, dict) and 'total_nodes_indexed' in index_result:
                                nodes_indexed_count = index_result.get('total_nodes_indexed', 0)
                            logger.info(f"Successfully indexed {nodes_indexed_count} nodes from GitHub repository {repo_full_name}.")
                        except Exception as e:
                            logger.error(f"Error indexing GitHub repository {repo_full_name}: {e}", exc_info=True)
                            # github_skipped remains False as an attempt was made for at least one repo
                    logger.info("Finished processing all configured GitHub repositories.")
                except Exception as e:
                    logger.error(f"Error initializing GitHubIndexer or during repo processing setup: {e}", exc_info=True)
                    github_skipped = True # Indexer failed to init or other setup error
        logger.info("--- Finished GitHub Processing ---")

        # 10. Google Drive Indexing
        logger.info("--- Starting Google Drive Processing ---")
        google_drive_skipped = True # Assume skipped
        if not settings.google_drive.google_drive_enabled:
            logger.info("Google Drive integration is not enabled via settings. Skipping Google Drive indexing.")
        elif not settings.google_drive.google_application_credentials:
            logger.error("GOOGLE_APPLICATION_CREDENTIALS is not configured. Skipping Google Drive indexing.")
        elif not settings.google_drive.google_drive_folder_id:
            logger.error("GOOGLE_DRIVE_FOLDER_ID is not configured. Skipping Google Drive indexing.")
        else:
            folder_ids_str = settings.google_drive.google_drive_folder_id
            folder_ids = [fid.strip() for fid in folder_ids_str.split(',') if fid.strip()]
            if not folder_ids:
                logger.error("No valid Google Drive folder IDs found after parsing GOOGLE_DRIVE_FOLDER_ID. Skipping Google Drive indexing.")
            else:
                any_actual_indexing_performed_or_attempted = True # Mark that we are attempting Google Drive
                google_drive_skipped = False # Tentatively false
                logger.info(f"Found Google Drive folder IDs to index: {folder_ids}")
                logger.info("Initializing GoogleDriveIndexer...")
                try:
                    google_drive_indexer = GoogleDriveIndexer(
                        service_account_key_path=settings.google_drive.google_application_credentials,
                        logger=logger
                    )
                    logger.info("GoogleDriveIndexer initialized.")

                    for folder_id in folder_ids:
                        logger.info(f"Processing Google Drive folder: {folder_id}")
                        try:
                            index_result = google_drive_indexer.index(
                                folder_id=folder_id,
                                storage_context=storage_context
                            )
                            nodes_indexed_count = 0
                            if isinstance(index_result, dict) and 'total_nodes_indexed' in index_result:
                                nodes_indexed_count = index_result.get('total_nodes_indexed', 0)
                            logger.info(f"Successfully indexed {nodes_indexed_count} nodes from Google Drive folder {folder_id}.")
                        except Exception as e:
                            logger.error(f"Error indexing Google Drive folder {folder_id}: {e}", exc_info=True)
                            # google_drive_skipped remains False as an attempt was made
                    logger.info("Finished processing all configured Google Drive folders.")
                except Exception as e:
                    logger.error(f"Error initializing GoogleDriveIndexer or during folder processing setup: {e}", exc_info=True)
                    google_drive_skipped = True # Indexer failed to init
        logger.info("--- Finished Google Drive Processing ---")

        # 11. Jira Indexing
        logger.info("--- Starting Jira Processing ---")
        jira_skipped = True # Assume skipped
        if not settings.atlassian.jira.jira_enabled:
            logger.info("Jira integration is not enabled via settings. Skipping Jira indexing.")
        elif not settings.atlassian.atlassian_url:
            logger.warning("ATLASSIAN_URL (for Jira) is not configured. Skipping Jira indexing.")
        elif not settings.atlassian.atlassian_username:
            logger.warning("ATLASSIAN_USERNAME (for Jira) is not configured. Skipping Jira indexing.")
        elif not settings.atlassian.atlassian_api_key:
            logger.warning("ATLASSIAN_API_KEY (for Jira) is not configured. Skipping Jira indexing.")
        elif not settings.atlassian.jira.jira_jql_query:
            logger.warning("ATLASSIAN_JIRA_JQL_QUERY is not configured. Skipping Jira indexing.")
        else:
            any_actual_indexing_performed_or_attempted = True # Mark that we are attempting Jira
            jira_skipped = False # Tentatively false
            logger.info(f"Found Jira JQL query to process: {settings.atlassian.jira.jira_jql_query}")
            logger.info("Initializing JiraIndexer...")
            try:
                jira_indexer = JiraIndexer(
                    jira_url=settings.atlassian.atlassian_url,
                    username=settings.atlassian.atlassian_username,
                    api_token=settings.atlassian.atlassian_api_key,
                    logger=logger
                )
                logger.info("JiraIndexer initialized.")

                logger.info(f"Processing Jira query: {settings.atlassian.jira.jira_jql_query}")
                index_result = jira_indexer.index(
                    jql_query=settings.atlassian.jira.jira_jql_query,
                    storage_context=storage_context,
                    service_context=Settings,  # Pass the global Settings object
                    jira_fetch_batch_size=settings.atlassian.jira.jira_fetch_batch_size
                )
                nodes_indexed_count = 0
                if isinstance(index_result, dict) and 'total_nodes_indexed' in index_result:
                    nodes_indexed_count = index_result.get('total_nodes_indexed', 0)
                logger.info(f"Successfully indexed {nodes_indexed_count} nodes from Jira for query: {settings.atlassian.jira.jira_jql_query}.")
            except Exception as e:
                logger.error(f"Error indexing Jira query {settings.atlassian.jira.jira_jql_query}: {e}", exc_info=True)
                jira_skipped = True # Mark as skipped due to error during indexing or init
        logger.info("--- Finished Jira Processing ---")

        # 12. Local Data Indexing
        logger.info("--- Starting Local Data Processing ---")
        local_data_skipped = True # Assume skipped
        # Reminder: The LocalData path from .env (e.g., settings.local_data.local_data_path)
        # is expected to be mounted to /app/local_data in the Docker container running this script.
        local_data_mount_path = "/app/local_data"

        if not settings.local_data.local_data_path:
            logger.info("LocalData path is not configured in settings (via LocalData env var). Skipping Local Data indexing.")
        elif not os.path.exists(local_data_mount_path) or not os.listdir(local_data_mount_path):
             logger.warning(
                f"Local data directory '{local_data_mount_path}' is empty or does not exist inside the container. "
                f"Ensure the volume is correctly mounted from '{settings.local_data.local_data_path}'. Skipping Local Data indexing."
            )
        else:
            any_actual_indexing_performed_or_attempted = True # Mark that we are attempting Local Data
            local_data_skipped = False # Tentatively false
            logger.info(f"Local data path configured to: {settings.local_data.local_data_path}. Indexing from container path: {local_data_mount_path}")
            logger.info("Initializing LocalDataIndexer...")
            try:
                local_data_indexer = LocalDataIndexer(
                    data_dir=local_data_mount_path, # Fixed path inside container
                    logger=logger
                )
                logger.info("LocalDataIndexer initialized.")

                logger.info(f"Processing local data directory: {local_data_mount_path}")
                try:
                    index_result = local_data_indexer.index(
                        storage_context=storage_context,
                        service_context=Settings
                    )
                    nodes_indexed_count = 0
                    if isinstance(index_result, dict) and 'total_nodes_indexed' in index_result:
                        nodes_indexed_count = index_result.get('total_nodes_indexed', 0)
                    logger.info(f"Successfully indexed {nodes_indexed_count} nodes from local data directory {local_data_mount_path}.")
                except Exception as e:
                    logger.error(f"Error indexing local data directory {local_data_mount_path}: {e}", exc_info=True)
                    local_data_skipped = True # Mark as skipped due to error
                logger.info("Finished processing local data directory.")
            except Exception as e:
                logger.error(f"Error initializing LocalDataIndexer: {e}", exc_info=True)
                local_data_skipped = True # Indexer failed to init
        logger.info("--- Finished Local Data Processing ---")

        # Final Summary
        logger.info("--- Final Summary ---")
        if not any_actual_indexing_performed_or_attempted:
            if confluence_skipped and github_skipped and google_drive_skipped and jira_skipped and local_data_skipped:
                 logger.info("No data sources were enabled or configured correctly for indexing. Exiting.")
            # If any_actual_indexing_performed_or_attempted is False, but some sources were not technically "skipped"
            # (e.g. enabled but failed pre-check not covered by specific skip flags), this means an earlier critical error.
            # The script might have exited already if critical init failed.
            # This log primarily covers the case where all sources are disabled/misconfigured from the start.

        log_messages = []
        if not confluence_skipped:
            log_messages.append("Confluence")
        if not github_skipped:
            log_messages.append("GitHub")
        if not google_drive_skipped:
            log_messages.append("Google Drive")
        if not jira_skipped:
            log_messages.append("Jira")
        if not local_data_skipped:
            log_messages.append("Local Data")

        processed_sources_summary = ", ".join(log_messages) if log_messages else "None"
        logger.info(f"Data sources for which processing was attempted: {processed_sources_summary}.")

        skipped_sources_summary = []
        if confluence_skipped:
            skipped_sources_summary.append("Confluence")
        if github_skipped:
            skipped_sources_summary.append("GitHub")
        if google_drive_skipped:
            skipped_sources_summary.append("Google Drive")
        if jira_skipped:
            skipped_sources_summary.append("Jira")
        if local_data_skipped:
            skipped_sources_summary.append("Local Data")

        if skipped_sources_summary:
            logger.info(f"Data sources that were skipped: {', '.join(skipped_sources_summary)} (due to being disabled, misconfigured, or errors during initialization/processing).")
        else:
            if any_actual_indexing_performed_or_attempted:
                 logger.info("All configured and enabled data sources were processed (or attempted).")
            # If no indexing was attempted and no sources were skipped, it implies an issue or no sources configured.

    except Exception as e:
        logger.error(f"CRITICAL: An unexpected error occurred during vector database initialization: {e}", exc_info=True)
        sys.exit(1) # Catch-all for truly unexpected issues

    # Determine final exit code
    if not any_actual_indexing_performed_or_attempted:
        logger.warning("No data sources were successfully configured and attempted for indexing. The script will exit with an error code.")
        logger.info("Vector database initialization script process completed with no actions taken.")
        sys.exit(1) # Exit with 1 if no indexing was performed or attempted
    else:
        logger.info("Vector database initialization script process completed.")
        sys.exit(0) # Explicitly exit with 0 if indexing was performed or attempted
