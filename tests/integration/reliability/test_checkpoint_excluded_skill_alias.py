"""Archive membership is the safety boundary, including for Skill aliases."""

from __future__ import annotations

import io
import json
import shutil
import tarfile
from pathlib import Path

import pytest
from temporalio.testing import ActivityEnvironment

from moonmind.workflows.skills.artifact_store import InMemoryArtifactStore
from moonmind.workflows.temporal.activity_catalog import build_default_activity_catalog
from moonmind.workflows.temporal.activity_runtime import (
    TemporalSandboxActivities,
    build_activity_bindings,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]


@pytest.mark.parametrize("legacy_path", [False, True], ids=["locator", "historical-path"])
@pytest.mark.parametrize("alias_target", ["internal", "external", "dangling"])
async def test_excluded_skill_alias_capture_and_restore(
    tmp_path: Path,
    legacy_path: bool,
    alias_target: str,
) -> None:
    fixture_path = (
        Path(__file__).parent
        / "replays/checkpoint-excluded-skill-alias/manifest.json"
    )
    fixture = json.loads(fixture_path.read_text())
    repo = tmp_path / "temporal_sandbox/source/repo"
    for name, contents in fixture["workspace"]["files"].items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents)
    outside = tmp_path / "resolved-skills"
    outside.mkdir()
    (outside / "SKILL.md").write_text("must never enter the checkpoint\n")
    (repo / ".agents/skills/_shared").symlink_to(outside, target_is_directory=True)
    for name, target in fixture["workspace"]["symlinks"].items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if alias_target != "internal":
            target = str(
                outside if alias_target == "external" else tmp_path / "missing-skills"
            )
        path.symlink_to(target, target_is_directory=True)

    store = InMemoryArtifactStore()
    sandbox = TemporalSandboxActivities(workspace_root=tmp_path, artifact_store=store)
    binding = next(
        item
        for item in build_activity_bindings(
            build_default_activity_catalog(),
            sandbox_activities=sandbox,
            fleets=["sandbox"],
        )
        if item.activity_type == fixture["failedActivityType"]
    )
    request = {
        "identity": {
            "workflowId": "replay",
            "runId": "replay-run",
            "logicalStepId": "implement",
            "executionOrdinal": 1,
        },
        "boundary": "after_execution",
        "kind": "worktree_archive",
        "artifactNamespace": "checkpoint",
        "idempotencyKey": "capture-replay",
    }
    if legacy_path:
        request["workspacePath"] = str(repo)
    else:
        request["workspaceLocator"] = {
            "kind": "sandbox",
            "workspaceId": "source",
            "relativePath": "repo",
        }
    capture = await ActivityEnvironment().run(binding.handler, request)
    assert capture["status"] == fixture["expected"]["captureStatus"]
    archive_bytes = store.get_bytes(capture["workspace"]["archiveRef"])
    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as archive:
        assert archive.getnames() == ["result.txt"]
    manifest = json.loads(store.get_bytes(capture["workspace"]["manifestRef"]))
    assert [entry["path"] for entry in manifest["entries"]] == ["result.txt"]

    # Exclusion is lexical: an included alias into the excluded external tree
    # still escapes the workspace and must fail closed.
    (repo / "escape").symlink_to(".agents/skills/_shared", target_is_directory=True)
    rejected = await ActivityEnvironment().run(binding.handler, request)
    assert rejected["status"] == fixture["expected"]["includedEscapeStatus"]
    assert rejected["failureCode"] == "unsafe_checkpoint"

    shutil.rmtree(repo)
    restored = tmp_path / "temporal_sandbox/restored/repo"
    checkpoint_ref = store.put_bytes(
        json.dumps({"workspace": capture["workspace"]}).encode(),
        content_type="application/json",
    ).artifact_ref
    restore = await sandbox.workspace_apply_policy(
        {
            "identity": request["identity"],
            "workspacePolicy": "restore_pre_execution",
            "checkpointRef": checkpoint_ref,
            "targetWorkspaceRef": str(restored),
            "idempotencyKey": "restore-replay",
        }
    )
    assert restore["status"] == "applied"
    assert (
        (restored / "result.txt").read_text()
        == fixture["workspace"]["files"]["result.txt"]
    )
    assert not (restored / ".agents").exists()
    assert not (restored / ".gemini").exists()
    assert (outside / "SKILL.md").read_text() == "must never enter the checkpoint\n"
