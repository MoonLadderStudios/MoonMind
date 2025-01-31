from langchain.document_loaders import ConfluenceLoader
from langchain_qdrant import QdrantVectorStore

from ..config.settings import AppSettings

# TODO: pass in logger

class ConfluenceIndexer:
    def __init__(self, settings: AppSettings):
        if not settings.confluence.confluence_url:
            raise ValueError("Confluence URL is required to enable confluence")
        if not settings.confluence.confluence_api_key:
            raise ValueError("Confluence API key is required to enable confluence")
        if not settings.confluence.confluence_username:
            raise ValueError("Confluence username is required to enable confluence")

        self.confluence_loader = ConfluenceLoader(
            url=settings.confluence.confluence_url,
            api_key=settings.confluence.confluence_api_key,
            username=settings.confluence.confluence_username
        )

    def index_space(self, vector_store: QdrantVectorStore, space_key: str, include_attachments: bool = True, limit: int = 50):
        try:
            # TODO: use logger and use pagination
            documents = self.confluence_loader.load(
                space_key=space_key,
                include_attachments=include_attachments,
                limit=limit
            )
            ids = vector_store.add_documents(documents)
            # logger.info(f"Loaded {len(documents)} documents from Confluence space {space_key}")
            return ids
        except Exception as e:
            # logger.error(f"Error indexing space {space_key}: {e}")
            raise e
