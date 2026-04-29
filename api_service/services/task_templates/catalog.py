"""Service helpers for listing and expanding task step templates."""

from __future__ import annotations

import hashlib
import logging
import os
import re
import socket
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import yaml
from jinja2.sandbox import SandboxedEnvironment
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api_service.db.models import (
    TaskStepTemplate,
    TaskStepTemplateFavorite,
    TaskStepTemplateRecent,
    TaskStepTemplateVersion,
    TaskTemplateReleaseStatus,
    TaskTemplateScopeType,
)
from moonmind.config.settings import settings

_FORBIDDEN_STEP_KEYS = frozenset(
    {
        "bash",
        "runtime",
        "targetRuntime",
        "target_runtime",
        "model",
        "effort",
        "repository",
        "repo",
        "git",
        "publish",
        "container",
        "command",
        "cmd",
        "script",
        "shell",
    }
)
_SUPPORTED_INPUT_TYPES = frozenset(
    {
        "text",
        "textarea",
        "markdown",
        "enum",
        "boolean",
        "user",
        "team",
        "repo_path",
        "jira_board",
    }
)
_JIRA_BREAKDOWN_SLUG = "jira-breakdown"
_JIRA_BREAKDOWN_ORCHESTRATE_SLUG = "jira-breakdown-orchestrate"
_JIRA_BREAKDOWN_PROJECT_DEFAULT_SLUGS = frozenset(
    {_JIRA_BREAKDOWN_SLUG, _JIRA_BREAKDOWN_ORCHESTRATE_SLUG}
)
_JIRA_BREAKDOWN_PROJECT_INPUT = "jira_project_key"
_SLUG_PATTERN = re.compile(r"[^a-z0-9-]+")
_UNRESOLVED_PLACEHOLDER_PATTERN = re.compile(r"{{\s*[^}]+\s*}}")
_NATIVE_BOOLEAN_TEMPLATE_PATTERN = re.compile(r"^\{\{.*\}\}$", re.DOTALL)
_STEP_RESERVED_KEYS = frozenset(
    {
        "id",
        "kind",
        "type",
        "title",
        "slug",
        "version",
        "alias",
        "scope",
        "inputMapping",
        "input_mapping",
        "instructions",
        "tool",
        "skill",
        "skills",
        "annotations",
    }
)
_INCLUDE_STEP_KEYS = frozenset(
    {
        "kind",
        "slug",
        "version",
        "alias",
        "scope",
        "inputMapping",
        "input_mapping",
        "annotations",
    }
)
_STEP_KIND = "step"
_INCLUDE_KIND = "include"
_STEP_TYPE_TOOL = "tool"
_STEP_TYPE_SKILL = "skill"
_SKILL_METADATA_KEYS = frozenset(
    {"context", "permissions", "autonomy", "runtime", "allowedTools"}
)
_TOOL_METADATA_KEYS = frozenset(
    {
        "requiredAuthorization",
        "requiredCapabilities",
        "sideEffectPolicy",
        "retryPolicy",
        "execution",
        "validation",
    }
)
_COMMAND_TOOL_MARKERS = ("command", "shell", "bash")
logger = logging.getLogger(__name__)

class _StatsdEmitter:
    """Best-effort StatsD counter emitter for template catalog activity."""

    def __init__(self) -> None:
        host = (
            os.getenv("TASK_TEMPLATE_METRICS_HOST")
            or os.getenv("WORKFLOW_METRICS_HOST")
            or os.getenv("STATSD_HOST")
            or ""
        ).strip()
        port_raw = (
            os.getenv("TASK_TEMPLATE_METRICS_PORT")
            or os.getenv("WORKFLOW_METRICS_PORT")
            or os.getenv("STATSD_PORT")
            or "8125"
        ).strip()
        prefix = (
            os.getenv("TASK_TEMPLATE_METRICS_PREFIX") or "moonmind.task_templates"
        ).strip()
        self._prefix = prefix.rstrip(".")
        self._address: tuple[str, int] | None = None
        self._socket: socket.socket | None = None
        self._lock = threading.Lock()
        self._enabled = False
        self._disabled_until: float | None = None
        self._backoff_seconds = 5.0
        if host:
            try:
                self._address = (host, int(port_raw))
                self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                self._enabled = True
            except Exception:
                logger.warning(
                    "Task template metrics emitter disabled due to init failure."
                )
                self._enabled = False

    @property
    def enabled(self) -> bool:
        if not self._enabled or self._socket is None or self._address is None:
            return False
        if self._disabled_until is not None and time.monotonic() < self._disabled_until:
            return False
        return True

    def increment(self, metric: str, value: int = 1) -> None:
        if not self.enabled:
            return
        payload = f"{self._prefix}.{metric}:{int(value)}|c".encode("utf-8")
        with self._lock:
            if not self.enabled:
                return
            try:
                assert self._socket is not None and self._address is not None
                self._socket.sendto(payload, self._address)
                self._backoff_seconds = 5.0
            except OSError:
                self._disabled_until = time.monotonic() + self._backoff_seconds
                self._backoff_seconds = min(self._backoff_seconds * 2, 60.0)

_METRICS = _StatsdEmitter()

class TaskTemplateError(RuntimeError):
    """Base error for task template catalog operations."""

class TaskTemplateNotFoundError(TaskTemplateError):
    """Raised when a template or version is missing."""

class TaskTemplateValidationError(TaskTemplateError):
    """Raised when template payloads fail validation."""

class TaskTemplateConflictError(TaskTemplateError):
    """Raised when uniqueness constraints are violated."""

@dataclass(slots=True)
class ExpandOptions:
    """Options provided when expanding template steps."""

    should_enforce_step_limit: bool = True

def _normalize_slug(value: str) -> str:
    normalized = _SLUG_PATTERN.sub("-", str(value or "").strip().lower()).strip("-")
    if not normalized:
        raise TaskTemplateValidationError("Template slug is required.")
    if len(normalized) > 128:
        raise TaskTemplateValidationError("Template slug exceeds max length (128).")
    return normalized

def _normalize_scope(scope: str) -> TaskTemplateScopeType:
    raw = str(scope or "").strip().lower()
    if raw not in {
        TaskTemplateScopeType.GLOBAL.value,
        TaskTemplateScopeType.PERSONAL.value,
    }:
        raise TaskTemplateValidationError(
            "scope must be one of: global, personal"
        )
    return TaskTemplateScopeType(raw)

def _normalize_scope_ref(
    scope: TaskTemplateScopeType, scope_ref: str | None
) -> str | None:
    cleaned = str(scope_ref or "").strip() or None
    if scope is TaskTemplateScopeType.GLOBAL:
        return None
    if cleaned is None:
        raise TaskTemplateValidationError(
            "scopeRef is required for personal scopes."
        )
    return cleaned

def _normalize_tag_list(values: list[Any] | None) -> list[str]:
    if values is None:
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in values:
        tag = str(raw or "").strip().lower()
        if not tag or tag in seen:
            continue
        seen.add(tag)
        normalized.append(tag)
    return normalized

def _normalize_capabilities(values: list[Any] | None) -> list[str]:
    if values is None:
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in values:
        capability = str(raw or "").strip().lower()
        if not capability or capability in seen:
            continue
        seen.add(capability)
        normalized.append(capability)
    return normalized

def _extract_step_capabilities(step: dict[str, Any]) -> list[str]:
    skill = step.get("skill")
    if not isinstance(skill, dict):
        return []
    caps = skill.get("requiredCapabilities")
    if not isinstance(caps, list):
        return []
    return _normalize_capabilities(caps)

