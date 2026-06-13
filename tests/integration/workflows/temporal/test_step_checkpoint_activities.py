from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from moonmind.schemas.temporal_models import (
    STEP_EXECUTION_CHECKPOINT_CONTENT_TYPE,
    StepExecutionIdentityModel,
)
from moonmind.workflows.skills.artifact_store import InMemoryArtifactStore
from moonmind.workflows.temporal.activity_runtime import (
    TemporalCheckpointActivities,
    TemporalSandboxActivities,
)

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci, pytest.mark.asyncio]


def _identity() -> StepExecutionIdentityModel:
    return StepExecutionIdentityModel(
        workflowId="workflow-1",
        runId="run-1",
        logicalStepId="checkpoint-story",
        executionOrdinal=1,
    )


def _workspace_root(tmp_path: Path) -> Path:
    root = tmp_path / "workspaces"
    (root / "temporal_sandbox").mkdir(parents=True)
    return root


def _repo(root: Path) -> Path:
    repo = root / "temporal_sandbox" / "repo"
    repo.mkdir(parents=True)
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.PIPE)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True)
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, stdout=subprocess.PIPE)
    (repo / "README.md").write_text("base\nchanged\n", encoding="utf-8")
    return repo


def _assert_secret_absent(payload: object) -> None:
    rendered = json.dumps(payload, sort_keys=True)
    assert "GHCR_PULL_USER" not in rendered
    assert "GHCR_PULL_TOKEN" not in rendered
    assert "secret-token" not in rendered


async def test_capture_git_patch_and_create_checkpoint_write_compact_artifacts(
    tmp_path: Path,
) -> None:
    store = InMemoryArtifactStore()
    root = _workspace_root(tmp_path)
    repo = _repo(root)
    sandbox = TemporalSandboxActivities(workspace_root=root, artifact_store=store)
    checkpoint_activities = TemporalCheckpointActivities(artifact_store=store)

    capture = await sandbox.workspace_capture_checkpoint(
        {
            "identity": _identity().model_dump(by_alias=True),
            "boundary": "after_execution",
            "kind": "git_patch",
            "workspacePath": str(repo),
            "artifactNamespace": "checkpoint",
            "idempotencyKey": "idem-capture",
            "baseCommit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo)
            .decode()
            .strip(),
            "pullAuthContextRef": "artifact-pull-auth-context",
            "providerLeaseContextRef": "artifact-provider-context",
        }
    )

    assert capture["status"] == "captured"
    assert capture["workspace"]["kind"] == "git_patch"
    assert capture["workspace"]["patchRef"].startswith("art:sha256:")
    assert "changed" in store.get_bytes(capture["workspace"]["patchRef"]).decode()
    _assert_secret_absent(capture)

    created = await checkpoint_activities.step_checkpoint_create(
        {
            "identity": _identity().model_dump(by_alias=True),
            "boundary": "after_execution",
            "taskInputSnapshotRef": "artifact-input",
            "workspace": capture["workspace"],
            "createdAt": datetime(2026, 6, 13, 12, 0, tzinfo=UTC).isoformat(),
            "planDigest": "sha256:plan",
            "diagnosticRefs": capture["diagnosticRefs"],
            "idempotencyKey": "idem-create",
        }
    )

    assert created["contentType"] == STEP_EXECUTION_CHECKPOINT_CONTENT_TYPE
    payload = json.loads(store.get_bytes(created["checkpointRef"]).decode())
    assert payload["contentType"] == STEP_EXECUTION_CHECKPOINT_CONTENT_TYPE
    assert payload["workspace"]["patchRef"] == capture["workspace"]["patchRef"]
    assert "diff" not in payload["workspace"]


async def test_checkpoint_activity_failures_are_typed_and_secret_safe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GHCR_PULL_USER", "secret-user")
    monkeypatch.setenv("GHCR_PULL_TOKEN", "secret-token")
    store = InMemoryArtifactStore()
    root = _workspace_root(tmp_path)
    repo = _repo(root)
    (repo / ".agents").mkdir(parents=True)
    (repo / ".agents" / "skills").symlink_to(tmp_path)
    sandbox = TemporalSandboxActivities(workspace_root=root, artifact_store=store)
    checkpoint_activities = TemporalCheckpointActivities(artifact_store=store)

    unsafe = await sandbox.workspace_capture_checkpoint(
        {
            "identity": _identity().model_dump(by_alias=True),
            "boundary": "after_execution",
            "kind": "worktree_archive",
            "workspacePath": str(repo),
            "artifactNamespace": "checkpoint",
            "idempotencyKey": "idem-archive",
        }
    )
    assert unsafe["status"] == "unsafe"
    assert unsafe["failureCode"] == "unsafe_checkpoint"
    _assert_secret_absent(unsafe)

    missing = await checkpoint_activities.step_checkpoint_validate(
        {
            "checkpoint": {"checkpointId": "missing"},
            "expectedSource": _identity().model_dump(by_alias=True),
            "expectedTaskInputSnapshotRef": "artifact-input",
            "requiredArtifactRefs": ["artifact-missing"],
            "checkpointRef": "artifact-checkpoint",
        }
    )
    assert missing["valid"] is False
    assert missing["failureCode"] == "artifact_missing"

    policy = await sandbox.workspace_apply_policy(
        {
            "identity": _identity().model_dump(by_alias=True),
            "workspacePolicy": "start_from_last_passed_commit",
            "checkpointRef": "artifact-checkpoint",
            "checkpoint": {"workspace": {"kind": "git_patch", "baseCommit": "abc", "patchRef": "artifact-patch"}},
            "targetWorkspaceRef": str(repo),
            "expectedPlanDigest": "sha256:plan",
            "idempotencyKey": "idem-policy",
        }
    )
    assert policy["status"] == "rejected"
    assert policy["failureCode"] == "policy_incompatible"
