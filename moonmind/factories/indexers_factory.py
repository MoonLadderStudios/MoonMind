import logging # Add if logging for missing Jira settings is desired
from moonmind.config.settings import AppSettings
from moonmind.indexers.confluence_indexer import ConfluenceIndexer
from moonmind.indexers.jira_indexer import JiraIndexer # New import

# logger = logging.getLogger(__name__) # Optional: if logging warnings

def build_indexers(settings: AppSettings):
    indexers = {}

    # Confluence Indexer
    if settings.atlassian.atlassian_enabled and settings.atlassian.confluence.confluence_enabled:
        if settings.atlassian.atlassian_url and \
           settings.atlassian.atlassian_username and \
           settings.atlassian.atlassian_api_key:
            indexers["confluence"] = ConfluenceIndexer(
                base_url=settings.atlassian.atlassian_url,
                user_name=settings.atlassian.atlassian_username,
                api_token=settings.atlassian.atlassian_api_key
                # cloud=True is the default in ConfluenceIndexer
            )
        else:
            # logger.warning("Confluence is enabled but missing required Atlassian settings (URL, Username, API Key).")
            pass # ConfluenceIndexer raises ValueError for missing essential params

    # Jira Indexer
    if settings.atlassian.atlassian_enabled and settings.atlassian.jira.jira_enabled:
        if settings.atlassian.atlassian_url and \
           settings.atlassian.atlassian_username and \
           settings.atlassian.atlassian_api_key:
            indexers["jira"] = JiraIndexer(
                jira_url=settings.atlassian.atlassian_url,
                username=settings.atlassian.atlassian_username,
                api_token=settings.atlassian.atlassian_api_key
            )
        else:
            # logger.warning("Jira is enabled but missing required Atlassian settings (URL, Username, API Token).")
            pass # JiraIndexer raises ValueError for missing essential params

    return indexers