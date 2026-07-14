from __future__ import annotations

import json
import shutil
import subprocess
import tarfile
from io import BytesIO
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from moonmind.schemas.temporal_models import (
    STEP_EXECUTION_CHECKPOINT_CONTENT_TYPE,
    StepExecutionIdentityModel,
)
from moonmind.schemas.workspace_locator_models import WorkspaceLocatorResolutionError
from moonmind.workflows.skills.artifact_store import InMemoryArtifactStore
from moonmind.workflows.temporal.activity_runtime import (
    TemporalCheckpointActivities,
    TemporalSandboxActivities,
)
from moonmind.workflows.temporal.workflows import run as run_module
from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow

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


def _repo(root: Path, *, workspace_id: str | None = None) -> Path:
    repo = root / "temporal_sandbox"
    repo = repo / workspace_id / "repo" if workspace_id else repo / "repo"
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


def _recovery_source_with_checkpoint_payload() -> dict[str, object]:
    return {
        "sourceWorkflowId": "source-workflow",
        "sourceRunId": "source-run",
        "sourceTaskInputSnapshotRef": "artifact-input",
        "sourcePlanDigest": "sha256:plan",
        "failedStepId": "implement",
        "failedStepExecution": 2,
        "recoveryCheckpointRef": "artifact-checkpoint",
        "failedRunRecoveryManifestRef": "artifact-manifest",
        "recoveryWorkspace": {
            "checkpointRef": "artifact-checkpoint",
            "targetWorkspaceRef": "workspace-target",
            "workspacePolicy": "apply_previous_execution_diff_to_clean_baseline",
            "checkpoint": {
                "contentType": STEP_EXECUTION_CHECKPOINT_CONTENT_TYPE,
                "schemaVersion": "v1",
                "checkpointId": (
                    "source-workflow:source-run:implement:execution:2:"
                    "checkpoint:before_execution"
                ),
                "checkpointKind": "step_boundary",
                "boundary": "before_execution",
                "source": {
                    "workflowId": "source-workflow",
                    "runId": "source-run",
                    "logicalStepId": "implement",
                    "executionOrdinal": 2,
                },
                "taskInputSnapshotRef": "artifact-input",
                "planDigest": "sha256:plan",
                "workspace": {
                    "kind": "git_patch",
                    "baseCommit": "abc123",
                    "patchRef": "artifact-patch",
                },
                "createdAt": "2026-06-13T12:00:00+00:00",
            },
        },
    }


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


async def test_create_checkpoint_is_idempotent_for_step_boundary_key(
    tmp_path: Path,
) -> None:
    store = InMemoryArtifactStore()
    root = _workspace_root(tmp_path)
    repo = _repo(root)
    sandbox = TemporalSandboxActivities(workspace_root=root, artifact_store=store)
    checkpoint_activities = TemporalCheckpointActivities(artifact_store=store)
    base_commit = (
        subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo)
        .decode()
        .strip()
    )

    capture = await sandbox.workspace_capture_checkpoint(
        {
            "identity": _identity().model_dump(by_alias=True),
            "boundary": "after_execution",
            "kind": "git_patch",
            "workspacePath": str(repo),
            "artifactNamespace": "checkpoint",
            "idempotencyKey": (
                "workflow-1:run-1:checkpoint-story:execution:1:"
                "checkpoint:after_execution"
            ),
            "baseCommit": base_commit,
        }
    )

    request = {
        "identity": _identity().model_dump(by_alias=True),
        "boundary": "after_execution",
        "taskInputSnapshotRef": "artifact-input",
        "workspace": capture["workspace"],
        "createdAt": datetime(2026, 6, 13, 12, 0, tzinfo=UTC).isoformat(),
        "planDigest": "sha256:plan",
        "idempotencyKey": (
            "workflow-1:run-1:checkpoint-story:execution:1:"
            "checkpoint:after_execution"
        ),
    }

    first = await checkpoint_activities.step_checkpoint_create(request)
    second = await checkpoint_activities.step_checkpoint_create(request)

    assert first == second
    assert first["checkpointRef"] == second["checkpointRef"]
    assert first["idempotencyKey"].endswith(":checkpoint:after_execution")


async def test_capture_all_checkpoint_kinds_as_compact_refs(tmp_path: Path) -> None:
    store = InMemoryArtifactStore()
    root = _workspace_root(tmp_path)
    repo = _repo(root)
    base_commit = (
        subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo)
        .decode()
        .strip()
    )
    sandbox = TemporalSandboxActivities(workspace_root=root, artifact_store=store)

    common = {
        "identity": _identity().model_dump(by_alias=True),
        "boundary": "after_execution",
        "workspacePath": str(repo),
        "workspaceRootRef": "artifact-workspace-root",
        "artifactNamespace": "checkpoint",
    }
    cases = [
        ("git_commit", {"baseCommit": base_commit}, ("headCommit",)),
        ("git_patch", {"baseCommit": base_commit}, ("patchRef", "manifestRef")),
        ("ephemeral_workspace_ref", {}, ("workspaceArtifactRef",)),
        ("worktree_archive", {}, ("archiveRef", "manifestRef")),
        ("external_state_ref", {}, ("externalStateRef",)),
    ]

    for kind, extra, required_refs in cases:
        capture = await sandbox.workspace_capture_checkpoint(
            {
                **common,
                **extra,
                "kind": kind,
                "idempotencyKey": f"idem-{kind}",
            }
        )
        assert capture["status"] == "captured"
        assert capture["workspace"]["kind"] == kind
        for ref_name in required_refs:
            assert capture["workspace"][ref_name]
        rendered = json.dumps(capture, sort_keys=True)
        assert str(repo) not in rendered

    external_capture = await sandbox.workspace_capture_checkpoint(
        {
            "identity": _identity().model_dump(by_alias=True),
            "boundary": "after_execution",
            "kind": "external_state_ref",
            "externalStateRef": "artifact://omnigent/external-state",
            "artifactNamespace": "checkpoint",
            "idempotencyKey": "idem-external-state-ref",
        }
    )
    assert external_capture["status"] == "captured"
    assert external_capture["workspace"]["kind"] == "external_state_ref"
    external_payload = json.loads(
        store.get_bytes(external_capture["workspace"]["externalStateRef"])
    )
    assert external_payload["sourceRef"] == "artifact://omnigent/external-state"

    archive_capture = await sandbox.workspace_capture_checkpoint(
        {
            **common,
            "kind": "worktree_archive",
            "idempotencyKey": "idem-archive-content",
        }
    )
    archive_bytes = store.get_bytes(archive_capture["workspace"]["archiveRef"])
    with tarfile.open(fileobj=BytesIO(archive_bytes), mode="r:gz") as archive:
        names = archive.getnames()
    assert "README.md" in names
    assert ".agents/skills" not in names


