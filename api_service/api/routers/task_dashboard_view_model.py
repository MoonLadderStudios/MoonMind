"""Shared view-model helpers for the task dashboard UI."""

from __future__ import annotations

import os
import re
import time
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Mapping
from urllib.parse import urlparse

import httpx

from moonmind.config.settings import WorkflowSettings, settings
from moonmind.utils.build_info import resolve_moonmind_build_id
from moonmind.workflows.tasks.runtime_defaults import (
    DEFAULT_REPOSITORY,
    normalize_runtime_id,
    resolve_default_task_runtime,
    resolve_runtime_defaults,
)

_POLL_INTERVALS_MS = {
    "list": 5000,
    "detail": 2000,
    "events": 1000,
}

_SUPPORTED_WORKER_RUNTIMES = ("codex_cli", "gemini_cli", "claude_code", "jules", "universal")
_OWNER_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_SSH_GIT_RE = re.compile(
    r"^(?:ssh://)?git@[A-Za-z0-9.-]+[:/]"
    r"([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$"
)
_GITHUB_REPOSITORY_DISCOVERY_URL = "https://api.github.com/user/repos"
_GITHUB_REPOSITORY_METADATA_URL_TEMPLATE = "https://api.github.com/repos/{repository}"
_GITHUB_BRANCH_DISCOVERY_URL_TEMPLATE = "https://api.github.com/repos/{repository}/branches"
_GITHUB_REPOSITORY_DISCOVERY_TIMEOUT_SECONDS = 5.0
_GITHUB_REPOSITORY_DISCOVERY_CACHE_TTL_SECONDS = 60.0
_GITHUB_REPOSITORY_OPTIONS_CACHE: dict[
    str, tuple[float, tuple["RepositoryOption", ...], str | None]
] = {}
_GITHUB_BRANCH_OPTIONS_CACHE: dict[
    str, tuple[float, tuple["BranchOption", ...], str | None, str | None]
] = {}

_JIRA_CREATE_PAGE_SOURCES = {
    "connections": "/api/jira/connections/verify",
    "projects": "/api/jira/projects",
    "boards": "/api/jira/projects/{projectKey}/boards",
    "columns": "/api/jira/boards/{boardId}/columns",
    "issues": "/api/jira/boards/{boardId}/issues",
    "issue": "/api/jira/issues/{issueKey}",
}

def _validate_jira_source_templates(sources: Mapping[str, str]) -> None:
    invalid = [
        name
        for name, value in sources.items()
        for normalized in (value.strip(),)
        if (
            value != normalized
            or not normalized
            or not normalized.startswith("/api/")
            or "://" in normalized
        )
    ]
    if invalid:
        invalid_names = ", ".join(sorted(invalid))
        raise ValueError(
            "Jira Create-page sources must be MoonMind API path templates: "
            f"{invalid_names}"
        )

_validate_jira_source_templates(_JIRA_CREATE_PAGE_SOURCES)

@dataclass(frozen=True, slots=True)
class RepositoryOption:
    """Browser-safe repository suggestion for the Create page."""

    value: str
    label: str
    source: str

    def to_payload(self) -> dict[str, str]:
        return {
            "value": self.value,
            "label": self.label,
            "source": self.source,
        }

@dataclass(frozen=True, slots=True)
class BranchOption:
    """Browser-safe branch suggestion for the Create page."""

    value: str
    label: str
    source: str

    def to_payload(self) -> dict[str, str]:
        return {
            "value": self.value,
            "label": self.label,
            "source": self.source,
        }

def _build_jira_sources() -> dict[str, str]:
    """Return MoonMind-owned Jira browser endpoint templates."""

    return dict(_JIRA_CREATE_PAGE_SOURCES)

def _normalize_repository_value(value: object) -> str | None:
    """Return a browser-safe owner/repo value, or ``None`` when invalid."""

    raw = str(value or "").strip()
    if not raw:
        return None
    if _OWNER_REPO_RE.fullmatch(raw):
        return raw

    ssh_match = _SSH_GIT_RE.fullmatch(raw)
    if ssh_match:
        owner, repo = ssh_match.groups()
        normalized = f"{owner}/{repo}"
        return normalized if _OWNER_REPO_RE.fullmatch(normalized) else None

    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        return None

    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 2:
        return None
    owner, repo = parts
    if repo.endswith(".git"):
        repo = repo[:-4]
    normalized = f"{owner}/{repo}"
    return normalized if _OWNER_REPO_RE.fullmatch(normalized) else None

