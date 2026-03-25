import os
import re
from typing import Annotated, Any, Optional, Sequence
from urllib.parse import urlsplit

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from moonmind.claude.runtime import CLAUDE_RUNTIME_DISABLED_MESSAGE
from moonmind.claude.runtime import (
    build_runtime_gate_state as build_claude_runtime_gate_state,
)
from moonmind.config.jules_settings import JulesSettings
from moonmind.config.paths import ENV_FILE
from moonmind.jules.runtime import JULES_RUNTIME_DISABLED_MESSAGE
from moonmind.jules.runtime import (
    build_runtime_gate_state as build_jules_runtime_gate_state,
)

_ALLOWED_TARGET_DEFAULTS = ("project", "moonmind", "both")
_ALLOWED_PROPOSAL_SEVERITIES = ("low", "medium", "high", "critical")
_OWNER_REPO_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class DatabaseSettings(BaseSettings):
    """Database settings"""

    POSTGRES_HOST: str = Field("moonmind-api-db", alias="POSTGRES_HOST")
    POSTGRES_USER: str = Field("postgres", alias="POSTGRES_USER")
    POSTGRES_PASSWORD: str = Field("password", alias="POSTGRES_PASSWORD")
    POSTGRES_DB: str = Field("moonmind", alias="POSTGRES_DB")
    POSTGRES_PORT: int = Field(5432, alias="POSTGRES_PORT")

    @property
    def POSTGRES_URL(self) -> str:
        """Construct PostgreSQL URL from components"""
        return f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"

    @property
    def POSTGRES_URL_SYNC(self) -> str:
        """Construct synchronous PostgreSQL URL for Alembic"""
        return f"postgresql+psycopg2://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"

    model_config = SettingsConfigDict(
        populate_by_name=True,
        env_prefix="",
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )


