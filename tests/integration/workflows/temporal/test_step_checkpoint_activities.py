from __future__ import annotations

import json
import subprocess
import tarfile
from io import BytesIO
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

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
from moonmind.workflows.temporal.step_checkpoints import build_step_checkpoint_payload
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


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, stdout=subprocess.PIPE)


def _head_sha(repo: Path) -> str:
    return (
        subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo).decode().strip()
    )


def _two_commit_repo(root: Path) -> tuple[Path, str, str]:
    """Initialize a repo with a base commit and a follow-on commit."""

    repo = root / "temporal_sandbox" / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "base")
    base = _head_sha(repo)
    (repo / "README.md").write_text("base\nchanged\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "change")
    head = _head_sha(repo)
    return repo, base, head


def _full_checkpoint_payload(*, kind: str, **evidence: object) -> dict[str, object]:
    workspace = {"kind": kind, **evidence}
    return build_step_checkpoint_payload(
        identity=_identity(),
        boundary="before_execution",
        task_input_snapshot_ref="artifact-input",
        plan_digest="sha256:plan",
        workspace=workspace,
        created_at=datetime(2026, 6, 13, 12, 0, tzinfo=UTC),
    )


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
        ("ephemeral_workspace_ref", {}, ("workspaceRef",)),
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

    # A structurally malformed checkpoint fails closed before any mutation.
    assert policy["status"] == "rejected"
    assert policy["failureCode"] == "invalid_checkpoint"
    assert policy["diagnosticRefs"]


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

    assert capture["workspace"]["workspaceRef"] == "artifact-completed"
    service.create.assert_awaited_once()
    service.write_complete.assert_awaited_once()


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
            "checkpoint": _full_checkpoint_payload(
                kind="git_patch",
                baseCommit="abc",
                patchRef="artifact-patch",
            ),
            "targetWorkspaceRef": str(repo),
            "expectedPlanDigest": "sha256:plan",
            "idempotencyKey": "idem-policy",
        }
    )
    assert policy["status"] == "rejected"
    assert policy["failureCode"] == "policy_incompatible"
    _assert_secret_absent(policy)


async def test_apply_policy_restore_pre_execution_resets_to_checkpoint_commit(
    tmp_path: Path,
) -> None:
    store = InMemoryArtifactStore()
    root = _workspace_root(tmp_path)
    repo, base, _head = _two_commit_repo(root)
    sandbox = TemporalSandboxActivities(workspace_root=root, artifact_store=store)

    policy = await sandbox.workspace_apply_policy(
        {
            "identity": _identity().model_dump(by_alias=True),
            "workspacePolicy": "restore_pre_execution",
            "checkpointRef": "artifact-checkpoint",
            "checkpoint": _full_checkpoint_payload(kind="git_commit", headCommit=base),
            "targetWorkspaceRef": str(repo),
            "idempotencyKey": "idem-restore",
        }
    )

    assert policy["status"] == "applied"
    assert policy["appliedCheckpointRef"] == "artifact-checkpoint"
    assert (repo / "README.md").read_text(encoding="utf-8") == "base\n"
    assert _head_sha(repo) == base


async def test_apply_policy_start_from_last_passed_commit_resets_to_commit(
    tmp_path: Path,
) -> None:
    store = InMemoryArtifactStore()
    root = _workspace_root(tmp_path)
    repo, base, head = _two_commit_repo(root)
    sandbox = TemporalSandboxActivities(workspace_root=root, artifact_store=store)
    assert _head_sha(repo) == head

    policy = await sandbox.workspace_apply_policy(
        {
            "identity": _identity().model_dump(by_alias=True),
            "workspacePolicy": "start_from_last_passed_commit",
            "checkpointRef": "artifact-checkpoint",
            "checkpoint": _full_checkpoint_payload(kind="git_commit", baseCommit=base),
            "targetWorkspaceRef": str(repo),
            "idempotencyKey": "idem-last-passed",
        }
    )

    assert policy["status"] == "applied"
    assert _head_sha(repo) == base
    assert (repo / "README.md").read_text(encoding="utf-8") == "base\n"