def _append_repository_option(
    options: list[RepositoryOption],
    seen: set[str],
    value: object,
    *,
    source: str,
) -> None:
    normalized = _normalize_repository_value(value)
    if not normalized:
        return
    key = normalized.lower()
    if key in seen:
        return
    seen.add(key)
    options.append(
        RepositoryOption(value=normalized, label=normalized, source=source)
    )

def _fetch_github_repository_options(
    token: str,
) -> tuple[list[RepositoryOption], str | None]:
    """Fetch credential-visible GitHub repositories without exposing secrets."""

    if not token:
        return [], None
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        with httpx.Client(
            timeout=_GITHUB_REPOSITORY_DISCOVERY_TIMEOUT_SECONDS
        ) as client:
            response = client.get(
                _GITHUB_REPOSITORY_DISCOVERY_URL,
                headers=headers,
                params={
                    "per_page": 100,
                    "sort": "updated",
                    "affiliation": "owner,collaborator,organization_member",
                },
            )
            response.raise_for_status()
            data = response.json()
    except (httpx.HTTPError, ValueError):
        return [], "GitHub repository discovery is unavailable."

    options: list[RepositoryOption] = []
    seen: set[str] = set()
    if isinstance(data, list):
        for item in data:
            if isinstance(item, Mapping):
                _append_repository_option(
                    options,
                    seen,
                    item.get("full_name"),
                    source="github",
                )
    return options, None

def _fetch_github_branch_options(
    token: str,
    repository: str,
) -> tuple[list[BranchOption], str | None, str | None]:
    """Fetch credential-visible GitHub branches without exposing secrets."""

    normalized_repository = _normalize_repository_value(repository)
    if not token or not normalized_repository:
        return [], None, None
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    options: list[BranchOption] = []
    seen: set[str] = set()
    default_branch: str | None = None
    next_url: str | None = _GITHUB_BRANCH_DISCOVERY_URL_TEMPLATE.format(
        repository=normalized_repository
    )
    params: dict[str, int] | None = {"per_page": 100}
    try:
        with httpx.Client(
            timeout=_GITHUB_REPOSITORY_DISCOVERY_TIMEOUT_SECONDS
        ) as client:
            metadata_response = client.get(
                _GITHUB_REPOSITORY_METADATA_URL_TEMPLATE.format(
                    repository=normalized_repository
                ),
                headers=headers,
            )
            metadata_response.raise_for_status()
            metadata = metadata_response.json()
            if isinstance(metadata, Mapping):
                default_branch = (
                    str(metadata.get("default_branch") or "").strip() or None
                )
            while next_url:
                response = client.get(
                    next_url,
                    headers=headers,
                    params=params,
                )
                params = None
                response.raise_for_status()
                data = response.json()
                if isinstance(data, list):
                    for item in data:
                        if not isinstance(item, Mapping):
                            continue
                        branch_name = str(item.get("name") or "").strip()
                        if not branch_name or branch_name in seen:
                            continue
                        seen.add(branch_name)
                        options.append(
                            BranchOption(
                                value=branch_name,
                                label=branch_name,
                                source="github",
                            )
                        )
                next_url = response.links.get("next", {}).get("url")
    except (httpx.HTTPError, ValueError):
        return [], "GitHub branch lookup is unavailable.", None

    return options, None, default_branch

def _github_repository_options_cache_key(token: str) -> str:
    return sha256(token.encode("utf-8")).hexdigest()

def _github_branch_options_cache_key(token: str, repository: str) -> str:
    normalized_repository = _normalize_repository_value(repository) or ""
    raw_key = f"{token}:{normalized_repository.lower()}"
    return sha256(raw_key.encode("utf-8")).hexdigest()

