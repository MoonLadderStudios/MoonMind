"""Verify saved-work recovery against its producer-owned immutable closure."""

from __future__ import annotations

from typing import Any

from moonmind.schemas.saved_work_models import saved_work_artifact_dependencies
from moonmind.schemas.saved_work_retention import (
    SAVED_WORK_MAX_DEPENDENCIES,
    SAVED_WORK_MAX_GRAPH_DEPTH,
)
from moonmind.workflows.temporal.artifacts import (
    ArtifactAccessContext,
    ArtifactDependency,
    TemporalArtifactNotFoundError,
    TemporalArtifactStateError,
)

SAVED_WORK_DEPENDENCY_CONTENT_KINDS = {
    "application/vnd.moonmind.saved-work-manifest+json;version=1": "saved_work_manifest",
    "application/vnd.moonmind.managed-workspace-checkpoint-manifest+json;version=1": "checkpoint_manifest",
    "application/vnd.moonmind.step-execution-checkpoint+json;version=1": "step_execution_checkpoint",
}
SAVED_WORK_DEPENDENCY_KINDS = frozenset(SAVED_WORK_DEPENDENCY_CONTENT_KINDS.values())


async def verify_saved_work_dependencies(
    service: Any,
    *,
    artifact: Any,
    payload: bytes,
    access: ArtifactAccessContext,
    _ancestors: frozenset[str] = frozenset(),
    _visited: set[str] | None = None,
) -> Any:
    """Use the frozen proof, decoding only retained roots with no seal.

    Callers first read the root under their existing authority. Historical
    decoding uses those exact bytes and the same scope for every dependency.
    It never installs grants or substitutes a current Skill/workspace revision.
    The existing service's explicit transaction ownership controls the seal.
    """
    kind = SAVED_WORK_DEPENDENCY_CONTENT_KINDS.get(str(artifact.content_type)) or str(
        (artifact.metadata_json or {}).get("artifact_kind") or ""
    )
    if kind not in SAVED_WORK_DEPENDENCY_KINDS:
        return None
    closure = await service.verified_dependencies(
        root_artifact_id=artifact.artifact_id, access=access
    )
    if closure is not None:
        return closure
    # Retained pre-contract roots only. A malformed/completed seal raises
    # above and must never be repaired by rediscovering JSON references.
    if (
        artifact.artifact_id in _ancestors
        or len(_ancestors) >= SAVED_WORK_MAX_GRAPH_DEPTH
    ):
        raise TemporalArtifactStateError(
            "Legacy saved-work dependency cycle/depth limit"
        )
    visited = _visited if _visited is not None else set()
    visited.add(artifact.artifact_id)
    if len(visited) > SAVED_WORK_MAX_DEPENDENCIES:
        raise TemporalArtifactStateError("Legacy saved-work dependency count limit")
    ancestors = _ancestors | {artifact.artifact_id}
    descriptors = saved_work_artifact_dependencies(kind, payload)
    visited.update(item["artifact_id"] for item in descriptors)
    if len(visited) > SAVED_WORK_MAX_DEPENDENCIES:
        raise TemporalArtifactStateError("Legacy saved-work dependency count limit")
    for descriptor in descriptors:
        try:
            child, *_ = await service.get_metadata(
                artifact_id=descriptor["artifact_id"], access=access
            )
        except TemporalArtifactNotFoundError:
            if descriptor["required"]:
                raise
            continue
        child_kind = SAVED_WORK_DEPENDENCY_CONTENT_KINDS.get(
            str(child.content_type)
        ) or str((child.metadata_json or {}).get("artifact_kind") or "")
        if child_kind in SAVED_WORK_DEPENDENCY_KINDS:
            _, child_payload = await service.read(
                artifact_id=child.artifact_id, access=access
            )
            await verify_saved_work_dependencies(
                service,
                artifact=child,
                payload=child_payload,
                access=access,
                _ancestors=ancestors,
                _visited=visited,
            )
        if descriptor["required"] and descriptor["sha256"] is None:
            descriptor["sha256"] = child.sha256
    return await service.record_verified_dependencies(
        root_artifact_id=artifact.artifact_id,
        dependencies=[ArtifactDependency(**item) for item in descriptors],
        access=access,
    )
