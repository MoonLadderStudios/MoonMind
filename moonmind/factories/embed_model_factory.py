from llama_index.embeddings.google_genai import GoogleGenAIEmbedding
from llama_index.embeddings.ollama import OllamaEmbedding

from ..config.settings import AppSettings


def build_embed_model(settings: AppSettings):
    provider = (
        settings.default_embedding_provider.lower()
        if settings.default_embedding_provider
        else "google"
    )

    if provider == "ollama":
        return OllamaEmbedding(
            base_url=settings.ollama.ollama_base_url,
            model_name=settings.ollama.ollama_embedding_model,
        ), settings.ollama.ollama_embeddings_dimensions
    elif provider == "google":
        if not settings.google.google_api_key:
            raise ValueError("Google API key is not configured for Google embeddings.")
        return GoogleGenAIEmbedding(
            model_name=settings.google.google_embedding_model,
            google_embed_batch_size=settings.google.google_embed_batch_size,
        ), settings.google.google_embedding_dimensions
    # Add other providers here if they become default options
    # TODO: OpenAI
    else:
        # Fallback or error if provider is not supported for embeddings
        raise ValueError(f"Unsupported default embed provider: {provider}.")
