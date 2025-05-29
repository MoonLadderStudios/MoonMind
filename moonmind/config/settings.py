import os
from typing import Optional
from pydantic import Field
from pydantic_settings import BaseSettings


class GoogleSettings(BaseSettings):
    """Google/Gemini API settings"""
    google_api_key: Optional[str] = Field(None, env="GOOGLE_API_KEY")
    google_chat_model: str = Field("gemini-2.5-pro-exp-03-25", env="GOOGLE_CHAT_MODEL")
    google_embeddings_model: str = Field("models/text-embedding-004", env="GOOGLE_EMBEDDINGS_MODEL")
    google_embeddings_dimensions: int = Field(768, env="GOOGLE_EMBEDDINGS_DIMENSIONS")
    google_enabled: bool = Field(True, env="GOOGLE_ENABLED")
    # google_application_credentials has been moved to GoogleDriveSettings as per requirements


    class Config:
        env_prefix = ""


class GitHubSettings(BaseSettings):
    """GitHub settings"""
    github_token: Optional[str] = Field(None, env="GITHUB_TOKEN")
    github_repos: Optional[str] = Field(None, env="GITHUB_REPOS") # Comma-delimited string of repositories
    github_enabled: bool = Field(True, env="GITHUB_ENABLED")

    class Config:
        env_prefix = ""


class GoogleDriveSettings(BaseSettings):
    """Google Drive settings"""
    google_drive_enabled: bool = Field(False, env="GOOGLE_DRIVE_ENABLED")
    google_drive_folder_id: Optional[str] = Field(None, env="GOOGLE_DRIVE_FOLDER_ID")
    google_application_credentials: Optional[str] = Field(None, env="GOOGLE_APPLICATION_CREDENTIALS")

    class Config:
        env_prefix = ""


class OpenAISettings(BaseSettings):
    """OpenAI API settings"""
    openai_api_key: Optional[str] = Field(None, env="OPENAI_API_KEY")
    openai_chat_model: str = Field("gpt-3.5-turbo", env="OPENAI_CHAT_MODEL")
    openai_enabled: bool = Field(True, env="OPENAI_ENABLED")

    class Config:
        env_prefix = ""


class OllamaSettings(BaseSettings):
    """Ollama settings"""
    ollama_base_url: str = Field("http://ollama:11434", env="OLLAMA_BASE_URL")
    ollama_embeddings_model: str = Field("hf.co/tensorblock/gte-Qwen2-7B-instruct-GGUF:Q6_K", env="OLLAMA_EMBEDDINGS_MODEL")
    ollama_embeddings_dimensions: int = Field(3584, env="OLLAMA_EMBEDDINGS_DIMENSIONS")
    ollama_keep_alive: str = Field("-1m", env="OLLAMA_KEEP_ALIVE")
    ollama_chat_model: str = Field("devstral:24b", env="OLLAMA_CHAT_MODEL")
    ollama_modes: str = Field("chat", env="OLLAMA_MODES")
    ollama_enabled: bool = Field(True, env="OLLAMA_ENABLED")

    class Config:
        env_prefix = ""

class QdrantSettings(BaseSettings):
    """Qdrant settings"""
    qdrant_host: str = Field("qdrant", env="QDRANT_HOST")
    qdrant_port: int = Field(6333, env="QDRANT_PORT")
    qdrant_api_key: Optional[str] = Field(None, env="QDRANT_API_KEY")
    qdrant_enabled: bool = Field(True, env="QDRANT_ENABLED")

class RAGSettings(BaseSettings):
    """RAG (Retrieval-Augmented Generation) settings"""
    rag_enabled: bool = Field(True, env="RAG_ENABLED")
    similarity_top_k: int = Field(5, env="RAG_SIMILARITY_TOP_K")
    max_context_length_chars: int = Field(8000, env="RAG_MAX_CONTEXT_LENGTH_CHARS")

    class Config:
        env_prefix = ""


