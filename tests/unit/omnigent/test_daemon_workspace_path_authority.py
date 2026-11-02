"""A remote daemon owns its paths; worker symlinks cannot rewrite them."""

import pytest

from moonmind.omnigent.host_services.workspace import resolve_daemon_workspace_root
from moonmind.workflows.temporal.runtime.workspace_locators import (
    WorkspaceLocatorResolutionError,
    daemon_visible_workspace_path,
)


@pytest.fixture
def remote_roots(tmp_path, monkeypatch):
    worker = tmp_path / "worker"
    worker.mkdir()
    unrelated = tmp_path / "unrelated-worker-directory"
    unrelated.mkdir()
    daemon = tmp_path / "daemon-owned-path"
    daemon.symlink_to(unrelated, target_is_directory=True)
    monkeypatch.setenv("WORKFLOW_DOCKER_DAEMON_MODE", "remote")
    monkeypatch.setenv("WORKFLOW_WORKSPACE_ROOT", str(worker))
    monkeypatch.setenv("WORKFLOW_WORKSPACE_DAEMON_ROOT", str(daemon))
    return worker, daemon, unrelated


@pytest.mark.asyncio
async def test_inspected_daemon_root_does_not_follow_worker_symlinks(remote_roots):
    _, daemon, _ = remote_roots

    async def runner(argv):
        assert argv == [
            "docker",
            "volume",
            "inspect",
            "--format",
            "{{.Mountpoint}}",
            "agent_workspaces",
        ]
        return 0, str(daemon) + "\n", ""

    assert (
        await resolve_daemon_workspace_root(
            runner=runner, workspace_volume="agent_workspaces"
        )
        == daemon
    )


@pytest.mark.parametrize("explicit", [False, True])
def test_remote_workspace_translation_preserves_the_daemon_root(remote_roots, explicit):
    worker, daemon, _ = remote_roots
    request = worker / "job" / "repo"
    translated = daemon_visible_workspace_path(
        request, **({"daemon_root": daemon} if explicit else {})
    )
    assert translated == daemon / "job" / "repo"


def test_worker_containment_still_rejects_a_symlink_escape(remote_roots):
    worker, _, unrelated = remote_roots
    (worker / "escape").symlink_to(unrelated, target_is_directory=True)
    with pytest.raises(
        WorkspaceLocatorResolutionError, match="outside the daemon mapping authority"
    ):
        daemon_visible_workspace_path(worker / "escape" / "repo")