def _get_cached_github_repository_options(
    token: str,
) -> tuple[list[RepositoryOption], str | None]:
    now = time.monotonic()
    cache_key = _github_repository_options_cache_key(token)
    cached = _GITHUB_REPOSITORY_OPTIONS_CACHE.get(cache_key)
    if cached:
        cached_at, cached_options, cached_error = cached
        if now - cached_at < _GITHUB_REPOSITORY_DISCOVERY_CACHE_TTL_SECONDS:
            return list(cached_options), cached_error

    options, error = _fetch_github_repository_options(token)
    _GITHUB_REPOSITORY_OPTIONS_CACHE[cache_key] = (now, tuple(options), error)
    return options, error

def _get_cached_github_branch_options(
    token: str,
    repository: str,
) -> tuple[list[BranchOption], str | None, str | None]:
    now = time.monotonic()
    cache_key = _github_branch_options_cache_key(token, repository)
    cached = _GITHUB_BRANCH_OPTIONS_CACHE.get(cache_key)
    if cached:
        cached_at, cached_options, cached_error, cached_default_branch = cached
        if now - cached_at < _GITHUB_REPOSITORY_DISCOVERY_CACHE_TTL_SECONDS:
            return list(cached_options), cached_error, cached_default_branch

    options, error, default_branch = _fetch_github_branch_options(token, repository)
    _GITHUB_BRANCH_OPTIONS_CACHE[cache_key] = (
        now,
        tuple(options),
        error,
        default_branch,
    )
    return options, error, default_branch

def _is_create_page_path(initial_path: str) -> bool:
    normalized_path = urlparse(initial_path or "").path.rstrip("/")
    return normalized_path in {"/tasks/new", "/tasks/create"}

def _build_repository_options(
    *,
    include_credential_discovery: bool = True,
) -> dict[str, Any]:
    """Build Create-page repository suggestions from safe runtime sources."""

    options: list[RepositoryOption] = []
    seen: set[str] = set()
    _append_repository_option(
        options,
        seen,
        settings.workflow.github_repository,
        source="default",
    )

    configured_repos = str(getattr(settings.github, "github_repos", "") or "")
    for raw_repo in configured_repos.split(","):
        _append_repository_option(options, seen, raw_repo, source="configured")

    discovery_error: str | None = None
    github_enabled = bool(getattr(settings.github, "github_enabled", True))
    github_token = str(getattr(settings.github, "github_token", "") or "").strip()
    if include_credential_discovery and github_enabled and github_token:
        discovered, discovery_error = _get_cached_github_repository_options(
            github_token
        )
        if discovery_error:
            discovery_error = "GitHub repository discovery is unavailable."
        for option in discovered:
            _append_repository_option(
                options,
                seen,
                option.value,
                source="github",
            )

    return {
        "items": [option.to_payload() for option in options],
        "error": discovery_error,
    }

def build_repository_branch_options(repository: str) -> dict[str, Any]:
    """Build Create-page branch suggestions through MoonMind-owned GitHub lookup."""

    normalized_repository = _normalize_repository_value(repository)
    if not normalized_repository:
        return {
            "items": [],
            "error": "Repository must be owner/repo before branches can be loaded.",
        }

    github_enabled = bool(getattr(settings.github, "github_enabled", True))
    github_token = str(getattr(settings.github, "github_token", "") or "").strip()
    if not github_enabled or not github_token:
        return {
            "items": [],
            "error": "GitHub branch lookup is unavailable.",
        }

    options, error, default_branch = _get_cached_github_branch_options(
        github_token,
        normalized_repository,
    )
    if error:
        error = "GitHub branch lookup is unavailable."
    return {
        "items": [option.to_payload() for option in options],
        "error": error,
        "defaultBranch": default_branch,
    }

def _jira_create_page_enabled() -> bool:
    """Return whether the Create-page Jira browser rollout is enabled."""

    return bool(settings.feature_flags.jira_create_page_enabled)

