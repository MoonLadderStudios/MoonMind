import unittest
from unittest.mock import MagicMock, patch, ANY
from llama_index.core import ServiceContext, StorageContext
from llama_index.readers.github import GithubRepositoryReader # For type hinting and spec for MagicMock
from llama_index.readers.github.utils import FilterType
from llama_index.core.node_parser import SimpleNodeParser
from llama_index.core.schema import Document # For creating mock documents
from fastapi import HTTPException

from moonmind.indexers.github_indexer import GitHubIndexer

@patch('moonmind.indexers.github_indexer.GithubRepositoryReader') # Patch where it's used
class TestGitHubIndexer(unittest.TestCase):

    def setUp(self):
        self.mock_storage_context = MagicMock(spec=StorageContext)
        self.mock_service_context = MagicMock(spec=ServiceContext)
        
        # Setup mock node parser and attach to service_context
        self.mock_node_parser = MagicMock(spec=SimpleNodeParser)
        self.mock_service_context.node_parser = self.mock_node_parser

        self.indexer = GitHubIndexer(logger=MagicMock())
        self.indexer_with_token = GitHubIndexer(github_token="test_token", logger=MagicMock())

    def test_index_success_public_repo(self, MockGithubReader):
        # Configure mock reader
        mock_reader_instance = MockGithubReader.return_value
        mock_doc = Document(text="Doc content", doc_id="doc1")
        mock_reader_instance.load_data.return_value = [mock_doc]
        
        # Configure mock node parser
        mock_node = MagicMock() # Represents a processed node
        self.mock_node_parser.get_nodes_from_documents.return_value = [mock_node]

        result = self.indexer.index(
            repo_full_name="owner/repo",
            branch="main",
            filter_extensions=None,
            storage_context=self.mock_storage_context,
            service_context=self.mock_service_context
        )

        MockGithubReader.assert_called_once_with(
            owner="owner",
            repo="repo",
            github_token=None,
            filter_file_extensions=None,
            verbose=False,
            concurrent_requests=5
        )
        mock_reader_instance.load_data.assert_called_once_with(branch="main")
        self.mock_node_parser.get_nodes_from_documents.assert_called_once_with([mock_doc])
        self.mock_storage_context.persist.assert_called_once()
        
        self.assertIsNotNone(result["index"]) # Index object should be created
        self.assertEqual(result["total_nodes_indexed"], 1)
        # Check if insert_nodes was called on the index
        # The actual index is created by VectorStoreIndex.from_documents, which we should mock or inspect carefully.
        # For simplicity, we trust the LlamaIndex components and focus on our logic.
        # If VectorStoreIndex.from_documents and index.insert_nodes need to be checked, more patching is required.

    def test_index_success_private_repo_with_token(self, MockGithubReader):
        mock_reader_instance = MockGithubReader.return_value
        mock_doc = Document(text="Private doc content", doc_id="priv_doc1")
        mock_reader_instance.load_data.return_value = [mock_doc]
        
        mock_node = MagicMock()
        self.mock_node_parser.get_nodes_from_documents.return_value = [mock_node]

        result = self.indexer_with_token.index(
            repo_full_name="secure_owner/private_repo",
            branch="develop",
            filter_extensions=None,
            storage_context=self.mock_storage_context,
            service_context=self.mock_service_context
        )

        MockGithubReader.assert_called_once_with(
            owner="secure_owner",
            repo="private_repo",
            github_token="test_token", # Token should be passed here
            filter_file_extensions=None,
            verbose=False,
            concurrent_requests=5
        )
        mock_reader_instance.load_data.assert_called_once_with(branch="develop")
        self.assertEqual(result["total_nodes_indexed"], 1)
        self.mock_storage_context.persist.assert_called_once()

    def test_index_with_filter_extensions(self, MockGithubReader):
        mock_reader_instance = MockGithubReader.return_value
        mock_reader_instance.load_data.return_value = [Document(text="Filtered doc")] # Assume one doc matches
        
        self.mock_node_parser.get_nodes_from_documents.return_value = [MagicMock()]

        filter_exts = [".py", ".md"]
        self.indexer.index(
            repo_full_name="owner/repo",
            branch="main",
            filter_extensions=filter_exts,
            storage_context=self.mock_storage_context,
            service_context=self.mock_service_context
        )

        MockGithubReader.assert_called_once_with(
            owner="owner",
            repo="repo",
            github_token=None,
            filter_file_extensions=(filter_exts, FilterType.INCLUDE), # Check filter applied
            verbose=False,
            concurrent_requests=5
        )
        self.assertEqual(MockGithubReader.call_args[1]['filter_file_extensions'][1], FilterType.INCLUDE)


    def test_index_invalid_repo_format(self, MockGithubReader):
        # This test does not need MockGithubReader, but it's passed by the class decorator
        with self.assertRaises(ValueError) as context:
            self.indexer.index(
                repo_full_name="invalid-repo-format-no-slash",
                branch="main",
                filter_extensions=None,
                storage_context=self.mock_storage_context,
                service_context=self.mock_service_context
            )
        self.assertIn("Invalid repo_full_name format", str(context.exception))

        with self.assertRaises(ValueError) as context:
            self.indexer.index(
                repo_full_name="/", # both empty
                branch="main",
                filter_extensions=None,
                storage_context=self.mock_storage_context,
                service_context=self.mock_service_context
            )
        self.assertIn("Owner and repo_name must not be empty", str(context.exception))

    def test_index_reader_load_data_raises_exception(self, MockGithubReader):
        mock_reader_instance = MockGithubReader.return_value
        # Simulate an error during document loading (e.g., repo not found, network issue)
        mock_reader_instance.load_data.side_effect = Exception("Git clone error or repo not found")

        with self.assertRaises(HTTPException) as context:
            self.indexer.index(
                repo_full_name="owner/repo-does-not-exist",
                branch="main",
                filter_extensions=None,
                storage_context=self.mock_storage_context,
                service_context=self.mock_service_context
            )
        # Check that the original exception message is part of the HTTPException detail
        self.assertEqual(context.exception.status_code, 500)
        self.assertIn("Failed to load repository content", str(context.exception.detail))
        self.assertIn("Git clone error or repo not found", str(context.exception.detail))


    def test_index_no_documents_found(self, MockGithubReader):
        mock_reader_instance = MockGithubReader.return_value
        mock_reader_instance.load_data.return_value = [] # No documents found
        
        # Node parser should not be called if there are no documents
        self.mock_node_parser.get_nodes_from_documents.return_value = []


        # We also need to mock VectorStoreIndex.from_documents as it's called even for empty.
        with patch('moonmind.indexers.github_indexer.VectorStoreIndex.from_documents') as mock_vs_from_docs:
            mock_empty_index = MagicMock()
            mock_vs_from_docs.return_value = mock_empty_index

            result = self.indexer.index(
                repo_full_name="owner/empty-repo",
                branch="main",
                filter_extensions=None,
                storage_context=self.mock_storage_context,
                service_context=self.mock_service_context
            )

            mock_vs_from_docs.assert_called_once_with(
                [], # Called with empty list
                storage_context=self.mock_storage_context,
                service_context=self.mock_service_context
            )
            self.mock_node_parser.get_nodes_from_documents.assert_not_called() # Because docs list is empty
            mock_empty_index.insert_nodes.assert_not_called() # No nodes to insert

        self.assertEqual(result["total_nodes_indexed"], 0)
        self.assertEqual(result["index"], mock_empty_index) 
        self.mock_storage_context.persist.assert_called_once()

if __name__ == '__main__':
    unittest.main()
