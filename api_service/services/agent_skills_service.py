"""Service helpers for the Agent Skill System architecture."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db.models import (
    AgentSkillDefinition,
    AgentSkillFormat,
    SkillSet,
)
from moonmind.capabilities.input_contracts import (
    CapabilityInputContractError,
    parse_skill_capability_input_contract,
)
from moonmind.services.skill_resolution import (
    extract_required_capabilities_from_skill_markdown,
    extract_required_skill_names_from_skill_markdown,
)
from moonmind.workflows.temporal import TemporalArtifactService

class AgentSkillDuplicateError(ValueError):
    """Raised when creating a skill or skillset with a duplicate slug."""

class AgentSkillNotFoundError(ValueError):
    """Raised when the requested agent skill definition is not found."""

class AgentSkillsService:
    """Orchestrates CRUD and current content storage for agent skills."""

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
            .order_by(AgentSkillDefinition.slug.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_skill(self, slug: str) -> AgentSkillDefinition | None:
        stmt = (
            select(AgentSkillDefinition)
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

    async def update_skill_content(
        self,
        *,
        skill_slug: str,
        content: str,
        format_str: str = "markdown",
        principal: str = "system:agent-skills",
    ) -> AgentSkillDefinition:
        if self._artifact_service is None:
            raise RuntimeError("TemporalArtifactService is required to update agent skill content.")

        skill = await self.require_skill(skill_slug)

        payload_bytes = content.encode("utf-8")
        content_digest = "sha256:" + hashlib.sha256(payload_bytes).hexdigest()
        required_skills = extract_required_skill_names_from_skill_markdown(
            content,
            skill_name=skill_slug,
            source_label=f"deployment skill '{skill_slug}'",
        )
        required_capabilities = extract_required_capabilities_from_skill_markdown(
            content,
            skill_name=skill_slug,
            source_label=f"deployment skill '{skill_slug}'",
        )
        try:
            input_contract = parse_skill_capability_input_contract(
                skill_id=skill_slug,
                label=skill.title or skill_slug,
                markdown=content,
                source={"kind": "deployment"},
                strict=True,
            )
        except CapabilityInputContractError as exc:
            raise ValueError(
                f"Invalid Skill input schema for '{skill_slug}': {exc}"
            ) from exc

        try:
            parsed_format = AgentSkillFormat(format_str)
        except ValueError as exc:
            raise ValueError(f"Invalid format processing skill '{skill_slug}': {exc}") from exc
        
        # Store content in artifact storage
        artifact, _upload = await self._artifact_service.create(
            principal=principal,
            content_type="text/markdown" if format_str == "markdown" else "application/octet-stream",
            size_bytes=len(payload_bytes),
            sha256=content_digest.removeprefix("sha256:"),
            metadata_json={
                "skill_slug": skill_slug,
                "format": format_str,
                "required_skills": list(required_skills),
                "required_capabilities": list(required_capabilities),
                "source_issue": "MM-1047",
                "implementation_issue": "MM-1053",
                "input_schema": input_contract.get("inputSchema", {}),
                "ui_schema": input_contract.get("uiSchema", {}),
                "defaults": input_contract.get("defaults", {}),
                "input_contract_digest": input_contract.get("contractDigest"),
                "input_schema_diagnostics": input_contract.get("diagnostics", []),
            },
        )
        artifact = await self._artifact_service.write_complete(
            artifact_id=artifact.artifact_id,
            principal=principal,
            payload=payload_bytes,
            content_type="text/markdown" if format_str == "markdown" else "application/octet-stream",
        )
        artifact_ref = artifact.artifact_id

        skill.format = parsed_format
        skill.artifact_ref = artifact_ref
        skill.content_digest = content_digest
        skill.updated_at = datetime.now(UTC)
        await self._session.commit()
        await self._session.refresh(skill)
        return skill

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
