from llama_index.embeddings.ollama import OllamaEmbedding

from ..config.settings import AppSettings


def build_embed_model(settings: AppSettings):
    if settings.default_embeddings_provider == "ollama":
        return OllamaEmbedding(
            base_url=settings.ollama.ollama_base_url,
            model_name=settings.ollama.ollama_embeddings_model
        ), settings.ollama.ollama_embeddings_dimensions
