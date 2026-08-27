"""Generic Omnigent workspace materialization rules."""

from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

from moonmind.omnigent.harness_platform.failures import HarnessPlatformError
from moonmind.omnigent.host_services.workspace import (
    OmnigentWorkspaceMaterializer,
    build_daemon_git_clone_argv,
    normalize_github_clone_source,
)
from moonmind.schemas.workspace_locator_models import SandboxWorkspaceLocator
from moonmind.workflows.temporal.runtime.workspace_locators import (
    SandboxWorkspaceRecord,
    SandboxWorkspaceRecordStore,
    resolve_sandbox_workspace_locator,
)


def _request(spec: dict) -> SimpleNamespace:
    return SimpleNamespace(
        workspace_spec=spec,
        parameters={},
        step_execution=None,
        correlation_id="workflow-1",
        idempotency_key="step-1",
    )


def _workspace_id() -> str:
    return hashlib.sha256(b"workflow-1:step-1").hexdigest()[:24]


def test_normalize_github_clone_source_accepts_owner_repo_and_https():
    assert (
        normalize_github_clone_source("MoonLadderStudios/MoonMind")
        == "https://github.com/MoonLadderStudios/MoonMind.git"
    )
    assert normalize_github_clone_source(
        "https://github.com/MoonLadderStudios/MoonMind"
    )
    assert normalize_github_clone_source("not a repo ref") is None
    assert normalize_github_clone_source("file:///etc/passwd") is None


def test_daemon_git_clone_argv_pins_target():
    argv = build_daemon_git_clone_argv(
        volume="agent_workspaces",
        target_in_volume="ws-1/repo",
        source="https://x-access-token:tok@github.com/org/repo.git",
        branch="feature/branch",
        image="alpine/git:v2.43.0",
    )
    assert argv[:4] == ["docker", "run", "--rm", "-v"]
    assert "agent_workspaces:/work" in argv
    assert argv[-2:] == [
        "https://x-access-token:tok@github.com/org/repo.git",
        "/work/ws-1/repo",
    ]
    assert "alpine/git:v2.43.0" in argv
    assert "clone" in argv and "--single-branch" in argv


@pytest.mark.asyncio
async def test_materializer_clones_missing_sandbox_workspace_via_daemon(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    """A fresh sandbox locator materializes by cloning the requested branch."""

    captured: dict = {}

    async def runner(argv):
        captured["argv"] = argv
        # Simulate git creating the checkout inside the volume.
        target = argv[-1]
        assert target.startswith("/work/")
        import pathlib

        local = tmp_path / pathlib.Path(target.removeprefix("/work/"))
        local.mkdir(parents=True, exist_ok=True)
        (local / "README.md").write_text("cloned", encoding="utf-8")
        return 0, "", ""

    async def fake_token(*args, **kwargs):
        return "tok" + "e" * 10

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.managed_api_key_resolve."
        "resolve_github_token_for_launch",
        fake_token,
    )

    materializer = OmnigentWorkspaceMaterializer(
        command_runner=runner, workspace_root=tmp_path
    )
    workspace_id = _workspace_id()
    workspace = await materializer.materialize(
        _request(
            {
                "workspaceLocator": {
                    "kind": "sandbox",
                    "workspaceId": workspace_id,
                    "relativePath": "repo",
                },
                "repository": "MoonLadderStudios/MoonMind",
                "branch": "dependabot/npm_and_yarn/multi-2181bdc769",
            }
        )
    )

    assert workspace["kind"] == "bind"
    argv = captured["argv"]
    assert argv[0] == "docker"
    assert f"{materializer._workspace_volume}:/work" in argv
    assert "/work/temporal_sandbox/" + workspace_id + "/repo" in argv
    joined = " ".join(argv)
    # The clone runs in a one-shot container with no credential helper, so the
    # remote carries launch-time GitHub auth. Assert the authenticated form.
    assert "https://x-access-token:" in joined
    assert "@github.com/MoonLadderStudios/MoonMind.git" in joined
    assert "dependabot/npm_and_yarn/multi-2181bdc769" in joined
    record = SandboxWorkspaceRecordStore(tmp_path).load(workspace_id)
    assert record == SandboxWorkspaceRecord(
        workspace_id=workspace_id,
        workflow_id="workflow-1",
        step_execution_id="step-1",
        relative_path="repo",
    )
    assert resolve_sandbox_workspace_locator(
        SandboxWorkspaceLocator(workspaceId=workspace_id),
        workspace_root=tmp_path,
        expected_workspace_id=workspace_id,
        owner_record=record,
        expected_workflow_id="workflow-1",
        expected_step_execution_id="step-1",
    ) == tmp_path / "temporal_sandbox" / workspace_id / "repo"


@pytest.mark.asyncio
async def test_materializer_keeps_existing_authoritative_workspace(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    workspace_id = _workspace_id()
    existing = tmp_path / "temporal_sandbox" / workspace_id / "repo"
    existing.mkdir(parents=True)
    (existing / "KEEP").write_text("x", encoding="utf-8")

    async def fail_runner(argv):  # pragma: no cover - must not run
        raise AssertionError("clone must not run for an existing workspace")

    materializer = OmnigentWorkspaceMaterializer(
        command_runner=fail_runner, workspace_root=tmp_path
    )
    await materializer.materialize(
        _request(
            {
                "workspaceLocator": {
                    "kind": "sandbox",
                    "workspaceId": workspace_id,
                    "relativePath": "repo",
                },
                "repository": "MoonLadderStudios/MoonMind",
                "branch": "main",
            }
        )
    )
    assert (existing / "KEEP").exists()


@pytest.mark.asyncio
async def test_materializer_fails_closed_without_safe_branch(tmp_path):
    async def runner(argv):
        return 0, "", ""

    materializer = OmnigentWorkspaceMaterializer(
        command_runner=runner, workspace_root=tmp_path
    )
    with pytest.raises(HarnessPlatformError, match="safe branch"):
        await materializer.materialize(
            _request(
                {
                    "workspaceLocator": {
                        "kind": "sandbox",
                        "workspaceId": _workspace_id(),
                        "relativePath": "repo",
                    },
                    "repository": "../etc/passwd",
                    "branch": "",
                }
            )
        )


@pytest.mark.asyncio
async def test_materializer_rejects_missing_authored_path_and_failed_clone(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    materializer = OmnigentWorkspaceMaterializer(
        command_runner=None, workspace_root=tmp_path  # type: ignore[arg-type]
    )

    # Authored absolute paths are preexisting authority: missing dir fails closed.
    with pytest.raises(HarnessPlatformError):
        await materializer.materialize(_request({"workspacePath": "/tmp/nowhere"}))

    calls: list[list[str]] = []

    async def failing_runner(argv):
        calls.append(argv)
        return 128, "", "fatal: repository not found"

    object.__setattr__(materializer, "_runner", failing_runner)

    async def fake_token(*args, **kwargs):
        return "tok" * 5

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.managed_api_key_resolve."
        "resolve_github_token_for_launch",
        fake_token,
    )
    with pytest.raises(HarnessPlatformError, match="clone failed"):
        await materializer.materialize(
            _request(
                {
                    "workspaceLocator": {
                        "kind": "sandbox",
                        "workspaceId": _workspace_id(),
                        "relativePath": "repo",
                    },
                    "repository": "MoonLadderStudios/MoonMind",
                    "branch": "does-not-exist",
                }
            )
        )
    assert len(calls) == 1
