import os
from pathlib import Path
from typing import Annotated, Any, Optional, Sequence

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

ENV_FILE = Path(__file__).resolve().parent.parent.parent / ".env"
_ALLOWED_TARGET_DEFAULTS = ("project", "moonmind", "both")
_ALLOWED_PROPOSAL_SEVERITIES = ("low", "medium", "high", "critical")


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
        extra="ignore",
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
        "moonmind.jobs",
        validation_alias=AliasChoices("WORKFLOW_DEFAULT_QUEUE", "CELERY_DEFAULT_QUEUE"),
        description="Default queue name for workflow tasks.",
    )
    default_exchange: str = Field(
        "moonmind.jobs",
        validation_alias=AliasChoices(
            "WORKFLOW_DEFAULT_EXCHANGE", "CELERY_DEFAULT_EXCHANGE"
        ),
        description="Default exchange for workflow tasks.",
    )
    default_routing_key: str = Field(
        "moonmind.jobs",
        validation_alias=AliasChoices(
            "WORKFLOW_DEFAULT_ROUTING_KEY", "CELERY_DEFAULT_ROUTING_KEY"
        ),
        description="Default routing key used by the workflow queue.",
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
        extra="ignore",
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

    repo_root: str = Field(
        ".",
        env="SPEC_WORKFLOW_REPO_ROOT",
        validation_alias=AliasChoices("WORKFLOW_REPO_ROOT", "SPEC_WORKFLOW_REPO_ROOT"),
    )
    tasks_root: str = Field(
        "specs",
        validation_alias=AliasChoices("WORKFLOW_TASKS_ROOT", "SPEC_WORKFLOW_TASKS_ROOT"),
    )
    artifacts_root: str = Field(
        "var/artifacts/spec_workflows",
        env=("SPEC_WORKFLOW_ARTIFACT_ROOT", "SPEC_WORKFLOW_ARTIFACTS_ROOT"),
        validation_alias=AliasChoices(
            "WORKFLOW_ARTIFACTS_ROOT",
            "SPEC_WORKFLOW_ARTIFACT_ROOT",
            "SPEC_WORKFLOW_ARTIFACTS_ROOT",
        ),
        description="Filesystem location where Spec workflow artifacts are persisted.",
    )
    agent_job_artifact_root: str = Field(
        "var/artifacts/agent_jobs",
        env="AGENT_JOB_ARTIFACT_ROOT",
        description="Filesystem location where agent queue artifacts are persisted.",
    )
    agent_job_artifact_max_bytes: int = Field(
        50 * 1024 * 1024,
        env="AGENT_JOB_ARTIFACT_MAX_BYTES",
        description="Maximum allowed artifact upload size in bytes for queue jobs.",
        gt=0,
    )
    allow_manifest_path_source: bool = Field(
        False,
        env="MOONMIND_ALLOW_MANIFEST_PATH_SOURCE",
        description="Allow manifest.source.kind='path' submissions (intended for dev/test images).",
    )
    manifest_required_capabilities: tuple[str, ...] = Field(
        ("manifest",),
        env="SPEC_WORKFLOW_MANIFEST_REQUIRED_CAPABILITIES",
        description="Comma-delimited list of base capability labels applied to manifest jobs.",
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
        "001-celery-chain-workflow",
        validation_alias=AliasChoices(
            "WORKFLOW_DEFAULT_FEATURE_KEY", "SPEC_WORKFLOW_DEFAULT_FEATURE_KEY"
        ),
    )
    codex_environment: Optional[str] = Field(None, env="CODEX_ENV")
    codex_model: Optional[str] = Field(
        "gpt-5.3-codex",
        env=("MOONMIND_CODEX_MODEL", "CODEX_MODEL"),
        validation_alias=AliasChoices("MOONMIND_CODEX_MODEL", "CODEX_MODEL"),
    )
    codex_effort: Optional[str] = Field(
        "high",
        env=(
            "MOONMIND_CODEX_EFFORT",
            "CODEX_MODEL_REASONING_EFFORT",
            "MODEL_REASONING_EFFORT",
        ),
        validation_alias=AliasChoices(
            "MOONMIND_CODEX_EFFORT",
            "CODEX_MODEL_REASONING_EFFORT",
            "MODEL_REASONING_EFFORT",
        ),
    )
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
        env=(
            "MOONMIND_QUEUE",
            "WORKFLOW_CODEX_QUEUE",
            "SPEC_WORKFLOW_CODEX_QUEUE",
            "CODEX_QUEUE",
        ),
        description="Explicit Codex queue name assigned to this worker.",
    )
    codex_volume_name: Optional[str] = Field(
        None,
        env="CODEX_VOLUME_NAME",
        description="Docker volume providing persistent Codex authentication.",
    )
    claude_volume_name: Optional[str] = Field(
        None,
        env="CLAUDE_VOLUME_NAME",
        description="Docker volume providing persistent Claude authentication.",
    )
    claude_volume_path: Optional[str] = Field(
        None,
        env="CLAUDE_VOLUME_PATH",
        description="In-container path where Claude auth data is mounted.",
    )
    claude_home: Optional[str] = Field(
        None,
        env="CLAUDE_HOME",
        description="Claude CLI home directory used for persisted OAuth state.",
    )
    codex_login_check_image: Optional[str] = Field(
        None,
        env="CODEX_LOGIN_CHECK_IMAGE",
        description="Override container image for Codex login status checks.",
    )
    default_task_runtime: str = Field(
        "codex",
        env=("MOONMIND_DEFAULT_TASK_RUNTIME", "SPEC_WORKFLOW_DEFAULT_TASK_RUNTIME"),
        validation_alias=AliasChoices(
            "MOONMIND_DEFAULT_TASK_RUNTIME",
            "SPEC_WORKFLOW_DEFAULT_TASK_RUNTIME",
        ),
        description="Fallback runtime for queue task payloads that omit runtime fields.",
    )
    default_publish_mode: str = Field(
        "pr",
        env=(
            "MOONMIND_DEFAULT_PUBLISH_MODE",
            "SPEC_WORKFLOW_DEFAULT_PUBLISH_MODE",
        ),
        validation_alias=AliasChoices(
            "MOONMIND_DEFAULT_PUBLISH_MODE",
            "SPEC_WORKFLOW_DEFAULT_PUBLISH_MODE",
        ),
        description="Fallback publish mode applied when queue task payloads omit publish.mode.",
    )
    github_repository: Optional[str] = Field(
        "MoonLadderStudios/MoonMind",
        env="SPEC_WORKFLOW_GITHUB_REPOSITORY",
        validation_alias=AliasChoices("SPEC_WORKFLOW_GITHUB_REPOSITORY"),
    )
    git_user_name: Optional[str] = Field(
        None,
        env=(
            "WORKFLOW_GIT_USER_NAME",
            "SPEC_WORKFLOW_GIT_USER_NAME",
            "MOONMIND_GIT_USER_NAME",
        ),
        validation_alias=AliasChoices(
            "WORKFLOW_GIT_USER_NAME",
            "SPEC_WORKFLOW_GIT_USER_NAME",
            "MOONMIND_GIT_USER_NAME",
        ),
        description="Optional Git author/committer display name used by worker publish stages.",
    )
    git_user_email: Optional[str] = Field(
        None,
        env=(
            "WORKFLOW_GIT_USER_EMAIL",
            "SPEC_WORKFLOW_GIT_USER_EMAIL",
            "MOONMIND_GIT_USER_EMAIL",
        ),
        validation_alias=AliasChoices(
            "WORKFLOW_GIT_USER_EMAIL",
            "SPEC_WORKFLOW_GIT_USER_EMAIL",
            "MOONMIND_GIT_USER_EMAIL",
        ),
        description="Optional Git author/committer email used by worker publish stages.",
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
    skills_enabled: bool = Field(
        True,
        env="SPEC_WORKFLOW_USE_SKILLS",
        description="Enable skills-first orchestration policy for workflow stages.",
    )
    skills_shadow_mode: bool = Field(
        False,
        env="SPEC_WORKFLOW_SKILLS_SHADOW_MODE",
        description="Enable shadow-mode telemetry for skills orchestration.",
    )
    skills_fallback_enabled: bool = Field(
        True,
        env="SPEC_WORKFLOW_SKILLS_FALLBACK_ENABLED",
        description="Allow direct stage fallback when skill adapters fail.",
    )
    skills_canary_percent: int = Field(
        100,
        env="SPEC_WORKFLOW_SKILLS_CANARY_PERCENT",
        description="Percentage of runs routed through skills-first policy (0-100).",
        ge=0,
        le=100,
    )
    default_skill: str = Field(
        "speckit",
        validation_alias=AliasChoices(
            "WORKFLOW_DEFAULT_SKILL",
            "SPEC_WORKFLOW_DEFAULT_SKILL",
            "MOONMIND_DEFAULT_SKILL",
        ),
        description="Default skill identifier for workflow stage execution.",
    )
    discover_skill: Optional[str] = Field(
        None,
        validation_alias=AliasChoices(
            "WORKFLOW_DISCOVER_SKILL",
            "SPEC_WORKFLOW_DISCOVER_SKILL",
            "MOONMIND_DISCOVER_SKILL",
        ),
        description="Optional skill override for discovery stage.",
    )
    submit_skill: Optional[str] = Field(
        None,
        validation_alias=AliasChoices(
            "WORKFLOW_SUBMIT_SKILL",
            "SPEC_WORKFLOW_SUBMIT_SKILL",
            "MOONMIND_SUBMIT_SKILL",
        ),
        description="Optional skill override for submit stage.",
    )
    publish_skill: Optional[str] = Field(
        None,
        validation_alias=AliasChoices(
            "WORKFLOW_PUBLISH_SKILL",
            "SPEC_WORKFLOW_PUBLISH_SKILL",
            "MOONMIND_PUBLISH_SKILL",
        ),
        description="Optional skill override for publish stage.",
    )
    skill_policy_mode: str = Field(
        "permissive",
        validation_alias=AliasChoices(
            "WORKFLOW_SKILL_POLICY_MODE",
            "SPEC_WORKFLOW_SKILL_POLICY_MODE",
            "MOONMIND_SKILL_POLICY_MODE",
            "SKILL_POLICY_MODE",
        ),
        description="Skill policy mode. 'permissive' allows any resolvable skill; 'allowlist' enforces SPEC_WORKFLOW_ALLOWED_SKILLS.",
    )
    allowed_skills: Annotated[tuple[str, ...], NoDecode] = Field(
        ("speckit",),
        validation_alias=AliasChoices(
            "WORKFLOW_ALLOWED_SKILLS",
            "SPEC_WORKFLOW_ALLOWED_SKILLS",
            "MOONMIND_ALLOWED_SKILLS",
        ),
        description="Allowlisted skills that can be selected for workflow stages.",
    )
    skills_cache_root: str = Field(
        "var/skill_cache",
        env="SPEC_SKILLS_CACHE_ROOT",
        description="Immutable cache root for verified skill artifacts.",
    )
    skills_workspace_root: str = Field(
        "runs",
        env="SPEC_SKILLS_WORKSPACE_ROOT",
        description="Workspace subdirectory (under SPEC_WORKFLOW_WORKSPACE_ROOT) for per-run active skills links.",
    )
    skills_registry_source: Optional[str] = Field(
        None,
        env="SPEC_SKILLS_REGISTRY_SOURCE",
        description="Optional registry profile/URI for skill source resolution.",
    )
    skills_local_mirror_root: str = Field(
        ".agents/skills/local",
        env="SPEC_SKILLS_LOCAL_MIRROR_ROOT",
        validation_alias=AliasChoices("SPEC_SKILLS_LOCAL_MIRROR_ROOT"),
        description="Default local-only skill mirror directory used for source resolution.",
    )
    skills_legacy_mirror_root: str = Field(
        ".agents/skills",
        env="SPEC_SKILLS_LEGACY_MIRROR_ROOT",
        validation_alias=AliasChoices("SPEC_SKILLS_LEGACY_MIRROR_ROOT"),
        description=(
            "Secondary shared mirror root checked after local-only skills; "
            "nested '<root>/skills' is auto-detected for compatibility."
        ),
    )
    skills_verify_signatures: bool = Field(
        False,
        env="SPEC_SKILLS_VERIFY_SIGNATURES",
        description="Require signature metadata for selected skills before activation.",
    )
    skills_validate_local_mirror: bool = Field(
        False,
        env="SPEC_SKILLS_VALIDATE_LOCAL_MIRROR",
        description="Enable startup validation of the configured local skill mirror root.",
    )
    live_session_enabled_default: bool = Field(
        True,
        env="MOONMIND_LIVE_SESSION_ENABLED_DEFAULT",
        validation_alias=AliasChoices("MOONMIND_LIVE_SESSION_ENABLED_DEFAULT"),
        description="Enable live task sessions by default for queue task runs.",
    )
    live_session_provider: str = Field(
        "tmate",
        env="MOONMIND_LIVE_SESSION_PROVIDER",
        validation_alias=AliasChoices("MOONMIND_LIVE_SESSION_PROVIDER"),
        description="Live session provider implementation.",
    )
    live_session_ttl_minutes: int = Field(
        60,
        env="MOONMIND_LIVE_SESSION_TTL_MINUTES",
        validation_alias=AliasChoices("MOONMIND_LIVE_SESSION_TTL_MINUTES"),
        description="Default live session lifetime before automatic revocation.",
        ge=1,
        le=1440,
    )
    live_session_rw_grant_ttl_minutes: int = Field(
        15,
        env="MOONMIND_LIVE_SESSION_RW_GRANT_TTL_MINUTES",
        validation_alias=AliasChoices("MOONMIND_LIVE_SESSION_RW_GRANT_TTL_MINUTES"),
        description="Default RW reveal grant duration for live sessions.",
        ge=1,
        le=240,
    )
    live_session_allow_web: bool = Field(
        False,
        env="MOONMIND_LIVE_SESSION_ALLOW_WEB",
        validation_alias=AliasChoices("MOONMIND_LIVE_SESSION_ALLOW_WEB"),
        description="Whether tmate web attach URLs are exposed via API responses.",
    )
    tmate_server_host: Optional[str] = Field(
        None,
        env="MOONMIND_TMATE_SERVER_HOST",
        validation_alias=AliasChoices("MOONMIND_TMATE_SERVER_HOST"),
        description="Optional self-hosted tmate relay hostname.",
    )
    live_session_max_concurrent_per_worker: int = Field(
        4,
        env="MOONMIND_LIVE_SESSION_MAX_CONCURRENT_PER_WORKER",
        validation_alias=AliasChoices(
            "MOONMIND_LIVE_SESSION_MAX_CONCURRENT_PER_WORKER"
        ),
        description="Maximum concurrent live sessions each worker should provision.",
        ge=1,
        le=64,
    )
    enable_task_proposals: bool = Field(
        False,
        env=("MOONMIND_ENABLE_TASK_PROPOSALS", "ENABLE_TASK_PROPOSALS"),
        validation_alias=AliasChoices(
            "MOONMIND_ENABLE_TASK_PROPOSALS",
            "ENABLE_TASK_PROPOSALS",
        ),
        description="Enable worker-side task proposal submission after successful runs.",
    )
    proposal_targets_default: str = Field(
        "project",
        env=("MOONMIND_PROPOSAL_TARGETS", "TASK_PROPOSALS_TARGETS_DEFAULT"),
        validation_alias=AliasChoices(
            "MOONMIND_PROPOSAL_TARGETS", "TASK_PROPOSALS_TARGETS_DEFAULT"
        ),
        description="Default proposal targets when tasks omit proposalPolicy (project|moonmind|both).",
    )
    proposal_max_items_project: int = Field(
        3,
        env=("TASK_PROPOSALS_MAX_ITEMS_PROJECT",),
        validation_alias=AliasChoices("TASK_PROPOSALS_MAX_ITEMS_PROJECT"),
        description="Default per-run project proposal cap applied when task policy omits maxItems.project.",
        ge=1,
    )
    proposal_max_items_moonmind: int = Field(
        2,
        env=("TASK_PROPOSALS_MAX_ITEMS_MOONMIND",),
        validation_alias=AliasChoices("TASK_PROPOSALS_MAX_ITEMS_MOONMIND"),
        description="Default per-run MoonMind proposal cap applied when task policy omits maxItems.moonmind.",
        ge=1,
    )
    proposal_moonmind_severity_floor: str = Field(
        "high",
        env=(
            "MOONMIND_MIN_SEVERITY_FOR_MOONMIND",
            "TASK_PROPOSALS_MIN_SEVERITY_FOR_MOONMIND",
        ),
        validation_alias=AliasChoices(
            "MOONMIND_MIN_SEVERITY_FOR_MOONMIND",
            "TASK_PROPOSALS_MIN_SEVERITY_FOR_MOONMIND",
        ),
        description="Lowest accepted severity for MoonMind CI proposals when policy omits a floor.",
    )
    moonmind_ci_repository: str = Field(
        "MoonLadderStudios/MoonMind",
        env=("MOONMIND_CI_REPOSITORY", "TASK_PROPOSALS_MOONMIND_CI_REPOSITORY"),
        description="Repository used for MoonMind CI/run-quality proposals.",
    )
    stage_command_timeout_seconds: int = Field(
        3600,
        env=(
            "MOONMIND_STAGE_COMMAND_TIMEOUT_SECONDS",
            "SPEC_WORKFLOW_STAGE_COMMAND_TIMEOUT_SECONDS",
        ),
        validation_alias=AliasChoices(
            "MOONMIND_STAGE_COMMAND_TIMEOUT_SECONDS",
            "SPEC_WORKFLOW_STAGE_COMMAND_TIMEOUT_SECONDS",
        ),
        description="Hard timeout for non-container worker stage commands.",
        ge=1,
    )

    model_config = SettingsConfigDict(
        env_prefix="",
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
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

    @field_validator("proposal_targets_default", mode="before")
    @classmethod
    def _normalize_proposal_targets_default(cls, value: object) -> str:
        text = str(value or "").strip().lower()
        if not text:
            return "project"
        if text not in _ALLOWED_TARGET_DEFAULTS:
            allowed = ", ".join(_ALLOWED_TARGET_DEFAULTS)
            raise ValueError(
                f"spec_workflow.proposal_targets_default must be one of: {allowed}"
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
                "spec_workflow.proposal_moonmind_severity_floor must be one of: "
                f"{allowed}"
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
        "celery_broker_url",
        "celery_result_backend",
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
        allowed = {"codex", "gemini", "claude"}
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

    def model_post_init(self, __context: Any) -> None:  # type: ignore[override]
        """Validate agent backend selections after settings load."""

        super().model_post_init(__context)
        # Ensure tuples are deduplicated even when env provided sequences
        allowed = tuple(dict.fromkeys(self.allowed_agent_backends or ()))
        self.allowed_agent_backends = allowed
        self.agent_runtime_env_keys = tuple(
            dict.fromkeys(self.agent_runtime_env_keys or ())
        )
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
            self.default_skill = "speckit"
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
        "gemini-embedding-001", env="GOOGLE_EMBEDDING_MODEL"
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
        env_prefix="", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )


class JiraSettings(BaseSettings):
    """Jira specific settings"""

    jira_jql_query: Optional[str] = Field(None, env="ATLASSIAN_JIRA_JQL_QUERY")
    jira_fetch_batch_size: int = Field(50, env="ATLASSIAN_JIRA_FETCH_BATCH_SIZE")
    jira_enabled: bool = Field(False, env="ATLASSIAN_JIRA_ENABLED")

    model_config = SettingsConfigDict(
        env_prefix="", env_file=".env", env_file_encoding="utf-8", extra="ignore"
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
        env_prefix="", env_file=".env", env_file_encoding="utf-8", extra="ignore"
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
        env_prefix="FEATURE_FLAGS__",
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )


class TaskProposalSettings(BaseSettings):
    """Task proposal queue runtime knobs."""

    proposal_targets_default: str = Field(
        "project",
        env=("MOONMIND_PROPOSAL_TARGETS", "TASK_PROPOSALS_TARGETS_DEFAULT"),
        validation_alias=AliasChoices(
            "MOONMIND_PROPOSAL_TARGETS", "TASK_PROPOSALS_TARGETS_DEFAULT"
        ),
        description="Default proposal targets when policy overrides are absent (project|moonmind|both).",
    )
    moonmind_ci_repository: str = Field(
        "MoonLadderStudios/MoonMind",
        env=("MOONMIND_CI_REPOSITORY", "TASK_PROPOSALS_MOONMIND_CI_REPOSITORY"),
        description="MoonMind CI repository used whenever proposals target run-quality improvements.",
    )
    max_items_project_default: int = Field(
        3,
        env=("TASK_PROPOSALS_MAX_ITEMS_PROJECT",),
        validation_alias=AliasChoices("TASK_PROPOSALS_MAX_ITEMS_PROJECT"),
        description="Default per-run cap for project-targeted proposals when unspecified.",
        ge=1,
    )
    max_items_moonmind_default: int = Field(
        2,
        env=("TASK_PROPOSALS_MAX_ITEMS_MOONMIND",),
        validation_alias=AliasChoices("TASK_PROPOSALS_MAX_ITEMS_MOONMIND"),
        description="Default per-run cap for MoonMind-targeted proposals when unspecified.",
        ge=1,
    )
    moonmind_severity_floor_default: str = Field(
        "high",
        env=(
            "MOONMIND_MIN_SEVERITY_FOR_MOONMIND",
            "TASK_PROPOSALS_MIN_SEVERITY_FOR_MOONMIND",
        ),
        validation_alias=AliasChoices(
            "MOONMIND_MIN_SEVERITY_FOR_MOONMIND",
            "TASK_PROPOSALS_MIN_SEVERITY_FOR_MOONMIND",
        ),
        description="Minimum severity that must be met before MoonMind CI proposals are emitted when policy omits a floor.",
    )
    severity_vocabulary: tuple[str, ...] = Field(
        _ALLOWED_PROPOSAL_SEVERITIES,
        env=("TASK_PROPOSALS_SEVERITY_VOCABULARY",),
        description="Allowed severity labels for proposal policy evaluation.",
    )
    notifications_enabled: bool = Field(
        False,
        env="TASK_PROPOSALS_NOTIFICATIONS_ENABLED",
        description="Emit webhook notifications for high-signal proposal categories.",
    )
    notifications_webhook_url: Optional[str] = Field(
        None,
        env="TASK_PROPOSALS_NOTIFICATIONS_WEBHOOK_URL",
        description="Webhook endpoint for proposal alerts.",
    )
    notifications_authorization: Optional[str] = Field(
        None,
        env="TASK_PROPOSALS_NOTIFICATIONS_AUTHORIZATION",
        description="Optional Authorization header for webhook calls.",
    )
    notifications_timeout_seconds: int = Field(
        5,
        env="TASK_PROPOSALS_NOTIFICATIONS_TIMEOUT_SECONDS",
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
                "task_proposals.severity_vocabulary must be subset of: " f"{allowed}"
            )
        ordered = tuple(
            token for token in _ALLOWED_PROPOSAL_SEVERITIES if token in normalized
        )
        return ordered or _ALLOWED_PROPOSAL_SEVERITIES

    model_config = SettingsConfigDict(
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
    celery: CelerySettings = Field(default_factory=CelerySettings)
    spec_workflow: SpecWorkflowSettings = Field(default_factory=SpecWorkflowSettings)
    feature_flags: FeatureFlagsSettings = Field(default_factory=FeatureFlagsSettings)
    task_proposals: TaskProposalSettings = Field(default_factory=TaskProposalSettings)
    worker_enable_task_proposals: Optional[bool] = Field(
        None,
        env=("MOONMIND_ENABLE_TASK_PROPOSALS", "ENABLE_TASK_PROPOSALS"),
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
        env=(
            "MOONMIND_STAGE_COMMAND_TIMEOUT_SECONDS",
            "SPEC_WORKFLOW_STAGE_COMMAND_TIMEOUT_SECONDS",
        ),
        validation_alias=AliasChoices(
            "MOONMIND_STAGE_COMMAND_TIMEOUT_SECONDS",
            "SPEC_WORKFLOW_STAGE_COMMAND_TIMEOUT_SECONDS",
        ),
        ge=1,
        exclude=True,
        description=(
            "Compatibility passthrough for worker stage-command timeout env flags."
        ),
    )
    worker_codex_model: Optional[str] = Field(
        None,
        env=("MOONMIND_CODEX_MODEL", "CODEX_MODEL"),
        validation_alias=AliasChoices("MOONMIND_CODEX_MODEL", "CODEX_MODEL"),
        exclude=True,
        description="Compatibility passthrough for worker Codex model env flags.",
    )
    worker_codex_effort: Optional[str] = Field(
        None,
        env=(
            "MOONMIND_CODEX_EFFORT",
            "CODEX_MODEL_REASONING_EFFORT",
            "MODEL_REASONING_EFFORT",
        ),
        validation_alias=AliasChoices(
            "MOONMIND_CODEX_EFFORT",
            "CODEX_MODEL_REASONING_EFFORT",
            "MODEL_REASONING_EFFORT",
        ),
        exclude=True,
        description="Compatibility passthrough for worker Codex effort env flags.",
    )

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
    rabbitmq_user: Optional[str] = Field(None, env="RABBITMQ_USER")
    rabbitmq_password: Optional[str] = Field(None, env="RABBITMQ_PASSWORD")

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
        if not self.spec_workflow.codex_queue:
            self.spec_workflow.codex_queue = self.celery.default_queue
        if self.worker_enable_task_proposals is not None:
            self.spec_workflow.enable_task_proposals = self.worker_enable_task_proposals
        if self.worker_stage_command_timeout_seconds is not None:
            self.spec_workflow.stage_command_timeout_seconds = (
                self.worker_stage_command_timeout_seconds
            )
        if self.worker_codex_model is not None:
            self.spec_workflow.codex_model = self.worker_codex_model
        if self.worker_codex_effort is not None:
            self.spec_workflow.codex_effort = self.worker_codex_effort

    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        extra="forbid",
    )


# Create a global settings instance
settings = AppSettings()
