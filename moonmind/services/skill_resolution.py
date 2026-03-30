import abc
from datetime import UTC, datetime
from typing import Any

from moonmind.schemas.agent_skill_models import (
    AgentSkillProvenance,
    AgentSkillSourceKind,
    ResolvedSkillEntry,
    ResolvedSkillSet,
    SkillSelector,
    SkillSelectorEntry,
)


class SkillResolutionContext:
    """Contextual parameters for skill resolution (e.g. paths, run ID)."""

    def __init__(
        self,
        snapshot_id: str,
        deployment_id: str | None = None,
        workspace_root: str | None = None,
        allow_local_skills: bool = False,
    ) -> None:
        self.snapshot_id = snapshot_id
        self.deployment_id = deployment_id
        self.workspace_root = workspace_root
        self.allow_local_skills = allow_local_skills


class SkillLoader(abc.ABC):
    """Base interface for an agent skill source provider."""

    @abc.abstractmethod
    async def load_skills(
        self, selector: SkillSelector, context: SkillResolutionContext
    ) -> list[ResolvedSkillEntry]:
        """Return resolved skill entries from this source that match the selector."""
        raise NotImplementedError


class BuiltInSkillLoader(SkillLoader):
    """Loads embedded capabilities shipped directly with the system."""

    async def load_skills(
        self, selector: SkillSelector, context: SkillResolutionContext
    ) -> list[ResolvedSkillEntry]:
        # For now, we mock an empty list or specific embedded skills
        return []


class DeploymentSkillLoader(SkillLoader):
    """Loads authoritative skills administered through the central DB/API."""

    async def load_skills(
        self, selector: SkillSelector, context: SkillResolutionContext
    ) -> list[ResolvedSkillEntry]:
        # Implementation will query PostgreSQL models:
        # AgentSkillDefinition, AgentSkillVersion
        
        # We need an async session to query `api_service` models here.
        # This will be filled in the detailed DB logic.
        from api_service.db.base import get_async_session_context
        from api_service.db.models import AgentSkillDefinition, AgentSkillVersion, SkillSet
        from sqlalchemy import select
        
        results: list[ResolvedSkillEntry] = []
        
        # NOTE: A more complex query matching the selector intent goes here
        
        return results


class RepoSkillLoader(SkillLoader):
    """Loads skills from the canonical `.agents/skills` repository path."""

    async def load_skills(
        self, selector: SkillSelector, context: SkillResolutionContext
    ) -> list[ResolvedSkillEntry]:
        # Implementation assumes context.workspace_root is available
        return []


class LocalSkillLoader(SkillLoader):
    """Loads local override skills from `.agents/skills/local`."""

    async def load_skills(
        self, selector: SkillSelector, context: SkillResolutionContext
    ) -> list[ResolvedSkillEntry]:
        if not context.allow_local_skills:
            return []
        # Return local skills
        return []


class AgentSkillResolver:
    """Core orchestrator that computes the final immutable ResolvedSkillSet."""

    def __init__(self, loaders: list[SkillLoader] | None = None) -> None:
        # Canonical Precedence: Built-in < Deployment < Repo < Local
        self.loaders = loaders or [
            BuiltInSkillLoader(),
            DeploymentSkillLoader(),
            RepoSkillLoader(),
            LocalSkillLoader(),
        ]

    async def resolve(
        self, selector: SkillSelector, context: SkillResolutionContext
    ) -> ResolvedSkillSet:
        """Resolve the selector against all sources and return a frozen snapshot."""
        
        # 1. Gather all candidates
        candidates_by_source: dict[AgentSkillSourceKind, list[ResolvedSkillEntry]] = {}
        for loader in self.loaders:
            source_kind = self._get_source_kind(loader)
            try:
                candidates = await loader.load_skills(selector, context)
                candidates_by_source[source_kind] = candidates
            except Exception as ex:
                # Log context for diagnostic purposes
                raise RuntimeError(
                    f"Failed to load skills from source {source_kind.value}: {ex}"
                ) from ex

        # 2. Merge respecting precedence
        resolved_map: dict[str, ResolvedSkillEntry] = {}
        
        precedence_order = [
            AgentSkillSourceKind.BUILT_IN,
            AgentSkillSourceKind.DEPLOYMENT,
            AgentSkillSourceKind.REPO,
            AgentSkillSourceKind.LOCAL,
        ]

        for source in precedence_order:
            if source in candidates_by_source:
                for entry in candidates_by_source[source]:
                    # Ignore excluded skills
                    if entry.skill_name in selector.exclude:
                        continue
                        
                    resolved_map[entry.skill_name] = entry

        final_skills = list(resolved_map.values())

        # 3. Create canonical snapshot
        return ResolvedSkillSet(
            snapshot_id=context.snapshot_id,
            deployment_id=context.deployment_id,
            resolved_at=datetime.now(tz=UTC),
            skills=final_skills,
            resolution_inputs=selector.model_dump(mode="json", exclude_none=True),
            policy_summary={
                "local_skills_allowed": context.allow_local_skills,
                "sources_merged": list(candidates_by_source.keys()),
            },
        )
        
    def _get_source_kind(self, loader: SkillLoader) -> AgentSkillSourceKind:
        if isinstance(loader, BuiltInSkillLoader):
            return AgentSkillSourceKind.BUILT_IN
        if isinstance(loader, DeploymentSkillLoader):
            return AgentSkillSourceKind.DEPLOYMENT
        if isinstance(loader, RepoSkillLoader):
            return AgentSkillSourceKind.REPO
        if isinstance(loader, LocalSkillLoader):
            return AgentSkillSourceKind.LOCAL
        raise ValueError(f"Unknown loader type: {type(loader)}")

