import logging

from langchain.vectorstores import VectorStore
from langchain_community.document_loaders import ConfluenceLoader


class ConfluenceIndexer:
    def __init__(
        self,
        url: str,
        api_key: str,
        username: str,
        space_key: str,
        include_attachments: bool = False,
        limit: int = 50,
        logger: logging.Logger = None
    ):
        # Use the provided logger or fall back to a module-specific logger
        self.logger = logger or logging.getLogger(__name__)
        self.set_loader(url, api_key, username, space_key, include_attachments, limit)

    def set_loader(
        self,
        url: str,
        api_key: str,
        username: str,
        space_key: str,
        include_attachments: bool = False,
        limit: int = 50
    ):
        if not url:
            raise ValueError("Confluence URL is required to set up confluence")
        if not api_key:
            raise ValueError("Confluence API key is required to set up confluence")
        if not username:
            raise ValueError("Confluence username is required to set up confluence")
        if not space_key:
            raise ValueError("Confluence space key is required to set up confluence")

        self.loader = ConfluenceLoader(
            url=url,
            api_key=api_key,
            username=username,
            space_key=space_key,
            include_attachments=include_attachments,
            limit=limit
        )
        self.url = url
        self.api_key = api_key
        self.username = username
        self.space_key = space_key
        self.include_attachments = include_attachments
        self.limit = limit

    def index_space(self, vector_store: VectorStore, space_key: str = None):
        # If a new space key is provided, update the loader
        if space_key is not None and space_key != self.space_key:
            self.set_loader(self.url, self.api_key, self.username, space_key, self.include_attachments, self.limit)
        try:
            documents = self.loader.load()
            ids = vector_store.add_documents(documents)
            self.logger.info(f"Loaded {len(documents)} documents from Confluence space {space_key or self.space_key}")
            return ids
        except Exception as e:
            self.logger.exception(f"Error indexing space {space_key or self.space_key}: {e}")
            raise