_STATUS_MAPS: dict[str, dict[str, str]] = {
    "proposals": {
        "open": "queued",
        "promoted": "completed",
        "dismissed": "canceled",
        "accepted": "completed",
        "rejected": "failed",
    },
    "temporal": {
        "scheduled": "queued",
        "initializing": "queued",
        "planning": "running",
        "executing": "running",
        "proposals": "running",
        "awaiting_external": "awaiting_action",
        "awaiting_slot": "queued",
        "waiting_on_dependencies": "waiting",
        "finalizing": "running",
        "running": "running",
        "succeeded": "completed",
        "completed": "completed",
        "failed": "failed",
        "canceled": "canceled",
        # Accept British spelling from legacy data or external adapters.
        "cancelled": "canceled",
        "queued": "queued",
        "awaiting_action": "awaiting_action",
    },

}

def normalize_status(source: str, raw_status: str | None) -> str:
    """Normalize source-specific status values into dashboard display states."""

    source_key = source.strip().lower()
    status_key = (raw_status or "").strip().lower()

    # Prioritize Temporal states, allowing proposals to use their local mapping.
    map_key = "proposals" if source_key == "proposals" else "temporal"
    mapping = _STATUS_MAPS.get(map_key)
    
    if mapping and status_key in mapping:
        return mapping[status_key]

    # Fallback for unexpected values so the dashboard remains renderable.
    if "running" in status_key:
        return "running"
    if status_key in {"success", "completed", "done"}:
        return "succeeded"
    if status_key in {"error", "failed", "failure"}:
        return "failed"

    return "queued"

def status_maps() -> dict[str, dict[str, str]]:
    """Return a copy of status maps so callers can safely mutate local copies."""

    return deepcopy(_STATUS_MAPS)

def _build_supported_task_runtimes() -> list[str]:
    supported: list[str] = ["codex_cli", "gemini_cli", "claude_code", "codex_cloud"]
    if settings.jules_runtime_gate.enabled:
        supported.append("jules")
    return supported

def _build_default_attachment_policy(config: "dict[str, Any]") -> dict[str, Any]:
    """Normalize attachment policy values for dashboard consumption."""

    max_count = max(1, int(config.get("agent_job_attachment_max_count", 1) or 1))
    max_bytes = max(
        1, int(config.get("agent_job_attachment_max_bytes", 10 * 1024 * 1024) or 1)
    )
    total_bytes = max(
        1, int(config.get("agent_job_attachment_total_bytes", 25 * 1024 * 1024) or 1)
    )
    allowed_types = tuple(
        config.get("agent_job_attachment_allowed_content_types") or ()
    )
    return {
        "maxCount": max_count,
        "maxBytes": max_bytes,
        "totalBytes": max(total_bytes, max_bytes),
        "allowedContentTypes": (
            list(allowed_types)
            if allowed_types
            else ["image/png", "image/jpeg", "image/webp"]
        ),
    }

def build_live_logs_feature_config() -> dict[str, object]:
    """Build the grouped Live Logs feature flags for dashboard consumers."""

    return {
        "logStreamingEnabled": bool(WorkflowSettings(_env_file=None).log_streaming_enabled),
        "liveLogsSessionTimelineEnabled": bool(
            settings.feature_flags.live_logs_session_timeline_enabled
        ),
        "liveLogsSessionTimelineRollout": str(
            settings.feature_flags.live_logs_session_timeline_rollout
        ),
        "liveLogsStructuredHistoryEnabled": bool(
            settings.feature_flags.live_logs_structured_history_enabled
        ),
    }

def _build_dashboard_system_metadata() -> dict[str, str | None]:
    """Return operator-facing build metadata for the dashboard shell and runtime config."""

    build_id = resolve_moonmind_build_id()
    return {
        "buildId": build_id,
    }

def _build_jira_runtime_config() -> dict[str, Any] | None:
    """Build Create-page Jira browser config when the UI rollout is enabled."""

    if not _jira_create_page_enabled():
        return None

    return {
        "sources": _build_jira_sources(),
        "system": {
            "enabled": True,
            "defaultProjectKey": settings.feature_flags.jira_create_page_default_project_key,
            "defaultBoardId": settings.feature_flags.jira_create_page_default_board_id,
            "rememberLastBoardInSession": settings.feature_flags.jira_create_page_remember_last_board_in_session,
        },
    }