async def test_apply_policy_continue_from_previous_execution_preserves_tree(
    tmp_path: Path,
) -> None:
    store = InMemoryArtifactStore()
    root = _workspace_root(tmp_path)
    repo, _base, head = _two_commit_repo(root)
    sandbox = TemporalSandboxActivities(workspace_root=root, artifact_store=store)
    # Leave an uncommitted working-tree change that must survive the policy.
    (repo / "README.md").write_text("base\nchanged\nwip\n", encoding="utf-8")

    policy = await sandbox.workspace_apply_policy(
        {
            "identity": _identity().model_dump(by_alias=True),
            "workspacePolicy": "continue_from_previous_execution",
            "checkpointRef": "artifact-checkpoint",
            "checkpoint": _full_checkpoint_payload(kind="git_commit", headCommit=head),
            "targetWorkspaceRef": str(repo),
            "idempotencyKey": "idem-continue",
        }
    )

    assert policy["status"] == "applied"
    # The working tree (including uncommitted edits) is preserved untouched.
    assert (repo / "README.md").read_text(encoding="utf-8") == "base\nchanged\nwip\n"


async def test_apply_policy_diff_to_clean_baseline_applies_patch(
    tmp_path: Path,
) -> None:
    store = InMemoryArtifactStore()
    root = _workspace_root(tmp_path)
    repo = root / "temporal_sandbox" / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "base")
    base = _head_sha(repo)

    # Build a patch relative to the base commit, then restore the clean baseline.
    (repo / "README.md").write_text("base\npatched\n", encoding="utf-8")
    patch_bytes = subprocess.check_output(["git", "diff"], cwd=repo)
    _git(repo, "checkout", "--", "README.md")
    patch_ref = store.put_bytes(patch_bytes, content_type="text/x-diff").artifact_ref

    # Dirty the workspace so the clean-baseline reset is observable.
    (repo / "stray.txt").write_text("stray\n", encoding="utf-8")
    sandbox = TemporalSandboxActivities(workspace_root=root, artifact_store=store)

    policy = await sandbox.workspace_apply_policy(
        {
            "identity": _identity().model_dump(by_alias=True),
            "workspacePolicy": "apply_previous_execution_diff_to_clean_baseline",
            "checkpointRef": "artifact-checkpoint",
            "checkpoint": _full_checkpoint_payload(
                kind="git_patch", baseCommit=base, patchRef=patch_ref
            ),
            "targetWorkspaceRef": str(repo),
            "idempotencyKey": "idem-diff",
        }
    )

    assert policy["status"] == "applied"
    assert (repo / "README.md").read_text(encoding="utf-8") == "base\npatched\n"
    # git clean removed the untracked drift before applying the diff.
    assert not (repo / "stray.txt").exists()


async def test_apply_policy_fresh_branch_from_source_creates_branch(
    tmp_path: Path,
) -> None:
    store = InMemoryArtifactStore()
    root = _workspace_root(tmp_path)
    repo, _base, _head = _two_commit_repo(root)
    sandbox = TemporalSandboxActivities(workspace_root=root, artifact_store=store)

    policy = await sandbox.workspace_apply_policy(
        {
            "identity": _identity().model_dump(by_alias=True),
            "workspacePolicy": "fresh_branch_from_source",
            "checkpointRef": "artifact-none",
            "checkpoint": {},
            "targetWorkspaceRef": str(repo),
            "branchRef": "mm/fresh-branch",
            "idempotencyKey": "idem-fresh",
        }
    )

    assert policy["status"] == "applied"
    current_branch = (
        subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo
        )
        .decode()
        .strip()
    )
    assert current_branch == "mm/fresh-branch"


