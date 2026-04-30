import abc
import typing
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from moonmind.config.settings import settings
from moonmind.schemas.agent_skill_models import (
    AgentSkillFormat,
    AgentSkillProvenance,
    AgentSkillSourceKind,
    ResolvedSkillEntry,
    ResolvedSkillSet,
    SkillSelector,
)

class SkillResolutionContext:
    """Contextual parameters for skill resolution (e.g. paths, run ID)."""

    def __init__(
        self,
        snapshot_id: str,
        deployment_id: str | None = None,
        workspace_root: str | None = None,
        allow_repo_skills: bool = False,
        allow_local_skills: bool = False,
        async_session_maker: Callable[[], typing.AsyncContextManager[AsyncSession]] | None = None,
    ) -> None:
        self.snapshot_id = snapshot_id
        self.deployment_id = deployment_id
        self.workspace_root = workspace_root
        self.allow_repo_skills = allow_repo_skills
        self.allow_local_skills = allow_local_skills
        self.async_session_maker = async_session_maker

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

    def __init__(self, skills_root: Path | None = None) -> None:
        self._skills_root = skills_root

    @property
    def skills_root(self) -> Path:
        if self._skills_root is not None:
            return self._skills_root
        candidates = [
            Path("/app/.agents/skills"),
            Path.cwd() / ".agents" / "skills",
            Path(settings.workflow.skills_legacy_mirror_root).expanduser(),
            Path(__file__).resolve().parents[2] / ".agents" / "skills",
        ]
        for candidate in candidates:
            resolved = candidate.resolve()
            if (resolved / "pr-resolver" / "SKILL.md").exists():
                return resolved
        return candidates[0]

    async def load_skills(
        self, selector: SkillSelector, context: SkillResolutionContext
    ) -> list[ResolvedSkillEntry]:
        results = _scan_for_skills(
            self.skills_root,
            AgentSkillSourceKind.BUILT_IN,
            version="1.0.0",
            skip_names={"local"},
        )
        discovered = {entry.skill_name for entry in results}
        for name in [
            "moonmind-doc-writer",
            "moonmind-default-reviewer",
            "pr-resolver",
            "batch-pr-resolver",
            "fix-comments",
            "fix-ci",
            "fix-merge-conflicts",
            "auto",
        ]:
            if name in discovered:
                continue
            results.append(
                ResolvedSkillEntry(
                    skill_name=name,
                    version="1.0.0",
                    provenance=AgentSkillProvenance(
                        source_kind=AgentSkillSourceKind.BUILT_IN
                    ),
                )
        )
        return results


class DeploymentSkillLoader(SkillLoader):
    """Loads authoritative skills administered through the central DB/API."""

    async def load_skills(
        self, selector: SkillSelector, context: SkillResolutionContext
    ) -> list[ResolvedSkillEntry]:
        if not context.async_session_maker:
            return []

        # Local import to avoid circular dependencies if this file is imported from api
        from api_service.db.models import AgentSkillDefinition

        results = []

        async with context.async_session_maker() as session:
            stmt = select(AgentSkillDefinition).options(
                selectinload(AgentSkillDefinition.versions)
            )

            if selector.include:
                stmt = stmt.where(
                    AgentSkillDefinition.slug.in_([e.name for e in selector.include])
                )

            res = await session.execute(stmt)
            defs = res.scalars().all()

            for definition in defs:
                if not definition.versions:
                    continue
                latest_version = definition.versions[-1]
                results.append(
                    ResolvedSkillEntry(
                        skill_name=definition.slug,
                        version=latest_version.version_string,
                        format=AgentSkillFormat(latest_version.format.value),
                        content_ref=latest_version.artifact_ref,
                        content_digest=latest_version.content_digest,
                        provenance=AgentSkillProvenance(
                            source_kind=AgentSkillSourceKind.DEPLOYMENT
                        ),
                    )
                )

        return results


def _scan_for_skills(
    skills_dir: Path,
    source_kind: AgentSkillSourceKind,
    *,
    version: str = "latest",
    skip_names: set[str] | None = None,
) -> list[ResolvedSkillEntry]:
    results = []
    if not skills_dir.is_dir():
        return results
    skip_names = skip_names or set()
    for item in sorted(skills_dir.iterdir(), key=lambda path: path.name):
        if item.name in skip_names:
            continue
        if item.is_dir() and (item / "SKILL.md").exists():
            results.append(
                ResolvedSkillEntry(
                    skill_name=item.name,
                    version=version,
                    provenance=AgentSkillProvenance(
                        source_kind=source_kind,
                        source_path=str(item),
                    ),
                )
            )
    return results


class RepoSkillLoader(SkillLoader):
    """Loads skills from the canonical `.agents/skills` repository path."""

    async def load_skills(
        self, selector: SkillSelector, context: SkillResolutionContext
    ) -> list[ResolvedSkillEntry]:
        if not context.allow_repo_skills or not context.workspace_root:
            return []
        skills_dir = Path(context.workspace_root) / ".agents" / "skills"
        return _scan_for_skills(skills_dir, AgentSkillSourceKind.REPO)

class LocalSkillLoader(SkillLoader):
    """Loads local override skills from `.agents/skills/local`."""

    async def load_skills(
        self, selector: SkillSelector, context: SkillResolutionContext
    ) -> list[ResolvedSkillEntry]:
        if not context.allow_local_skills or not context.workspace_root:
            return []
        skills_dir = (
            Path(context.workspace_root) / ".agents" / "skills" / "local"
        )
        return _scan_for_skills(skills_dir, AgentSkillSourceKind.LOCAL)

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
        candidates_by_source: dict[
            AgentSkillSourceKind, list[ResolvedSkillEntry]
        ] = {}
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
                bucket_seen = set()
                for entry in candidates_by_source[source]:
                    # Ignore excluded skills
                    if entry.skill_name in selector.exclude:
                        continue

                    # Collision detection within the same source
                    if entry.skill_name in bucket_seen:
                        raise ValueError(
                            f"Duplicate skill definition '{entry.skill_name}' found in source {source.value}"
                        )
                    bucket_seen.add(entry.skill_name)

                    resolved_map[entry.skill_name] = entry

        # Pinning strict check
        for include_entry in selector.include:
            if include_entry.version:
                resolved = resolved_map.get(include_entry.name)
                if not resolved or resolved.version != include_entry.version:
                    raise ValueError(
                        f"Could not resolve pinned version '{include_entry.name}@{include_entry.version}' across any active sources"
                    )

        final_skills = list(resolved_map.values())
        
        requested_names = {entry.name for entry in selector.include}
        # Note: Future implementation for `sets` would expand skill names here.

        if selector.include or selector.sets:
            final_skills = [s for s in final_skills if s.skill_name in requested_names]
        else:
            # If nothing was requested, return empty
            final_skills = []
            
        final_skills.sort(key=lambda s: s.skill_name)

        # 3. Create canonical snapshot
        return ResolvedSkillSet(
            snapshot_id=context.snapshot_id,
            deployment_id=context.deployment_id,
            resolved_at=datetime.now(tz=UTC),
            skills=final_skills,
            resolution_inputs=selector.model_dump(
                mode="json", exclude_none=True
            ),
            policy_summary={
                "repo_skills_allowed": context.allow_repo_skills,
                "local_skills_allowed": context.allow_local_skills,
                "sources_merged": [
                    s.value for s in candidates_by_source.keys()
                ],
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