async def test_checkpoint_capture_preserves_internal_symlinks(tmp_path: Path) -> None:
    store = InMemoryArtifactStore()
    root = _workspace_root(tmp_path)
    repo = _repo(root)
    (repo / "target.txt").write_text("target\n", encoding="utf-8")
    (repo / "link.txt").symlink_to("target.txt")
    sandbox = TemporalSandboxActivities(workspace_root=root, artifact_store=store)

    capture = await sandbox.workspace_capture_checkpoint(
        {
            "identity": _identity().model_dump(by_alias=True),
            "boundary": "after_execution",
            "kind": "worktree_archive",
            "workspacePath": str(repo),
            "artifactNamespace": "checkpoint",
            "idempotencyKey": "idem-archive-symlink",
        }
    )

    archive_bytes = store.get_bytes(capture["workspace"]["archiveRef"])
    with tarfile.open(fileobj=BytesIO(archive_bytes), mode="r:gz") as archive:
        link_info = archive.getmember("link.txt")
    assert link_info.issym()
    assert link_info.linkname == "target.txt"


async def test_git_patch_checkpoint_rejects_unsupported_file_options(
    tmp_path: Path,
) -> None:
    store = InMemoryArtifactStore()
    root = _workspace_root(tmp_path)
    repo = _repo(root)
    sandbox = TemporalSandboxActivities(workspace_root=root, artifact_store=store)
    base_commit = (
        subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo)
        .decode()
        .strip()
    )

    with pytest.raises(
        RuntimeError,
        match="git_patch checkpoint kind does not support including untracked or ignored files",
    ):
        await sandbox.workspace_capture_checkpoint(
            {
                "identity": _identity().model_dump(by_alias=True),
                "boundary": "after_execution",
                "kind": "git_patch",
                "workspacePath": str(repo),
                "artifactNamespace": "checkpoint",
                "idempotencyKey": "idem-git-patch-options",
                "baseCommit": base_commit,
                "includeUntracked": True,
            }
        )


async def test_workspace_classify_git_effect_accepts_workspace_root_ref(
    tmp_path: Path,
) -> None:
    store = InMemoryArtifactStore()
    root = _workspace_root(tmp_path)
    repo = _repo(root)
    sandbox = TemporalSandboxActivities(workspace_root=root, artifact_store=store)

    result = await sandbox.workspace_classify_git_effect(
        {
            "workspaceRootRef": str(repo),
        }
    )

    assert result["status"] == "dirty"
    assert result["diagnosticRefs"]


async def test_workspace_locator_kinds_are_enforced_for_git_effect(
    tmp_path: Path,
) -> None:
    store = InMemoryArtifactStore()
    root = _workspace_root(tmp_path)
    repo = _repo(root, workspace_id="locator-workspace")
    sandbox = TemporalSandboxActivities(workspace_root=root, artifact_store=store)

    result = await sandbox.workspace_classify_git_effect(
        {
            "workspaceLocator": {
                "kind": "sandbox",
                "workspaceId": "locator-workspace",
                "relativePath": "repo",
            }
        }
    )
    assert result["status"] == "dirty"

    for locator, code in (
        (
            {"kind": "managed_runtime", "runtimeId": "codex", "agentRunId": "run-1"},
            "WORKSPACE_AUTHORITY_MISMATCH",
        ),
        (
            {"kind": "external_state", "artifactRef": "art:sha256:evidence"},
            "WORKSPACE_LOCATOR_UNSUPPORTED",
        ),
    ):
        with pytest.raises(WorkspaceLocatorResolutionError) as exc_info:
            await sandbox.workspace_classify_git_effect({"workspaceLocator": locator})
        assert exc_info.value.code == code


async def test_workspace_locator_kinds_are_enforced_for_checkpoint_capture(
    tmp_path: Path,
) -> None:
    store = InMemoryArtifactStore()
    root = _workspace_root(tmp_path)
    _repo(root, workspace_id="capture-workspace")
    sandbox = TemporalSandboxActivities(workspace_root=root, artifact_store=store)
    base_request = {
        "identity": _identity().model_dump(by_alias=True),
        "boundary": "after_execution",
        "artifactNamespace": "checkpoint",
        "idempotencyKey": "locator-capture",
    }

    captured = await sandbox.workspace_capture_checkpoint(
        dict(
            base_request,
            kind="git_commit",
            workspaceLocator={
                "kind": "sandbox",
                "workspaceId": "capture-workspace",
                "relativePath": "repo",
            },
        )
    )
    assert captured["status"] == "captured"

    external = await sandbox.workspace_capture_checkpoint(
        dict(
            base_request,
            kind="external_state_ref",
            idempotencyKey="locator-capture-external",
            workspaceLocator={
                "kind": "external_state",
                "artifactRef": "art:sha256:evidence",
            },
        )
    )
    assert external["status"] == "captured"

    with pytest.raises(WorkspaceLocatorResolutionError) as exc_info:
        await sandbox.workspace_capture_checkpoint(
            dict(
                base_request,
                kind="git_commit",
                idempotencyKey="locator-capture-managed",
                workspaceLocator={
                    "kind": "managed_runtime",
                    "runtimeId": "codex",
                    "agentRunId": "run-1",
                },
            )
        )
    assert exc_info.value.code == "WORKSPACE_AUTHORITY_MISMATCH"


