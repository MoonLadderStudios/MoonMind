"""Resolved skill artifacts cannot turn names or snapshot IDs into host paths."""

from datetime import UTC, datetime
import json

import pytest
from pydantic import ValidationError

from moonmind.schemas.agent_skill_models import (
    ResolvedSkillSet,
    RuntimeMaterializationMode,
)
from moonmind.services.skill_materialization import AgentSkillMaterializer
from moonmind.workflows.skills.run_projection import (
    SkillProjectionError,
    load_resolved_skillset,
)


def snapshot_payload(**updates):
    return {
        "snapshot_id": "snapshot-safe",
        "resolved_at": datetime.now(UTC).isoformat(),
        "skills": [
            {"skill_name": "safe-skill", "provenance": {"source_kind": "deployment"}}
        ],
        **updates,
    }


@pytest.mark.parametrize(
    "component",
    [
        "",
        ".",
        "..",
        "../other",
        "/tmp/other",
        "nested/name",
        "nested\\name",
        "nul\x00name",
    ],
)
@pytest.mark.parametrize("field", ["snapshot_id", "skill_name"])
def test_snapshot_artifact_rejects_path_components(field, component):
    payload = snapshot_payload()
    if field == "snapshot_id":
        payload[field] = component
    else:
        payload["skills"][0][field] = component
    with pytest.raises(ValidationError, match="single path component"):
        ResolvedSkillSet.model_validate(payload)


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["snapshot_id", "skill_name"])
async def test_materializer_rechecks_mutated_models_before_filesystem_io(
    tmp_path, field
):
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "saved-work.txt"
    marker.write_text("preserve")
    snapshot = ResolvedSkillSet.model_validate(snapshot_payload())
    if field == "snapshot_id":
        snapshot.snapshot_id = str(outside)
    else:
        snapshot.skills[0].skill_name = str(outside)
    materializer = AgentSkillMaterializer(str(tmp_path / "workspace"))
    with pytest.raises(ValueError, match="single path component"):
        await materializer.materialize(
            snapshot, "codex", RuntimeMaterializationMode.HYBRID
        )
    assert marker.read_text() == "preserve"
    assert not (tmp_path / "workspace").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit_backing", [False, True])
async def test_materializer_rejects_symlinked_active_parent(tmp_path, explicit_backing):
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "saved-work.txt"
    marker.write_text("preserve")
    workspace = tmp_path / "workspace"
    (workspace / "runtime").mkdir(parents=True)
    (workspace / "runtime" / "skills_active").symlink_to(
        outside, target_is_directory=True
    )
    backing = workspace / "runtime" / "skills_active" / "snapshot-safe"
    materializer = AgentSkillMaterializer(
        str(workspace), backing_root=str(backing) if explicit_backing else None
    )
    snapshot = ResolvedSkillSet.model_validate(snapshot_payload(skills=[]))
    with pytest.raises(RuntimeError, match="Failed to prepare"):
        await materializer.materialize(
            snapshot, "codex", RuntimeMaterializationMode.HYBRID
        )
    assert marker.read_text() == "preserve"
    assert sorted(path.name for path in outside.iterdir()) == ["saved-work.txt"]


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["snapshot_id", "skill_name"])
async def test_runtime_artifact_loader_rejects_unsafe_paths(field):
    payload = snapshot_payload()
    if field == "snapshot_id":
        payload[field] = "../../outside"
    else:
        payload["skills"][0][field] = "../../outside"

    class ArtifactService:
        async def read(self, **kwargs):
            return object(), json.dumps(payload).encode()

    with pytest.raises(SkillProjectionError, match="single path component"):
        await load_resolved_skillset(ArtifactService(), "artifact-snapshot")


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["materializer", "projection"])
async def test_skill_materialization_does_not_launder_symlinked_run_root(
    tmp_path, entrypoint
):
    from moonmind.workflows.skills.run_projection import materialize_run_skill_snapshot

    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "saved-work.txt"
    marker.write_text("preserve")
    root = tmp_path / "run"
    root.symlink_to(outside, target_is_directory=True)
    snapshot = ResolvedSkillSet.model_validate(snapshot_payload(skills=[]))
    with pytest.raises((RuntimeError, OSError), match="directory|symlink"):
        if entrypoint == "materializer":
            await AgentSkillMaterializer(str(root)).materialize(
                snapshot, "codex", RuntimeMaterializationMode.HYBRID
            )
        else:
            await materialize_run_skill_snapshot(
                workspace_path=root / "repo",
                run_root=root,
                runtime_id="codex",
                resolved_skillset=snapshot,
                artifact_service=None,
            )
    assert marker.read_text() == "preserve"
    assert sorted(path.name for path in outside.iterdir()) == ["saved-work.txt"]
