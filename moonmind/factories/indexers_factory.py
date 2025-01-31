from moonmind.config.settings import AppSettings
from moonmind.indexers.confluence_indexer import ConfluenceIndexer


def build_indexers(settings: AppSettings):
    indexers = {}

    if settings.confluence.confluence_enabled:
        indexers["confluence"] = ConfluenceIndexer(settings)

    return indexers