class TemporalSettings(BaseSettings):
    """Temporal runtime lifecycle settings."""

    address: str = Field("temporal:7233", validation_alias="TEMPORAL_ADDRESS")
    namespace: str = Field("moonmind", validation_alias="TEMPORAL_NAMESPACE")
    worker_fleet: str = Field("workflow", validation_alias="TEMPORAL_WORKER_FLEET")
    workflow_task_queue: str = Field(
        "mm.workflow", validation_alias="TEMPORAL_WORKFLOW_TASK_QUEUE"
    )
    activity_artifacts_task_queue: str = Field(
        "mm.activity.artifacts",
        validation_alias="TEMPORAL_ACTIVITY_ARTIFACTS_TASK_QUEUE",
    )
    activity_llm_task_queue: str = Field(
        "mm.activity.llm",
        validation_alias="TEMPORAL_ACTIVITY_LLM_TASK_QUEUE",
    )
    activity_sandbox_task_queue: str = Field(
        "mm.activity.sandbox",
        validation_alias="TEMPORAL_ACTIVITY_SANDBOX_TASK_QUEUE",
    )
    activity_integrations_task_queue: str = Field(
        "mm.activity.integrations",
        validation_alias="TEMPORAL_ACTIVITY_INTEGRATIONS_TASK_QUEUE",
    )
    activity_agent_runtime_task_queue: str = Field(
        "mm.activity.agent_runtime",
        validation_alias="TEMPORAL_ACTIVITY_AGENT_RUNTIME_TASK_QUEUE",
    )
    temporal_authoritative_read_enabled: bool = Field(
        False,
        alias="TEMPORAL_AUTHORITATIVE_READ_ENABLED",
    )
    workflow_worker_concurrency: int | None = Field(
        8,
        validation_alias="TEMPORAL_WORKFLOW_WORKER_CONCURRENCY",
        ge=1,
    )
    artifacts_worker_concurrency: int | None = Field(
        8,
        validation_alias="TEMPORAL_ARTIFACTS_WORKER_CONCURRENCY",
        ge=1,
    )
    llm_worker_concurrency: int | None = Field(
        4,
        validation_alias="TEMPORAL_LLM_WORKER_CONCURRENCY",
        ge=1,
    )
    sandbox_worker_concurrency: int | None = Field(
        2,
        validation_alias="TEMPORAL_SANDBOX_WORKER_CONCURRENCY",
        ge=1,
    )
    integrations_worker_concurrency: int | None = Field(
        4,
        validation_alias="TEMPORAL_INTEGRATIONS_WORKER_CONCURRENCY",
        ge=1,
    )
    agent_runtime_worker_concurrency: int | None = Field(
        16,
        validation_alias="TEMPORAL_AGENT_RUNTIME_WORKER_CONCURRENCY",
        ge=1,
    )
    integration_poll_initial_seconds: int = Field(
        5,
        validation_alias="TEMPORAL_INTEGRATION_POLL_INITIAL_SECONDS",
        ge=1,
    )
    integration_poll_max_seconds: int = Field(
        300,
        validation_alias="TEMPORAL_INTEGRATION_POLL_MAX_SECONDS",
        ge=1,
    )
    integration_poll_jitter_ratio: float = Field(
        0.2,
        validation_alias="TEMPORAL_INTEGRATION_POLL_JITTER_RATIO",
        ge=0.0,
        le=1.0,
    )
    run_continue_as_new_step_threshold: int = Field(
        500,
        validation_alias="TEMPORAL_RUN_CONTINUE_AS_NEW_STEP_THRESHOLD",
        ge=1,
    )
    run_continue_as_new_wait_cycle_threshold: int = Field(
        200,
        validation_alias="TEMPORAL_RUN_CONTINUE_AS_NEW_WAIT_CYCLE_THRESHOLD",
        ge=1,
    )
    manifest_continue_as_new_phase_threshold: int = Field(
        5,
        validation_alias="TEMPORAL_MANIFEST_CONTINUE_AS_NEW_PHASE_THRESHOLD",
        ge=1,
    )

    model_config = SettingsConfigDict(
        populate_by_name=True,
        env_prefix="",
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator("worker_fleet", mode="before")
    @classmethod
    def _normalize_worker_fleet(cls, value: Any) -> str:
        normalized = str(value or "workflow").strip().lower()
        allowed = {
            "workflow",
            "artifacts",
            "llm",
            "sandbox",
            "integrations",
            "agent_runtime",
        }
        if normalized not in allowed:
            raise ValueError(
                "TEMPORAL_WORKER_FLEET must be one of "
                "workflow, artifacts, llm, sandbox, integrations, agent_runtime"
            )
        return normalized


class TemporalDashboardSettings(BaseSettings):
    """Task-dashboard Temporal source contract and rollout flags."""

    enabled: bool = Field(
        True,
        validation_alias=AliasChoices("TEMPORAL_DASHBOARD_ENABLED"),
    )
    list_enabled: bool = Field(
        True,
        validation_alias=AliasChoices("TEMPORAL_DASHBOARD_LIST_ENABLED"),
    )
    detail_enabled: bool = Field(
        True,
        validation_alias=AliasChoices("TEMPORAL_DASHBOARD_DETAIL_ENABLED"),
    )
    actions_enabled: bool = Field(
        True,
        validation_alias=AliasChoices("TEMPORAL_DASHBOARD_ACTIONS_ENABLED"),
    )
    submit_enabled: bool = Field(
        True,
        validation_alias=AliasChoices("TEMPORAL_DASHBOARD_SUBMIT_ENABLED"),
    )
    debug_fields_enabled: bool = Field(
        False,
        validation_alias=AliasChoices("TEMPORAL_DASHBOARD_DEBUG_FIELDS_ENABLED"),
    )
    list_endpoint: str = Field(
        "/api/executions",
        validation_alias=AliasChoices("TEMPORAL_DASHBOARD_LIST_ENDPOINT"),
    )
    create_endpoint: str = Field(
        "/api/executions",
        validation_alias=AliasChoices("TEMPORAL_DASHBOARD_CREATE_ENDPOINT"),
    )
    detail_endpoint: str = Field(
        "/api/executions/{workflowId}",
        validation_alias=AliasChoices("TEMPORAL_DASHBOARD_DETAIL_ENDPOINT"),
    )
    update_endpoint: str = Field(
        "/api/executions/{workflowId}/update",
        validation_alias=AliasChoices("TEMPORAL_DASHBOARD_UPDATE_ENDPOINT"),
    )
    signal_endpoint: str = Field(
        "/api/executions/{workflowId}/signal",
        validation_alias=AliasChoices("TEMPORAL_DASHBOARD_SIGNAL_ENDPOINT"),
    )
    cancel_endpoint: str = Field(
        "/api/executions/{workflowId}/cancel",
        validation_alias=AliasChoices("TEMPORAL_DASHBOARD_CANCEL_ENDPOINT"),
    )
    artifacts_endpoint: str = Field(
        "/api/executions/{namespace}/{workflowId}/{temporalRunId}/artifacts",
        validation_alias=AliasChoices("TEMPORAL_DASHBOARD_ARTIFACTS_ENDPOINT"),
    )
    artifact_create_endpoint: str = Field(
        "/api/artifacts",
        validation_alias=AliasChoices("TEMPORAL_DASHBOARD_ARTIFACT_CREATE_ENDPOINT"),
    )
    artifact_metadata_endpoint: str = Field(
        "/api/artifacts/{artifactId}",
        validation_alias=AliasChoices("TEMPORAL_DASHBOARD_ARTIFACT_METADATA_ENDPOINT"),
    )
    artifact_presign_download_endpoint: str = Field(
        "/api/artifacts/{artifactId}/presign-download",
        validation_alias=AliasChoices(
            "TEMPORAL_DASHBOARD_ARTIFACT_PRESIGN_DOWNLOAD_ENDPOINT"
        ),
    )
    artifact_download_endpoint: str = Field(
        "/api/artifacts/{artifactId}/download",
        validation_alias=AliasChoices("TEMPORAL_DASHBOARD_ARTIFACT_DOWNLOAD_ENDPOINT"),
    )

    model_config = SettingsConfigDict(
        populate_by_name=True,
        env_prefix="",
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )


class WorkflowSettings(BaseSettings):
    """Settings specific to workflow automation."""

    repo_root: str = Field(
        ".",
        validation_alias=AliasChoices("WORKFLOW_REPO_ROOT"),
    )
    tasks_root: str = Field(
        "specs",
        validation_alias=AliasChoices("WORKFLOW_TASKS_ROOT", "WORKFLOW_TASKS_ROOT"),
    )
    recurring_dispatch_engine: str = Field(
        "app",
        validation_alias=AliasChoices("RECURRING_DISPATCH_ENGINE"),
        description="Engine for recurring schedules: 'app', 'temporal', or 'dual'",
    )
    artifacts_root: str = Field(
        "var/artifacts/workflows",
        validation_alias=AliasChoices(
            "WORKFLOW_ARTIFACT_ROOT",
            "WORKFLOW_ARTIFACTS_ROOT",
            "WORKFLOW_ARTIFACT_ROOT",
            "WORKFLOW_ARTIFACTS_ROOT",
        ),
        description="Filesystem location where Spec workflow artifacts are persisted.",
    )
    agent_job_artifact_root: str = Field(
        "var/artifacts/agent_jobs",
        alias="AGENT_JOB_ARTIFACT_ROOT",
        description="Filesystem location where agent queue artifacts are persisted.",
    )
    temporal_artifact_root: str = Field(
        "var/artifacts/temporal_artifacts",
        validation_alias=AliasChoices(
            "TEMPORAL_ARTIFACT_ROOT",
            "TEMPORAL_ARTIFACTS_ROOT",
        ),
        description="Filesystem location where local-dev Temporal artifacts are persisted.",
    )
    temporal_artifact_backend: str = Field(
        "s3",
        validation_alias=AliasChoices("TEMPORAL_ARTIFACT_BACKEND"),
        description="Artifact blob backend for Temporal runtime (s3 or local_fs).",
    )
    temporal_artifact_s3_endpoint: str = Field(
        "http://minio:9000",
        validation_alias=AliasChoices(
            "TEMPORAL_ARTIFACT_S3_ENDPOINT", "MINIO_ENDPOINT"
        ),
        description="S3-compatible endpoint used by Temporal artifact storage.",
    )
    temporal_artifact_s3_bucket: str = Field(
        "moonmind-temporal-artifacts",
        validation_alias=AliasChoices("TEMPORAL_ARTIFACT_S3_BUCKET", "MINIO_BUCKET"),
        description="S3 bucket used to persist Temporal artifact bytes.",
    )
    temporal_artifact_s3_access_key_id: str = Field(
        "minioadmin",
        validation_alias=AliasChoices(
            "TEMPORAL_ARTIFACT_S3_ACCESS_KEY_ID",
            "MINIO_ACCESS_KEY",
        ),
        description="Access key for the Temporal artifact S3-compatible backend.",
    )
    temporal_artifact_s3_secret_access_key: str = Field(
        "minioadmin",
        validation_alias=AliasChoices(
            "TEMPORAL_ARTIFACT_S3_SECRET_ACCESS_KEY",
            "MINIO_SECRET_KEY",
        ),
        description="Secret key for the Temporal artifact S3-compatible backend.",
    )
    temporal_artifact_s3_region: str = Field(
        "us-east-1",
        validation_alias=AliasChoices("TEMPORAL_ARTIFACT_S3_REGION"),
        description="Region hint for S3-compatible artifact backend.",
    )
    temporal_artifact_s3_use_ssl: bool = Field(
        False,
        validation_alias=AliasChoices("TEMPORAL_ARTIFACT_S3_USE_SSL"),
        description="Whether S3-compatible artifact endpoint requires TLS.",
    )
    temporal_artifact_default_namespace: str = Field(
        "moonmind",
        validation_alias=AliasChoices("TEMPORAL_NAMESPACE"),
        description="Default namespace prefix used for Temporal artifact storage keys.",
    )
    temporal_artifact_presign_ttl_seconds: int = Field(
        15 * 60,
        validation_alias=AliasChoices("TEMPORAL_ARTIFACT_PRESIGN_TTL_SECONDS"),
        description="TTL in seconds for local-dev artifact download/upload URL hints.",
        ge=1,
    )
    temporal_artifact_direct_upload_max_bytes: int = Field(
        10 * 1024 * 1024,
        validation_alias=AliasChoices("TEMPORAL_ARTIFACT_DIRECT_UPLOAD_MAX_BYTES"),
        description="Maximum local-dev direct upload payload size in bytes.",
        gt=0,
    )
    temporal_artifact_lifecycle_hard_delete_after_seconds: int = Field(
        3600,
        validation_alias=AliasChoices(
            "TEMPORAL_ARTIFACT_LIFECYCLE_HARD_DELETE_AFTER_SECONDS"
        ),
        description=(
            "Delay in seconds between soft-delete and hard-delete/tombstone lifecycle sweep."
        ),
        ge=0,
    )
    agent_job_artifact_max_bytes: int = Field(
        50 * 1024 * 1024,
        alias="AGENT_JOB_ARTIFACT_MAX_BYTES",
        description="Maximum allowed artifact upload size in bytes for queue jobs.",
        gt=0,
    )
    agent_job_attachment_enabled: bool = Field(
        False,
        alias="AGENT_JOB_ATTACHMENT_ENABLED",
        description="Toggle for allowing input image attachments during queue job creation.",
    )
    agent_job_attachment_max_count: int = Field(
        10,
        alias="AGENT_JOB_ATTACHMENT_MAX_COUNT",
        description="Maximum number of input attachments permitted per job.",
        ge=0,
    )
    agent_job_attachment_max_bytes: int = Field(
        10 * 1024 * 1024,
        alias="AGENT_JOB_ATTACHMENT_MAX_BYTES",
        description="Maximum size (bytes) for a single input attachment.",
        gt=0,
    )
    agent_job_attachment_total_bytes: int = Field(
        25 * 1024 * 1024,
        alias="AGENT_JOB_ATTACHMENT_TOTAL_BYTES",
        description="Maximum combined size (bytes) for all input attachments for a job.",
        gt=0,
    )
    agent_job_attachment_allowed_content_types: Annotated[tuple[str, ...], NoDecode] = (
        Field(
            ("image/png", "image/jpeg", "image/webp"),
            validation_alias=AliasChoices("AGENT_JOB_ATTACHMENT_ALLOWED_TYPES"),
            description="Allowed MIME types for input attachments.",
        )
    )
    vision_context_enabled: bool = Field(
        True,
        validation_alias=AliasChoices("MOONMIND_VISION_CONTEXT_ENABLED"),
        description="Enable attachment vision context generation during worker prepare stages.",
    )
    vision_provider: str = Field(
        "gemini_cli",
        validation_alias=AliasChoices("MOONMIND_VISION_PROVIDER"),
        description="Vision provider identifier (gemini, openai, anthropic, off).",
    )
    vision_model: str = Field(
        "models/gemini-2.5-flash",
        validation_alias=AliasChoices("MOONMIND_VISION_MODEL"),
        description="Default caption/OCR model for the configured provider.",
    )
    vision_max_tokens: int = Field(
        512,
        validation_alias=AliasChoices("MOONMIND_VISION_MAX_TOKENS"),
        ge=64,
        description="Maximum token budget allocated per vision prompt batch.",
    )
    vision_ocr_enabled: bool = Field(
        True,
        validation_alias=AliasChoices("MOONMIND_VISION_OCR_ENABLED"),
        description="Toggle OCR extraction when rendering image context.",
    )
    agent_job_max_runtime_seconds: int = Field(
        4 * 3600,
        alias="AGENT_JOB_MAX_RUNTIME_SECONDS",
        description=(
            "Maximum wall-clock runtime (seconds) a queue job may run before it is"
            " automatically timed out."
        ),
        ge=300,
    )
    agent_job_stale_lease_grace_seconds: int = Field(
        300,
        alias="AGENT_JOB_STALE_LEASE_GRACE_SECONDS",
        description=(
            "Grace period (seconds) after a lease expires before the job is reported"
            " as stale-running to operators."
        ),
        ge=60,
    )
    allow_manifest_path_source: bool = Field(
        False,
        alias="MOONMIND_ALLOW_MANIFEST_PATH_SOURCE",
        description="Allow manifest.source.kind='path' submissions (intended for dev/test images).",
    )
    manifest_required_capabilities: tuple[str, ...] = Field(
        ("manifest",),
        validation_alias=AliasChoices(
            "WORKFLOW_MANIFEST_REQUIRED_CAPABILITIES",
            "WORKFLOW_MANIFEST_REQUIRED_CAPABILITIES",
        ),
        description="Comma-delimited list of base capability labels applied to manifest jobs.",
    )
    job_image: str = Field(
        "ghcr.io/moonladderstudios/moonmind:latest",
        validation_alias=AliasChoices("WORKFLOW_JOB_IMAGE", "WORKFLOW_JOB_IMAGE"),
        description="Container image used for workflow automation job executions.",
    )
    workspace_root: str = Field(
        "/work",
        alias="WORKFLOW_WORKSPACE_ROOT",
        description="Host-mounted root directory for workflow automation workspaces.",
    )
    default_queue: str = Field(
        "moonmind.jobs",
        validation_alias=AliasChoices("WORKFLOW_DEFAULT_QUEUE"),
        description="Default queue name for workflow tasks.",
    )
    default_exchange: str = Field(
        "moonmind.jobs",
        validation_alias=AliasChoices("WORKFLOW_DEFAULT_EXCHANGE"),
        description="Default exchange for workflow tasks.",
    )
    default_routing_key: str = Field(
        "moonmind.jobs",
        validation_alias=AliasChoices("WORKFLOW_DEFAULT_ROUTING_KEY"),
        description="Default routing key used by the workflow queue.",
    )
    metrics_enabled: bool = Field(
        False,
        validation_alias=AliasChoices(
            "WORKFLOW_METRICS_ENABLED", "WORKFLOW_METRICS_ENABLED"
        ),
        description="Toggle emission of workflow automation StatsD metrics.",
    )
    metrics_host: Optional[str] = Field(
        None,
        validation_alias=AliasChoices("WORKFLOW_METRICS_HOST", "WORKFLOW_METRICS_HOST"),
        description="Hostname for the StatsD metrics sink (optional).",
    )
    metrics_port: Optional[int] = Field(
        None,
        validation_alias=AliasChoices("WORKFLOW_METRICS_PORT", "WORKFLOW_METRICS_PORT"),
        description="Port for the StatsD metrics sink (optional).",
    )
    metrics_namespace: str = Field(
        "automation",
        validation_alias=AliasChoices(
            "WORKFLOW_METRICS_NAMESPACE", "WORKFLOW_METRICS_NAMESPACE"
        ),
        description="Namespace/prefix applied to emitted workflow automation metrics.",
    )
    default_feature_key: str = Field(
        "001-workflow",
        validation_alias=AliasChoices(
            "WORKFLOW_DEFAULT_FEATURE_KEY", "WORKFLOW_DEFAULT_FEATURE_KEY"
        ),
    )
    codex_environment: Optional[str] = Field(None, alias="CODEX_ENV")
    codex_model: Optional[str] = Field(
        "gpt-5.3-codex",
        validation_alias=AliasChoices("MOONMIND_CODEX_MODEL", "CODEX_MODEL"),
    )
    codex_effort: Optional[str] = Field(
        "high",
        validation_alias=AliasChoices(
            "MOONMIND_CODEX_EFFORT",
            "CODEX_MODEL_REASONING_EFFORT",
            "MODEL_REASONING_EFFORT",
        ),
    )
    codex_profile: Optional[str] = Field(None, alias="CODEX_PROFILE")
    codex_shards: int = Field(
        3,
        alias="CODEX_SHARDS",
        description="Number of Codex worker shards available for routing.",
        gt=0,
        le=64,
    )
    codex_queue: Optional[str] = Field(
        None,
        validation_alias=AliasChoices(
            "WORKFLOW_CODEX_QUEUE",
            "CODEX_QUEUE",
        ),
        description="Explicit Codex queue name assigned to this worker.",
    )
    codex_volume_name: Optional[str] = Field(
        None,
        alias="CODEX_VOLUME_NAME",
        description="Docker volume providing persistent Codex authentication.",
    )
    claude_volume_name: Optional[str] = Field(
        None,
        alias="CLAUDE_VOLUME_NAME",
        description="Docker volume providing persistent Claude authentication.",
    )
    claude_volume_path: Optional[str] = Field(
        None,
        alias="CLAUDE_VOLUME_PATH",
        description="In-container path where Claude auth data is mounted.",
    )
    claude_home: Optional[str] = Field(
        None,
        alias="CLAUDE_HOME",
        description="Claude CLI home directory used for persisted OAuth state.",
    )
    codex_login_check_image: Optional[str] = Field(
        None,
        alias="CODEX_LOGIN_CHECK_IMAGE",
        description="Override container image for Codex login status checks.",
    )
    default_task_runtime: str = Field(
        "codex",
        validation_alias=AliasChoices(
            "WORKFLOW_DEFAULT_TASK_RUNTIME",
            "MOONMIND_DEFAULT_TASK_RUNTIME",
            "WORKFLOW_DEFAULT_TASK_RUNTIME",
        ),
        description="Fallback runtime for queue task payloads that omit runtime fields.",
    )
    default_publish_mode: str = Field(
        "pr",
        validation_alias=AliasChoices(
            "WORKFLOW_DEFAULT_PUBLISH_MODE",
            "MOONMIND_DEFAULT_PUBLISH_MODE",
            "WORKFLOW_DEFAULT_PUBLISH_MODE",
        ),
        description="Fallback publish mode applied when queue task payloads omit publish.mode.",
    )
    github_repository: Optional[str] = Field(
        "MoonLadderStudios/MoonMind",
        validation_alias=AliasChoices(
            "WORKFLOW_GITHUB_REPOSITORY",
            "WORKFLOW_GITHUB_REPOSITORY",
        ),
    )
    git_user_name: Optional[str] = Field(
        None,
        validation_alias=AliasChoices(
            "WORKFLOW_GIT_USER_NAME",
            "WORKFLOW_GIT_USER_NAME",
            "MOONMIND_GIT_USER_NAME",
        ),
        description="Optional Git author/committer display name used by worker publish stages.",
    )
    git_user_email: Optional[str] = Field(
        None,
        validation_alias=AliasChoices(
            "WORKFLOW_GIT_USER_EMAIL",
            "WORKFLOW_GIT_USER_EMAIL",
            "MOONMIND_GIT_USER_EMAIL",
        ),
        description="Optional Git author/committer email used by worker publish stages.",
    )
    github_token: Optional[str] = Field(
        None,
        validation_alias=AliasChoices("WORKFLOW_GITHUB_TOKEN", "WORKFLOW_GITHUB_TOKEN"),
    )
    test_mode: bool = Field(
        False,
        validation_alias=AliasChoices("WORKFLOW_TEST_MODE", "WORKFLOW_TEST_MODE"),
    )
    agent_backend: str = Field(
        "codex_cli",
        alias="WORKFLOW_AGENT_BACKEND",
        description="Active agent backend identifier for workflow automation runs.",
    )
    allowed_agent_backends: tuple[str, ...] = Field(
        ("codex_cli",),
        alias="WORKFLOW_ALLOWED_AGENT_BACKENDS",
        description="Whitelisted agent backend identifiers for workflow automation.",
    )
    agent_version: str = Field(
        "unspecified",
        alias="WORKFLOW_AGENT_VERSION",
        description="Version string recorded with the agent configuration snapshot.",
    )
    prompt_pack_version: Optional[str] = Field(
        None,
        alias="WORKFLOW_PROMPT_PACK_VERSION",
        description="Prompt pack version associated with the selected agent.",
    )
    skills_enabled: bool = Field(
        True,
        validation_alias=AliasChoices("WORKFLOW_USE_SKILLS", "WORKFLOW_USE_SKILLS"),
        description="Enable skills-first orchestration policy for workflow stages.",
    )
    skills_shadow_mode: bool = Field(
        False,
        validation_alias=AliasChoices(
            "WORKFLOW_SKILLS_SHADOW_MODE", "WORKFLOW_SKILLS_SHADOW_MODE"
        ),
        description="Enable shadow-mode telemetry for skills orchestration.",
    )
    skills_fallback_enabled: bool = Field(
        True,
        validation_alias=AliasChoices(
            "WORKFLOW_SKILLS_FALLBACK_ENABLED", "WORKFLOW_SKILLS_FALLBACK_ENABLED"
        ),
        description="Allow direct stage fallback when skill adapters fail.",
    )
    skills_canary_percent: int = Field(
        100,
        validation_alias=AliasChoices(
            "WORKFLOW_SKILLS_CANARY_PERCENT", "WORKFLOW_SKILLS_CANARY_PERCENT"
        ),
        description="Percentage of runs routed through skills-first policy (0-100).",
        ge=0,
        le=100,
    )
    default_skill: str = Field(
        "auto",
        validation_alias=AliasChoices(
            "WORKFLOW_DEFAULT_SKILL",
            "WORKFLOW_DEFAULT_SKILL",
            "MOONMIND_DEFAULT_SKILL",
        ),
        description="Default skill identifier for workflow stage execution.",
    )
    discover_skill: Optional[str] = Field(
        None,
        validation_alias=AliasChoices(
            "WORKFLOW_DISCOVER_SKILL",
            "WORKFLOW_DISCOVER_SKILL",
            "MOONMIND_DISCOVER_SKILL",
        ),
        description="Optional skill override for discovery stage.",
    )
    submit_skill: Optional[str] = Field(
        None,
        validation_alias=AliasChoices(
            "WORKFLOW_SUBMIT_SKILL",
            "WORKFLOW_SUBMIT_SKILL",
            "MOONMIND_SUBMIT_SKILL",
        ),
        description="Optional skill override for submit stage.",
    )
    publish_skill: Optional[str] = Field(
        None,
        validation_alias=AliasChoices(
            "WORKFLOW_PUBLISH_SKILL",
            "WORKFLOW_PUBLISH_SKILL",
            "MOONMIND_PUBLISH_SKILL",
        ),
        description="Optional skill override for publish stage.",
    )
    skill_policy_mode: str = Field(
        "permissive",
        validation_alias=AliasChoices(
            "WORKFLOW_SKILL_POLICY_MODE",
            "WORKFLOW_SKILL_POLICY_MODE",
            "MOONMIND_SKILL_POLICY_MODE",
            "SKILL_POLICY_MODE",
        ),
        description="Skill policy mode. 'permissive' allows any resolvable skill; 'allowlist' enforces allowed skills list.",
    )
    allowed_skills: Annotated[tuple[str, ...], NoDecode] = Field(
        ("auto",),
        validation_alias=AliasChoices(
            "WORKFLOW_ALLOWED_SKILLS",
            "WORKFLOW_ALLOWED_SKILLS",
            "MOONMIND_ALLOWED_SKILLS",
        ),
        description="Allowlisted skills that can be selected for workflow stages.",
    )
    skills_cache_root: str = Field(
        "var/skill_cache",
        validation_alias=AliasChoices("WORKFLOW_SKILLS_CACHE_ROOT"),
        description="Immutable cache root for verified skill artifacts.",
    )
    skills_workspace_root: str = Field(
        "runs",
        validation_alias=AliasChoices("WORKFLOW_SKILLS_WORKSPACE_ROOT"),
        description="Workspace subdirectory (under WORKFLOW_WORKSPACE_ROOT) for per-run active skills links.",
    )
    skills_registry_source: Optional[str] = Field(
        None,
        validation_alias=AliasChoices("WORKFLOW_SKILLS_REGISTRY_SOURCE"),
        description="Optional registry profile/URI for skill source resolution.",
    )
    skills_local_mirror_root: str = Field(
        ".agents/skills/local",
        validation_alias=AliasChoices("WORKFLOW_SKILLS_LOCAL_MIRROR_ROOT"),
        description="Default local-only skill mirror directory used for source resolution.",
    )
    skills_legacy_mirror_root: str = Field(
        ".agents/skills",
        validation_alias=AliasChoices("WORKFLOW_SKILLS_LEGACY_MIRROR_ROOT"),
        description=(
            "Secondary shared mirror root checked after local-only skills; "
            "nested '<root>/skills' is auto-detected for compatibility."
        ),
    )
    skills_verify_signatures: bool = Field(
        False,
        validation_alias=AliasChoices("WORKFLOW_SKILLS_VERIFY_SIGNATURES"),
        description="Require signature metadata for selected skills before activation.",
    )
    skills_validate_local_mirror: bool = Field(
        False,
        validation_alias=AliasChoices("WORKFLOW_SKILLS_VALIDATE_LOCAL_MIRROR"),
        description="Enable startup validation of the configured local skill mirror root.",
    )
    live_session_enabled_default: bool = Field(
        True,
        validation_alias=AliasChoices("MOONMIND_LIVE_SESSION_ENABLED_DEFAULT"),
        description="Enable live task sessions by default for queue task runs.",
    )
    live_session_provider: str = Field(
        "tmate",
        validation_alias=AliasChoices("MOONMIND_LIVE_SESSION_PROVIDER"),
        description="Live session provider implementation.",
    )
    live_session_ttl_minutes: int = Field(
        60,
        validation_alias=AliasChoices("MOONMIND_LIVE_SESSION_TTL_MINUTES"),
        description="Default live session lifetime before automatic revocation.",
        ge=1,
        le=1440,
    )
    live_session_rw_grant_ttl_minutes: int = Field(
        15,
        validation_alias=AliasChoices("MOONMIND_LIVE_SESSION_RW_GRANT_TTL_MINUTES"),
        description="Default RW reveal grant duration for live sessions.",
        ge=1,
        le=240,
    )
    live_session_allow_web: bool = Field(
        False,
        validation_alias=AliasChoices("MOONMIND_LIVE_SESSION_ALLOW_WEB"),
        description="Whether tmate web attach URLs are exposed via API responses.",
    )
    tmate_server_host: Optional[str] = Field(
        None,
        validation_alias=AliasChoices("MOONMIND_TMATE_SERVER_HOST"),
        description="Optional self-hosted tmate relay hostname.",
    )
    live_session_max_concurrent_per_worker: int = Field(
        4,
        validation_alias=AliasChoices(
            "MOONMIND_LIVE_SESSION_MAX_CONCURRENT_PER_WORKER"
        ),
        description="Maximum concurrent live sessions each worker should provision.",
        ge=1,
        le=64,
    )
    enable_task_proposals: bool = Field(
        True,
        validation_alias=AliasChoices(
            "MOONMIND_ENABLE_TASK_PROPOSALS",
            "ENABLE_TASK_PROPOSALS",
        ),
        description="Enable worker-side task proposal submission after successful runs.",
    )
    proposal_targets_default: str = Field(
        "project",
        validation_alias=AliasChoices(
            "MOONMIND_PROPOSAL_TARGETS", "TASK_PROPOSALS_TARGETS_DEFAULT"
        ),
        description="Default proposal targets when tasks omit proposalPolicy (project|moonmind|both).",
    )
    proposal_max_items_project: int = Field(
        3,
        validation_alias=AliasChoices("TASK_PROPOSALS_MAX_ITEMS_PROJECT"),
        description="Default per-run project proposal cap applied when task policy omits maxItems.project.",
        ge=1,
    )
    proposal_max_items_moonmind: int = Field(
        2,
        validation_alias=AliasChoices("TASK_PROPOSALS_MAX_ITEMS_MOONMIND"),
        description="Default per-run MoonMind proposal cap applied when task policy omits maxItems.moonmind.",
        ge=1,
    )
    proposal_moonmind_severity_floor: str = Field(
        "high",
        validation_alias=AliasChoices(
            "MOONMIND_MIN_SEVERITY_FOR_MOONMIND",
            "TASK_PROPOSALS_MIN_SEVERITY_FOR_MOONMIND",
        ),
        description="Lowest accepted severity for MoonMind CI proposals when policy omits a floor.",
    )
    moonmind_ci_repository: str = Field(
        "MoonLadderStudios/MoonMind",
        validation_alias=AliasChoices(
            "MOONMIND_CI_REPOSITORY", "TASK_PROPOSALS_MOONMIND_CI_REPOSITORY"
        ),
        description="Repository used for MoonMind CI/run-quality proposals.",
    )
    stage_command_timeout_seconds: int = Field(
        3600,
        validation_alias=AliasChoices(
            "WORKFLOW_STAGE_COMMAND_TIMEOUT_SECONDS",
            "MOONMIND_STAGE_COMMAND_TIMEOUT_SECONDS",
            "WORKFLOW_STAGE_COMMAND_TIMEOUT_SECONDS",
        ),
        description="Hard timeout for non-container worker stage commands.",
        ge=1,
    )

    model_config = SettingsConfigDict(
        populate_by_name=True,
        env_prefix="",
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator("manifest_required_capabilities", mode="before")
    @classmethod
    def _split_manifest_capabilities(
        cls, value: Optional[str | Sequence[str]]
    ) -> tuple[str, ...] | None:
        """Allow comma-delimited strings for manifest capability flags."""

        if value is None:
            return None

        if isinstance(value, str):
            raw_items: Sequence[object] = value.split(",")
        elif isinstance(value, Sequence) and not isinstance(
            value, (bytes, bytearray, str)
        ):
            raw_items = value
        else:
            return value

        tokens = [str(item).strip() for item in raw_items if str(item).strip()]
        if not tokens:
            return ()
        return tuple(dict.fromkeys(tokens))

    @field_validator("temporal_artifact_backend", mode="before")
    @classmethod
    def _normalize_temporal_artifact_backend(cls, value: object) -> str:
        backend = str(value or "").strip().lower()
        if not backend:
            return "s3"
        if backend not in {"s3", "local_fs"}:
            raise ValueError(
                "workflow.temporal_artifact_backend must be one of: s3, local_fs"
            )
        return backend

    @field_validator("proposal_targets_default", mode="before")
    @classmethod
    def _normalize_proposal_targets_default(cls, value: object) -> str:
        text = str(value or "").strip().lower()
        if not text:
            return "project"
        if text not in _ALLOWED_TARGET_DEFAULTS:
            allowed = ", ".join(_ALLOWED_TARGET_DEFAULTS)
            raise ValueError(
                f"workflow.proposal_targets_default must be one of: {allowed}"
            )
        return text

    @field_validator("proposal_moonmind_severity_floor", mode="before")
    @classmethod
    def _normalize_proposal_severity_floor(cls, value: object) -> str:
        text = str(value or "").strip().lower()
        if not text:
            return "high"
        if text not in _ALLOWED_PROPOSAL_SEVERITIES:
            allowed = ", ".join(_ALLOWED_PROPOSAL_SEVERITIES)
            raise ValueError(
                f"workflow.proposal_moonmind_severity_floor must be one of: {allowed}"
            )
        return text

    @field_validator("moonmind_ci_repository", mode="before")
    @classmethod
    def _normalize_moonmind_ci_repository(cls, value: object) -> str:
        text = str(value or "").strip()
        if not text:
            return "MoonLadderStudios/MoonMind"
        return text

    @field_validator(
        "metrics_host",
        "codex_environment",
        "codex_model",
        "codex_effort",
        "codex_profile",
        "codex_queue",
        "codex_volume_name",
        "claude_volume_name",
        "claude_volume_path",
        "claude_home",
        "codex_login_check_image",
        "git_user_name",
        "git_user_email",
        "default_skill",
        "discover_skill",
        "submit_skill",
        "publish_skill",
        "skills_registry_source",
        "live_session_provider",
        "tmate_server_host",
        mode="before",
    )
    @classmethod
    def _strip_and_blank_to_none(cls, value: Optional[str]) -> Optional[str]:
        """Normalize optional string settings by stripping whitespace and treating blanks as ``None``."""

        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value

    @field_validator("allowed_agent_backends", mode="before")
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

    @field_validator("allowed_skills", mode="before")
    @classmethod
    def _split_allowed_skills(
        cls, value: Optional[str | Sequence[str]]
    ) -> tuple[str, ...]:
        """Allow comma-delimited strings for skill allowlists."""

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
        return tuple(dict.fromkeys(items))

    @field_validator("agent_job_attachment_allowed_content_types", mode="before")
    @classmethod
    def _normalize_attachment_types(
        cls, value: Optional[str | Sequence[str]]
    ) -> tuple[str, ...]:
        """Normalize attachment content-type configuration."""

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

        normalized: list[str] = []
        for item in raw_items:
            text = str(item or "").strip()
            if text:
                normalized.append(text)
        return tuple(dict.fromkeys(normalized))

    @field_validator("vision_provider", mode="before")
    @classmethod
    def _normalize_vision_provider(cls, value: object) -> str:
        candidate = str(value or "").strip().lower() or "gemini_cli"
        allowed = {"gemini_cli", "openai", "anthropic", "off"}
        if candidate not in allowed:
            supported = ", ".join(sorted(allowed))
            raise ValueError(f"MOONMIND_VISION_PROVIDER must be one of: {supported}")
        return candidate

    @field_validator("vision_model", mode="before")
    @classmethod
    def _normalize_vision_model(cls, value: object) -> str:
        return str(value or "").strip() or "models/gemini-2.5-flash"

    @field_validator("skill_policy_mode", mode="before")
    @classmethod
    def _normalize_skill_policy_mode(cls, value: object) -> str:
        """Normalize skill policy mode and reject unknown values."""

        normalized = str(value or "").strip().lower() or "permissive"
        if normalized not in {"permissive", "allowlist"}:
            raise ValueError("skill_policy_mode must be one of: permissive, allowlist")
        return normalized

    @field_validator("default_task_runtime", mode="before")
    @classmethod
    def _normalize_default_task_runtime(cls, value: object) -> str:
        """Normalize queue runtime fallback and reject unknown values."""

        normalized = str(value or "").strip().lower() or "codex"
        allowed = {"codex", "gemini_cli", "claude", "jules"}
        if normalized not in allowed:
            supported = ", ".join(sorted(allowed))
            raise ValueError(f"default_task_runtime must be one of: {supported}")
        return normalized

    @field_validator("default_publish_mode", mode="before")
    @classmethod
    def _normalize_default_publish_mode(cls, value: object) -> str:
        """Normalize default publish mode and reject unsupported values."""

        normalized = str(value or "").strip().lower() or "pr"
        allowed = {"none", "branch", "pr"}
        if normalized not in allowed:
            supported = ", ".join(sorted(allowed))
            raise ValueError(f"default_publish_mode must be one of: {supported}")
        return normalized

    @field_validator("live_session_provider", mode="before")
    @classmethod
    def _normalize_live_session_provider(cls, value: object) -> str:
        """Normalize live session provider and reject unknown values."""

        normalized = str(value or "").strip().lower() or "tmate"
        if normalized not in {"tmate"}:
            raise ValueError("live_session_provider must be one of: tmate")
        return normalized

    @field_validator("github_repository", mode="before")
    @classmethod
    def _normalize_github_repository(cls, value: object) -> str:
        """Normalize default workflow repository and reject unsafe references."""

        normalized = str(value or "").strip() or "MoonLadderStudios/MoonMind"
        if _OWNER_REPO_PATTERN.fullmatch(normalized):
            return normalized
        if normalized.startswith("http://") or normalized.startswith("https://"):
            parsed = urlsplit(normalized)
            if parsed.username is not None or parsed.password is not None:
                raise ValueError(
                    "github_repository URL must not include embedded credentials"
                )
            if not parsed.netloc or not parsed.path or parsed.path == "/":
                raise ValueError(
                    "github_repository URL must include a host and repository path"
                )
            return normalized
        if normalized.startswith("git@"):
            return normalized
        raise ValueError(
            "github_repository must be owner/repo, https://<host>/<path>, or git@<host>:<path>"
        )

    def model_post_init(self, __context: Any) -> None:  # type: ignore[override]
        """Validate agent backend selections after settings load."""

        super().model_post_init(__context)
        # Ensure tuples are deduplicated even when env provided sequences
        allowed = tuple(dict.fromkeys(self.allowed_agent_backends or ()))
        self.allowed_agent_backends = allowed
        self.allowed_skills = tuple(dict.fromkeys(self.allowed_skills or ()))

        for attr in (
            "default_skill",
            "discover_skill",
            "submit_skill",
            "publish_skill",
        ):
            value = getattr(self, attr)
            if isinstance(value, str):
                normalized = value.strip()
                setattr(self, attr, normalized or None)

        if not self.default_skill:
            self.default_skill = "auto"
        if (
            self.skill_policy_mode == "allowlist"
            and self.default_skill
            and self.default_skill not in self.allowed_skills
        ):
            self.allowed_skills = tuple(
                dict.fromkeys((*self.allowed_skills, self.default_skill))
            )
        if not self.codex_model:
            self.codex_model = "gpt-5.3-codex"
        if not self.codex_effort:
            self.codex_effort = "high"
        if not self.claude_volume_name:
            self.claude_volume_name = "claude_auth_volume"
        if not self.claude_volume_path:
            self.claude_volume_path = "/home/app/.claude"
        if not self.claude_home:
            self.claude_home = self.claude_volume_path
        if not self.github_repository:
            self.github_repository = "MoonLadderStudios/MoonMind"

        if allowed and self.agent_backend not in allowed:
            allowed_display = ", ".join(allowed)
            raise ValueError(
                f"Agent backend '{self.agent_backend}' is not permitted. "
                f"Allowed values: {allowed_display or '<none>'}"
            )


# Workflow overrides rely on pydantic ``env`` fallbacks and
# ``AppSettings.model_post_init`` to populate sensible defaults.


class AppWorkflowSettings(WorkflowSettings):
    """App-level variant used by `AppSettings` to avoid legacy alias overrides."""

    github_repository: Optional[str] = Field(
        "MoonLadderStudios/MoonMind",
        validation_alias=AliasChoices("WORKFLOW_GITHUB_REPOSITORY"),
    )


class SecuritySettings(BaseSettings):
    """Security settings"""

    JWT_SECRET_KEY: Optional[str] = Field(
        "test_jwt_secret_key", alias="JWT_SECRET_KEY"
    )  # Made Optional and added default
    ENCRYPTION_MASTER_KEY: Optional[str] = Field(
        "test_encryption_master_key", alias="ENCRYPTION_MASTER_KEY"
    )  # Made Optional and added default

    model_config = SettingsConfigDict(populate_by_name=True, env_prefix="")


DEFAULT_GOOGLE_EMBEDDING_DIMENSIONS: int = 3072


class GoogleSettings(BaseSettings):
    """Google/Gemini API settings"""

    google_api_key: Optional[str] = Field(
        None,
        validation_alias=AliasChoices(
            "google_api_key", "GOOGLE_API_KEY", "GEMINI_API_KEY"
        ),
    )
    google_chat_model: str = Field("gemini-3.1-pro", alias="GOOGLE_CHAT_MODEL")
    google_embedding_model: str = Field(
        "gemini-embedding-2-preview", alias="GOOGLE_EMBEDDING_MODEL"
    )
    google_embedding_dimensions: int = Field(
        DEFAULT_GOOGLE_EMBEDDING_DIMENSIONS, alias="GOOGLE_EMBEDDING_DIMENSIONS"
    )
    google_enabled: bool = Field(True, alias="GOOGLE_ENABLED")
    google_embed_batch_size: int = Field(100, alias="GOOGLE_EMBED_BATCH_SIZE")
    # google_application_credentials has been moved to GoogleDriveSettings as per requirements

    model_config = SettingsConfigDict(populate_by_name=True, env_prefix="")


class AnthropicSettings(BaseSettings):
    """Anthropic API settings"""

    anthropic_api_key: Optional[str] = Field(
        None,
        validation_alias=AliasChoices(
            "anthropic_api_key", "ANTHROPIC_API_KEY", "CLAUDE_API_KEY"
        ),
    )
    anthropic_chat_model: str = Field("claude-sonnet-4-6", alias="ANTHROPIC_CHAT_MODEL")
    anthropic_enabled: bool = Field(True, alias="ANTHROPIC_ENABLED")

    model_config = SettingsConfigDict(populate_by_name=True, env_prefix="")


class GitHubSettings(BaseSettings):
    """GitHub settings"""

    github_token: Optional[str] = Field(None, alias="GITHUB_TOKEN")
    github_repos: Optional[str] = Field(
        None, alias="GITHUB_REPOS"
    )  # Comma-delimited string of repositories
    github_enabled: bool = Field(True, alias="GITHUB_ENABLED")

    model_config = SettingsConfigDict(populate_by_name=True, env_prefix="")


class GoogleDriveSettings(BaseSettings):
    """Google Drive settings"""

    google_drive_enabled: bool = Field(False, alias="GOOGLE_DRIVE_ENABLED")
    google_drive_folder_id: Optional[str] = Field(None, alias="GOOGLE_DRIVE_FOLDER_ID")
    google_application_credentials: Optional[str] = Field(
        None, alias="GOOGLE_APPLICATION_CREDENTIALS"
    )

    model_config = SettingsConfigDict(populate_by_name=True, env_prefix="")


class OpenAISettings(BaseSettings):
    """OpenAI API settings"""

    openai_api_key: Optional[str] = Field(None, alias="OPENAI_API_KEY")
    openai_chat_model: str = Field("gpt-3.5-turbo", alias="OPENAI_CHAT_MODEL")
    openai_embedding_model: str = Field(
        "text-embedding-3-small", alias="OPENAI_EMBEDDING_MODEL"
    )
    openai_embedding_dimensions: int = Field(1536, alias="OPENAI_EMBEDDING_DIMENSIONS")
    openai_enabled: bool = Field(True, alias="OPENAI_ENABLED")

    model_config = SettingsConfigDict(populate_by_name=True, env_prefix="")


class OllamaSettings(BaseSettings):
    """Ollama settings"""

    ollama_base_url: str = Field("http://ollama:11434", alias="OLLAMA_BASE_URL")
    ollama_embedding_model: str = Field(
        "hf.co/tensorblock/gte-Qwen2-7B-instruct-GGUF:Q6_K",
        alias="OLLAMA_EMBEDDING_MODEL",
    )
    ollama_embeddings_dimensions: int = Field(
        3584, alias="OLLAMA_EMBEDDINGS_DIMENSIONS"
    )
    ollama_keep_alive: str = Field("-1m", alias="OLLAMA_KEEP_ALIVE")
    ollama_chat_model: str = Field("devstral:24b", alias="OLLAMA_CHAT_MODEL")
    ollama_modes: str = Field("chat", alias="OLLAMA_MODES")
    ollama_enabled: bool = Field(True, alias="OLLAMA_ENABLED")

    model_config = SettingsConfigDict(populate_by_name=True, env_prefix="")


class ConfluenceSettings(BaseSettings):
    """Confluence specific settings"""

    confluence_space_keys: Optional[str] = Field(
        None, alias="ATLASSIAN_CONFLUENCE_SPACE_KEYS"
    )
    confluence_enabled: bool = Field(False, alias="ATLASSIAN_CONFLUENCE_ENABLED")

    model_config = SettingsConfigDict(
        populate_by_name=True,
        env_prefix="",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


class JiraSettings(BaseSettings):
    """Jira specific settings"""

    jira_jql_query: Optional[str] = Field(None, alias="ATLASSIAN_JIRA_JQL_QUERY")
    jira_fetch_batch_size: int = Field(50, alias="ATLASSIAN_JIRA_FETCH_BATCH_SIZE")
    jira_enabled: bool = Field(False, alias="ATLASSIAN_JIRA_ENABLED")

    model_config = SettingsConfigDict(
        populate_by_name=True,
        env_prefix="",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


class AtlassianSettings(BaseSettings):
    """Atlassian base settings"""

    atlassian_api_key: Optional[str] = Field(None, alias="ATLASSIAN_API_KEY")
    atlassian_username: Optional[str] = Field(None, alias="ATLASSIAN_USERNAME")
    atlassian_url: Optional[str] = Field(None, alias="ATLASSIAN_URL")

    # Nested settings for Confluence and Jira
    confluence: ConfluenceSettings = Field(default_factory=ConfluenceSettings)
    jira: JiraSettings = Field(default_factory=JiraSettings)

    model_config = SettingsConfigDict(
        populate_by_name=True,
        env_prefix="",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
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

    qdrant_host: str = Field("qdrant", alias="QDRANT_HOST")
    qdrant_port: int = Field(6333, alias="QDRANT_PORT")
    qdrant_api_key: Optional[str] = Field(None, alias="QDRANT_API_KEY")
    qdrant_enabled: bool = Field(True, alias="QDRANT_ENABLED")
    model_config = SettingsConfigDict(populate_by_name=True, env_prefix="")


class RAGSettings(BaseSettings):
    """RAG (Retrieval-Augmented Generation) settings"""

    rag_enabled: bool = Field(True, alias="RAG_ENABLED")
    similarity_top_k: int = Field(5, alias="RAG_SIMILARITY_TOP_K")
    max_context_length_chars: int = Field(8000, alias="RAG_MAX_CONTEXT_LENGTH_CHARS")

    model_config = SettingsConfigDict(populate_by_name=True, env_prefix="")


class LocalDataSettings(BaseSettings):
    """Settings for local data indexing"""

    local_data_path: Optional[str] = Field(
        None,
        validation_alias=AliasChoices("LocalData", "LOCAL_DATA_PATH"),
    )
    # Add local_data_enabled if we want a separate boolean flag, but for now, path presence implies enabled.
    # local_data_enabled: bool = Field(False, alias="LOCAL_DATA_ENABLED")

    model_config = SettingsConfigDict(
        populate_by_name=True,
        env_prefix="",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


class OIDCSettings(BaseSettings):
    """OIDC settings"""

    AUTH_PROVIDER: str = Field(
        "disabled",
        description="Authentication provider: 'disabled' or 'keycloak'.",
        alias="AUTH_PROVIDER",
    )
    OIDC_ISSUER_URL: Optional[str] = Field(
        None,
        alias="OIDC_ISSUER_URL",
        description="URL of the OIDC provider, e.g., Keycloak.",
    )
    OIDC_CLIENT_ID: Optional[str] = Field(None, alias="OIDC_CLIENT_ID")
    OIDC_CLIENT_SECRET: Optional[str] = Field(None, alias="OIDC_CLIENT_SECRET")
    DEFAULT_USER_ID: Optional[str] = Field(
        None,
        alias="DEFAULT_USER_ID",
        description="Default user ID for 'disabled' auth_provider mode.",
    )
    DEFAULT_USER_EMAIL: Optional[str] = Field(
        None,
        alias="DEFAULT_USER_EMAIL",
        description="Default user email for 'disabled' auth_provider mode.",
    )
    DEFAULT_USER_PASSWORD: Optional[str] = Field(
        "default_password_please_change",
        alias="DEFAULT_USER_PASSWORD",
        description="Default user password for 'disabled' auth_provider mode. Used for user creation if needed.",
    )

    model_config = SettingsConfigDict(populate_by_name=True, env_prefix="")


class FeatureFlagsSettings(BaseSettings):
    """Feature flag toggles for runtime surfaces."""

    task_template_catalog: bool = Field(
        True,
        validation_alias=AliasChoices(
            "FEATURE_FLAGS__TASK_TEMPLATE_CATALOG",
            "TASK_TEMPLATE_CATALOG",
        ),
        description=(
            "Legacy enable toggle for task preset catalog endpoints + UI wiring. "
            "Defaults on."
        ),
    )
    disable_task_template_catalog: bool = Field(
        False,
        validation_alias=AliasChoices(
            "FEATURE_FLAGS__DISABLE_TASK_TEMPLATE_CATALOG",
            "DISABLE_TASK_TEMPLATE_CATALOG",
        ),
        description="Disable task preset catalog endpoints + UI wiring.",
    )

    @property
    def task_template_catalog_enabled(self) -> bool:
        """Return whether task presets should be exposed for this runtime."""

        return bool(
            self.task_template_catalog and not self.disable_task_template_catalog
        )

    model_config = SettingsConfigDict(
        populate_by_name=True,
        env_prefix="FEATURE_FLAGS__",
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )


class TaskProposalSettings(BaseSettings):
    """Task proposal queue runtime knobs."""

    proposal_targets_default: str = Field(
        "project",
        validation_alias=AliasChoices(
            "MOONMIND_PROPOSAL_TARGETS", "TASK_PROPOSALS_TARGETS_DEFAULT"
        ),
        description="Default proposal targets when policy overrides are absent (project|moonmind|both).",
    )
    moonmind_ci_repository: str = Field(
        "MoonLadderStudios/MoonMind",
        validation_alias=AliasChoices(
            "MOONMIND_CI_REPOSITORY", "TASK_PROPOSALS_MOONMIND_CI_REPOSITORY"
        ),
        description="MoonMind CI repository used whenever proposals target run-quality improvements.",
    )
    max_items_project_default: int = Field(
        3,
        validation_alias=AliasChoices("TASK_PROPOSALS_MAX_ITEMS_PROJECT"),
        description="Default per-run cap for project-targeted proposals when unspecified.",
        ge=1,
    )
    max_items_moonmind_default: int = Field(
        2,
        validation_alias=AliasChoices("TASK_PROPOSALS_MAX_ITEMS_MOONMIND"),
        description="Default per-run cap for MoonMind-targeted proposals when unspecified.",
        ge=1,
    )
    moonmind_severity_floor_default: str = Field(
        "high",
        validation_alias=AliasChoices(
            "MOONMIND_MIN_SEVERITY_FOR_MOONMIND",
            "TASK_PROPOSALS_MIN_SEVERITY_FOR_MOONMIND",
        ),
        description="Minimum severity that must be met before MoonMind CI proposals are emitted when policy omits a floor.",
    )
    severity_vocabulary: tuple[str, ...] = Field(
        _ALLOWED_PROPOSAL_SEVERITIES,
        validation_alias=AliasChoices("TASK_PROPOSALS_SEVERITY_VOCABULARY"),
        description="Allowed severity labels for proposal policy evaluation.",
    )
    notifications_enabled: bool = Field(
        False,
        alias="TASK_PROPOSALS_NOTIFICATIONS_ENABLED",
        description="Emit webhook notifications for high-signal proposal categories.",
    )
    notifications_webhook_url: Optional[str] = Field(
        None,
        alias="TASK_PROPOSALS_NOTIFICATIONS_WEBHOOK_URL",
        description="Webhook endpoint for proposal alerts.",
    )
    notifications_authorization: Optional[str] = Field(
        None,
        alias="TASK_PROPOSALS_NOTIFICATIONS_AUTHORIZATION",
        description="Optional Authorization header for webhook calls.",
    )
    notifications_timeout_seconds: int = Field(
        5,
        alias="TASK_PROPOSALS_NOTIFICATIONS_TIMEOUT_SECONDS",
        description="Webhook timeout in seconds.",
        gt=0,
    )

    @field_validator("proposal_targets_default", mode="before")
    @classmethod
    def _normalize_setting_targets_default(cls, value: object) -> str:
        text = str(value or "").strip().lower()
        if not text:
            return "project"
        if text not in _ALLOWED_TARGET_DEFAULTS:
            allowed = ", ".join(_ALLOWED_TARGET_DEFAULTS)
            raise ValueError(
                f"task_proposals.proposal_targets_default must be one of: {allowed}"
            )
        return text

    @field_validator("moonmind_severity_floor_default", mode="before")
    @classmethod
    def _normalize_setting_severity_floor(cls, value: object) -> str:
        text = str(value or "").strip().lower()
        if not text:
            return "high"
        if text not in _ALLOWED_PROPOSAL_SEVERITIES:
            allowed = ", ".join(_ALLOWED_PROPOSAL_SEVERITIES)
            raise ValueError(
                "task_proposals.moonmind_severity_floor_default must be one of: "
                f"{allowed}"
            )
        return text

    @field_validator("moonmind_ci_repository", mode="before")
    @classmethod
    def _normalize_setting_ci_repo(cls, value: object) -> str:
        text = str(value or "").strip()
        if not text:
            return "MoonLadderStudios/MoonMind"
        return text

    @field_validator("severity_vocabulary", mode="before")
    @classmethod
    def _normalize_setting_severity_vocab(cls, value: object) -> tuple[str, ...]:
        if value is None or value == "":
            return _ALLOWED_PROPOSAL_SEVERITIES
        if isinstance(value, str):
            tokens = [token.strip().lower() for token in value.split(",")]
        elif isinstance(value, Sequence):
            tokens = [str(token).strip().lower() for token in value]
        else:
            tokens = [str(value).strip().lower()]
        normalized = tuple(dict.fromkeys(token for token in tokens if token))
        if not normalized:
            return _ALLOWED_PROPOSAL_SEVERITIES
        invalid = [
            token for token in normalized if token not in _ALLOWED_PROPOSAL_SEVERITIES
        ]
        if invalid:
            allowed = ", ".join(_ALLOWED_PROPOSAL_SEVERITIES)
            raise ValueError(
                f"task_proposals.severity_vocabulary must be subset of: {allowed}"
            )
        ordered = tuple(
            token for token in _ALLOWED_PROPOSAL_SEVERITIES if token in normalized
        )
        return ordered or _ALLOWED_PROPOSAL_SEVERITIES

    model_config = SettingsConfigDict(
        populate_by_name=True,
        env_prefix="",
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )


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

    temporal: TemporalSettings = Field(default_factory=TemporalSettings)
    temporal_dashboard: TemporalDashboardSettings = Field(
        default_factory=TemporalDashboardSettings
    )
    workflow: AppWorkflowSettings = Field(default_factory=AppWorkflowSettings)
    feature_flags: FeatureFlagsSettings = Field(default_factory=FeatureFlagsSettings)
    task_proposals: TaskProposalSettings = Field(default_factory=TaskProposalSettings)
    jules: JulesSettings = Field(default_factory=JulesSettings)
    worker_enable_task_proposals: Optional[bool] = Field(
        None,
        validation_alias=AliasChoices(
            "MOONMIND_ENABLE_TASK_PROPOSALS",
            "ENABLE_TASK_PROPOSALS",
        ),
        exclude=True,
        description=(
            "Compatibility passthrough for worker task-proposal bootstrap env flags."
        ),
    )
    worker_stage_command_timeout_seconds: Optional[int] = Field(
        None,
        validation_alias=AliasChoices(
            "WORKFLOW_STAGE_COMMAND_TIMEOUT_SECONDS",
            "MOONMIND_STAGE_COMMAND_TIMEOUT_SECONDS",
            "WORKFLOW_STAGE_COMMAND_TIMEOUT_SECONDS",
        ),
        ge=1,
        exclude=True,
        description=(
            "Compatibility passthrough for worker stage-command timeout env flags."
        ),
    )
    worker_codex_model: Optional[str] = Field(
        None,
        validation_alias=AliasChoices("MOONMIND_CODEX_MODEL", "CODEX_MODEL"),
        exclude=True,
        description="Compatibility passthrough for worker Codex model env flags.",
    )
    worker_codex_effort: Optional[str] = Field(
        None,
        validation_alias=AliasChoices(
            "MOONMIND_CODEX_EFFORT",
            "CODEX_MODEL_REASONING_EFFORT",
            "MODEL_REASONING_EFFORT",
        ),
        exclude=True,
        description="Compatibility passthrough for worker Codex effort env flags.",
    )

    moonmind_workdir: Optional[str] = Field(
        None,
        alias="MOONMIND_WORKDIR",
        exclude=True,
        description="Compatibility passthrough for worker workspace root.",
    )
    moonmind_agent_workspaces_volume_name: Optional[str] = Field(
        None,
        alias="MOONMIND_AGENT_WORKSPACES_VOLUME_NAME",
        exclude=True,
        description="Compatibility passthrough for worker workspace volume name.",
    )
    moonmind_worker_capabilities: Optional[str] = Field(
        None,
        alias="MOONMIND_WORKER_CAPABILITIES",
        exclude=True,
        description="Compatibility passthrough for worker capabilities declaration.",
    )
    moonmind_unreal_ccache_volume_name: Optional[str] = Field(
        None,
        alias="MOONMIND_UNREAL_CCACHE_VOLUME_NAME",
        exclude=True,
        description="Compatibility passthrough for ccache volume in DooD workflows.",
    )
    moonmind_unreal_ubt_volume_name: Optional[str] = Field(
        None,
        alias="MOONMIND_UNREAL_UBT_VOLUME_NAME",
        exclude=True,
        description="Compatibility passthrough for UBT metadata volume in DooD workflows.",
    )
    workflow_git_user_name: Optional[str] = Field(
        None,
        validation_alias=AliasChoices(
            "workflow_git_user_name", "WORKFLOW_GIT_USER_NAME"
        ),
        description="Compatibility passthrough for workflow git user display name.",
        exclude=True,
    )
    workflow_github_repository: Optional[str] = Field(
        None,
        validation_alias=AliasChoices(
            "workflow_github_repository",
            "WORKFLOW_GITHUB_REPOSITORY",
            "WORKFLOW_GITHUB_REPOSITORY",
        ),
        description="Compatibility passthrough for workflow repository override.",
        exclude=True,
    )
    workflow_git_user_email: Optional[str] = Field(
        None,
        validation_alias=AliasChoices(
            "workflow_git_user_email", "WORKFLOW_GIT_USER_EMAIL"
        ),
        description="Compatibility passthrough for workflow git user email.",
        exclude=True,
    )
    moonmind_git_user_name: Optional[str] = Field(
        None,
        validation_alias=AliasChoices(
            "moonmind_git_user_name", "MOONMIND_GIT_USER_NAME"
        ),
        description="Compatibility passthrough for legacy MoonMind git user display name.",
        exclude=True,
    )
    moonmind_git_user_email: Optional[str] = Field(
        None,
        validation_alias=AliasChoices(
            "moonmind_git_user_email", "MOONMIND_GIT_USER_EMAIL"
        ),
        description="Compatibility passthrough for legacy MoonMind git user email.",
        exclude=True,
    )
    moonmind_live_log_events_enabled: Optional[str] = Field(
        None,
        validation_alias=AliasChoices(
            "moonmind_live_log_events_enabled",
            "MOONMIND_LIVE_LOG_EVENTS_ENABLED",
        ),
        description="Compatibility passthrough for legacy live log event setting.",
        exclude=True,
    )
    moonmind_live_log_events_batch_bytes: Optional[int] = Field(
        None,
        validation_alias=AliasChoices(
            "moonmind_live_log_events_batch_bytes",
            "MOONMIND_LIVE_LOG_EVENTS_BATCH_BYTES",
        ),
        description="Compatibility passthrough for legacy live log batch size setting.",
        exclude=True,
    )
    moonmind_live_log_events_flush_interval_ms: Optional[int] = Field(
        None,
        validation_alias=AliasChoices(
            "moonmind_live_log_events_flush_interval_ms",
            "MOONMIND_LIVE_LOG_EVENTS_FLUSH_INTERVAL_MS",
        ),
        description="Compatibility passthrough for legacy live log flush interval setting.",
        exclude=True,
    )
    moonmind_gemini_cli_auth_mode: Optional[str] = Field(
        None,
        validation_alias=AliasChoices(
            "moonmind_gemini_cli_auth_mode",
            "MOONMIND_GEMINI_CLI_AUTH_MODE",
        ),
        description="Compatibility passthrough for legacy Gemini CLI auth mode.",
        exclude=True,
    )
    gemini_home: Optional[str] = Field(
        None,
        validation_alias=AliasChoices("gemini_home", "GEMINI_HOME"),
        description="Compatibility passthrough for legacy Gemini auth home.",
        exclude=True,
    )
    moonmind_claude_cli_auth_mode: Optional[str] = Field(
        None,
        validation_alias=AliasChoices(
            "moonmind_claude_cli_auth_mode",
            "MOONMIND_CLAUDE_CLI_AUTH_MODE",
        ),
        description="Compatibility passthrough for legacy Claude CLI auth mode.",
        exclude=True,
    )
    claude_home: Optional[str] = Field(
        None,
        validation_alias=AliasChoices("claude_home", "CLAUDE_HOME"),
        description="Compatibility passthrough for legacy Claude auth home.",
        exclude=True,
    )

    # Default providers and models
    default_chat_provider: str = Field("google", alias="DEFAULT_CHAT_PROVIDER")
    default_embedding_provider: str = Field(
        "google", alias="DEFAULT_EMBEDDING_PROVIDER"
    )

    # Legacy settings for backwards compatibility
    default_embeddings_provider: str = Field(
        "ollama", alias="DEFAULT_EMBEDDINGS_PROVIDER"
    )

    # Model cache settings
    model_cache_refresh_interval: int = Field(
        3600, alias="MODEL_CACHE_REFRESH_INTERVAL"
    )
    model_cache_refresh_interval_seconds: int = Field(
        3600, alias="MODEL_CACHE_REFRESH_INTERVAL_SECONDS"
    )
    vector_store_provider: str = Field(
        "qdrant", alias="VECTOR_STORE_PROVIDER"
    )  # Added field

    # Vector store settings
    vector_store_collection_name: str = Field(
        "moonmind", alias="VECTOR_STORE_COLLECTION_NAME"
    )

    # Other settings
    fastapi_reload: bool = Field(False, alias="FASTAPI_RELOAD")
    fernet_key: Optional[str] = Field(None, alias="FERNET_KEY")
    hf_access_token: Optional[str] = Field(None, alias="HF_ACCESS_TOKEN")

    langchain_api_key: Optional[str] = Field(None, alias="LANGCHAIN_API_KEY")
    langchain_tracing_v2: str = Field("true", alias="LANGCHAIN_TRACING_V2")
    langchain_project: str = Field("MoonMind", alias="LANGCHAIN_PROJECT")

    model_directory: str = Field("/app/model_data", alias="MODEL_DIRECTORY")

    # OpenHands settings
    openhands_llm_api_key: Optional[str] = Field(None, alias="OPENHANDS__LLM__API_KEY")
    openhands_llm_model: str = Field(
        "gemini/gemini-2.5-pro-exp-03-25", alias="OPENHANDS__LLM__MODEL"
    )
    openhands_llm_custom_llm_provider: str = Field(
        "gemini_cli", alias="OPENHANDS__LLM__CUSTOM_LLM_PROVIDER"
    )
    openhands_llm_timeout: int = Field(600, alias="OPENHANDS__LLM__TIMEOUT")
    openhands_llm_embedding_model: str = Field(
        "models/text-embedding-004", alias="OPENHANDS__LLM__EMBEDDING_MODEL"
    )
    openhands_core_workspace_base: str = Field(
        "/workspace", alias="OPENHANDS__CORE__WORKSPACE_BASE"
    )

    postgres_version: int = Field(14, alias="POSTGRES_VERSION")
    rabbitmq_user: Optional[str] = Field(None, alias="RABBITMQ_USER")
    rabbitmq_password: Optional[str] = Field(None, alias="RABBITMQ_PASSWORD")

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

    @property
    def claude_runtime_gate(self):
        """Return reusable Claude gate state for API surfaces."""

        return build_claude_runtime_gate_state(
            env=os.environ,
            api_key=self.anthropic.anthropic_api_key,
            error_message=CLAUDE_RUNTIME_DISABLED_MESSAGE,
        )

    @property
    def jules_runtime_gate(self):
        """Return reusable Jules gate state for API surfaces."""

        return build_jules_runtime_gate_state(
            env=os.environ,
            enabled=self.jules.jules_enabled,
            api_url=self.jules.jules_api_url,
            api_key=self.jules.jules_api_key,
            error_message=JULES_RUNTIME_DISABLED_MESSAGE,
        )

    def model_post_init(self, __context: Any) -> None:  # type: ignore[override]
        """Populate derived defaults after settings load."""
        super().model_post_init(__context)
        if not self.workflow.codex_queue:
            self.workflow.codex_queue = self.workflow.default_queue
        if self.worker_enable_task_proposals is not None:
            self.workflow.enable_task_proposals = self.worker_enable_task_proposals
        if self.worker_stage_command_timeout_seconds is not None:
            self.workflow.stage_command_timeout_seconds = (
                self.worker_stage_command_timeout_seconds
            )
        if self.worker_codex_model is not None:
            self.workflow.codex_model = self.worker_codex_model
        if self.worker_codex_effort is not None:
            self.workflow.codex_effort = self.worker_codex_effort
        if (
            "workflow" not in self.__pydantic_fields_set__
            and self.workflow_github_repository is not None
        ):
            repo = self.workflow_github_repository.strip()
            if repo:
                self.workflow = AppWorkflowSettings(
                    _env_file=None,
                    **self.workflow.model_dump(exclude={"github_repository"}),
                    github_repository=repo,
                )
        configured_default = (
            str(self.workflow.default_task_runtime or "").strip().lower()
        )
        if configured_default == "jules":
            default_runtime_gate = build_jules_runtime_gate_state(
                env=os.environ,
                enabled=self.jules.jules_enabled,
                api_url=self.jules.jules_api_url,
                api_key=self.jules.jules_api_key,
                error_message="default_task_runtime=jules requires JULES_API_KEY configured (set JULES_ENABLED=false to explicitly disable)",
            )
            if not default_runtime_gate.enabled:
                raise ValueError(default_runtime_gate.error_message)

    model_config = SettingsConfigDict(
        populate_by_name=True,
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )


# Create a global settings instance
settings = AppSettings()
