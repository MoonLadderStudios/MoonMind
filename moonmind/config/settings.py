import os
from pathlib import Path
from typing import Any, Optional, Sequence

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_FILE = Path(__file__).resolve().parent.parent.parent / ".env"


class DatabaseSettings(BaseSettings):
    """Database settings"""

    POSTGRES_HOST: str = Field("api-db", env="POSTGRES_HOST")
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

    model_config = SettingsConfigDict(
        env_prefix="",
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
    )


class CelerySettings(BaseSettings):
    """Celery broker and result backend settings."""

    broker_url: str = Field(
        "amqp://guest:guest@rabbitmq:5672//",
        env="CELERY_BROKER_URL",
        description="AMQP URL for the Celery broker (RabbitMQ).",
    )
    result_backend: Optional[str] = Field(
        None,
        env="CELERY_RESULT_BACKEND",
        description="Database URL used by Celery to persist task results.",
    )
    default_queue: str = Field(
        "speckit",
        env="CELERY_DEFAULT_QUEUE",
        description="Default queue name for Spec Kit workflow tasks.",
    )
    default_exchange: str = Field(
        "speckit",
        env="CELERY_DEFAULT_EXCHANGE",
        description="Default exchange for Spec Kit workflow tasks.",
    )
    default_routing_key: str = Field(
        "speckit",
        env="CELERY_DEFAULT_ROUTING_KEY",
        description="Default routing key used by the Spec Kit queue.",
    )
    task_serializer: str = Field("json", env="CELERY_TASK_SERIALIZER")
    result_serializer: str = Field("json", env="CELERY_RESULT_SERIALIZER")
    accept_content: tuple[str, ...] = Field(
        ("json",),
        env="CELERY_ACCEPT_CONTENT",
        description="Accepted content types for Celery tasks.",
    )
    task_acks_late: bool = Field(True, env="CELERY_TASK_ACKS_LATE")
    task_acks_on_failure_or_timeout: bool = Field(
        True, env="CELERY_TASK_ACKS_ON_FAILURE_OR_TIMEOUT"
    )
    task_reject_on_worker_lost: bool = Field(
        True, env="CELERY_TASK_REJECT_ON_WORKER_LOST"
    )
    worker_prefetch_multiplier: int = Field(1, env="CELERY_WORKER_PREFETCH_MULTIPLIER")
    imports: tuple[str, ...] = Field(
        (),
        env="CELERY_IMPORTS",
        description="Celery modules imported by the worker on startup.",
    )
    result_extended: bool = Field(True, env="CELERY_RESULT_EXTENDED")
    result_expires: int = Field(7 * 24 * 60 * 60, env="CELERY_RESULT_EXPIRES")

    model_config = SettingsConfigDict(
        env_prefix="",
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
    )

    @field_validator("accept_content", "imports", mode="before")
    @classmethod
    def _split_csv(cls, value):
        """Allow comma-delimited strings for tuple fields."""
        if isinstance(value, str):
            items = [item.strip() for item in value.split(",") if item.strip()]
            return tuple(items)
        if isinstance(value, (list, tuple)):
            return tuple(value)
        return value


