"""Registry helpers for tool definitions and pinned registry snapshots."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

from .artifact_store import ArtifactStore
from .tool_plan_contracts import (
    REGISTRY_DIGEST_PREFIX,
    ContractValidationError,
    ToolDefinition,
    parse_tool_definition,
)

class ToolRegistryError(ValueError):
    """Raised when registry definitions or snapshots are invalid."""

@dataclass(frozen=True, slots=True)
class ToolRegistrySnapshot:
    """Immutable registry snapshot pinned by plan metadata."""

    digest: str
    artifact_ref: str
    skills: tuple[ToolDefinition, ...]

    def __post_init__(self) -> None:
        if not self.digest.startswith(REGISTRY_DIGEST_PREFIX):
            raise ToolRegistryError(
                f"Invalid snapshot digest '{self.digest}': expected {REGISTRY_DIGEST_PREFIX}*"
            )

    @property
    def by_key(self) -> dict[tuple[str, str], ToolDefinition]:
        return {skill.key: skill for skill in self.skills}

    @property
    def tools(self) -> tuple[ToolDefinition, ...]:
        return self.skills

    @property
    def tools_by_key(self) -> dict[tuple[str, str], ToolDefinition]:
        return self.by_key

    def get_skill(self, *, name: str, version: str) -> ToolDefinition:
        try:
            return self.by_key[(name, version)]
        except KeyError as exc:
            raise ToolRegistryError(
                f"Skill '{name}:{version}' was not found in pinned snapshot {self.digest}"
            ) from exc

    def get_tool(self, *, name: str, version: str) -> ToolDefinition:
        return self.get_skill(name=name, version=version)

    def to_payload(self) -> dict[str, Any]:
        entries = [skill.to_payload() for skill in self.skills]
        return {
            "digest": self.digest,
            "artifact_ref": self.artifact_ref,
            "tools": entries,
            "skills": entries,
        }

def _canonical_registry_doc(skills: tuple[ToolDefinition, ...]) -> dict[str, Any]:
    ordered = sorted(skills, key=lambda skill: (skill.name, skill.version))
    entries = [skill.to_payload() for skill in ordered]
    return {
        "schema_version": "1.0",
        "tools": entries,
        "skills": entries,
    }

def _digest_registry_doc(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"{REGISTRY_DIGEST_PREFIX}{hashlib.sha256(encoded).hexdigest()}"

def compute_registry_digest(*, skills: tuple[ToolDefinition, ...]) -> str:
    """Compute the canonical digest for a registry snapshot payload."""

    return _digest_registry_doc(_canonical_registry_doc(skills))

def validate_tool_registry(skills: tuple[ToolDefinition, ...]) -> None:
    """Validate a registry payload after it has been parsed."""

    if not skills:
        raise ToolRegistryError("Registry is empty")

    seen: set[tuple[str, str]] = set()
    for definition in skills:
        if definition.key in seen:
            raise ToolRegistryError(
                f"Duplicate tool definition for '{definition.name}:{definition.version}'"
            )
        seen.add(definition.key)

def parse_tool_registry(payload: Mapping[str, Any]) -> tuple[ToolDefinition, ...]:
    """Parse untrusted registry payload into validated tool definitions."""

    raw_skills: Any
    if "tools" in payload:
        raw_skills = payload.get("tools")
    elif "skills" in payload:
        raw_skills = payload.get("skills")
    else:
        raw_skills = payload

    if isinstance(raw_skills, Mapping):
        raise ToolRegistryError("Registry 'tools' must be an array")
    if not isinstance(raw_skills, list):
        raise ToolRegistryError(
            "Registry payload must be an object with a tools array"
        )

    parsed: list[ToolDefinition] = []
    for entry in raw_skills:
        if not isinstance(entry, Mapping):
            raise ToolRegistryError("Tool registry entries must be objects")
        try:
            parsed.append(parse_tool_definition(entry))
        except ContractValidationError as exc:
            raise ToolRegistryError(str(exc)) from exc

    skills = tuple(parsed)
    validate_tool_registry(skills)
    return skills

def load_tool_registry(path: Path) -> tuple[ToolDefinition, ...]:
    """Load registry definitions from a YAML or JSON file."""

    data = path.read_text(encoding="utf-8")
    suffix = path.suffix.lower()
    if suffix in {".yaml", ".yml"}:
        payload = yaml.safe_load(data) or {}
    elif suffix == ".json":
        payload = json.loads(data)
    else:
        raise ToolRegistryError(f"Unsupported registry file extension: {path}")

    if not isinstance(payload, Mapping):
        raise ToolRegistryError("Registry root must be an object")
    return parse_tool_registry(payload)

def create_registry_snapshot(
    *,
    skills: tuple[ToolDefinition, ...],
    artifact_store: ArtifactStore,
) -> ToolRegistrySnapshot:
    """Create and persist an immutable registry snapshot artifact."""

    validate_tool_registry(skills)
    canonical = _canonical_registry_doc(skills)
    digest = compute_registry_digest(skills=skills)

    artifact = artifact_store.put_json(
        canonical,
        metadata={
            "name": "registry_snapshot.json",
            "producer": "tool:registry.snapshot",
            "labels": ["registry", "snapshot"],
            "digest": digest,
        },
    )

    return ToolRegistrySnapshot(
        digest=digest, artifact_ref=artifact.artifact_ref, skills=skills
    )

def load_registry_snapshot_from_artifact(
    *,
    artifact_ref: str,
    artifact_store: ArtifactStore,
) -> ToolRegistrySnapshot:
    """Load and validate a registry snapshot from artifact storage."""

    payload = artifact_store.get_json(artifact_ref)
    if not isinstance(payload, Mapping):
        raise ToolRegistryError("Registry snapshot artifact payload must be an object")

    skills = parse_tool_registry(payload)
    digest = _digest_registry_doc(_canonical_registry_doc(skills))
    return ToolRegistrySnapshot(digest=digest, artifact_ref=artifact_ref, skills=skills)

__all__ = [
    "ToolRegistryError",
    "ToolRegistrySnapshot",
    "compute_registry_digest",
    "create_registry_snapshot",
    "load_registry_snapshot_from_artifact",
    "load_tool_registry",
    "parse_tool_registry",
    "validate_tool_registry",
]