def _normalize_step_type(raw_step: Mapping[str, Any], *, index: int) -> str:
    raw_type = raw_step.get("type")
    if raw_type is None or str(raw_type).strip() == "":
        return _STEP_TYPE_SKILL
    step_type = str(raw_type).strip().lower()
    if step_type not in {_STEP_TYPE_TOOL, _STEP_TYPE_SKILL}:
        raise TaskTemplateValidationError(
            f"Step {index} type must be one of: tool, skill."
        )
    return step_type

def _normalize_tool_payload(raw_tool: Any, *, index: int) -> dict[str, Any]:
    if not isinstance(raw_tool, Mapping):
        raise TaskTemplateValidationError(
            f"Step {index} Tool step requires a tool payload object."
        )
    tool_id = str(raw_tool.get("id") or raw_tool.get("name") or "").strip()
    if not tool_id:
        raise TaskTemplateValidationError(
            f"Step {index} tool.id or tool.name is required."
        )
    inputs = raw_tool.get("inputs")
    if inputs is None:
        inputs = raw_tool.get("args")
    if inputs is None:
        inputs = {}
    if not isinstance(inputs, Mapping):
        raise TaskTemplateValidationError(
            f"Step {index} tool.inputs must be an object when provided."
        )
    tool_payload: dict[str, Any] = {"id": tool_id, "inputs": dict(inputs)}
    version = str(raw_tool.get("version") or "").strip()
    if version:
        tool_payload["version"] = version
    caps = raw_tool.get("requiredCapabilities")
    if caps is not None:
        if not isinstance(caps, list):
            raise TaskTemplateValidationError(
                f"Step {index} tool.requiredCapabilities must be a list."
            )
        normalized_caps = _normalize_capabilities(caps)
        if normalized_caps:
            tool_payload["requiredCapabilities"] = normalized_caps
    for key in _TOOL_METADATA_KEYS - {"requiredCapabilities"}:
        value = raw_tool.get(key)
        if value is not None:
            tool_payload[key] = value
    if any(marker in tool_id.lower() for marker in _COMMAND_TOOL_MARKERS):
        if not tool_payload["inputs"] or not any(
            key in tool_payload for key in ("sideEffectPolicy", "validation")
        ):
            raise TaskTemplateValidationError(
                f"Step {index} command-like Tool steps require bounded inputs and policy metadata."
            )
    return tool_payload

def _normalize_skill_payload(raw_skill: Any, *, index: int) -> dict[str, Any]:
    if raw_skill is None:
        return {"id": "auto", "args": {}}
    if not isinstance(raw_skill, Mapping):
        raise TaskTemplateValidationError(
            f"Step {index} skill must be an object when provided."
        )
    skill_id = str(raw_skill.get("id") or "auto").strip() or "auto"
    skill_args = raw_skill.get("args")
    if skill_args is None:
        skill_args = {}
    if not isinstance(skill_args, Mapping):
        raise TaskTemplateValidationError(
            f"Step {index} skill.args must be an object when provided."
        )
    skill_payload: dict[str, Any] = {"id": skill_id, "args": dict(skill_args)}
    caps = raw_skill.get("requiredCapabilities")
    if caps is not None:
        if not isinstance(caps, list):
            raise TaskTemplateValidationError(
                f"Step {index} skill.requiredCapabilities must be a list."
            )
        normalized_caps = _normalize_capabilities(caps)
        if normalized_caps:
            skill_payload["requiredCapabilities"] = normalized_caps
    for key in _SKILL_METADATA_KEYS:
        value = raw_skill.get(key)
        if value is not None:
            if not isinstance(value, Mapping) and key in {
                "context",
                "permissions",
                "autonomy",
                "runtime",
            }:
                raise TaskTemplateValidationError(
                    f"Step {index} skill.{key} must be an object when provided."
                )
            skill_payload[key] = dict(value) if isinstance(value, Mapping) else value
    return skill_payload

def _slugify_from_title(title: str) -> str:
    return _normalize_slug(title)

def _hash_from_inputs(values: dict[str, Any]) -> str:
    normalized = repr(sorted(values.items())).encode("utf-8")
    return hashlib.sha1(normalized).hexdigest()[:8]

def _build_step_id(
    *, slug: str, version: str, index: int, inputs: dict[str, Any]
) -> str:
    return f"tpl:{slug}:{version}:{index:02d}:{_hash_from_inputs(inputs)}"

def _template_path_label(
    *, slug: str, version: str, alias: str | None = None
) -> str:
    label = f"{slug}@{version}"
    if alias:
        return f"{alias}:{label}"
    return label

def _format_include_path(path: list[str]) -> str:
    return " -> ".join(path)

def _composition_capabilities(node: dict[str, Any]) -> list[str]:
    capabilities = list(node.get("requiredCapabilities") or [])
    for child in node.get("includes") or []:
        if isinstance(child, dict):
            capabilities.extend(_composition_capabilities(child))
    return _normalize_capabilities(capabilities)

def _render_value(
    env: SandboxedEnvironment,
    value: Any,
    *,
    variables: dict[str, Any],
) -> Any:
    if isinstance(value, str):
        rendered = env.from_string(value).render(**variables)
        stripped = rendered.strip()
        if _NATIVE_BOOLEAN_TEMPLATE_PATTERN.match(value.strip()):
            lowered = stripped.lower()
            if lowered in {"true", "false"}:
                return lowered == "true"
        return stripped
    if isinstance(value, list):
        return [_render_value(env, item, variables=variables) for item in value]
    if isinstance(value, dict):
        return {
            str(key): _render_value(env, item, variables=variables)
            for key, item in value.items()
        }
    return value

def _single_allowed_jira_project_key() -> str | None:
    projects = settings.atlassian.jira.jira_allowed_projects
    if not projects:
        return None
    allowed = [project.strip() for project in projects.split(",") if project.strip()]
    if len(allowed) != 1:
        return None
    return allowed[0]

