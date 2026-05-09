"""Per-run skill snapshot projection for managed runtimes.

Shared helpers used at the activity/launcher boundary to materialize an
immutable :class:`ResolvedSkillSet` snapshot into a run-scoped active backing
store, project it at ``.agents/skills``, verify the projection, and prepend the
canonical activation summary to the runtime instruction payload.

These helpers implement the contract in ``docs/Steps/SkillSystem.md`` §14 for
managed runtimes that launch through :class:`ManagedAgentAdapter` /
:class:`ManagedRuntimeLauncher` (notably ``claude_code``). The codex
managed-session path uses an equivalent flow inside
:class:`TemporalAgentRuntimeActivities`; the helpers here are the same logic
exposed at module scope so the launcher can consume them without depending on
the activity class.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from moonmind.schemas.agent_skill_models import (
    ResolvedSkillSet,
    RuntimeMaterializationMode,
)
from moonmind.services.skill_materialization import AgentSkillMaterializer
from moonmind.services.skills_on_demand import skills_on_demand_disabled_instruction
from moonmind.workflows.agent_skills.selection import selected_agent_skill
from moonmind.workflows.temporal.artifacts import TemporalArtifactError

AUTO_SKILL_SENTINEL = "auto"
ACTIVE_SKILL_SNAPSHOT_HEADER = "Active MoonMind skill snapshot:"


class SkillProjectionError(RuntimeError):
    """Raised when run-scoped skill projection cannot be completed safely.

    The launcher converts these into typed activity failures so the workflow
    sees a ``skill_projection_failed`` classification rather than a generic
    ``user_error`` after the runtime exits with a missing result artifact.
    """


async def load_resolved_skillset(
    artifact_service: Any,
    skillset_ref: str,
) -> ResolvedSkillSet:
    """Read and validate a :class:`ResolvedSkillSet` from artifact storage."""

    if artifact_service is None:
        raise SkillProjectionError(
            "skill projection failed before runtime launch: "
            "artifact service is required to read request.resolvedSkillsetRef"
        )
    try:
        _artifact, payload = await artifact_service.read(
            artifact_id=skillset_ref,
            principal="agent_runtime",
            allow_restricted_raw=True,
        )
        data = json.loads(payload.decode("utf-8"))
        return ResolvedSkillSet.model_validate(data)
    except (
        OSError,
        TemporalArtifactError,
        TypeError,
        ValueError,
        ValidationError,
    ) as exc:
        raise SkillProjectionError(
            f"skill projection failed before runtime launch: failed to read "
            f"resolvedSkillsetRef {skillset_ref}: {exc}"
        ) from exc


async def materialize_run_skill_snapshot(
    *,
    workspace_path: str | Path,
    run_root: str | Path,
    runtime_id: str,
    resolved_skillset: ResolvedSkillSet,
    artifact_service: Any,
    mode: RuntimeMaterializationMode = RuntimeMaterializationMode.HYBRID,
) -> dict[str, Any]:
    """Materialize a resolved snapshot for one run and return its metadata.

    The active backing store lives at
    ``<run_root>/runtime/skills_active/<snapshot_id>``. The canonical
    runtime-visible alias is created at ``<workspace_path>/.agents/skills``
    when projection is safe (see ``§14.4`` and ``§14.7`` collision policy).
    """

    workspace = Path(workspace_path).expanduser().resolve()
    root = Path(run_root).expanduser().resolve()
    backing_root = root / "runtime" / "skills_active" / resolved_skillset.snapshot_id
    source_preservation_root = root / "runtime" / "skill_sources" / "repo_agents_skills"

    materializer = AgentSkillMaterializer(
        workspace_root=str(workspace),
        artifact_service=artifact_service,
        backing_root=str(backing_root),
        source_preservation_root=str(source_preservation_root),
    )
    try:
        materialization = await materializer.materialize(
            resolved_skillset=resolved_skillset,
            runtime_id=runtime_id or "managed-runtime",
            mode=mode,
        )
    except (RuntimeError, OSError, ValueError, ValidationError) as exc:
        raise SkillProjectionError(
            f"skill projection failed before runtime launch: {exc}"
        ) from exc

    metadata = dict(materialization.metadata or {})
    metadata.setdefault("snapshotId", resolved_skillset.snapshot_id)
    if not str(metadata.get("visiblePath") or "").strip():
        raise SkillProjectionError(
            "skill projection failed before runtime launch: "
            "active skills visiblePath metadata is missing"
        )
    return metadata


async def verify_skill_projection(
    *,
    materialization_metadata: Mapping[str, Any],
    resolved_skillset: ResolvedSkillSet,
    selected_skill: str | None = None,
) -> None:
    """Fail before runtime launch if the runtime-visible projection is wrong.

    Checks the contract in ``docs/Steps/SkillSystem.md`` §14.7.2 hard
    invariants: the active visible path resolves, ``_manifest.json`` is
    present and matches the supplied snapshot id, the manifest lists the
    expected skill names, and (when a ``selected_skill`` is named) its
    ``SKILL.md`` is materialized. Raises :class:`SkillProjectionError` so the
    activity boundary can surface a ``skill_projection_failed`` classification.
    """

    visible_path_raw = str(materialization_metadata.get("visiblePath") or "").strip()
    if not visible_path_raw:
        raise SkillProjectionError(
            "skill projection failed before runtime launch: "
            "active skills visiblePath metadata is missing"
        )
    visible_skills_dir = Path(visible_path_raw).expanduser()
    if not visible_skills_dir.exists() or not visible_skills_dir.is_dir():
        raise SkillProjectionError(
            "skill projection failed before runtime launch: "
            f"active skills visiblePath is missing at {visible_skills_dir}"
        )

    manifest_path = visible_skills_dir / "_manifest.json"
    if not manifest_path.exists() or not manifest_path.is_file():
        raise SkillProjectionError(
            "skill projection failed before runtime launch: "
            f"active skill manifest is missing at {manifest_path}"
        )
    try:
        manifest_text = await asyncio.to_thread(
            manifest_path.read_text, encoding="utf-8"
        )
        manifest = json.loads(manifest_text)
        if not isinstance(manifest, Mapping):
            raise ValueError(f"manifest at {manifest_path} is not a mapping")
    except (OSError, TypeError, ValueError) as exc:
        raise SkillProjectionError(
            "skill projection failed before runtime launch: "
            f"active skill manifest is unreadable at {manifest_path}: {exc}"
        ) from exc

    snapshot_id = str(manifest.get("snapshot_id") or "").strip()
    if snapshot_id != resolved_skillset.snapshot_id:
        raise SkillProjectionError(
            "skill projection failed before runtime launch: "
            "active skill manifest snapshot_id does not match resolvedSkillsetRef "
            f"({snapshot_id!r} != {resolved_skillset.snapshot_id!r})"
        )

    manifest_skills = {
        str(entry.get("name") or "").strip()
        for entry in manifest.get("skills", [])
        if isinstance(entry, Mapping)
    }
    expected_skills = {entry.skill_name for entry in resolved_skillset.skills}
    missing = expected_skills - manifest_skills
    if missing:
        raise SkillProjectionError(
            "skill projection failed before runtime launch: "
            "active skill manifest is missing expected skills: "
            f"{sorted(missing)}"
        )

    if selected_skill and selected_skill != AUTO_SKILL_SENTINEL:
        if selected_skill not in manifest_skills:
            raise SkillProjectionError(
                "skill projection failed before runtime launch: "
                f"active skill manifest does not include selected skill '{selected_skill}'"
            )
        selected_skill_doc = visible_skills_dir / selected_skill / "SKILL.md"
        if not selected_skill_doc.exists() or not selected_skill_doc.is_file():
            raise SkillProjectionError(
                "skill projection failed before runtime launch: "
                f"selected skill '{selected_skill}' is missing {selected_skill_doc}"
            )


def build_skill_activation_summary(
    *,
    parameters: Mapping[str, Any] | None,
    materialization_metadata: Mapping[str, Any] | None,
    skills_on_demand_enabled: bool,
) -> str:
    """Render the canonical inline activation summary for a runtime turn.

    Returns an empty string when no selected skill is in scope. The summary
    follows ``docs/Steps/SkillSystem.md`` §14.5: active skill names, the
    materialized ``visiblePath``, first-read hint at ``SKILL.md``, and the
    repo-authored ``.agents/skills`` non-mutation rule when the canonical
    alias is unavailable.
    """

    if not materialization_metadata:
        return ""
    selected_skill = selected_agent_skill(parameters)
    if not selected_skill or selected_skill == AUTO_SKILL_SENTINEL:
        return ""

    visible_path = str(materialization_metadata.get("visiblePath") or "").strip()
    skill_doc = (
        str(Path(visible_path) / selected_skill / "SKILL.md") if visible_path else ""
    )
    alias_available = bool(materialization_metadata.get("canonicalAliasAvailable"))
    block = (
        f"{ACTIVE_SKILL_SNAPSHOT_HEADER}\n"
        f"- Selected skill: {selected_skill}\n"
        f"- Full active MoonMind skill content is available at: {visible_path}\n"
        f"- Read `{skill_doc}` first and follow that active snapshot.\n"
        "- Do not discover skills from repo-local or local-only source folders during execution.\n"
    )
    disabled_instruction = skills_on_demand_disabled_instruction(
        enabled=skills_on_demand_enabled
    )
    if disabled_instruction:
        block = block + f"{disabled_instruction}\n"
    if not alias_available:
        block = block + (
            "- The repository also contains `.agents/skills`; that directory "
            "is repo-authored source and must not be modified or treated as "
            "the active selected skill snapshot.\n"
        )
    return block + "\n"


def prepend_skill_activation_summary(
    instructions: str,
    *,
    parameters: Mapping[str, Any] | None,
    materialization_metadata: Mapping[str, Any] | None,
    skills_on_demand_enabled: bool,
) -> str:
    """Prepend the activation summary to ``instructions`` if applicable."""

    if ACTIVE_SKILL_SNAPSHOT_HEADER in instructions:
        return instructions
    block = build_skill_activation_summary(
        parameters=parameters,
        materialization_metadata=materialization_metadata,
        skills_on_demand_enabled=skills_on_demand_enabled,
    )
    if not block:
        return instructions
    return block + instructions


__all__ = [
    "AUTO_SKILL_SENTINEL",
    "ACTIVE_SKILL_SNAPSHOT_HEADER",
    "SkillProjectionError",
    "build_skill_activation_summary",
    "load_resolved_skillset",
    "materialize_run_skill_snapshot",
    "prepend_skill_activation_summary",
    "verify_skill_projection",
]
