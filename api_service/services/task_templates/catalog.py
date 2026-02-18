"""Service helpers for listing and expanding task step templates."""

from __future__ import annotations

import hashlib
import logging
import os
import re
import socket
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import yaml
from jinja2.sandbox import SandboxedEnvironment
from sqlalchemy import and_, delete, select
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

_FORBIDDEN_STEP_KEYS = frozenset(
    {
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
    }
)
_SUPPORTED_INPUT_TYPES = frozenset(
    {"text", "textarea", "markdown", "enum", "boolean", "user", "team", "repo_path"}
)
_SLUG_PATTERN = re.compile(r"[^a-z0-9-]+")
_UNRESOLVED_PLACEHOLDER_PATTERN = re.compile(r"{{\s*[^}]+\s*}}")
logger = logging.getLogger(__name__)


class _StatsdEmitter:
    """Best-effort StatsD counter emitter for template catalog activity."""

    def __init__(self) -> None:
        host = (
            os.getenv("TASK_TEMPLATE_METRICS_HOST")
            or os.getenv("SPEC_WORKFLOW_METRICS_HOST")
            or os.getenv("STATSD_HOST")
            or ""
        ).strip()
        port_raw = (
            os.getenv("TASK_TEMPLATE_METRICS_PORT")
            or os.getenv("SPEC_WORKFLOW_METRICS_PORT")
            or os.getenv("STATSD_PORT")
            or "8125"
        ).strip()
        prefix = (
            os.getenv("TASK_TEMPLATE_METRICS_PREFIX")
            or "moonmind.task_templates"
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
                logger.warning("Task template metrics emitter disabled due to init failure.")
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
        TaskTemplateScopeType.TEAM.value,
        TaskTemplateScopeType.PERSONAL.value,
    }:
        raise TaskTemplateValidationError(
            "scope must be one of: global, team, personal"
        )
    return TaskTemplateScopeType(raw)


def _normalize_scope_ref(scope: TaskTemplateScopeType, scope_ref: str | None) -> str | None:
    cleaned = str(scope_ref or "").strip() or None
    if scope is TaskTemplateScopeType.GLOBAL:
        return None
    if cleaned is None:
        raise TaskTemplateValidationError("scopeRef is required for team/personal scopes.")
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


def _slugify_from_title(title: str) -> str:
    return _normalize_slug(title)


def _hash_from_inputs(values: dict[str, Any]) -> str:
    normalized = repr(sorted(values.items())).encode("utf-8")
    return hashlib.sha1(normalized).hexdigest()[:8]


def _build_step_id(*, slug: str, version: str, index: int, inputs: dict[str, Any]) -> str:
    return f"tpl:{slug}:{version}:{index:02d}:{_hash_from_inputs(inputs)}"