class AppSettings(BaseSettings):
    """Main application settings"""

    # Sub-settings
    google: GoogleSettings = GoogleSettings()
    openai: OpenAISettings = OpenAISettings()
    ollama: OllamaSettings = OllamaSettings()
    github: GitHubSettings = GitHubSettings()
    google_drive: GoogleDriveSettings = GoogleDriveSettings()
    qdrant: QdrantSettings = QdrantSettings()
    rag: RAGSettings = RAGSettings()

    # Default providers and models
    default_chat_provider: str = Field("google", env="DEFAULT_CHAT_PROVIDER")
    default_embed_provider: str = Field("google", env="DEFAULT_EMBED_PROVIDER")
    default_chat_model: Optional[str] = Field(None, env="DEFAULT_CHAT_MODEL")
    default_embed_model: Optional[str] = Field(None, env="DEFAULT_EMBED_MODEL")

    # Legacy settings for backwards compatibility
    default_embeddings_provider: str = Field("ollama", env="DEFAULT_EMBEDDINGS_PROVIDER")

    # Model cache settings
    model_cache_refresh_interval: int = Field(3600, env="MODEL_CACHE_REFRESH_INTERVAL")
    model_cache_refresh_interval_seconds: int = Field(3600, env="MODEL_CACHE_REFRESH_INTERVAL_SECONDS")
    vector_store_provider: str = Field("qdrant", env="VECTOR_STORE_PROVIDER") # Added field

    # Vector store settings
    vector_store_collection_name: str = Field("moonmind", env="VECTOR_STORE_COLLECTION_NAME")

    # Other settings
    confluence_api_key: Optional[str] = Field(None, env="CONFLUENCE_API_KEY")
    confluence_enabled: bool = Field(True, env="CONFLUENCE_ENABLED")
    confluence_url: Optional[str] = Field(None, env="CONFLUENCE_URL")
    confluence_username: Optional[str] = Field(None, env="CONFLUENCE_USERNAME")
    confluence_space_keys: Optional[str] = Field(None, env="CONFLUENCE_SPACE_KEYS")

    fastapi_reload: bool = Field(False, env="FASTAPI_RELOAD")
    fernet_key: Optional[str] = Field(None, env="FERNET_KEY")
    hf_access_token: Optional[str] = Field(None, env="HF_ACCESS_TOKEN")

    langchain_api_key: Optional[str] = Field(None, env="LANGCHAIN_API_KEY")
    langchain_tracing_v2: str = Field("true", env="LANGCHAIN_TRACING_V2")
    langchain_project: str = Field("MoonMind", env="LANGCHAIN_PROJECT")

    model_directory: str = Field("/app/model_data", env="MODEL_DIRECTORY")

    # OpenHands settings
    openhands_llm_api_key: Optional[str] = Field(None, env="OPENHANDS__LLM__API_KEY")
    openhands_llm_model: str = Field("gemini/gemini-2.5-pro-exp-03-25", env="OPENHANDS__LLM__MODEL")
    openhands_llm_custom_llm_provider: str = Field("gemini", env="OPENHANDS__LLM__CUSTOM_LLM_PROVIDER")
    openhands_llm_timeout: int = Field(600, env="OPENHANDS__LLM__TIMEOUT")
    openhands_llm_embedding_model: str = Field("models/text-embedding-004", env="OPENHANDS__LLM__EMBEDDING_MODEL")
    openhands_core_workspace_base: str = Field("/workspace", env="OPENHANDS__CORE__WORKSPACE_BASE")

    postgres_version: int = Field(14, env="POSTGRES_VERSION")

    def get_default_chat_model(self) -> str:
        """Get the default chat model, falling back to provider defaults"""
        if self.default_chat_model:
            return self.default_chat_model

        if self.default_chat_provider == "google":
            return self.google.google_chat_model
        elif self.default_chat_provider == "openai":
            return self.openai.openai_chat_model
        elif self.default_chat_provider == "ollama":
            return self.ollama.ollama_chat_model
        else:
            # Fallback to Google as default
            return self.google.google_chat_model

    def get_default_embed_model(self) -> str:
        """Get the default embedding model, falling back to provider defaults"""
        if self.default_embed_model:
            return self.default_embed_model

        if self.default_embed_provider == "ollama":
            return self.ollama.ollama_embeddings_model
        else:
            # Fallback to Ollama as default
            return self.ollama.ollama_embeddings_model

    def is_provider_enabled(self, provider: str) -> bool:
        """Check if a provider is enabled"""
        provider = provider.lower()
        if provider == "google":
            return self.google.google_enabled and bool(self.google.google_api_key)
        elif provider == "openai":
            return self.openai.openai_enabled and bool(self.openai.openai_api_key)
        elif provider == "ollama":
            return self.ollama.ollama_enabled
        else:
            return False

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


# Create a global settings instance
settings = AppSettings()
