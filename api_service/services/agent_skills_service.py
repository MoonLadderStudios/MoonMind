"""Service helpers for the Agent Skill System architecture."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api_service.db.models import (
    AgentSkillDefinition,
    AgentSkillFormat,
    AgentSkillVersion,
    SkillSet,
)
from moonmind.workflows.temporal import TemporalArtifactService

class AgentSkillDuplicateError(ValueError):
    """Raised when creating a skill or skillset with a duplicate slug."""

class AgentSkillNotFoundError(ValueError):
    """Raised when the requested agent skill definition is not found."""

class AgentSkillsService:
    """Orchestrates CRUD and version immutable storage for agent skills."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        artifact_service: TemporalArtifactService | None = None,
    ) -> None:
        self._session = session
        self._artifact_service = artifact_service

    async def list_skills(self) -> list[AgentSkillDefinition]:
        stmt = (
            select(AgentSkillDefinition)
            .options(selectinload(AgentSkillDefinition.versions))
            .order_by(AgentSkillDefinition.slug.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_skill(self, slug: str) -> AgentSkillDefinition | None:
        stmt = (
            select(AgentSkillDefinition)
            .options(selectinload(AgentSkillDefinition.versions))
            .where(AgentSkillDefinition.slug == slug)
        )
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def require_skill(self, slug: str) -> AgentSkillDefinition:
        record = await self.get_skill(slug)
        if record is None:
            raise AgentSkillNotFoundError(f"Agent skill '{slug}' not found.")
        return record

    async def create_skill(
        self,
        *,
        slug: str,
        title: str,
        description: str | None = None,
        author: str | None = None,
    ) -> AgentSkillDefinition:
        skill = AgentSkillDefinition(
            slug=slug,
            title=title,
            description=description,
            author=author,
        )
        self._session.add(skill)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            raise AgentSkillDuplicateError(f"Agent skill slug '{slug}' already exists.") from exc

        await self._session.commit()
        await self._session.refresh(skill)
        return skill

    async def create_version(
        self,
        *,
        skill_slug: str,
        version_string: str,
        content: str,
        format_str: str = "markdown",
        principal: str = "system:agent-skills",
    ) -> AgentSkillVersion:
        if self._artifact_service is None:
            raise RuntimeError("TemporalArtifactService is required to create an agent skill version.")

        skill = await self.require_skill(skill_slug)

        payload_bytes = content.encode("utf-8")
        content_digest = "sha256:" + hashlib.sha256(payload_bytes).hexdigest()

        # Check for duplicate version early if possible
        stmt = select(AgentSkillVersion).where(
            AgentSkillVersion.skill_id == skill.id,
            AgentSkillVersion.version_string == version_string,
        )
        result = await self._session.execute(stmt)
        if result.scalars().first() is not None:
            raise AgentSkillDuplicateError(
                f"Version '{version_string}' already exists for skill '{skill_slug}'."
            )

        try:
            parsed_format = AgentSkillFormat(format_str)
        except ValueError as exc:
            raise ValueError(f"Invalid format processing skill version '{version_string}': {exc}") from exc
        
        # Store content in artifact storage
        artifact, _upload = await self._artifact_service.create(
            principal=principal,
            content_type="text/markdown" if format_str == "markdown" else "application/octet-stream",
            size_bytes=len(payload_bytes),
            sha256=content_digest.removeprefix("sha256:"),
            metadata_json={
                "skill_slug": skill_slug,
                "version_string": version_string,
                "format": format_str,
            },
        )
        artifact = await self._artifact_service.write_complete(
            artifact_id=artifact.artifact_id,
            principal=principal,
            payload=payload_bytes,
            content_type="text/markdown" if format_str == "markdown" else "application/octet-stream",
        )
        artifact_ref = artifact.artifact_id

        version = AgentSkillVersion(
            skill_id=skill.id,
            version_string=version_string,
            format=parsed_format,
            artifact_ref=artifact_ref,
            content_digest=content_digest,
        )
        self._session.add(version)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            raise AgentSkillDuplicateError(
                f"Version '{version_string}' already exists for skill '{skill_slug}'."
            ) from exc

        # Keep skill updated_at fresh
        skill.updated_at = datetime.now(UTC)
        await self._session.commit()
        await self._session.refresh(version)
        return version

    async def create_skill_set(
        self,
        *,
        slug: str,
        title: str,
        description: str | None = None,
    ) -> SkillSet:
        skill_set = SkillSet(
            slug=slug,
            title=title,
            description=description,
        )
        self._session.add(skill_set)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            raise AgentSkillDuplicateError(f"SkillSet slug '{slug}' already exists.") from exc

        await self._session.commit()
        await self._session.refresh(skill_set)
        return skill_set

__all__ = [
    "AgentSkillDuplicateError",
    "AgentSkillNotFoundError",
    "AgentSkillsService",
]