async def test_workspace_locator_kinds_are_enforced_for_recovery_apply(
    tmp_path: Path,
) -> None:
    store = InMemoryArtifactStore()
    root = _workspace_root(tmp_path)
    sandbox = TemporalSandboxActivities(workspace_root=root, artifact_store=store)
    checkpoint_ref = _checkpoint_artifact(
        store,
        workspace={
            "kind": "worktree_archive",
            "archiveRef": store.put_bytes(
                b"invalid", content_type="application/octet-stream"
            ).artifact_ref,
        },
    )
    base_request = {
        "identity": _identity().model_dump(by_alias=True),
        "workspacePolicy": "restore_pre_execution",
        "checkpointRef": checkpoint_ref,
        "checkpoint": {},
        "expectedPlanDigest": "sha256:plan",
        "idempotencyKey": "locator-recovery",
    }

    sandbox_request = dict(
        base_request,
        targetWorkspaceLocator={
            "kind": "sandbox",
            "workspaceId": "recovery-workspace",
            "relativePath": "repo",
        },
    )
    result = await sandbox.workspace_apply_policy(sandbox_request)
    assert result["status"] == "rejected"
    assert result["failureCode"] == "artifact_corrupted"

    for locator, code in (
        (
            {"kind": "managed_runtime", "runtimeId": "codex", "agentRunId": "run-1"},
            "WORKSPACE_AUTHORITY_MISMATCH",
        ),
        (
            {"kind": "external_state", "artifactRef": "art:sha256:evidence"},
            "WORKSPACE_LOCATOR_UNSUPPORTED",
        ),
    ):
        request = dict(base_request, targetWorkspaceLocator=locator)
        request["idempotencyKey"] = f"locator-recovery-{locator['kind']}"
        with pytest.raises(WorkspaceLocatorResolutionError) as exc_info:
            await sandbox.workspace_apply_policy(request)
        assert exc_info.value.code == code


async def test_legacy_workspace_path_usage_emits_compatibility_metric(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    emitter = Mock()
    monkeypatch.setattr(
        "moonmind.workflows.temporal.activity_runtime.get_metrics_emitter",
        lambda: emitter,
    )
    store = InMemoryArtifactStore()
    root = _workspace_root(tmp_path)
    repo = _repo(root)
    sandbox = TemporalSandboxActivities(workspace_root=root, artifact_store=store)

    await sandbox.workspace_classify_git_effect({"workspacePath": str(repo)})

    emitter.increment.assert_called_once_with(
        "workspace_locator.compatibility_path_usage",
        tags={"operation": "classify_git_effect"},
    )


async def test_workspace_apply_policy_handles_degraded_checkpoint_payload(
    tmp_path: Path,
) -> None:
    store = InMemoryArtifactStore()
    root = _workspace_root(tmp_path)
    repo = _repo(root)
    sandbox = TemporalSandboxActivities(workspace_root=root, artifact_store=store)

    policy = await sandbox.workspace_apply_policy(
        {
            "identity": _identity().model_dump(by_alias=True),
            "workspacePolicy": "start_from_last_passed_commit",
            "checkpointRef": "artifact-checkpoint",
            "checkpoint": {"workspace": "degraded"},
            "targetWorkspaceRef": str(repo),
            "expectedPlanDigest": "sha256:plan",
            "idempotencyKey": "idem-degraded-policy",
        }
    )

    assert policy["status"] == "rejected"
    assert policy["failureCode"] == "artifact_missing"
    diagnostic = json.loads(store.get_bytes(policy["diagnosticRefs"][0]).decode())
    assert diagnostic["status"] == "blocked"
    assert diagnostic["failureCode"] == "artifact_missing"
    assert diagnostic["recommendedNextAction"]


def _checkpoint_artifact(
    store: InMemoryArtifactStore,
    *,
    workspace: dict[str, object],
    boundary: str = "after_execution",
) -> str:
    payload = {
        "contentType": STEP_EXECUTION_CHECKPOINT_CONTENT_TYPE,
        "schemaVersion": "v1",
        "checkpointId": (
            "workflow-1:run-1:checkpoint-story:execution:1:"
            f"checkpoint:{boundary}"
        ),
        "checkpointKind": "step_boundary",
        "boundary": boundary,
        "source": _identity().model_dump(by_alias=True),
        "taskInputSnapshotRef": "artifact-input",
        "planDigest": "sha256:plan",
        "workspace": workspace,
        "createdAt": datetime(2026, 6, 13, 12, 0, tzinfo=UTC).isoformat(),
    }
    artifact = store.put_json(payload, metadata={"artifact_kind": "checkpoint"})
    return artifact.artifact_ref


async def test_workspace_apply_policy_applies_all_canonical_policies_and_is_idempotent(
    tmp_path: Path,
) -> None:
    store = InMemoryArtifactStore()
    root = _workspace_root(tmp_path)
    repo = _repo(root)
    base_commit = (
        subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo)
        .decode()
        .strip()
    )
    sandbox = TemporalSandboxActivities(workspace_root=root, artifact_store=store)
    archive_capture = await sandbox.workspace_capture_checkpoint(
        {
            "identity": _identity().model_dump(by_alias=True),
            "boundary": "after_execution",
            "kind": "worktree_archive",
            "workspacePath": str(repo),
            "artifactNamespace": "checkpoint",
            "idempotencyKey": "idem-archive-policy-capture",
        }
    )
    patch_capture = await sandbox.workspace_capture_checkpoint(
        {
            "identity": _identity().model_dump(by_alias=True),
            "boundary": "after_execution",
            "kind": "git_patch",
            "workspacePath": str(repo),
            "artifactNamespace": "checkpoint",
            "idempotencyKey": "idem-patch-policy-capture",
            "baseCommit": base_commit,
        }
    )
    cases = [
        (
            "restore_pre_execution",
            archive_capture["workspace"],
            "base\nchanged\n",
        ),
        (
            "continue_from_previous_execution",
            {
                "kind": "ephemeral_workspace_ref",
                "workspaceRef": str(repo),
            },
            "base\nchanged\n",
        ),
        (
            "apply_previous_execution_diff_to_clean_baseline",
            {
                **patch_capture["workspace"],
                "workspaceRef": str(repo),
            },
            "base\nchanged\n",
        ),
        (
            "start_from_last_passed_commit",
            {
                "kind": "git_commit",
                "headCommit": base_commit,
                "workspaceRef": str(repo),
            },
            "base\n",
        ),
        (
            "fresh_branch_from_source",
            {
                "kind": "git_commit",
                "headCommit": base_commit,
                "workspaceRef": str(repo),
            },
            "base\nchanged\n",
        ),
    ]

    for policy, workspace, expected_readme in cases:
        target = root / "temporal_sandbox" / f"target-{policy}"
        checkpoint_ref = _checkpoint_artifact(store, workspace=workspace)
        request = {
            "identity": _identity().model_dump(by_alias=True),
            "workspacePolicy": policy,
            "checkpointRef": checkpoint_ref,
            "checkpoint": {},
            "targetWorkspaceRef": str(target),
            "expectedPlanDigest": "sha256:plan",
            "idempotencyKey": f"MM-824:{policy}:idem",
        }

        first = await sandbox.workspace_apply_policy(request)
        second = await sandbox.workspace_apply_policy(request)

        assert first["status"] == "applied"
        assert second == first
        assert first["workspaceRef"] == str(target)
        assert (target / "README.md").read_text(encoding="utf-8") == expected_readme


