"""Controlling journey for MoonLadderStudios/MoonMind#3507.

Proves the complete normal-workflow Omnigent workspace path end to end, without
Docker or network: a fresh sandbox locator materializes the authored repository
and branch into the single authoritative workspace through the owning-worker
boundary, a retry is idempotent and preserves working-tree state, and locator
kinds that are not a valid host workspace fail closed rather than silently
substituting a different workspace.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

from moonmind.omnigent.host_failures import OmnigentOAuthHostError
from moonmind.omnigent.oauth_host_runtime import OmnigentOAuthHostRuntime
from moonmind.schemas.workspace_locator_models import (
    WorkspaceLocatorResolutionError,
)
from moonmind.workflows.temporal.runtime.workspace_locators import (
    SandboxWorkspaceRecordStore,
)
from types import SimpleNamespace


pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=journey@moonmind.test",
            "-c",
            "user.name=MoonMind Journey",
            "-c",
            "init.defaultBranch=main",
            *args,
        ],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


def _init_source_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init")
    _git(path, "checkout", "-B", "main")
    (path / "app.py").write_text("print('hello')\n", encoding="utf-8")
    _git(path, "add", "app.py")
    _git(path, "commit", "-m", "seed")
    _git(path, "checkout", "-B", "release")
    (path / "release.txt").write_text("released\n", encoding="utf-8")
    _git(path, "add", "release.txt")
    _git(path, "commit", "-m", "release")
    _git(path, "checkout", "main")


def _current_branch(repo: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.mark.asyncio
async def test_normal_workflow_materializes_one_authoritative_workspace(
    tmp_path,
) -> None:
    source = tmp_path / "source"
    _init_source_repo(source)
    workspace_root = tmp_path / "workspaces"
    runtime = OmnigentOAuthHostRuntime(
        client=SimpleNamespace(),
        workspace_root=workspace_root,
        repository_source_root=tmp_path,
    )

    # The workflow authors a durable locator identity, never a worker path.
    workflow_id = "mm:wf-3507"
    step_execution_id = "mm:wf-3507:run:implement:execution:1"
    workspace_id = hashlib.sha256(
        f"{workflow_id}:{step_execution_id}".encode("utf-8")
    ).hexdigest()[:24]
    locator = {"kind": "sandbox", "workspaceId": workspace_id, "relativePath": "repo"}

    resolved = await runtime._prepare_workspace(
        workspace_locator=locator,
        current_workflow_id=workflow_id,
        current_step_execution_id=step_execution_id,
        repository_source=str(source),
        starting_branch="release",
        target_branch="agent/implement",
    )

    # One authoritative workspace with the authored repository + branch state.
    assert resolved == workspace_root / "temporal_sandbox" / workspace_id / "repo"
    assert (resolved / ".git").is_dir()
    assert (resolved / "release.txt").is_file()
    assert _current_branch(resolved) == "agent/implement"

    # Durable owner evidence binds the workspace to this exact execution.
    record = SandboxWorkspaceRecordStore(workspace_root).load(workspace_id)
    assert record is not None
    assert record.workflow_id == workflow_id
    assert record.step_execution_id == step_execution_id

    # A retry cannot create a second workspace/host or discard in-flight work.
    (resolved / "in-flight.txt").write_text("uncommitted", encoding="utf-8")
    retry = await runtime._prepare_workspace(
        workspace_locator=locator,
        current_workflow_id=workflow_id,
        current_step_execution_id=step_execution_id,
        repository_source=str(source),
        starting_branch="release",
        target_branch="agent/implement",
    )
    assert retry == resolved
    assert (resolved / "in-flight.txt").read_text(encoding="utf-8") == "uncommitted"
    assert runtime._last_workspace_evidence["materialization"]["action"] == (
        "reused_pre_materialized"
    )

    # A cross-execution retry pointed at the same durable identity is rejected
    # before any mutation, so it cannot hijack another run's workspace.
    with pytest.raises(WorkspaceLocatorResolutionError):
        await runtime._prepare_workspace(
            workspace_locator=locator,
            current_workflow_id="mm:wf-other",
            current_step_execution_id="mm:wf-other:run:implement:execution:1",
            repository_source=str(source),
        )


@pytest.mark.asyncio
async def test_non_sandbox_locators_fail_closed(tmp_path) -> None:
    runtime = OmnigentOAuthHostRuntime(
        client=SimpleNamespace(), workspace_root=tmp_path / "workspaces"
    )

    for locator in (
        {"kind": "external_state", "artifactRef": "artifact://checkpoint/1"},
        {"kind": "managed_runtime", "runtimeId": "codex_cli", "agentRunId": "run-1"},
    ):
        with pytest.raises(OmnigentOAuthHostError) as exc:
            await runtime._prepare_workspace(
                workspace_locator=locator,
                current_workflow_id="mm:wf-3507",
                current_step_execution_id="mm:wf-3507:run:implement:execution:1",
            )
        assert exc.value.code == "WORKSPACE_LOCATOR_UNSUPPORTED"
