"""Skill registry loading, validation, and snapshotting helpers."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

from .artifact_store import ArtifactStore
from .skill_plan_contracts import (
    REGISTRY_DIGEST_PREFIX,
    ContractValidationError,
    SkillDefinition,
    parse_skill_definition,
)


class SkillRegistryError(ValueError):
    """Raised when registry definitions or snapshots are invalid."""


@dataclass(frozen=True, slots=True)
class SkillRegistrySnapshot:
    """Immutable registry snapshot pinned by plan metadata."""

    digest: str
    artifact_ref: str
    skills: tuple[SkillDefinition, ...]

    def __post_init__(self) -> None:
        if not self.digest.startswith(REGISTRY_DIGEST_PREFIX):
            raise SkillRegistryError(
                f"Invalid snapshot digest '{self.digest}': expected {REGISTRY_DIGEST_PREFIX}*"
            )

    @property
    def by_key(self) -> dict[tuple[str, str], SkillDefinition]:
        return {skill.key: skill for skill in self.skills}

    def get_skill(self, *, name: str, version: str) -> SkillDefinition:
        try:
            return self.by_key[(name, version)]
        except KeyError as exc:
            raise SkillRegistryError(
                f"Skill '{name}:{version}' was not found in pinned snapshot {self.digest}"
            ) from exc

    def to_payload(self) -> dict[str, Any]:
        return {
            "digest": self.digest,
            "artifact_ref": self.artifact_ref,
            "skills": [skill.to_payload() for skill in self.skills],
        }


def _canonical_registry_doc(skills: tuple[SkillDefinition, ...]) -> dict[str, Any]:
    ordered = sorted(skills, key=lambda skill: (skill.name, skill.version))
    return {
        "schema_version": "1.0",
        "skills": [skill.to_payload() for skill in ordered],
    }


def _digest_registry_doc(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"{REGISTRY_DIGEST_PREFIX}{hashlib.sha256(encoded).hexdigest()}"


def validate_skill_registry(skills: tuple[SkillDefinition, ...]) -> None:
    """Validate a registry payload after it has been parsed."""

    if not skills:
        raise SkillRegistryError("Registry is empty")

    seen: set[tuple[str, str]] = set()
    for skill in skills:
        if skill.key in seen:
            raise SkillRegistryError(
                f"Duplicate skill definition for '{skill.name}:{skill.version}'"
            )
        seen.add(skill.key)


def parse_skill_registry(payload: Mapping[str, Any]) -> tuple[SkillDefinition, ...]:
    """Parse untrusted registry payload into validated skill definitions."""

    raw_skills: Any
    if "skills" in payload:
        raw_skills = payload.get("skills")
    else:
        raw_skills = payload

    if isinstance(raw_skills, Mapping):
        raise SkillRegistryError("Registry 'skills' must be an array")
    if not isinstance(raw_skills, list):
        raise SkillRegistryError("Registry payload must be an object with a skills array")

    parsed: list[SkillDefinition] = []
    for entry in raw_skills:
        if not isinstance(entry, Mapping):
            raise SkillRegistryError("Skill registry entries must be objects")
        try:
            parsed.append(parse_skill_definition(entry))
        except ContractValidationError as exc:
            raise SkillRegistryError(str(exc)) from exc

    skills = tuple(parsed)
    validate_skill_registry(skills)
    return skills


def load_skill_registry(path: Path) -> tuple[SkillDefinition, ...]:
    """Load registry definitions from a YAML or JSON file."""

    data = path.read_text(encoding="utf-8")
    suffix = path.suffix.lower()
    if suffix in {".yaml", ".yml"}:
        payload = yaml.safe_load(data) or {}
    elif suffix == ".json":
        payload = json.loads(data)
    else:
        raise SkillRegistryError(f"Unsupported registry file extension: {path}")

    if not isinstance(payload, Mapping):
        raise SkillRegistryError("Registry root must be an object")
    return parse_skill_registry(payload)


def create_registry_snapshot(
    *,
    skills: tuple[SkillDefinition, ...],
    artifact_store: ArtifactStore,
) -> SkillRegistrySnapshot:
    """Create and persist an immutable registry snapshot artifact."""

    validate_skill_registry(skills)
    canonical = _canonical_registry_doc(skills)
    digest = _digest_registry_doc(canonical)

    artifact = artifact_store.put_json(
        canonical,
        metadata={
            "name": "registry_snapshot.json",
            "producer": "skill:registry.snapshot",
            "labels": ["registry", "snapshot"],
            "digest": digest,
        },
    )

    return SkillRegistrySnapshot(digest=digest, artifact_ref=artifact.artifact_ref, skills=skills)


def load_registry_snapshot_from_artifact(
    *,
    artifact_ref: str,
    artifact_store: ArtifactStore,
) -> SkillRegistrySnapshot:
    """Load and validate a registry snapshot from artifact storage."""

    payload = artifact_store.get_json(artifact_ref)
    if not isinstance(payload, Mapping):
        raise SkillRegistryError("Registry snapshot artifact payload must be an object")

    skills = parse_skill_registry(payload)
    digest = _digest_registry_doc(_canonical_registry_doc(skills))
    return SkillRegistrySnapshot(digest=digest, artifact_ref=artifact_ref, skills=skills)


__all__ = [
    "SkillRegistryError",
    "SkillRegistrySnapshot",
    "create_registry_snapshot",
    "load_registry_snapshot_from_artifact",
    "load_skill_registry",
    "parse_skill_registry",
    "validate_skill_registry",
]
