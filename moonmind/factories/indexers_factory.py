import logging  # Add if logging for missing Jira settings is desired

from moonmind.config.settings import AppSettings
from moonmind.indexers.confluence_indexer import ConfluenceIndexer
from moonmind.indexers.jira_indexer import JiraIndexer  # New import

# logger = logging.getLogger(__name__) # Optional: if logging warnings

def build_indexers(settings: AppSettings):
    indexers = {}

    if settings.confluence_enabled:
        # Ensure required Confluence settings are present
        if settings.atlassian.atlassian_url and settings.atlassian.atlassian_username and settings.atlassian.atlassian_api_key: # Check for atlassian_api_key
            try:
                logger.info("Attempting to create Confluence Indexer")
                confluence_indexer = ConfluenceIndexer(
                    base_url=settings.atlassian.atlassian_url,
                    user_name=settings.atlassian.atlassian_username,
                    api_token=settings.atlassian.atlassian_api_key # Use atlassian_api_key
                    # cloud=True is the default in ConfluenceIndexer
                )
                indexers["confluence"] = confluence_indexer
            except Exception as e:
                logger.error(f"Failed to create Confluence Indexer: {e}")
        else:
            # logger.warning("Confluence is enabled but missing required settings (URL, Username, API Key).")
            pass # ConfluenceIndexer raises ValueError for missing essential params

    if settings.jira_enabled:
        # Ensure required Jira settings are present
        if settings.jira_url and settings.jira_username and settings.jira_api_token:
            indexers["jira"] = JiraIndexer(
                jira_url=settings.jira_url,
                username=settings.jira_username,
                api_token=settings.jira_api_token
            )
        else:
            # logger.warning("Jira is enabled but missing required settings (URL, Username, API Token).")
            pass # JiraIndexer raises ValueError for missing essential params

    return indexers