"""Daemon loop and queue API client for the standalone Codex worker."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import re
import shutil
import socket
import time
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from os import environ
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Sequence
from uuid import UUID

import httpx
from pydantic import ValidationError

import moonmind.utils.logging as moonmind_logging
from moonmind.agents.codex_worker.handlers import (
    ArtifactUpload,
    CodexExecHandler,
    CommandCancelledError,
    CommandResult,
    OutputChunkCallback,
    WorkerExecutionResult,
)
from moonmind.agents.codex_worker.secret_refs import (
    SecretReferenceError,
    VaultSecretResolver,
    load_vault_token,
)
from moonmind.config.settings import settings
from moonmind.rag.settings import RagRuntimeSettings
from moonmind.workflows.agent_queue.task_contract import (
    CANONICAL_TASK_JOB_TYPE,
    LEGACY_TASK_JOB_TYPES,
    SUPPORTED_EXECUTION_RUNTIMES,
    TaskContractError,
    TaskProposalPolicy,
    build_canonical_task_view,
    build_effective_proposal_policy,
    build_task_stage_plan,
)
from moonmind.workflows.skills.materializer import (
    SkillMaterializationError,
    materialize_run_skill_workspace,
)
from moonmind.workflows.skills.resolver import (
    SkillResolutionError,
    resolve_run_skill_selection,
)
from moonmind.workflows.skills.workspace_links import (
    SkillWorkspaceError,
    ensure_shared_skill_links,
)
from moonmind.workflows.speckit_celery.workspace import generate_branch_name

logger = logging.getLogger(__name__)

_CONTAINER_RESERVED_ENV_KEYS = frozenset({"ARTIFACT_DIR", "JOB_ID", "REPOSITORY"})
_CONTAINER_VOLUME_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_CONTAINER_STOP_TIMEOUT_SECONDS = 30.0
_FULL_UUID_PATTERN = re.compile(r"[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}")
_SECRET_LIKE_METADATA_PATTERN = re.compile(
    r"""(?ix)
    (?:
        gh[pousr]_[A-Za-z0-9]{8,}
        | github_pat_[A-Za-z0-9_]{10,}
        | AIza[0-9A-Za-z_-]{10,}
        | ATATT[A-Za-z0-9_-]{6,}
        | AKIA[0-9A-Z]{8,}
        | -----BEGIN [A-Z ]+PRIVATE KEY-----
        | (?:token|password|secret)\s*[:=]
    )
    """
)
_SENSITIVE_COMMAND_FLAGS = frozenset({"--title", "--body", "--message", "-m"})
_MOONMIND_SIGNAL_TAGS = frozenset(
    {
        "retry",
        "duplicate_output",
        "missing_ref",
        "conflicting_instructions",
        "flaky_test",
        "loop_detected",
        "artifact_gap",
    }
)


def _normalize_instruction_text_for_comparison(value: str | None) -> str:
    """Normalize instruction text for objective/step deduplication checks."""

    return re.sub(r"\s+", " ", str(value or "").strip())


def _normalize_proposal_tags(values: object) -> list[str]:
    """Normalize proposal tags into a stable lowercase unique list."""

    if not isinstance(values, (list, tuple)):
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in values:
        token = str(raw or "").strip().lower()
        if not token or token in seen:
            continue
        normalized.append(token)
        seen.add(token)
    return normalized


def _extract_proposal_signal(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Extract signal metadata from proposal payload or origin metadata."""

    direct = payload.get("signal")
    if isinstance(direct, Mapping):
        return dict(direct)
    origin = payload.get("origin")
    if isinstance(origin, Mapping):
        metadata = origin.get("metadata")
        if isinstance(metadata, Mapping):
            signal = metadata.get("signal")
            if isinstance(signal, Mapping):
                return dict(signal)
    return {}


def _ensure_task_request_repository(
    payload: dict[str, Any],
    *,
    repository: str,
    proposals_path: Path,
) -> bool:
    """Ensure taskCreateRequest exists and points to the repository."""

    request_node = payload.get("taskCreateRequest")
    if not isinstance(request_node, Mapping):
        logger.warning(
            "Proposal entry in %s missing taskCreateRequest; skipping",
            proposals_path,
        )
        return False
    request = copy.deepcopy(request_node)
    payload["taskCreateRequest"] = request
    request_payload = request.get("payload")
    payload_node = dict(request_payload) if isinstance(request_payload, Mapping) else {}
    payload_node["repository"] = repository
    request["payload"] = payload_node
    payload["repository"] = repository
    return True


def _ensure_run_quality_title(title: str) -> str:
    """Normalize the run_quality title prefix."""

    normalized = title.strip()
    if not normalized.lower().startswith("[run_quality]"):
        normalized = normalized or "Run quality proposal"
        normalized = f"[run_quality] {normalized}"
    return normalized.strip()


def _append_tag_slug(title: str, tags: Sequence[str]) -> str:
    """Append a sorted tag slug marker used for deduping run_quality proposals."""

    slug_items = sorted({tag for tag in tags if tag})
    if not slug_items:
        return title
    slug_text = "+".join(slug_items)
    marker = f"(tags: {slug_text})"
    if marker in title:
        return title
    return f"{title} (tags: {slug_text})"


class QueueClientError(RuntimeError):
    """Raised when queue API requests fail."""


class JobCancellationRequested(RuntimeError):
    """Raised when a claimed job has an active cancellation request."""


@dataclass(frozen=True, slots=True)
class CodexWorkerConfig:
    """Runtime configuration for the standalone Codex worker."""

    moonmind_url: str
    worker_id: str
    worker_token: str | None
    poll_interval_ms: int
    lease_seconds: int
    workdir: Path
    allowed_types: tuple[str, ...] = ("task", "codex_exec", "codex_skill")
    legacy_job_types_enabled: bool = True
    pause_poll_interval_ms: int = 5000
    worker_runtime: str = "codex"
    default_skill: str = "speckit"
    skill_policy_mode: str = "permissive"
    allowed_skills: tuple[str, ...] = ("speckit",)
    default_codex_model: str | None = None
    default_codex_effort: str | None = None
    default_gemini_model: str | None = None
    default_gemini_effort: str | None = None
    default_claude_model: str | None = None
    default_claude_effort: str | None = None
    gemini_binary: str = "gemini"
    gemini_cli_auth_mode: str = "api_key"
    claude_binary: str = "claude"
    worker_capabilities: tuple[str, ...] = ("codex", "git", "gh")
    docker_binary: str = "docker"
    container_workspace_volume: str | None = None
    container_default_timeout_seconds: int = 3600
    stage_command_timeout_seconds: int = 3600
    vault_address: str | None = None
    vault_token: str | None = None
    vault_token_file: Path | None = None
    vault_namespace: str | None = None
    vault_allowed_mounts: tuple[str, ...] = ("kv",)
    vault_timeout_seconds: float = 10.0
    git_user_name: str | None = None
    git_user_email: str | None = None
    live_log_events_enabled: bool = True
    live_log_events_batch_bytes: int = 8192
    live_log_events_flush_interval_ms: int = 300
    live_session_enabled_default: bool = True
    live_session_provider: str = "tmate"
    live_session_ttl_minutes: int = 60
    live_session_rw_grant_ttl_minutes: int = 15
    live_session_allow_web: bool = False
    tmate_server_host: str | None = None
    live_session_max_concurrent_per_worker: int = 4
    enable_task_proposals: bool = False
    artifact_upload_incremental: bool = True

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "CodexWorkerConfig":
        """Load worker settings from environment variables."""

        source = env or environ
        moonmind_url = str(source.get("MOONMIND_URL", "")).strip()
        if not moonmind_url:
            raise ValueError("MOONMIND_URL must be configured")

        worker_id = (
            str(source.get("MOONMIND_WORKER_ID", "")).strip() or socket.gethostname()
        )
        poll_interval_ms = int(
            str(source.get("MOONMIND_POLL_INTERVAL_MS", "1500")).strip()
        )
        pause_poll_interval_ms = int(
            str(source.get("MOONMIND_PAUSE_POLL_INTERVAL_MS", "5000")).strip()
        )
        lease_seconds = int(str(source.get("MOONMIND_LEASE_SECONDS", "120")).strip())
        if poll_interval_ms < 1:
            raise ValueError("MOONMIND_POLL_INTERVAL_MS must be >= 1")
        if lease_seconds < 1:
            raise ValueError("MOONMIND_LEASE_SECONDS must be >= 1")
        pause_poll_interval_ms = max(1000, pause_poll_interval_ms)

        workdir_raw = (
            str(source.get("MOONMIND_WORKDIR", "var/worker")).strip() or "var/worker"
        )
        worker_token = str(source.get("MOONMIND_WORKER_TOKEN", "")).strip() or None
        legacy_enabled_raw = (
            str(source.get("MOONMIND_ENABLE_LEGACY_JOB_TYPES", "true")).strip().lower()
        )
        legacy_job_types_enabled = legacy_enabled_raw not in {
            "0",
            "false",
            "no",
            "off",
            "",
        }
        allowed_types = (
            ("task", "codex_exec", "codex_skill")
            if legacy_job_types_enabled
            else ("task",)
        )

        default_skill = (
            str(
                source.get(
                    "MOONMIND_DEFAULT_SKILL",
                    source.get("SPEC_WORKFLOW_DEFAULT_SKILL", "speckit"),
                )
            ).strip()
            or "speckit"
        )
        skill_policy_mode = (
            str(
                source.get(
                    "MOONMIND_SKILL_POLICY_MODE",
                    source.get(
                        "SPEC_WORKFLOW_SKILL_POLICY_MODE",
                        source.get("SKILL_POLICY_MODE", "permissive"),
                    ),
                )
            )
            .strip()
            .lower()
            or "permissive"
        )
        if skill_policy_mode not in {"permissive", "allowlist"}:
            raise ValueError(
                "MOONMIND_SKILL_POLICY_MODE must be one of: permissive, allowlist"
            )
        allowed_skills_csv = str(
            source.get(
                "MOONMIND_ALLOWED_SKILLS",
                source.get("SPEC_WORKFLOW_ALLOWED_SKILLS", default_skill),
            )
        ).strip()
        allowed_skills_items = [
            item.strip() for item in allowed_skills_csv.split(",") if item.strip()
        ]
        if default_skill not in allowed_skills_items:
            allowed_skills_items.append(default_skill)
        allowed_skills = tuple(dict.fromkeys(allowed_skills_items))

        default_codex_model = (
            str(
                source.get(
                    "MOONMIND_CODEX_MODEL",
                    source.get("CODEX_MODEL", ""),
                )
            ).strip()
            or None
        )
        default_codex_effort = (
            str(
                source.get(
                    "MOONMIND_CODEX_EFFORT",
                    source.get(
                        "CODEX_MODEL_REASONING_EFFORT",
                        source.get("MODEL_REASONING_EFFORT", ""),
                    ),
                )
            ).strip()
            or None
        )
        worker_runtime = (
            str(source.get("MOONMIND_WORKER_RUNTIME", "codex")).strip().lower()
            or "codex"
        )
        allowed_worker_runtimes = {"codex", "gemini", "claude", "universal"}
        if worker_runtime not in allowed_worker_runtimes:
            supported = ", ".join(sorted(allowed_worker_runtimes))
            raise ValueError(f"MOONMIND_WORKER_RUNTIME must be one of: {supported}")

        default_gemini_model = (
            str(
                source.get(
                    "MOONMIND_GEMINI_MODEL",
                    source.get("GEMINI_MODEL", ""),
                )
            ).strip()
            or None
        )
        default_gemini_effort = (
            str(
                source.get(
                    "MOONMIND_GEMINI_EFFORT",
                    source.get("GEMINI_REASONING_EFFORT", ""),
                )
            ).strip()
            or None
        )
        default_claude_model = (
            str(
                source.get(
                    "MOONMIND_CLAUDE_MODEL",
                    source.get("CLAUDE_MODEL", ""),
                )
            ).strip()
            or None
        )
        default_claude_effort = (
            str(
                source.get(
                    "MOONMIND_CLAUDE_EFFORT",
                    source.get("CLAUDE_REASONING_EFFORT", ""),
                )
            ).strip()
            or None
        )
        gemini_binary = (
            str(source.get("MOONMIND_GEMINI_BINARY", "gemini")).strip() or "gemini"
        )
        gemini_cli_auth_mode = (
            str(source.get("MOONMIND_GEMINI_CLI_AUTH_MODE", "api_key")).strip().lower()
            or "api_key"
        )
        if gemini_cli_auth_mode not in {"api_key", "oauth"}:
            raise ValueError(
                "MOONMIND_GEMINI_CLI_AUTH_MODE must be one of: api_key, oauth"
            )
        claude_binary = (
            str(source.get("MOONMIND_CLAUDE_BINARY", "claude")).strip() or "claude"
        )

        capability_csv = str(source.get("MOONMIND_WORKER_CAPABILITIES", "")).strip()
        if capability_csv:
            worker_capabilities = tuple(
                dict.fromkeys(
                    [item.strip() for item in capability_csv.split(",") if item.strip()]
                )
            )
        else:
            if worker_runtime == "universal":
                worker_capabilities = ("codex", "gemini", "claude", "git", "gh")
            else:
                worker_capabilities = (worker_runtime, "git", "gh")

        docker_binary = (
            str(source.get("MOONMIND_DOCKER_BINARY", "docker")).strip() or "docker"
        )
        container_workspace_volume = (
            str(source.get("MOONMIND_CONTAINER_WORKSPACE_VOLUME", "")).strip() or None
        )
        container_default_timeout_seconds = int(
            str(source.get("MOONMIND_CONTAINER_TIMEOUT_SECONDS", "3600")).strip()
        )
        if container_default_timeout_seconds < 1:
            raise ValueError("MOONMIND_CONTAINER_TIMEOUT_SECONDS must be >= 1")
        stage_command_timeout_raw = str(
            source.get(
                "MOONMIND_STAGE_COMMAND_TIMEOUT_SECONDS",
                source.get(
                    "SPEC_WORKFLOW_STAGE_COMMAND_TIMEOUT_SECONDS",
                    str(settings.spec_workflow.stage_command_timeout_seconds),
                ),
            )
        ).strip()
        try:
            stage_command_timeout_seconds = int(stage_command_timeout_raw)
        except ValueError as exc:
            raise ValueError(
                "MOONMIND_STAGE_COMMAND_TIMEOUT_SECONDS must be an integer"
            ) from exc
        if stage_command_timeout_seconds < 1:
            raise ValueError("MOONMIND_STAGE_COMMAND_TIMEOUT_SECONDS must be >= 1")

        vault_address = str(source.get("MOONMIND_VAULT_ADDR", "")).strip() or None
        vault_token_file_raw = str(source.get("MOONMIND_VAULT_TOKEN_FILE", "")).strip()
        vault_token_file = Path(vault_token_file_raw) if vault_token_file_raw else None
        vault_token: str | None = None
        if vault_address:
            vault_token = load_vault_token(
                token=str(source.get("MOONMIND_VAULT_TOKEN", "")).strip() or None,
                token_file=vault_token_file,
            )
        vault_namespace = (
            str(source.get("MOONMIND_VAULT_NAMESPACE", "")).strip() or None
        )
        vault_mounts_csv = (
            str(source.get("MOONMIND_VAULT_ALLOWED_MOUNTS", "kv")).strip() or "kv"
        )
        vault_allowed_mounts = tuple(
            dict.fromkeys(
                [item.strip() for item in vault_mounts_csv.split(",") if item.strip()]
            )
        ) or ("kv",)
        vault_timeout_seconds = float(
            str(source.get("MOONMIND_VAULT_TIMEOUT_SECONDS", "10")).strip() or "10"
        )
        if vault_timeout_seconds <= 0:
            raise ValueError("MOONMIND_VAULT_TIMEOUT_SECONDS must be > 0")

        git_user_name = (
            str(
                source.get(
                    "WORKFLOW_GIT_USER_NAME",
                    source.get(
                        "SPEC_WORKFLOW_GIT_USER_NAME",
                        source.get(
                            "MOONMIND_GIT_USER_NAME",
                            settings.spec_workflow.git_user_name or "",
                        ),
                    ),
                )
            ).strip()
            or None
        )
        git_user_email = (
            str(
                source.get(
                    "WORKFLOW_GIT_USER_EMAIL",
                    source.get(
                        "SPEC_WORKFLOW_GIT_USER_EMAIL",
                        source.get(
                            "MOONMIND_GIT_USER_EMAIL",
                            settings.spec_workflow.git_user_email or "",
                        ),
                    ),
                )
            ).strip()
            or None
        )

        live_log_events_enabled_raw = (
            str(source.get("MOONMIND_LIVE_LOG_EVENTS_ENABLED", "true")).strip().lower()
        )
        live_log_events_enabled = live_log_events_enabled_raw not in {
            "0",
            "false",
            "no",
            "off",
            "",
        }
        live_log_events_batch_bytes = int(
            str(source.get("MOONMIND_LIVE_LOG_EVENTS_BATCH_BYTES", "8192")).strip()
        )
        if live_log_events_batch_bytes < 128:
            raise ValueError("MOONMIND_LIVE_LOG_EVENTS_BATCH_BYTES must be >= 128")
        live_log_events_flush_interval_ms = int(
            str(source.get("MOONMIND_LIVE_LOG_EVENTS_FLUSH_INTERVAL_MS", "300")).strip()
        )
        if live_log_events_flush_interval_ms < 10:
            raise ValueError("MOONMIND_LIVE_LOG_EVENTS_FLUSH_INTERVAL_MS must be >= 10")

        live_session_enabled_raw = (
            str(
                source.get(
                    "MOONMIND_LIVE_SESSION_ENABLED_DEFAULT",
                    str(settings.spec_workflow.live_session_enabled_default),
                )
            )
            .strip()
            .lower()
        )
        live_session_enabled_default = live_session_enabled_raw not in {
            "0",
            "false",
            "no",
            "off",
            "",
        }
        live_session_provider = (
            str(
                source.get(
                    "MOONMIND_LIVE_SESSION_PROVIDER",
                    settings.spec_workflow.live_session_provider,
                )
            )
            .strip()
            .lower()
            or "tmate"
        )
        if live_session_provider not in {"tmate"}:
            raise ValueError("MOONMIND_LIVE_SESSION_PROVIDER must be one of: tmate")
        live_session_ttl_minutes = int(
            str(
                source.get(
                    "MOONMIND_LIVE_SESSION_TTL_MINUTES",
                    str(settings.spec_workflow.live_session_ttl_minutes),
                )
            ).strip()
        )
        if live_session_ttl_minutes < 1:
            raise ValueError("MOONMIND_LIVE_SESSION_TTL_MINUTES must be >= 1")
        live_session_rw_grant_ttl_minutes = int(
            str(
                source.get(
                    "MOONMIND_LIVE_SESSION_RW_GRANT_TTL_MINUTES",
                    str(settings.spec_workflow.live_session_rw_grant_ttl_minutes),
                )
            ).strip()
        )
        if live_session_rw_grant_ttl_minutes < 1:
            raise ValueError("MOONMIND_LIVE_SESSION_RW_GRANT_TTL_MINUTES must be >= 1")
        live_session_allow_web_raw = (
            str(
                source.get(
                    "MOONMIND_LIVE_SESSION_ALLOW_WEB",
                    str(settings.spec_workflow.live_session_allow_web),
                )
            )
            .strip()
            .lower()
        )
        live_session_allow_web = live_session_allow_web_raw not in {
            "0",
            "false",
            "no",
            "off",
            "",
        }
        tmate_server_host = (
            str(
                source.get(
                    "MOONMIND_TMATE_SERVER_HOST",
                    settings.spec_workflow.tmate_server_host or "",
                )
            ).strip()
            or None
        )
        live_session_max_concurrent_per_worker = int(
            str(
                source.get(
                    "MOONMIND_LIVE_SESSION_MAX_CONCURRENT_PER_WORKER",
                    str(settings.spec_workflow.live_session_max_concurrent_per_worker),
                )
            ).strip()
        )
        if live_session_max_concurrent_per_worker < 1:
            raise ValueError(
                "MOONMIND_LIVE_SESSION_MAX_CONCURRENT_PER_WORKER must be >= 1"
            )
        enable_task_proposals_raw = (
            str(
                source.get(
                    "MOONMIND_ENABLE_TASK_PROPOSALS",
                    source.get(
                        "ENABLE_TASK_PROPOSALS",
                        str(settings.spec_workflow.enable_task_proposals),
                    ),
                )
            )
            .strip()
            .lower()
        )
        enable_task_proposals = enable_task_proposals_raw not in {
            "0",
            "false",
            "no",
            "off",
            "",
        }
        artifact_upload_incremental_raw = (
            str(source.get("MOONMIND_ARTIFACT_UPLOAD_INCREMENTAL", "true"))
            .strip()
            .lower()
        )
        artifact_upload_incremental = artifact_upload_incremental_raw not in {
            "0",
            "false",
            "no",
            "off",
            "",
        }

        return cls(
            moonmind_url=moonmind_url.rstrip("/"),
            worker_id=worker_id,
            worker_token=worker_token,
            poll_interval_ms=poll_interval_ms,
            pause_poll_interval_ms=pause_poll_interval_ms,
            lease_seconds=lease_seconds,
            workdir=Path(workdir_raw),
            allowed_types=allowed_types,
            legacy_job_types_enabled=legacy_job_types_enabled,
            worker_runtime=worker_runtime,
            default_skill=default_skill,
            skill_policy_mode=skill_policy_mode,
            allowed_skills=allowed_skills,
            default_codex_model=default_codex_model,
            default_codex_effort=default_codex_effort,
            default_gemini_model=default_gemini_model,
            default_gemini_effort=default_gemini_effort,
            default_claude_model=default_claude_model,
            default_claude_effort=default_claude_effort,
            gemini_binary=gemini_binary,
            gemini_cli_auth_mode=gemini_cli_auth_mode,
            claude_binary=claude_binary,
            worker_capabilities=worker_capabilities,
            docker_binary=docker_binary,
            container_workspace_volume=container_workspace_volume,
            container_default_timeout_seconds=container_default_timeout_seconds,
            stage_command_timeout_seconds=stage_command_timeout_seconds,
            vault_address=vault_address,
            vault_token=vault_token,
            vault_token_file=vault_token_file,
            vault_namespace=vault_namespace,
            vault_allowed_mounts=vault_allowed_mounts,
            vault_timeout_seconds=vault_timeout_seconds,
            git_user_name=git_user_name,
            git_user_email=git_user_email,
            live_log_events_enabled=live_log_events_enabled,
            live_log_events_batch_bytes=live_log_events_batch_bytes,
            live_log_events_flush_interval_ms=live_log_events_flush_interval_ms,
            live_session_enabled_default=live_session_enabled_default,
            live_session_provider=live_session_provider,
            live_session_ttl_minutes=live_session_ttl_minutes,
            live_session_rw_grant_ttl_minutes=live_session_rw_grant_ttl_minutes,
            live_session_allow_web=live_session_allow_web,
            tmate_server_host=tmate_server_host,
            live_session_max_concurrent_per_worker=live_session_max_concurrent_per_worker,
            enable_task_proposals=enable_task_proposals,
            artifact_upload_incremental=artifact_upload_incremental,
        )


