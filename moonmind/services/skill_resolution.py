import abc
import asyncio
import re
import typing
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from moonmind.config.settings import settings
from moonmind.schemas.agent_skill_models import (
    AgentSkillFormat,
    AgentSkillProvenance,
    AgentSkillSourceKind,
    ResolvedSkillEntry,
    ResolvedSkillSet,
    SkillSelector,
)

_REQUIRED_SKILLS_METADATA_KEY = "required-skills"
_AGENT_SKILL_NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9_-]{0,62}[a-z0-9])?$")

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
        return await self.load_named_skills(
            [entry.name for entry in selector.include],
            context,
        )

    async def load_named_skills(
        self,
        skill_names: typing.Iterable[str],
        context: SkillResolutionContext,
    ) -> list[ResolvedSkillEntry]:
        if not context.async_session_maker:
            return []

        requested_names = sorted(
            {str(name).strip() for name in skill_names if str(name).strip()}
        )
        if not requested_names:
            return []

        # Local import to avoid circular dependencies if this file is imported from api
        from api_service.db.models import AgentSkillDefinition, TemporalArtifact

        results = []

        async with context.async_session_maker() as session:
            stmt = (
                select(AgentSkillDefinition)
                .options(selectinload(AgentSkillDefinition.versions))
                .where(AgentSkillDefinition.slug.in_(requested_names))
            )

            res = await session.execute(stmt)
            defs = res.scalars().all()
            latest_versions_by_artifact: dict[str, typing.Any] = {}
            for definition in defs:
                if definition.versions:
                    latest_version = definition.versions[-1]
                    latest_versions_by_artifact[latest_version.artifact_ref] = latest_version

            metadata_by_artifact: dict[str, dict[str, typing.Any]] = {}
            if latest_versions_by_artifact:
                artifact_stmt = select(
                    TemporalArtifact.artifact_id,
                    TemporalArtifact.metadata_json,
                ).where(TemporalArtifact.artifact_id.in_(latest_versions_by_artifact))
                artifact_res = await session.execute(artifact_stmt)
                for artifact_id, metadata_json in artifact_res.all():
                    if isinstance(metadata_json, dict):
                        metadata_by_artifact[str(artifact_id)] = metadata_json

            for definition in defs:
                if not definition.versions:
                    continue
                latest_version = definition.versions[-1]
                metadata = metadata_by_artifact.get(latest_version.artifact_ref, {})
                required_skills = _required_skill_names_from_artifact_metadata(
                    metadata,
                    owner=definition.slug,
                )
                results.append(
                    ResolvedSkillEntry(
                        skill_name=definition.slug,
                        version=latest_version.version_string,
                        format=AgentSkillFormat(latest_version.format.value),
                        content_ref=latest_version.artifact_ref,
                        content_digest=latest_version.content_digest,
                        required_skills=list(required_skills),
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


def _load_skill_frontmatter(skill_dir: Path) -> dict[str, typing.Any]:
    skill_file = skill_dir / "SKILL.md"
    try:
        lines = skill_file.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValueError(f"failed to read skill frontmatter from {skill_file}: {exc}") from exc
    if not lines or lines[0].strip() != "---":
        return {}
    frontmatter_lines: list[str] = []
    for line in lines[1:]:
        if line.strip() == "---":
            break
        frontmatter_lines.append(line)
    else:
        return {}
    try:
        parsed = yaml.safe_load("\n".join(frontmatter_lines)) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid YAML frontmatter in {skill_file}: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"frontmatter in {skill_file} must be a mapping")
    return parsed


def _load_skill_frontmatter_from_markdown(
    markdown: str,
    *,
    source_label: str,
) -> dict[str, typing.Any]:
    lines = markdown.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    frontmatter_lines: list[str] = []
    for line in lines[1:]:
        if line.strip() == "---":
            break
        frontmatter_lines.append(line)
    else:
        return {}
    try:
        parsed = yaml.safe_load("\n".join(frontmatter_lines)) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid YAML frontmatter in {source_label}: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"frontmatter in {source_label} must be a mapping")
    return parsed