async def test_workspace_apply_policy_applies_canonical_git_patch_from_target_repo(
    tmp_path: Path,
) -> None:
    store = InMemoryArtifactStore()
    root = _workspace_root(tmp_path)
    repo = _repo(root)
    base_commit = (
        subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo)
        .decode()
        .strip()
    )
    sandbox = TemporalSandboxActivities(workspace_root=root, artifact_store=store)
    patch_capture = await sandbox.workspace_capture_checkpoint(
        {
            "identity": _identity().model_dump(by_alias=True),
            "boundary": "after_execution",
            "kind": "git_patch",
            "workspacePath": str(repo),
            "artifactNamespace": "checkpoint",
            "idempotencyKey": "idem-canonical-patch-policy-capture",
            "baseCommit": base_commit,
        }
    )
    target = root / "temporal_sandbox" / "canonical-patch-target"
    shutil.copytree(repo, target, symlinks=True)
    subprocess.run(
        ["git", "checkout", "--force", "--detach", base_commit],
        cwd=target,
        check=True,
        stdout=subprocess.PIPE,
    )
    checkpoint_ref = _checkpoint_artifact(
        store,
        workspace=patch_capture["workspace"],
    )

    policy = await sandbox.workspace_apply_policy(
        {
            "identity": _identity().model_dump(by_alias=True),
            "workspacePolicy": "apply_previous_execution_diff_to_clean_baseline",
            "checkpointRef": checkpoint_ref,
            "checkpoint": {},
            "targetWorkspaceRef": str(target),
            "expectedPlanDigest": "sha256:plan",
            "idempotencyKey": "MM-824:canonical-patch-policy",
        }
    )

    assert policy["status"] == "applied"
    assert (target / "README.md").read_text(encoding="utf-8") == "base\nchanged\n"


async def test_workspace_apply_policy_copies_to_nested_target_parent(
    tmp_path: Path,
) -> None:
    store = InMemoryArtifactStore()
    root = _workspace_root(tmp_path)
    repo = _repo(root)
    checkpoint_ref = _checkpoint_artifact(
        store,
        workspace={
            "kind": "ephemeral_workspace_ref",
            "workspaceRef": str(repo),
        },
    )
    target = root / "temporal_sandbox" / "missing" / "nested-target"
    sandbox = TemporalSandboxActivities(workspace_root=root, artifact_store=store)

    policy = await sandbox.workspace_apply_policy(
        {
            "identity": _identity().model_dump(by_alias=True),
            "workspacePolicy": "continue_from_previous_execution",
            "checkpointRef": checkpoint_ref,
            "checkpoint": {},
            "targetWorkspaceRef": str(target),
            "idempotencyKey": "MM-824:nested-copy-target",
        }
    )

    assert policy["status"] == "applied"
    assert (target / "README.md").read_text(encoding="utf-8") == "base\nchanged\n"


