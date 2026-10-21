"""Service helpers for creating presets from existing steps."""

from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db.models import (
    Preset,
    PresetReleaseStatus,
)
from api_service.services.presets.catalog import (
    PresetCatalogService,
    PresetValidationError,
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
    {"slug", "title", "type", "instructions", "tool", "skill", "annotations"}
)
logger = logging.getLogger(__name__)


def _contains_secret(value: str) -> bool:
    return any(pattern.search(value) is not None for pattern in _SECRET_PATTERNS)


def _slug_from_title(title: str) -> str:
    slug = re.sub(r"[^a-z0-9-]+", "-", title.strip().lower()).strip("-")
    return slug or "template"


class PresetSaveService:
    """Persist user-authored presets sourced from existing workflow steps."""

    def __init__(self, session: AsyncSession):
        self._session = session
        self._catalog = PresetCatalogService(session)

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

    async def _next_available_slug(self, *, base_slug: str) -> str:
        candidate = base_slug
        suffix = 1
        while True:
            existing = await self._session.execute(
                select(Preset.id).where(
                    Preset.slug == candidate,
                    Preset.scope_type.is_(None),
                )
            )
            if existing.scalar_one_or_none() is None:
                return candidate
            suffix += 1
            candidate = f"{base_slug}-{suffix}"

    def _sanitize_steps(self, steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not steps:
            raise PresetValidationError("steps is required")
        sanitized: list[dict[str, Any]] = []
        for index, raw in enumerate(steps, start=1):
            if not isinstance(raw, dict):
                raise PresetValidationError(f"step {index} must be an object")
            cleaned = {key: raw[key] for key in raw if key in _ALLOWED_STEP_KEYS}
            instructions = str(cleaned.get("instructions") or "").strip()
            if not instructions:
                raise PresetValidationError(
                    f"step {index} requires non-empty instructions"
                )
            cleaned["instructions"] = instructions
            title = str(cleaned.get("title") or "").strip()
            if title:
                cleaned["title"] = title
            else:
                cleaned.pop("title", None)
            step_type = str(cleaned.get("type") or "skill").strip().lower()
            if step_type not in {"tool", "skill"}:
                raise PresetValidationError(
                    f"step {index} type must be one of: tool, skill"
                )
            cleaned["type"] = step_type
            tool = cleaned.get("tool")
            if step_type == "tool":
                if not isinstance(tool, dict):
                    raise PresetValidationError(f"step {index} tool must be an object")
                cleaned.pop("skill", None)
            elif tool is not None:
                raise PresetValidationError(
                    f"step {index} skill step must not include tool"
                )
            skill = cleaned.get("skill")
            if step_type == "skill":
                if skill is not None and not isinstance(skill, dict):
                    raise PresetValidationError(f"step {index} skill must be an object")
            else:
                cleaned.pop("skill", None)
            sanitized.append(cleaned)
        return sanitized

    async def save_from_workflow(
        self,
        *,
        title: str,
        description: str,
        steps: list[dict[str, Any]],
        suggested_inputs: list[dict[str, Any]] | None = None,
        tags: list[str] | None = None,
        slug: str | None = None,
        release_status: PresetReleaseStatus = PresetReleaseStatus.DRAFT,
    ) -> dict[str, Any]:
        normalized_title = str(title or "").strip()
        normalized_description = str(description or "").strip() or normalized_title
        if not normalized_title:
            raise PresetValidationError("title is required")

        sanitized_steps = self._sanitize_steps(steps)
        secret_hits = self._scan_for_secrets(sanitized_steps, path="steps")
        if secret_hits:
            raise PresetValidationError(
                "Potential secrets detected; scrub before saving template. "
                f"paths={', '.join(secret_hits[:10])}"
            )

        base_slug = _slug_from_title(slug or normalized_title)
        unique_slug = await self._next_available_slug(
            base_slug=base_slug,
        )
        try:
            saved = await self._catalog.create_template(
                slug=unique_slug,
                title=normalized_title,
                description=normalized_description,
                tags=tags or [],
                inputs_schema=suggested_inputs or [],
                steps=sanitized_steps,
                annotations={},
                required_capabilities=[],
                auto_commit=False,
                release_status=release_status,
            )
            await self._catalog.set_favorite(slug=unique_slug, auto_commit=False)
            preset_id = (
                await self._session.execute(
                    select(Preset.id).where(
                        Preset.slug == unique_slug, Preset.scope_type.is_(None)
                    )
                )
            ).scalar_one()
            await self._catalog.record_recent(template_id=preset_id, auto_commit=False)
            await self._session.commit()
        except Exception:
            await self._session.rollback()
            raise

        logger.info(
            "preset_catalog.save_from_workflow",
            extra={
                "slug": unique_slug,
                "step_count": len(sanitized_steps),
            },
        )
        return saved