@dataclass(frozen=True, slots=True)
class ClaimedJob:
    """Normalized job returned by queue claim API."""

    id: UUID
    type: str
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class QueueSystemStatus:
    """Global pause metadata returned alongside queue responses."""

    workers_paused: bool
    mode: str | None
    reason: str | None
    version: int
    requested_at: datetime | None
    updated_at: datetime | None


@dataclass(frozen=True, slots=True)
class QueueClaimResult:
    """Structured claim response including optional job and system metadata."""

    job: ClaimedJob | None
    system: QueueSystemStatus


@dataclass(frozen=True, slots=True)
class QueueHeartbeatResult:
    """Structured heartbeat response with job payload and pause metadata."""

    job: dict[str, Any]
    system: QueueSystemStatus


@dataclass(frozen=True, slots=True)
class PreparedTaskWorkspace:
    """Resolved workspace context generated by the prepare stage."""

    job_root: Path
    repo_dir: Path
    artifacts_dir: Path
    prepare_log_path: Path
    execute_log_path: Path
    publish_log_path: Path
    task_context_path: Path
    publish_result_path: Path
    default_branch: str
    starting_branch: str
    new_branch: str | None
    working_branch: str
    workdir_mode: str
    repo_command_env: dict[str, str] | None
    publish_command_env: dict[str, str] | None


@dataclass(frozen=True, slots=True)
class LiveSessionHandle:
    """Worker-owned live session context for active queue job execution."""

    job_id: UUID
    session_name: str
    socket_path: Path
    config_path: Path | None
    log_path: Path
    status: str


@dataclass(frozen=True, slots=True)
class TaskAuthContext:
    """Resolved auth references and runtime token-source metadata."""

    repo_auth_ref: str | None
    publish_auth_ref: str | None
    repo_auth_source: str
    publish_auth_source: str | None
    repo_command_env: dict[str, str] | None
    publish_command_env: dict[str, str] | None


@dataclass(frozen=True, slots=True)
class ContainerCacheVolume:
    """Normalized cache volume mount for container task execution."""

    name: str
    target: str


@dataclass(frozen=True, slots=True)
class ContainerTaskSpec:
    """Normalized task.container payload used by execute stage."""

    image: str
    command: tuple[str, ...]
    workdir: str | None
    env: dict[str, str]
    artifacts_subdir: str
    timeout_seconds: int
    pull_mode: str
    cpus: str | None
    memory: str | None
    cache_volumes: tuple[ContainerCacheVolume, ...]


@dataclass(frozen=True, slots=True)
class ResolvedTaskStep:
    """Runtime-resolved step metadata for execute-stage iteration."""

    step_index: int
    step_id: str
    title: str | None
    instructions: str | None
    effective_skill_id: str
    effective_skill_args: dict[str, Any]
    has_step_instructions: bool


