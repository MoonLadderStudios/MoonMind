import unittest
from unittest.mock import MagicMock, patch
from llama_index import ServiceContext, StorageContext
from llama_index.readers.confluence import ConfluenceReader # For type hinting if needed, but will be mocked
from llama_index.core.schema import Document # For creating mock documents

from moonmind.indexers.confluence_indexer import ConfluenceIndexer

# Mocking SimpleNodeParser for the case where service_context.node_parser is not set
# This might be needed if the ConfluenceIndexer tries to default it.
# We'll mock it where it's imported within the indexer.
@patch('llama_index.core.node_parser.SimpleNodeParser.from_defaults')
class TestConfluenceIndexer(unittest.TestCase):

    def setUp(self, mock_simple_node_parser_from_defaults):
        # Mock the SimpleNodeParser's get_nodes_from_documents method
        self.mock_node_parser_instance = MagicMock()
        mock_simple_node_parser_from_defaults.return_value = self.mock_node_parser_instance

        self.mock_service_context = MagicMock(spec=ServiceContext)
        # Ensure node_parser is part of the mock_service_context spec if accessed directly
        # If ConfluenceIndexer checks for AttributeError on node_parser, this is fine.
        # If it expects node_parser to exist, we should set it:
        self.mock_service_context.node_parser = self.mock_node_parser_instance

        self.mock_storage_context = MagicMock(spec=StorageContext)
        
        self.indexer = ConfluenceIndexer(
            base_url="http://fake-confluence.com",
            api_token="fake_token",
            user_name="fake_user",
            logger=MagicMock()
        )
        # Replace the actual ConfluenceReader with a mock
        self.indexer.reader = MagicMock(spec=ConfluenceReader)

    def test_initialization(self, mock_simple_node_parser_from_defaults):
        self.assertIsNotNone(self.indexer)
        self.assertEqual(self.indexer.base_url, "http://fake-confluence.com")
        self.assertIsInstance(self.indexer.reader, MagicMock)

    @patch('llama_index.VectorStoreIndex.from_documents')
    def test_index_by_space_key(self, mock_from_documents, mock_simple_node_parser_from_defaults):
        mock_index_instance = MagicMock()
        mock_from_documents.return_value = mock_index_instance

        mock_doc1 = Document(text="Doc 1 content", doc_id="doc1")
        mock_doc2 = Document(text="Doc 2 content", doc_id="doc2")
        mock_doc3 = Document(text="Doc 3 content", doc_id="doc3")

        # Simulate batching: first call returns 2 docs, second call returns 1, third returns empty
        self.indexer.reader.load_data.side_effect = [
            [mock_doc1, mock_doc2],
            [mock_doc3],
            []
        ]
        
        # Simulate node parsing
        # Let's say 2 docs become 2 nodes, and 1 doc becomes 1 node.
        def mock_get_nodes(docs):
            return [MagicMock() for _ in docs] # Each doc maps to one node
        self.mock_service_context.node_parser.get_nodes_from_documents.side_effect = mock_get_nodes


        result = self.indexer.index(
            space_key="TEST_SPACE",
            storage_context=self.mock_storage_context,
            service_context=self.mock_service_context,
            confluence_fetch_batch_size=2 # Set batch size to 2 for testing pagination
        )

        self.assertEqual(self.indexer.reader.load_data.call_count, 3)
        self.indexer.reader.load_data.assert_any_call(space_key="TEST_SPACE", start=0, max_num_results=2)
        self.indexer.reader.load_data.assert_any_call(space_key="TEST_SPACE", start=2, max_num_results=2)
        self.indexer.reader.load_data.assert_any_call(space_key="TEST_SPACE", start=4, max_num_results=2) # Last call that returns []

        # Check node parsing calls
        self.mock_service_context.node_parser.get_nodes_from_documents.assert_any_call([mock_doc1, mock_doc2, mock_doc3])
        
        # Check nodes insertion
        # The current implementation fetches all docs then converts and inserts.
        mock_index_instance.insert_nodes.assert_called_once() 
        self.assertEqual(len(mock_index_instance.insert_nodes.call_args[0][0]), 3) # 3 nodes inserted

        self.assertEqual(result["total_nodes_indexed"], 3)
        self.assertEqual(result["index"], mock_index_instance)
        self.mock_storage_context.persist.assert_called_once()

    @patch('llama_index.VectorStoreIndex.from_documents')
    def test_index_by_page_ids(self, mock_from_documents, mock_simple_node_parser_from_defaults):
        mock_index_instance = MagicMock()
        mock_from_documents.return_value = mock_index_instance

        mock_doc1 = Document(text="Page 1 content", doc_id="page1")
        mock_doc2 = Document(text="Page 2 content", doc_id="page2")
        
        self.indexer.reader.load_data.return_value = [mock_doc1, mock_doc2]
        
        def mock_get_nodes(docs):
            return [MagicMock() for _ in docs]
        self.mock_service_context.node_parser.get_nodes_from_documents.side_effect = mock_get_nodes

        result = self.indexer.index(
            space_key="ANY_SPACE_KEY_BUT_SHOULD_BE_IGNORED", # space_key is mandatory in model, but logic should ignore
            page_ids=["page1", "page2"],
            storage_context=self.mock_storage_context,
            service_context=self.mock_service_context
        )

        self.indexer.reader.load_data.assert_called_once_with(page_ids=["page1", "page2"])
        self.mock_service_context.node_parser.get_nodes_from_documents.assert_called_once_with([mock_doc1, mock_doc2])
        mock_index_instance.insert_nodes.assert_called_once()
        self.assertEqual(len(mock_index_instance.insert_nodes.call_args[0][0]), 2)


        self.assertEqual(result["total_nodes_indexed"], 2)
        self.assertEqual(result["index"], mock_index_instance)
        self.mock_storage_context.persist.assert_called_once()

    @patch('llama_index.VectorStoreIndex.from_documents')
    def test_index_no_documents_by_space_key(self, mock_from_documents, mock_simple_node_parser_from_defaults):
        mock_index_instance = MagicMock()
        mock_from_documents.return_value = mock_index_instance
        
        self.indexer.reader.load_data.return_value = [] # No documents found

        result = self.indexer.index(
            space_key="NO_DOCS_SPACE",
            storage_context=self.mock_storage_context,
            service_context=self.mock_service_context
        )
        
        self.indexer.reader.load_data.assert_called_once_with(space_key="NO_DOCS_SPACE", start=0, max_num_results=100) # Default batch size
        self.mock_service_context.node_parser.get_nodes_from_documents.assert_not_called() # Should not be called if no docs
        mock_index_instance.insert_nodes.assert_not_called()

        self.assertEqual(result["total_nodes_indexed"], 0)
        self.assertEqual(result["index"], mock_index_instance) # Index is still returned
        self.mock_storage_context.persist.assert_called_once() # Persist is called even if no docs

    @patch('llama_index.VectorStoreIndex.from_documents')
    def test_index_no_documents_by_page_id(self, mock_from_documents, mock_simple_node_parser_from_defaults):
        mock_index_instance = MagicMock()
        mock_from_documents.return_value = mock_index_instance
        
        self.indexer.reader.load_data.return_value = [] # No documents found

        result = self.indexer.index(
            space_key="ANY_SPACE",
            page_ids=["nonexistent1", "nonexistent2"],
            storage_context=self.mock_storage_context,
            service_context=self.mock_service_context
        )
        
        self.indexer.reader.load_data.assert_called_once_with(page_ids=["nonexistent1", "nonexistent2"])
        self.mock_service_context.node_parser.get_nodes_from_documents.assert_not_called()
        mock_index_instance.insert_nodes.assert_not_called()

        self.assertEqual(result["total_nodes_indexed"], 0)
        self.assertEqual(result["index"], mock_index_instance)
        self.mock_storage_context.persist.assert_called_once()

if __name__ == '__main__':
    unittest.main()