def build_runtime_config(initial_path: str) -> dict[str, Any]:
    """Build runtime config consumed by dashboard JavaScript."""

    supported_task_runtimes = _build_supported_task_runtimes()
    temporal_dashboard = settings.temporal_dashboard
    configured_runtime = normalize_runtime_id(
        str(os.environ.get("MOONMIND_WORKER_RUNTIME", "")).strip().lower() or None
    ) if os.environ.get("MOONMIND_WORKER_RUNTIME", "").strip() else ""
    if configured_runtime in supported_task_runtimes:
        default_task_runtime = configured_runtime
    else:
        configured_default = resolve_default_task_runtime(settings.workflow)
        if configured_default in supported_task_runtimes:
            default_task_runtime = configured_default
        else:
            default_task_runtime = supported_task_runtimes[0]
    default_task_model_by_runtime: dict[str, str] = {}
    default_task_effort_by_runtime: dict[str, str] = {}
    for runtime in supported_task_runtimes:
        default_model, default_effort = resolve_runtime_defaults(
            runtime,
            workflow_settings=settings.workflow,
        )
        if default_model:
            default_task_model_by_runtime[runtime] = default_model
        if default_effort:
            default_task_effort_by_runtime[runtime] = default_effort
    default_task_model = default_task_model_by_runtime.get(default_task_runtime, "")
    default_task_effort = default_task_effort_by_runtime.get(default_task_runtime, "")
    default_repository = (
        str(settings.workflow.github_repository or "").strip()
        or DEFAULT_REPOSITORY
    )
    default_publish_mode = (
        str(settings.workflow.default_publish_mode or "").strip().lower() or "pr"
    )
    repository_options = _build_repository_options(
        include_credential_discovery=_is_create_page_path(initial_path)
    )

    system_metadata = _build_dashboard_system_metadata()
    jira_runtime_config = _build_jira_runtime_config()
    jira_sources = (
        {"jira": jira_runtime_config["sources"]} if jira_runtime_config else {}
    )
    jira_system = (
        {"jiraIntegration": jira_runtime_config["system"]}
        if jira_runtime_config
        else {}
    )

    return {
        "initialPath": initial_path,
        "pollIntervalsMs": {
            "list": _POLL_INTERVALS_MS["list"],
            "detail": _POLL_INTERVALS_MS["detail"],
            "events": _POLL_INTERVALS_MS["events"],
        },
        "statusMaps": status_maps(),
        "sources": {
            "proposals": {
                "list": "/api/proposals",
                "detail": "/api/proposals/{id}",
                "promote": "/api/proposals/{id}/promote",
                "dismiss": "/api/proposals/{id}/dismiss",
                "priority": "/api/proposals/{id}/priority",
            },
            "schedules": {
                "list": "/api/recurring-tasks?scope=personal",
                "create": "/api/recurring-tasks",
                "detail": "/api/recurring-tasks/{id}",
                "update": "/api/recurring-tasks/{id}",
                "runNow": "/api/recurring-tasks/{id}/run",
                "runs": "/api/recurring-tasks/{id}/runs?limit=200",
            },
            "temporal": {
                "list": temporal_dashboard.list_endpoint,
                "create": temporal_dashboard.create_endpoint,
                "detail": temporal_dashboard.detail_endpoint,
                "steps": temporal_dashboard.steps_endpoint,
                "update": temporal_dashboard.update_endpoint,
                "manifestStatus": "/api/executions/{workflowId}/manifest-status",
                "manifestNodes": "/api/executions/{workflowId}/manifest-nodes",
                "signal": temporal_dashboard.signal_endpoint,
                "cancel": temporal_dashboard.cancel_endpoint,
                "artifacts": temporal_dashboard.artifacts_endpoint,
                "artifactCreate": temporal_dashboard.artifact_create_endpoint,
                "artifactMetadata": temporal_dashboard.artifact_metadata_endpoint,
                "artifactPresignDownload": temporal_dashboard.artifact_presign_download_endpoint,
                "artifactDownload": temporal_dashboard.artifact_download_endpoint,
            },
            "taskRuns": {
                "observabilitySummary": "/api/task-runs/{taskRunId}/observability-summary",
                "observabilityEvents": "/api/task-runs/{taskRunId}/observability/events",
                "logsStream": "/api/task-runs/{taskRunId}/logs/stream",
                "logsStdout": "/api/task-runs/{taskRunId}/logs/stdout",
                "logsStderr": "/api/task-runs/{taskRunId}/logs/stderr",
                "logsMerged": "/api/task-runs/{taskRunId}/logs/merged",
                "diagnostics": "/api/task-runs/{taskRunId}/diagnostics",
                "artifactSession": "/api/task-runs/{taskRunId}/artifact-sessions/{sessionId}",
                "artifactSessionControl": "/api/task-runs/{taskRunId}/artifact-sessions/{sessionId}/control",
            },
            **jira_sources,
            "github": {
                "branches": "/api/github/branches?repository={repository}",
            },

        },
        "features": {
            "temporalDashboard": {
                "enabled": bool(temporal_dashboard.enabled),
                "listEnabled": bool(temporal_dashboard.list_enabled),
                "detailEnabled": bool(temporal_dashboard.detail_enabled),
                "actionsEnabled": bool(temporal_dashboard.actions_enabled),
                "submitEnabled": bool(temporal_dashboard.submit_enabled),
                "temporalTaskEditing": bool(
                    temporal_dashboard.temporal_task_editing_enabled
                ),
                "debugFieldsEnabled": bool(temporal_dashboard.debug_fields_enabled),
            },
            **build_live_logs_feature_config(),
        },
        "system": {
            **system_metadata,
            "defaultRepository": default_repository,
            "repositoryOptions": repository_options,
            "defaultTaskRuntime": default_task_runtime,
            "defaultTaskModel": default_task_model,
            "defaultTaskEffort": default_task_effort,
            "defaultTaskModelByRuntime": default_task_model_by_runtime,
            "defaultTaskEffortByRuntime": default_task_effort_by_runtime,
            "defaultPublishMode": default_publish_mode,
            # Keep task proposals opt-in from the submit form so Temporal
            # remains the default execution substrate for new runs.
            "defaultProposeTasks": False,
            "workerRuntimeEnv": "MOONMIND_WORKER_RUNTIME",
            "supportedTaskRuntimes": supported_task_runtimes,
            "supportedWorkerRuntimes": list(_SUPPORTED_WORKER_RUNTIMES),
            "taskTemplateCatalog": {
                "enabled": bool(settings.feature_flags.task_template_catalog_enabled),
                "templateSaveEnabled": bool(
                    settings.feature_flags.task_template_catalog_enabled
                ),
                "list": "/api/task-step-templates",
                "detail": "/api/task-step-templates/{slug}",
                "expand": "/api/task-step-templates/{slug}:expand",
                "saveFromTask": "/api/task-step-templates/save-from-task",
            },
            "providerProfiles": {
                "list": "/api/v1/provider-profiles",
                "create": "/api/v1/provider-profiles",
                "detail": "/api/v1/provider-profiles/{profileId}",
                "update": "/api/v1/provider-profiles/{profileId}",
                "delete": "/api/v1/provider-profiles/{profileId}",
            },
            "attachmentPolicy": {
                "enabled": bool(settings.workflow.agent_job_attachment_enabled),
                **_build_default_attachment_policy(
                    {
                        "agent_job_attachment_max_count": settings.workflow.agent_job_attachment_max_count,
                        "agent_job_attachment_max_bytes": settings.workflow.agent_job_attachment_max_bytes,
                        "agent_job_attachment_total_bytes": settings.workflow.agent_job_attachment_total_bytes,
                        "agent_job_attachment_allowed_content_types": settings.workflow.agent_job_attachment_allowed_content_types,
                    }
                ),
            },
            **jira_system,
        },
    }

__all__ = [
    "build_live_logs_feature_config",
    "build_repository_branch_options",
    "build_runtime_config",
    "normalize_status",
    "BranchOption",
    "RepositoryOption",
    "status_maps",
]