class QueueApiClient:
    """HTTP client wrapper for queue and artifact endpoints."""

    def __init__(
        self,
        *,
        base_url: str,
        worker_token: str | None,
        timeout_seconds: float = 30.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        headers: dict[str, str] = {"Accept": "application/json"}
        if worker_token:
            headers["X-MoonMind-Worker-Token"] = worker_token
        self._client = client or httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout_seconds,
            headers=headers,
        )
        self._owns_client = client is None

    async def aclose(self) -> None:
        """Close the underlying HTTP client when owned by this wrapper."""

        if self._owns_client:
            await self._client.aclose()

    async def claim_job(
        self,
        *,
        worker_id: str,
        lease_seconds: int,
        allowed_types: Sequence[str] | None = None,
        worker_capabilities: Sequence[str] | None = None,
    ) -> QueueClaimResult:
        payload: dict[str, Any] = {
            "workerId": worker_id,
            "leaseSeconds": lease_seconds,
        }
        if allowed_types:
            payload["allowedTypes"] = list(allowed_types)
        if worker_capabilities:
            payload["workerCapabilities"] = list(worker_capabilities)
        data = await self._post_json("/api/queue/jobs/claim", json=payload)
        job_data = data.get("job")
        job = None
        if job_data:
            job = ClaimedJob(
                id=UUID(str(job_data["id"])),
                type=str(job_data["type"]),
                payload=dict(job_data.get("payload") or {}),
            )
        system = self._parse_system_metadata(data.get("system"))
        return QueueClaimResult(job=job, system=system)

    async def heartbeat(
        self,
        *,
        job_id: UUID,
        worker_id: str,
        lease_seconds: int,
    ) -> QueueHeartbeatResult:
        data = await self._post_json(
            f"/api/queue/jobs/{job_id}/heartbeat",
            json={"workerId": worker_id, "leaseSeconds": lease_seconds},
        )
        system = self._parse_system_metadata(data.get("system"))
        return QueueHeartbeatResult(job=data, system=system)

    async def ack_cancel(
        self,
        *,
        job_id: UUID,
        worker_id: str,
        message: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"workerId": worker_id}
        if message:
            payload["message"] = message
        return await self._post_json(
            f"/api/queue/jobs/{job_id}/cancel/ack",
            json=payload,
        )

    async def complete_job(
        self,
        *,
        job_id: UUID,
        worker_id: str,
        result_summary: str | None,
    ) -> None:
        payload = {"workerId": worker_id}
        if result_summary:
            payload["resultSummary"] = result_summary
        await self._post_json(f"/api/queue/jobs/{job_id}/complete", json=payload)

    async def fail_job(
        self,
        *,
        job_id: UUID,
        worker_id: str,
        error_message: str,
        retryable: bool = False,
    ) -> None:
        await self._post_json(
            f"/api/queue/jobs/{job_id}/fail",
            json={
                "workerId": worker_id,
                "errorMessage": error_message,
                "retryable": retryable,
            },
        )

    async def append_event(
        self,
        *,
        job_id: UUID,
        worker_id: str,
        level: str,
        message: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        body: dict[str, Any] = {
            "workerId": worker_id,
            "level": level,
            "message": message,
        }
        if payload is not None:
            body["payload"] = payload
        await self._post_json(f"/api/queue/jobs/{job_id}/events", json=body)

    async def report_live_session(
        self,
        *,
        job_id: UUID,
        worker_id: str,
        status: str,
        worker_hostname: str | None = None,
        provider: str | None = None,
        attach_ro: str | None = None,
        attach_rw: str | None = None,
        web_ro: str | None = None,
        web_rw: str | None = None,
        tmate_session_name: str | None = None,
        tmate_socket_path: str | None = None,
        expires_at: str | None = None,
        error_message: str | None = None,
    ) -> dict[str, Any]:
        """Report live-session lifecycle updates for a task run."""

        payload: dict[str, Any] = {
            "workerId": worker_id,
            "status": status,
        }
        if worker_hostname:
            payload["workerHostname"] = worker_hostname
        if provider:
            payload["provider"] = provider
        if attach_ro:
            payload["attachRo"] = attach_ro
        if attach_rw:
            payload["attachRw"] = attach_rw
        if web_ro:
            payload["webRo"] = web_ro
        if web_rw:
            payload["webRw"] = web_rw
        if tmate_session_name:
            payload["tmateSessionName"] = tmate_session_name
        if tmate_socket_path:
            payload["tmateSocketPath"] = tmate_socket_path
        if expires_at:
            payload["expiresAt"] = expires_at
        if error_message:
            payload["errorMessage"] = error_message
        return await self._post_json(
            f"/api/task-runs/{job_id}/live-session/report",
            json=payload,
        )

    async def heartbeat_live_session(
        self,
        *,
        job_id: UUID,
        worker_id: str,
    ) -> dict[str, Any]:
        """Send live-session heartbeat updates."""

        return await self._post_json(
            f"/api/task-runs/{job_id}/live-session/heartbeat",
            json={"workerId": worker_id},
        )

    @staticmethod
    def _parse_system_metadata(node: Any) -> QueueSystemStatus:
        metadata = node if isinstance(node, Mapping) else {}
        try:
            version = int(metadata.get("version") or 1)
        except (TypeError, ValueError):
            version = 1
        if version < 1:
            version = 1
        return QueueSystemStatus(
            workers_paused=bool(metadata.get("workersPaused")),
            mode=str(metadata.get("mode") or "").strip() or None,
            reason=str(metadata.get("reason") or "").strip() or None,
            version=version,
            requested_at=QueueApiClient._parse_iso_datetime(
                metadata.get("requestedAt")
            ),
            updated_at=QueueApiClient._parse_iso_datetime(metadata.get("updatedAt")),
        )

    @staticmethod
    def _parse_iso_datetime(value: Any) -> datetime | None:
        text = str(value or "").strip()
        if not text:
            return None
        normalized = text.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(normalized)
        except ValueError:
            return None

    async def get_live_session(self, *, job_id: UUID) -> dict[str, Any] | None:
        """Fetch current live-session payload; return ``None`` when no session exists."""

        try:
            path = f"/api/task-runs/{job_id}/live-session/worker"
            response = await self._client.get(path)
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return dict(response.json()) if response.content else {}
        except httpx.HTTPError as exc:
            raise QueueClientError(f"queue API request failed: {path}: {exc}") from exc

    async def upload_artifact(
        self,
        *,
        job_id: UUID,
        worker_id: str,
        artifact: ArtifactUpload,
    ) -> None:
        if not artifact.path.exists():
            raise QueueClientError(f"artifact file does not exist: {artifact.path}")

        content_type = artifact.content_type or "application/octet-stream"
        data: dict[str, str] = {
            "name": artifact.name,
            "workerId": worker_id,
        }
        if artifact.content_type:
            data["contentType"] = artifact.content_type

        digest = artifact.digest or self._sha256_file(artifact.path)
        if digest:
            data["digest"] = digest

        with artifact.path.open("rb") as handle:
            files = {
                "file": (artifact.path.name, handle, content_type),
            }
            try:
                response = await self._client.post(
                    f"/api/queue/jobs/{job_id}/artifacts/upload",
                    data=data,
                    files=files,
                )
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise QueueClientError(
                    f"artifact upload failed for job {job_id}: {exc}"
                ) from exc

    async def _post_json(self, path: str, *, json: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self._client.post(path, json=json)
            response.raise_for_status()
            return dict(response.json()) if response.content else {}
        except httpx.HTTPError as exc:
            raise QueueClientError(f"queue API request failed: {path}: {exc}") from exc

    async def create_task_proposal(self, *, proposal: dict[str, Any]) -> dict[str, Any]:
        """Submit a task proposal to the MoonMind API."""

        return await self._post_json("/api/proposals", json=proposal)

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(8192), b""):
                digest.update(chunk)
        return f"sha256:{digest.hexdigest()}"


class CodexWorker:
    """Single-worker daemon that claims and executes queue jobs."""

    def __init__(
        self,
        *,
        config: CodexWorkerConfig,
        queue_client: QueueApiClient,
        codex_exec_handler: CodexExecHandler,
    ) -> None:
        self._config = config
        self._queue_client = queue_client
        self._codex_exec_handler = codex_exec_handler
        self._pause_poll_interval_seconds = max(
            1.0, self._config.pause_poll_interval_ms / 1000.0
        )
        self._last_pause_version_logged: int | None = None
        self._latest_system_metadata: QueueSystemStatus | None = None
        self._last_run_outcome: str = "idle"
        self._secret_redactor = moonmind_logging.SecretRedactor.from_environ(
            placeholder="[REDACTED]"
        )
        self._dynamic_redaction_values: set[str] = set()
        self._active_cancel_event: asyncio.Event | None = None
        self._active_pause_event: asyncio.Event | None = None
        self._active_live_session: LiveSessionHandle | None = None
        self._live_session_start_lock = asyncio.Lock()
        self._vault_secret_resolver: VaultSecretResolver | None = None
        if self._config.vault_address and self._config.vault_token:
            self._vault_secret_resolver = VaultSecretResolver(
                address=self._config.vault_address,
                token=self._config.vault_token,
                namespace=self._config.vault_namespace,
                allowed_mounts=self._config.vault_allowed_mounts,
                timeout_seconds=self._config.vault_timeout_seconds,
            )

    async def run_forever(self, *, stop_event: asyncio.Event | None = None) -> None:
        """Continuously process queue jobs until asked to stop."""

        run_stop = stop_event or asyncio.Event()
        try:
            while not run_stop.is_set():
                try:
                    claimed_work = await self.run_once()
                except Exception:
                    logger.exception("Unhandled exception in CodexWorker.run_forever")
                    await asyncio.sleep(self._config.poll_interval_ms / 1000.0)
                    continue
                if claimed_work:
                    continue
                if self._last_run_outcome == "paused":
                    await asyncio.sleep(self._pause_poll_interval_seconds)
                else:
                    await asyncio.sleep(self._config.poll_interval_ms / 1000.0)
        finally:
            if self._vault_secret_resolver is not None:
                with suppress(Exception):
                    await self._vault_secret_resolver.aclose()

    async def run_once(self) -> bool:
        """Claim and process one job if available."""

        outcome = await self._run_once_internal()
        self._last_run_outcome = outcome
        return outcome == "claimed"

    async def _run_once_internal(self) -> str:
        """Internal implementation that returns a descriptive outcome."""

        claim_result = await self._claim_next_job()
        metadata = claim_result.system
        self._handle_system_metadata(metadata)
        job = claim_result.job
        if job is None:
            if metadata.workers_paused:
                return "paused"
            return "idle"

        supported_types = {CANONICAL_TASK_JOB_TYPE, *LEGACY_TASK_JOB_TYPES}
        if job.type not in supported_types:
            await self._emit_event(
                job_id=job.id,
                level="error",
                message="Unsupported job type",
                payload={"jobType": job.type},
            )
            await self._queue_client.fail_job(
                job_id=job.id,
                worker_id=self._config.worker_id,
                error_message=f"unsupported job type: {job.type}",
                retryable=False,
            )
            return "claimed"
        if (
            not self._config.legacy_job_types_enabled
            and job.type in LEGACY_TASK_JOB_TYPES
        ):
            await self._emit_event(
                job_id=job.id,
                level="error",
                message="Legacy queue job types are disabled for this worker",
                payload={"jobType": job.type},
            )
            await self._queue_client.fail_job(
                job_id=job.id,
                worker_id=self._config.worker_id,
                error_message=f"legacy job type disabled: {job.type}",
                retryable=False,
            )
            return "claimed"

        try:
            canonical_payload = build_canonical_task_view(
                job_type=job.type,
                payload=job.payload,
            )
        except TaskContractError as exc:
            await self._emit_event(
                job_id=job.id,
                level="error",
                message="Job payload failed task-contract normalization",
                payload={"jobType": job.type, "error": str(exc)},
            )
            await self._queue_client.fail_job(
                job_id=job.id,
                worker_id=self._config.worker_id,
                error_message=f"invalid job payload: {exc}",
                retryable=False,
            )
            return "claimed"

        runtime_mode = (
            str(canonical_payload.get("targetRuntime") or "codex").strip().lower()
        )
        if runtime_mode not in SUPPORTED_EXECUTION_RUNTIMES:
            await self._emit_event(
                job_id=job.id,
                level="error",
                message="Task runtime is not recognized by worker",
                payload={
                    "jobType": job.type,
                    "targetRuntime": runtime_mode,
                },
            )
            await self._queue_client.fail_job(
                job_id=job.id,
                worker_id=self._config.worker_id,
                error_message=f"unsupported task runtime: {runtime_mode}",
                retryable=False,
            )
            return "claimed"
        if not self._runtime_can_execute(runtime_mode):
            await self._emit_event(
                job_id=job.id,
                level="error",
                message="Task runtime is not executable by this worker runtime mode",
                payload={
                    "jobType": job.type,
                    "targetRuntime": runtime_mode,
                    "workerRuntime": self._config.worker_runtime,
                },
            )
            await self._queue_client.fail_job(
                job_id=job.id,
                worker_id=self._config.worker_id,
                error_message=(
                    "unsupported task runtime for worker "
                    f"({self._config.worker_runtime}): {runtime_mode}"
                ),
                retryable=False,
            )
            return "claimed"

        policy_error = self._validate_required_job_policy(canonical_payload)
        if policy_error is not None:
            await self._emit_event(
                job_id=job.id,
                level="error",
                message="Task rejected by worker policy requirements",
                payload={
                    "jobType": job.type,
                    "targetRuntime": runtime_mode,
                    "error": policy_error,
                },
            )
            await self._queue_client.fail_job(
                job_id=job.id,
                worker_id=self._config.worker_id,
                error_message=policy_error,
                retryable=False,
            )
            return "claimed"

        resolved_steps = self._resolve_task_steps(canonical_payload)
        skill_meta = self._execution_metadata(canonical_payload, resolved_steps)
        await self._emit_event(
            job_id=job.id,
            level="info",
            message="Worker claimed job",
            payload={"jobType": job.type, "targetRuntime": runtime_mode, **skill_meta},
        )

        selected_skills = tuple(
            sorted(
                {
                    step.effective_skill_id
                    for step in resolved_steps
                    if step.effective_skill_id != "auto"
                }
            )
        )
        if self._config.skill_policy_mode == "allowlist":
            disallowed = sorted(
                skill
                for skill in selected_skills
                if skill not in self._config.allowed_skills
            )
            if disallowed:
                await self._emit_event(
                    job_id=job.id,
                    level="error",
                    message="Skill is not allowlisted for this worker",
                    payload={
                        "jobType": job.type,
                        "selectedSkills": disallowed,
                        **skill_meta,
                    },
                )
                await self._queue_client.fail_job(
                    job_id=job.id,
                    worker_id=self._config.worker_id,
                    error_message=f"skill not allowlisted: {', '.join(disallowed)}",
                    retryable=False,
                )
                return "claimed"

        heartbeat_stop = asyncio.Event()
        cancel_requested_event = asyncio.Event()
        pause_requested_event = asyncio.Event()
        self._active_cancel_event = cancel_requested_event
        self._active_pause_event = pause_requested_event
        self._active_live_session = None
        live_session_cwd = self._config.workdir / str(job.id)
        live_session_log_path = live_session_cwd / "artifacts" / "logs" / "prepare.log"
        heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(
                job_id=job.id,
                stop_event=heartbeat_stop,
                cancel_event=cancel_requested_event,
                pause_event=pause_requested_event,
                live_session_bootstrap_cwd=live_session_cwd,
                live_session_bootstrap_log_path=live_session_log_path,
            )
        )

        staged_artifacts: list[ArtifactUpload] = []
        uploaded_artifact_names: set[str] = set()
        optional_artifact_failures: set[str] = set()

        async def _upload_new_artifacts(
            artifacts: Sequence[ArtifactUpload],
        ) -> None:
            if not artifacts:
                return
            failed_optionals = await self._upload_artifacts(
                job_id=job.id,
                artifacts=artifacts,
                uploaded_artifact_names=uploaded_artifact_names,
            )
            optional_artifact_failures.update(failed_optionals)

        async def _flush_staged_artifacts() -> None:
            if not staged_artifacts:
                return
            await _upload_new_artifacts(staged_artifacts)
            staged_artifacts.clear()

        async def _handle_incremental_artifacts(
            artifacts: Sequence[ArtifactUpload],
        ) -> None:
            if not self._config.artifact_upload_incremental:
                return
            await _upload_new_artifacts(artifacts)

        try:
            stage_plan = build_task_stage_plan(canonical_payload)
            await self._emit_event(
                job_id=job.id,
                level="info",
                message="moonmind.task.plan",
                payload={"jobType": job.type, "stages": stage_plan, **skill_meta},
            )
            await self._raise_if_cancel_requested(cancel_event=cancel_requested_event)
            await self._wait_if_paused(
                job_id=job.id,
                pause_event=pause_requested_event,
                cancel_event=cancel_requested_event,
            )

            prepared = await self._run_prepare_stage(
                job_id=job.id,
                canonical_payload=canonical_payload,
                source_payload=job.payload,
                selected_skills=selected_skills,
                job_type=job.type,
                skill_meta=skill_meta,
            )
            staged_artifacts.extend(self._prepare_stage_artifacts(prepared))
            await self._raise_if_cancel_requested(cancel_event=cancel_requested_event)
            await self._wait_if_paused(
                job_id=job.id,
                pause_event=pause_requested_event,
                cancel_event=cancel_requested_event,
            )

            await self._emit_stage_event(
                job_id=job.id,
                stage="moonmind.task.execute",
                status="started",
                payload={"jobType": job.type, **skill_meta},
            )
            result = await self._run_execute_stage(
                job_id=job.id,
                canonical_payload=canonical_payload,
                source_payload=job.payload,
                runtime_mode=runtime_mode,
                resolved_steps=resolved_steps,
                prepared=prepared,
                artifact_callback=_handle_incremental_artifacts,
            )
            execute_artifacts = self._normalize_execute_artifacts(
                result_artifacts=result.artifacts,
                execute_log_path=prepared.execute_log_path,
            )
            staged_artifacts.extend(execute_artifacts)
            await self._emit_stage_event(
                job_id=job.id,
                stage="moonmind.task.execute",
                status="finished" if result.succeeded else "failed",
                payload={"jobType": job.type, "summary": result.summary, **skill_meta},
            )
            await self._raise_if_cancel_requested(cancel_event=cancel_requested_event)
            if result.succeeded:
                await self._wait_if_paused(
                    job_id=job.id,
                    pause_event=pause_requested_event,
                    cancel_event=cancel_requested_event,
                )
                publish_note = await self._run_publish_stage(
                    job_id=job.id,
                    canonical_payload=canonical_payload,
                    prepared=prepared,
                    skill_meta=skill_meta,
                    job_type=job.type,
                    staged_artifacts=staged_artifacts,
                )
                if publish_note:
                    base_summary = result.summary or "task completed"
                    result = type(result)(
                        succeeded=True,
                        summary=f"{base_summary}; {publish_note}",
                        error_message=None,
                        artifacts=result.artifacts,
                    )
                await self._raise_if_cancel_requested(
                    cancel_event=cancel_requested_event
                )
                if self._config.enable_task_proposals:
                    with suppress(Exception):
                        await self._maybe_submit_task_proposals(
                            job=job,
                            prepared=prepared,
                        )

            await _flush_staged_artifacts()
            if optional_artifact_failures:
                await self._emit_event(
                    job_id=job.id,
                    level="warn",
                    message="Optional artifact uploads failed",
                    payload={
                        "failedCount": len(optional_artifact_failures),
                        "artifacts": sorted(optional_artifact_failures),
                    },
                )
                if result.succeeded:
                    base_summary = result.summary or "task completed"
                    result = type(result)(
                        succeeded=True,
                        summary=(
                            f"{base_summary}; "
                            f"{len(optional_artifact_failures)} optional artifact upload(s) failed"
                        ),
                        error_message=None,
                        artifacts=result.artifacts,
                    )
            if cancel_requested_event.is_set():
                await self._acknowledge_cancellation(
                    job_id=job.id,
                    message="cancellation requested",
                )
                return "claimed"
            if result.succeeded:
                await self._queue_client.complete_job(
                    job_id=job.id,
                    worker_id=self._config.worker_id,
                    result_summary=result.summary,
                )
                await self._emit_event(
                    job_id=job.id,
                    level="info",
                    message="Job completed",
                    payload={
                        "summary": result.summary,
                        "jobType": job.type,
                        **skill_meta,
                    },
                )
            else:
                terminal_error = self._redact_text(
                    result.error_message or "codex_exec failed"
                )
                await self._queue_client.fail_job(
                    job_id=job.id,
                    worker_id=self._config.worker_id,
                    error_message=terminal_error,
                    retryable=False,
                )
                await self._emit_event(
                    job_id=job.id,
                    level="error",
                    message="Job failed",
                    payload={
                        "error": terminal_error,
                        "jobType": job.type,
                        **skill_meta,
                    },
                )
        except Exception as exc:
            if staged_artifacts:
                try:
                    failed_optionals = await self._upload_artifacts(
                        job_id=job.id,
                        artifacts=staged_artifacts,
                        uploaded_artifact_names=uploaded_artifact_names,
                    )
                    optional_artifact_failures.update(failed_optionals)
                    staged_artifacts.clear()
                except Exception:
                    logger.warning(
                        "Best-effort artifact upload failed during exception path",
                        exc_info=True,
                    )
            if cancel_requested_event.is_set() or isinstance(
                exc, (JobCancellationRequested, CommandCancelledError)
            ):
                await self._acknowledge_cancellation(
                    job_id=job.id,
                    message=self._redact_text(str(exc) or "cancellation requested"),
                )
                return "claimed"
            terminal_error = self._redact_text(str(exc))
            await self._queue_client.fail_job(
                job_id=job.id,
                worker_id=self._config.worker_id,
                error_message=terminal_error,
                retryable=False,
            )
            await self._emit_event(
                job_id=job.id,
                level="error",
                message="Worker exception while executing job",
                payload={"error": terminal_error, "jobType": job.type, **skill_meta},
            )
        finally:
            self._active_cancel_event = None
            self._active_pause_event = None
            heartbeat_stop.set()
            heartbeat_task.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat_task
            await self._teardown_live_session(job_id=job.id)

        return "claimed"

    async def _claim_next_job(self) -> QueueClaimResult:
        """Claim next eligible job using policy-safe claim parameters."""

        return await self._queue_client.claim_job(
            worker_id=self._config.worker_id,
            lease_seconds=self._config.lease_seconds,
            allowed_types=self._config.allowed_types,
            worker_capabilities=self._config.worker_capabilities,
        )

    def _handle_system_metadata(self, metadata: QueueSystemStatus) -> None:
        """Log pause status changes once per version."""

        self._latest_system_metadata = metadata
        if metadata.workers_paused:
            if self._last_pause_version_logged != metadata.version:
                pause_mode = (metadata.mode or "drain").lower()
                reason = metadata.reason or "operator request"
                logger.warning(
                    "Worker pause active (%s mode): %s",
                    pause_mode,
                    reason,
                )
                self._last_pause_version_logged = metadata.version
        elif self._last_pause_version_logged is not None:
            logger.info("Worker pause cleared (version=%s)", metadata.version)
            self._last_pause_version_logged = None

    @staticmethod
    async def _raise_if_cancel_requested(*, cancel_event: asyncio.Event) -> None:
        """Raise when a cancellation request has been observed for active job."""

        if cancel_event.is_set():
            raise JobCancellationRequested("cancellation requested by user")

    async def _wait_if_paused(
        self,
        *,
        job_id: UUID,
        pause_event: asyncio.Event,
        cancel_event: asyncio.Event,
    ) -> None:
        """Block at safe checkpoints while operator pause is active."""

        was_paused = False
        while pause_event.is_set():
            if cancel_event.is_set():
                raise JobCancellationRequested("cancellation requested by user")
            if not was_paused:
                await self._emit_event(
                    job_id=job_id,
                    level="warn",
                    message="task.control.pause.active",
                    payload={"status": "paused"},
                )
                was_paused = True
            await asyncio.sleep(1.0)
        if was_paused:
            await self._emit_event(
                job_id=job_id,
                level="info",
                message="task.control.pause.cleared",
                payload={"status": "resumed"},
            )

    async def _acknowledge_cancellation(self, *, job_id: UUID, message: str) -> None:
        """Acknowledge cancellation and avoid terminal success/failure transitions."""

        await self._emit_event(
            job_id=job_id,
            level="warn",
            message="Job cancellation requested; stopping",
            payload={"workerId": self._config.worker_id, "message": message},
        )
        try:
            await self._queue_client.ack_cancel(
                job_id=job_id,
                worker_id=self._config.worker_id,
                message=message,
            )
        except Exception:
            # Fallback to fail_job so ownership is still released on transient ack issues.
            await self._queue_client.fail_job(
                job_id=job_id,
                worker_id=self._config.worker_id,
                error_message="cancellation requested but cancel ack failed",
                retryable=False,
            )

    def _resolve_task_steps(
        self, canonical_payload: Mapping[str, Any]
    ) -> list[ResolvedTaskStep]:
        """Resolve canonical payload into ordered runtime step metadata."""

        task_node = canonical_payload.get("task")
        task = task_node if isinstance(task_node, Mapping) else {}
        task_skill_node = task.get("skill")
        task_skill = task_skill_node if isinstance(task_skill_node, Mapping) else {}
        task_skill_id = str(task_skill.get("id") or "auto").strip() or "auto"
        task_skill_args_node = task_skill.get("args")
        task_skill_args = (
            dict(task_skill_args_node)
            if isinstance(task_skill_args_node, Mapping)
            else {}
        )

        raw_steps = task.get("steps")
        if not isinstance(raw_steps, list) or not raw_steps:
            return [
                ResolvedTaskStep(
                    step_index=0,
                    step_id="step-1",
                    title=None,
                    instructions=None,
                    effective_skill_id=task_skill_id,
                    effective_skill_args=(
                        dict(task_skill_args) if task_skill_id != "auto" else {}
                    ),
                    has_step_instructions=False,
                )
            ]

        resolved: list[ResolvedTaskStep] = []
        for index, raw_step in enumerate(raw_steps):
            step = raw_step if isinstance(raw_step, Mapping) else {}
            step_id = str(step.get("id") or "").strip() or f"step-{index + 1}"
            step_title = str(step.get("title") or "").strip() or None
            step_instructions = str(step.get("instructions") or "").strip() or None

            step_skill_node = step.get("skill")
            step_skill = (
                step_skill_node if isinstance(step_skill_node, Mapping) else None
            )
            if step_skill is not None:
                explicit_step_skill = str(step_skill.get("id") or "").strip()
                if explicit_step_skill:
                    effective_skill_id = explicit_step_skill
                    step_skill_args_node = step_skill.get("args")
                    effective_skill_args = (
                        dict(step_skill_args_node)
                        if isinstance(step_skill_args_node, Mapping)
                        else {}
                    )
                else:
                    effective_skill_id = task_skill_id
                    effective_skill_args = dict(task_skill_args)
            else:
                effective_skill_id = task_skill_id
                effective_skill_args = dict(task_skill_args)

            if not effective_skill_id:
                effective_skill_id = "auto"
            if effective_skill_id == "auto":
                effective_skill_args = {}

            resolved.append(
                ResolvedTaskStep(
                    step_index=index,
                    step_id=step_id,
                    title=step_title,
                    instructions=step_instructions,
                    effective_skill_id=effective_skill_id,
                    effective_skill_args=effective_skill_args,
                    has_step_instructions=step_instructions is not None,
                )
            )
        return resolved

    def _execution_metadata(
        self,
        canonical_payload: Mapping[str, Any],
        resolved_steps: Sequence[ResolvedTaskStep],
    ) -> dict[str, Any]:
        """Return normalized skill execution metadata for job events."""

        task_node = canonical_payload.get("task")
        runtime_node = (
            task_node.get("runtime") if isinstance(task_node, Mapping) else None
        )
        if isinstance(runtime_node, Mapping):
            selected_model = str(runtime_node.get("model") or "").strip() or None
            selected_effort = str(runtime_node.get("effort") or "").strip() or None
        else:
            selected_model = None
            selected_effort = None

        selected_skills = sorted(
            {
                step.effective_skill_id
                for step in resolved_steps
                if step.effective_skill_id != "auto"
            }
        )
        used_skills = bool(selected_skills)
        used_fallback = any(skill != "speckit" for skill in selected_skills)
        if not used_skills:
            selected_skill = "auto"
            execution_path = "direct_only"
        elif len(selected_skills) == 1:
            selected_skill = selected_skills[0]
            execution_path = (
                "skill" if selected_skill == "speckit" else "direct_fallback"
            )
        else:
            selected_skill = "multiple"
            execution_path = "direct_fallback"

        metadata: dict[str, Any] = {
            "selectedSkill": selected_skill,
            "selectedSkills": selected_skills,
            "stepCount": len(resolved_steps),
            "executionPath": execution_path,
            "usedSkills": used_skills,
            "usedFallback": used_fallback,
            "shadowModeRequested": False,
            "runtimeModel": selected_model,
            "runtimeEffort": selected_effort,
        }
        metadata.update(self._rag_capability_metadata())
        return metadata

    @staticmethod
    def _rag_capability_metadata() -> dict[str, Any]:
        """Describe worker retrieval capability for queue events and task context."""

        try:
            settings = RagRuntimeSettings.from_env()
        except Exception:
            return {
                "ragAvailable": False,
                "ragMode": "unavailable",
            }
        if not settings.rag_enabled:
            return {
                "ragAvailable": False,
                "ragMode": "disabled",
            }
        transport = settings.resolved_transport(None)
        mode = "direct-qdrant" if transport == "direct" else "retrieval-gateway"
        return {
            "ragAvailable": True,
            "ragMode": mode,
            "ragCommand": "moonmind rag search --query '<query>' --output-file <path>",
        }

    @staticmethod
    def _safe_workdir_mode(source_payload: Mapping[str, Any]) -> str:
        candidate = str(source_payload.get("workdirMode", "fresh_clone")).strip()
        if candidate in {"fresh_clone", "reuse"}:
            return candidate
        return "fresh_clone"

    def _build_exec_payload(
        self,
        *,
        canonical_payload: Mapping[str, Any],
        source_payload: Mapping[str, Any],
        instruction_override: str | None = None,
        ref_override: str | None = None,
        publish_mode_override: str | None = None,
        publish_base_override: str | None = None,
        workdir_mode_override: str | None = None,
        include_ref: bool = True,
    ) -> dict[str, Any]:
        task_node = canonical_payload.get("task")
        task = task_node if isinstance(task_node, Mapping) else {}
        runtime_node = task.get("runtime")
        runtime = runtime_node if isinstance(runtime_node, Mapping) else {}
        git_node = task.get("git")
        git = git_node if isinstance(git_node, Mapping) else {}
        publish_node = task.get("publish")
        publish = publish_node if isinstance(publish_node, Mapping) else {}

        payload: dict[str, Any] = {
            "repository": canonical_payload.get("repository"),
            "instruction": (
                instruction_override
                if instruction_override is not None
                else task.get("instructions")
            ),
            "workdirMode": workdir_mode_override
            or self._safe_workdir_mode(source_payload),
            "publish": {
                "mode": publish_mode_override or publish.get("mode") or "pr",
                "baseBranch": (
                    publish_base_override
                    if publish_base_override is not None
                    else publish.get("prBaseBranch")
                ),
            },
        }
        selected_ref = (
            ref_override if ref_override is not None else git.get("startingBranch")
        )
        if include_ref and selected_ref:
            payload["ref"] = selected_ref

        codex_overrides: dict[str, str] = {}
        runtime_model = str(runtime.get("model") or "").strip()
        runtime_effort = str(runtime.get("effort") or "").strip()
        if runtime_model:
            codex_overrides["model"] = runtime_model
        if runtime_effort:
            codex_overrides["effort"] = runtime_effort
        if codex_overrides:
            payload["codex"] = codex_overrides
        return payload

    async def _run_prepare_stage(
        self,
        *,
        job_id: UUID,
        canonical_payload: Mapping[str, Any],
        source_payload: Mapping[str, Any],
        selected_skills: Sequence[str],
        job_type: str,
        skill_meta: Mapping[str, Any],
    ) -> PreparedTaskWorkspace:
        """Prepare workspace, repository checkout, and branch selection."""

        await self._emit_stage_event(
            job_id=job_id,
            stage="moonmind.task.prepare",
            status="started",
            payload={"jobType": job_type, **dict(skill_meta)},
        )
        try:
            job_root = self._config.workdir / str(job_id)
            artifacts_dir = job_root / "artifacts"
            logs_dir = artifacts_dir / "logs"
            prepare_log_path = logs_dir / "prepare.log"
            execute_log_path = logs_dir / "execute.log"
            publish_log_path = logs_dir / "publish.log"
            task_context_path = artifacts_dir / "task_context.json"
            publish_result_path = artifacts_dir / "publish_result.json"
            repo_dir = job_root / "repo"
            home_dir = job_root / "home"
            skills_active_path = job_root / "skills_active"

            artifacts_dir.mkdir(parents=True, exist_ok=True)
            logs_dir.mkdir(parents=True, exist_ok=True)
            home_dir.mkdir(parents=True, exist_ok=True)
            skills_active_path.mkdir(parents=True, exist_ok=True)
            await self._ensure_live_session_started(
                job_id=job_id,
                log_path=prepare_log_path,
                cwd=job_root,
            )

            deduped_selected_skills = tuple(
                dict.fromkeys(
                    [skill.strip() for skill in selected_skills if skill.strip()]
                )
            )
            materialized_skill_payload: dict[str, Any] | None = None
            if deduped_selected_skills:
                try:
                    selection = resolve_run_skill_selection(
                        run_id=str(job_id),
                        context={"skill_selection": list(deduped_selected_skills)},
                    )
                    materialized_workspace = materialize_run_skill_workspace(
                        selection=selection,
                        run_root=job_root,
                        cache_root=self._resolve_skills_cache_root(),
                        verify_signatures=settings.spec_workflow.skills_verify_signatures,
                    )
                    materialized_skill_payload = materialized_workspace.to_payload()
                    self._append_stage_log(
                        prepare_log_path,
                        (
                            "materialized selected skill workspace: "
                            + ", ".join(deduped_selected_skills)
                        ),
                    )
                except (SkillResolutionError, SkillMaterializationError) as exc:
                    raise RuntimeError(f"skill materialization failed: {exc}") from exc
            else:
                try:
                    ensure_shared_skill_links(
                        run_root=job_root, skills_active_path=skills_active_path
                    )
                except SkillWorkspaceError as exc:
                    self._append_stage_log(
                        prepare_log_path, f"skill-link setup warning: {exc}"
                    )

            workdir_mode = self._safe_workdir_mode(source_payload)
            task_node = canonical_payload.get("task")
            task = task_node if isinstance(task_node, Mapping) else {}
            git_node = task.get("git")
            git = git_node if isinstance(git_node, Mapping) else {}
            publish_node = task.get("publish")
            publish = publish_node if isinstance(publish_node, Mapping) else {}
            publish_mode = str(publish.get("mode") or "pr").strip().lower() or "pr"

            repository = str(canonical_payload.get("repository") or "").strip()
            if not repository:
                raise ValueError("repository is required for prepare stage")
            auth_context = await self._resolve_task_auth_context(
                canonical_payload=canonical_payload,
                publish_mode=publish_mode,
            )
            self._append_stage_log(
                prepare_log_path,
                (
                    "repo auth source: "
                    f"{auth_context.repo_auth_source}"
                    + (
                        f" ({auth_context.repo_auth_ref})"
                        if auth_context.repo_auth_ref
                        else ""
                    )
                ),
            )
            if auth_context.publish_auth_source is not None:
                self._append_stage_log(
                    prepare_log_path,
                    (
                        "publish auth source: "
                        f"{auth_context.publish_auth_source}"
                        + (
                            f" ({auth_context.publish_auth_ref})"
                            if auth_context.publish_auth_ref
                            else ""
                        )
                    ),
                )

            clone_runner_available = hasattr(self._codex_exec_handler, "_run_command")
            if clone_runner_available:
                if workdir_mode == "fresh_clone" and repo_dir.exists():
                    shutil.rmtree(repo_dir)
                if not repo_dir.exists():
                    job_root.mkdir(parents=True, exist_ok=True)
                    await self._run_stage_command(
                        [
                            "git",
                            "clone",
                            self._resolve_clone_url(repository),
                            str(repo_dir),
                        ],
                        cwd=job_root,
                        log_path=prepare_log_path,
                        env=auth_context.repo_command_env,
                    )
                await self._run_stage_command(
                    ["git", "fetch", "--all", "--prune"],
                    cwd=repo_dir,
                    log_path=prepare_log_path,
                    check=False,
                    env=auth_context.repo_command_env,
                )

            default_branch = await self._resolve_default_branch(
                repo_dir=repo_dir,
                log_path=prepare_log_path,
                fallback="main",
                env=auth_context.repo_command_env,
            )
            starting_branch_input = str(git.get("startingBranch") or "").strip() or None
            new_branch_input = str(git.get("newBranch") or "").strip() or None
            starting_branch = starting_branch_input or default_branch

            if new_branch_input:
                new_branch = new_branch_input
            elif starting_branch != default_branch:
                new_branch = None
            else:
                if len(deduped_selected_skills) == 1:
                    suffix = deduped_selected_skills[0]
                elif len(deduped_selected_skills) > 1:
                    suffix = "multi"
                else:
                    suffix = None
                new_branch = generate_branch_name(job_id, prefix="task", suffix=suffix)

            working_branch = new_branch or starting_branch
            if clone_runner_available:
                await self._ensure_working_branch(
                    repo_dir=repo_dir,
                    starting_branch=starting_branch,
                    new_branch=new_branch,
                    log_path=prepare_log_path,
                    env=auth_context.repo_command_env,
                )

            context_payload = {
                "repository": repository,
                "runtime": canonical_payload.get("targetRuntime"),
                "skill": {
                    "id": (
                        deduped_selected_skills[0]
                        if len(deduped_selected_skills) == 1
                        else ("multiple" if deduped_selected_skills else "auto")
                    ),
                    "ids": list(deduped_selected_skills),
                    "args": (
                        (task.get("skill") or {}).get("args")
                        if isinstance(task.get("skill"), Mapping)
                        else {}
                    ),
                },
                "workdirMode": workdir_mode,
                "publishMode": publish_mode,
                "auth": {
                    "repoAuthRef": auth_context.repo_auth_ref,
                    "publishAuthRef": auth_context.publish_auth_ref,
                    "repoAuthSource": auth_context.repo_auth_source,
                    "publishAuthSource": auth_context.publish_auth_source,
                },
                "resolved": {
                    "defaultBranch": default_branch,
                    "startingBranch": {
                        "value": starting_branch,
                        "explicit": starting_branch_input is not None,
                    },
                    "newBranch": {
                        "value": new_branch,
                        "explicit": new_branch_input is not None,
                    },
                    "workingBranch": working_branch,
                },
                "workspace": {
                    "jobRoot": str(job_root),
                    "repo": str(repo_dir),
                    "home": str(home_dir),
                    "skillsActive": str(skills_active_path),
                    "artifacts": str(artifacts_dir),
                },
                "rag": self._rag_capability_metadata(),
                "timestamp": datetime.now(UTC).isoformat(),
            }
            if materialized_skill_payload is not None:
                context_payload["skillsMaterialized"] = materialized_skill_payload
            redacted_context_payload = self._redact_payload(context_payload)
            task_context_path.write_text(
                json.dumps(redacted_context_payload, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

            await self._emit_event(
                job_id=job_id,
                level="info",
                message="task.git.defaultBranchResolved",
                payload={
                    "defaultBranch": default_branch,
                    "startingBranch": starting_branch,
                    "newBranch": new_branch,
                    "workingBranch": working_branch,
                },
            )
            await self._emit_stage_event(
                job_id=job_id,
                stage="moonmind.task.prepare",
                status="finished",
                payload={
                    "jobType": job_type,
                    "defaultBranch": default_branch,
                    "startingBranch": starting_branch,
                    "newBranch": new_branch,
                    "workingBranch": working_branch,
                    **dict(skill_meta),
                },
            )

            return PreparedTaskWorkspace(
                job_root=job_root,
                repo_dir=repo_dir,
                artifacts_dir=artifacts_dir,
                prepare_log_path=prepare_log_path,
                execute_log_path=execute_log_path,
                publish_log_path=publish_log_path,
                task_context_path=task_context_path,
                publish_result_path=publish_result_path,
                default_branch=default_branch,
                starting_branch=starting_branch,
                new_branch=new_branch,
                working_branch=working_branch,
                workdir_mode=workdir_mode,
                repo_command_env=auth_context.repo_command_env,
                publish_command_env=auth_context.publish_command_env,
            )
        except Exception as exc:
            await self._emit_stage_event(
                job_id=job_id,
                stage="moonmind.task.prepare",
                status="failed",
                payload={
                    "jobType": job_type,
                    "error": str(exc),
                    **dict(skill_meta),
                },
            )
            raise

    @staticmethod
    def _normalize_publish_text_line(value: str | None) -> str | None:
        """Collapse whitespace and return None when content is empty."""

        if value is None:
            return None
        normalized = " ".join(str(value).split())
        return normalized or None

    @staticmethod
    def _resolve_publish_text_override(value: Any) -> str | None:
        """Return explicit publish override verbatim when non-empty."""

        if value is None:
            return None
        candidate = str(value)
        if not candidate.strip():
            return None
        return candidate

    @staticmethod
    def _sanitize_metadata_footer_value(
        value: str | None, *, fallback: str = "unknown"
    ) -> str:
        """Normalize footer values and redact secret-like tokens."""

        normalized = " ".join(str(value or "").split())
        if not normalized:
            return fallback
        if _SECRET_LIKE_METADATA_PATTERN.search(normalized):
            return "[REDACTED]"
        return normalized

    @classmethod
    def _extract_instruction_title(cls, instructions: str | None) -> str | None:
        """Return first sentence/line from task instructions for title fallback."""

        if instructions is None:
            return None
        for raw_line in str(instructions).splitlines():
            line = raw_line.strip()
            if not line:
                continue
            sentence = re.split(r"(?<=[.!?])\s+", line, maxsplit=1)[0]
            return cls._normalize_publish_text_line(sentence or line)
        return None

    @staticmethod
    def _sanitize_pr_title(title: str, *, max_chars: int = 90) -> str:
        """Keep generated PR titles concise and avoid full UUID text."""

        sanitized = _FULL_UUID_PATTERN.sub("job", title).strip()
        if not sanitized:
            sanitized = "MoonMind task result"
        if len(sanitized) <= max_chars:
            return sanitized
        return f"{sanitized[: max_chars - 3].rstrip()}..."

    @classmethod
    def _derive_default_pr_title(
        cls,
        *,
        job_id: UUID,
        canonical_payload: Mapping[str, Any],
        resolved_steps: Sequence[ResolvedTaskStep],
    ) -> str:
        """Derive PR title using documented fallback order."""

        for step in resolved_steps:
            candidate = cls._normalize_publish_text_line(step.title)
            if candidate:
                return cls._sanitize_pr_title(candidate)

        task_node = canonical_payload.get("task")
        task = task_node if isinstance(task_node, Mapping) else {}
        instruction_candidate = cls._extract_instruction_title(
            str(task.get("instructions") or "").strip() or None
        )
        if instruction_candidate:
            return cls._sanitize_pr_title(instruction_candidate)

        return cls._sanitize_pr_title(f"MoonMind task result [mm:{str(job_id)[:8]}]")

    @staticmethod
    def _resolve_publish_runtime_mode(canonical_payload: Mapping[str, Any]) -> str:
        task_node = canonical_payload.get("task")
        task = task_node if isinstance(task_node, Mapping) else {}
        runtime_node = task.get("runtime")
        runtime = runtime_node if isinstance(runtime_node, Mapping) else {}
        runtime_mode = (
            str(runtime.get("mode") or canonical_payload.get("targetRuntime") or "")
            .strip()
            .lower()
        )
        return runtime_mode or "codex"

    @staticmethod
    def _resolve_pr_base_branch(
        *,
        publish: Mapping[str, Any],
        starting_branch: str,
    ) -> str:
        return str(publish.get("prBaseBranch") or "").strip() or starting_branch

    @classmethod
    def _derive_default_pr_body(
        cls,
        *,
        job_id: UUID,
        runtime_mode: str,
        base_branch: str,
        head_branch: str,
    ) -> str:
        runtime_value = cls._sanitize_metadata_footer_value(
            runtime_mode, fallback="codex"
        )
        base_value = cls._sanitize_metadata_footer_value(base_branch)
        head_value = cls._sanitize_metadata_footer_value(head_branch)
        lines = [
            "Automated PR generated by MoonMind queue publish stage.",
            "",
            "---",
            "<!-- moonmind:begin -->",
            f"MoonMind Job: {job_id}",
            f"Runtime: {runtime_value}",
            f"Base: {base_value}",
            f"Head: {head_value}",
            "<!-- moonmind:end -->",
        ]
        return "\n".join(lines)

    async def _run_publish_stage(
        self,
        *,
        job_id: UUID,
        canonical_payload: Mapping[str, Any],
        prepared: PreparedTaskWorkspace,
        skill_meta: Mapping[str, Any],
        job_type: str,
        staged_artifacts: list[ArtifactUpload],
    ) -> str | None:
        """Publish repository changes according to task publish policy."""

        task_node = canonical_payload.get("task")
        task = task_node if isinstance(task_node, Mapping) else {}
        publish_node = task.get("publish")
        publish = publish_node if isinstance(publish_node, Mapping) else {}
        publish_mode = str(publish.get("mode") or "pr").strip().lower() or "pr"
        if publish_mode == "none":
            await self._emit_event(
                job_id=job_id,
                level="info",
                message="moonmind.task.publish",
                payload={
                    "status": "skipped",
                    "reason": "publish mode is none",
                    **dict(skill_meta),
                },
            )
            return None

        await self._emit_stage_event(
            job_id=job_id,
            stage="moonmind.task.publish",
            status="started",
            payload={"jobType": job_type, "mode": publish_mode, **dict(skill_meta)},
        )
        try:
            self._append_stage_log(
                prepared.publish_log_path, f"publish mode: {publish_mode}"
            )
            self._append_stage_log(
                prepared.publish_log_path,
                f"working branch: {prepared.working_branch}",
            )

            status = await self._run_stage_command(
                ["git", "status", "--porcelain"],
                cwd=prepared.repo_dir,
                log_path=prepared.publish_log_path,
                check=False,
                env=prepared.publish_command_env,
            )
            if not status.stdout.strip():
                result_payload = {
                    "mode": publish_mode,
                    "branch": prepared.working_branch,
                    "prUrl": None,
                    "skipped": True,
                    "reason": "no local changes",
                }
                prepared.publish_result_path.write_text(
                    json.dumps(result_payload, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                staged_artifacts.extend(
                    [
                        ArtifactUpload(
                            path=prepared.publish_log_path,
                            name="logs/publish.log",
                            content_type="text/plain",
                        ),
                        ArtifactUpload(
                            path=prepared.publish_result_path,
                            name="publish_result.json",
                            content_type="application/json",
                        ),
                    ]
                )
                await self._emit_stage_event(
                    job_id=job_id,
                    stage="moonmind.task.publish",
                    status="finished",
                    payload={
                        "jobType": job_type,
                        "mode": publish_mode,
                        "skipped": True,
                        **dict(skill_meta),
                    },
                )
                await self._emit_event(
                    job_id=job_id,
                    level="info",
                    message="moonmind.task.publish",
                    payload={
                        "status": "skipped",
                        "reason": "no local changes",
                        **dict(skill_meta),
                    },
                )
                return "publish skipped: no local changes"

            await self._run_stage_command(
                ["git", "checkout", prepared.working_branch],
                cwd=prepared.repo_dir,
                log_path=prepared.publish_log_path,
                env=prepared.publish_command_env,
            )
            await self._run_stage_command(
                ["git", "add", "-A"],
                cwd=prepared.repo_dir,
                log_path=prepared.publish_log_path,
                env=prepared.publish_command_env,
            )

            commit_message = (
                self._resolve_publish_text_override(publish.get("commitMessage"))
                or f"MoonMind task result for job {job_id}"
            )
            await self._run_stage_command(
                ["git", "commit", "-m", commit_message],
                cwd=prepared.repo_dir,
                log_path=prepared.publish_log_path,
                env=prepared.publish_command_env,
                redaction_values=(commit_message,),
            )
            await self._run_stage_command(
                ["git", "push", "-u", "origin", prepared.working_branch],
                cwd=prepared.repo_dir,
                log_path=prepared.publish_log_path,
                env=prepared.publish_command_env,
            )

            pr_url: str | None = None
            publish_note = f"published branch {prepared.working_branch}"
            if publish_mode == "pr":
                pr_base = self._resolve_pr_base_branch(
                    publish=publish,
                    starting_branch=prepared.starting_branch,
                )
                pr_title = self._resolve_publish_text_override(
                    publish.get("prTitle")
                ) or self._derive_default_pr_title(
                    job_id=job_id,
                    canonical_payload=canonical_payload,
                    resolved_steps=self._resolve_task_steps(canonical_payload),
                )
                pr_body = self._resolve_publish_text_override(
                    publish.get("prBody")
                ) or self._derive_default_pr_body(
                    job_id=job_id,
                    runtime_mode=self._resolve_publish_runtime_mode(canonical_payload),
                    base_branch=pr_base,
                    head_branch=prepared.working_branch,
                )
                pr_result = await self._run_stage_command(
                    [
                        self._resolve_gh_binary(),
                        "pr",
                        "create",
                        "--base",
                        pr_base,
                        "--head",
                        prepared.working_branch,
                        "--title",
                        pr_title,
                        "--body",
                        pr_body,
                    ],
                    cwd=prepared.repo_dir,
                    log_path=prepared.publish_log_path,
                    env=prepared.publish_command_env,
                    redaction_values=(pr_title, pr_body),
                )
                pr_url = self._extract_pr_url(pr_result.stdout)
                publish_note = (
                    f"published PR from {prepared.working_branch}"
                    if pr_url is None
                    else f"published PR {pr_url}"
                )

            result_payload = {
                "mode": publish_mode,
                "branch": prepared.working_branch,
                "baseBranch": prepared.starting_branch,
                "prUrl": pr_url,
                "skipped": False,
            }
            prepared.publish_result_path.write_text(
                json.dumps(result_payload, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            staged_artifacts.extend(
                [
                    ArtifactUpload(
                        path=prepared.publish_log_path,
                        name="logs/publish.log",
                        content_type="text/plain",
                    ),
                    ArtifactUpload(
                        path=prepared.publish_result_path,
                        name="publish_result.json",
                        content_type="application/json",
                    ),
                ]
            )
            await self._emit_stage_event(
                job_id=job_id,
                stage="moonmind.task.publish",
                status="finished",
                payload={
                    "jobType": job_type,
                    "mode": publish_mode,
                    "workingBranch": prepared.working_branch,
                    "prUrl": pr_url,
                    **dict(skill_meta),
                },
            )
            await self._emit_event(
                job_id=job_id,
                level="info",
                message="moonmind.task.publish",
                payload={
                    "status": "published",
                    "mode": publish_mode,
                    "workingBranch": prepared.working_branch,
                    "prUrl": pr_url,
                    **dict(skill_meta),
                },
            )
            return publish_note
        except Exception as exc:
            await self._emit_stage_event(
                job_id=job_id,
                stage="moonmind.task.publish",
                status="failed",
                payload={"jobType": job_type, "error": str(exc), **dict(skill_meta)},
            )
            raise

    def _prepare_stage_artifacts(
        self, prepared: PreparedTaskWorkspace
    ) -> list[ArtifactUpload]:
        """Return standard prepare-stage artifacts."""

        return [
            ArtifactUpload(
                path=prepared.prepare_log_path,
                name="logs/prepare.log",
                content_type="text/plain",
            ),
            ArtifactUpload(
                path=prepared.task_context_path,
                name="task_context.json",
                content_type="application/json",
            ),
        ]

    def _normalize_execute_artifacts(
        self,
        *,
        result_artifacts: Sequence[ArtifactUpload],
        execute_log_path: Path,
    ) -> list[ArtifactUpload]:
        """Map execute artifacts to standard stage naming conventions."""

        normalized: list[ArtifactUpload] = []
        has_execute_log = False
        for artifact in result_artifacts:
            if artifact.name == "logs/codex_exec.log":
                if artifact.path.exists():
                    execute_log_path.parent.mkdir(parents=True, exist_ok=True)
                    if artifact.path != execute_log_path:
                        shutil.copy2(artifact.path, execute_log_path)
                    has_execute_log = True
                    normalized.append(
                        ArtifactUpload(
                            path=execute_log_path,
                            name="logs/execute.log",
                            content_type="text/plain",
                            digest=artifact.digest,
                        )
                    )
                continue
            normalized.append(artifact)

        if not has_execute_log and execute_log_path.exists():
            normalized.append(
                ArtifactUpload(
                    path=execute_log_path,
                    name="logs/execute.log",
                    content_type="text/plain",
                )
            )
        return normalized

    def _normalize_step_artifacts(
        self,
        *,
        step: ResolvedTaskStep,
        result_artifacts: Sequence[ArtifactUpload],
        step_log_path: Path,
        step_patch_path: Path,
    ) -> list[ArtifactUpload]:
        """Map runtime artifacts to deterministic per-step artifact paths."""

        step_log_name = f"logs/steps/step-{step.step_index:04d}.log"
        step_patch_name = f"patches/steps/step-{step.step_index:04d}.patch"
        normalized: list[ArtifactUpload] = []
        has_step_log = False
        has_step_patch = False
        for artifact in result_artifacts:
            if artifact.name in {"logs/codex_exec.log", "logs/execute.log"}:
                if artifact.path.exists():
                    step_log_path.parent.mkdir(parents=True, exist_ok=True)
                    if artifact.path != step_log_path:
                        shutil.copy2(artifact.path, step_log_path)
                    normalized.append(
                        ArtifactUpload(
                            path=step_log_path,
                            name=step_log_name,
                            content_type="text/plain",
                            digest=artifact.digest,
                            required=False,
                        )
                    )
                    has_step_log = True
                continue
            if artifact.name == "patches/changes.patch":
                if artifact.path.exists():
                    step_patch_path.parent.mkdir(parents=True, exist_ok=True)
                    if artifact.path != step_patch_path:
                        shutil.copy2(artifact.path, step_patch_path)
                    if step_patch_path.stat().st_size > 0:
                        normalized.append(
                            ArtifactUpload(
                                path=step_patch_path,
                                name=step_patch_name,
                                content_type="text/x-diff",
                                digest=artifact.digest,
                                required=False,
                            )
                        )
                        has_step_patch = True
                continue
            normalized.append(artifact)

        if not has_step_log and step_log_path.exists():
            normalized.append(
                ArtifactUpload(
                    path=step_log_path,
                    name=step_log_name,
                    content_type="text/plain",
                    required=False,
                )
            )
        if (
            not has_step_patch
            and step_patch_path.exists()
            and step_patch_path.stat().st_size > 0
        ):
            normalized.append(
                ArtifactUpload(
                    path=step_patch_path,
                    name=step_patch_name,
                    content_type="text/x-diff",
                    required=False,
                )
            )
        return normalized

    async def _emit_stage_event(
        self,
        *,
        job_id: UUID,
        stage: str,
        status: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        event_payload = {"stage": stage, "status": status}
        if payload:
            event_payload.update(payload)
        await self._emit_event(
            job_id=job_id,
            level="info" if status != "failed" else "error",
            message=stage,
            payload=event_payload,
        )

    def _append_stage_log(self, log_path: Path, line: str) -> None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).isoformat()
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"[{timestamp}] {self._redact_text(line)}\n")

    @staticmethod
    def _infer_stage_from_log_path(log_path: Path) -> str:
        name = log_path.name.lower()
        if name == "prepare.log":
            return "moonmind.task.prepare"
        if name == "publish.log":
            return "moonmind.task.publish"
        return "moonmind.task.execute"

    @staticmethod
    def _step_index_from_log_path(log_path: Path) -> int | None:
        match = re.search(r"step-(\d+)\.log$", str(log_path))
        if not match:
            return None
        return int(match.group(1))

    @staticmethod
    def _job_id_from_log_path(log_path: Path) -> UUID | None:
        for part in log_path.parts:
            with suppress(ValueError):
                return UUID(part)
        return None

    def _build_live_log_chunk_callback(
        self,
        *,
        job_id: UUID,
        stage: str,
        step_id: str | None = None,
        step_index: int | None = None,
    ) -> OutputChunkCallback | None:
        if not self._config.live_log_events_enabled:
            return None

        batch_limit = max(128, int(self._config.live_log_events_batch_bytes))
        flush_interval_seconds = max(
            0.01,
            float(self._config.live_log_events_flush_interval_ms) / 1000.0,
        )
        buffers: dict[str, str] = {"stdout": "", "stderr": ""}
        last_flush_monotonic = time.monotonic()

        async def _flush_stream(stream: str, *, force: bool = False) -> None:
            nonlocal last_flush_monotonic
            pending = buffers.get(stream, "")
            if not pending:
                return

            while pending:
                if not force and len(pending) < batch_limit and "\n" not in pending:
                    break
                if len(pending) <= batch_limit:
                    emit = pending
                    pending = ""
                else:
                    newline_index = pending.rfind("\n", 0, batch_limit)
                    if newline_index > 0:
                        emit = pending[: newline_index + 1]
                        pending = pending[newline_index + 1 :]
                    else:
                        emit = pending[:batch_limit]
                        pending = pending[batch_limit:]
                if not emit.strip():
                    continue
                payload: dict[str, Any] = {
                    "kind": "log",
                    "stream": stream,
                    "stage": stage,
                }
                if step_id:
                    payload["stepId"] = step_id
                if step_index is not None:
                    payload["stepIndex"] = step_index
                await self._emit_event(
                    job_id=job_id,
                    level="warn" if stream == "stderr" else "info",
                    message=emit,
                    payload=payload,
                )
                last_flush_monotonic = time.monotonic()
            buffers[stream] = pending

        async def _on_output_chunk(stream: str, text: str | None) -> None:
            if stream not in buffers:
                return
            if text is None:
                await _flush_stream(stream, force=True)
                return
            if not text:
                return
            buffers[stream] = f"{buffers[stream]}{text}"
            now = time.monotonic()
            interval_elapsed = (now - last_flush_monotonic) >= flush_interval_seconds
            if (
                "\n" in buffers[stream]
                or len(buffers[stream]) >= batch_limit
                or interval_elapsed
            ):
                await _flush_stream(stream, force=interval_elapsed)

        return _on_output_chunk

    async def _resolve_default_branch(
        self,
        *,
        repo_dir: Path,
        log_path: Path,
        fallback: str,
        env: Mapping[str, str] | None = None,
    ) -> str:
        runner_available = hasattr(self._codex_exec_handler, "_run_command")
        if not runner_available or not repo_dir.exists():
            return fallback

        symbolic = await self._run_stage_command(
            ["git", "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"],
            cwd=repo_dir,
            log_path=log_path,
            check=False,
            env=env,
        )
        resolved = symbolic.stdout.strip()
        if symbolic.returncode == 0 and resolved:
            if resolved.startswith("origin/"):
                return resolved.removeprefix("origin/")
            return resolved

        remote_show = await self._run_stage_command(
            ["git", "remote", "show", "origin"],
            cwd=repo_dir,
            log_path=log_path,
            check=False,
            env=env,
        )
        for line in remote_show.stdout.splitlines():
            marker = "HEAD branch:"
            if marker in line:
                candidate = line.split(marker, 1)[1].strip()
                if candidate:
                    return candidate
        return fallback

    async def _ensure_working_branch(
        self,
        *,
        repo_dir: Path,
        starting_branch: str,
        new_branch: str | None,
        log_path: Path,
        env: Mapping[str, str] | None = None,
    ) -> None:
        checkout = await self._run_stage_command(
            ["git", "checkout", starting_branch],
            cwd=repo_dir,
            log_path=log_path,
            check=False,
            env=env,
        )
        if checkout.returncode != 0:
            await self._run_stage_command(
                ["git", "checkout", "-B", starting_branch, f"origin/{starting_branch}"],
                cwd=repo_dir,
                log_path=log_path,
                env=env,
            )
        if new_branch:
            await self._run_stage_command(
                ["git", "checkout", "-B", new_branch, starting_branch],
                cwd=repo_dir,
                log_path=log_path,
                env=env,
            )

    async def _run_stage_command(
        self,
        command: list[str],
        *,
        cwd: Path,
        log_path: Path,
        check: bool = True,
        env: Mapping[str, str] | None = None,
        redaction_values: tuple[str, ...] = (),
        cancel_event: asyncio.Event | None = None,
        timeout_seconds: float | None = None,
    ) -> CommandResult:
        effective_cancel_event = cancel_event or getattr(
            self, "_active_cancel_event", None
        )
        if effective_cancel_event is not None and effective_cancel_event.is_set():
            raise JobCancellationRequested(
                "cancellation requested before command start"
            )
        merged_redaction_values = tuple(
            dict.fromkeys(
                [
                    value
                    for value in (
                        *self._dynamic_redaction_values,
                        *redaction_values,
                    )
                    if value
                ]
            )
        )
        live_log_callback: OutputChunkCallback | None = None
        inferred_job_id = self._job_id_from_log_path(log_path)
        if inferred_job_id is not None:
            live_log_callback = self._build_live_log_chunk_callback(
                job_id=inferred_job_id,
                stage=self._infer_stage_from_log_path(log_path),
                step_index=self._step_index_from_log_path(log_path),
            )
        effective_timeout_seconds = (
            float(self._config.stage_command_timeout_seconds)
            if timeout_seconds is None
            else float(timeout_seconds)
        )
        runner = getattr(self._codex_exec_handler, "_run_command", None)
        if callable(runner):
            try:
                return await asyncio.wait_for(
                    runner(
                        command,
                        cwd=cwd,
                        log_path=log_path,
                        check=check,
                        env=dict(env) if env is not None else None,
                        redaction_values=merged_redaction_values,
                        cancel_event=effective_cancel_event,
                        output_chunk_callback=live_log_callback,
                    ),
                    timeout=effective_timeout_seconds,
                )
            except asyncio.TimeoutError as exc:
                redacted_command = self._redact_command_for_log(
                    command, redaction_values=merged_redaction_values
                )
                timeout_display = f"{effective_timeout_seconds:g}"
                timeout_msg = (
                    "command timed out after "
                    f"{timeout_display}s: {' '.join(redacted_command)}"
                )
                self._append_stage_log(log_path, timeout_msg)
                raise asyncio.TimeoutError(timeout_msg) from exc
        redacted_command = self._redact_command_for_log(
            command, redaction_values=merged_redaction_values
        )
        self._append_stage_log(log_path, f"$ {' '.join(redacted_command)}")
        raise RuntimeError("Codex execution handler is missing command runner")

    @staticmethod
    def _mask_sensitive_command_args(command: Sequence[str]) -> list[str]:
        masked: list[str] = []
        redact_next = False
        for part in command:
            if redact_next:
                masked.append("[REDACTED]")
                redact_next = False
                continue
            if part in _SENSITIVE_COMMAND_FLAGS:
                masked.append(part)
                redact_next = True
                continue
            if part.startswith("--title="):
                masked.append("--title=[REDACTED]")
                continue
            if part.startswith("--body="):
                masked.append("--body=[REDACTED]")
                continue
            if part.startswith("--message="):
                masked.append("--message=[REDACTED]")
                continue
            masked.append(part)
        return masked

    def _redact_command_for_log(
        self,
        command: Sequence[str],
        *,
        redaction_values: Sequence[str] = (),
    ) -> list[str]:
        redacted: list[str] = []
        for part in self._mask_sensitive_command_args(command):
            cleaned = part
            for value in redaction_values:
                if value:
                    cleaned = cleaned.replace(value, "[REDACTED]")
            redacted.append(self._redact_text(cleaned))
        return redacted

    def _resolve_clone_url(self, repository: str) -> str:
        to_clone_url = getattr(self._codex_exec_handler, "_to_clone_url", None)
        if callable(to_clone_url):
            return str(to_clone_url(repository))
        if repository.startswith(("http://", "https://", "git@")):
            return repository
        return f"https://github.com/{repository}.git"

    def _resolve_gh_binary(self) -> str:
        gh_binary = getattr(self._codex_exec_handler, "_gh_binary", None)
        if isinstance(gh_binary, str) and gh_binary.strip():
            return gh_binary
        return "gh"

    def _resolve_skills_cache_root(self) -> Path:
        cache_root = Path(settings.spec_workflow.skills_cache_root)
        if not cache_root.is_absolute():
            cache_root = (Path.cwd() / cache_root).resolve()
        cache_root.mkdir(parents=True, exist_ok=True)
        return cache_root

    async def _ensure_live_session_started(
        self,
        *,
        job_id: UUID,
        log_path: Path,
        cwd: Path,
    ) -> None:
        """Best-effort live-session bootstrap for the active task run."""

        async with self._live_session_start_lock:
            if self._active_live_session is not None:
                return
            if self._config.live_session_provider != "tmate":
                return
            if not await self._should_bootstrap_live_session(job_id=job_id):
                return

            expires_at = datetime.now(UTC) + timedelta(
                minutes=max(1, self._config.live_session_ttl_minutes)
            )
            with suppress(Exception):
                await self._queue_client.report_live_session(
                    job_id=job_id,
                    worker_id=self._config.worker_id,
                    worker_hostname=socket.gethostname(),
                    status="starting",
                    provider="tmate",
                    expires_at=expires_at.isoformat(),
                )

            tmate_binary = shutil.which("tmate")
            if not tmate_binary:
                with suppress(Exception):
                    await self._queue_client.report_live_session(
                        job_id=job_id,
                        worker_id=self._config.worker_id,
                        worker_hostname=socket.gethostname(),
                        status="error",
                        provider="tmate",
                        error_message="tmate binary was not found in PATH",
                        expires_at=expires_at.isoformat(),
                    )
                return

            cwd.mkdir(parents=True, exist_ok=True)
            socket_dir = Path("/tmp/moonmind/tmate")
            socket_dir.mkdir(parents=True, exist_ok=True)
            socket_path = socket_dir / f"{job_id}.sock"
            socket_path.unlink(missing_ok=True)
            session_name = f"mm-{str(job_id).replace('-', '')[:16]}"
            tmate_config_path: Path | None = None
            tmate_host = str(self._config.tmate_server_host or "").strip()
            if tmate_host:
                escaped_host = tmate_host.replace("\\", "\\\\").replace('"', '\\"')
                tmate_config_path = socket_dir / f"{job_id}.conf"
                tmate_config_path.write_text(
                    f'set -g tmate-server-host "{escaped_host}"\n',
                    encoding="utf-8",
                )
            tmate_command_prefix = [tmate_binary]
            if tmate_config_path is not None:
                tmate_command_prefix.extend(["-f", str(tmate_config_path)])
            tmate_command_prefix.extend(["-S", str(socket_path)])

            try:
                await self._run_stage_command(
                    [*tmate_command_prefix, "new-session", "-d", "-s", session_name],
                    cwd=cwd,
                    log_path=log_path,
                )
                await self._run_stage_command(
                    [
                        *tmate_command_prefix,
                        "set",
                        "-g",
                        "remain-on-exit",
                        "on",
                    ],
                    cwd=cwd,
                    log_path=log_path,
                )
                await self._run_stage_command(
                    [
                        *tmate_command_prefix,
                        "set",
                        "-g",
                        "history-limit",
                        "200000",
                    ],
                    cwd=cwd,
                    log_path=log_path,
                )
                await self._run_stage_command(
                    [
                        *tmate_command_prefix,
                        "split-window",
                        "-h",
                        "-t",
                        f"{session_name}:0",
                    ],
                    cwd=cwd,
                    log_path=log_path,
                )
                await self._run_stage_command(
                    [
                        *tmate_command_prefix,
                        "split-window",
                        "-v",
                        "-t",
                        f"{session_name}:0.0",
                    ],
                    cwd=cwd,
                    log_path=log_path,
                )
                await self._run_stage_command(
                    [
                        *tmate_command_prefix,
                        "split-window",
                        "-v",
                        "-t",
                        f"{session_name}:0.1",
                    ],
                    cwd=cwd,
                    log_path=log_path,
                )
                await self._run_stage_command(
                    [*tmate_command_prefix, "wait", "tmate-ready"],
                    cwd=cwd,
                    log_path=log_path,
                )
                ssh_ro_result = await self._run_command_without_logging(
                    [*tmate_command_prefix, "display", "-p", "#{tmate_ssh_ro}"],
                    cwd=cwd,
                )
                ssh_rw_result = await self._run_command_without_logging(
                    [*tmate_command_prefix, "display", "-p", "#{tmate_ssh}"],
                    cwd=cwd,
                )
                web_ro_result = await self._run_command_without_logging(
                    [*tmate_command_prefix, "display", "-p", "#{tmate_web_ro}"],
                    cwd=cwd,
                )
                web_rw_result = await self._run_command_without_logging(
                    [*tmate_command_prefix, "display", "-p", "#{tmate_web}"],
                    cwd=cwd,
                )
                attach_ro = ssh_ro_result.stdout.strip() or None
                attach_rw = ssh_rw_result.stdout.strip() or None
                web_ro = web_ro_result.stdout.strip() or None
                web_rw = web_rw_result.stdout.strip() or None
                self._register_redaction_value(attach_rw)
                self._register_redaction_value(web_rw)
                if not self._config.live_session_allow_web:
                    web_ro = None
                    web_rw = None

                self._active_live_session = LiveSessionHandle(
                    job_id=job_id,
                    session_name=session_name,
                    socket_path=socket_path,
                    config_path=tmate_config_path,
                    log_path=log_path,
                    status="ready",
                )

                with suppress(Exception):
                    await self._queue_client.report_live_session(
                        job_id=job_id,
                        worker_id=self._config.worker_id,
                        worker_hostname=socket.gethostname(),
                        status="ready",
                        provider="tmate",
                        attach_ro=attach_ro,
                        attach_rw=attach_rw,
                        web_ro=web_ro,
                        web_rw=web_rw,
                        tmate_session_name=session_name,
                        tmate_socket_path=str(socket_path),
                        expires_at=expires_at.isoformat(),
                    )
            except Exception as exc:
                with suppress(Exception):
                    await self._queue_client.report_live_session(
                        job_id=job_id,
                        worker_id=self._config.worker_id,
                        worker_hostname=socket.gethostname(),
                        status="error",
                        provider="tmate",
                        error_message=str(exc),
                        tmate_session_name=session_name,
                        tmate_socket_path=str(socket_path),
                        expires_at=expires_at.isoformat(),
                    )
                with suppress(Exception):
                    await self._run_stage_command(
                        [*tmate_command_prefix, "kill-session", "-t", session_name],
                        cwd=cwd,
                        log_path=log_path,
                        check=False,
                    )
                socket_path.unlink(missing_ok=True)
                if tmate_config_path is not None:
                    tmate_config_path.unlink(missing_ok=True)

    async def _teardown_live_session(self, *, job_id: UUID) -> None:
        """Best-effort live-session teardown + terminal status report."""

        live = self._active_live_session
        self._active_live_session = None
        if live is None:
            return
        tmate_binary = shutil.which("tmate") or "tmate"
        tmate_command_prefix = [tmate_binary]
        if live.config_path is not None:
            tmate_command_prefix.extend(["-f", str(live.config_path)])
        tmate_command_prefix.extend(["-S", str(live.socket_path)])
        with suppress(Exception):
            await self._run_stage_command(
                [*tmate_command_prefix, "kill-session", "-t", live.session_name],
                cwd=live.socket_path.parent,
                log_path=live.log_path,
                check=False,
            )
        live.socket_path.unlink(missing_ok=True)
        if live.config_path is not None:
            live.config_path.unlink(missing_ok=True)
        with suppress(Exception):
            await self._queue_client.report_live_session(
                job_id=job_id,
                worker_id=self._config.worker_id,
                worker_hostname=socket.gethostname(),
                status="ended",
                provider="tmate",
                tmate_session_name=live.session_name,
                tmate_socket_path=str(live.socket_path),
            )

    @staticmethod
    def _extract_pr_url(stdout: str) -> str | None:
        for line in stdout.splitlines():
            candidate = line.strip()
            if candidate.startswith("http://") or candidate.startswith("https://"):
                return candidate
        return None

    def _build_skill_payload(
        self,
        *,
        canonical_payload: Mapping[str, Any],
        selected_skill: str,
        source_payload: Mapping[str, Any],
        instruction_override: str | None = None,
        skill_args_override: Mapping[str, Any] | None = None,
        ref_override: str | None = None,
        publish_mode_override: str | None = None,
        publish_base_override: str | None = None,
        workdir_mode_override: str | None = None,
        include_ref: bool = True,
    ) -> dict[str, Any]:
        task_node = canonical_payload.get("task")
        task = task_node if isinstance(task_node, Mapping) else {}
        runtime_node = task.get("runtime")
        runtime = runtime_node if isinstance(runtime_node, Mapping) else {}
        git_node = task.get("git")
        git = git_node if isinstance(git_node, Mapping) else {}
        publish_node = task.get("publish")
        publish = publish_node if isinstance(publish_node, Mapping) else {}
        skill_node = task.get("skill")
        skill = skill_node if isinstance(skill_node, Mapping) else {}
        if isinstance(skill_args_override, Mapping):
            args = dict(skill_args_override)
        else:
            raw_args = skill.get("args")
            args = dict(raw_args) if isinstance(raw_args, Mapping) else {}

        repository = str(canonical_payload.get("repository") or "").strip()
        instructions = (
            str(instruction_override).strip()
            if instruction_override is not None
            else str(task.get("instructions") or "").strip()
        )
        starting_branch = str(git.get("startingBranch") or "").strip()
        selected_ref = (
            str(ref_override).strip() if ref_override is not None else starting_branch
        )
        workdir_mode = workdir_mode_override or self._safe_workdir_mode(source_payload)

        if repository:
            args.setdefault("repo", repository)
        if instructions:
            args.setdefault("instruction", instructions)
        if include_ref and selected_ref:
            args.setdefault("ref", selected_ref)
        args.setdefault("workdirMode", workdir_mode)
        args.setdefault(
            "publishMode", publish_mode_override or publish.get("mode") or "pr"
        )
        publish_base = (
            publish_base_override
            if publish_base_override is not None
            else publish.get("prBaseBranch")
        )
        if publish_base:
            args.setdefault("publishBaseBranch", publish_base)

        payload: dict[str, Any] = {
            "skillId": selected_skill,
            "inputs": args,
            "repository": repository,
            "instruction": instructions,
            "workdirMode": workdir_mode,
            "publishMode": publish_mode_override or publish.get("mode") or "pr",
            "publishBaseBranch": publish_base,
        }
        if include_ref and selected_ref:
            payload["ref"] = selected_ref

        codex_overrides: dict[str, str] = {}
        runtime_model = str(runtime.get("model") or "").strip()
        runtime_effort = str(runtime.get("effort") or "").strip()
        if runtime_model:
            codex_overrides["model"] = runtime_model
        if runtime_effort:
            codex_overrides["effort"] = runtime_effort
        if codex_overrides:
            payload["codex"] = codex_overrides
        return payload

    async def _run_execute_stage(
        self,
        *,
        job_id: UUID,
        canonical_payload: Mapping[str, Any],
        source_payload: Mapping[str, Any],
        runtime_mode: str,
        resolved_steps: Sequence[ResolvedTaskStep],
        prepared: PreparedTaskWorkspace,
        artifact_callback: (
            Callable[[Sequence[ArtifactUpload]], Awaitable[None]] | None
        ) = None,
    ) -> WorkerExecutionResult:
        """Execute resolved task steps via selected runtime adapter."""

        task_node = canonical_payload.get("task")
        task = task_node if isinstance(task_node, Mapping) else {}
        explicit_steps = isinstance(task.get("steps"), list) and bool(task.get("steps"))
        container_spec = self._extract_container_task_spec(canonical_payload)
        if container_spec is not None:
            if explicit_steps:
                raise ValueError(
                    "task.steps is not supported when task.container.enabled=true"
                )
            return await self._run_container_execute_stage(
                job_id=job_id,
                canonical_payload=canonical_payload,
                prepared=prepared,
                container_spec=container_spec,
            )

        runtime_model, runtime_effort = self._resolve_runtime_overrides(
            canonical_payload=canonical_payload,
            runtime_mode=runtime_mode,
        )
        plan_payload = {
            "stepCount": len(resolved_steps),
            "stepIds": [step.step_id for step in resolved_steps],
        }
        await self._emit_event(
            job_id=job_id,
            level="info",
            message="task.steps.plan",
            payload=plan_payload,
        )

        cancel_event = getattr(self, "_active_cancel_event", None)
        pause_event = getattr(self, "_active_pause_event", None)
        step_artifacts: list[ArtifactUpload] = []
        for step in resolved_steps:
            if cancel_event is not None:
                await self._raise_if_cancel_requested(cancel_event=cancel_event)
            if pause_event is not None and cancel_event is not None:
                await self._wait_if_paused(
                    job_id=job_id,
                    pause_event=pause_event,
                    cancel_event=cancel_event,
                )
            step_log_path = (
                prepared.artifacts_dir
                / "logs"
                / "steps"
                / f"step-{step.step_index:04d}.log"
            )
            step_patch_path = (
                prepared.artifacts_dir
                / "patches"
                / "steps"
                / f"step-{step.step_index:04d}.patch"
            )
            event_payload: dict[str, Any] = {
                "stepIndex": step.step_index,
                "stepId": step.step_id,
                "effectiveSkill": step.effective_skill_id,
                "hasStepInstructions": step.has_step_instructions,
            }
            if step.title:
                event_payload["stepTitle"] = step.title

            self._append_stage_log(
                prepared.execute_log_path,
                (
                    f"step {step.step_index + 1}/{len(resolved_steps)} started: "
                    f"{step.step_id} (skill={step.effective_skill_id})"
                ),
            )
            await self._emit_event(
                job_id=job_id,
                level="info",
                message="task.step.started",
                payload=event_payload,
            )
            instruction = self._compose_step_instruction_for_runtime(
                canonical_payload=canonical_payload,
                runtime_mode=runtime_mode,
                step=step,
                total_steps=len(resolved_steps),
            )

            try:
                if runtime_mode == "codex":
                    if step.effective_skill_id != "auto":
                        step_log_callback = self._build_live_log_chunk_callback(
                            job_id=job_id,
                            stage="moonmind.task.execute",
                            step_id=step.step_id,
                            step_index=step.step_index,
                        )
                        step_result = await self._codex_exec_handler.handle_skill(
                            job_id=job_id,
                            payload=self._build_skill_payload(
                                canonical_payload=canonical_payload,
                                selected_skill=step.effective_skill_id,
                                source_payload=source_payload,
                                instruction_override=instruction,
                                skill_args_override=step.effective_skill_args,
                                ref_override=None,
                                publish_mode_override="none",
                                publish_base_override=None,
                                workdir_mode_override="reuse",
                                include_ref=False,
                            ),
                            selected_skill=step.effective_skill_id,
                            fallback=step.effective_skill_id != "speckit",
                            cancel_event=cancel_event,
                            output_chunk_callback=step_log_callback,
                        )
                    else:
                        exec_payload = self._build_exec_payload(
                            canonical_payload=canonical_payload,
                            source_payload=source_payload,
                            instruction_override=instruction,
                            ref_override=None,
                            publish_mode_override="none",
                            publish_base_override=None,
                            workdir_mode_override="reuse",
                            include_ref=False,
                        )
                        codex_overrides: dict[str, str] = {}
                        if runtime_model:
                            codex_overrides["model"] = runtime_model
                        if runtime_effort:
                            codex_overrides["effort"] = runtime_effort
                        if codex_overrides:
                            exec_payload["codex"] = codex_overrides
                        step_log_callback = self._build_live_log_chunk_callback(
                            job_id=job_id,
                            stage="moonmind.task.execute",
                            step_id=step.step_id,
                            step_index=step.step_index,
                        )
                        step_result = await self._codex_exec_handler.handle(
                            job_id=job_id,
                            payload=exec_payload,
                            cancel_event=cancel_event,
                            output_chunk_callback=step_log_callback,
                        )
                else:
                    command = self._build_non_codex_runtime_command(
                        runtime_mode=runtime_mode,
                        instruction=instruction,
                        model=runtime_model,
                        effort=runtime_effort,
                    )
                    runtime_env = self._build_non_codex_runtime_env(
                        runtime_mode=runtime_mode
                    )
                    await self._run_stage_command(
                        command,
                        cwd=prepared.repo_dir,
                        log_path=step_log_path,
                        env=runtime_env,
                    )
                    patch_result = await self._run_stage_command(
                        ["git", "diff"],
                        cwd=prepared.repo_dir,
                        log_path=step_log_path,
                        check=False,
                    )
                    step_patch_path.parent.mkdir(parents=True, exist_ok=True)
                    step_patch_path.write_text(
                        patch_result.stdout or "",
                        encoding="utf-8",
                    )
                    step_result = WorkerExecutionResult(
                        succeeded=True,
                        summary=f"{runtime_mode} step execution completed",
                        error_message=None,
                        artifacts=(
                            ArtifactUpload(
                                path=step_log_path,
                                name="logs/execute.log",
                                content_type="text/plain",
                            ),
                            ArtifactUpload(
                                path=step_patch_path,
                                name="patches/changes.patch",
                                content_type="text/x-diff",
                            ),
                        ),
                    )
            except CommandCancelledError:
                raise
            except Exception as exc:
                event_payload["summary"] = str(exc)
                await self._emit_event(
                    job_id=job_id,
                    level="error",
                    message="task.step.failed",
                    payload=event_payload,
                )
                self._append_stage_log(
                    prepared.execute_log_path,
                    (
                        f"step {step.step_index + 1}/{len(resolved_steps)} failed: "
                        f"{step.step_id}; error={exc}"
                    ),
                )
                raise

            normalized_step_artifacts = self._normalize_step_artifacts(
                step=step,
                result_artifacts=step_result.artifacts,
                step_log_path=step_log_path,
                step_patch_path=step_patch_path,
            )
            step_artifacts.extend(normalized_step_artifacts)
            if artifact_callback is not None and normalized_step_artifacts:
                await artifact_callback(normalized_step_artifacts)

            if step_result.succeeded:
                event_payload["summary"] = step_result.summary
                await self._emit_event(
                    job_id=job_id,
                    level="info",
                    message="task.step.finished",
                    payload=event_payload,
                )
                self._append_stage_log(
                    prepared.execute_log_path,
                    (
                        f"step {step.step_index + 1}/{len(resolved_steps)} finished: "
                        f"{step.step_id}; summary={step_result.summary or '-'}"
                    ),
                )
                continue

            event_payload["summary"] = step_result.error_message
            await self._emit_event(
                job_id=job_id,
                level="error",
                message="task.step.failed",
                payload=event_payload,
            )
            self._append_stage_log(
                prepared.execute_log_path,
                (
                    f"step {step.step_index + 1}/{len(resolved_steps)} failed: "
                    f"{step.step_id}; error={step_result.error_message or '-'}"
                ),
            )
            return WorkerExecutionResult(
                succeeded=False,
                summary=None,
                error_message=step_result.error_message,
                artifacts=tuple(step_artifacts),
            )

        patch_path = prepared.artifacts_dir / "changes.patch"
        patch_result = await self._run_stage_command(
            ["git", "diff"],
            cwd=prepared.repo_dir,
            log_path=prepared.execute_log_path,
            check=False,
        )
        patch_path.write_text(patch_result.stdout or "", encoding="utf-8")
        if patch_path.exists() and patch_path.stat().st_size > 0:
            final_patch_artifact = ArtifactUpload(
                path=patch_path,
                name="patches/changes.patch",
                content_type="text/x-diff",
            )
            step_artifacts.append(final_patch_artifact)
            if artifact_callback is not None:
                await artifact_callback((final_patch_artifact,))

        return WorkerExecutionResult(
            succeeded=True,
            summary=(
                f"{runtime_mode} task execution completed "
                f"({len(resolved_steps)} step{'s' if len(resolved_steps) != 1 else ''})"
            ),
            error_message=None,
            artifacts=tuple(step_artifacts),
        )

    def _extract_container_task_spec(
        self, canonical_payload: Mapping[str, Any]
    ) -> ContainerTaskSpec | None:
        task_node = canonical_payload.get("task")
        task = task_node if isinstance(task_node, Mapping) else {}
        container_node = task.get("container")
        container = container_node if isinstance(container_node, Mapping) else {}
        if not bool(container.get("enabled")):
            return None

        image = str(container.get("image") or "").strip()
        if not image:
            raise ValueError(
                "task.container.image is required when task.container.enabled=true"
            )

        raw_command = container.get("command")
        if not isinstance(raw_command, list):
            raise ValueError("task.container.command must be a list when enabled")
        command = tuple(str(item).strip() for item in raw_command if str(item).strip())
        if not command:
            raise ValueError(
                "task.container.command is required when task.container.enabled=true"
            )

        workdir = str(container.get("workdir") or "").strip() or None
        artifacts_subdir = self._sanitize_artifacts_subdir(
            str(container.get("artifactsSubdir") or "").strip() or None
        )

        timeout_raw = container.get("timeoutSeconds")
        if timeout_raw is None or timeout_raw == "":
            timeout_seconds = self._config.container_default_timeout_seconds
        else:
            timeout_seconds = int(timeout_raw)
        if timeout_seconds < 1:
            raise ValueError("task.container.timeoutSeconds must be >= 1")

        pull_mode = str(container.get("pull") or "if-missing").strip().lower()
        if pull_mode not in {"if-missing", "always"}:
            raise ValueError("task.container.pull must be if-missing or always")

        resources_node = container.get("resources")
        resources = resources_node if isinstance(resources_node, Mapping) else {}
        cpus = str(resources.get("cpus") or "").strip() or None
        memory = str(resources.get("memory") or "").strip() or None

        env_node = container.get("env")
        env = env_node if isinstance(env_node, Mapping) else {}
        normalized_env: dict[str, str] = {}
        for raw_key, raw_value in env.items():
            key = str(raw_key).strip()
            if not key:
                continue
            if "=" in key:
                raise ValueError("task.container.env keys may not contain '='")
            if key.upper() in _CONTAINER_RESERVED_ENV_KEYS:
                raise ValueError(
                    f"task.container.env may not override reserved key '{key}'"
                )
            normalized_env[key] = str(raw_value)

        cache_volumes: list[ContainerCacheVolume] = []
        cache_node = container.get("cacheVolumes")
        if isinstance(cache_node, list):
            for item in cache_node:
                if not isinstance(item, Mapping):
                    continue
                name = str(item.get("name") or "").strip()
                target = str(item.get("target") or "").strip()
                if not name or not target:
                    continue
                cache_volumes.append(
                    ContainerCacheVolume(
                        name=self._sanitize_container_volume_name(name),
                        target=self._sanitize_container_volume_target(target),
                    )
                )

        return ContainerTaskSpec(
            image=image,
            command=command,
            workdir=workdir,
            env=normalized_env,
            artifacts_subdir=artifacts_subdir,
            timeout_seconds=timeout_seconds,
            pull_mode=pull_mode,
            cpus=cpus,
            memory=memory,
            cache_volumes=tuple(cache_volumes),
        )

    @staticmethod
    def _sanitize_artifacts_subdir(value: str | None) -> str:
        candidate = str(value or "").strip().strip("/")
        if not candidate:
            return "container"
        parts = [
            segment for segment in candidate.split("/") if segment and segment != "."
        ]
        if any(segment == ".." for segment in parts):
            raise ValueError("task.container.artifactsSubdir may not contain '..'")
        normalized = "/".join(parts)
        return normalized or "container"

    @staticmethod
    def _sanitize_container_volume_name(name: str) -> str:
        cleaned = str(name).strip()
        if not cleaned:
            raise ValueError("task.container.cacheVolumes[].name is required")
        if "," in cleaned or "=" in cleaned:
            raise ValueError(
                "task.container.cacheVolumes[].name contains invalid characters"
            )
        if not _CONTAINER_VOLUME_NAME_PATTERN.fullmatch(cleaned):
            raise ValueError("task.container.cacheVolumes[].name has invalid format")
        return cleaned

    @staticmethod
    def _sanitize_container_volume_target(target: str) -> str:
        cleaned = str(target).strip()
        if not cleaned:
            raise ValueError("task.container.cacheVolumes[].target is required")
        if "," in cleaned:
            raise ValueError("task.container.cacheVolumes[].target may not contain ','")
        if not cleaned.startswith("/"):
            raise ValueError(
                "task.container.cacheVolumes[].target must be an absolute path"
            )
        return cleaned

    @staticmethod
    def _container_command_summary(command: Sequence[str]) -> str:
        rendered = " ".join(str(item) for item in command)
        if len(rendered) <= 320:
            return rendered
        return f"{rendered[:317]}..."

    async def _ensure_container_image(
        self,
        *,
        image: str,
        pull_mode: str,
        cwd: Path,
        log_path: Path,
    ) -> None:
        if pull_mode == "always":
            await self._run_stage_command(
                [self._config.docker_binary, "pull", image],
                cwd=cwd,
                log_path=log_path,
            )
            return

        inspect_result = await self._run_stage_command(
            [self._config.docker_binary, "image", "inspect", image],
            cwd=cwd,
            log_path=log_path,
            check=False,
        )
        if inspect_result.returncode == 0:
            return
        await self._run_stage_command(
            [self._config.docker_binary, "pull", image],
            cwd=cwd,
            log_path=log_path,
        )

    def _build_container_run_command(
        self,
        *,
        job_id: UUID,
        repository: str,
        prepared: PreparedTaskWorkspace,
        container_spec: ContainerTaskSpec,
    ) -> tuple[list[str], str, str, dict[str, str]]:
        workspace_root = self._config.workdir
        if not workspace_root.is_absolute():
            raise ValueError(
                "MOONMIND_WORKDIR must be an absolute path for task.container execution"
            )

        mount_target = str(workspace_root)
        artifact_dir_in_container = str(
            prepared.artifacts_dir / container_spec.artifacts_subdir
        )
        workdir = container_spec.workdir or str(prepared.repo_dir)
        container_name = f"mm-task-{job_id}"

        command: list[str] = [
            self._config.docker_binary,
            "run",
            "--rm",
            "--name",
            container_name,
            "--label",
            f"moonmind.job_id={job_id}",
            "--label",
            f"moonmind.repository={repository}",
            "--label",
            "moonmind.runtime=container",
        ]

        if self._config.container_workspace_volume:
            command.extend(
                [
                    "--mount",
                    (
                        "type=volume,"
                        f"src={self._config.container_workspace_volume},"
                        f"dst={mount_target}"
                    ),
                ]
            )
        else:
            command.extend(
                [
                    "--mount",
                    (
                        "type=bind,"
                        f"src={workspace_root.resolve()},"
                        f"dst={mount_target}"
                    ),
                ]
            )

        for cache_volume in container_spec.cache_volumes:
            command.extend(
                [
                    "--mount",
                    (
                        "type=volume,"
                        f"src={cache_volume.name},"
                        f"dst={cache_volume.target}"
                    ),
                ]
            )

        if container_spec.cpus:
            command.extend(["--cpus", container_spec.cpus])
        if container_spec.memory:
            command.extend(["--memory", container_spec.memory])

        command.extend(["--workdir", workdir])

        run_env = {
            **container_spec.env,
            "ARTIFACT_DIR": artifact_dir_in_container,
            "JOB_ID": str(job_id),
            "REPOSITORY": repository,
        }
        for key in sorted(run_env):
            command.extend(["--env", key])

        command.append(container_spec.image)
        command.extend(container_spec.command)
        return command, container_name, artifact_dir_in_container, run_env

    async def _run_container_execute_stage(
        self,
        *,
        job_id: UUID,
        canonical_payload: Mapping[str, Any],
        prepared: PreparedTaskWorkspace,
        container_spec: ContainerTaskSpec,
    ) -> WorkerExecutionResult:
        repository = str(canonical_payload.get("repository") or "").strip()
        if not repository:
            raise ValueError("repository is required for task.container execution")

        run_command: list[str] = []
        container_name = f"mm-task-{job_id}"
        artifact_root = prepared.artifacts_dir / container_spec.artifacts_subdir
        artifact_root.mkdir(parents=True, exist_ok=True)
        artifact_dir_in_container = str(artifact_root)
        run_result: CommandResult | None = None
        timed_out = False
        error_message: str | None = None
        started_at = datetime.now(UTC)

        await self._emit_event(
            job_id=job_id,
            level="info",
            message="moonmind.task.container.started",
            payload={
                "image": container_spec.image,
                "command": self._container_command_summary(container_spec.command),
                "pullMode": container_spec.pull_mode,
                "timeoutSeconds": container_spec.timeout_seconds,
            },
        )

        try:
            await self._ensure_container_image(
                image=container_spec.image,
                pull_mode=container_spec.pull_mode,
                cwd=prepared.repo_dir,
                log_path=prepared.execute_log_path,
            )
            run_command, container_name, artifact_dir_in_container, run_env = (
                self._build_container_run_command(
                    job_id=job_id,
                    repository=repository,
                    prepared=prepared,
                    container_spec=container_spec,
                )
            )
            run_command_env = dict(environ)
            run_command_env.update(run_env)
            run_result = await self._run_stage_command(
                run_command,
                cwd=prepared.repo_dir,
                log_path=prepared.execute_log_path,
                check=False,
                env=run_command_env,
                redaction_values=tuple(value for value in run_env.values() if value),
                timeout_seconds=float(container_spec.timeout_seconds),
            )
            if run_result.returncode != 0:
                error_message = f"container command failed ({run_result.returncode})"
        except asyncio.TimeoutError:
            timed_out = True
            error_message = (
                f"container execution timed out after {container_spec.timeout_seconds}s"
            )
            with suppress(Exception):
                await asyncio.wait_for(
                    self._run_stage_command(
                        [self._config.docker_binary, "stop", container_name],
                        cwd=prepared.repo_dir,
                        log_path=prepared.execute_log_path,
                        check=False,
                    ),
                    timeout=_CONTAINER_STOP_TIMEOUT_SECONDS,
                )
            run_result = CommandResult(
                command=tuple(run_command),
                returncode=124,
                stdout="",
                stderr=error_message,
            )
        except Exception as exc:
            error_message = str(exc)
            run_result = CommandResult(
                command=tuple(run_command),
                returncode=1,
                stdout="",
                stderr=error_message,
            )

        finished_at = datetime.now(UTC)
        duration_seconds = max(0.0, (finished_at - started_at).total_seconds())
        exit_code = run_result.returncode if run_result is not None else None
        succeeded = bool(run_result is not None and run_result.returncode == 0) and (
            not timed_out
        )

        metadata_dir = (
            prepared.artifacts_dir / container_spec.artifacts_subdir / "metadata"
        )
        metadata_dir.mkdir(parents=True, exist_ok=True)
        metadata_path = metadata_dir / "run.json"
        metadata_payload = {
            "jobId": str(job_id),
            "repository": repository,
            "containerName": container_name,
            "image": container_spec.image,
            "command": list(container_spec.command),
            "commandSummary": self._container_command_summary(container_spec.command),
            "pullMode": container_spec.pull_mode,
            "workdir": container_spec.workdir or str(prepared.repo_dir),
            "artifactDir": artifact_dir_in_container,
            "timeoutSeconds": container_spec.timeout_seconds,
            "timedOut": timed_out,
            "exitCode": exit_code,
            "startedAt": started_at.isoformat(),
            "finishedAt": finished_at.isoformat(),
            "durationSeconds": duration_seconds,
            "error": error_message,
        }
        metadata_path.write_text(
            json.dumps(metadata_payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        await self._emit_event(
            job_id=job_id,
            level="info" if succeeded else "error",
            message="moonmind.task.container.finished",
            payload={
                "containerName": container_name,
                "image": container_spec.image,
                "command": self._container_command_summary(container_spec.command),
                "timedOut": timed_out,
                "exitCode": exit_code,
                "durationSeconds": duration_seconds,
                "artifactsSubdir": container_spec.artifacts_subdir,
            },
        )

        return WorkerExecutionResult(
            succeeded=succeeded,
            summary="container task execution completed" if succeeded else None,
            error_message=error_message,
            artifacts=(
                ArtifactUpload(
                    path=metadata_path,
                    name=f"{container_spec.artifacts_subdir}/metadata/run.json",
                    content_type="application/json",
                ),
            ),
        )

    def _compose_step_instruction_for_runtime(
        self,
        *,
        canonical_payload: Mapping[str, Any],
        runtime_mode: str,
        step: ResolvedTaskStep,
        total_steps: int,
    ) -> str:
        task_node = canonical_payload.get("task")
        task = task_node if isinstance(task_node, Mapping) else {}
        objective = str(task.get("instructions") or "").strip()
        step_title = f" {step.title}" if step.title else ""
        normalized_objective = _normalize_instruction_text_for_comparison(objective)
        step_instruction_value = (
            str(step.instructions).strip() if step.instructions is not None else ""
        )
        normalized_step_instruction = _normalize_instruction_text_for_comparison(
            step_instruction_value
        )
        if (
            step_instruction_value
            and normalized_objective
            and normalized_step_instruction == normalized_objective
        ):
            step_instruction = (
                "(same as task objective; no additional step-specific instructions)"
            )
        else:
            step_instruction = (
                step_instruction_value
                or "(no step-specific instructions; continue based on objective)"
            )
        instruction = (
            "MOONMIND TASK OBJECTIVE:\n"
            f"{objective}\n\n"
            f"STEP {step.step_index + 1}/{total_steps} {step.step_id}{step_title}:\n"
            f"{step_instruction}\n\n"
            "EFFECTIVE SKILL:\n"
            f"{step.effective_skill_id}\n\n"
            "WORKSPACE:\n"
            "- Repo is already checked out on the working branch.\n"
            "- Do NOT commit or push. Publish is handled by MoonMind publish stage.\n"
            "- Skills are available via .agents/skills and .gemini/skills links.\n"
            "- Selected skills are always materialized under ../skills_active/<skill-id>/.\n"
            "- Write logs to stdout/stderr; MoonMind captures them.\n\n"
            f"RUNTIME ADAPTER: {runtime_mode}"
        )
        if step.effective_skill_id != "auto":
            instruction += (
                "\n\nSKILL USAGE:\n"
                "Use the selected skill's files under .agents/skills/{skill}/ as the procedure for this step. "
                "If that path is missing, use ../skills_active/{skill}/."
            ).format(skill=step.effective_skill_id)
        return instruction

    def _build_non_codex_runtime_command(
        self,
        *,
        runtime_mode: str,
        instruction: str,
        model: str | None,
        effort: str | None,
    ) -> list[str]:
        if runtime_mode == "gemini":
            command = [self._config.gemini_binary, "--prompt", instruction]
        elif runtime_mode == "claude":
            command = [self._config.claude_binary, "--print", instruction]
        else:
            raise ValueError(f"runtime adapter not implemented: {runtime_mode}")

        if model:
            command.extend(["--model", model])
        if effort:
            command.extend(["--effort", effort])
        return command

    def _build_non_codex_runtime_env(
        self,
        *,
        runtime_mode: str,
    ) -> Mapping[str, str] | None:
        if runtime_mode != "gemini":
            return None
        if self._config.gemini_cli_auth_mode != "oauth":
            return None
        env = dict(environ)
        env.pop("GEMINI_API_KEY", None)
        env.pop("GOOGLE_API_KEY", None)
        return env

    def _resolve_runtime_overrides(
        self,
        *,
        canonical_payload: Mapping[str, Any],
        runtime_mode: str,
    ) -> tuple[str | None, str | None]:
        task_node = canonical_payload.get("task")
        task = task_node if isinstance(task_node, Mapping) else {}
        runtime_node = task.get("runtime")
        runtime = runtime_node if isinstance(runtime_node, Mapping) else {}
        model_override = str(runtime.get("model") or "").strip() or None
        effort_override = str(runtime.get("effort") or "").strip() or None

        if runtime_mode == "codex":
            return (
                model_override or self._config.default_codex_model,
                effort_override or self._config.default_codex_effort,
            )
        if runtime_mode == "gemini":
            return (
                model_override or self._config.default_gemini_model,
                effort_override or self._config.default_gemini_effort,
            )
        if runtime_mode == "claude":
            return (
                model_override or self._config.default_claude_model,
                effort_override or self._config.default_claude_effort,
            )
        return (model_override, effort_override)

    def _runtime_can_execute(self, runtime_mode: str) -> bool:
        worker_runtime = self._config.worker_runtime
        if worker_runtime == "universal":
            return runtime_mode in SUPPORTED_EXECUTION_RUNTIMES
        return runtime_mode == worker_runtime

    def _validate_required_job_policy(
        self,
        canonical_payload: Mapping[str, Any],
    ) -> str | None:
        """Fail-closed policy guard for repository + capability requirements."""

        repository = str(canonical_payload.get("repository") or "").strip()
        if not repository:
            return "repository is required for task execution"

        required_raw = canonical_payload.get("requiredCapabilities")
        if not isinstance(required_raw, list):
            return "requiredCapabilities must be a non-empty list"
        required = {
            str(item).strip().lower() for item in required_raw if str(item).strip()
        }
        if not required:
            return "requiredCapabilities must include at least one capability"

        available = {
            str(item).strip().lower()
            for item in self._config.worker_capabilities
            if str(item).strip()
        }
        missing = sorted(required - available)
        if missing:
            return f"worker is missing required capabilities: {', '.join(missing)}"
        return None

    async def _resolve_task_auth_context(
        self,
        *,
        canonical_payload: Mapping[str, Any],
        publish_mode: str,
    ) -> TaskAuthContext:
        """Resolve token-free auth refs with Vault-first and env-token fallback."""

        auth_node = canonical_payload.get("auth")
        auth = auth_node if isinstance(auth_node, Mapping) else {}
        repo_auth_ref = str(auth.get("repoAuthRef") or "").strip() or None
        publish_auth_ref = str(auth.get("publishAuthRef") or "").strip() or None

        repo_token, repo_source = await self._resolve_github_token(
            auth_ref=repo_auth_ref,
            purpose="repository",
        )
        repo_env = self._build_command_env(
            repo_token,
            git_user_name=self._config.git_user_name,
            git_user_email=self._config.git_user_email,
        )

        publish_source: str | None = None
        publish_env: dict[str, str] | None = None
        if publish_mode != "none":
            publish_ref = publish_auth_ref or repo_auth_ref
            publish_token, publish_source = await self._resolve_github_token(
                auth_ref=publish_ref,
                purpose="publish",
            )
            publish_env = self._build_command_env(
                publish_token,
                git_user_name=self._config.git_user_name,
                git_user_email=self._config.git_user_email,
            )
            if publish_env is None:
                publish_env = repo_env
                publish_source = repo_source

        return TaskAuthContext(
            repo_auth_ref=repo_auth_ref,
            publish_auth_ref=publish_auth_ref,
            repo_auth_source=repo_source,
            publish_auth_source=publish_source,
            repo_command_env=repo_env,
            publish_command_env=publish_env,
        )

    async def _resolve_github_token(
        self,
        *,
        auth_ref: str | None,
        purpose: str,
    ) -> tuple[str | None, str]:
        """Resolve a GitHub token from Vault reference or env fallback."""

        if auth_ref:
            if self._vault_secret_resolver is None:
                raise RuntimeError(
                    f"{purpose} auth ref was provided but Vault resolver is not configured"
                )
            try:
                resolved = await self._vault_secret_resolver.resolve_github_auth(
                    auth_ref
                )
            except SecretReferenceError as exc:
                raise RuntimeError(f"{purpose} auth resolution failed: {exc}") from exc
            self._register_redaction_value(resolved.token)
            return (resolved.token, f"vault:{resolved.source_ref}")

        env_token = str(environ.get("GITHUB_TOKEN", "")).strip() or None
        if env_token:
            self._register_redaction_value(env_token)
            return (env_token, "env:GITHUB_TOKEN")
        return (None, "none")

    @staticmethod
    def _build_command_env(
        token: str | None,
        *,
        git_user_name: str | None = None,
        git_user_email: str | None = None,
    ) -> dict[str, str] | None:
        inherited_keys = (
            "PATH",
            "HOME",
            "USER",
            "LOGNAME",
            "TMPDIR",
            "TMP",
            "TEMP",
            "LANG",
            "LC_ALL",
            "LC_CTYPE",
            "SHELL",
            "SSH_AUTH_SOCK",
            "SYSTEMROOT",
            "COMSPEC",
            "PATHEXT",
            "WINDIR",
        )
        command_env: dict[str, str] = {}
        for key in inherited_keys:
            value = environ.get(key)
            if value:
                command_env[key] = value
        configured = False

        if token:
            command_env["GITHUB_TOKEN"] = token
            command_env["GH_TOKEN"] = token
            command_env["GIT_TERMINAL_PROMPT"] = "0"
            configured = True

        git_name = str(git_user_name or "").strip()
        if git_name:
            command_env["GIT_AUTHOR_NAME"] = git_name
            command_env["GIT_COMMITTER_NAME"] = git_name
            configured = True

        git_email = str(git_user_email or "").strip()
        if git_email:
            command_env["GIT_AUTHOR_EMAIL"] = git_email
            command_env["GIT_COMMITTER_EMAIL"] = git_email
            configured = True

        return command_env if configured else None

    def _register_redaction_value(self, value: str | None) -> None:
        candidate = str(value or "").strip()
        if candidate:
            self._dynamic_redaction_values.add(candidate)

    def _redact_text(self, text: str) -> str:
        redacted = self._secret_redactor.scrub(text)
        for value in sorted(self._dynamic_redaction_values, key=len, reverse=True):
            redacted = redacted.replace(value, "[REDACTED]")
        return redacted

    def _redact_payload(self, payload: Any) -> Any:
        if payload is None:
            return None
        if isinstance(payload, str):
            return self._redact_text(payload)
        if isinstance(payload, Mapping):
            return {
                str(key): self._redact_payload(value) for key, value in payload.items()
            }
        if isinstance(payload, list):
            return [self._redact_payload(item) for item in payload]
        if isinstance(payload, tuple):
            return tuple(self._redact_payload(item) for item in payload)
        return payload

    async def _heartbeat_loop(
        self,
        *,
        job_id: UUID,
        stop_event: asyncio.Event,
        cancel_event: asyncio.Event,
        pause_event: asyncio.Event | None = None,
        live_session_bootstrap_cwd: Path | None = None,
        live_session_bootstrap_log_path: Path | None = None,
    ) -> None:
        """Send lease renewals while a job is actively executing."""

        interval_seconds = min(max(1.0, self._config.lease_seconds / 3.0), 5.0)
        effective_pause_event = pause_event or asyncio.Event()
        while not stop_event.is_set():
            await asyncio.sleep(interval_seconds)
            if stop_event.is_set():
                return
            try:
                heartbeat = await self._queue_client.heartbeat(
                    job_id=job_id,
                    worker_id=self._config.worker_id,
                    lease_seconds=self._config.lease_seconds,
                )
                self._handle_system_metadata(heartbeat.system)
                payload = heartbeat.job
                cancel_requested_at = str(
                    payload.get("cancelRequestedAt") or ""
                ).strip()
                if cancel_requested_at:
                    cancel_event.set()
                payload_node = payload.get("payload")
                payload_mapping = (
                    payload_node if isinstance(payload_node, Mapping) else {}
                )
                live_control_node = payload_mapping.get("liveControl")
                live_control = (
                    live_control_node if isinstance(live_control_node, Mapping) else {}
                )
                global_quiesce = (
                    heartbeat.system.workers_paused
                    and heartbeat.system.mode == "quiesce"
                )
                paused = bool(live_control.get("paused")) or global_quiesce
                if paused:
                    effective_pause_event.set()
                else:
                    effective_pause_event.clear()
                if (
                    self._active_live_session is None
                    and not self._config.live_session_enabled_default
                    and live_session_bootstrap_cwd is not None
                    and live_session_bootstrap_log_path is not None
                ):
                    with suppress(Exception):
                        await self._ensure_live_session_started(
                            job_id=job_id,
                            cwd=live_session_bootstrap_cwd,
                            log_path=live_session_bootstrap_log_path,
                        )
                if self._active_live_session is not None:
                    with suppress(Exception):
                        await self._queue_client.heartbeat_live_session(
                            job_id=job_id,
                            worker_id=self._config.worker_id,
                        )
            except Exception:
                # Heartbeat errors are tolerated so terminal transition can still run.
                continue

    async def _should_bootstrap_live_session(self, *, job_id: UUID) -> bool:
        """Return whether this run should start a live session for current config/state."""

        if self._config.live_session_enabled_default:
            return True
        payload = await self._queue_client.get_live_session(job_id=job_id)
        if not isinstance(payload, Mapping):
            return False
        session_node = payload.get("session")
        if not isinstance(session_node, Mapping):
            return False
        status = str(session_node.get("status") or "").strip().lower()
        if status not in {"starting", "ready"}:
            return False
        worker_id = str(session_node.get("workerId") or "").strip()
        if worker_id and worker_id != self._config.worker_id:
            return False
        return True

    async def _run_command_without_logging(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
    ) -> CommandResult:
        """Run a subprocess quietly and capture output without stage-log emission."""

        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await process.communicate()
        return CommandResult(
            tuple(command),
            int(process.returncode or 0),
            stdout_bytes.decode(errors="replace"),
            stderr_bytes.decode(errors="replace"),
        )

    async def _upload_artifacts(
        self,
        *,
        job_id: UUID,
        artifacts: Sequence[ArtifactUpload],
        uploaded_artifact_names: set[str] | None = None,
    ) -> set[str]:
        optional_failures: set[str] = set()
        for artifact in artifacts:
            if not artifact.path.exists():
                continue
            if artifact.path.stat().st_size == 0:
                continue
            if (
                uploaded_artifact_names is not None
                and artifact.name in uploaded_artifact_names
            ):
                continue
            try:
                await self._queue_client.upload_artifact(
                    job_id=job_id,
                    worker_id=self._config.worker_id,
                    artifact=artifact,
                )
                if uploaded_artifact_names is not None:
                    uploaded_artifact_names.add(artifact.name)
            except Exception as exc:
                if artifact.required:
                    raise
                optional_failures.add(artifact.name)
                await self._emit_event(
                    job_id=job_id,
                    level="warn",
                    message="Optional artifact upload failed",
                    payload={"name": artifact.name, "error": str(exc)},
                )
                logger.warning(
                    "Optional artifact upload failed for job %s artifact %s: %s",
                    job_id,
                    artifact.name,
                    exc,
                )
        return optional_failures

    async def _maybe_submit_task_proposals(
        self,
        *,
        job: ClaimedJob,
        prepared: PreparedTaskWorkspace,
    ) -> None:
        """Read worker-generated proposals and submit them when enabled."""

        if not self._config.enable_task_proposals:
            return
        proposals_path = prepared.task_context_path / "task_proposals.json"
        if not proposals_path.exists():
            return
        try:
            raw_text = proposals_path.read_text(encoding="utf-8")
            parsed = json.loads(raw_text)
        except Exception:
            logger.warning(
                "Task proposal file %s is missing or invalid JSON; skipping",
                proposals_path,
            )
            return
        proposals = parsed if isinstance(parsed, list) else [parsed]
        canonical_payload = job.payload if isinstance(job.payload, Mapping) else {}
        project_repository = str(canonical_payload.get("repository") or "").strip()
        if not project_repository:
            logger.warning(
                "Skipping task proposal submission for job %s: canonical repository missing",
                job.id,
            )
            return

        task_node = canonical_payload.get("task")
        policy_node = (
            task_node.get("proposalPolicy") if isinstance(task_node, Mapping) else None
        )
        task_policy: TaskProposalPolicy | None = None
        if isinstance(policy_node, Mapping):
            try:
                task_policy = TaskProposalPolicy.model_validate(dict(policy_node))
            except ValidationError:
                logger.warning(
                    "Invalid proposalPolicy override for job %s; using defaults",
                    job.id,
                )

        defaults = settings.spec_workflow
        effective_policy = build_effective_proposal_policy(
            policy=task_policy,
            default_targets=defaults.proposal_targets_default,
            default_max_items_project=defaults.proposal_max_items_project,
            default_max_items_moonmind=defaults.proposal_max_items_moonmind,
            default_moonmind_severity_floor=defaults.proposal_moonmind_severity_floor,
            severity_vocabulary=settings.task_proposals.severity_vocabulary,
        )
        approved_ci_tags = _MOONMIND_SIGNAL_TAGS

        submitted = 0
        for candidate in proposals:
            if not isinstance(candidate, dict):
                continue
            payload = copy.deepcopy(candidate)
            origin_node = payload.get("origin")
            origin = origin_node if isinstance(origin_node, Mapping) else {}
            if not origin.get("source"):
                origin["source"] = "queue"
            if not origin.get("id"):
                origin["id"] = str(job.id)
            metadata = origin.get("metadata")
            metadata_dict = metadata if isinstance(metadata, Mapping) else {}
            metadata_dict.setdefault("triggerRepo", project_repository)
            metadata_dict.setdefault("triggerJobId", str(job.id))
            metadata_dict.setdefault("startingBranch", prepared.starting_branch)
            metadata_dict.setdefault("workingBranch", prepared.working_branch)
            if prepared.new_branch and "newBranch" not in metadata_dict:
                metadata_dict["newBranch"] = prepared.new_branch
            origin["metadata"] = metadata_dict
            payload["origin"] = origin

            signal_payload = _extract_proposal_signal(payload)
            if signal_payload:
                metadata_dict["signal"] = signal_payload
            severity_value = (
                str(signal_payload.get("severity") or "").strip().lower()
                if signal_payload
                else None
            )

            normalized_tags = _normalize_proposal_tags(payload.get("tags"))
            payload["tags"] = normalized_tags

            title_text = str(payload.get("title") or "").strip()
            if not title_text:
                logger.warning(
                    "Proposal entry in %s missing title; skipping", proposals_path
                )
                continue

            if not isinstance(payload.get("taskCreateRequest"), Mapping):
                logger.warning(
                    "Proposal entry in %s missing taskCreateRequest; skipping",
                    proposals_path,
                )
                continue

            if effective_policy.has_project_capacity():
                project_payload = copy.deepcopy(payload)
                if _ensure_task_request_repository(
                    project_payload,
                    repository=project_repository,
                    proposals_path=proposals_path,
                ):
                    try:
                        await self._queue_client.create_task_proposal(
                            proposal=project_payload
                        )
                        effective_policy.consume_project_slot()
                        submitted += 1
                    except Exception:
                        logger.exception(
                            "Failed to submit project proposal derived from %s",
                            proposals_path,
                        )

            if effective_policy.has_moonmind_capacity():
                if not effective_policy.severity_meets_floor(severity_value):
                    logger.info(
                        "Skipping MoonMind CI proposal for job %s: severity '%s' below floor '%s'",
                        job.id,
                        severity_value or "-",
                        effective_policy.min_severity_for_moonmind,
                    )
                    continue
                moonmind_tags = [
                    tag for tag in normalized_tags if tag in approved_ci_tags
                ]
                if not moonmind_tags:
                    logger.info(
                        "Skipping MoonMind CI proposal for job %s: missing approved tags",
                        job.id,
                    )
                    continue
                if not signal_payload:
                    logger.info(
                        "Skipping MoonMind CI proposal for job %s: signal metadata missing",
                        job.id,
                    )
                    continue

                moonmind_payload = copy.deepcopy(payload)
                moonmind_payload["category"] = "run_quality"
                moonmind_payload["tags"] = moonmind_tags
                moonmind_payload["title"] = _append_tag_slug(
                    _ensure_run_quality_title(title_text), moonmind_tags
                )
                if _ensure_task_request_repository(
                    moonmind_payload,
                    repository=settings.task_proposals.moonmind_ci_repository,
                    proposals_path=proposals_path,
                ):
                    try:
                        await self._queue_client.create_task_proposal(
                            proposal=moonmind_payload
                        )
                        effective_policy.consume_moonmind_slot()
                        submitted += 1
                    except Exception:
                        logger.exception(
                            "Failed to submit MoonMind proposal derived from %s",
                            proposals_path,
                        )
        if submitted:
            logger.info(
                "Submitted %s task proposal(s) for job %s from %s",
                submitted,
                job.id,
                proposals_path,
            )
            with suppress(Exception):
                proposals_path.unlink()

    async def _emit_event(
        self,
        *,
        job_id: UUID,
        level: str,
        message: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """Best-effort event emission for streaming-ish worker logs."""

        try:
            redacted_message = self._redact_text(message)
            redacted_payload = (
                self._redact_payload(payload) if payload is not None else None
            )
            await self._queue_client.append_event(
                job_id=job_id,
                worker_id=self._config.worker_id,
                level=level,
                message=redacted_message,
                payload=(
                    redacted_payload if isinstance(redacted_payload, dict) else None
                ),
            )
        except Exception:
            # Event publication failures should not break terminal job transitions.
            return


__all__ = [
    "ClaimedJob",
    "CodexWorker",
    "CodexWorkerConfig",
    "QueueApiClient",
    "QueueClientError",
]