def _validate_required_skill_name(skill_name: str, *, owner: str) -> str:
    normalized = skill_name.strip()
    if (
        not normalized
        or "--" in normalized
        or _AGENT_SKILL_NAME_RE.fullmatch(normalized) is None
    ):
        raise ValueError(
            f"skill '{owner}' metadata.{_REQUIRED_SKILLS_METADATA_KEY} contains "
            f"invalid Agent Skills name '{skill_name}'"
        )
    return normalized


def _required_skill_names_from_metadata(
    raw_required: typing.Any,
    *,
    owner: str,
) -> tuple[str, ...]:
    if raw_required is None:
        return ()
    if not isinstance(raw_required, str):
        raise ValueError(
            f"skill '{owner}' metadata.{_REQUIRED_SKILLS_METADATA_KEY} "
            "must be a string"
        )
    required: list[str] = []
    seen: set[str] = set()
    for raw_name in raw_required.split():
        name = _validate_required_skill_name(raw_name, owner=owner)
        if name == owner:
            raise ValueError(f"skill '{owner}' cannot require itself")
        if name in seen:
            continue
        seen.add(name)
        required.append(name)
    return tuple(required)


def extract_required_skill_names_from_skill_markdown(
    markdown: str,
    *,
    skill_name: str,
    source_label: str | None = None,
) -> tuple[str, ...]:
    """Return required-skill metadata from a skill markdown payload."""

    frontmatter = _load_skill_frontmatter_from_markdown(
        markdown,
        source_label=source_label or skill_name,
    )
    return _required_skill_names_from_frontmatter(frontmatter, owner=skill_name)


def _required_skill_names_from_frontmatter(
    frontmatter: dict[str, typing.Any],
    *,
    owner: str,
) -> tuple[str, ...]:
    metadata = frontmatter.get("metadata") or {}
    if not isinstance(metadata, dict):
        raise ValueError(f"skill '{owner}' metadata must be a mapping")
    return _required_skill_names_from_metadata(
        metadata.get(_REQUIRED_SKILLS_METADATA_KEY),
        owner=owner,
    )


async def _required_skill_names_for_entry(entry: ResolvedSkillEntry) -> tuple[str, ...]:
    if entry.required_skills:
        required: list[str] = []
        seen: set[str] = set()
        for raw_name in entry.required_skills:
            name = _validate_required_skill_name(str(raw_name), owner=entry.skill_name)
            if name == entry.skill_name:
                raise ValueError(f"skill '{entry.skill_name}' cannot require itself")
            if name in seen:
                continue
            seen.add(name)
            required.append(name)
        return tuple(required)

    source_path = entry.provenance.source_path
    if not source_path:
        return ()
    frontmatter = await asyncio.to_thread(_load_skill_frontmatter, Path(source_path))
    return _required_skill_names_from_frontmatter(frontmatter, owner=entry.skill_name)


