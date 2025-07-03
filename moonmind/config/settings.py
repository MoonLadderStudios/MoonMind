from pathlib import Path  # Added Path
from typing import Optional  # Keep one Optional import

from pydantic import (
    Field, field_validator)
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseSettings):
    """Database settings"""

    POSTGRES_HOST: str = Field("localhost", env="POSTGRES_HOST")
    POSTGRES_USER: str = Field("postgres", env="POSTGRES_USER")
    POSTGRES_PASSWORD: str = Field("password", env="POSTGRES_PASSWORD")
    POSTGRES_DB: str = Field("moonmind", env="POSTGRES_DB")
    POSTGRES_PORT: int = Field(5432, env="POSTGRES_PORT")

    @property
    def POSTGRES_URL(self) -> str:
        """Construct PostgreSQL URL from components"""
        return f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"

    @property
    def POSTGRES_URL_SYNC(self) -> str:
        """Construct synchronous PostgreSQL URL for Alembic"""
        return f"postgresql+psycopg2://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"

    model_config = SettingsConfigDict(env_prefix="")


class SecuritySettings(BaseSettings):
    """Security settings"""

    JWT_SECRET_KEY: Optional[str] = Field(
        "test_jwt_secret_key", env="JWT_SECRET_KEY"
    )  # Made Optional and added default
    ENCRYPTION_MASTER_KEY: Optional[str] = Field(
        "test_encryption_master_key", env="ENCRYPTION_MASTER_KEY"
    )  # Made Optional and added default

    model_config = SettingsConfigDict(env_prefix="")


class GoogleSettings(BaseSettings):
    """Google/Gemini API settings"""

    google_api_key: Optional[str] = Field(None, env="GOOGLE_API_KEY")
    google_chat_model: str = Field("gemini-2.5-pro-exp-03-25", env="GOOGLE_CHAT_MODEL")
    google_embedding_model: str = Field(
        "models/gemini-embedding-exp-03-07", env="GOOGLE_EMBEDDING_MODEL"
    )
    google_embedding_dimensions: int = Field(3072, env="GOOGLE_EMBEDDING_DIMENSIONS")
    google_enabled: bool = Field(True, env="GOOGLE_ENABLED")
    google_embed_batch_size: int = Field(100, env="GOOGLE_EMBED_BATCH_SIZE")
    # google_application_credentials has been moved to GoogleDriveSettings as per requirements

    model_config = SettingsConfigDict(env_prefix="")


class AnthropicSettings(BaseSettings):
    """Anthropic API settings"""

    anthropic_api_key: Optional[str] = Field(None, env="ANTHROPIC_API_KEY")
    anthropic_chat_model: str = Field(
        "claude-3-opus-20240229", env="ANTHROPIC_CHAT_MODEL"
    )
    anthropic_enabled: bool = Field(True, env="ANTHROPIC_ENABLED")

    model_config = SettingsConfigDict(env_prefix="")


class GitHubSettings(BaseSettings):
    """GitHub settings"""

    github_token: Optional[str] = Field(None, env="GITHUB_TOKEN")
    github_repos: Optional[str] = Field(
        None, env="GITHUB_REPOS"
    )  # Comma-delimited string of repositories
    github_enabled: bool = Field(True, env="GITHUB_ENABLED")

    model_config = SettingsConfigDict(env_prefix="")


class GoogleDriveSettings(BaseSettings):
    """Google Drive settings"""

    google_drive_enabled: bool = Field(False, env="GOOGLE_DRIVE_ENABLED")
    google_drive_folder_id: Optional[str] = Field(None, env="GOOGLE_DRIVE_FOLDER_ID")
    google_application_credentials: Optional[str] = Field(
        None, env="GOOGLE_APPLICATION_CREDENTIALS"
    )

    model_config = SettingsConfigDict(env_prefix="")


class OpenAISettings(BaseSettings):
    """OpenAI API settings"""

    openai_api_key: Optional[str] = Field(None, env="OPENAI_API_KEY")
    openai_chat_model: str = Field("gpt-3.5-turbo", env="OPENAI_CHAT_MODEL")
    openai_enabled: bool = Field(True, env="OPENAI_ENABLED")

    model_config = SettingsConfigDict(env_prefix="")


class OllamaSettings(BaseSettings):
    """Ollama settings"""

    ollama_base_url: str = Field("http://ollama:11434", env="OLLAMA_BASE_URL")
    ollama_embedding_model: str = Field(
        "hf.co/tensorblock/gte-Qwen2-7B-instruct-GGUF:Q6_K",
        env="OLLAMA_EMBEDDING_MODEL",
    )
    ollama_embeddings_dimensions: int = Field(3584, env="OLLAMA_EMBEDDINGS_DIMENSIONS")
    ollama_keep_alive: str = Field("-1m", env="OLLAMA_KEEP_ALIVE")
    ollama_chat_model: str = Field("devstral:24b", env="OLLAMA_CHAT_MODEL")
    ollama_modes: str = Field("chat", env="OLLAMA_MODES")
    ollama_enabled: bool = Field(True, env="OLLAMA_ENABLED")

    model_config = SettingsConfigDict(env_prefix="")


