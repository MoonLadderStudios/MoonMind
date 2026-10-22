"""Host-side Git consumers send the selected credential, never ambient A.

Real ``git`` reaches a synthetic loopback ``https://github.com`` while the
worker environment offers ambient credential A through every route Git and
gh read (HOME config helper and header, ``~/.netrc``, injected config,
askpass, gh tokens and login state). No network or provider account is used.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from moonmind.workflows.temporal.runtime.git_auth import build_remote_git_environment
from tests.helpers import git_transport

AMBIENT = "syntheticAmbientA"
SELECTED = "syntheticSelectedB"
REMOTE = "https://github.com/owner/repo.git"


@pytest.fixture
def ambient_worker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    for key in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM", "GIT_CONFIG_COUNT"):
        monkeypatch.delenv(key, raising=False)
    for key, value in git_transport.ambient_credential_environment(
        tmp_path, AMBIENT
    ).items():
        monkeypatch.setenv(key, value)

    def serve(required_token: str | None):
        generator = git_transport.start_synthetic_github(
            tmp_path / "transport", required_token=required_token
        )
        transport = next(generator)
        shim = git_transport.write_proxy_git_shim(tmp_path, transport)
        monkeypatch.setenv("PATH", f"{shim}:{os.environ['PATH']}")
        return transport, generator

    return serve


def _assert_never_sent_ambient(transport) -> None:
    assert transport.requests
    ambient = git_transport.basic_authorization(AMBIENT)
    assert ambient not in transport.header_values()
    assert not any(AMBIENT in value for value in transport.header_values())


def _assert_sent_only(transport, token: str | None) -> None:
    _assert_never_sent_ambient(transport)
    expected = [git_transport.basic_authorization(token)] if token else []
    assert set(transport.sent_authorizations()) <= set(expected)
    if token:
        assert transport.sent_authorizations()


def _clone_with_origin(tmp_path: Path, origin: str = REMOTE) -> Path:
    workspace = tmp_path / "run-1" / "repo"
    subprocess.run(
        ["git", "-c", "init.defaultBranch=main", "init", "-q", str(workspace)],
        check=True,
        env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent"},
    )
    subprocess.run(
        ["git", "-C", str(workspace), "remote", "add", "origin", origin],
        check=True,
        env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent"},
    )
    return workspace


def test_ambient_fixture_reaches_an_unisolated_git(ambient_worker):
    """Guard the fixture: plain inherited Git really would send A."""

    transport, generator = ambient_worker(SELECTED)
    try:
        subprocess.run(["git", "ls-remote", REMOTE], capture_output=True, check=False)
        assert git_transport.basic_authorization(AMBIENT) in transport.header_values()
    finally:
        generator.close()


@pytest.mark.parametrize("token", [SELECTED, None])
def test_remote_environment_overlaid_on_worker_env_stays_isolated(
    ambient_worker, token
):
    """A runner that merges ``os.environ`` under the overlay still sends only B."""

    transport, generator = ambient_worker(token)
    try:
        env = {
            **os.environ,
            **build_remote_git_environment(
                token, base_env=os.environ, remote_url=REMOTE
            ),
        }
        result = subprocess.run(
            ["git", "ls-remote", REMOTE], env=env, capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr
        _assert_sent_only(transport, token)
        # gh reads these before its login cache; empty values are unset.
        assert env["GH_TOKEN"] == env["GITHUB_TOKEN"] == ""
        assert env["GH_CONFIG_DIR"] == env["XDG_CONFIG_HOME"] == "/nonexistent"
    finally:
        generator.close()


def test_non_https_remote_gets_no_credential():
    env = build_remote_git_environment(
        SELECTED, base_env={"PATH": "/usr/bin"}, remote_url="/srv/repo.git"
    )
    assert SELECTED not in env.values()
    assert env["HOME"] == "/nonexistent"


@pytest.mark.asyncio
@pytest.mark.parametrize("token", [SELECTED, None])
async def test_launcher_clone_uses_selected_credential_or_none(
    ambient_worker, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, token
):
    from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
    from moonmind.workflows.temporal.runtime.launcher import ManagedRuntimeLauncher
    from moonmind.workflows.temporal.runtime.store import ManagedRunStore

    monkeypatch.setenv("MOONMIND_AGENT_RUNTIME_STORE", str(tmp_path / "store"))
    transport, generator = ambient_worker(token)
    try:
        launcher = ManagedRuntimeLauncher(ManagedRunStore(tmp_path / "managed_runs"))
        request = AgentExecutionRequest(
            agent_kind="managed",
            agent_id="agent-1",
            execution_profile_ref="default-managed",
            correlation_id="corr-1",
            idempotency_key="run-1",
            workspace_spec={"repository": "owner/repo"},
        )
        git_env = launcher._launch_git_environment(request, token)
        resolved = await launcher._prepare_workspace(
            run_id="run-1", request=request, workspace_path=None, git_env=git_env
        )
        assert resolved is not None
        assert (Path(resolved) / "README.md").exists()
        _assert_sent_only(transport, token)
    finally:
        generator.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("token", [SELECTED, ""])
async def test_session_controller_clone_overlaid_on_worker_env_sends_only_selected(
    ambient_worker, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, token
):
    from moonmind.schemas.managed_session_models import LaunchCodexManagedSessionRequest
    from moonmind.workflows.temporal.runtime import managed_session_controller
    from moonmind.workflows.temporal.runtime.managed_session_controller import (
        DockerCodexManagedSessionController,
        _default_command_runner,
    )

    async def selected_access(_request, **_kwargs):
        return token, None

    monkeypatch.setattr(
        managed_session_controller, "_session_github_access", selected_access
    )
    transport, generator = ambient_worker(token or None)
    try:
        request = LaunchCodexManagedSessionRequest(
            agentRunId="mm:task-1",
            sessionId="sess-1",
            threadId="logical-thread-1",
            workspacePath=str(tmp_path / "mm:task-1" / "repo"),
            sessionWorkspacePath=str(tmp_path / "mm:task-1" / "session"),
            artifactSpoolPath=str(tmp_path / "mm:task-1" / "artifacts"),
            codexHomePath="/home/app/.codex",
            imageRef="ghcr.io/moonladderstudios/moonmind:latest",
            workspaceSpec={"repository": "owner/repo"},
        )
        git_env = await DockerCodexManagedSessionController._git_host_environment(
            request
        )
        # The production runner overlays this on the worker's os.environ.
        code, _stdout, stderr = await _default_command_runner(
            ("git", "clone", REMOTE, str(tmp_path / "clone")), env=git_env
        )
        assert code == 0, stderr
        _assert_sent_only(transport, token or None)
    finally:
        generator.close()


@pytest.mark.parametrize("token", [SELECTED, ""])
def test_activity_workspace_env_uses_selected_push_credential_without_fallback(
    ambient_worker, tmp_path: Path, token
):
    """An unavailable selected push credential never becomes ambient A."""

    from moonmind.workflows.temporal.activity_runtime import (
        TemporalAgentRuntimeActivities,
    )

    transport, generator = ambient_worker(SELECTED)
    try:
        workspace = _clone_with_origin(tmp_path)
        env = TemporalAgentRuntimeActivities._workspace_command_env(
            str(workspace), github_token=token
        )
        assert AMBIENT not in env.values()
        result = subprocess.run(
            ["git", "-C", str(workspace), "ls-remote", "origin"],
            env=env,
            capture_output=True,
            text=True,
        )
        assert (result.returncode == 0) is bool(token), result.stderr
        _assert_sent_only(transport, token or None)
        assert env.get("GH_TOKEN", "") == token
    finally:
        generator.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("token", [SELECTED, ""])
async def test_workspace_publication_reads_origin_with_selected_credential_only(
    ambient_worker, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, token
):
    """The real publisher fetches the base through B (or anonymously), never A.

    The synthetic transport serves dumb HTTP, so the later push fails; the
    fetch before it already proves which credential the publisher's Git used.
    """

    import hashlib

    from moonmind.config.settings import settings
    from moonmind.omnigent.harness_platform.failures import HarnessPlatformError
    from moonmind.omnigent.workspace_publication import (
        OmnigentWorkspacePublicationService,
    )
    from moonmind.workflows.temporal.runtime.workspace_locators import (
        SandboxWorkspaceRecord,
        SandboxWorkspaceRecordStore,
    )

    monkeypatch.setattr(settings.security, "high_security_mode", False)
    transport, generator = ambient_worker(token or None)
    try:
        workflow_id, step_id = "workflow", "step-1"
        workspace_id = hashlib.sha256(f"{workflow_id}:{step_id}".encode()).hexdigest()[
            :24
        ]
        workspace = tmp_path / "temporal_sandbox" / workspace_id / "repo"
        workspace.parent.mkdir(parents=True)
        subprocess.run(
            ["git", "clone", "-q", REMOTE, str(workspace)],
            check=True,
            env=build_remote_git_environment(
                token, base_env=os.environ, remote_url=REMOTE
            ),
        )
        transport.requests.clear()
        (workspace / "work.txt").write_text("saved work\n")
        SandboxWorkspaceRecordStore(tmp_path).ensure(
            SandboxWorkspaceRecord(workspace_id, workflow_id, step_id, "repo")
        )
        with pytest.raises(HarnessPlatformError):
            await OmnigentWorkspacePublicationService(tmp_path).publish_workspace(
                workspace_locator={
                    "kind": "sandbox",
                    "workspaceId": workspace_id,
                    "relativePath": "repo",
                },
                current_workflow_id=workflow_id,
                current_step_execution_id=step_id,
                publication_identity="publication-1",
                publish_mode="branch",
                base_branch="main",
                repository="owner/repo",
                github_token=token,
            )
        assert any("/info/refs" in item.path for item in transport.requests)
        _assert_sent_only(transport, token or None)
    finally:
        generator.close()