def _render_value(
    env: SandboxedEnvironment,
    value: Any,
    *,
    variables: dict[str, Any],
) -> Any:
    if isinstance(value, str):
        rendered = env.from_string(value).render(**variables)
        return rendered.strip()
    if isinstance(value, list):
        return [_render_value(env, item, variables=variables) for item in value]
    if isinstance(value, dict):
        return {
            str(key): _render_value(env, item, variables=variables)
            for key, item in value.items()
        }
    return value


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
        "latestVersion": version.version,
        "inputs": list(version.inputs_schema or []),
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
        stmt = select(TaskStepTemplate).options(selectinload(TaskStepTemplate.latest_version))
        if not include_inactive:
            stmt = stmt.where(TaskStepTemplate.is_active.is_(True))
        if scope is not None:
            scope_type = _normalize_scope(scope)
            stmt = stmt.where(TaskStepTemplate.scope_type == scope_type)
            if scope_type is not TaskTemplateScopeType.GLOBAL:
                stmt = stmt.where(
                    TaskStepTemplate.scope_ref == _normalize_scope_ref(scope_type, scope_ref)
                )

        template_rows = (await self._session.execute(stmt)).scalars().all()
        lowered_tag = str(tag or "").strip().lower()
        lowered_search = str(search or "").strip().lower()

        favorites_map: dict[UUID, bool] = {}
        recents_map: dict[UUID, datetime] = {}
        if user_id is not None:
            favorite_rows = (
                await self._session.execute(
                    select(TaskStepTemplateFavorite.template_id).where(
                        TaskStepTemplateFavorite.user_id == user_id
                    )
                )
            ).scalars().all()
            favorites_map = {template_id: True for template_id in favorite_rows}

            recent_rows = (
                await self._session.execute(
                    select(TaskStepTemplateVersion.template_id, TaskStepTemplateRecent.applied_at)
                    .join(
                        TaskStepTemplateVersion,
                        TaskStepTemplateVersion.id == TaskStepTemplateRecent.template_version_id,
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
            if lowered_tag and lowered_tag not in {str(item).lower() for item in template.tags or []}:
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
            raise TaskTemplateConflictError("Template slug already exists for this scope.")

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
            + [cap for step in validated_steps for cap in _extract_step_capabilities(step)]
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
            max_step_count=max(1, len(validated_steps)),
            release_status=release_status,
            seed_source=seed_source,
        )
        template.latest_version = version_model
        self._session.add(template)
        self._session.add(version_model)
        await self._session.flush()
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
                raise TaskTemplateValidationError("Template inputs require non-empty names.")
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
                normalized_options = [str(item).strip() for item in options if str(item).strip()]
                if not normalized_options:
                    raise TaskTemplateValidationError(
                        f"Enum input '{name}' requires at least one non-empty option."
                    )
            else:
                normalized_options = []
            names.add(name)
            validated.append(
                {
                    "name": name,
                    "label": label,
                    "type": input_type,
                    "required": bool(raw_input.get("required", False)),
                    "default": raw_input.get("default"),
                    **({"options": normalized_options} if normalized_options else {}),
                }
            )
        return validated

    def _validate_template_steps(self, steps: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
        if not steps:
            raise TaskTemplateValidationError("Template steps must not be empty.")
        validated: list[dict[str, Any]] = []
        for index, raw_step in enumerate(steps, start=1):
            if not isinstance(raw_step, dict):
                raise TaskTemplateValidationError(
                    f"Step {index} must be an object with instructions and optional skill."
                )
            blocked = sorted(key for key in raw_step if str(key).strip() in _FORBIDDEN_STEP_KEYS)
            if blocked:
                raise TaskTemplateValidationError(
                    f"Step {index} uses forbidden keys: {', '.join(blocked)}."
                )
            instructions = str(raw_step.get("instructions") or "").strip()
            if not instructions:
                raise TaskTemplateValidationError(
                    f"Step {index} requires non-empty instructions."
                )
            step_payload: dict[str, Any] = {"instructions": instructions}
            title = str(raw_step.get("title") or "").strip()
            if title:
                step_payload["title"] = title
            if "slug" in raw_step and str(raw_step.get("slug") or "").strip():
                step_payload["slug"] = str(raw_step.get("slug")).strip()
            skill = raw_step.get("skill")
            if skill is not None:
                if not isinstance(skill, dict):
                    raise TaskTemplateValidationError(
                        f"Step {index} skill must be an object when provided."
                    )
                skill_id = str(skill.get("id") or "auto").strip() or "auto"
                skill_args = skill.get("args")
                if skill_args is None:
                    skill_args = {}
                if not isinstance(skill_args, dict):
                    raise TaskTemplateValidationError(
                        f"Step {index} skill.args must be an object when provided."
                    )
                skill_payload = {"id": skill_id, "args": dict(skill_args)}
                caps = skill.get("requiredCapabilities")
                if caps is not None:
                    if not isinstance(caps, list):
                        raise TaskTemplateValidationError(
                            f"Step {index} skill.requiredCapabilities must be a list."
                        )
                    normalized_caps = _normalize_capabilities(caps)
                    if normalized_caps:
                        skill_payload["requiredCapabilities"] = normalized_caps
                step_payload["skill"] = skill_payload
            annotations = raw_step.get("annotations")
            if annotations is not None:
                if not isinstance(annotations, dict):
                    raise TaskTemplateValidationError(
                        f"Step {index} annotations must be an object when provided."
                    )
                step_payload["annotations"] = dict(annotations)
            validated.append(step_payload)
        return validated

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
        selected_version = None
        for candidate in template.versions:
            if candidate.version == version:
                selected_version = candidate
                break
        if selected_version is None:
            raise TaskTemplateNotFoundError("Template version not found.")

        validated_inputs = self._resolve_inputs(
            schema=list(selected_version.inputs_schema or []),
            submitted=dict(inputs or {}),
        )
        variables = {
            "inputs": validated_inputs,
            "context": dict(context or {}),
            "now": datetime.now(UTC).isoformat(),
            "iso_today": datetime.now(UTC).date().isoformat(),
        }

        resolved_steps: list[dict[str, Any]] = []
        warnings: list[str] = []
        enforce_limit = options.should_enforce_step_limit if options else True
        max_step_count = max(int(selected_version.max_step_count or 25), 1)
        if enforce_limit and len(selected_version.steps or []) > max_step_count:
            raise TaskTemplateValidationError(
                f"Template expansion exceeded max_step_count={max_step_count}."
            )

        for index, source_step in enumerate(selected_version.steps or [], start=1):
            rendered = _render_value(self._template_env, source_step, variables=variables)
            if not isinstance(rendered, dict):
                raise TaskTemplateValidationError("Expanded step payload must be an object.")
            blocked = sorted(key for key in rendered if str(key).strip() in _FORBIDDEN_STEP_KEYS)
            if blocked:
                raise TaskTemplateValidationError(
                    f"Expanded step uses forbidden keys: {', '.join(blocked)}."
                )
            instructions = str(rendered.get("instructions") or "").strip()
            if not instructions:
                raise TaskTemplateValidationError("Expanded step instructions may not be empty.")
            if _UNRESOLVED_PLACEHOLDER_PATTERN.search(instructions):
                raise TaskTemplateValidationError(
                    "Expanded instructions still contain unresolved template placeholders."
                )
            step_payload: dict[str, Any] = {
                "id": _build_step_id(
                    slug=template.slug,
                    version=selected_version.version,
                    index=index,
                    inputs=validated_inputs,
                ),
                "instructions": instructions,
            }
            title = str(rendered.get("title") or "").strip()
            if title:
                step_payload["title"] = title
            if isinstance(rendered.get("skill"), dict):
                step_payload["skill"] = rendered["skill"]
            resolved_steps.append(step_payload)

        template_caps = _normalize_capabilities(
            list(template.required_capabilities or [])
            + list(selected_version.required_capabilities or [])
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
                raise TaskTemplateValidationError(f"Missing required template input '{name}'.")
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
            await self._session.commit()
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
    ) -> None:
        self._session.add(
            TaskStepTemplateRecent(
                user_id=user_id,
                template_version_id=template_version_id,
            )
        )
        await self._session.flush()
        stale_rows = (
            await self._session.execute(
                select(TaskStepTemplateRecent.id)
                .where(TaskStepTemplateRecent.user_id == user_id)
                .order_by(
                    TaskStepTemplateRecent.applied_at.desc(),
                    TaskStepTemplateRecent.id.desc(),
                )
                .offset(5)
            )
        ).scalars().all()
        if stale_rows:
            await self._session.execute(
                delete(TaskStepTemplateRecent).where(TaskStepTemplateRecent.id.in_(stale_rows))
            )
        await self._session.commit()
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
                    slug=str(item.get("slug") or _slugify_from_title(item.get("title", ""))),
                    title=str(item.get("title") or "").strip(),
                    description=str(item.get("description") or "").strip() or "Seed template.",
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
                )
                created += 1
            except TaskTemplateConflictError:
                continue
        return created