async def test_workspace_apply_policy_accepts_snake_case_workspace_ref(
    tmp_path: Path,
) -> None:
    store = InMemoryArtifactStore()
    root = _workspace_root(tmp_path)
    repo = _repo(root)
    checkpoint_ref = _checkpoint_artifact(
        store,
        workspace={
            "kind": "ephemeral_workspace_ref",
            "workspace_ref": str(repo),
        },
    )
    target = root / "temporal_sandbox" / "snake-case-workspace-ref"
    sandbox = TemporalSandboxActivities(workspace_root=root, artifact_store=store)

    policy = await sandbox.workspace_apply_policy(
        {
            "identity": _identity().model_dump(by_alias=True),
            "workspacePolicy": "continue_from_previous_execution",
            "checkpointRef": checkpoint_ref,
            "checkpoint": {},
            "targetWorkspaceRef": str(target),
            "idempotencyKey": "MM-1079:snake-case-workspace-ref",
        }
    )

    assert policy["status"] == "applied"
    assert (target / "README.md").read_text(encoding="utf-8") == "base\nchanged\n"


async def test_workspace_apply_policy_rejects_artifact_backed_workspace_ref_ambiguity(
    tmp_path: Path,
) -> None:
    store = InMemoryArtifactStore()
    root = _workspace_root(tmp_path)
    repo = _repo(root)
    sandbox = TemporalSandboxActivities(workspace_root=root, artifact_store=store)
    capture = await sandbox.workspace_capture_checkpoint(
        {
            "identity": _identity().model_dump(by_alias=True),
            "boundary": "after_execution",
            "kind": "ephemeral_workspace_ref",
            "workspacePath": str(repo),
            "workspaceRootRef": "artifact-workspace-root",
            "artifactNamespace": "checkpoint",
            "idempotencyKey": "MM-1079:MM-1074:capture-boundary",
        }
    )
    checkpoint_ref = _checkpoint_artifact(store, workspace=capture["workspace"])
    target = root / "temporal_sandbox" / "artifact-backed-target"

    policy = await sandbox.workspace_apply_policy(
        {
            "identity": _identity().model_dump(by_alias=True),
            "workspacePolicy": "continue_from_previous_execution",
            "checkpointRef": checkpoint_ref,
            "checkpoint": {},
            "targetWorkspaceRef": str(target),
            "idempotencyKey": "MM-1079:MM-1074:apply-boundary",
        }
    )

    assert policy["status"] == "rejected"
    assert policy["failureCode"] == "workspace_incompatible"
    assert "artifact-backed ephemeral workspace evidence" in policy["summary"]
    assert not target.exists()
    diagnostic = json.loads(store.get_bytes(policy["diagnosticRefs"][0]).decode())
    assert diagnostic["checkpointKind"] == "ephemeral_workspace_ref"
    assert (
        diagnostic["providerSessionCorrelation"]["workspaceArtifactRef"]
        == capture["workspace"]["workspaceArtifactRef"]
    )
    assert "workspaceRef" not in capture["workspace"]


async def test_workspace_apply_policy_rejects_external_ref_with_omnigent_diagnostic(
    tmp_path: Path,
) -> None:
    store = InMemoryArtifactStore()
    root = _workspace_root(tmp_path)
    target = root / "temporal_sandbox" / "external-state-target"
    checkpoint_ref = _checkpoint_artifact(
        store,
        workspace={
            "kind": "external_state_ref",
            "externalStateRef": "artifact-omnigent-state",
            "idempotencyKey": "omnigent-idem",
            "omnigentSessionId": "omni-session-123",
            "providerSessionRef": "artifact-provider-session-ref",
        },
    )
    sandbox = TemporalSandboxActivities(workspace_root=root, artifact_store=store)

    policy = await sandbox.workspace_apply_policy(
        {
            "identity": _identity().model_dump(by_alias=True),
            "workspacePolicy": "continue_from_previous_execution",
            "checkpointRef": checkpoint_ref,
            "checkpoint": {},
            "targetWorkspaceRef": str(target),
            "idempotencyKey": "MM-1079:external-state-ref-unsupported",
        }
    )

    assert policy["status"] == "rejected"
    assert policy["failureCode"] == "workspace_incompatible"
    assert "external provider restore bridge" in policy["summary"]
    assert not target.exists()
    diagnostic = json.loads(store.get_bytes(policy["diagnosticRefs"][0]).decode())
    assert diagnostic["checkpointKind"] == "external_state_ref"
    correlation = diagnostic["providerSessionCorrelation"]
    assert correlation["externalStateRef"] == "artifact-omnigent-state"
    assert correlation["omnigentSessionId"] == "omni-session-123"
    assert correlation["providerSessionRef"] == "artifact-provider-session-ref"
    _assert_secret_absent(diagnostic)


async def test_workspace_apply_policy_rejection_diagnostic_accepts_snake_case_refs(
    tmp_path: Path,
) -> None:
    store = InMemoryArtifactStore()
    root = _workspace_root(tmp_path)
    target = root / "temporal_sandbox" / "external-state-snake-case-target"
    checkpoint_ref = _checkpoint_artifact(
        store,
        workspace={
            "kind": "external_state_ref",
            "external_state_ref": "artifact-omnigent-state",
            "idempotency_key": "omnigent-idem",
            "omnigent_session_id": "omni-session-123",
            "provider_session_ref": "artifact-provider-session-ref",
        },
    )
    sandbox = TemporalSandboxActivities(workspace_root=root, artifact_store=store)

    policy = await sandbox.workspace_apply_policy(
        {
            "identity": _identity().model_dump(by_alias=True),
            "workspacePolicy": "continue_from_previous_execution",
            "checkpointRef": checkpoint_ref,
            "checkpoint": {},
            "targetWorkspaceRef": str(target),
            "idempotencyKey": "MM-1079:external-state-ref-snake-case",
        }
    )

    assert policy["status"] == "rejected"
    assert policy["failureCode"] == "workspace_incompatible"
    diagnostic = json.loads(store.get_bytes(policy["diagnosticRefs"][0]).decode())
    correlation = diagnostic["providerSessionCorrelation"]
    assert correlation["externalStateRef"] == "artifact-omnigent-state"
    assert correlation["omnigentSessionId"] == "omni-session-123"
    assert correlation["providerSessionRef"] == "artifact-provider-session-ref"
    _assert_secret_absent(diagnostic)


