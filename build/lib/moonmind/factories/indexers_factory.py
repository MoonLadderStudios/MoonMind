import logging # Add if logging for missing Jira settings is desired
from moonmind.config.settings import AppSettings
from moonmind.indexers.confluence_indexer import ConfluenceIndexer
from moonmind.indexers.jira_indexer import JiraIndexer # New import

# logger = logging.getLogger(__name__) # Optional: if logging warnings

def build_indexers(settings: AppSettings):
    indexers = {}

    if settings.confluence_enabled:
        # Ensure required Confluence settings are present
        if settings.confluence_url and settings.confluence_username and settings.confluence_api_key:
            indexers["confluence"] = ConfluenceIndexer(
                base_url=settings.confluence_url,
                user_name=settings.confluence_username,
                api_token=settings.confluence_api_key
                # cloud=True is the default in ConfluenceIndexer
            )
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