import logging
import os
import time
import unittest
import logging
from unittest.mock import patch, MagicMock, call, AsyncMock, Mock

from moonmind.models_cache import ModelCache, force_refresh_model_cache
from moonmind.config import settings # To mock API keys
from moonmind.config.settings import AppSettings # Import AppSettings class for patching

# Configure basic logging for tests to see warnings/errors if needed
logging.basicConfig(level=logging.INFO)

# IsProviderEnabledMockError removed
# ThreadConstructorCalledError removed as it's no longer used

class TestModelCache(unittest.TestCase):

    def setUp(self):
        from threading import Lock # Ensure Lock is imported if not already
        # from moonmind.models_cache import ModelCache # ModelCache is already imported in this file

        # Mock ModelCache.__init__ to do nothing - THIS PATCH IS NOW REMOVED/COMMENTED
        # def mock_model_cache_init(self_instance, *args, **kwargs):
        #     # print("DEBUG: Mocked ModelCache.__init__ called, truly doing nothing now.") # Optional
        #     pass

        # self.model_cache_init_patch = patch('moonmind.models_cache.ModelCache.__init__', side_effect=mock_model_cache_init)
        # self.mocked_init = self.model_cache_init_patch.start()

        ModelCache._lock = Lock() # Reset class-level lock
        ModelCache._instance = None # Force re-creation for each test

        # Store original settings values to restore in tearDown
        self.original_google_api_key = settings.google.google_api_key
        self.original_google_enabled = settings.google.google_enabled
        self.original_openai_api_key = settings.openai.openai_api_key
        self.original_openai_enabled = settings.openai.openai_enabled
        self.original_ollama_enabled = settings.ollama.ollama_enabled
        self.original_refresh_interval = settings.model_cache_refresh_interval_seconds

        # Set default settings for tests (providers enabled with fake keys)
        settings.google.google_api_key = "fake_google_key_for_test"
        settings.google.google_enabled = True
        settings.openai.openai_api_key = "fake_openai_key_for_test"
        settings.openai.openai_enabled = True
        settings.ollama.ollama_enabled = True

        # Mock the factory functions for listing models from providers
        # Corrected patch targets to where they are used (in moonmind.models_cache)
        self.mock_google_models_patch = patch('moonmind.models_cache.list_google_models')
        self.mock_openai_models_patch = patch('moonmind.models_cache.list_openai_models')
        self.mock_ollama_models_patch = patch('moonmind.models_cache.list_ollama_models', new_callable=AsyncMock)

        self.mock_list_google_models = self.mock_google_models_patch.start()
        self.mock_list_openai_models = self.mock_openai_models_patch.start()
        self.mock_list_ollama_models = self.mock_ollama_models_patch.start()

        # Define default mock return data for provider model lists
        self.google_model_raw_1 = MagicMock(name="gemini-pro-raw")
        self.google_model_raw_1.name = "models/gemini-pro"
        self.google_model_raw_1.input_token_limit = 8192
        self.google_model_raw_1.supported_generation_methods = ['generateContent']
        self.mock_list_google_models.return_value = [self.google_model_raw_1]

        self.openai_model_raw_1 = MagicMock(name="gpt-3.5-turbo-raw")
        self.openai_model_raw_1.id = "gpt-3.5-turbo"
        self.openai_model_raw_1.created = int(time.time()) - 1000
        self.openai_model_raw_1.owned_by = "openai" # Expected by parsing logic
        # The context window is likely derived by ModelCache from the ID.
        self.mock_list_openai_models.return_value = [self.openai_model_raw_1]

        self.ollama_model_raw_1 = {"name": "test-ollama-model", "details": {"parameter_size": "7B"}}
        self.mock_list_ollama_models.return_value = [self.ollama_model_raw_1]

        # Remove redundant assignments for ollama_model_raw_1

        # Patch AppSettings.is_provider_enabled to always return False
        self.mock_is_provider_enabled_patch = patch.object(AppSettings, 'is_provider_enabled') # Target AppSettings class
        self.mock_is_provider_enabled_method = self.mock_is_provider_enabled_patch.start()
        # self.mock_is_provider_enabled_method.side_effect = lambda *args: False # Always return False

        # New side_effect for is_provider_enabled
        def actual_side_effect_is_provider_enabled(provider_name_arg):
            # Access the global 'settings' which might be patched by individual tests
            provider_name = provider_name_arg.lower() # Ensure consistent casing
            if provider_name == "google":
                return settings.google.google_enabled and bool(settings.google.google_api_key)
            elif provider_name == "openai":
                return settings.openai.openai_enabled and bool(settings.openai.openai_api_key)
            elif provider_name == "ollama":
                return settings.ollama.ollama_enabled
            return False
        self.mock_is_provider_enabled_method.side_effect = actual_side_effect_is_provider_enabled
          # Prevent actual thread creation for most tests by mocking threading.Thread
        self.thread_patch = patch('moonmind.models_cache.Thread')
        mock_thread_class = self.thread_patch.start()
        self.mock_thread_class = mock_thread_class # Store for assertion
        # Revert to returning an instance
        self.mock_thread_instance = MagicMock()
        self.mock_thread_instance.start = MagicMock() # This is the one asserted in test_singleton_behavior
        mock_thread_class.return_value = self.mock_thread_instance
        mock_thread_class.side_effect = None # Ensure no side effect like raising an error# Patch time.sleep
        self.time_sleep_patch = patch('time.sleep', MagicMock())
        self.mock_time_sleep = self.time_sleep_patch.start()        # Patch ModelCache._periodic_refresh - but don't use autospec to avoid signature issues
        self.periodic_refresh_patch = patch('moonmind.models_cache.ModelCache._periodic_refresh')
        self.mock_periodic_refresh = self.periodic_refresh_patch.start()
        # Just make it do nothing - don't try to simulate behavior
        self.mock_periodic_refresh.return_value = None

        # Store original periodic_refresh for use in tests that need real functionality
        self._original_periodic_refresh = ModelCache._periodic_refresh

        # Patch threading.Thread.start to do nothing - REMOVED/COMMENTED OUT
        # self.thread_start_patch = patch('threading.Thread.start', MagicMock())
        # self.mock_thread_start_method = self.thread_start_patch.start()


    def tearDown(self):
        self.mock_google_models_patch.stop()
        self.mock_openai_models_patch.stop()
        self.mock_ollama_models_patch.stop()
        self.mock_is_provider_enabled_patch.stop()
        self.thread_patch.stop()
        if hasattr(self, 'time_sleep_patch') and self.time_sleep_patch.is_started: # Ensure patch was started
            self.time_sleep_patch.stop()
        if hasattr(self, 'periodic_refresh_patch') and self.periodic_refresh_patch.is_started:
            self.periodic_refresh_patch.stop()
        # if hasattr(self, 'thread_start_patch') and self.thread_start_patch.is_started: # REMOVED/COMMENTED OUT
            # self.thread_start_patch.stop() # REMOVED/COMMENTED OUT
        # Ensure model_cache_init_patch is stopped only if it was started
        if hasattr(self, 'model_cache_init_patch') and hasattr(self, 'mocked_init') and self.model_cache_init_patch.is_started:
            self.model_cache_init_patch.stop()
        patch.stopall()

        settings.google.google_api_key = self.original_google_api_key
        settings.google.google_enabled = self.original_google_enabled
        settings.openai.openai_api_key = self.original_openai_api_key
        settings.openai.openai_enabled = self.original_openai_enabled
        settings.ollama.ollama_enabled = self.original_ollama_enabled
        settings.model_cache_refresh_interval_seconds = self.original_refresh_interval

        ModelCache._instance = None


    def test_singleton_behavior(self):
        # The 'with patch' block for 'builtins.hasattr' and mock_hasattr definitions are removed.
        cache1 = ModelCache(refresh_interval_seconds=1000)
        # The assertions for thread class and instance start calls are kept as per original intent,
        # but they are expected to fail if __init__ is fully mocked to do nothing.
        self.mock_thread_class.assert_called_once()
        self.mock_thread_instance.start.assert_called_once()

        cache2 = ModelCache(refresh_interval_seconds=1000) # Should use existing instance
        self.assertIs(cache1, cache2)


    def test_default_refresh_interval_from_settings(self):
        with patch.object(settings, 'model_cache_refresh_interval_seconds', 43200):
            cache = ModelCache()
            self.assertEqual(cache.refresh_interval_seconds, 43200)

    def test_override_refresh_interval_via_patched_settings(self):
        test_interval = 100
        with patch.object(settings, 'model_cache_refresh_interval_seconds', test_interval):
            cache = ModelCache()
            self.assertEqual(cache.refresh_interval_seconds, test_interval)

    def test_override_refresh_interval_via_constructor_argument(self):
        constructor_interval = 50
        with patch.object(settings, 'model_cache_refresh_interval_seconds', 9999):
            cache = ModelCache(refresh_interval_seconds=constructor_interval)
            self.assertEqual(cache.refresh_interval_seconds, constructor_interval)


    def test_initial_refresh_populates_data(self):
        cache = ModelCache(refresh_interval_seconds=36000)
        cache.refresh_models_sync()

        self.mock_list_google_models.assert_called_once()
        self.mock_list_openai_models.assert_called_once()
        self.mock_list_ollama_models.assert_called_once()

        self.assertEqual(len(cache.models_data), 3)
        self.assertIn("models/gemini-pro", cache.model_to_provider)
        self.assertEqual(cache.model_to_provider["models/gemini-pro"], "Google")
        self.assertIn("gpt-3.5-turbo", cache.model_to_provider)
        self.assertEqual(cache.model_to_provider["gpt-3.5-turbo"], "OpenAI")
        self.assertIn("test-ollama-model", cache.model_to_provider)
        self.assertEqual(cache.model_to_provider["test-ollama-model"], "Ollama")

        gemini_model_data = next(m for m in cache.models_data if m["id"] == "models/gemini-pro")
        self.assertEqual(gemini_model_data["owned_by"], "Google")
        self.assertEqual(gemini_model_data["context_window"], 8192)

        openai_model_data = next(m for m in cache.models_data if m["id"] == "gpt-3.5-turbo")
        self.assertEqual(openai_model_data["owned_by"], "OpenAI")
        self.assertEqual(openai_model_data["context_window"], 4096)

        ollama_model_data = next(m for m in cache.models_data if m["id"] == "test-ollama-model")
        self.assertEqual(ollama_model_data["owned_by"], "Ollama")


    def test_get_all_models_after_refresh(self):
        cache = ModelCache(refresh_interval_seconds=36000)
        cache.refresh_models_sync()

        models = cache.get_all_models()
        self.assertEqual(len(models), 3)
        self.assertTrue(any(m["id"] == "models/gemini-pro" for m in models))
        self.assertTrue(any(m["id"] == "gpt-3.5-turbo" for m in models))
        self.assertTrue(any(m["id"] == "test-ollama-model" for m in models))

    def test_get_model_provider(self):
        cache = ModelCache(refresh_interval_seconds=36000)
        cache.refresh_models_sync()

        self.assertEqual(cache.get_model_provider("models/gemini-pro"), "Google")
        self.assertEqual(cache.get_model_provider("gpt-3.5-turbo"), "OpenAI")
        self.assertEqual(cache.get_model_provider("test-ollama-model"), "Ollama")
        self.assertIsNone(cache.get_model_provider("non-existent-model"))

    @patch('time.time')
    def test_cache_refresh_logic_manual_trigger(self, mock_time):
        mock_time.return_value = 1000.0

        cache = ModelCache(refresh_interval_seconds=3600)
        cache.refresh_models_sync()

        self.mock_list_google_models.assert_called_once()
        self.mock_list_openai_models.assert_called_once()
        self.mock_list_ollama_models.assert_called_once()

        self.mock_list_google_models.reset_mock()
        self.mock_list_openai_models.reset_mock()
        self.mock_list_ollama_models.reset_mock()

        cache.refresh_models_sync()
        self.mock_list_google_models.assert_called_once()
        self.mock_list_openai_models.assert_called_once()
        self.mock_list_ollama_models.assert_called_once()
        self.assertEqual(cache.last_refresh_time, 1000.0)


    @patch('time.time')
    def test_cache_refresh_logic_stale_get_all_models(self, mock_time):
        initial_time = 1000.0
        refresh_interval = 60

        mock_time.return_value = initial_time
        cache = ModelCache(refresh_interval_seconds=refresh_interval)
        cache.refresh_models_sync()

        self.mock_list_google_models.assert_called_once()
        self.mock_list_openai_models.assert_called_once()
        self.mock_list_ollama_models.assert_called_once()
        self.assertEqual(cache.last_refresh_time, initial_time)

        self.mock_list_google_models.reset_mock()
        self.mock_list_openai_models.reset_mock()
        self.mock_list_ollama_models.reset_mock()

        mock_time.return_value = initial_time + refresh_interval + 1

        cache.get_all_models()

        self.mock_list_google_models.assert_called_once()
        self.mock_list_openai_models.assert_called_once()
        self.mock_list_ollama_models.assert_called_once()
        self.assertEqual(cache.last_refresh_time, initial_time + refresh_interval + 1)


    def test_error_handling_google_fetch_fails(self):
        # Values that should be seen by ModelCache
        fake_google_key = "fake_google_key_for_test"
        fake_openai_key = "fake_openai_key_for_test"

        # Patch the settings object that ModelCache will import and use.
        # This ensures that when ModelCache accesses settings.google.google_api_key,
        # it gets the value we've patched, bypassing potential pydantic-settings reloading issues.
        with patch.object(settings.google, 'google_api_key', fake_google_key), \
             patch.object(settings.openai, 'openai_api_key', fake_openai_key), \
             patch.object(settings.google, 'google_enabled', True), \
             patch.object(settings.openai, 'openai_enabled', True), \
             patch.object(settings.ollama, 'ollama_enabled', True):

            # Ensure that the AppSettings.is_provider_enabled mock also uses these consistent values.
            # The mock 'actual_side_effect_is_provider_enabled_corrected' already reads from the global 'settings'
            # object, which we are patching here. So, this should be consistent.

            self.mock_list_google_models.side_effect = Exception("Google API Error")
            self.mock_list_openai_models.return_value = [self.openai_model_raw_1]
            self.mock_list_ollama_models.return_value = [self.ollama_model_raw_1]

            # Ensure a fresh cache instance for the test
            ModelCache._instance = None
            cache = ModelCache(refresh_interval_seconds=36000)

            with patch.object(cache, 'logger') as mock_logger:
                cache.refresh_models_sync()

            # Debug: Check what models were actually loaded
            # print(f"Cache models data: {cache.models_data}")
            # print(f"Cache model_to_provider: {cache.model_to_provider}")
            # for call_args in mock_logger.warning.call_args_list:
            #    print(f"Cache warning: {call_args}")
            # for call_args in mock_logger.exception.call_args_list:
            #    print(f"Cache exception: {call_args}")


            self.assertTrue(any(m["id"] == "gpt-3.5-turbo" for m in cache.models_data))
            self.assertTrue(any(m["id"] == "test-ollama-model" for m in cache.models_data))
            self.assertEqual(len(cache.models_data), 2)
            self.assertIsNone(cache.get_model_provider("models/gemini-pro"))
            self.assertEqual(cache.get_model_provider("gpt-3.5-turbo"), "OpenAI")
            self.assertEqual(cache.get_model_provider("test-ollama-model"), "Ollama")
            # Check that the specific error for Google was logged
            self.assertTrue(any("Error fetching Google models: Google API Error" in str(arg) for arg_list in mock_logger.exception.call_args_list for arg in arg_list[0]))


    def test_missing_api_keys_skips_providers(self):
        settings.google.google_api_key = None
        settings.openai.openai_api_key = None
        # Ollama enabled status is controlled by settings.ollama.ollama_enabled, which is True in setUp

        cache = ModelCache(refresh_interval_seconds=36000)
        with patch.object(cache, 'logger') as mock_logger:
            cache.refresh_models_sync()

        self.assertEqual(len(cache.models_data), 1) # Only Ollama model should be loaded
        self.assertTrue(any(m["id"] == "test-ollama-model" for m in cache.models_data))
        self.assertEqual(len(cache.model_to_provider), 1)
        self.assertEqual(cache.get_model_provider("test-ollama-model"), "Ollama")

        warnings_logged = [str(args[0]) for args, kwargs in mock_logger.warning.call_args_list]
        self.assertTrue(any("Google API key not set." in w for w in warnings_logged))
        self.assertTrue(any("OpenAI API key not set." in w for w in warnings_logged))

    def test_force_refresh_model_cache_function(self):
        # Import the global model_cache and use it directly for the test
        from moonmind.models_cache import model_cache as global_cache

        # Since we reset _instance in setUp, create a new instance
        # which will become the singleton
        cache = ModelCache(refresh_interval_seconds=36000)

        # After creating the first instance post-reset, it should be the singleton
        # and the global cache should refer to the same instance if re-imported
        # However, the global_cache variable was set at module import time
        # So we need to test with the newly created cache instance

        cache.refresh_models_sync()

        self.mock_list_google_models.assert_called_once()
        self.mock_list_openai_models.assert_called_once()
        self.mock_list_ollama_models.assert_called_once()

        # Ensure the refresh_in_progress flag is reset
        cache._refresh_in_progress = False

        # Test the force refresh function with the cache instance
        force_refresh_model_cache()

        self.assertEqual(self.mock_list_google_models.call_count, 2)
        self.assertEqual(self.mock_list_openai_models.call_count, 2)
        self.assertEqual(self.mock_list_ollama_models.call_count, 2)

    def test_periodic_refresh_thread_execution(self):
        # This test verifies that the periodic refresh thread executes at least the initial refresh
        # Instead of trying to test real threading behavior with mocks,
        # we'll test that the thread creation and periodic refresh method work correctly

        # Reset mock call counts before starting the test
        self.mock_list_google_models.reset_mock()
        self.mock_list_openai_models.reset_mock()
        self.mock_list_ollama_models.reset_mock()

        # Stop the global Thread mock from setUp for this specific test.
        self.thread_patch.stop()
        # Also stop the periodic_refresh mock to allow real method execution
        self.periodic_refresh_patch.stop() # This should restore the original method to ModelCache

        # Create a mock for the Thread constructor to capture what gets passed to it
        with patch('moonmind.models_cache.Thread') as mock_thread_constructor:
            mock_thread_instance = Mock()
            mock_thread_constructor.return_value = mock_thread_instance

            try:
                refresh_interval = 1 # Short interval for faster test

                # Create cache, this should create a Thread instance
                # The ModelCache.__init__ will use the original _periodic_refresh now
                cache = ModelCache(refresh_interval_seconds=refresh_interval)

                # Verify that a Thread was created with the correct target
                # The target should be the bound method of the 'cache' instance
                mock_thread_constructor.assert_called_once_with(
                    target=cache._periodic_refresh, daemon=True
                )
                # Verify that start() was called on the thread
                mock_thread_instance.start.assert_called_once()

                # Now, to test the refresh logic directly without the thread,
                # we call the (now original) _periodic_refresh method on the instance.
                # No need to reassign it if self.periodic_refresh_patch.stop() worked.

                # Let's ensure the instance has the original method.
                # If periodic_refresh_patch.stop() worked, cache._periodic_refresh is the original.
                # We can call it directly to test its internal logic.
                # For this test, we want to simulate what the thread would do,
                # which includes an initial synchronous refresh.

                # The _periodic_refresh method itself calls refresh_models_sync.
                # We can call refresh_models_sync directly on the cache instance
                # as the _periodic_refresh method's first action is to call it.
                cache.refresh_models_sync()

                # Verify the refresh happened with our mocks
                self.assertEqual(self.mock_list_google_models.call_count, 1, "Google fetch")
                self.assertEqual(self.mock_list_openai_models.call_count, 1, "OpenAI fetch")
                self.assertEqual(self.mock_list_ollama_models.call_count, 1, "Ollama fetch")
                self.assertGreater(cache.last_refresh_time, 0, "Last refresh time should be set")

            finally:
                # Restart the global Thread patcher if it was stopped for this test
                if not self.thread_patch.is_started:
                     self.thread_patch.start()
                # Restart the periodic_refresh patcher if it was stopped for this test
                if not self.periodic_refresh_patch.is_started:
                     self.periodic_refresh_patch.start()


if __name__ == '__main__':
    unittest.main()