async def test_workspace_apply_policy_rejects_sandbox_escape_before_mutation(
    tmp_path: Path,
) -> None:
    store = InMemoryArtifactStore()
    root = _workspace_root(tmp_path)
    repo = _repo(root)
    checkpoint_ref = _checkpoint_artifact(
        store,
        workspace={
            "kind": "ephemeral_workspace_ref",
            "workspaceRef": str(repo),
        },
    )
    sandbox = TemporalSandboxActivities(workspace_root=root, artifact_store=store)

    with pytest.raises(RuntimeError, match="escapes sandbox root"):
        await sandbox.workspace_apply_policy(
            {
                "identity": _identity().model_dump(by_alias=True),
                "workspacePolicy": "continue_from_previous_execution",
                "checkpointRef": checkpoint_ref,
                "checkpoint": {},
                "targetWorkspaceRef": str(tmp_path / "outside"),
                "idempotencyKey": "MM-824:sandbox-escape",
            }
        )

    assert not (tmp_path / "outside").exists()


async def test_workspace_apply_policy_fails_closed_for_missing_and_corrupt_evidence(
    tmp_path: Path,
) -> None:
    store = InMemoryArtifactStore()
    root = _workspace_root(tmp_path)
    repo = _repo(root)
    corrupt_ref = store.put_bytes(
        b"{not-json",
        content_type=STEP_EXECUTION_CHECKPOINT_CONTENT_TYPE,
        metadata={"artifact_kind": "checkpoint"},
    ).artifact_ref
    target = root / "temporal_sandbox" / "missing-policy-target"
    sandbox = TemporalSandboxActivities(workspace_root=root, artifact_store=store)

    missing = await sandbox.workspace_apply_policy(
        {
            "identity": _identity().model_dump(by_alias=True),
            "workspacePolicy": "continue_from_previous_execution",
            "checkpointRef": "art:sha256:missing",
            "checkpoint": {},
            "targetWorkspaceRef": str(target),
            "idempotencyKey": "MM-824:missing-policy",
        }
    )
    corrupt = await sandbox.workspace_apply_policy(
        {
            "identity": _identity().model_dump(by_alias=True),
            "workspacePolicy": "continue_from_previous_execution",
            "checkpointRef": corrupt_ref,
            "checkpoint": {},
            "targetWorkspaceRef": str(root / "temporal_sandbox" / "corrupt-target"),
            "idempotencyKey": "MM-824:corrupt-policy",
        }
    )

    assert missing["status"] == "rejected"
    assert missing["failureCode"] == "artifact_missing"
    assert corrupt["status"] == "rejected"
    assert corrupt["failureCode"] == "artifact_corrupted"
    assert not target.exists()
    assert (repo / "README.md").read_text(encoding="utf-8") == "base\nchanged\n"


async def test_workspace_apply_policy_preserves_target_on_archive_restore_failure(
    tmp_path: Path,
) -> None:
    store = InMemoryArtifactStore()
    root = _workspace_root(tmp_path)
    target = root / "temporal_sandbox" / "archive-target"
    target.mkdir()
    (target / "README.md").write_text("keep me\n", encoding="utf-8")
    corrupt_archive_ref = store.put_bytes(
        b"not-a-tarball",
        content_type="application/gzip",
        metadata={"artifact_kind": "checkpoint_archive"},
    ).artifact_ref
    checkpoint_ref = _checkpoint_artifact(
        store,
        workspace={
            "kind": "worktree_archive",
            "archiveRef": corrupt_archive_ref,
        },
    )
    sandbox = TemporalSandboxActivities(workspace_root=root, artifact_store=store)

    policy = await sandbox.workspace_apply_policy(
        {
            "identity": _identity().model_dump(by_alias=True),
            "workspacePolicy": "restore_pre_execution",
            "checkpointRef": checkpoint_ref,
            "checkpoint": {},
            "targetWorkspaceRef": str(target),
            "idempotencyKey": "MM-824:corrupt-archive-preserves-target",
        }
    )

    assert policy["status"] == "rejected"
    assert policy["failureCode"] == "artifact_corrupted"
    assert (target / "README.md").read_text(encoding="utf-8") == "keep me\n"


async def test_workspace_apply_policy_rejects_invalid_git_checkout(
    tmp_path: Path,
) -> None:
    store = InMemoryArtifactStore()
    root = _workspace_root(tmp_path)
    repo = _repo(root)
    checkpoint_ref = _checkpoint_artifact(
        store,
        workspace={
            "kind": "git_commit",
            "headCommit": "0" * 40,
            "workspaceRef": str(repo),
        },
    )
    sandbox = TemporalSandboxActivities(workspace_root=root, artifact_store=store)

    policy = await sandbox.workspace_apply_policy(
        {
            "identity": _identity().model_dump(by_alias=True),
            "workspacePolicy": "start_from_last_passed_commit",
            "checkpointRef": checkpoint_ref,
            "checkpoint": {},
            "targetWorkspaceRef": str(root / "temporal_sandbox" / "bad-checkout"),
            "idempotencyKey": "MM-824:bad-checkout",
        }
    )

    assert policy["status"] == "rejected"
    assert policy["failureCode"] == "workspace_incompatible"
    assert "git checkout failed" in policy["summary"]