class SpecWorkflowSettings(BaseSettings):
    """Settings specific to Spec Kit Celery workflows."""

    repo_root: str = Field(".", env="SPEC_WORKFLOW_REPO_ROOT")
    tasks_root: str = Field("specs", env="SPEC_WORKFLOW_TASKS_ROOT")
    artifacts_root: str = Field(
        "var/artifacts/spec_workflows",
        env=("SPEC_WORKFLOW_ARTIFACT_ROOT", "SPEC_WORKFLOW_ARTIFACTS_ROOT"),
        description="Filesystem location where Spec workflow artifacts are persisted.",
    )
    job_image: str = Field(
        "moonmind/spec-automation-job:latest",
        env="SPEC_AUTOMATION_JOB_IMAGE",
        description="Container image used for Spec Automation job executions.",
    )
    workspace_root: str = Field(
        "/work",
        env="SPEC_AUTOMATION_WORKSPACE_ROOT",
        description="Host-mounted root directory for Spec Automation workspaces.",
    )
    celery_broker_url: Optional[str] = Field(
        None,
        env=("SPEC_WORKFLOW_CELERY_BROKER_URL", "CELERY_BROKER_URL"),
        description="Override Celery broker URL dedicated to Spec workflow chains.",
    )
    celery_result_backend: Optional[str] = Field(
        None,
        env=("SPEC_WORKFLOW_CELERY_RESULT_BACKEND", "CELERY_RESULT_BACKEND"),
        description="Override Celery result backend for Spec workflow chains.",
    )
    metrics_enabled: bool = Field(
        False,
        env="SPEC_WORKFLOW_METRICS_ENABLED",
        description="Toggle emission of Spec Automation StatsD metrics.",
    )
    metrics_host: Optional[str] = Field(
        None,
        env="SPEC_WORKFLOW_METRICS_HOST",
        description="Hostname for the StatsD metrics sink (optional).",
    )
    metrics_port: Optional[int] = Field(
        None,
        env="SPEC_WORKFLOW_METRICS_PORT",
        description="Port for the StatsD metrics sink (optional).",
    )
    metrics_namespace: str = Field(
        "spec_automation",
        env="SPEC_WORKFLOW_METRICS_NAMESPACE",
        description="Namespace/prefix applied to emitted Spec Automation metrics.",
    )
    default_feature_key: str = Field(
        "001-celery-chain-workflow", env="SPEC_WORKFLOW_DEFAULT_FEATURE_KEY"
    )
    codex_environment: Optional[str] = Field(None, env="CODEX_ENV")
    codex_model: Optional[str] = Field(None, env="CODEX_MODEL")
    codex_profile: Optional[str] = Field(None, env="CODEX_PROFILE")
    codex_shards: int = Field(
        3,
        env="CODEX_SHARDS",
        description="Number of Codex worker shards available for routing.",
        gt=0,
        le=64,
    )
    codex_queue: Optional[str] = Field(
        None,
        env=("SPEC_WORKFLOW_CODEX_QUEUE", "CODEX_QUEUE"),
        description="Explicit Codex queue name assigned to this worker.",
    )
    codex_volume_name: Optional[str] = Field(
        None,
        env="CODEX_VOLUME_NAME",
        description="Docker volume providing persistent Codex authentication.",
    )
    codex_login_check_image: Optional[str] = Field(
        None,
        env="CODEX_LOGIN_CHECK_IMAGE",
        description="Override container image for Codex login status checks.",
    )
    github_repository: Optional[str] = Field(
        None, env="SPEC_WORKFLOW_GITHUB_REPOSITORY"
    )
    github_token: Optional[str] = Field(None, env="SPEC_WORKFLOW_GITHUB_TOKEN")
    test_mode: bool = Field(False, env="SPEC_WORKFLOW_TEST_MODE")
    agent_backend: str = Field(
        "codex_cli",
        env="SPEC_AUTOMATION_AGENT_BACKEND",
        description="Active agent backend identifier for Spec Kit automation runs.",
    )
    allowed_agent_backends: tuple[str, ...] = Field(
        ("codex_cli",),
        env="SPEC_AUTOMATION_ALLOWED_AGENT_BACKENDS",
        description="Whitelisted agent backend identifiers for Spec Kit automation.",
    )
    agent_version: str = Field(
        "unspecified",
        env="SPEC_AUTOMATION_AGENT_VERSION",
        description="Version string recorded with the agent configuration snapshot.",
    )
    prompt_pack_version: Optional[str] = Field(
        None,
        env="SPEC_AUTOMATION_PROMPT_PACK_VERSION",
        description="Spec Kit prompt pack version associated with the selected agent.",
    )
    agent_runtime_env_keys: tuple[str, ...] = Field(
        ("CODEX_ENV", "CODEX_MODEL", "CODEX_PROFILE", "CODEX_API_KEY"),
        env="SPEC_AUTOMATION_AGENT_RUNTIME_ENV_KEYS",
        description="Environment variable names forwarded to the agent runtime snapshot.",
    )

    model_config = SettingsConfigDict(
        env_prefix="",
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
    )

    @field_validator(
        "metrics_host",
        "celery_broker_url",
        "celery_result_backend",
        "codex_environment",
        "codex_model",
        "codex_profile",
        "codex_queue",
        "codex_volume_name",
        "codex_login_check_image",
        mode="before",
    )
    @classmethod
    def _strip_and_blank_to_none(cls, value: Optional[str]) -> Optional[str]:
        """Normalize optional string settings by stripping whitespace and treating blanks as ``None``."""

        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value

    @field_validator("allowed_agent_backends", "agent_runtime_env_keys", mode="before")
    @classmethod
    def _split_agent_csv(cls, value: Optional[str | Sequence[str]]) -> tuple[str, ...]:
        """Allow comma-delimited strings for tuple-based agent settings."""

        if value is None:
            return ()

        if isinstance(value, str):
            raw_items: Sequence[object] = value.split(",")
        elif isinstance(value, Sequence) and not isinstance(
            value, (bytes, bytearray, str)
        ):
            raw_items = value
        else:
            raw_items = (value,)

        items = [str(item).strip() for item in raw_items if str(item).strip()]
        if not items:
            return ()

        # Preserve order while removing duplicates.
        return tuple(dict.fromkeys(items))

    def model_post_init(self, __context: Any) -> None:  # type: ignore[override]
        """Validate agent backend selections after settings load."""

        super().model_post_init(__context)
        # Ensure tuples are deduplicated even when env provided sequences
        allowed = tuple(dict.fromkeys(self.allowed_agent_backends or ()))
        self.allowed_agent_backends = allowed
        self.agent_runtime_env_keys = tuple(
            dict.fromkeys(self.agent_runtime_env_keys or ())
        )
        if allowed and self.agent_backend not in allowed:
            allowed_display = ", ".join(allowed)
            raise ValueError(
                f"Agent backend '{self.agent_backend}' is not permitted. "
                f"Allowed values: {allowed_display or '<none>'}"
            )

        # Spec workflow Celery overrides rely on pydantic ``env`` fallbacks and
        # ``AppSettings.model_post_init`` to populate sensible defaults.


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
    google_chat_model: str = Field("gemini-2.5-flash", env="GOOGLE_CHAT_MODEL")
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
        # Pydantic does not automatically populate nested models from environment
        # variables when they are created with ``default_factory``. Explicitly
        # handle boolean flags for nested Confluence and Jira settings to ensure
        # environment variables like ``ATLASSIAN_CONFLUENCE_ENABLED`` and
        # ``ATLASSIAN_JIRA_ENABLED`` are respected.
        confluence_env = os.getenv("ATLASSIAN_CONFLUENCE_ENABLED")
        if confluence_env is not None:
            self.confluence.confluence_enabled = confluence_env.lower() == "true"

        jira_env = os.getenv("ATLASSIAN_JIRA_ENABLED")
        if jira_env is not None:
            self.jira.jira_enabled = jira_env.lower() == "true"


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
    OIDC_ISSUER_URL: Optional[str] = Field(
        None,
        env="OIDC_ISSUER_URL",
        description="URL of the OIDC provider, e.g., Keycloak.",
    )
    OIDC_CLIENT_ID: Optional[str] = Field(None, env="OIDC_CLIENT_ID")
    OIDC_CLIENT_SECRET: Optional[str] = Field(None, env="OIDC_CLIENT_SECRET")
    DEFAULT_USER_ID: Optional[str] = Field(
        None,
        env="DEFAULT_USER_ID",
        description="Default user ID for 'disabled' auth_provider mode.",
    )
    DEFAULT_USER_EMAIL: Optional[str] = Field(
        None,
        env="DEFAULT_USER_EMAIL",
        description="Default user email for 'disabled' auth_provider mode.",
    )
    DEFAULT_USER_PASSWORD: Optional[str] = Field(
        "default_password_please_change",
        env="DEFAULT_USER_PASSWORD",
        description="Default user password for 'disabled' auth_provider mode. Used for user creation if needed.",
    )

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
    celery: CelerySettings = Field(default_factory=CelerySettings)
    spec_workflow: SpecWorkflowSettings = Field(default_factory=SpecWorkflowSettings)

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

    def model_post_init(self, __context: Any) -> None:  # type: ignore[override]
        """Populate derived Celery defaults after settings load."""
        super().model_post_init(__context)
        if not self.celery.result_backend:
            db = self.database
            self.celery.result_backend = "db+postgresql://{}:{}@{}:{}/{}".format(
                db.POSTGRES_USER,
                db.POSTGRES_PASSWORD,
                db.POSTGRES_HOST,
                db.POSTGRES_PORT,
                db.POSTGRES_DB,
            )

        if not self.spec_workflow.celery_broker_url:
            self.spec_workflow.celery_broker_url = self.celery.broker_url
        if not self.spec_workflow.celery_result_backend:
            self.spec_workflow.celery_result_backend = self.celery.result_backend

    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        extra="forbid",
    )


# Create a global settings instance
settings = AppSettings()