class ConfluenceSettings(BaseSettings):
    """Confluence specific settings"""

    confluence_space_keys: Optional[str] = Field(
        None, env="ATLASSIAN_CONFLUENCE_SPACE_KEYS"
    )
    confluence_enabled: bool = Field(False, env="ATLASSIAN_CONFLUENCE_ENABLED")

    model_config = SettingsConfigDict(
        env_prefix="", env_file=".env", env_file_encoding="utf-8"
    )


class JiraSettings(BaseSettings):
    """Jira specific settings"""

    jira_jql_query: Optional[str] = Field(None, env="ATLASSIAN_JIRA_JQL_QUERY")
    jira_fetch_batch_size: int = Field(50, env="ATLASSIAN_JIRA_FETCH_BATCH_SIZE")
    jira_enabled: bool = Field(False, env="ATLASSIAN_JIRA_ENABLED")

    model_config = SettingsConfigDict(
        env_prefix="", env_file=".env", env_file_encoding="utf-8"
    )


class AtlassianSettings(BaseSettings):
    """Atlassian base settings"""

    atlassian_api_key: Optional[str] = Field(None, env="ATLASSIAN_API_KEY")
    atlassian_username: Optional[str] = Field(None, env="ATLASSIAN_USERNAME")
    atlassian_url: Optional[str] = Field(None, env="ATLASSIAN_URL")
    atlassian_enabled: bool = Field(False, env="ATLASSIAN_ENABLED")

    # Nested settings for Confluence and Jira
    confluence: ConfluenceSettings = Field(default_factory=ConfluenceSettings)
    jira: JiraSettings = Field(default_factory=JiraSettings)

    model_config = SettingsConfigDict(
        env_prefix="", env_file=".env", env_file_encoding="utf-8"
    )

    def __init__(self, **data):
        super().__init__(**data)
        if self.atlassian_url and self.atlassian_url.startswith("https://https://"):
            self.atlassian_url = self.atlassian_url[8:]


class QdrantSettings(BaseSettings):
    """Qdrant settings"""

    qdrant_host: str = Field("qdrant", env="QDRANT_HOST")
    qdrant_port: int = Field(6333, env="QDRANT_PORT")
    qdrant_api_key: Optional[str] = Field(None, env="QDRANT_API_KEY")
    qdrant_enabled: bool = Field(True, env="QDRANT_ENABLED")
    model_config = SettingsConfigDict(env_prefix="")


class RAGSettings(BaseSettings):
    """RAG (Retrieval-Augmented Generation) settings"""

    rag_enabled: bool = Field(True, env="RAG_ENABLED")
    similarity_top_k: int = Field(5, env="RAG_SIMILARITY_TOP_K")
    max_context_length_chars: int = Field(8000, env="RAG_MAX_CONTEXT_LENGTH_CHARS")

    model_config = SettingsConfigDict(env_prefix="")


class LocalDataSettings(BaseSettings):
    """Settings for local data indexing"""

    local_data_path: Optional[str] = Field(
        None,
        env=["LocalData", "LOCAL_DATA_PATH"],
    )
    # Add local_data_enabled if we want a separate boolean flag, but for now, path presence implies enabled.
    # local_data_enabled: bool = Field(False, env="LOCAL_DATA_ENABLED")

    model_config = SettingsConfigDict(
        env_prefix="", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )


class OIDCSettings(BaseSettings):
    """OIDC settings"""

    AUTH_PROVIDER: str = Field(
        "disabled",
        description="Authentication provider: 'disabled' or 'keycloak'.",
        env="AUTH_PROVIDER",
    )
    OIDC_ISSUER_URL: Optional[str] = Field(None, env="OIDC_ISSUER_URL", description="URL of the OIDC provider, e.g., Keycloak.")
    OIDC_CLIENT_ID: Optional[str] = Field(None, env="OIDC_CLIENT_ID")
    OIDC_CLIENT_SECRET: Optional[str] = Field(None, env="OIDC_CLIENT_SECRET")
    DEFAULT_USER_ID: Optional[str] = Field(None, env="DEFAULT_USER_ID", description="Default user ID for 'disabled' auth_provider mode.")
    DEFAULT_USER_EMAIL: Optional[str] = Field(None, env="DEFAULT_USER_EMAIL", description="Default user email for 'disabled' auth_provider mode.")
    DEFAULT_USER_PASSWORD: Optional[str] = Field("default_password_please_change", env="DEFAULT_USER_PASSWORD", description="Default user password for 'disabled' auth_provider mode. Used for user creation if needed.")

    model_config = SettingsConfigDict(env_prefix="")