async def test_sandbox_checkpoint_writes_use_artifact_service_when_available(
    tmp_path: Path,
) -> None:
    service = AsyncMock()
    created = SimpleNamespace(artifact_id="artifact-created")
    completed = SimpleNamespace(
        artifact_id="artifact-completed",
        sha256="sha256:payload",
        size_bytes=12,
        content_type="application/json",
        encryption=SimpleNamespace(value="none"),
    )
    service.create.return_value = (created, None)
    service.write_complete.return_value = completed
    root = _workspace_root(tmp_path)
    repo = _repo(root)
    sandbox = TemporalSandboxActivities(workspace_root=root, artifact_service=service)

    capture = await sandbox.workspace_capture_checkpoint(
        {
            "identity": _identity().model_dump(by_alias=True),
            "boundary": "after_execution",
            "kind": "ephemeral_workspace_ref",
            "workspacePath": str(repo),
            "workspaceRootRef": "artifact-workspace-root",
            "artifactNamespace": "checkpoint",
            "idempotencyKey": "idem-service-writer",
        }
    )

    assert capture["workspace"]["workspaceArtifactRef"] == "artifact-completed"
    assert "workspaceRef" not in capture["workspace"]
    service.create.assert_awaited_once()
    service.write_complete.assert_awaited_once()


async def test_workspace_apply_policy_rejects_artifact_backed_ephemeral_ref_as_path(
    tmp_path: Path,
) -> None:
    store = InMemoryArtifactStore()
    root = _workspace_root(tmp_path)
    repo = _repo(root)
    sandbox = TemporalSandboxActivities(workspace_root=root, artifact_store=store)
    capture = await sandbox.workspace_capture_checkpoint(
        {
            "identity": _identity().model_dump(by_alias=True),
            "boundary": "after_execution",
            "kind": "ephemeral_workspace_ref",
            "workspacePath": str(repo),
            "workspaceRootRef": "artifact-workspace-root",
            "artifactNamespace": "checkpoint",
            "idempotencyKey": "MM-1079:ephemeral-capture",
        }
    )
    checkpoint_ref = _checkpoint_artifact(store, workspace=capture["workspace"])

    policy = await sandbox.workspace_apply_policy(
        {
            "identity": _identity().model_dump(by_alias=True),
            "workspacePolicy": "continue_from_previous_execution",
            "checkpointRef": checkpoint_ref,
            "checkpoint": {},
            "targetWorkspaceRef": str(root / "temporal_sandbox" / "target"),
            "idempotencyKey": "MM-1079:ephemeral-apply",
        }
    )

    assert policy["status"] == "rejected"
    assert policy["failureCode"] == "workspace_incompatible"
    assert "artifact-backed ephemeral workspace evidence" in policy["summary"]
    diagnostic = json.loads(store.get_bytes(policy["diagnosticRefs"][-1]).decode())
    assert diagnostic["checkpointKind"] == "ephemeral_workspace_ref"
    assert diagnostic["providerSessionCorrelation"]["workspaceArtifactRef"] == (
        capture["workspace"]["workspaceArtifactRef"]
    )


async def test_workspace_apply_policy_rejects_external_state_ref_with_diagnostics(
    tmp_path: Path,
) -> None:
    store = InMemoryArtifactStore()
    root = _workspace_root(tmp_path)
    sandbox = TemporalSandboxActivities(workspace_root=root, artifact_store=store)
    external_ref = store.put_bytes(
        json.dumps(
            {
                "kind": "external_state_ref",
                "sourceRef": "omnigent-session-123",
            }
        ).encode(),
        content_type="application/json",
        metadata={"artifact_kind": "checkpoint_external_state_ref"},
    ).artifact_ref
    checkpoint_ref = _checkpoint_artifact(
        store,
        workspace={
            "kind": "external_state_ref",
            "externalStateRef": external_ref,
        },
    )

    policy = await sandbox.workspace_apply_policy(
        {
            "identity": _identity().model_dump(by_alias=True),
            "workspacePolicy": "continue_from_previous_execution",
            "checkpointRef": checkpoint_ref,
            "checkpoint": {},
            "targetWorkspaceRef": str(root / "temporal_sandbox" / "target"),
            "providerLeaseContextRef": "provider-lease-context",
            "idempotencyKey": "MM-1079:external-state-apply",
        }
    )

    assert policy["status"] == "rejected"
    assert policy["failureCode"] == "workspace_incompatible"
    assert "external provider restore bridge" in policy["summary"]
    diagnostic = json.loads(store.get_bytes(policy["diagnosticRefs"][-1]).decode())
    assert diagnostic["checkpointKind"] == "external_state_ref"
    assert diagnostic["providerSessionCorrelation"]["externalStateRef"] == external_ref


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

    incompatible_checkpoint_ref = _checkpoint_artifact(
        store,
        workspace={
            "kind": "git_patch",
            "baseCommit": "abc",
            "patchRef": "artifact-patch",
        },
    )
    policy = await sandbox.workspace_apply_policy(
        {
            "identity": _identity().model_dump(by_alias=True),
            "workspacePolicy": "start_from_last_passed_commit",
            "checkpointRef": incompatible_checkpoint_ref,
            "checkpoint": {},
            "targetWorkspaceRef": str(repo),
            "expectedPlanDigest": "sha256:plan",
            "idempotencyKey": "idem-policy",
        }
    )
    assert policy["status"] == "rejected"
    assert policy["failureCode"] == "policy_incompatible"


