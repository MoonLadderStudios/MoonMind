import unittest
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

# Assuming settings are accessible like this for mocking
from moonmind.config import settings as app_settings 
from moonmind.models.documents_models import ConfluenceLoadRequest
# The router from your application
from fastapi.api.routers.documents import router as documents_router 


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

        self.client = TestClient(self.app)

    def tearDown(self):
        self.patch_get_storage_context.stop()
        self.patch_get_service_context.stop()
        self.patch_confluence_indexer.stop()
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

if __name__ == '__main__':
    unittest.main()
