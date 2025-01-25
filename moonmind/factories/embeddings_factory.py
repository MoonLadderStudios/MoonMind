from langchain_ollama import OllamaEmbeddings

from ..config.settings import AppSettings


def build_embeddings_provider(settings: AppSettings):
    if settings.embeddings_provider == "ollama":
        return OllamaEmbeddings(base_url=settings.ollama.ollama_base_url, model=settings.ollama.ollama_embeddings_model)
    else:
        raise ValueError(f"Unsupported embeddings provider: {settings.embeddings_provider}")
