import logging
import time
import asyncio
from threading import Lock, Thread
from typing import Any, Dict, List, Optional, Tuple

from moonmind.factories.google_factory import list_google_models
from moonmind.factories.openai_factory import list_openai_models
from moonmind.factories.ollama_factory import list_ollama_models
from moonmind.factories.anthropic_factory import AnthropicFactory # Assuming a similar listing function or direct model add
from moonmind.config.settings import settings

logger = logging.getLogger(__name__)

class ModelCache:
    _instance = None
    _lock = Lock()

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            with cls._lock:
                if not cls._instance:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, refresh_interval_seconds: Optional[int] = None):
        # Ensure __init__ is only run once for the singleton
        if hasattr(self, '_initialized') and self._initialized:
            return
        with self._lock:
            if hasattr(self, '_initialized') and self._initialized:
                return

            self.models_data: List[Dict[str, Any]] = []
            self.model_to_provider: Dict[str, str] = {}
            self.last_refresh_time: float = 0
            # Use the value from settings if no specific interval is passed during instantiation
            self.refresh_interval_seconds: int = refresh_interval_seconds if refresh_interval_seconds is not None else settings.model_cache_refresh_interval_seconds
            self._initialized: bool = True
            self._refresh_operation_lock = Lock() # New instance lock for refresh operations
            self.logger = logger # Assign module-level logger to instance attribute
            self._refresh_thread = Thread(target=self._periodic_refresh, daemon=True)
            self._refresh_in_progress = False # Flag to prevent concurrent refreshes

            # self.logger.info("ModelCache initialized. Starting refresh thread.") # Commented out
            self._refresh_thread.start()

    def _fetch_all_models(self) -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
        self.logger.info("Attempting to fetch all models for cache refresh.")
        all_models_data = []
        model_to_provider_map = {}

        # Fetch Google Models
        try:
            if settings.is_provider_enabled("google"):
                google_models_list = list(list_google_models()) # Ensure it's a list
                self.logger.info(f"Fetched {len(google_models_list)} raw Google models.")
                for model in google_models_list: # Iterate over the list
                    context_window = model.input_token_limit
                    if context_window is None: # Default context window
                        context_window = 1024 if 'embedContent' in model.supported_generation_methods else 8192

                    capabilities = {
                        "chat_completion": 'generateContent' in model.supported_generation_methods,
                        "text_completion": 'generateContent' in model.supported_generation_methods,
                        "embedding": 'embedContent' in model.supported_generation_methods,
                    }
                    model_entry = {
                        "id": model.name, "object": "model", "created": int(time.time()),
                        "owned_by": "Google", "permission": [], "root": model.name, "parent": None,
                        "context_window": context_window, "capabilities": capabilities,
                    }
                    all_models_data.append(model_entry)
                    model_to_provider_map[model.name] = "Google"
            else:
                if not settings.google.google_enabled:
                    self.logger.info("Google provider is disabled.")
                else:
                    self.logger.warning("Google API key not set. Skipping Google models.")
        except Exception as e:
            self.logger.exception(f"Error fetching Google models: {e}")

        # Fetch OpenAI Models
        try:
            if settings.is_provider_enabled("openai"):
                openai_models_raw = list_openai_models() # This should return a list of model objects/dicts
                self.logger.info(f"Fetched {len(openai_models_raw)} raw OpenAI models.")
                for model in openai_models_raw: # Assuming model is an object with an 'id' attribute
                    model_id = model.id
                    # Determine context window (these are common defaults, might need adjustment)
                    if "gpt-4" in model_id: # Covers gpt-4, gpt-4-32k etc.
                        context_window = 8192
                        if "32k" in model_id: context_window = 32768
                        if "turbo-2024-04-09" in model_id or "128k" in model_id : context_window = 128000
                    elif "gpt-3.5-turbo" in model_id:
                        context_window = 4096
                        if "16k" in model_id: context_window = 16384
                    else: # Default for other OpenAI models
                        context_window = 4096

                    capabilities = { # Assume chat models are for chat/text completion
                        "chat_completion": True, "text_completion": True, "embedding": "embedding" in model_id,
                    }
                    model_entry = {
                        "id": model_id, "object": "model", "created": int(getattr(model, 'created', time.time())),
                        "owned_by": "OpenAI", "permission": [], "root": model_id, "parent": None,
                        "context_window": context_window, "capabilities": capabilities,
                    }
                    all_models_data.append(model_entry)
                    model_to_provider_map[model_id] = "OpenAI"
            else:
                if not settings.openai.openai_enabled:
                    self.logger.info("OpenAI provider is disabled.")
                else:
                    self.logger.warning("OpenAI API key not set. Skipping OpenAI models.")
        except Exception as e:
            self.logger.exception(f"Error fetching OpenAI models: {e}")

        # Fetch Ollama Models
        try:
            if settings.is_provider_enabled("ollama"):
                ollama_models_raw = asyncio.run(list_ollama_models())
                self.logger.info(f"Fetched {len(ollama_models_raw)} raw Ollama models.")
                for model in ollama_models_raw:
                    model_name = model.get("name", "")
                    if not model_name:
                        continue

                    # Ollama models typically have flexible context windows, defaulting to 8192
                    context_window = 8192

                    # Assume all Ollama models support chat completion and text completion
                    capabilities = {
                        "chat_completion": True,
                        "text_completion": True,
                        "embedding": False,  # Most chat models don't do embeddings
                    }

                    model_entry = {
                        "id": model_name,
                        "object": "model",
                        "created": int(time.time()),
                        "owned_by": "Ollama",
                        "permission": [],
                        "root": model_name,
                        "parent": None,
                        "context_window": context_window,
                        "capabilities": capabilities,
                    }
                    all_models_data.append(model_entry)
                    model_to_provider_map[model_name] = "Ollama"
            else:
                if not settings.ollama.ollama_enabled:
                    self.logger.info("Ollama provider is disabled.")
                else:
                    self.logger.info("Ollama provider is enabled but may not be available.")
        except Exception as e:
            self.logger.exception(f"Error fetching Ollama models: {e}")

        # Fetch Anthropic Models
        # Anthropic SDK does not have a public "list_models" function like OpenAI.
        # Models are typically known and specified directly.
        # We will add the configured Anthropic model if the provider is enabled.
        try:
            if settings.is_provider_enabled("anthropic"):
                # Using the model name from settings directly
                anthropic_model_name = settings.anthropic.anthropic_chat_model
                if anthropic_model_name:
                    # Determine context window
                    context_window = 200000  # Default for current Anthropic models
                    if anthropic_model_name == "claude-opus-4-20250514":
                        context_window = 200000
                    elif anthropic_model_name == "claude-sonnet-4-20250514":
                        context_window = 200000
                    elif anthropic_model_name == "claude-3-5-haiku-20241022":
                        context_window = 200000
                    # Older models for reference, though we are focusing on the latest
                    elif "claude-2.1" in anthropic_model_name: # Older model
                        context_window = 200000
                    elif "claude-2.0" in anthropic_model_name: # Older model
                        context_window = 100000
                    # Add more specific model context windows if needed

                    capabilities = {
                        "chat_completion": True,
                        "text_completion": True, # Anthropic models are generally good for this too
                        "embedding": False, # Anthropic models are not for embeddings
                    }
                    model_entry = {
                        "id": anthropic_model_name,
                        "object": "model",
                        "created": int(time.time()), # Placeholder, Anthropic models don't have a creation timestamp via API
                        "owned_by": "Anthropic",
                        "permission": [],
                        "root": anthropic_model_name,
                        "parent": None,
                        "context_window": context_window,
                        "capabilities": capabilities,
                    }
                    all_models_data.append(model_entry)
                    model_to_provider_map[anthropic_model_name] = "Anthropic"
                    self.logger.info(f"Added configured Anthropic model: {anthropic_model_name}")
            else:
                if not settings.anthropic.anthropic_enabled:
                    self.logger.info("Anthropic provider is disabled.")
                else:
                    self.logger.warning("Anthropic API key not set. Skipping Anthropic models.")
        except Exception as e:
            self.logger.exception(f"Error adding Anthropic model: {e}")


        self.logger.info(f"Total models fetched: {len(all_models_data)}. Model to provider map size: {len(model_to_provider_map)}")

        # Sort models to bring the default chat model to the top
        try:
            default_chat_model_id = settings.get_default_chat_model()
            default_provider_name = settings.default_chat_provider.capitalize() # Capitalize to match "owned_by"

            self.logger.info(f"Default chat provider: {default_provider_name}, Default chat model: {default_chat_model_id}")

            # Find the default model entry
            default_model_entry = None
            for i, model_entry in enumerate(all_models_data):
                # Check if the model ID matches and if it belongs to the default provider
                # The model ID in all_models_data for Google is like "models/gemini-1.5-flash-latest"
                # but settings.get_default_chat_model() might return "gemini-1.5-flash-latest"
                # So we need to handle this potential mismatch.
                # For Google, model.name is "models/gemini..."
                # For OpenAI, model.id is "gpt-..."
                # For Ollama, model_name is "llama2:latest"
                # For Anthropic, anthropic_model_name is "claude..."

                # We need to ensure the comparison is fair.
                # The `id` in `model_entry` is what the UI will see and use.
                # `settings.get_default_chat_model()` returns the model ID as configured.

                # Let's refine the check based on how IDs are stored.
                # For Google, the settings.google.google_chat_model is the short form (e.g. "gemini-1.5-pro-latest")
                # but the model_entry["id"] is "models/gemini-1.5-pro-latest".
                # So, if the provider is Google, we might need to prepend "models/" to the default_chat_model_id for comparison.

                current_model_id = model_entry.get("id")
                current_model_owner = model_entry.get("owned_by")

                is_default_model = False
                if default_provider_name == "Google" and current_model_owner == "Google":
                    # Google models in cache have "models/" prefix, setting might not.
                    if default_chat_model_id.startswith("models/"):
                        is_default_model = (current_model_id == default_chat_model_id)
                    else:
                        is_default_model = (current_model_id == f"models/{default_chat_model_id}")
                elif current_model_owner == default_provider_name: # For OpenAI, Ollama, Anthropic
                    is_default_model = (current_model_id == default_chat_model_id)


                if is_default_model:
                    default_model_entry = all_models_data.pop(i)
                    self.logger.info(f"Found default model '{default_chat_model_id}' from provider '{default_provider_name}' and moved it to the top.")
                    break

            if default_model_entry:
                all_models_data.insert(0, default_model_entry)
            else:
                self.logger.warning(f"Default model '{default_chat_model_id}' from provider '{default_provider_name}' not found in the fetched models list.")

        except Exception as e:
            self.logger.exception(f"Error while trying to prioritize default model: {e}")

        return all_models_data, model_to_provider_map

    def refresh_models_sync(self):
        with self._refresh_operation_lock: # Changed to use new instance lock
            if self._refresh_in_progress:
                self.logger.info("Refresh already in progress. Skipping.")
                return
            self._refresh_in_progress = True

        self.logger.info("Starting synchronous model cache refresh.")
        try:
            self.models_data, self.model_to_provider = self._fetch_all_models()
            self.last_refresh_time = time.time()
            self.logger.info(f"Model cache refreshed. {len(self.models_data)} models loaded. Last refresh: {self.last_refresh_time}")
        except Exception as e:
            self.logger.error(f"Failed to refresh model cache: {e}")
        finally:
            with self._refresh_operation_lock: # Changed to use new instance lock
                self._refresh_in_progress = False

    def _periodic_refresh(self):
        # Initial refresh immediately after thread starts
        self.refresh_models_sync()
        while True:
            time_since_last_refresh = time.time() - self.last_refresh_time
            if time_since_last_refresh >= self.refresh_interval_seconds:
                self.logger.info(f"Scheduled refresh interval reached ({self.refresh_interval_seconds}s). Refreshing models.")
                self.refresh_models_sync()            # Sleep for a short duration before checking again.
            # This determines how frequently the thread wakes up to check if a refresh is needed
            # and also allows the thread to respond to shutdown signals more gracefully
            # rather than sleeping for the entire refresh_interval_seconds.
            time.sleep(min(60, self.refresh_interval_seconds / 10 if self.refresh_interval_seconds > 0 else 60))

    def get_all_models(self) -> List[Dict[str, Any]]:
        if not self.models_data or (time.time() - self.last_refresh_time > self.refresh_interval_seconds): # also check if cache is empty
            self.logger.info("Models data is empty or stale, attempting synchronous refresh before returning.")
            self.refresh_models_sync()
        return self.models_data

    def get_model_provider(self, model_id: str) -> Optional[str]:
        if not self.model_to_provider or (time.time() - self.last_refresh_time > self.refresh_interval_seconds): # also check if cache is empty
            self.logger.info("Model to provider map is empty or stale, attempting synchronous refresh.")
            self.refresh_models_sync()
        return self.model_to_provider.get(model_id)

    def get_model_details(self, model_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves detailed information for a specific model."""
        if not self.models_data or (time.time() - self.last_refresh_time > self.refresh_interval_seconds):
            self.logger.info("Models data is empty or stale, attempting synchronous refresh before returning details.")
            self.refresh_models_sync()
        for model in self.models_data:
            if model.get("id") == model_id:
                return model
        return None

# Global instance of ModelCache
# Initialize with the refresh interval from settings
model_cache = ModelCache(refresh_interval_seconds=settings.model_cache_refresh_interval_seconds)

def force_refresh_model_cache():
    """Utility function to manually trigger a cache refresh."""
    logger.info("Force refresh of model cache requested.")
    model_cache.refresh_models_sync()