import logging
from typing import List, Optional, Dict, Union

from llama_index.core import VectorStoreIndex, Settings, StorageContext
from llama_index.readers.google import GoogleDriveReader
from llama_index.core.node_parser import SimpleNodeParser
from fastapi import HTTPException


class GoogleDriveIndexer:
    def __init__(
        self,
        service_account_key_path: Optional[str] = None,
        logger: Optional[logging.Logger] = None,
    ):
        self.logger = logger or logging.getLogger(__name__)
        self.service_account_key_path = service_account_key_path
        # GoogleDriveReader is initialized in the index method

    def index(
        self,
        storage_context: StorageContext,
        service_context: Settings,
        folder_id: Optional[str] = None,
        file_ids: Optional[List[str]] = None,
        # recursive: bool = False, # LlamaIndex GoogleDriveReader loads all files from a folder_id.
                                 # This parameter is noted from the request model but not directly used here
                                 # as the reader's behavior is to fetch all contents of the specified folder/files.
    ) -> Dict[str, Union[VectorStoreIndex, int]]:

        self.logger.info(f"Starting Google Drive indexing. Folder ID: {folder_id}, File IDs: {file_ids}")

        if not folder_id and not file_ids:
            self.logger.error("Either folder_id or file_ids must be provided for Google Drive loading.")
            raise ValueError("Either folder_id or file_ids must be provided for Google Drive loading.")

        self.logger.info(f"Initializing GoogleDriveReader. Service account path: {self.service_account_key_path}")
        try:
            reader = GoogleDriveReader(credentials_path=self.service_account_key_path)
        except Exception as e:
            self.logger.error(f"Failed to initialize GoogleDriveReader: {e}")
            # This could be due to invalid path, malformed JSON, or other auth issues.
            raise HTTPException(status_code=500, detail=f"Failed to initialize GoogleDriveReader: {str(e)}")

        docs = []
        try:
            if file_ids:
                self.logger.info(f"Loading documents from Google Drive using file_ids: {file_ids}")
                docs = reader.load_data(file_ids=file_ids)
            elif folder_id: # Only use folder_id if file_ids are not provided
                self.logger.info(f"Loading documents from Google Drive folder_id: {folder_id}")
                docs = reader.load_data(folder_id=folder_id)
            # No 'else' needed here as the initial validation ensures one is present.
        except Exception as e:
            self.logger.error(f"Error loading data from Google Drive (folder: {folder_id}, files: {file_ids}): {e}")
            # This can include file not found, access denied, API errors, etc.
            raise HTTPException(status_code=500, detail=f"Error loading data from Google Drive: {str(e)}")

        # Initialize an empty index first. This handles the case of no docs and provides a consistent return.
        index = VectorStoreIndex.from_documents(
            [], # No initial documents
            storage_context=storage_context,
            embed_model=service_context.embed_model
        )
        total_nodes_indexed = 0

        if not docs:
            self.logger.info("No documents found in Google Drive matching criteria.")
            storage_context.persist() # Persist to save the (empty) index state
            self.logger.info("Google Drive indexing complete (no documents found) and storage context persisted.")
            return {"index": index, "total_nodes_indexed": total_nodes_indexed}

        self.logger.info(f"Loaded {len(docs)} documents from Google Drive. Converting to nodes.")
        try:
            node_parser = service_context.node_parser
        except AttributeError:
            self.logger.info("No node_parser found in service_context, using SimpleNodeParser.from_defaults().")
            node_parser = SimpleNodeParser.from_defaults()

        nodes = node_parser.get_nodes_from_documents(docs)
        total_nodes_indexed = len(nodes)
        self.logger.info(f"Converted to {total_nodes_indexed} nodes.")

        if nodes:
            index.insert_nodes(nodes) # Insert the new nodes into the index
            self.logger.info(f"Successfully indexed {total_nodes_indexed} nodes from Google Drive.")
        else:
            self.logger.info(f"No nodes were generated from the loaded documents from Google Drive.")

        storage_context.persist()
        self.logger.info("Google Drive indexing complete and storage context persisted.")

        return {"index": index, "total_nodes_indexed": total_nodes_indexed}
