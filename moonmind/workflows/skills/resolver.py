"""Per-run skill selection and source resolution."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse

from moonmind.config.settings import settings


class SkillResolutionError(ValueError):
    """Raised when a run skill selection cannot be resolved."""


_SKILL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


@dataclass(frozen=True, slots=True)
class ResolvedSkill:
    """Resolved runtime metadata for one selected skill."""

    skill_name: str
    version: str
    source_uri: str
    content_hash: str | None = None
    signature: str | None = None


@dataclass(frozen=True, slots=True)
class RunSkillSelection:
    """Effective per-run skill selection used by the materializer."""

    run_id: str
    selection_source: str
    skills: tuple[ResolvedSkill, ...]

    def to_payload(self) -> dict[str, Any]:
        """Return a serializable payload for logs and context metadata."""

        return {
            "selectionSource": self.selection_source,
            "skills": [
                {
                    "name": skill.skill_name,
                    "version": skill.version,
                    "sourceUri": skill.source_uri,
                    "contentHash": skill.content_hash,
                    "signature": skill.signature,
                }
                for skill in self.skills
            ],
        }


def _normalize_skill_entry(raw: object) -> dict[str, str | None]:
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            raise SkillResolutionError("Skill entry cannot be blank")
        if ":" in text:
            skill_name, version = text.split(":", 1)
        else:
            skill_name, version = text, "local"
        return {
            "skill_name": skill_name.strip(),
            "version": version.strip() or "local",
            "source_uri": None,
            "content_hash": None,
            "signature": None,
        }

    if isinstance(raw, Mapping):
        skill_name = str(raw.get("skill_name") or raw.get("name") or "").strip()
        if not skill_name:
            raise SkillResolutionError("Skill entry is missing skill_name")
        version = str(raw.get("version") or "local").strip() or "local"
        source_uri = (
            str(raw.get("source_uri") or raw.get("sourceUri") or "").strip() or None
        )
        content_hash = (
            str(raw.get("content_hash") or raw.get("contentHash") or "").strip() or None
        )
        signature = str(raw.get("signature") or "").strip() or None
        return {
            "skill_name": skill_name,
            "version": version,
            "source_uri": source_uri,
            "content_hash": content_hash,
            "signature": signature,
        }

    raise SkillResolutionError(f"Unsupported skill entry type: {type(raw)!r}")


def _normalize_skill_selection(raw: object) -> list[dict[str, str | None]]:
    if raw is None:
        return []
    if isinstance(raw, str):
        parts = [item.strip() for item in raw.split(",") if item.strip()]
        return [_normalize_skill_entry(item) for item in parts]
    if isinstance(raw, Sequence) and not isinstance(raw, (bytes, bytearray, str)):
        return [_normalize_skill_entry(item) for item in raw]
    raise SkillResolutionError(
        "Skill selection must be a sequence or comma-delimited string"
    )


def _file_uri(path: Path) -> str:
    return path.resolve().as_uri()


def _validate_skill_name(skill_name: str) -> None:
    if not skill_name:
        raise SkillResolutionError("Skill name cannot be blank")
    if Path(skill_name).name != skill_name or ".." in skill_name:
        raise SkillResolutionError(
            f"Skill name '{skill_name}' is not a safe path component"
        )
    if not _SKILL_NAME_PATTERN.fullmatch(skill_name):
        raise SkillResolutionError(
            f"Skill name '{skill_name}' contains unsupported characters"
        )


def _resolve_local_source(skill_name: str) -> str | None:
    cfg = settings.spec_workflow

    roots = [
        Path(cfg.skills_local_mirror_root),
        Path(cfg.skills_legacy_mirror_root),
    ]
    for root in roots:
        base = root.expanduser()
        if not base.is_absolute():
            base = (Path.cwd() / base).resolve()
        candidate = base / skill_name
        if candidate.is_dir():
            return _file_uri(candidate)
    return None


def _resolve_source_uri(
    *,
    skill_name: str,
    version: str,
    declared_source: str | None,
    source_overrides: Mapping[str, object] | None,
) -> str:
    if declared_source:
        return declared_source

    if source_overrides:
        keyed = source_overrides.get(f"{skill_name}:{version}")
        fallback = source_overrides.get(skill_name)
        raw = keyed if keyed is not None else fallback
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
        if isinstance(raw, Mapping):
            uri = str(raw.get("source_uri") or raw.get("sourceUri") or "").strip()
            if uri:
                return uri

    local_source = _resolve_local_source(skill_name)
    if local_source:
        return local_source

    # Preserve backward compatibility for the built-in Speckit execution path.
    if skill_name == "speckit":
        return "builtin://speckit"

    raise SkillResolutionError(
        f"No source URI resolved for skill '{skill_name}:{version}'. "
        "Provide skill_sources override or configure a local mirror root."
    )


def resolve_run_skill_selection(
    *,
    run_id: str,
    context: Mapping[str, Any],
) -> RunSkillSelection:
    """Resolve the effective skill set for a workflow run."""

    job_override = context.get("skill_selection")
    queue_profile = context.get("queue_skill_selection")

    if job_override:
        raw_selection = job_override
        selection_source = "job_override"
    elif queue_profile:
        raw_selection = queue_profile
        selection_source = "queue_profile"
    else:
        allowed = tuple(settings.spec_workflow.allowed_skills or ())
        default = settings.spec_workflow.default_skill
        if default and default not in allowed:
            allowed = (*allowed, default)
        raw_selection = allowed
        selection_source = "global_default"

    normalized = _normalize_skill_selection(raw_selection)
    if not normalized:
        raise SkillResolutionError("Resolved skill selection is empty")

    source_overrides = context.get("skill_sources")
    if source_overrides is not None and not isinstance(source_overrides, Mapping):
        raise SkillResolutionError("skill_sources must be a mapping when provided")

    resolved: list[ResolvedSkill] = []
    seen_names: set[str] = set()

    for entry in normalized:
        skill_name = str(entry["skill_name"] or "").strip()
        version = str(entry["version"] or "local").strip() or "local"
        _validate_skill_name(skill_name)
        if skill_name in seen_names:
            raise SkillResolutionError(
                f"Duplicate skill name '{skill_name}' in resolved selection"
            )

        source_uri = _resolve_source_uri(
            skill_name=skill_name,
            version=version,
            declared_source=entry.get("source_uri"),
            source_overrides=(
                source_overrides if isinstance(source_overrides, Mapping) else None
            ),
        )

        # Basic URI sanity check so we fail fast on malformed values.
        if "://" in source_uri:
            parsed = urlparse(source_uri)
            if not parsed.scheme:
                raise SkillResolutionError(
                    f"Invalid source URI for skill '{skill_name}': {source_uri}"
                )

        resolved.append(
            ResolvedSkill(
                skill_name=skill_name,
                version=version,
                source_uri=source_uri,
                content_hash=entry.get("content_hash"),
                signature=entry.get("signature"),
            )
        )
        seen_names.add(skill_name)

    return RunSkillSelection(
        run_id=run_id,
        selection_source=selection_source,
        skills=tuple(resolved),
    )
