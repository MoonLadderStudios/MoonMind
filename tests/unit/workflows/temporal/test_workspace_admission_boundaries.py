"""Machine filesystem boundaries for retained legacy runtime paths."""

from pathlib import Path
import subprocess
from unittest.mock import AsyncMock

import pytest

from moonmind.schemas.managed_session_models import LaunchCodexManagedSessionRequest
from moonmind.workflows.temporal.activity_runtime import (
    TemporalSandboxActivities,
    TemporalActivityRuntimeError,
)
from moonmind.workflows.temporal.runtime.managed_session_controller import (
    DockerCodexManagedSessionController,
)


def launch_request(root: Path, **overrides):
    return LaunchCodexManagedSessionRequest.model_validate(
        {
            "agentRunId": "run-1",
            "workflowId": "workflow-1",
            "sessionId": "session-1",
            "threadId": "thread-1",
            "workspacePath": str(root / "run-1" / "repo"),
            "sessionWorkspacePath": str(root / "run-1" / "session"),
            "artifactSpoolPath": str(root / "run-1" / "artifacts"),
            "codexHomePath": "/home/app/.codex",
            "imageRef": "moonmind:test",
            **overrides,
        }
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    ["/tmp/auth,type=bind,src=/", "/tmp/auth,readonly=false", "/tmp/auth\x00ignored"],
)
async def test_managed_auth_mount_injection_fails_before_workspace_changes(
    tmp_path, path
):
    controller = DockerCodexManagedSessionController(
        workspace_volume_name="workspaces",
        codex_volume_name="auth",
        workspace_root=str(tmp_path),
    )
    request = launch_request(tmp_path, environment={"MANAGED_AUTH_VOLUME_PATH": path})
    controller._ensure_workspace_paths = AsyncMock()
    with pytest.raises(RuntimeError, match="mount|path"):
        await controller.launch_session(request)
    controller._ensure_workspace_paths.assert_not_awaited()


@pytest.mark.parametrize(
    "field", ["workspacePath", "sessionWorkspacePath", "artifactSpoolPath"]
)
def test_managed_launch_cannot_use_whole_workspace_store(tmp_path, field):
    controller = DockerCodexManagedSessionController(
        workspace_volume_name="workspaces",
        codex_volume_name="auth",
        workspace_root=str(tmp_path),
    )
    with pytest.raises(RuntimeError, match="workspace_root"):
        controller._validate_launch_request(
            launch_request(tmp_path, **{field: str(tmp_path)})
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("encoding", ["ordinary", "percent_encoded"])
async def test_file_uri_checkout_cannot_bypass_local_source_authority(
    tmp_path, encoding
):
    root = tmp_path / "workspaces"
    outside = tmp_path / "private-repo"
    outside.mkdir()
    uri = outside.as_uri()
    if encoding == "percent_encoded":
        uri = uri.replace("private-repo", "%70rivate-repo")
    activities = TemporalSandboxActivities(workspace_root=root)
    activities.sandbox_run_command = AsyncMock()
    with pytest.raises(TemporalActivityRuntimeError, match="under workspace_root"):
        await activities.sandbox_checkout_repo(repo_ref=uri, idempotency_key="checkout")
    activities.sandbox_run_command.assert_not_awaited()
    assert not root.exists()


def test_contained_file_uri_remains_a_git_clone_source(tmp_path):
    source = tmp_path / "local-repo"
    source.mkdir()
    activities = TemporalSandboxActivities(workspace_root=tmp_path)
    assert activities._resolve_checkout_source(source.as_uri()) == (
        "remote",
        source.as_uri(),
    )


def checkpoint_identity():
    return {
        "workflowId": "wf-1",
        "runId": "run-1",
        "logicalStepId": "step-1",
        "executionOrdinal": 1,
    }


@pytest.mark.asyncio
async def test_checkpoint_cannot_archive_whole_sandbox_store(tmp_path):
    sandbox = tmp_path / "temporal_sandbox"
    sandbox.mkdir()
    (sandbox / "saved-work.txt").write_text("preserve")
    activities = TemporalSandboxActivities(workspace_root=tmp_path)
    with pytest.raises(TemporalActivityRuntimeError, match="sandbox store"):
        await activities.workspace_capture_checkpoint(
            {
                "identity": checkpoint_identity(),
                "boundary": "after_execution",
                "kind": "worktree_archive",
                "workspacePath": str(sandbox),
                "artifactNamespace": "checkpoint",
                "idempotencyKey": "capture-root",
            }
        )
    assert (sandbox / "saved-work.txt").read_text() == "preserve"


def test_workspace_policy_cannot_replace_whole_sandbox_store(tmp_path):
    from moonmind.schemas.temporal_models import WorkspacePolicyApplyInput

    sandbox = tmp_path / "temporal_sandbox"
    sandbox.mkdir()
    (sandbox / "saved-work.txt").write_text("preserve")
    activities = TemporalSandboxActivities(workspace_root=tmp_path)
    request = WorkspacePolicyApplyInput.model_validate(
        {
            "identity": checkpoint_identity(),
            "workspacePolicy": "restore_pre_execution",
            "checkpointRef": "artifact://checkpoint",
            "targetWorkspaceRef": str(sandbox),
            "idempotencyKey": "restore-root",
        }
    )
    with pytest.raises(TemporalActivityRuntimeError, match="sandbox store"):
        activities._policy_target_workspace(request)
    assert (sandbox / "saved-work.txt").read_text() == "preserve"


@pytest.mark.parametrize("source_is_root", [False, True])
def test_workspace_copy_cannot_read_or_replace_whole_sandbox_store(
    tmp_path, source_is_root
):
    sandbox = tmp_path / "temporal_sandbox"
    source = sandbox / "source"
    source.mkdir(parents=True)
    (source / "saved-work.txt").write_text("preserve")
    activities = TemporalSandboxActivities(workspace_root=tmp_path)
    with pytest.raises(TemporalActivityRuntimeError, match="sandbox store"):
        if source_is_root:
            activities._replace_workspace_tree(sandbox, sandbox / "target")
        else:
            activities._replace_workspace_tree(source, sandbox)
    assert (source / "saved-work.txt").read_text() == "preserve"


@pytest.mark.asyncio
async def test_contained_file_uri_checkout_preserves_git_revision_behavior(tmp_path):
    source = tmp_path / "local repo"
    source.mkdir()
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    (source / "README.md").write_text("preserved source\n")
    subprocess.run(["git", "-C", str(source), "add", "README.md"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(source),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            "source",
        ],
        check=True,
    )
    revision = subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "HEAD"], text=True
    ).strip()
    activities = TemporalSandboxActivities(workspace_root=tmp_path)
    workspace = await activities.sandbox_checkout_repo(
        repo_ref=source.as_uri(),
        idempotency_key="local-file-checkout",
        checkout_revision=revision,
    )
    assert (Path(workspace) / "README.md").read_text() == "preserved source\n"
    observed_revision = subprocess.check_output(
        ["git", "-C", workspace, "rev-parse", "HEAD"], text=True
    ).strip()
    assert observed_revision == revision