class AppSettings(BaseSettings):
    """Main application settings"""

    # Sub-settings
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    security: SecuritySettings = Field(default_factory=SecuritySettings)
    google: GoogleSettings = Field(default_factory=GoogleSettings)
    openai: OpenAISettings = Field(default_factory=OpenAISettings)
    ollama: OllamaSettings = Field(default_factory=OllamaSettings)
    anthropic: AnthropicSettings = Field(default_factory=AnthropicSettings)
    github: GitHubSettings = Field(default_factory=GitHubSettings)
    google_drive: GoogleDriveSettings = Field(default_factory=GoogleDriveSettings)
    qdrant: QdrantSettings = Field(default_factory=QdrantSettings)
    rag: RAGSettings = Field(default_factory=RAGSettings)
    atlassian: AtlassianSettings = Field(default_factory=AtlassianSettings)
    local_data: LocalDataSettings = Field(default_factory=LocalDataSettings)
    oidc: OIDCSettings = Field(default_factory=OIDCSettings)

    # Default providers and models
    default_chat_provider: str = Field("google", env="DEFAULT_CHAT_PROVIDER")
    default_embedding_provider: str = Field("google", env="DEFAULT_EMBEDDING_PROVIDER")

    # Legacy settings for backwards compatibility
    default_embeddings_provider: str = Field(
        "ollama", env="DEFAULT_EMBEDDINGS_PROVIDER"
    )

    # Model cache settings
    model_cache_refresh_interval: int = Field(3600, env="MODEL_CACHE_REFRESH_INTERVAL")
    model_cache_refresh_interval_seconds: int = Field(
        3600, env="MODEL_CACHE_REFRESH_INTERVAL_SECONDS"
    )
    vector_store_provider: str = Field(
        "qdrant", env="VECTOR_STORE_PROVIDER"
    )  # Added field

    # Vector store settings
    vector_store_collection_name: str = Field(
        "moonmind", env="VECTOR_STORE_COLLECTION_NAME"
    )

    # Other settings
    fastapi_reload: bool = Field(False, env="FASTAPI_RELOAD")
    fernet_key: Optional[str] = Field(None, env="FERNET_KEY")
    hf_access_token: Optional[str] = Field(None, env="HF_ACCESS_TOKEN")

    langchain_api_key: Optional[str] = Field(None, env="LANGCHAIN_API_KEY")
    langchain_tracing_v2: str = Field("true", env="LANGCHAIN_TRACING_V2")
    langchain_project: str = Field("MoonMind", env="LANGCHAIN_PROJECT")

    model_directory: str = Field("/app/model_data", env="MODEL_DIRECTORY")

    # OpenHands settings
    openhands_llm_api_key: Optional[str] = Field(None, env="OPENHANDS__LLM__API_KEY")
    openhands_llm_model: str = Field(
        "gemini/gemini-2.5-pro-exp-03-25", env="OPENHANDS__LLM__MODEL"
    )
    openhands_llm_custom_llm_provider: str = Field(
        "gemini", env="OPENHANDS__LLM__CUSTOM_LLM_PROVIDER"
    )
    openhands_llm_timeout: int = Field(600, env="OPENHANDS__LLM__TIMEOUT")
    openhands_llm_embedding_model: str = Field(
        "models/text-embedding-004", env="OPENHANDS__LLM__EMBEDDING_MODEL"
    )
    openhands_core_workspace_base: str = Field(
        "/workspace", env="OPENHANDS__CORE__WORKSPACE_BASE"
    )

    postgres_version: int = Field(14, env="POSTGRES_VERSION")

    # ------------------------------------------------------------------
    # Validators
    # ------------------------------------------------------------------

    @field_validator("fastapi_reload", mode="before")
    @classmethod
    def _coerce_fastapi_reload(cls, v):
        """Ensure an empty string or malformed value for FASTAPI_RELOAD becomes False.

        This avoids ValidationError when the env var is set but left blank.
        """
        from moonmind.utils.env_bool import env_to_bool

        return env_to_bool(v, default=False)

    def is_provider_enabled(self, provider: str) -> bool:
        """Check if a provider is enabled"""
        provider = provider.lower()
        if provider == "google":
            return self.google.google_enabled and bool(self.google.google_api_key)
        elif provider == "openai":
            return self.openai.openai_enabled and bool(self.openai.openai_api_key)
        elif provider == "ollama":
            return self.ollama.ollama_enabled
        elif provider == "anthropic":
            return self.anthropic.anthropic_enabled and bool(
                self.anthropic.anthropic_api_key
            )
        else:
            return False

    def get_default_chat_model(self) -> str:
        """Get the default chat model based on the default provider"""
        provider = self.default_chat_provider.lower()
        if provider == "google":
            return self.google.google_chat_model
        elif provider == "openai":
            return self.openai.openai_chat_model
        elif provider == "ollama":
            return self.ollama.ollama_chat_model
        elif provider == "anthropic":
            return self.anthropic.anthropic_chat_model
        else:
            # Fallback to google if unknown provider
            return self.google.google_chat_model

    model_config = SettingsConfigDict(
        env_file=str(Path(__file__).resolve().parent.parent.parent / ".env"),
        env_file_encoding="utf-8",
        extra="forbid",
    )


# Create a global settings instance
settings = AppSettings()
