import logging
import os

from langchain_googledrive.document_loaders import GoogleDriveLoader
from llama_index.vector_stores.qdrant import QdrantVectorStore


class GoogleDriveIndexer:
    def __init__(
        self,
        google_account_file: str,
        folder_id: str,
        recursive: bool = False,
        num_results: int = 50,
        logger: logging.Logger = None,
    ):
        self.logger = logger or logging.getLogger(__name__)
        self.set_loader(google_account_file, folder_id, recursive, num_results)

    def set_loader(
        self,
        google_account_file: str,
        folder_id: str,
        recursive: bool,
        num_results: int,
    ):
        if not google_account_file:
            raise ValueError("Google account file is required to set up Google Drive")
        if not folder_id:
            raise ValueError("Folder ID is required to set up Google Drive")

        self.loader = GoogleDriveLoader(
            folder_id=folder_id,
            recursive=recursive,
            num_results=num_results,
            gdrive_api_file=google_account_file
        )
        self.google_account_file = google_account_file
        self.folder_id = folder_id
        self.recursive = recursive
        self.num_results = num_results

    def index(self, vector_store: QdrantVectorStore, folder_id: str = None):
        if folder_id is not None and self.folder_id != folder_id:
            self.set_loader(self.google_account_file, folder_id, self.recursive, self.num_results)

        try:
            documents = self.loader.load()
            ids = vector_store.insert(documents)
            self.logger.info(
                f"Loaded {len(documents)} documents from Google Drive folder {self.folder_id}"
            )
            return ids
        except Exception as e:
            self.logger.exception(f"Error indexing folder {self.folder_id}: {e}")
            raise