async def test_workflow_recovery_routes_checkpoint_validation_before_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    async def fake_execute_activity(
        activity_type: str,
        payload: dict[str, object],
        **_kwargs: object,
    ) -> dict[str, object]:
        calls.append((activity_type, payload))
        if activity_type == "step_checkpoint.validate":
            return {
                "valid": True,
                "failureCode": None,
                "message": "checkpoint validation passed",
                "checkpointId": payload["checkpoint"]["checkpointId"],
                "checkpointRef": payload["checkpointRef"],
            }
        if activity_type == "workspace.apply_policy":
            return {
                "status": "applied",
                "workspaceRef": "workspace-target",
                "appliedCheckpointRef": payload["checkpointRef"],
                "providerLeaseRefs": [],
                "summary": "workspace policy applied",
                "failureCode": None,
            }
        raise AssertionError(f"unexpected activity {activity_type}")

    monkeypatch.setattr(run_module.workflow, "execute_activity", fake_execute_activity)
    workflow = MoonMindRunWorkflow()
    workflow._recovery_source = _recovery_source_with_checkpoint_payload()
    workflow._initialize_step_ledger(
        ordered_nodes=[{"id": "implement", "title": "Implement"}],
        dependency_map={"implement": []},
        updated_at=datetime(2026, 6, 13, 12, 0, tzinfo=UTC),
    )

    restored_ref = await workflow._prepare_recovery_workspace_for_failed_step(
        "implement"
    )

    assert restored_ref == "workspace-target"
    assert [activity_type for activity_type, _payload in calls] == [
        "step_checkpoint.validate",
        "workspace.apply_policy",
    ]
    validate_payload = calls[0][1]
    assert validate_payload["checkpointRef"] == "artifact-checkpoint"
    assert validate_payload["workspacePolicy"] == (
        "apply_previous_execution_diff_to_clean_baseline"
    )
    policy_payload = calls[1][1]
    assert policy_payload["checkpointRef"] == "artifact-checkpoint"
    assert policy_payload["targetWorkspaceRef"] == "workspace-target"


async def test_workflow_recovery_validation_failure_blocks_policy_application(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def fake_execute_activity(
        activity_type: str,
        payload: dict[str, object],
        **_kwargs: object,
    ) -> dict[str, object]:
        calls.append(activity_type)
        if activity_type == "step_checkpoint.validate":
            return {
                "valid": False,
                "failureCode": "artifact_unauthorized",
                "message": "unauthorized",
                "checkpointId": payload["checkpoint"]["checkpointId"],
                "checkpointRef": payload["checkpointRef"],
            }
        raise AssertionError("workspace policy must not run after failed validation")

    monkeypatch.setattr(run_module.workflow, "execute_activity", fake_execute_activity)
    workflow = MoonMindRunWorkflow()
    workflow._recovery_source = _recovery_source_with_checkpoint_payload()
    workflow._initialize_step_ledger(
        ordered_nodes=[{"id": "implement", "title": "Implement"}],
        dependency_map={"implement": []},
        updated_at=datetime(2026, 6, 13, 12, 0, tzinfo=UTC),
    )

    with pytest.raises(ValueError, match="artifact_unauthorized"):
        await workflow._prepare_recovery_workspace_for_failed_step("implement")

    assert calls == ["step_checkpoint.validate"]


async def test_checkpoint_validate_activity_reports_boundary_failure_classes(
    tmp_path: Path,
) -> None:
    store = InMemoryArtifactStore()
    root = _workspace_root(tmp_path)
    repo = _repo(root)
    sandbox = TemporalSandboxActivities(workspace_root=root, artifact_store=store)
    checkpoint_activities = TemporalCheckpointActivities(artifact_store=store)
    base_commit = (
        subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo)
        .decode()
        .strip()
    )
    capture = await sandbox.workspace_capture_checkpoint(
        {
            "identity": _identity().model_dump(by_alias=True),
            "boundary": "after_execution",
            "kind": "git_patch",
            "workspacePath": str(repo),
            "artifactNamespace": "checkpoint",
            "idempotencyKey": "idem-validation-capture",
            "baseCommit": base_commit,
        }
    )
    created = await checkpoint_activities.step_checkpoint_create(
        {
            "identity": _identity().model_dump(by_alias=True),
            "boundary": "after_execution",
            "taskInputSnapshotRef": "artifact-input",
            "workspace": capture["workspace"],
            "createdAt": datetime(2026, 6, 13, 12, 0, tzinfo=UTC).isoformat(),
            "planDigest": "sha256:plan",
            "idempotencyKey": "idem-validation-create",
        }
    )
    checkpoint = json.loads(store.get_bytes(created["checkpointRef"]).decode())

    cases = [
        (
            {"requiredArtifactRefs": ["artifact-missing"]},
            "artifact_missing",
        ),
        (
            {"unauthorizedArtifactRefs": [capture["workspace"]["patchRef"]]},
            "artifact_unauthorized",
        ),
        (
            {"corruptedArtifactRefs": [capture["workspace"]["patchRef"]]},
            "artifact_corrupted",
        ),
        (
            {"workspacePolicy": "start_from_last_passed_commit"},
            "policy_incompatible",
        ),
        (
            {"checkpoint": {"checkpointId": "bad-checkpoint"}, "checkpointRef": None},
            "invalid_checkpoint",
        ),
    ]

    for extra, expected_code in cases:
        payload = {
            "checkpoint": checkpoint,
            "expectedSource": _identity().model_dump(by_alias=True),
            "expectedTaskInputSnapshotRef": "artifact-input",
            "expectedPlanDigest": "sha256:plan",
            "checkpointRef": created["checkpointRef"],
            **extra,
        }
        result = await checkpoint_activities.step_checkpoint_validate(payload)
        assert result["valid"] is False
        assert result["failureCode"] == expected_code
