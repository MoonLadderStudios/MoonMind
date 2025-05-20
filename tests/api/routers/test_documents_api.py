import unittest
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

# Assuming settings are accessible like this for mocking
from moonmind.config import settings as app_settings
# Models for request bodies
from moonmind.models.documents_models import ConfluenceLoadRequest, GitHubLoadRequest
# The router from your application
from fastapi.api.routers.documents import router as documents_router
from fastapi import HTTPException # For testing exceptions


class TestDocumentsAPI(unittest.TestCase):

    def setUp(self):
        self.app = FastAPI()
        self.app.include_router(documents_router, prefix="/api") # Assuming a prefix if any
        
        # Mock dependencies: get_storage_context and get_service_context
        # These are module-level functions in fastapi.api.dependencies
        self.mock_storage_context = MagicMock()
        self.mock_service_context = MagicMock()

        # Apply patches for dependencies
        self.patch_get_storage_context = patch('fastapi.api.dependencies.get_storage_context', return_value=self.mock_storage_context)
        self.patch_get_service_context = patch('fastapi.api.dependencies.get_service_context', return_value=self.mock_service_context)
        
        self.mock_get_storage_context = self.patch_get_storage_context.start()
        self.mock_get_service_context = self.patch_get_service_context.start()

        # Mock ConfluenceIndexer
        self.mock_confluence_indexer_instance = MagicMock()
        self.patch_confluence_indexer = patch('fastapi.api.routers.documents.ConfluenceIndexer', return_value=self.mock_confluence_indexer_instance)
        self.mock_confluence_indexer_class = self.patch_confluence_indexer.start()

        # Mock GitHubIndexer
        self.mock_github_indexer_instance = MagicMock()
        self.patch_github_indexer = patch('fastapi.api.routers.documents.GitHubIndexer', return_value=self.mock_github_indexer_instance)
        self.mock_github_indexer_class = self.patch_github_indexer.start()


        self.client = TestClient(self.app)

    def tearDown(self):
        self.patch_get_storage_context.stop()
        self.patch_get_service_context.stop()
        self.patch_confluence_indexer.stop()
        self.patch_github_indexer.stop() # Stop GitHubIndexer patch
        # Reset settings if they were changed for specific tests
        try:
            del app_settings.confluence.__dict__['_confluence_enabled_original']
        except AttributeError:
            pass # Not set, no problem

    def test_load_by_space_key_success(self):
        # Store original value and set mock value
        original_setting = app_settings.confluence.confluence_enabled
        app_settings.confluence.confluence_enabled = True
        app_settings.confluence.__dict__['_confluence_enabled_original'] = original_setting


        self.mock_confluence_indexer_instance.index.return_value = {"index": MagicMock(), "total_nodes_indexed": 5}

        response = self.client.post(
            "/api/documents/confluence/load", # Adjusted path if you have a prefix
            json={"space_key": "TEST_SPACE", "max_num_results": 50}
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["message"], "Successfully loaded 5 nodes from Confluence space TEST_SPACE.")
        self.assertEqual(data["total_nodes_indexed"], 5)
        
        self.mock_confluence_indexer_class.assert_called_once() # Check if ConfluenceIndexer was instantiated
        self.mock_confluence_indexer_instance.index.assert_called_once_with(
            space_key="TEST_SPACE",
            storage_context=self.mock_storage_context,
            service_context=self.mock_service_context,
            page_ids=None, # Pydantic model defaults to None
            confluence_fetch_batch_size=50
        )
        # Restore original setting
        app_settings.confluence.confluence_enabled = app_settings.confluence.__dict__['_confluence_enabled_original']
        del app_settings.confluence.__dict__['_confluence_enabled_original']


    def test_load_by_page_ids_success(self):
        original_setting = app_settings.confluence.confluence_enabled
        app_settings.confluence.confluence_enabled = True
        app_settings.confluence.__dict__['_confluence_enabled_original'] = original_setting

        self.mock_confluence_indexer_instance.index.return_value = {"index": MagicMock(), "total_nodes_indexed": 2}

        response = self.client.post(
            "/api/documents/confluence/load",
            json={"space_key": "ANY_SPACE", "page_ids": ["101", "102"], "max_num_results": 100} # max_num_results still passed
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["message"], "Successfully loaded 2 nodes from 2 specified page IDs.")
        self.assertEqual(data["total_nodes_indexed"], 2)

        self.mock_confluence_indexer_class.assert_called_once()
        self.mock_confluence_indexer_instance.index.assert_called_once_with(
            space_key="ANY_SPACE",
            storage_context=self.mock_storage_context,
            service_context=self.mock_service_context,
            page_ids=["101", "102"],
            confluence_fetch_batch_size=100 # Default from model if not provided, or value from request
        )
        # Restore original setting
        app_settings.confluence.confluence_enabled = app_settings.confluence.__dict__['_confluence_enabled_original']
        del app_settings.confluence.__dict__['_confluence_enabled_original']

    def test_load_confluence_disabled(self):
        original_setting = app_settings.confluence.confluence_enabled
        app_settings.confluence.confluence_enabled = False
        app_settings.confluence.__dict__['_confluence_enabled_original'] = original_setting

        response = self.client.post(
            "/api/documents/confluence/load",
            json={"space_key": "TEST_SPACE"}
        )
        self.assertEqual(response.status_code, 500)
        data = response.json()
        self.assertEqual(data["detail"], "Confluence is not enabled")
        
        self.mock_confluence_indexer_instance.index.assert_not_called() # Indexer should not be called

        # Restore original setting
        app_settings.confluence.confluence_enabled = app_settings.confluence.__dict__['_confluence_enabled_original']
        del app_settings.confluence.__dict__['_confluence_enabled_original']


    # --- Tests for GitHub Endpoint ---

    def test_load_github_repo_success(self):
        self.mock_github_indexer_instance.index.return_value = {"index": MagicMock(), "total_nodes_indexed": 5}
        
        payload = {"repo": "test/repo", "branch": "dev"}
        response = self.client.post("/api/documents/github/load", json=payload)

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["total_nodes_indexed"], 5)
        self.assertEqual(data["repository"], "test/repo")
        self.assertEqual(data["branch"], "dev")
        self.assertIn("Successfully loaded 5 nodes from GitHub repository test/repo on branch dev", data["message"])

        self.mock_github_indexer_class.assert_called_once_with(github_token=None, logger=ANY)
        self.mock_github_indexer_instance.index.assert_called_once_with(
            repo_full_name="test/repo",
            branch="dev",
            filter_extensions=None, # Default from model
            storage_context=self.mock_storage_context,
            service_context=self.mock_service_context
        )

    def test_load_github_repo_with_token_and_filters(self):
        self.mock_github_indexer_instance.index.return_value = {"index": MagicMock(), "total_nodes_indexed": 3}
        
        payload = {
            "repo": "secure/repo", 
            "branch": "main", 
            "github_token": "secret_token", 
            "filter_extensions": [".py", ".md"]
        }
        response = self.client.post("/api/documents/github/load", json=payload)

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["total_nodes_indexed"], 3)
        self.assertEqual(data["repository"], "secure/repo")

        self.mock_github_indexer_class.assert_called_once_with(github_token="secret_token", logger=ANY)
        self.mock_github_indexer_instance.index.assert_called_once_with(
            repo_full_name="secure/repo",
            branch="main",
            filter_extensions=[".py", ".md"],
            storage_context=self.mock_storage_context,
            service_context=self.mock_service_context
        )

    def test_load_github_repo_invalid_repo_format(self):
        # This error is raised by GitHubIndexer and caught by the endpoint
        self.mock_github_indexer_instance.index.side_effect = ValueError("Invalid repo format from indexer")
        
        payload = {"repo": "invalid-format", "branch": "main"} # API model validation passes
        response = self.client.post("/api/documents/github/load", json=payload)

        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertIn("Invalid repo format from indexer", data["detail"])

    def test_load_github_repo_indexer_raises_http_exception(self):
        self.mock_github_indexer_instance.index.side_effect = HTTPException(status_code=502, detail="Git upstream error from indexer")
        
        payload = {"repo": "owner/repo", "branch": "main"}
        response = self.client.post("/api/documents/github/load", json=payload)

        self.assertEqual(response.status_code, 502)
        data = response.json()
        self.assertIn("Git upstream error from indexer", data["detail"])

    def test_load_github_repo_indexer_raises_unexpected_exception(self):
        self.mock_github_indexer_instance.index.side_effect = Exception("Some unexpected internal error in indexer")
        
        payload = {"repo": "owner/repo", "branch": "main"}
        response = self.client.post("/api/documents/github/load", json=payload)

        self.assertEqual(response.status_code, 500)
        data = response.json()
        self.assertIn("An unexpected error occurred while processing owner/repo", data["detail"])
        self.assertIn("Some unexpected internal error in indexer", data["detail"])


if __name__ == '__main__':
    unittest.main()
