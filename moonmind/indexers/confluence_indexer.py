from langchain.vectorstores import VectorStore
from langchain_community.document_loaders import ConfluenceLoader

from ..config.logging import logger

# TODO: pass in logger

class ConfluenceIndexer:
    def __init__(self, url: str, api_key: str, username: str, space_key: str, include_attachments: bool = False, limit: int = 50):
        self.set_loader(url, api_key, username, space_key, include_attachments, limit)

    def set_loader(self, url: str, api_key: str, username: str, space_key: str, include_attachments: bool = False, limit: int = 50):
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
        # If a space key was provided and it's different from the current space key, we need to update the loader
        if space_key is not None and space_key != self.space_key:
            self.set_loader(self.url, self.api_key, self.username, space_key, self.include_attachments, self.limit)
        try:
            # TODO: use logger and use pagination
            documents = self.loader.load()
            ids = vector_store.add_documents(documents)
            # logger.info(f"Loaded {len(documents)} documents from Confluence space {space_key}")
            return ids
        except Exception as e:
            # logger.error(f"Error indexing space {space_key}: {e}")
            raise e
