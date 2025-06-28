import logging
import unittest
from unittest.mock import MagicMock, patch

from llama_index.core import ServiceContext, StorageContext
from llama_index.core.node_parser import SimpleNodeParser
from llama_index.core.schema import Document  # For creating mock documents

from fastapi import HTTPException
from moonmind.indexers.google_drive_indexer import GoogleDriveIndexer


class TestGoogleDriveIndexer(unittest.TestCase):
    def setUp(self):
        self.mock_storage_context = MagicMock(spec=StorageContext)
        self.mock_service_context = MagicMock(spec=ServiceContext)
        self.mock_service_context.embed_model = (
            MagicMock()
        )  # Added embed_model attribute

        self.mock_node_parser = MagicMock(spec=SimpleNodeParser)
        self.mock_service_context.node_parser = self.mock_node_parser
        self.mock_logger = MagicMock(spec=logging.Logger)

    @patch("moonmind.indexers.google_drive_indexer.GoogleDriveReader")
    def test_index_success_with_folder_id(self, MockGoogleDriveReader):
        indexer = GoogleDriveIndexer(logger=self.mock_logger)

        mock_reader_instance = MockGoogleDriveReader.return_value
        mock_doc = Document(text="Doc content from folder", doc_id="doc_folder_1")
        mock_reader_instance.load_data.return_value = [mock_doc]

        mock_node = MagicMock()
        self.mock_node_parser.get_nodes_from_documents.return_value = [mock_node]

        # Mock VectorStoreIndex.from_documents for consistent index object
        with patch(
            "moonmind.indexers.google_drive_indexer.VectorStoreIndex.from_documents"
        ) as mock_vs_from_docs:
            mock_index_instance = MagicMock()
            mock_vs_from_docs.return_value = mock_index_instance

            result = indexer.index(
                storage_context=self.mock_storage_context,
                service_context=self.mock_service_context,
                folder_id="test_folder",
            )

            MockGoogleDriveReader.assert_called_once_with(credentials_path=None)
            mock_reader_instance.load_data.assert_called_once_with(
                folder_id="test_folder"
            )
            self.mock_node_parser.get_nodes_from_documents.assert_called_once_with(
                [mock_doc]
            )
            self.mock_storage_context.persist.assert_called_once()
            mock_index_instance.insert_nodes.assert_called_once_with([mock_node])

            self.assertEqual(result["total_nodes_indexed"], 1)
            self.assertEqual(result["index"], mock_index_instance)

    @patch("moonmind.indexers.google_drive_indexer.GoogleDriveReader")
    def test_index_success_with_file_ids(self, MockGoogleDriveReader):
        indexer = GoogleDriveIndexer(
            service_account_key_path="fake/path.json", logger=self.mock_logger
        )

        mock_reader_instance = MockGoogleDriveReader.return_value
        mock_doc1 = Document(text="Doc from file1", doc_id="file1_doc")
        mock_doc2 = Document(text="Doc from file2", doc_id="file2_doc")
        mock_reader_instance.load_data.return_value = [mock_doc1, mock_doc2]

        mock_node1, mock_node2 = MagicMock(), MagicMock()
        self.mock_node_parser.get_nodes_from_documents.return_value = [
            mock_node1,
            mock_node2,
        ]

        with patch(
            "moonmind.indexers.google_drive_indexer.VectorStoreIndex.from_documents"
        ) as mock_vs_from_docs:
            mock_index_instance = MagicMock()
            mock_vs_from_docs.return_value = mock_index_instance

            result = indexer.index(
                storage_context=self.mock_storage_context,
                service_context=self.mock_service_context,
                file_ids=["file1", "file2"],
            )

            MockGoogleDriveReader.assert_called_once_with(
                credentials_path="fake/path.json"
            )
            mock_reader_instance.load_data.assert_called_once_with(
                file_ids=["file1", "file2"]
            )
            self.mock_node_parser.get_nodes_from_documents.assert_called_once_with(
                [mock_doc1, mock_doc2]
            )
            self.mock_storage_context.persist.assert_called_once()
            mock_index_instance.insert_nodes.assert_called_once_with(
                [mock_node1, mock_node2]
            )

            self.assertEqual(result["total_nodes_indexed"], 2)
            self.assertEqual(result["index"], mock_index_instance)

    @patch("moonmind.indexers.google_drive_indexer.GoogleDriveReader")
    def test_index_no_folder_or_file_ids(self, MockGoogleDriveReader):
        # MockGoogleDriveReader is passed due to class decorator, but not used here
        indexer = GoogleDriveIndexer(logger=self.mock_logger)
        with self.assertRaises(ValueError) as context:
            indexer.index(
                storage_context=self.mock_storage_context,
                service_context=self.mock_service_context,
                folder_id=None,
                file_ids=None,
            )
        self.assertIn(
            "Either folder_id or file_ids must be provided", str(context.exception)
        )

    @patch("moonmind.indexers.google_drive_indexer.GoogleDriveReader")
    def test_index_reader_init_fails(self, MockGoogleDriveReader):
        MockGoogleDriveReader.side_effect = Exception("Init error")
        indexer = GoogleDriveIndexer(logger=self.mock_logger)

        with self.assertRaises(HTTPException) as context:
            indexer.index(
                storage_context=self.mock_storage_context,
                service_context=self.mock_service_context,
                folder_id="test_folder",
            )
        self.assertEqual(context.exception.status_code, 500)
        self.assertIn(
            "Failed to initialize GoogleDriveReader: Init error",
            str(context.exception.detail),
        )

    @patch("moonmind.indexers.google_drive_indexer.GoogleDriveReader")
    def test_index_load_data_fails(self, MockGoogleDriveReader):
        mock_reader_instance = MockGoogleDriveReader.return_value
        mock_reader_instance.load_data.side_effect = Exception("Load error")
        indexer = GoogleDriveIndexer(logger=self.mock_logger)

        with self.assertRaises(HTTPException) as context:
            indexer.index(
                storage_context=self.mock_storage_context,
                service_context=self.mock_service_context,
                folder_id="test_folder",
            )
        self.assertEqual(context.exception.status_code, 500)
        self.assertIn(
            "Error loading data from Google Drive: Load error",
            str(context.exception.detail),
        )

    @patch("moonmind.indexers.google_drive_indexer.GoogleDriveReader")
    def test_index_no_documents_found(self, MockGoogleDriveReader):
        indexer = GoogleDriveIndexer(logger=self.mock_logger)

        mock_reader_instance = MockGoogleDriveReader.return_value
        mock_reader_instance.load_data.return_value = []  # No documents found

        self.mock_node_parser.get_nodes_from_documents.return_value = []

        with patch(
            "moonmind.indexers.google_drive_indexer.VectorStoreIndex.from_documents"
        ) as mock_vs_from_docs:
            mock_empty_index = MagicMock()
            mock_vs_from_docs.return_value = mock_empty_index

            result = indexer.index(
                storage_context=self.mock_storage_context,
                service_context=self.mock_service_context,
                folder_id="test_folder",
            )

            mock_vs_from_docs.assert_called_once_with(
                [],
                storage_context=self.mock_storage_context,
                embed_model=self.mock_service_context.embed_model,  # Changed from service_context
            )
            self.mock_node_parser.get_nodes_from_documents.assert_not_called()  # Corrected: should not be called if docs is empty
            mock_empty_index.insert_nodes.assert_not_called()

            self.assertEqual(result["total_nodes_indexed"], 0)
            self.assertEqual(result["index"], mock_empty_index)
            self.mock_storage_context.persist.assert_called_once()


if __name__ == "__main__":
    unittest.main()
