from llama_index.embeddings.ollama import OllamaEmbedding
from llama_index.embeddings.google import GoogleGenerativeAiEmbedding # Add this import
# Ensure other necessary imports like AppSettings from ..config.settings are present

from ..config.settings import AppSettings


def build_embed_model(settings: AppSettings):
    provider = settings.default_embed_provider.lower() if settings.default_embed_provider else "google"
    model_name = settings.get_default_embed_model() # This now gets the correct model based on provider

    if provider == "ollama":
        return OllamaEmbedding(
            base_url=settings.ollama.ollama_base_url,
            model_name=model_name # Use the model_name from get_default_embed_model
        ), settings.ollama.ollama_embeddings_dimensions
    elif provider == "google":
        # Ensure GOOGLE_API_KEY is handled if directly using GoogleGenerativeAiEmbedding
        # This might be handled by genai.configure in main or when settings are loaded
        # For now, assume settings.google.google_api_key is available and configured
        if not settings.google.google_api_key:
            raise ValueError("Google API key is not configured for Google embeddings.")
        return GoogleGenerativeAiEmbedding(
            model_name=model_name, # Use the model_name
            # title="Default Title", # Optional: if required by your specific use case or version
            # task_type="retrieval_document" # Optional: common default
        ), settings.google.google_embeddings_dimensions
    # Add other providers here if they become default options
    # elif provider == "openai":
    #     from llama_index.embeddings.openai import OpenAIEmbedding # Conditional import
    #     return OpenAIEmbedding(
    #         model=model_name
    #         # dimensions=settings.openai.openai_embed_dimensions # if defined
    #     ), settings.openai.openai_embed_dimensions # if defined
    else:
        # Fallback or error if provider is not supported for embeddings
        raise ValueError(f"Unsupported default embed provider: {provider}.")
