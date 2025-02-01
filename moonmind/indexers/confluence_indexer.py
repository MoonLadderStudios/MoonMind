import logging
from typing import Generator, List

from llama_index.core.schema import TextNode
from llama_index.readers.confluence import ConfluenceReader
from llama_index.vector_stores.qdrant import QdrantVectorStore

from .base_connector import BaseConnector, BaseDocument


class ConfluenceIndexer:
    def __init__(
        self,
        base_url: str,
        api_token: str,
        user_name: str,
        cloud: bool = True,
        logger: logging.Logger = None
    ):
        self.logger = logger or logging.getLogger(__name__)
        if not base_url:
            raise ValueError("Confluence URL is required to set up Confluence")
        if not api_token:
            raise ValueError("Confluence API key is required to set up Confluence")
        if not user_name:
            raise ValueError("Confluence username is required to set up Confluence")

        self.base_url = base_url
        self.api_token = api_token
        self.user_name = user_name
        self.cloud = cloud
        self.reader = None

        self.reader = ConfluenceReader(
            base_url=self.base_url,
            user_name=self.user_name,
            password=self.api_token,
            cloud=self.cloud
        )


    def stream_space(self, space_key: str) -> Generator[BaseDocument, None, None]:
        """Stream documents from a Confluence space."""

        try:
            self.logger.info(f"Streaming documents from space: {space_key}")
            document_count = 0
            documents = self.reader.load_data(space_key=space_key)

            for doc in documents:
                document_count += 1

                # Convert Confluence page ID to integer
                try:
                    doc_id = int(doc.id_)
                except (ValueError, TypeError):
                    self.logger.error(f"Invalid document ID format from Confluence: {doc.id_}")
                    raise ValueError(f"Document ID must be a valid integer. Got: {doc.id_}")

                yield BaseDocument(
                    text=doc.text,
                    metadata={
                        **doc.metadata,
                        "space_key": space_key,
                        "source": "confluence",
                        "url": doc.metadata.get("url"),
                        "title": doc.metadata.get("title"),
                    },
                    id=doc_id  # Now passing as integer
                )

            self.logger.info(f"Completed streaming {document_count} total documents from {space_key}")

        except Exception as e:
            self.logger.error(f"Error streaming documents: {str(e)}")
            raise

    def index_space(self, vector_store, embedder, space_key: str):
        """Index a Confluence space."""
        try:
            # Stream each document from the Confluence space
            for doc in self.stream_space(space_key):
                # Convert the streamed BaseDocument to a LlamaIndex TextNode
                node = TextNode(
                    text=doc.text,
                    id_=str(doc.id),
                    metadata=doc.metadata
                )

                embedding = embedder.get_text_embedding(doc.text)

                vector_store.add(
                    nodes=[node],
                    embeddings=[embedding]
                )

                self.logger.info(f"Indexed document ID {doc.id} with embeddings.")

        except Exception as e:
            self.logger.error(f"Error while indexing Confluence space {space_key}: {str(e)}")
            raise

