import logging  # For silencing logger if needed, or passing a mock logger
import unittest
from unittest.mock import MagicMock, call, patch  # Removed PropertyMock

from llama_index.core import Settings, StorageContext  # Use Settings
from llama_index.core.schema import Document

from moonmind.indexers.jira_indexer import JiraIndexer

# Assuming JiraReader is in llama_index.readers.jira. If it's elsewhere, adjust path.


class TestJiraIndexer(unittest.TestCase):
    def setUp(self):
        self.mock_logger = MagicMock(spec=logging.Logger)

        # Patch resolve_embed_model globally to prevent OpenAI API key check
        # The target is 'llama_index.core.settings.resolve_embed_model' as Settings class imports and uses it.
        self.resolve_embed_model_patcher = patch(
            "llama_index.core.settings.resolve_embed_model"
        )
        self.MockResolveEmbedModel = self.resolve_embed_model_patcher.start()
        self.mock_embed_model_instance = MagicMock(name="MockEmbedModelInstance")
        self.MockResolveEmbedModel.return_value = self.mock_embed_model_instance

        # Patch resolve_llm globally to prevent OpenAI API key check
        # The target is 'llama_index.core.settings.resolve_llm' as Settings class imports and uses it.
        self.resolve_llm_patcher = patch("llama_index.core.settings.resolve_llm")
        self.MockResolveLlm = self.resolve_llm_patcher.start()
        self.mock_llm_instance = MagicMock(name="MockLlmInstance")
        self.MockResolveLlm.return_value = self.mock_llm_instance

        # Mock for Settings
        # Now, if MagicMock(spec=Settings) internally tries to access/evaluate Settings.embed_model or Settings.llm,
        # the calls to resolve_embed_model/resolve_llm within them will be intercepted by our patches.
        self.mock_settings_obj = MagicMock(spec=Settings)

        # Explicitly set embed_model and llm on the instance to our controlled mocks
        # This is important because the actual JiraIndexer code will access service_context.embed_model and potentially .llm
        self.mock_settings_obj.embed_model = self.mock_embed_model_instance
        self.mock_settings_obj.llm = self.mock_llm_instance

        self.mock_node_parser_instance = MagicMock()
        self.mock_settings_obj.node_parser = self.mock_node_parser_instance

        self.mock_storage_context = MagicMock(spec=StorageContext)

        # Patch JiraReader within the moonmind.indexers.jira_indexer module
        # This is where JiraIndexer looks for JiraReader
        self.jira_reader_patcher = patch("moonmind.indexers.jira_indexer.JiraReader")
        self.MockJiraReaderClass = self.jira_reader_patcher.start()
        self.mock_jira_reader_instance = (
            self.MockJiraReaderClass.return_value
        )  # Instance returned by JiraReader(...)

        self.indexer = JiraIndexer(
            jira_url="https://fake-jira.com",
            username="fake_user",
            api_token="fake_token",
            logger=self.mock_logger,
        )
        # The self.indexer.reader is now the self.mock_jira_reader_instance automatically
        # due to patching JiraReader at the class level where it's imported in jira_indexer.py

    def tearDown(self):
        self.resolve_embed_model_patcher.stop()
        self.resolve_llm_patcher.stop()  # Stop the new llm patcher
        self.jira_reader_patcher.stop()  # Stop JiraReader patcher

    def test_initialization_success(self):
        self.assertIsNotNone(self.indexer)
        self.assertEqual(self.indexer.jira_url, "https://fake-jira.com")
        self.assertEqual(self.indexer.username, "fake_user")
        self.MockJiraReaderClass.assert_called_once_with(
            server_url="fake-jira.com",  # Updated to reflect scheme removal by JiraIndexer
            email="fake_user",
            api_token="fake_token",
        )
        self.assertIsNotNone(
            self.indexer.reader, "JiraReader instance should be created."
        )
        self.assertEqual(self.indexer.reader, self.mock_jira_reader_instance)

    def test_initialization_failure_missing_url(self):
        with self.assertRaisesRegex(ValueError, "Jira URL is required."):
            JiraIndexer(
                jira_url="", username="user", api_token="token", logger=self.mock_logger
            )

    def test_initialization_failure_missing_username(self):
        with self.assertRaisesRegex(ValueError, "Jira username is required."):
            JiraIndexer(
                jira_url="https://url",
                username="",
                api_token="token",
                logger=self.mock_logger,
            )

    def test_initialization_failure_missing_token(self):
        with self.assertRaisesRegex(ValueError, "Jira API token is required."):
            JiraIndexer(
                jira_url="https://url",
                username="user",
                api_token="",
                logger=self.mock_logger,
            )

    def test_jira_url_scheme_removal(self):
        test_cases = [
            ("https://withssl.com", "withssl.com"),
            ("http://no_ssl.com", "no_ssl.com"),
            ("justdomain.com", "justdomain.com"),
            ("https://://weirdprefix.com", "weirdprefix.com"),
            # Test with paths and query params, ensuring they are preserved if scheme is removed
            ("https://example.com/jira", "example.com/jira"),
            ("http://example.com/path?query=1", "example.com/path?query=1"),
            ("example.com/pathonly", "example.com/pathonly"),
            ("https://://example.com/another/path", "example.com/another/path"),
        ]

        original_username = "test_user_scheme_test"  # Use a different username to avoid clashes if logs were checked
        original_api_token = "test_token_scheme_test"

        for jira_url_input, expected_server_url in test_cases:
            with self.subTest(
                jira_url_input=jira_url_input
            ):  # Provides better output if one case fails
                self.MockJiraReaderClass.reset_mock()
                JiraIndexer(
                    jira_url=jira_url_input,
                    username=original_username,
                    api_token=original_api_token,
                    logger=self.mock_logger,
                )
                self.MockJiraReaderClass.assert_called_once_with(
                    server_url=expected_server_url,
                    email=original_username,
                    api_token=original_api_token,
                )

    @patch("moonmind.indexers.jira_indexer.VectorStoreIndex.from_documents")
    # If JiraIndexer uses SimpleNodeParser as a fallback, you might need to patch it too.
    # @patch('moonmind.indexers.jira_indexer.SimpleNodeParser.from_defaults')
    def test_index_successful_pagination(
        self, mock_from_documents
    ):  # , mock_simple_node_parser_from_defaults):
        # mock_simple_node_parser_from_defaults.return_value = self.mock_node_parser_instance # If using this patch

        mock_index_instance = mock_from_documents.return_value
        mock_index_instance.insert_nodes = MagicMock()

        mock_doc_batch1 = [
            Document(text="Issue 1 Summary", doc_id="JIRA-1"),
            Document(text="Issue 2 Summary", doc_id="JIRA-2"),
        ]
        mock_doc_batch2 = [Document(text="Issue 3 Summary", doc_id="JIRA-3")]

        # Configure the mocked JiraReader's load_data method
        self.mock_jira_reader_instance.load_data.side_effect = [
            mock_doc_batch1,  # First call returns 2 docs
            mock_doc_batch2,  # Second call returns 1 doc
            [],  # Third call returns empty list, stopping pagination
        ]

        mock_nodes_batch1 = [MagicMock(name="node1_b1"), MagicMock(name="node2_b1")]
        mock_nodes_batch2 = [MagicMock(name="node3_b2")]
        self.mock_settings_obj.node_parser.get_nodes_from_documents.side_effect = [
            mock_nodes_batch1,
            mock_nodes_batch2,
        ]

        test_jql = "project = TEST"
        batch_size = 2

        result = self.indexer.index(
            jql_query=test_jql,
            storage_context=self.mock_storage_context,
            service_context=self.mock_settings_obj,  # Pass the mock Settings object
            jira_fetch_batch_size=batch_size,
        )

        self.assertEqual(result["total_nodes_indexed"], 3)  # 2 + 1
        self.assertEqual(result["index"], mock_index_instance)

        # Check calls to reader.load_data
        expected_load_data_calls = [
            call(query=test_jql, start_at=0, max_results=batch_size),
            call(query=test_jql, start_at=batch_size, max_results=batch_size),
            call(
                query=test_jql, start_at=batch_size * 2, max_results=batch_size
            ),  # Called 3 times
        ]
        self.mock_jira_reader_instance.load_data.assert_has_calls(
            expected_load_data_calls
        )
        self.assertEqual(self.mock_jira_reader_instance.load_data.call_count, 3)

        # Check calls to node_parser.get_nodes_from_documents
        self.mock_settings_obj.node_parser.get_nodes_from_documents.assert_any_call(
            mock_doc_batch1
        )
        self.mock_settings_obj.node_parser.get_nodes_from_documents.assert_any_call(
            mock_doc_batch2
        )
        self.assertEqual(
            self.mock_settings_obj.node_parser.get_nodes_from_documents.call_count, 2
        )

        # Check calls to index.insert_nodes
        mock_index_instance.insert_nodes.assert_any_call(mock_nodes_batch1)
        mock_index_instance.insert_nodes.assert_any_call(mock_nodes_batch2)
        self.assertEqual(mock_index_instance.insert_nodes.call_count, 2)

        self.mock_storage_context.persist.assert_called_once()
        mock_from_documents.assert_called_once_with(
            [],
            storage_context=self.mock_storage_context,
            embed_model=self.mock_settings_obj.embed_model,
        )

    @patch("moonmind.indexers.jira_indexer.VectorStoreIndex.from_documents")
    def test_index_no_documents_found(self, mock_from_documents):
        mock_index_instance = mock_from_documents.return_value
        mock_index_instance.insert_nodes = MagicMock()

        self.mock_jira_reader_instance.load_data.return_value = (
            []
        )  # No documents returned

        self.mock_settings_obj.node_parser.get_nodes_from_documents.return_value = []

        test_jql = "project = EMPTY"
        result = self.indexer.index(
            jql_query=test_jql,
            storage_context=self.mock_storage_context,
            service_context=self.mock_settings_obj,
            jira_fetch_batch_size=50,
        )

        self.assertEqual(result["total_nodes_indexed"], 0)
        self.assertEqual(result["index"], mock_index_instance)
        self.mock_jira_reader_instance.load_data.assert_called_once_with(
            query=test_jql, start_at=0, max_results=50
        )
        self.mock_settings_obj.node_parser.get_nodes_from_documents.assert_not_called()  # Called with empty list, so get_nodes might not be called or return empty
        mock_index_instance.insert_nodes.assert_not_called()
        self.mock_storage_context.persist.assert_not_called()  # Persist is only called if total_nodes_indexed > 0

    def test_index_missing_jql_query(self):
        with self.assertRaisesRegex(ValueError, "JQL query is required for indexing."):
            self.indexer.index(
                jql_query="",  # Empty JQL
                storage_context=self.mock_storage_context,
                service_context=self.mock_settings_obj,
            )

    @patch("moonmind.indexers.jira_indexer.VectorStoreIndex.from_documents")
    def test_index_reader_load_data_raises_exception(self, mock_from_documents):
        # mock_index_instance = mock_from_documents.return_value # F841: local variable `mock_index_instance` is assigned to but never used

        self.mock_jira_reader_instance.load_data.side_effect = Exception(
            "Jira API Error"
        )

        test_jql = "project = FAIL"
        result = self.indexer.index(
            jql_query=test_jql,
            storage_context=self.mock_storage_context,
            service_context=self.mock_settings_obj,
            jira_fetch_batch_size=50,
        )

        self.assertEqual(result["total_nodes_indexed"], 0)
        self.mock_logger.error.assert_called_with(
            "Failed to load data from Jira: Jira API Error", exc_info=True
        )
        self.mock_storage_context.persist.assert_not_called()


if __name__ == "__main__":
    unittest.main()
