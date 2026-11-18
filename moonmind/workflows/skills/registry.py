"""Registry compatibility wrappers for skill-named callers."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .artifact_store import ArtifactStore
from .tool_registry import ToolRegistryError, ToolRegistrySnapshot
from .tool_registry import create_registry_snapshot as create_contract_registry_snapshot
from .tool_registry import (
    load_registry_snapshot_from_artifact as load_contract_registry_snapshot_from_artifact,
)
from .tool_registry import load_tool_registry as load_contract_tool_registry
from .tool_registry import parse_tool_registry as parse_contract_tool_registry
from .tool_registry import validate_tool_registry as validate_contract_tool_registry

SkillRegistryError = ToolRegistryError
SkillRegistrySnapshot = ToolRegistrySnapshot

def load_tool_registry(path: Path) -> tuple[Any, ...]:
    """Load tool definitions from a YAML/JSON registry file."""

    return load_contract_tool_registry(path)

def parse_tool_registry(payload: Mapping[str, Any]) -> tuple[Any, ...]:
    """Parse untrusted tool registry payload."""

    return parse_contract_tool_registry(payload)

def validate_tool_registry(skills: tuple[Any, ...]) -> None:
    """Validate parsed tool definitions."""

    validate_contract_tool_registry(skills)

def load_skill_registry(path: Path) -> tuple[Any, ...]:
    """Compatibility wrapper for legacy skill-named callers."""

    return load_tool_registry(path)

def parse_skill_registry(payload: Mapping[str, Any]) -> tuple[Any, ...]:
    """Compatibility wrapper for legacy skill-named callers."""

    return parse_tool_registry(payload)

def validate_skill_registry(skills: tuple[Any, ...]) -> None:
    """Compatibility wrapper for legacy skill-named callers."""

    validate_tool_registry(skills)

def create_registry_snapshot(
    *,
    skills: tuple[Any, ...],
    artifact_store: ArtifactStore,
) -> ToolRegistrySnapshot:
    """Create immutable registry snapshot artifact for plan pinning."""

    return create_contract_registry_snapshot(
        skills=skills, artifact_store=artifact_store
    )

def load_registry_snapshot_from_artifact(
    *,
    artifact_ref: str,
    artifact_store: ArtifactStore,
) -> ToolRegistrySnapshot:
    """Load registry snapshot from immutable artifact storage."""

    return load_contract_registry_snapshot_from_artifact(
        artifact_ref=artifact_ref,
        artifact_store=artifact_store,
    )

__all__ = [
    "ToolRegistryError",
    "ToolRegistrySnapshot",
    "SkillRegistryError",
    "SkillRegistrySnapshot",
    "create_registry_snapshot",
    "load_registry_snapshot_from_artifact",
    "load_skill_registry",
    "load_tool_registry",
    "parse_skill_registry",
    "parse_tool_registry",
    "validate_skill_registry",
    "validate_tool_registry",
]
