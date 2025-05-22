from llama_index.core import Settings

from ..config.settings import AppSettings


def build_service_context(settings: AppSettings, embed_model):
    """
    Configure and return global Settings with the provided embedding model.
    """
    # Configure global settings
    Settings.embed_model = embed_model
    
    # Return the configured Settings object
    return Settings