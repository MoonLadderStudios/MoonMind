from typing import Generator

from llama_index.readers.confluence import ConfluenceReader

from .base_connector import BaseConnector, BaseDocument


class ConfluenceConnector(BaseConnector):
    def __init__(self, base_url, api_token, user_name, cloud=True, logger=None):
        super().__init__(logger)
        self.base_url = f"{base_url}/wiki" if cloud and not base_url.endswith('/wiki') else base_url
        self.api_token = api_token
        self.user_name = user_name
        self.cloud = cloud
        self.reader = None

    def connect(self):
        self.logger.info(f"Connecting to Confluence at {self.base_url}")
        self.reader = ConfluenceReader(
            base_url=self.base_url,
            user_name=self.user_name,
            password=self.api_token,
            cloud=self.cloud
        )
        self.logger.info("ConfluenceReader initialized")

    def stream_documents(self, space_key: str) -> Generator[BaseDocument, None, None]:
        """Stream documents from a Confluence space."""
        if not self.reader:
            self.connect()

        try:
            self.logger.info(f"Streaming documents from space: {space_key}")
            document_count = 0
            documents = self.reader.load_data(space_key=space_key)

            for doc in documents:
                document_count += 1
                self._log_progress(document_count)

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