def _required_skill_names_from_artifact_metadata(
    metadata: dict[str, typing.Any],
    *,
    owner: str,
) -> tuple[str, ...]:
    raw_required = metadata.get("required_skills")
    if raw_required is None:
        return ()
    if not isinstance(raw_required, list) or not all(
        isinstance(item, str) for item in raw_required
    ):
        raise ValueError(
            f"skill '{owner}' artifact metadata.required_skills must be a list of strings"
        )
    required: list[str] = []
    seen: set[str] = set()
    for raw_name in raw_required:
        name = _validate_required_skill_name(raw_name, owner=owner)
        if name == owner:
            raise ValueError(f"skill '{owner}' cannot require itself")
        if name in seen:
            continue
        seen.add(name)
        required.append(name)
    return tuple(required)


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
        precedence_rank = {
            source: index for index, source in enumerate(precedence_order)
        }

        def merge_entry(entry: ResolvedSkillEntry) -> None:
            existing = resolved_map.get(entry.skill_name)
            if existing is None:
                resolved_map[entry.skill_name] = entry
                return
            existing_rank = precedence_rank[existing.provenance.source_kind]
            incoming_rank = precedence_rank[entry.provenance.source_kind]
            if incoming_rank >= existing_rank:
                resolved_map[entry.skill_name] = entry

        for source in precedence_order:
            if source in candidates_by_source:
                bucket_seen = set()
                for entry in candidates_by_source[source]:
                    # Collision detection within the same source
                    if entry.skill_name in bucket_seen:
                        raise ValueError(
                            f"Duplicate skill definition '{entry.skill_name}' found in source {source.value}"
                        )
                    bucket_seen.add(entry.skill_name)

                    merge_entry(entry)

        excluded_names = {str(name).strip() for name in selector.exclude if str(name).strip()}

        # Pinning strict check
        for include_entry in selector.include:
            if include_entry.name in excluded_names:
                raise ValueError(
                    f"selected skill '{include_entry.name}' cannot also be excluded"
                )
            if include_entry.version:
                resolved = resolved_map.get(include_entry.name)
                if not resolved or resolved.version != include_entry.version:
                    raise ValueError(
                        f"Could not resolve pinned version '{include_entry.name}@{include_entry.version}' across any active sources"
                    )

        requested_names = {entry.name for entry in selector.include}
        # Note: Future implementation for `sets` would expand skill names here.

        if selector.include or selector.sets:
            required_by: dict[str, set[str]] = {}
            closure_names = set(requested_names)
            pending = list(sorted(requested_names))
            deployment_fetch_attempted = {
                entry.skill_name
                for entry in candidates_by_source.get(AgentSkillSourceKind.DEPLOYMENT, [])
            }
            while pending:
                current_name = pending.pop(0)
                current = resolved_map.get(current_name)
                if current is None:
                    raise ValueError(f"Could not resolve selected skill '{current_name}'")
                for required_name in await _required_skill_names_for_entry(current):
                    if required_name in excluded_names:
                        raise ValueError(
                            f"skill '{current_name}' requires skill '{required_name}', "
                            "but it was explicitly excluded"
                        )
                    if required_name not in deployment_fetch_attempted:
                        deployment_fetch_attempted.add(required_name)
                        deployment_loader = self._deployment_loader()
                        if deployment_loader is not None:
                            deployment_entries = await deployment_loader.load_named_skills(
                                [required_name],
                                context,
                            )
                            if deployment_entries:
                                deployment_bucket = candidates_by_source.setdefault(
                                    AgentSkillSourceKind.DEPLOYMENT,
                                    [],
                                )
                                deployment_seen = {
                                    entry.skill_name for entry in deployment_bucket
                                }
                                for entry in deployment_entries:
                                    if entry.skill_name not in deployment_seen:
                                        deployment_bucket.append(entry)
                                        deployment_seen.add(entry.skill_name)
                                    merge_entry(entry)
                    if required_name not in resolved_map:
                        raise ValueError(
                            f"skill '{current_name}' requires missing skill '{required_name}'"
                        )
                    required_by.setdefault(required_name, set()).add(current_name)
                    if required_name not in closure_names:
                        closure_names.add(required_name)
                        pending.append(required_name)

            final_skills = []
            for name in closure_names:
                entry = resolved_map[name]
                reason = "selected" if name in requested_names else "required"
                final_skills.append(
                    entry.model_copy(
                        update={
                            "selection_reason": reason,
                            "required_by": sorted(required_by.get(name, set())),
                        }
                    )
                )
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
            source_trace={
                "requiredSkillEdges": [
                    {
                        "requiredBy": required_by,
                        "skill": entry.skill_name,
                    }
                    for entry in final_skills
                    for required_by in entry.required_by
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

    def _deployment_loader(self) -> DeploymentSkillLoader | None:
        for loader in self.loaders:
            if isinstance(loader, DeploymentSkillLoader):
                return loader
        return None
