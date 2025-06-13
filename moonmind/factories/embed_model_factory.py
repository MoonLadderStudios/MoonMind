from llama_index.embeddings.ollama import OllamaEmbedding
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from llama_index.embeddings.langchain import LangchainEmbedding # New import
# Ensure other necessary imports like AppSettings from ..config.settings are present

from ..config.settings import AppSettings


def build_embed_model(settings: AppSettings):
    provider = settings.default_embedding_provider.lower() if settings.default_embedding_provider else "google"

    if provider == "google":
        model_name = settings.google.google_embedding_model
    elif provider == "ollama":
        model_name = settings.ollama.ollama_embeddings_model
    # elif provider == "openai": # Placeholder for future OpenAI embedding model from settings
    #     model_name = settings.openai.openai_embedding_model # Assuming such a setting would exist
    else:
        # Fallback to a sensible default or raise an error if the provider's model isn't specified
        # For now, let's assume if the provider is set, its specific model name should be available.
        # This path might need better error handling or a default model if a provider is selected
        # but its specific model isn't configured (though pydantic usually ensures defaults).
        raise ValueError(f"Unsupported or misconfigured embed provider: {provider}")

    if provider == "ollama":
        return OllamaEmbedding(
            base_url=settings.ollama.ollama_base_url,
            model_name=model_name
        ), settings.ollama.ollama_embeddings_dimensions
    elif provider == "google":
        # Ensure GOOGLE_API_KEY is handled if directly using GoogleGenerativeAiEmbedding
        # This might be handled by genai.configure in main or when settings are loaded
        # For now, assume settings.google.google_api_key is available and configured
        if not settings.google.google_api_key: # Keep this check
            raise ValueError("Google API key is not configured for Google embeddings.")
        # It's good practice to ensure genai is configured if API key isn't passed directly
        # import google.generativeai as genai # May already be configured globally
        # if not genai.get_api_key(): # This is a runtime check, might be better if settings ensure configuration
        #    genai.configure(api_key=settings.google.google_api_key)
        # The GoogleGenerativeAIEmbeddings constructor will use the globally configured API key
        # or one passed directly.
        lc_embed_model = GoogleGenerativeAIEmbeddings( # This is the Langchain model
            model=model_name, # Parameter changed from model_name to model
            google_api_key=settings.google.google_api_key # Explicitly pass for clarity
            # Langchain's GoogleGenerativeAIEmbeddings handles API key through google-generativeai SDK,
            # which can be configured globally (e.g., genai.configure(api_key=...))
            # or might pick up env variables. Explicitly passing it here if the lc_embed_model supports it,
            # otherwise, ensure it's configured before this call.
            # The `google_api_key` param might not be needed if `genai.configure` was used.
        )
        # Wrap the Langchain embedding model for LlamaIndex compatibility
        llama_embed_model = LangchainEmbedding(lc_embed_model)
        return llama_embed_model, settings.google.google_embeddings_dimensions
    # Add other providers here if they become default options
    # elif provider == "openai":
    #     from llama_index.embeddings.openai import OpenAIEmbedding # Conditional import
    #     return OpenAIEmbedding(
    #         model=model_name
    #         # dimensions=settings.openai.openai_embed_dimensions # if defined
    #     ), settings.openai.openai_embed_dimensions # if defined
    else:
        # Fallback or error if provider is not supported for embeddings
        # This specific 'else' might be unreachable if the provider check above is exhaustive
        # and raises an error for unsupported providers.
        raise ValueError(f"Unsupported default embed provider: {provider}. Or Google provider selected but API key missing.")
