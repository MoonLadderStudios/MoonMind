import logging
import time
import unittest
from unittest.mock import patch, MagicMock, call

from moonmind.models_cache import ModelCache, force_refresh_model_cache
from moonmind.config import settings # To mock API keys

# Configure basic logging for tests to see warnings/errors if needed
logging.basicConfig(level=logging.INFO)
# Reduce log spam from dependencies if necessary
# logging.getLogger("httpx").setLevel(logging.WARNING)


class TestModelCache(unittest.TestCase):

    def setUp(self):
        # Ensure each test gets a fresh ModelCache instance for some tests,
        # or reset the existing singleton carefully.
        # Forcing a new instance for some tests might be easier if state is complex.
        # ModelCache._instance = None # This would force re-initialization.
        # Be cautious with this approach if other parts of the system rely on the singleton's persistence.
        # A better way for testing might be to get the instance and then clear its state.
        
        # Store original settings
        self.original_google_api_key = settings.google.google_api_key
        self.original_openai_api_key = settings.openai.openai_api_key
        self.original_refresh_interval = settings.model_cache_refresh_interval if hasattr(settings, 'model_cache_refresh_interval') else 3600

        # Mock API keys for most tests (can be overridden in specific tests)
        settings.google.google_api_key = "fake_google_key"
        settings.openai.openai_api_key = "fake_openai_key"
        # Set a short refresh interval for testing, or control time via mocks
        # For these tests, we'll often trigger refreshes manually or via time mocking.
        
        # Mock the factory functions used by the cache
        self.mock_google_models_patch = patch('moonmind.factories.google_factory.list_google_models')
        self.mock_openai_models_patch = patch('moonmind.factories.openai_factory.list_openai_models')
        
        self.mock_list_google_models = self.mock_google_models_patch.start()
        self.mock_list_openai_models = self.mock_openai_models_patch.start()

        # Define default mock return values
        self.google_model_raw_1 = MagicMock(name="gemini-pro-raw")
        self.google_model_raw_1.name = "models/gemini-pro"
        self.google_model_raw_1.input_token_limit = 8192
        self.google_model_raw_1.supported_generation_methods = ['generateContent']

        self.openai_model_raw_1 = MagicMock(name="gpt-3.5-turbo-raw")
        self.openai_model_raw_1.id = "gpt-3.5-turbo"
        self.openai_model_raw_1.created = int(time.time()) - 1000 # Example created time

        self.mock_list_google_models.return_value = [self.google_model_raw_1]
        self.mock_list_openai_models.return_value = [self.openai_model_raw_1]

        # Reset the singleton instance to ensure a clean state for each test
        # This is crucial because the ModelCache is a singleton and its state persists across tests.
        if ModelCache._instance:
            # Stop the existing refresh thread if it's running
            if hasattr(ModelCache._instance, '_refresh_thread') and ModelCache._instance._refresh_thread.is_alive():
                # This is tricky; daemon threads might not be easily stoppable.
                # For testing, it's better to not start the thread or mock its behavior.
                # Or, re-initialize the cache with a very long interval and mock time.
                # A simple approach for now, assuming tests are quick enough not to overlap badly.
                pass # Cannot reliably stop daemon threads, rely on re-patching for new instances.
        ModelCache._instance = None # Forces re-creation on next ModelCache() call
        # Also ensure the settings object itself is restored if tests modify its attributes directly
        # For pydantic BaseSettings, this usually means ensuring env vars are clean or re-evaluating `settings = Settings()`
        # For this test suite, direct patching of settings attributes is restored in tearDown.


    def tearDown(self):
        self.mock_google_models_patch.stop()
        self.mock_openai_models_patch.stop()
        patch.stopall() # Stop any other patches that might have been started

        # Restore original settings by direct assignment
        settings.google.google_api_key = self.original_google_api_key
        settings.openai.openai_api_key = self.original_openai_api_key
        # Ensure this attribute exists on settings before trying to set it.
        # The settings object is a module global, so changes persist if not restored.
        settings.model_cache_refresh_interval_seconds = self.original_refresh_interval
        
        # Clean up the singleton instance to prevent state leakage between tests
        ModelCache._instance = None


    @patch.object(ModelCache, '_periodic_refresh', MagicMock()) # Prevent thread start
    def test_singleton_behavior(self, mock_periodic_refresh_disabled):
        cache1 = ModelCache(refresh_interval_seconds=1000) 
        cache2 = ModelCache(refresh_interval_seconds=1000)
        self.assertIs(cache1, cache2)
        # Ensure _periodic_refresh (and thus the thread) wasn't called due to the outer patch
        mock_periodic_refresh_disabled.assert_not_called() 


    @patch.object(ModelCache, '_periodic_refresh', MagicMock()) # Prevent thread start for this specific test too
    def test_default_refresh_interval_from_settings(self, mock_periodic_refresh_disabled):
        # Ensure settings.model_cache_refresh_interval_seconds has its default or a known value
        # The actual default is 43200, set in settings.py
        # Here, we explicitly patch it to ensure the test condition.
        with patch.object(settings, 'model_cache_refresh_interval_seconds', 43200):
            # ModelCache._instance is None due to setUp
            cache = ModelCache() # Instantiate without arguments
            self.assertEqual(cache.refresh_interval_seconds, 43200)

    @patch.object(ModelCache, '_periodic_refresh', MagicMock())
    def test_override_refresh_interval_via_patched_settings(self, mock_periodic_refresh_disabled):
        test_interval = 100
        # Patch the settings value that ModelCache constructor will read
        with patch.object(settings, 'model_cache_refresh_interval_seconds', test_interval):
            # ModelCache._instance is None due to setUp
            cache = ModelCache() # Instantiate without arguments
            self.assertEqual(cache.refresh_interval_seconds, test_interval)

    @patch.object(ModelCache, '_periodic_refresh', MagicMock())
    def test_override_refresh_interval_via_constructor_argument(self, mock_periodic_refresh_disabled):
        constructor_interval = 50
        # For this test, set settings.model_cache_refresh_interval_seconds to something different
        # to ensure the constructor argument takes precedence.
        with patch.object(settings, 'model_cache_refresh_interval_seconds', 9999):
            # ModelCache._instance is None due to setUp
            cache = ModelCache(refresh_interval_seconds=constructor_interval)
            self.assertEqual(cache.refresh_interval_seconds, constructor_interval)


    def test_initial_refresh_populates_data(self):
        # This test specifically checks the threaded initial refresh.
        # The ModelCache constructor now starts a thread that calls refresh_models_sync.
        # We need to allow time for this or mock the threading part.
        # For simplicity, let's test refresh_models_sync directly first, then address threading.

        # Create instance (this will trigger initial refresh in its own thread)
        # Set a very high refresh interval to prevent auto-refreshes during the test itself
        cache = ModelCache(refresh_interval_seconds=36000) 
        
        # Wait a moment for the initial refresh thread to complete
        # This is not ideal for unit tests but necessary if testing the threaded behavior directly.
        # A more robust way would be to use an event or condition variable.
        time.sleep(0.5) # Adjust as needed, depends on how fast the refresh runs

        self.mock_list_google_models.assert_called()
        self.mock_list_openai_models.assert_called()
        
        self.assertGreater(len(cache.models_data), 0)
        self.assertIn("models/gemini-pro", cache.model_to_provider)
        self.assertEqual(cache.model_to_provider["models/gemini-pro"], "Google")
        self.assertIn("gpt-3.5-turbo", cache.model_to_provider)
        self.assertEqual(cache.model_to_provider["gpt-3.5-turbo"], "OpenAI")

        # Verify data structure (simplified check)
        gemini_model_data = next(m for m in cache.models_data if m["id"] == "models/gemini-pro")
        self.assertEqual(gemini_model_data["owned_by"], "Google")
        self.assertEqual(gemini_model_data["context_window"], 8192)

        openai_model_data = next(m for m in cache.models_data if m["id"] == "gpt-3.5-turbo")
        self.assertEqual(openai_model_data["owned_by"], "OpenAI")
        self.assertEqual(openai_model_data["context_window"], 4096) # Default for gpt-3.5-turbo


    def test_get_all_models_after_refresh(self):
        cache = ModelCache(refresh_interval_seconds=36000)
        time.sleep(0.5) # Allow initial refresh

        models = cache.get_all_models()
        self.assertEqual(len(models), 2) # gemini-pro and gpt-3.5-turbo
        self.assertTrue(any(m["id"] == "models/gemini-pro" for m in models))
        self.assertTrue(any(m["id"] == "gpt-3.5-turbo" for m in models))

    def test_get_model_provider(self):
        cache = ModelCache(refresh_interval_seconds=36000)
        time.sleep(0.5) # Allow initial refresh

        self.assertEqual(cache.get_model_provider("models/gemini-pro"), "Google")
        self.assertEqual(cache.get_model_provider("gpt-3.5-turbo"), "OpenAI")
        self.assertIsNone(cache.get_model_provider("non-existent-model"))

    @patch('time.time')
    def test_cache_refresh_logic_manual_trigger(self, mock_time):
        # Test manual refresh first, then scheduled refresh
        mock_time.return_value = 1000.0 # Initial time
        
        # Instantiate cache, initial refresh happens (mocked calls clear implicitly by setup/teardown)
        cache = ModelCache(refresh_interval_seconds=3600) # Interval of 1 hour
        time.sleep(0.5) # Allow initial refresh to complete
        
        # Ensure initial calls happened
        self.mock_list_google_models.assert_called_once()
        self.mock_list_openai_models.assert_called_once()
        
        # Reset mocks for the next call count
        self.mock_list_google_models.reset_mock()
        self.mock_list_openai_models.reset_mock()

        # Manually trigger refresh
        cache.refresh_models_sync() 
        self.mock_list_google_models.assert_called_once() # Called again
        self.mock_list_openai_models.assert_called_once() # Called again
        self.assertEqual(cache.last_refresh_time, 1000.0) # Time should be updated by refresh_models_sync


    @patch('time.time')
    @patch.object(ModelCache, '_periodic_refresh') # Stop the thread from running for this test
    def test_cache_refresh_logic_stale_get_all_models(self, mock_periodic_refresh_thread, mock_time):
        initial_time = 1000.0
        refresh_interval = 60 # 1 minute
        
        mock_time.return_value = initial_time
        cache = ModelCache(refresh_interval_seconds=refresh_interval)
        # Manually call refresh_models_sync to simulate initial population without thread sleep
        cache.refresh_models_sync()

        self.mock_list_google_models.assert_called_once()
        self.mock_list_openai_models.assert_called_once()
        self.assertEqual(cache.last_refresh_time, initial_time)

        # Move time forward beyond refresh interval
        mock_time.return_value = initial_time + refresh_interval + 1 
        
        # Accessing models when cache is stale should trigger refresh
        cache.get_all_models()
        
        # Should have been called twice now (initial + stale access)
        self.assertEqual(self.mock_list_google_models.call_count, 2)
        self.assertEqual(self.mock_list_openai_models.call_count, 2)
        self.assertEqual(cache.last_refresh_time, initial_time + refresh_interval + 1)


    @patch.object(ModelCache, '_periodic_refresh') # Stop thread
    def test_error_handling_google_fetch_fails(self, mock_periodic_refresh_thread):
        self.mock_list_google_models.side_effect = Exception("Google API Error")
        # OpenAI should still be loaded
        self.mock_list_openai_models.return_value = [self.openai_model_raw_1]

        cache = ModelCache(refresh_interval_seconds=36000)
        with patch.object(cache, 'logger') as mock_logger: # Mock logger on the instance
             cache.refresh_models_sync() # Manually refresh

        self.assertTrue(any(m["id"] == "gpt-3.5-turbo" for m in cache.models_data))
        self.assertEqual(len(cache.models_data), 1) # Only OpenAI model
        self.assertIsNone(cache.get_model_provider("models/gemini-pro"))
        self.assertEqual(cache.get_model_provider("gpt-3.5-turbo"), "OpenAI")
        
        # Check if error was logged
        self.assertTrue(any("Error fetching Google models" in str(arg) for arg_list in mock_logger.exception.call_args_list for arg in arg_list[0]))


    @patch.object(ModelCache, '_periodic_refresh') # Stop thread
    def test_missing_api_keys_skips_providers(self, mock_periodic_refresh_thread):
        settings.google.google_api_key = None
        settings.openai.openai_api_key = None
        
        cache = ModelCache(refresh_interval_seconds=36000)
        with patch.object(cache, 'logger') as mock_logger: # Mock logger on the instance
            cache.refresh_models_sync()

        self.assertEqual(len(cache.models_data), 0)
        self.assertEqual(len(cache.model_to_provider), 0)
        
        # Check for warnings
        warnings = [str(args[0]) for args, kwargs in mock_logger.warning.call_args_list]
        self.assertTrue(any("Google API key not set" in w for w in warnings))
        self.assertTrue(any("OpenAI API key not set" in w for w in warnings))


    @patch.object(ModelCache, '_periodic_refresh') # Stop thread
    def test_force_refresh_model_cache_function(self, mock_periodic_refresh_thread):
        cache = ModelCache(refresh_interval_seconds=36000)
        cache.refresh_models_sync() # Initial refresh

        self.mock_list_google_models.assert_called_once()
        self.mock_list_openai_models.assert_called_once()

        force_refresh_model_cache() # This should call refresh_models_sync on the instance

        self.assertEqual(self.mock_list_google_models.call_count, 2)
        self.assertEqual(self.mock_list_openai_models.call_count, 2)

    @patch('time.sleep') # Mock time.sleep to speed up periodic refresh test
    @patch('time.time')
    def test_periodic_refresh_thread_execution(self, mock_time, mock_sleep):
        # This test is more complex due to threading.
        # We'll mock time and sleep to control the loop.
        initial_time = 1000.0
        refresh_interval = 60 # seconds
        
        mock_time.return_value = initial_time
        
        # Stop the real thread from starting by patching its target method within the instance
        # We'll call _periodic_refresh manually in a controlled way or not at all if just testing setup.
        # The cache instance is created, which should start the thread.
        # For this specific test, we let the thread run but control its execution via mocks.
        
        # Create cache, this starts the _periodic_refresh thread.
        # The thread itself will call refresh_models_sync once at the beginning.
        cache = ModelCache(refresh_interval_seconds=refresh_interval)
        
        # Wait for the initial refresh to complete (called by the thread's start)
        # Needs a way to confirm this. Let's check call counts after a short delay.
        time.sleep(0.5) # Allow thread to run initial refresh
        self.assertEqual(self.mock_list_google_models.call_count, 1)
        self.assertEqual(self.mock_list_openai_models.call_count, 1)
        self.assertEqual(cache.last_refresh_time, initial_time)

        # Simulate time passing for the next scheduled refresh
        mock_time.return_value = initial_time + refresh_interval + 1.0
        
        # The thread's loop should call refresh_models_sync again.
        # We need to give the thread a chance to wake up and check the time.
        # mock_sleep is called inside the loop. We can make it return immediately.
        mock_sleep.return_value = None 
        
        # To ensure the thread runs its check and refreshes, we might need to wait.
        # This is where testing threads gets tricky.
        # Let's check call counts after another delay.
        # Give it up to a few seconds for the thread to cycle.
        # This is not ideal as it makes tests slow and potentially flaky.
        
        # For a more deterministic test of _periodic_refresh's logic (not the threading itself):
        # 1. Instantiate ModelCache (which starts the thread).
        # 2. Wait for initial refresh.
        # 3. Mock time forward.
        # 4. Call cache._periodic_refresh() directly (if it were not a loop, or test its inner logic).
        #    Since it's a loop, this is hard.

        # Alternative: Use an event set by refresh_models_sync and wait for it in the test.
        # For now, relying on time.sleep and checking call counts.
        
        # Try to wait until the next refresh *should* have occurred
        max_wait_time = 5 # seconds
        wait_interval = 0.1
        elapsed_wait = 0
        while elapsed_wait < max_wait_time:
            if self.mock_list_google_models.call_count >= 2: # Expecting the second call
                break
            time.sleep(wait_interval)
            elapsed_wait += wait_interval
        
        self.assertEqual(self.mock_list_google_models.call_count, 2, "Periodic refresh did not occur as expected.")
        self.assertEqual(self.mock_list_openai_models.call_count, 2, "Periodic refresh did not occur as expected.")
        self.assertEqual(cache.last_refresh_time, initial_time + refresh_interval + 1.0)

        # To stop the thread for cleanup, if it were not a daemon:
        # cache._running = False # Assuming an attribute to control the loop
        # cache._refresh_thread.join()


if __name__ == '__main__':
    unittest.main()
