import unittest
from unittest.mock import MagicMock, patch

from llama_index.core import ServiceContext, StorageContext
from llama_index.core.schema import Document  # For creating mock documents
from llama_index.readers.confluence import (  # For type hinting if needed, but will be mocked
    ConfluenceReader,
)

from moonmind.indexers.confluence_indexer import ConfluenceIndexer


class TestConfluenceIndexer(unittest.TestCase):
    def setUp(self):
        self.mock_node_parser_instance = MagicMock()
        self.mock_service_context = MagicMock(spec=ServiceContext)
        self.mock_service_context.node_parser = self.mock_node_parser_instance
        self.mock_service_context.embed_model = (
            MagicMock()
        )  # Added embed_model attribute
        self.mock_storage_context = MagicMock(spec=StorageContext)
        self.indexer = ConfluenceIndexer(
            base_url="http://fake-confluence.com",
            api_token="fake_token",
            user_name="fake_user",
            logger=MagicMock(),
        )
        self.indexer.reader = MagicMock(spec=ConfluenceReader)

    def test_initialization(self):
        self.assertIsNotNone(self.indexer)
        self.assertEqual(self.indexer.base_url, "http://fake-confluence.com")
        self.assertIsInstance(self.indexer.reader, MagicMock)

    @patch("llama_index.core.VectorStoreIndex.from_documents")
    @patch("llama_index.core.node_parser.SimpleNodeParser.from_defaults")
    def test_index_by_space_key(
        self, mock_simple_node_parser_from_defaults, mock_from_documents
    ):
        mock_simple_node_parser_from_defaults.return_value = (
            self.mock_node_parser_instance
        )
        mock_index_instance = mock_from_documents.return_value
        mock_index_instance.insert_nodes = MagicMock()
        mock_doc_batch1 = [
            Document(text="Doc 1 batch 1"),
            Document(text="Doc 2 batch 1"),
        ]
        mock_doc_batch2 = [Document(text="Doc 1 batch 2")]
        self.indexer.reader.load_data.side_effect = [
            mock_doc_batch1,
            mock_doc_batch2,
            [],
        ]
        mock_nodes_batch1 = [
            MagicMock(name="node1_batch1"),
            MagicMock(name="node2_batch1"),
        ]
        mock_nodes_batch2 = [MagicMock(name="node1_batch2")]
        self.mock_service_context.node_parser.get_nodes_from_documents.side_effect = [
            mock_nodes_batch1,
            mock_nodes_batch2,
        ]
        result = self.indexer.index(
            space_key="TEST_SPACE",
            storage_context=self.mock_storage_context,
            service_context=self.mock_service_context,
            max_pages_to_fetch=2,  # Changed from confluence_fetch_batch_size
        )
        self.assertEqual(
            result["total_nodes_indexed"], 2
        )  # Will fetch one batch of 2, then stops due to max_pages_to_fetch
        self.assertEqual(result["index"], mock_index_instance)
        # The loop behavior with max_pages_to_fetch:
        # It requests num_to_request_this_batch = min(api_batch_size=50, remaining_to_fetch=2) = 2
        # Fetches 2 (mock_doc_batch1). len(docs) is now 2.
        # remaining_to_fetch = 2 - 2 = 0. Loop breaks.
        self.assertEqual(self.indexer.reader.load_data.call_count, 1)
        self.indexer.reader.load_data.assert_any_call(
            space_key="TEST_SPACE", start=0, max_num_results=2
        )
        # self.indexer.reader.load_data.assert_any_call(space_key="TEST_SPACE", start=2, max_num_results=2) # This won't be called
        self.assertEqual(
            self.mock_service_context.node_parser.get_nodes_from_documents.call_count, 1
        )  # Called once for the single batch
        self.mock_service_context.node_parser.get_nodes_from_documents.assert_called_with(
            mock_doc_batch1
        )  # Should be called with mock_doc_batch1
        # self.mock_service_context.node_parser.get_nodes_from_documents.assert_any_call(mock_doc_batch2) # This won't be called
        self.assertEqual(mock_index_instance.insert_nodes.call_count, 1)  # Called once
        mock_index_instance.insert_nodes.assert_called_with(
            mock_nodes_batch1
        )  # Should be called with mock_nodes_batch1
        # mock_index_instance.insert_nodes.assert_any_call(mock_nodes_batch2) # This won't be called
        self.mock_storage_context.persist.assert_called_once()

    @patch("llama_index.core.VectorStoreIndex.from_documents")
    @patch("llama_index.core.node_parser.SimpleNodeParser.from_defaults")
    def test_index_by_page_ids(
        self, mock_simple_node_parser_from_defaults, mock_from_documents
    ):
        mock_simple_node_parser_from_defaults.return_value = (
            self.mock_node_parser_instance
        )
        mock_index_instance = mock_from_documents.return_value
        mock_index_instance.insert_nodes = MagicMock()
        mock_doc1 = Document(text="Page 1 content", doc_id="page1")
        # mock_doc2 = Document( # F841: local variable `mock_doc2` is assigned to but never used
        #     text="Page 2 content", doc_id="page2"
        # )
        self.indexer.reader.load_data.return_value = [
            mock_doc1
        ]  # Simulate loading one page
        mock_nodes_from_pages = [MagicMock(name="node_page1")]
        self.mock_service_context.node_parser.get_nodes_from_documents.return_value = (
            mock_nodes_from_pages
        )
        result = self.indexer.index(
            page_id="page1",  # Changed from page_ids
            storage_context=self.mock_storage_context,
            service_context=self.mock_service_context,
            # space_key is optional and not needed here
        )
        self.indexer.reader.load_data.assert_called_once_with(
            page_ids=["page1"]
        )  # Ensure it's called with a list
        self.mock_service_context.node_parser.get_nodes_from_documents.assert_called_once_with(
            [mock_doc1]
        )
        mock_index_instance.insert_nodes.assert_called_once_with(mock_nodes_from_pages)
        self.assertEqual(result["total_nodes_indexed"], 1)  # Adjusted for one page
        self.assertEqual(result["index"], mock_index_instance)
        self.mock_storage_context.persist.assert_called_once()

    @patch("llama_index.core.VectorStoreIndex.from_documents")
    @patch("llama_index.core.node_parser.SimpleNodeParser.from_defaults")
    def test_index_no_documents_by_space_key(
        self, mock_simple_node_parser_from_defaults, mock_from_documents
    ):
        mock_simple_node_parser_from_defaults.return_value = (
            self.mock_node_parser_instance
        )
        mock_index_instance = mock_from_documents.return_value
        mock_index_instance.insert_nodes = MagicMock()
        self.indexer.reader.load_data.return_value = []
        result = self.indexer.index(
            space_key="NO_DOCS_SPACE",
            storage_context=self.mock_storage_context,
            service_context=self.mock_service_context,
            max_pages_to_fetch=70,  # Explicitly set for test, will result in api_batch_size=50 for first call
        )
        # The indexer's loop uses api_batch_size = 50 if max_pages_to_fetch is > 50 or None.
        # num_to_request_this_batch will be min(50, 70) = 50
        self.indexer.reader.load_data.assert_called_once_with(
            space_key="NO_DOCS_SPACE", start=0, max_num_results=50
        )
        self.mock_service_context.node_parser.get_nodes_from_documents.assert_not_called()  # Corrected assertion
        mock_index_instance.insert_nodes.assert_not_called()
        self.assertEqual(result["total_nodes_indexed"], 0)
        self.assertEqual(result["index"], mock_index_instance)
        self.mock_storage_context.persist.assert_called_once()

    @patch("llama_index.core.VectorStoreIndex.from_documents")
    @patch("llama_index.core.node_parser.SimpleNodeParser.from_defaults")
    def test_index_no_documents_by_page_id(
        self, mock_simple_node_parser_from_defaults, mock_from_documents
    ):
        mock_simple_node_parser_from_defaults.return_value = (
            self.mock_node_parser_instance
        )
        mock_index_instance = mock_from_documents.return_value
        mock_index_instance.insert_nodes = MagicMock()
        self.indexer.reader.load_data.return_value = []
        self.mock_service_context.node_parser.get_nodes_from_documents.return_value = []
        result = self.indexer.index(
            page_id="nonexistent1",  # Changed from page_ids
            storage_context=self.mock_storage_context,
            service_context=self.mock_service_context,
            # space_key is optional and not needed
        )
        self.indexer.reader.load_data.assert_called_once_with(page_ids=["nonexistent1"])
        self.mock_service_context.node_parser.get_nodes_from_documents.assert_not_called()  # Corrected assertion
        mock_index_instance.insert_nodes.assert_not_called()
        self.assertEqual(result["total_nodes_indexed"], 0)
        self.assertEqual(result["index"], mock_index_instance)
        self.mock_storage_context.persist.assert_called_once()


if __name__ == "__main__":
    unittest.main()
