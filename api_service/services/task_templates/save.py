"""Service helpers for creating task step templates from existing steps."""

from __future__ import annotations

import logging
import re
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db.models import (
    TaskStepTemplate,
    TaskStepTemplateVersion,
    TaskTemplateScopeType,
)
from api_service.services.task_templates.catalog import (
    TaskTemplateCatalogService,
    TaskTemplateValidationError,
)

_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),
    re.compile(r"-----BEGIN [A-Z ]+PRIVATE KEY-----"),
    re.compile(r"(token|password|secret)\s*=\s*[^\s]+", re.IGNORECASE),
)
_ALLOWED_STEP_KEYS = frozenset(
    {"slug", "title", "instructions", "skill", "annotations"}
)
logger = logging.getLogger(__name__)


def _contains_secret(value: str) -> bool:
    return any(pattern.search(value) is not None for pattern in _SECRET_PATTERNS)


def _slug_from_title(title: str) -> str:
    slug = re.sub(r"[^a-z0-9-]+", "-", title.strip().lower()).strip("-")
    return slug or "template"


class TaskTemplateSaveService:
    """Persist user-authored templates sourced from existing task steps."""

    def __init__(self, session: AsyncSession):
        self._session = session
        self._catalog = TaskTemplateCatalogService(session)

    def _scan_for_secrets(self, value: Any, *, path: str) -> list[str]:
        hits: list[str] = []
        if isinstance(value, str):
            if _contains_secret(value):
                hits.append(path)
            return hits
        if isinstance(value, list):
            for index, item in enumerate(value):
                hits.extend(self._scan_for_secrets(item, path=f"{path}[{index}]"))
            return hits
        if isinstance(value, dict):
            for key, item in value.items():
                hits.extend(self._scan_for_secrets(item, path=f"{path}.{key}"))
            return hits
        return hits

    async def _next_available_slug(
        self, *, base_slug: str, scope: str, scope_ref: str | None
    ) -> str:
        candidate = base_slug
        suffix = 1
        normalized_scope = TaskTemplateScopeType(scope)
        while True:
            existing = await self._session.execute(
                select(TaskStepTemplate.id).where(
                    TaskStepTemplate.slug == candidate,
                    TaskStepTemplate.scope_type == normalized_scope,
                    TaskStepTemplate.scope_ref == scope_ref,
                )
            )
            if existing.scalar_one_or_none() is None:
                return candidate
            suffix += 1
            candidate = f"{base_slug}-{suffix}"

    def _sanitize_steps(self, steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not steps:
            raise TaskTemplateValidationError("steps is required")
        sanitized: list[dict[str, Any]] = []
        for index, raw in enumerate(steps, start=1):
            if not isinstance(raw, dict):
                raise TaskTemplateValidationError(f"step {index} must be an object")
            cleaned = {key: raw[key] for key in raw if key in _ALLOWED_STEP_KEYS}
            instructions = str(cleaned.get("instructions") or "").strip()
            if not instructions:
                raise TaskTemplateValidationError(
                    f"step {index} requires non-empty instructions"
                )
            cleaned["instructions"] = instructions
            title = str(cleaned.get("title") or "").strip()
            if title:
                cleaned["title"] = title
            else:
                cleaned.pop("title", None)
            skill = cleaned.get("skill")
            if skill is not None and not isinstance(skill, dict):
                raise TaskTemplateValidationError(
                    f"step {index} skill must be an object"
                )
            sanitized.append(cleaned)
        return sanitized

    async def save_from_task(
        self,
        *,
        scope: str,
        title: str,
        description: str,
        steps: list[dict[str, Any]],
        suggested_inputs: list[dict[str, Any]] | None = None,
        tags: list[str] | None = None,
        created_by: UUID | None = None,
        scope_ref: str | None = None,
        slug: str | None = None,
    ) -> dict[str, Any]:
        normalized_scope = str(scope or "").strip().lower() or "personal"
        if normalized_scope not in {"personal", "team"}:
            raise TaskTemplateValidationError("scope must be one of: personal, team")
        normalized_title = str(title or "").strip()
        normalized_description = str(description or "").strip() or normalized_title
        if not normalized_title:
            raise TaskTemplateValidationError("title is required")

        sanitized_steps = self._sanitize_steps(steps)
        secret_hits = self._scan_for_secrets(sanitized_steps, path="steps")
        if secret_hits:
            raise TaskTemplateValidationError(
                "Potential secrets detected; scrub before saving template. "
                f"paths={', '.join(secret_hits[:10])}"
            )

        normalized_scope_ref = str(scope_ref or "").strip() or None
        if normalized_scope_ref is None and created_by is not None:
            normalized_scope_ref = str(created_by)

        base_slug = _slug_from_title(slug or normalized_title)
        unique_slug = await self._next_available_slug(
            base_slug=base_slug,
            scope=normalized_scope,
            scope_ref=normalized_scope_ref,
        )
        saved = await self._catalog.create_template(
            slug=unique_slug,
            title=normalized_title,
            description=normalized_description,
            scope=normalized_scope,
            scope_ref=normalized_scope_ref,
            tags=tags or [],
            inputs_schema=suggested_inputs or [],
            steps=sanitized_steps,
            annotations={},
            required_capabilities=[],
            created_by=created_by,
            version="1.0.0",
        )
        if created_by is not None:
            await self._catalog.set_favorite(
                user_id=created_by,
                slug=unique_slug,
                scope=normalized_scope,
                scope_ref=normalized_scope_ref,
            )
            version_id = (
                await self._session.execute(
                    select(TaskStepTemplateVersion.id)
                    .join(
                        TaskStepTemplate,
                        TaskStepTemplate.id == TaskStepTemplateVersion.template_id,
                    )
                    .where(
                        TaskStepTemplate.slug == unique_slug,
                        TaskStepTemplate.scope_type
                        == TaskTemplateScopeType(normalized_scope),
                        TaskStepTemplate.scope_ref == normalized_scope_ref,
                    )
                    .order_by(TaskStepTemplateVersion.created_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if version_id is not None:
                await self._catalog.record_recent(
                    user_id=created_by,
                    template_version_id=version_id,
                )
        logger.info(
            "task_template_catalog.save_from_task",
            extra={
                "slug": unique_slug,
                "scope": normalized_scope,
                "step_count": len(sanitized_steps),
            },
        )
        return saved
