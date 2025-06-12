import os
import unittest
from unittest.mock import patch

# Assuming AppSettings can be imported like this.
# Adjust if the path or import method is different.
from moonmind.config.settings import AppSettings, AtlassianSettings, ConfluenceSettings, JiraSettings

class TestAtlassianSettings(unittest.TestCase):

    @patch.dict(os.environ, {
        "ATLASSIAN_API_KEY": "test_api_key",
        "ATLASSIAN_USERNAME": "test_user",
        "ATLASSIAN_URL": "https://test.atlassian.net",
        "ATLASSIAN_ENABLED": "True",
        "ATLASSIAN_CONFLUENCE_ENABLED": "True",
        "ATLASSIAN_CONFLUENCE_SPACE_KEYS": "TEST_SPACE_1,TEST_SPACE_2",
        "ATLASSIAN_JIRA_ENABLED": "True",
        "ATLASSIAN_JIRA_JQL_QUERY": "project=TEST",
        "ATLASSIAN_JIRA_FETCH_BATCH_SIZE": "100"
    })
    def test_load_atlassian_settings_from_env(self):
        # Create the main settings
        settings = AppSettings()
        
        # Verify the main Atlassian settings
        self.assertTrue(settings.atlassian.atlassian_enabled)
        self.assertEqual(settings.atlassian.atlassian_api_key, "test_api_key")
        self.assertEqual(settings.atlassian.atlassian_username, "test_user")
        self.assertEqual(settings.atlassian.atlassian_url, "https://test.atlassian.net")
        
        # Verify the nested settings in the main settings
        self.assertTrue(settings.atlassian.confluence.confluence_enabled)
        self.assertEqual(settings.atlassian.confluence.confluence_space_keys, "TEST_SPACE_1,TEST_SPACE_2")
        
        self.assertTrue(settings.atlassian.jira.jira_enabled)
        self.assertEqual(settings.atlassian.jira.jira_jql_query, "project=TEST")
        self.assertEqual(settings.atlassian.jira.jira_fetch_batch_size, 100)

    @patch.dict(os.environ, {}, clear=True) # Clear all env vars for this test method
    def test_atlassian_settings_defaults(self):
        # Keys relevant to Atlassian settings that might have been set by other tests or globally
        # We want to ensure these are not present for this default testing scenario.
        atlassian_env_keys = [
            "ATLASSIAN_API_KEY", "ATLASSIAN_USERNAME", "ATLASSIAN_URL", "ATLASSIAN_ENABLED",
            "ATLASSIAN_CONFLUENCE_ENABLED", "ATLASSIAN_CONFLUENCE_SPACE_KEYS",
            "ATLASSIAN_JIRA_ENABLED", "ATLASSIAN_JIRA_JQL_QUERY", "ATLASSIAN_JIRA_FETCH_BATCH_SIZE"
        ]
        
        # Construct a dictionary for patch.dict to ensure these keys are removed from os.environ
        # if they were somehow set before this test method's @patch.dict(clear=True) took effect
        # or if they were set by a broader scope patch.
        vars_to_ensure_removed = {k: "" for k in atlassian_env_keys if k in os.environ}

        with patch.dict(os.environ, vars_to_ensure_removed, clear=True):
            # Instantiating AppSettings here will read from the (now modified for this context) os.environ
            temp_settings = AppSettings()

            # Check default values (as defined in Pydantic models)
            self.assertFalse(temp_settings.atlassian.atlassian_enabled) # Default is False
            self.assertIsNone(temp_settings.atlassian.atlassian_api_key)
            self.assertIsNone(temp_settings.atlassian.atlassian_username)
            self.assertIsNone(temp_settings.atlassian.atlassian_url)

            self.assertFalse(temp_settings.atlassian.confluence.confluence_enabled) # Default is False
            self.assertIsNone(temp_settings.atlassian.confluence.confluence_space_keys)

            self.assertFalse(temp_settings.atlassian.jira.jira_enabled) # Default is False
            self.assertIsNone(temp_settings.atlassian.jira.jira_jql_query)
            self.assertEqual(temp_settings.atlassian.jira.jira_fetch_batch_size, 50) # Default is 50


    def test_atlassian_settings_types(self):
        # Instantiate with whatever environment is currently active; type checks are independent of values.
        settings = AppSettings() 
        self.assertIsInstance(settings.atlassian, AtlassianSettings)
        self.assertIsInstance(settings.atlassian.confluence, ConfluenceSettings)
        self.assertIsInstance(settings.atlassian.jira, JiraSettings)
        
        # Field type checks
        self.assertIsInstance(settings.atlassian.atlassian_enabled, bool)
        # Optional fields can be None or str, but after Pydantic processing, they should be str if set.
        # If not set, they are None. Type check against Pydantic model field types might be more robust.
        # However, for enabled flags, they are bool. For batch_size, it's int.
        
        self.assertIsInstance(settings.atlassian.confluence.confluence_enabled, bool)
        
        self.assertIsInstance(settings.atlassian.jira.jira_enabled, bool)
        self.assertIsInstance(settings.atlassian.jira.jira_fetch_batch_size, int)

if __name__ == "__main__":
    unittest.main()