async def test_apply_policy_is_idempotent_for_repeated_key(tmp_path: Path) -> None:
    store = InMemoryArtifactStore()
    root = _workspace_root(tmp_path)
    repo, base, _head = _two_commit_repo(root)
    sandbox = TemporalSandboxActivities(workspace_root=root, artifact_store=store)

    request = {
        "identity": _identity().model_dump(by_alias=True),
        "workspacePolicy": "restore_pre_execution",
        "checkpointRef": "artifact-checkpoint",
        "checkpoint": _full_checkpoint_payload(kind="git_commit", headCommit=base),
        "targetWorkspaceRef": str(repo),
        "idempotencyKey": "idem-repeat",
    }

    first = await sandbox.workspace_apply_policy(dict(request))
    assert first["status"] == "applied"
    assert (repo / "README.md").read_text(encoding="utf-8") == "base\n"

    # Mutate the workspace, then replay the same idempotency key. The cached
    # result is returned and the workspace is NOT reset a second time.
    (repo / "README.md").write_text("base\nlater-edit\n", encoding="utf-8")
    second = await sandbox.workspace_apply_policy(dict(request))

    assert second == first
    assert (repo / "README.md").read_text(encoding="utf-8") == "base\nlater-edit\n"


async def test_apply_policy_rejects_workspace_outside_sandbox(tmp_path: Path) -> None:
    store = InMemoryArtifactStore()
    root = _workspace_root(tmp_path)
    repo, base, _head = _two_commit_repo(root)
    sandbox = TemporalSandboxActivities(workspace_root=root, artifact_store=store)
    outside = tmp_path / "outside-sandbox"
    outside.mkdir()

    policy = await sandbox.workspace_apply_policy(
        {
            "identity": _identity().model_dump(by_alias=True),
            "workspacePolicy": "restore_pre_execution",
            "checkpointRef": "artifact-checkpoint",
            "checkpoint": _full_checkpoint_payload(kind="git_commit", headCommit=base),
            "targetWorkspaceRef": str(outside),
            "idempotencyKey": "idem-outside",
        }
    )

    assert policy["status"] == "rejected"
    assert policy["failureCode"] == "workspace_incompatible"
    assert policy["diagnosticRefs"]
    diagnostics = json.loads(store.get_bytes(policy["diagnosticRefs"][0]).decode())
    assert diagnostics["failureCode"] == "workspace_incompatible"
    assert diagnostics["logicalStepId"] == "checkpoint-story"
    assert diagnostics["sourceWorkflowId"] == "workflow-1"
    assert diagnostics["sourceRunId"] == "run-1"
    assert diagnostics["checkpointRef"] == "artifact-checkpoint"
    assert diagnostics["workspacePolicy"] == "restore_pre_execution"
    assert diagnostics["recommendedNextAction"] == "provision_approved_workspace"


async def test_apply_policy_missing_patch_artifact_fails_closed(
    tmp_path: Path,
) -> None:
    store = InMemoryArtifactStore()
    root = _workspace_root(tmp_path)
    repo = root / "temporal_sandbox" / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "base")
    base = _head_sha(repo)
    (repo / "drift.txt").write_text("drift\n", encoding="utf-8")
    sandbox = TemporalSandboxActivities(workspace_root=root, artifact_store=store)

    policy = await sandbox.workspace_apply_policy(
        {
            "identity": _identity().model_dump(by_alias=True),
            "workspacePolicy": "apply_previous_execution_diff_to_clean_baseline",
            "checkpointRef": "artifact-checkpoint",
            "checkpoint": _full_checkpoint_payload(
                kind="git_patch", baseCommit=base, patchRef="artifact-absent-patch"
            ),
            "targetWorkspaceRef": str(repo),
            "idempotencyKey": "idem-missing-patch",
        }
    )

    assert policy["status"] == "rejected"
    assert policy["failureCode"] == "artifact_missing"
    assert policy["diagnosticRefs"]


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
                "status": "prepared",
                "workspaceRef": "workspace-target",
                "appliedCheckpointRef": payload["checkpointRef"],
                "providerLeaseRefs": [],
                "summary": "workspace policy prepared",
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
            {"checkpoint": {"checkpointId": "bad-checkpoint"}},
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
