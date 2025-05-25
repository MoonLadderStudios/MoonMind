import logging
import time
import unittest
from unittest.mock import patch, MagicMock, call, AsyncMock

from moonmind.models_cache import ModelCache, force_refresh_model_cache
from moonmind.config import settings # To mock API keys
from moonmind.config.settings import AppSettings # Import AppSettings class for patching

# Configure basic logging for tests to see warnings/errors if needed
logging.basicConfig(level=logging.INFO)

class TestModelCache(unittest.TestCase):

    def setUp(self):
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
        self.mock_google_models_patch = patch('moonmind.factories.google_factory.list_google_models')
        self.mock_openai_models_patch = patch('moonmind.factories.openai_factory.list_openai_models')
        self.mock_ollama_models_patch = patch('moonmind.factories.ollama_factory.list_ollama_models', new_callable=AsyncMock)
        
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
        self.mock_list_openai_models.return_value = [self.openai_model_raw_1]
        
        self.ollama_model_raw_1 = {"name": "test-ollama-model", "details": {"parameter_size": "7B"}}
        self.mock_list_ollama_models.return_value = [self.ollama_model_raw_1]

        # Patch AppSettings.is_provider_enabled to use actual (potentially patched) settings
        self.mock_is_provider_enabled_patch = patch.object(AppSettings, 'is_provider_enabled')
        self.mock_is_provider_enabled_method = self.mock_is_provider_enabled_patch.start()
        
        def actual_side_effect_is_provider_enabled(instance_self, provider_name_str):
            provider_name_str = provider_name_str.lower()
            if provider_name_str == "google":
                return instance_self.google.google_enabled and bool(instance_self.google.google_api_key)
            elif provider_name_str == "openai":
                return instance_self.openai.openai_enabled and bool(instance_self.openai.openai_api_key)
            elif provider_name_str == "ollama":
                return instance_self.ollama.ollama_enabled
            return False
        self.mock_is_provider_enabled_method.side_effect = actual_side_effect_is_provider_enabled
        
        # Prevent actual thread creation for most tests by mocking threading.Thread
        self.thread_patch = patch('threading.Thread')
        mock_thread_class = self.thread_patch.start() 
        self.mock_thread_instance = MagicMock()
        self.mock_thread_instance.start = MagicMock() 
        mock_thread_class.return_value = self.mock_thread_instance


    def tearDown(self):
        self.mock_google_models_patch.stop()
        self.mock_openai_models_patch.stop()
        self.mock_ollama_models_patch.stop()
        self.mock_is_provider_enabled_patch.stop()
        self.thread_patch.stop()
        patch.stopall() 

        settings.google.google_api_key = self.original_google_api_key
        settings.google.google_enabled = self.original_google_enabled
        settings.openai.openai_api_key = self.original_openai_api_key
        settings.openai.openai_enabled = self.original_openai_enabled
        settings.ollama.ollama_enabled = self.original_ollama_enabled
        settings.model_cache_refresh_interval_seconds = self.original_refresh_interval
        
        ModelCache._instance = None


    def test_singleton_behavior(self): 
        cache1 = ModelCache(refresh_interval_seconds=1000) 
        cache2 = ModelCache(refresh_interval_seconds=1000)
        self.assertIs(cache1, cache2)
        self.mock_thread_instance.start.assert_called_once() 


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
        self.mock_list_google_models.side_effect = Exception("Google API Error")
        # Ensure other providers are still processed
        self.mock_list_openai_models.return_value = [self.openai_model_raw_1]
        self.mock_list_ollama_models.return_value = [self.ollama_model_raw_1]

        cache = ModelCache(refresh_interval_seconds=36000)
        with patch.object(cache, 'logger') as mock_logger: 
             cache.refresh_models_sync() 

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
        cache = ModelCache(refresh_interval_seconds=36000)
        cache.refresh_models_sync() 

        self.mock_list_google_models.assert_called_once()
        self.mock_list_openai_models.assert_called_once()
        self.mock_list_ollama_models.assert_called_once()

        force_refresh_model_cache() 

        self.assertEqual(self.mock_list_google_models.call_count, 2)
        self.assertEqual(self.mock_list_openai_models.call_count, 2)
        self.assertEqual(self.mock_list_ollama_models.call_count, 2)

    @patch('time.sleep', MagicMock()) # Make time.sleep do nothing for faster execution
    @patch('time.time')
    def test_periodic_refresh_thread_execution(self, mock_time):
        # This test allows the real thread to run but controls its timing.
        # Stop the global Thread mock from setUp for this specific test.
        self.thread_patch.stop() 
        try:
            initial_time = 1000.0
            refresh_interval = 60 # seconds
            mock_time.return_value = initial_time
            
            # Create cache, this starts the _periodic_refresh thread with real Thread.
            cache = ModelCache(refresh_interval_seconds=refresh_interval)
            
            # Wait for the initial refresh to complete.
            # The initial refresh is done synchronously by the thread before it loops.
            # We need to ensure it has run.
            # Check call counts after giving the thread a moment to run its first refresh.
            # This can be a bit flaky without proper thread synchronization.
            # A short sleep might be needed if assertions fail due to thread not completing initial refresh.
            # However, the design of _periodic_refresh calls refresh_models_sync() before the loop.
            
            # Assertions for the initial refresh (should have happened once)
            # Give a very brief moment for thread to start and run initial sync
            for _ in range(10): # Try a few times for initial refresh
                if self.mock_list_google_models.call_count >= 1: break
                time.sleep(0.01) # Short sleep
            
            self.assertEqual(self.mock_list_google_models.call_count, 1, "Initial Google fetch")
            self.assertEqual(self.mock_list_openai_models.call_count, 1, "Initial OpenAI fetch")
            self.assertEqual(self.mock_list_ollama_models.call_count, 1, "Initial Ollama fetch")
            self.assertEqual(cache.last_refresh_time, initial_time)

            # Simulate time passing for the next scheduled refresh
            mock_time.return_value = initial_time + refresh_interval + 1.0
            
            # Wait for the periodic refresh to complete.
            # The thread's sleep is mocked by @patch('time.sleep', MagicMock()) at method level.
            # So the loop should execute quickly once time advances.
            for _ in range(10): # Try a few times for periodic refresh
                if self.mock_list_google_models.call_count >= 2: break
                time.sleep(0.01) # Short sleep

            self.assertGreaterEqual(self.mock_list_google_models.call_count, 2, "Periodic Google refresh")
            self.assertGreaterEqual(self.mock_list_openai_models.call_count, 2, "Periodic OpenAI refresh")
            self.assertGreaterEqual(self.mock_list_ollama_models.call_count, 2, "Periodic Ollama refresh")
            self.assertEqual(cache.last_refresh_time, initial_time + refresh_interval + 1.0)
        finally:
            # Restart the global Thread patcher if it was stopped for this test
            if not self.thread_patch.is_started:
                 self.thread_patch.start()


if __name__ == '__main__':
    unittest.main()