def _effective_inputs_schema(
    *,
    slug: str,
    inputs_schema: list[dict[str, Any]],
    context: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Apply runtime-derived defaults without mutating stored template versions."""

    if slug not in _JIRA_BREAKDOWN_PROJECT_DEFAULT_SLUGS:
        return inputs_schema

    repository = _repository_from_context(context)
    project_key = _jira_project_default_for_context(repository)
    repository_default = repository if slug == _JIRA_BREAKDOWN_ORCHESTRATE_SLUG else None
    effective_schema = [dict(definition) for definition in inputs_schema]
    default_configured_repository = str(
        settings.workflow.github_repository or ""
    ).strip()
    for definition in effective_schema:
        name = definition.get("name")
        if name == _JIRA_BREAKDOWN_PROJECT_INPUT:
            if project_key:
                definition["default"] = project_key
            elif str(definition.get("default") or "").strip() == "TOOL":
                definition["default"] = None
        elif name == "repository" and repository_default:
            definition["default"] = repository_default
        elif name == "repository" and slug == _JIRA_BREAKDOWN_ORCHESTRATE_SLUG:
            default_repository = str(definition.get("default") or "").strip()
            if default_repository and default_repository == default_configured_repository:
                definition["default"] = None
    return effective_schema

def _repository_from_context(context: Mapping[str, Any] | None) -> str | None:
    if not isinstance(context, Mapping):
        return None
    for key in ("repository", "repo"):
        value = context.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None

def _jira_project_default_for_repository(repository: str | None) -> str | None:
    if not repository:
        return None
    raw = settings.atlassian.jira.jira_project_defaults_by_repository
    if not raw:
        return None
    normalized_repository = repository.strip().lower()
    allowed_projects = {
        project.strip().upper()
        for project in str(settings.atlassian.jira.jira_allowed_projects or "").split(",")
        if project.strip()
    }
    for item in raw.split(","):
        candidate = item.strip()
        if not candidate or "=" not in candidate:
            continue
        mapped_repository, mapped_project = candidate.split("=", 1)
        project_key = mapped_project.strip().upper()
        if mapped_repository.strip().lower() != normalized_repository:
            continue
        if allowed_projects and project_key not in allowed_projects:
            raise TaskTemplateValidationError(
                f"Configured Jira project default {project_key!r} for repository "
                f"{repository!r} is not in ATLASSIAN_JIRA_ALLOWED_PROJECTS."
            )
        return project_key
    return None

def _jira_project_default_for_context(repository: str | None) -> str | None:
    return _jira_project_default_for_repository(
        repository
    ) or _single_allowed_jira_project_key()


def _apply_contextual_input_overrides(
    *,
    slug: str,
    inputs_schema: list[dict[str, Any]],
    submitted: dict[str, Any],
    context: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if slug not in _JIRA_BREAKDOWN_PROJECT_DEFAULT_SLUGS:
        return submitted

    repository = _repository_from_context(context)
    project_key = _jira_project_default_for_context(repository)
    if not repository and not project_key:
        return submitted

    adjusted = dict(submitted)
    schema_defaults = _input_schema_defaults_by_name(inputs_schema)
    if slug == _JIRA_BREAKDOWN_ORCHESTRATE_SLUG:
        submitted_repository = str(adjusted.get("repository") or "").strip()
        schema_repository = schema_defaults.get("repository", "")
        if (
            repository
            and (not submitted_repository or submitted_repository == schema_repository)
        ):
            adjusted["repository"] = repository

    if project_key:
        submitted_project = str(adjusted.get(_JIRA_BREAKDOWN_PROJECT_INPUT) or "").strip()
        schema_project = schema_defaults.get(_JIRA_BREAKDOWN_PROJECT_INPUT, "")
        if (
            not submitted_project
            or submitted_project == schema_project
            or submitted_project == "TOOL"
        ):
            adjusted[_JIRA_BREAKDOWN_PROJECT_INPUT] = project_key
    return adjusted

def _input_schema_defaults_by_name(inputs_schema: list[dict[str, Any]]) -> dict[str, str]:
    return {
        str(definition.get("name") or "").strip(): str(
            definition.get("default") or ""
        ).strip()
        for definition in inputs_schema
        if str(definition.get("name") or "").strip()
    }


def _serialize_template(
    *,
    template: TaskStepTemplate,
    version: TaskStepTemplateVersion,
    is_favorite: bool,
    recent_applied_at: datetime | None,
) -> dict[str, Any]:
    return {
        "slug": template.slug,
        "scope": template.scope_type.value,
        "scopeRef": template.scope_ref,
        "title": template.title,
        "description": template.description,
        "tags": list(template.tags or []),
        "version": version.version,
        "latestVersion": (
            template.latest_version.version
            if template.latest_version
            else version.version
        ),
        "inputs": _effective_inputs_schema(
            slug=template.slug,
            inputs_schema=version.inputs_schema or [],
        ),
        "steps": list(version.steps or []),
        "annotations": dict(version.annotations or {}),
        "requiredCapabilities": _normalize_capabilities(
            list(template.required_capabilities or [])
            + list(version.required_capabilities or [])
        ),
        "releaseStatus": version.release_status.value,
        "reviewedBy": str(version.reviewed_by) if version.reviewed_by else None,
        "reviewedAt": version.reviewed_at.isoformat() if version.reviewed_at else None,
        "isFavorite": is_favorite,
        "recentAppliedAt": (
            recent_applied_at.astimezone(UTC).isoformat() if recent_applied_at else None
        ),
    }

def load_seed_template_definitions(seed_dir: Path) -> list[dict[str, Any]]:
    """Load seed template definitions from YAML files."""

    if not seed_dir.exists():
        return []
    loaded: list[dict[str, Any]] = []
    for yaml_path in sorted(seed_dir.glob("*.yaml")):
        document = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
        if not isinstance(document, dict):
            continue
        document["seedSource"] = str(yaml_path)
        loaded.append(document)
    return loaded

@dataclass(slots=True)
class SeedSyncResult:
    """Summary of a seed synchronization run."""

    created: int = 0
    updated: int = 0

class TaskTemplateCatalogService:
    """Catalog service for task step templates."""

    def __init__(self, session: AsyncSession):
        self._session = session
        self._template_env = SandboxedEnvironment(autoescape=False)

    async def _get_template_for_scope(
        self,
        *,
        slug: str,
        scope: TaskTemplateScopeType,
        scope_ref: str | None,
        include_inactive: bool = False,
    ) -> TaskStepTemplate:
        stmt = (
            select(TaskStepTemplate)
            .where(
                TaskStepTemplate.slug == slug,
                TaskStepTemplate.scope_type == scope,
                TaskStepTemplate.scope_ref == scope_ref,
            )
            .options(
                selectinload(TaskStepTemplate.latest_version),
                selectinload(TaskStepTemplate.versions),
            )
            .limit(1)
        )
        if not include_inactive:
            stmt = stmt.where(TaskStepTemplate.is_active.is_(True))
        result = await self._session.execute(stmt)
        template = result.scalar_one_or_none()
        if template is None:
            raise TaskTemplateNotFoundError("Template not found.")
        return template

    async def list_templates(
        self,
        *,
        scope: str | None = None,
        scope_ref: str | None = None,
        tag: str | None = None,
        search: str | None = None,
        favorites_only: bool = False,
        user_id: UUID | None = None,
        include_inactive: bool = False,
    ) -> list[dict[str, Any]]:
        stmt = select(TaskStepTemplate).options(
            selectinload(TaskStepTemplate.latest_version)
        )
        if not include_inactive:
            stmt = stmt.where(TaskStepTemplate.is_active.is_(True))
        if scope is not None:
            scope_type = _normalize_scope(scope)
            stmt = stmt.where(TaskStepTemplate.scope_type == scope_type)
            if scope_type is not TaskTemplateScopeType.GLOBAL:
                stmt = stmt.where(
                    TaskStepTemplate.scope_ref
                    == _normalize_scope_ref(scope_type, scope_ref)
                )

        template_rows = (await self._session.execute(stmt)).scalars().all()
        lowered_tag = str(tag or "").strip().lower()
        lowered_search = str(search or "").strip().lower()

        favorites_map: dict[UUID, bool] = {}
        recents_map: dict[UUID, datetime] = {}
        if user_id is not None:
            favorite_rows = (
                (
                    await self._session.execute(
                        select(TaskStepTemplateFavorite.template_id).where(
                            TaskStepTemplateFavorite.user_id == user_id
                        )
                    )
                )
                .scalars()
                .all()
            )
            favorites_map = {template_id: True for template_id in favorite_rows}

            recent_rows = (
                await self._session.execute(
                    select(
                        TaskStepTemplateVersion.template_id,
                        TaskStepTemplateRecent.applied_at,
                    )
                    .join(
                        TaskStepTemplateVersion,
                        TaskStepTemplateVersion.id
                        == TaskStepTemplateRecent.template_version_id,
                    )
                    .where(TaskStepTemplateRecent.user_id == user_id)
                    .order_by(TaskStepTemplateRecent.applied_at.desc())
                )
            ).all()
            for template_id, applied_at in recent_rows:
                if template_id not in recents_map:
                    recents_map[template_id] = applied_at

        serialized: list[dict[str, Any]] = []
        for template in template_rows:
            version = template.latest_version
            if version is None and template.versions:
                version = template.versions[-1]
            if version is None:
                continue
            if lowered_tag and lowered_tag not in {
                str(item).lower() for item in template.tags or []
            }:
                continue
            if lowered_search:
                haystack = " ".join(
                    [
                        template.slug,
                        template.title,
                        template.description,
                        " ".join(str(item) for item in (template.tags or [])),
                    ]
                ).lower()
                if lowered_search not in haystack:
                    continue
            is_favorite = bool(favorites_map.get(template.id, False))
            if favorites_only and not is_favorite:
                continue
            serialized.append(
                _serialize_template(
                    template=template,
                    version=version,
                    is_favorite=is_favorite,
                    recent_applied_at=recents_map.get(template.id),
                )
            )
        serialized.sort(
            key=lambda item: (
                not item["isFavorite"],
                item["recentAppliedAt"] is None,
                item["recentAppliedAt"] or "",
                item["title"].lower(),
            )
        )
        logger.info(
            "task_template_catalog.list",
            extra={
                "scope": scope,
                "favorites_only": favorites_only,
                "results": len(serialized),
            },
        )
        _METRICS.increment("list")
        return serialized

    async def get_template(
        self,
        *,
        slug: str,
        scope: str,
        scope_ref: str | None,
        version: str | None = None,
        user_id: UUID | None = None,
    ) -> dict[str, Any]:
        scope_type = _normalize_scope(scope)
        normalized_scope_ref = _normalize_scope_ref(scope_type, scope_ref)
        template = await self._get_template_for_scope(
            slug=_normalize_slug(slug),
            scope=scope_type,
            scope_ref=normalized_scope_ref,
        )
        version_model = template.latest_version
        if version is not None:
            for candidate in template.versions:
                if candidate.version == version:
                    version_model = candidate
                    break
        if version_model is None:
            raise TaskTemplateNotFoundError("Template version not found.")

        is_favorite = False
        recent_applied_at = None
        if user_id is not None:
            favorite = await self._session.execute(
                select(TaskStepTemplateFavorite.id).where(
                    TaskStepTemplateFavorite.user_id == user_id,
                    TaskStepTemplateFavorite.template_id == template.id,
                )
            )
            is_favorite = favorite.scalar_one_or_none() is not None
            recent = await self._session.execute(
                select(TaskStepTemplateRecent.applied_at)
                .where(
                    TaskStepTemplateRecent.user_id == user_id,
                    TaskStepTemplateRecent.template_version_id == version_model.id,
                )
                .order_by(TaskStepTemplateRecent.applied_at.desc())
                .limit(1)
            )
            recent_applied_at = recent.scalar_one_or_none()

        return _serialize_template(
            template=template,
            version=version_model,
            is_favorite=is_favorite,
            recent_applied_at=recent_applied_at,
        )

    async def create_template(
        self,
        *,
        slug: str,
        title: str,
        description: str,
        scope: str,
        scope_ref: str | None,
        tags: list[Any] | None,
        inputs_schema: list[dict[str, Any]],
        steps: list[dict[str, Any]],
        annotations: dict[str, Any] | None = None,
        required_capabilities: list[Any] | None = None,
        created_by: UUID | None = None,
        version: str = "1.0.0",
        release_status: TaskTemplateReleaseStatus = TaskTemplateReleaseStatus.DRAFT,
        seed_source: str | None = None,
        auto_commit: bool = True,
    ) -> dict[str, Any]:
        normalized_scope = _normalize_scope(scope)
        normalized_scope_ref = _normalize_scope_ref(normalized_scope, scope_ref)
        normalized_slug = _normalize_slug(slug)
        existing = await self._session.execute(
            select(TaskStepTemplate.id).where(
                TaskStepTemplate.slug == normalized_slug,
                TaskStepTemplate.scope_type == normalized_scope,
                TaskStepTemplate.scope_ref == normalized_scope_ref,
            )
        )
        if existing.scalar_one_or_none() is not None:
            raise TaskTemplateConflictError(
                "Template slug already exists for this scope."
            )

        normalized_title = str(title or "").strip()
        normalized_description = str(description or "").strip()
        if not normalized_title:
            raise TaskTemplateValidationError("title is required")
        if not normalized_description:
            raise TaskTemplateValidationError("description is required")

        validated_inputs = self._validate_inputs_schema(inputs_schema)
        validated_steps = self._validate_template_steps(steps)
        derived_capabilities = _normalize_capabilities(
            (required_capabilities or [])
            + [
                cap
                for step in validated_steps
                for cap in _extract_step_capabilities(step)
            ]
        )
        version_label = str(version or "").strip() or "1.0.0"
        template = TaskStepTemplate(
            id=uuid4(),
            slug=normalized_slug,
            scope_type=normalized_scope,
            scope_ref=normalized_scope_ref,
            title=normalized_title,
            description=normalized_description,
            tags=_normalize_tag_list(tags),
            required_capabilities=derived_capabilities,
            is_active=True,
            created_by=created_by,
        )
        version_model = TaskStepTemplateVersion(
            id=uuid4(),
            template=template,
            version=version_label,
            inputs_schema=validated_inputs,
            steps=validated_steps,
            annotations=dict(annotations or {}),
            required_capabilities=derived_capabilities,
            max_step_count=max(25, len(validated_steps)),
            release_status=release_status,
            seed_source=seed_source,
        )
        template.latest_version = version_model
        self._session.add(template)
        self._session.add(version_model)
        await self._session.flush()
        if auto_commit:
            await self._session.commit()
            logger.info(
                "task_template_catalog.create",
                extra={
                    "slug": normalized_slug,
                    "scope": normalized_scope.value,
                    "version": version_label,
                },
            )
            _METRICS.increment("create")
        return _serialize_template(
            template=template,
            version=version_model,
            is_favorite=False,
            recent_applied_at=None,
        )

    def _validate_inputs_schema(
        self, inputs_schema: list[dict[str, Any]] | None
    ) -> list[dict[str, Any]]:
        if inputs_schema is None:
            return []
        validated: list[dict[str, Any]] = []
        names: set[str] = set()
        for raw_input in inputs_schema:
            if not isinstance(raw_input, dict):
                raise TaskTemplateValidationError("Template inputs must be objects.")
            name = str(raw_input.get("name") or "").strip()
            label = str(raw_input.get("label") or "").strip()
            input_type = str(raw_input.get("type") or "text").strip().lower()
            if not name:
                raise TaskTemplateValidationError(
                    "Template inputs require non-empty names."
                )
            if name in names:
                raise TaskTemplateValidationError(
                    f"Duplicate template input '{name}' is not allowed."
                )
            if not label:
                raise TaskTemplateValidationError(
                    f"Template input '{name}' requires a non-empty label."
                )
            if input_type not in _SUPPORTED_INPUT_TYPES:
                supported = ", ".join(sorted(_SUPPORTED_INPUT_TYPES))
                raise TaskTemplateValidationError(
                    f"Template input '{name}' has unsupported type '{input_type}'. "
                    f"Supported: {supported}"
                )
            options = raw_input.get("options")
            if input_type == "enum":
                if not isinstance(options, list) or len(options) == 0:
                    raise TaskTemplateValidationError(
                        f"Enum input '{name}' requires a non-empty options list."
                    )
                normalized_options = [
                    str(item).strip() for item in options if str(item).strip()
                ]
                if not normalized_options:
                    raise TaskTemplateValidationError(
                        f"Enum input '{name}' requires at least one non-empty option."
                    )
            else:
                normalized_options = []
            placeholder = str(raw_input.get("placeholder") or "").strip()
            names.add(name)
            validated.append(
                {
                    "name": name,
                    "label": label,
                    "type": input_type,
                    "required": bool(raw_input.get("required", False)),
                    "default": raw_input.get("default"),
                    **({"options": normalized_options} if normalized_options else {}),
                    **({"placeholder": placeholder} if placeholder else {}),
                }
            )
        return validated

    def _validate_template_steps(
        self, steps: list[dict[str, Any]] | None
    ) -> list[dict[str, Any]]:
        if not steps:
            raise TaskTemplateValidationError("Template steps must not be empty.")
        validated: list[dict[str, Any]] = []
        include_aliases: set[str] = set()
        for index, raw_step in enumerate(steps, start=1):
            if not isinstance(raw_step, dict):
                raise TaskTemplateValidationError(
                    f"Step {index} must be an object with instructions and optional skill."
                )
            blocked = sorted(
                key for key in raw_step if str(key).strip() in _FORBIDDEN_STEP_KEYS
            )
            if blocked:
                raise TaskTemplateValidationError(
                    f"Step {index} uses forbidden keys: {', '.join(blocked)}."
                )
            kind = str(raw_step.get("kind") or _STEP_KIND).strip().lower()
            if kind not in {_STEP_KIND, _INCLUDE_KIND}:
                raise TaskTemplateValidationError(
                    f"Step {index} kind must be one of: {_STEP_KIND}, {_INCLUDE_KIND}."
                )
            if kind == _INCLUDE_KIND:
                unsupported = sorted(
                    key
                    for key in raw_step
                    if str(key).strip() not in _INCLUDE_STEP_KEYS
                )
                if unsupported:
                    raise TaskTemplateValidationError(
                        f"Step {index} include uses unsupported keys: "
                        f"{', '.join(unsupported)}."
                    )
                include_slug = _normalize_slug(str(raw_step.get("slug") or ""))
                include_version = str(raw_step.get("version") or "").strip()
                include_alias = _normalize_slug(str(raw_step.get("alias") or ""))
                if not include_version:
                    raise TaskTemplateValidationError(
                        f"Step {index} include requires a pinned version."
                    )
                if _UNRESOLVED_PLACEHOLDER_PATTERN.search(include_version):
                    raise TaskTemplateValidationError(
                        f"Step {index} include version must be a literal pinned version."
                    )
                if include_alias in include_aliases:
                    raise TaskTemplateValidationError(
                        f"Step {index} include alias '{include_alias}' is duplicated."
                    )
                include_aliases.add(include_alias)
                include_scope = raw_step.get("scope")
                include_payload: dict[str, Any] = {
                    "kind": _INCLUDE_KIND,
                    "slug": include_slug,
                    "version": include_version,
                    "alias": include_alias,
                }
                if include_scope is not None:
                    include_payload["scope"] = _normalize_scope(str(include_scope)).value
                input_mapping = raw_step.get("inputMapping", raw_step.get("input_mapping"))
                if input_mapping is not None:
                    if not isinstance(input_mapping, dict):
                        raise TaskTemplateValidationError(
                            f"Step {index} include inputMapping must be an object."
                        )
                    include_payload["inputMapping"] = dict(input_mapping)
                annotations = raw_step.get("annotations")
                if annotations is not None:
                    if not isinstance(annotations, dict):
                        raise TaskTemplateValidationError(
                            f"Step {index} annotations must be an object when provided."
                        )
                    include_payload["annotations"] = dict(annotations)
                validated.append(include_payload)
                continue
            instructions = str(raw_step.get("instructions") or "").strip()
            if not instructions:
                raise TaskTemplateValidationError(
                    f"Step {index} requires non-empty instructions."
                )
            step_type = _normalize_step_type(raw_step, index=index)
            step_payload: dict[str, Any] = {
                "type": step_type,
                "instructions": instructions,
            }
            title = str(raw_step.get("title") or "").strip()
            if title:
                step_payload["title"] = title
            if "slug" in raw_step and str(raw_step.get("slug") or "").strip():
                step_payload["slug"] = str(raw_step.get("slug")).strip()
            if step_type == _STEP_TYPE_TOOL:
                if raw_step.get("skill") is not None:
                    raise TaskTemplateValidationError(
                        f"Step {index} Tool step must not include a skill payload."
                    )
                step_payload["tool"] = _normalize_tool_payload(
                    raw_step.get("tool"), index=index
                )
            else:
                if raw_step.get("tool") is not None:
                    raise TaskTemplateValidationError(
                        f"Step {index} Skill step must not include a tool payload."
                    )
                step_payload["skill"] = _normalize_skill_payload(
                    raw_step.get("skill"), index=index
                )
            annotations = raw_step.get("annotations")
            if annotations is not None:
                if not isinstance(annotations, dict):
                    raise TaskTemplateValidationError(
                        f"Step {index} annotations must be an object when provided."
                    )
                step_payload["annotations"] = dict(annotations)
            step_payload.update(
                {
                    str(key).strip(): value
                    for key, value in raw_step.items()
                    if str(key).strip()
                    and str(key).strip() not in _STEP_RESERVED_KEYS
                }
            )
            validated.append(step_payload)
        return validated

    def _select_template_version(
        self, template: TaskStepTemplate, version: str
    ) -> TaskStepTemplateVersion:
        for candidate in template.versions:
            if candidate.version == version:
                return candidate
        raise TaskTemplateNotFoundError("Template version not found.")

    async def _expand_version_steps(
        self,
        *,
        template: TaskStepTemplate,
        version_model: TaskStepTemplateVersion,
        scope: TaskTemplateScopeType,
        scope_ref: str | None,
        variables: dict[str, Any],
        root_slug: str,
        root_version: str,
        root_inputs: dict[str, Any],
        root_max_step_count: int,
        enforce_limit: bool,
        path: list[str],
        visited: set[tuple[str, str, str]],
        resolved_steps: list[dict[str, Any]],
        alias: str | None = None,
    ) -> dict[str, Any]:
        node: dict[str, Any] = {
            "slug": template.slug,
            "version": version_model.version,
            "scope": scope.value,
            "path": list(path),
            "stepIds": [],
            "includes": [],
            "requiredCapabilities": _normalize_capabilities(
                list(template.required_capabilities or [])
                + list(version_model.required_capabilities or [])
            ),
        }
        if alias:
            node["alias"] = alias

        for source_index, source_step in enumerate(version_model.steps or [], start=1):
            rendered = _render_value(
                self._template_env, source_step, variables=variables
            )
            if not isinstance(rendered, dict):
                raise TaskTemplateValidationError(
                    f"Expanded step at {_format_include_path(path)} must be an object."
                )
            kind = str(rendered.get("kind") or _STEP_KIND).strip().lower()
            if kind == _INCLUDE_KIND:
                include_slug = _normalize_slug(str(rendered.get("slug") or ""))
                include_version = str(rendered.get("version") or "").strip()
                include_alias = _normalize_slug(str(rendered.get("alias") or ""))
                include_scope = _normalize_scope(str(rendered.get("scope") or scope.value))
                include_scope_ref = (
                    None
                    if include_scope is TaskTemplateScopeType.GLOBAL
                    else scope_ref
                )
                include_path = [
                    *path,
                    _template_path_label(
                        slug=include_slug,
                        version=include_version,
                        alias=include_alias,
                    ),
                ]
                if scope is TaskTemplateScopeType.GLOBAL and include_scope is TaskTemplateScopeType.PERSONAL:
                    raise TaskTemplateValidationError(
                        "Global presets cannot include personal presets at "
                        f"{_format_include_path(include_path)}."
                    )
                target_key = (include_scope.value, include_slug, include_version)
                if target_key in visited:
                    raise TaskTemplateValidationError(
                        "Preset include cycle detected at "
                        f"{_format_include_path(include_path)}."
                    )
                try:
                    child_template = await self._get_template_for_scope(
                        slug=include_slug,
                        scope=include_scope,
                        scope_ref=include_scope_ref,
                    )
                    child_version = self._select_template_version(
                        child_template, include_version
                    )
                except TaskTemplateError as exc:
                    raise TaskTemplateValidationError(
                        f"Preset include target unavailable at "
                        f"{_format_include_path(include_path)}: {exc}"
                    ) from exc
                if child_version.release_status is TaskTemplateReleaseStatus.INACTIVE:
                    raise TaskTemplateValidationError(
                        f"Preset include target is inactive at "
                        f"{_format_include_path(include_path)}."
                    )
                input_mapping = rendered.get("inputMapping") or {}
                if not isinstance(input_mapping, dict):
                    raise TaskTemplateValidationError(
                        f"Preset include inputMapping must be an object at "
                        f"{_format_include_path(include_path)}."
                    )
                try:
                    child_schema = _effective_inputs_schema(
                        slug=child_template.slug,
                        inputs_schema=child_version.inputs_schema or [],
                        context=variables.get("context"),
                    )
                    child_inputs = self._resolve_inputs(
                        schema=child_schema,
                        submitted=_apply_contextual_input_overrides(
                            slug=child_template.slug,
                            inputs_schema=child_schema,
                            submitted=dict(input_mapping),
                            context=variables.get("context"),
                        ),
                    )
                except TaskTemplateValidationError as exc:
                    raise TaskTemplateValidationError(
                        f"Preset include input mapping is incompatible at "
                        f"{_format_include_path(include_path)}: {exc}"
                    ) from exc
                child_variables = {
                    **variables,
                    "inputs": child_inputs,
                }
                child_node = await self._expand_version_steps(
                    template=child_template,
                    version_model=child_version,
                    scope=include_scope,
                    scope_ref=include_scope_ref,
                    variables=child_variables,
                    root_slug=root_slug,
                    root_version=root_version,
                    root_inputs=root_inputs,
                    root_max_step_count=root_max_step_count,
                    enforce_limit=enforce_limit,
                    path=include_path,
                    visited={*visited, target_key},
                    resolved_steps=resolved_steps,
                    alias=include_alias,
                )
                node["includes"].append(child_node)
                node["stepIds"].extend(child_node["stepIds"])
                continue

            if kind != _STEP_KIND:
                raise TaskTemplateValidationError(
                    f"Expanded step kind must be one of: {_STEP_KIND}, {_INCLUDE_KIND}."
                )
            blocked = sorted(
                key for key in rendered if str(key).strip() in _FORBIDDEN_STEP_KEYS
            )
            if blocked:
                raise TaskTemplateValidationError(
                    f"Expanded step uses forbidden keys at {_format_include_path(path)}: "
                    f"{', '.join(blocked)}."
                )
            instructions = str(rendered.get("instructions") or "").strip()
            if not instructions:
                raise TaskTemplateValidationError(
                    f"Expanded step instructions may not be empty at "
                    f"{_format_include_path(path)}."
                )
            if _UNRESOLVED_PLACEHOLDER_PATTERN.search(instructions):
                raise TaskTemplateValidationError(
                    f"Expanded instructions still contain unresolved template "
                    f"placeholders at {_format_include_path(path)}."
                )
            step_type = _normalize_step_type(rendered, index=source_index)
            next_index = len(resolved_steps) + 1
            if enforce_limit and next_index > root_max_step_count:
                raise TaskTemplateValidationError(
                    f"Template expansion exceeded max_step_count={root_max_step_count} "
                    f"at {_format_include_path(path)}."
                )
            step_payload: dict[str, Any] = {
                "id": _build_step_id(
                    slug=root_slug,
                    version=root_version,
                    index=next_index,
                    inputs=root_inputs,
                ),
                "type": step_type,
                "instructions": instructions,
                "presetProvenance": {
                    "root": {"slug": root_slug, "version": root_version},
                    "source": {
                        "slug": template.slug,
                        "version": version_model.version,
                        "scope": scope.value,
                        "stepIndex": source_index,
                    },
                    "path": list(path),
                },
            }
            if alias:
                step_payload["presetProvenance"]["alias"] = alias
            title = str(rendered.get("title") or "").strip()
            if title:
                step_payload["title"] = title
            if step_type == _STEP_TYPE_TOOL:
                if rendered.get("skill") is not None:
                    raise TaskTemplateValidationError(
                        f"Expanded Tool step must not include a skill payload at "
                        f"{_format_include_path(path)}."
                    )
                step_payload["tool"] = _normalize_tool_payload(
                    rendered.get("tool"), index=source_index
                )
            else:
                if rendered.get("tool") is not None:
                    raise TaskTemplateValidationError(
                        f"Expanded Skill step must not include a tool payload at "
                        f"{_format_include_path(path)}."
                    )
                step_payload["skill"] = _normalize_skill_payload(
                    rendered.get("skill"), index=source_index
                )
            step_payload.update(
                {
                    str(key).strip(): value
                    for key, value in rendered.items()
                    if str(key).strip()
                    and str(key).strip() not in _STEP_RESERVED_KEYS
                }
            )
            resolved_steps.append(step_payload)
            node["stepIds"].append(step_payload["id"])
        return node

    async def expand_template(
        self,
        *,
        slug: str,
        scope: str,
        scope_ref: str | None,
        version: str,
        inputs: dict[str, Any],
        context: dict[str, Any] | None = None,
        options: ExpandOptions | None = None,
        user_id: UUID | None = None,
    ) -> dict[str, Any]:
        normalized_scope = _normalize_scope(scope)
        normalized_scope_ref = _normalize_scope_ref(normalized_scope, scope_ref)
        template = await self._get_template_for_scope(
            slug=_normalize_slug(slug),
            scope=normalized_scope,
            scope_ref=normalized_scope_ref,
        )
        selected_version = self._select_template_version(template, version)

        effective_context = dict(context or {})
        if not _repository_from_context(effective_context):
            input_repository = (inputs or {}).get("repository") or (inputs or {}).get(
                "repo"
            )
            if isinstance(input_repository, str) and input_repository.strip():
                effective_context["repository"] = input_repository.strip()
                effective_context["repo"] = input_repository.strip()
        effective_schema = _effective_inputs_schema(
            slug=template.slug,
            inputs_schema=selected_version.inputs_schema or [],
            context=effective_context,
        )
        validated_inputs = self._resolve_inputs(
            schema=effective_schema,
            submitted=_apply_contextual_input_overrides(
                slug=template.slug,
                inputs_schema=effective_schema,
                submitted=dict(inputs or {}),
                context=effective_context,
            ),
        )
        variables = {
            "inputs": validated_inputs,
            "context": effective_context,
            "now": datetime.now(UTC).isoformat(),
            "iso_today": datetime.now(UTC).date().isoformat(),
        }

        resolved_steps: list[dict[str, Any]] = []
        warnings: list[str] = []
        enforce_limit = options.should_enforce_step_limit if options else True
        max_step_count = max(int(selected_version.max_step_count or 25), 1)
        root_path = [
            _template_path_label(slug=template.slug, version=selected_version.version)
        ]
        composition = await self._expand_version_steps(
            template=template,
            version_model=selected_version,
            scope=normalized_scope,
            scope_ref=normalized_scope_ref,
            variables=variables,
            root_slug=template.slug,
            root_version=selected_version.version,
            root_inputs=validated_inputs,
            root_max_step_count=max_step_count,
            enforce_limit=enforce_limit,
            path=root_path,
            visited={(normalized_scope.value, template.slug, selected_version.version)},
            resolved_steps=resolved_steps,
        )

        template_caps = _normalize_capabilities(
            list(template.required_capabilities or [])
            + list(selected_version.required_capabilities or [])
            + _composition_capabilities(composition)
            + [
                cap
                for step in resolved_steps
                for cap in _extract_step_capabilities(step)
            ]
        )
        if selected_version.release_status is TaskTemplateReleaseStatus.INACTIVE:
            warnings.append("Template version is marked inactive.")

        if user_id is not None:
            await self.record_recent(
                user_id=user_id,
                template_version_id=selected_version.id,
            )

        applied_at = datetime.now(UTC).isoformat()
        logger.info(
            "task_template_catalog.expand",
            extra={
                "slug": template.slug,
                "scope": normalized_scope.value,
                "version": selected_version.version,
                "steps": len(resolved_steps),
            },
        )
        _METRICS.increment("expand")
        return {
            "steps": resolved_steps,
            "composition": composition,
            "appliedTemplate": {
                "slug": template.slug,
                "version": selected_version.version,
                "inputs": validated_inputs,
                "stepIds": [step["id"] for step in resolved_steps],
                "appliedAt": applied_at,
            },
            "capabilities": template_caps,
            "warnings": warnings,
        }

    def _resolve_inputs(
        self,
        *,
        schema: list[dict[str, Any]],
        submitted: dict[str, Any],
    ) -> dict[str, Any]:
        resolved: dict[str, Any] = {}
        for definition in schema:
            name = str(definition.get("name") or "").strip()
            input_type = str(definition.get("type") or "text").strip().lower()
            required = bool(definition.get("required", False))
            default = definition.get("default")
            raw_value = submitted[name] if name in submitted else default
            if raw_value in (None, "") and required:
                raise TaskTemplateValidationError(
                    f"Missing required template input '{name}'."
                )
            if raw_value in (None, ""):
                resolved[name] = raw_value
                continue
            if input_type == "boolean":
                if isinstance(raw_value, bool):
                    resolved[name] = raw_value
                else:
                    lowered = str(raw_value).strip().lower()
                    if lowered in {"1", "true", "yes", "on"}:
                        resolved[name] = True
                    elif lowered in {"0", "false", "no", "off"}:
                        resolved[name] = False
                    else:
                        raise TaskTemplateValidationError(
                            f"Input '{name}' must be a boolean value."
                        )
                continue
            if input_type == "enum":
                options = [str(item).strip() for item in definition.get("options", [])]
                candidate = str(raw_value).strip()
                if candidate not in options:
                    raise TaskTemplateValidationError(
                        f"Input '{name}' must be one of: {', '.join(options)}."
                    )
                resolved[name] = candidate
                continue
            resolved[name] = str(raw_value).strip()
        return resolved

    async def set_favorite(
        self,
        *,
        user_id: UUID,
        slug: str,
        scope: str,
        scope_ref: str | None,
        auto_commit: bool = True,
    ) -> None:
        scope_type = _normalize_scope(scope)
        normalized_scope_ref = _normalize_scope_ref(scope_type, scope_ref)
        template = await self._get_template_for_scope(
            slug=_normalize_slug(slug),
            scope=scope_type,
            scope_ref=normalized_scope_ref,
        )
        existing = await self._session.execute(
            select(TaskStepTemplateFavorite.id).where(
                TaskStepTemplateFavorite.user_id == user_id,
                TaskStepTemplateFavorite.template_id == template.id,
            )
        )
        if existing.scalar_one_or_none() is None:
            self._session.add(
                TaskStepTemplateFavorite(
                    user_id=user_id,
                    template_id=template.id,
                )
            )
            if auto_commit:
                await self._session.commit()
            else:
                await self._session.flush()
            logger.info(
                "task_template_catalog.favorite",
                extra={
                    "slug": template.slug,
                    "scope": scope_type.value,
                },
            )
            _METRICS.increment("favorite")

    async def clear_favorite(
        self,
        *,
        user_id: UUID,
        slug: str,
        scope: str,
        scope_ref: str | None,
    ) -> None:
        scope_type = _normalize_scope(scope)
        normalized_scope_ref = _normalize_scope_ref(scope_type, scope_ref)
        template = await self._get_template_for_scope(
            slug=_normalize_slug(slug),
            scope=scope_type,
            scope_ref=normalized_scope_ref,
            include_inactive=True,
        )
        await self._session.execute(
            delete(TaskStepTemplateFavorite).where(
                TaskStepTemplateFavorite.user_id == user_id,
                TaskStepTemplateFavorite.template_id == template.id,
            )
        )
        await self._session.commit()
        logger.info(
            "task_template_catalog.unfavorite",
            extra={
                "slug": template.slug,
                "scope": scope_type.value,
            },
        )
        _METRICS.increment("unfavorite")

    async def record_recent(
        self,
        *,
        user_id: UUID,
        template_version_id: UUID,
        auto_commit: bool = True,
    ) -> None:
        dialect_name = self._session.bind.dialect.name if self._session.bind else ""
        if dialect_name == "postgresql":
            await self._session.execute(
                pg_insert(TaskStepTemplateRecent)
                .values(
                    user_id=user_id,
                    template_version_id=template_version_id,
                )
                .on_conflict_do_update(
                    index_elements=["user_id", "template_version_id"],
                    set_={"applied_at": datetime.now(UTC)},
                )
            )
        else:
            self._session.add(
                TaskStepTemplateRecent(
                    user_id=user_id,
                    template_version_id=template_version_id,
                )
            )
        await self._session.flush()
        keep_recent_ids = (
            select(TaskStepTemplateRecent.id)
            .where(TaskStepTemplateRecent.user_id == user_id)
            .order_by(
                TaskStepTemplateRecent.applied_at.desc(),
                TaskStepTemplateRecent.id.desc(),
            )
            .limit(5)
        )
        await self._session.execute(
            delete(TaskStepTemplateRecent).where(
                TaskStepTemplateRecent.user_id == user_id,
                TaskStepTemplateRecent.id.not_in(keep_recent_ids),
            )
        )
        if auto_commit:
            await self._session.commit()
        else:
            await self._session.flush()
        logger.info(
            "task_template_catalog.recent",
            extra={"template_version_id": str(template_version_id)},
        )
        _METRICS.increment("recent")

    async def soft_delete_template(
        self,
        *,
        slug: str,
        scope: str,
        scope_ref: str | None,
    ) -> None:
        scope_type = _normalize_scope(scope)
        normalized_scope_ref = _normalize_scope_ref(scope_type, scope_ref)
        template = await self._get_template_for_scope(
            slug=_normalize_slug(slug),
            scope=scope_type,
            scope_ref=normalized_scope_ref,
            include_inactive=True,
        )
        template.is_active = False
        await self._session.commit()
        logger.info(
            "task_template_catalog.delete",
            extra={
                "slug": template.slug,
                "scope": scope_type.value,
            },
        )
        _METRICS.increment("delete")

    async def deactivate_templates(
        self,
        *,
        slugs: Sequence[str],
        scope: str,
        scope_ref: str | None,
    ) -> int:
        scope_type = _normalize_scope(scope)
        normalized_scope_ref = _normalize_scope_ref(scope_type, scope_ref)
        normalized_slugs = [
            _normalize_slug(slug) for slug in slugs if str(slug or "").strip()
        ]
        if not normalized_slugs:
            return 0

        result = await self._session.execute(
            select(TaskStepTemplate).where(
                TaskStepTemplate.slug.in_(normalized_slugs),
                TaskStepTemplate.scope_type == scope_type,
                TaskStepTemplate.scope_ref == normalized_scope_ref,
                TaskStepTemplate.is_active.is_(True),
            )
        )
        templates = result.scalars().all()
        for template in templates:
            template.is_active = False

        if templates:
            await self._session.commit()
            logger.info(
                "task_template_catalog.deactivate",
                extra={
                    "slugs": [template.slug for template in templates],
                    "scope": scope_type.value,
                },
            )
            _METRICS.increment("delete", len(templates))
        return len(templates)

    async def set_release_status(
        self,
        *,
        slug: str,
        scope: str,
        scope_ref: str | None,
        version: str,
        release_status: TaskTemplateReleaseStatus,
        reviewer_id: UUID | None = None,
    ) -> dict[str, Any]:
        scope_type = _normalize_scope(scope)
        normalized_scope_ref = _normalize_scope_ref(scope_type, scope_ref)
        template = await self._get_template_for_scope(
            slug=_normalize_slug(slug),
            scope=scope_type,
            scope_ref=normalized_scope_ref,
            include_inactive=True,
        )
        target = None
        for candidate in template.versions:
            if candidate.version == version:
                target = candidate
                break
        if target is None:
            raise TaskTemplateNotFoundError("Template version not found.")
        target.release_status = release_status
        if reviewer_id is not None:
            target.reviewed_by = reviewer_id
            target.reviewed_at = datetime.now(UTC)
        if release_status is TaskTemplateReleaseStatus.ACTIVE:
            template.latest_version = target
        await self._session.commit()
        logger.info(
            "task_template_catalog.review",
            extra={
                "slug": template.slug,
                "scope": scope_type.value,
                "version": target.version,
                "release_status": release_status.value,
            },
        )
        _METRICS.increment("review")
        return _serialize_template(
            template=template,
            version=target,
            is_favorite=False,
            recent_applied_at=None,
        )

    async def import_seed_templates(
        self,
        *,
        seed_dir: Path,
    ) -> int:
        loaded = load_seed_template_definitions(seed_dir)

        created = 0
        for item in loaded:
            scope = str(item.get("scope", "global")).strip() or "global"
            version = str(item.get("version", "1.0.0")).strip() or "1.0.0"
            try:
                await self.create_template(
                    slug=str(
                        item.get("slug") or _slugify_from_title(item.get("title", ""))
                    ),
                    title=str(item.get("title") or "").strip(),
                    description=str(item.get("description") or "").strip()
                    or "Seed template.",
                    scope=scope,
                    scope_ref=item.get("scopeRef"),
                    tags=item.get("tags") or [],
                    inputs_schema=item.get("inputs") or [],
                    steps=item.get("steps") or [],
                    annotations=item.get("annotations") or {},
                    required_capabilities=item.get("requiredCapabilities") or [],
                    created_by=None,
                    version=version,
                    release_status=TaskTemplateReleaseStatus.ACTIVE,
                    seed_source=item.get("seedSource"),
                    auto_commit=False,
                )
                created += 1
            except TaskTemplateConflictError:
                pass
        if created > 0:
            await self._session.commit()
        return created

    async def sync_seed_templates(
        self,
        *,
        seed_dir: Path,
    ) -> SeedSyncResult:
        """Create or refresh seeded templates from YAML definitions."""

        loaded = load_seed_template_definitions(seed_dir)
        result = SeedSyncResult()

        for item in loaded:
            scope = _normalize_scope(str(item.get("scope", "global")).strip() or "global")
            scope_ref = _normalize_scope_ref(scope, item.get("scopeRef"))
            slug = str(item.get("slug") or _slugify_from_title(item.get("title", "")))
            normalized_slug = _normalize_slug(slug)
            title = str(item.get("title") or "").strip()
            description = str(item.get("description") or "").strip() or "Seed template."
            version_label = str(item.get("version", "1.0.0")).strip() or "1.0.0"
            validated_inputs = self._validate_inputs_schema(item.get("inputs") or [])
            validated_steps = self._validate_template_steps(item.get("steps") or [])
            annotations = dict(item.get("annotations") or {})
            derived_capabilities = _normalize_capabilities(
                (item.get("requiredCapabilities") or [])
                + [
                    cap
                    for step in validated_steps
                    for cap in _extract_step_capabilities(step)
                ]
            )

            existing = await self._session.execute(
                select(TaskStepTemplate)
                .where(
                    TaskStepTemplate.slug == normalized_slug,
                    TaskStepTemplate.scope_type == scope,
                    TaskStepTemplate.scope_ref == scope_ref,
                )
                .options(
                    selectinload(TaskStepTemplate.latest_version),
                    selectinload(TaskStepTemplate.versions),
                )
                .limit(1)
            )
            template = existing.scalar_one_or_none()

            if template is None:
                await self.create_template(
                    slug=normalized_slug,
                    title=title,
                    description=description,
                    scope=scope.value,
                    scope_ref=scope_ref,
                    tags=item.get("tags") or [],
                    inputs_schema=validated_inputs,
                    steps=validated_steps,
                    annotations=annotations,
                    required_capabilities=derived_capabilities,
                    created_by=None,
                    version=version_label,
                    release_status=TaskTemplateReleaseStatus.ACTIVE,
                    seed_source=item.get("seedSource"),
                    auto_commit=False,
                )
                result.created += 1
                continue

            updated = False
            normalized_tags = _normalize_tag_list(item.get("tags") or [])
            if template.title != title:
                template.title = title
                updated = True
            if template.description != description:
                template.description = description
                updated = True
            if list(template.tags or []) != normalized_tags:
                template.tags = normalized_tags
                updated = True
            if list(template.required_capabilities or []) != list(derived_capabilities):
                template.required_capabilities = list(derived_capabilities)
                updated = True
            if not template.is_active:
                template.is_active = True
                updated = True

            version_model = next(
                (
                    candidate
                    for candidate in template.versions
                    if candidate.version == version_label
                ),
                None,
            )
            if version_model is None:
                version_model = TaskStepTemplateVersion(
                    id=uuid4(),
                    template=template,
                    version=version_label,
                    inputs_schema=validated_inputs,
                    steps=validated_steps,
                    annotations=annotations,
                    required_capabilities=derived_capabilities,
                    max_step_count=max(25, len(validated_steps)),
                    release_status=TaskTemplateReleaseStatus.ACTIVE,
                    seed_source=item.get("seedSource"),
                )
                self._session.add(version_model)
                updated = True
            else:
                if list(version_model.inputs_schema or []) != validated_inputs:
                    version_model.inputs_schema = validated_inputs
                    updated = True
                if list(version_model.steps or []) != validated_steps:
                    version_model.steps = validated_steps
                    updated = True
                if dict(version_model.annotations or {}) != annotations:
                    version_model.annotations = annotations
                    updated = True
                if list(version_model.required_capabilities or []) != list(
                    derived_capabilities
                ):
                    version_model.required_capabilities = list(derived_capabilities)
                    updated = True
                max_step_count = max(25, len(validated_steps))
                if int(version_model.max_step_count or 0) != max_step_count:
                    version_model.max_step_count = max_step_count
                    updated = True
                if version_model.release_status is not TaskTemplateReleaseStatus.ACTIVE:
                    version_model.release_status = TaskTemplateReleaseStatus.ACTIVE
                    updated = True
                if version_model.seed_source != item.get("seedSource"):
                    version_model.seed_source = item.get("seedSource")
                    updated = True

            if template.latest_version_id != version_model.id:
                template.latest_version = version_model
                updated = True
            if updated:
                result.updated += 1

        if result.created > 0 or result.updated > 0:
            await self._session.commit()

        return result
