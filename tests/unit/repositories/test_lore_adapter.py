from pathlib import Path

import pytest

from moonmind.repositories.lore_adapter import (
    LORE_CHECKPOINT_TOO_LARGE,
    LORE_EXTERNAL_SCAN_FAILED,
    LORE_UNSUPPORTED_RUNTIME_LANE,
    LoreRepositoryProviderAdapter,
    LoreWorkspaceError,
)
from moonmind.schemas.workspace_locator_models import SandboxWorkspaceLocator


class FakeLoreClient:
    def __init__(self):
        self.calls = []
        self.changed = []
        self.staged = []
        self.scan_ok = True

    def materialize(self, *, repository, revision, destination):
        self.calls.append(("materialize", revision))
        (destination / "Content").mkdir()
        (destination / "Content/root.uasset").write_bytes(b"root")
        (destination / "Plugins/P/Content").mkdir(parents=True)
        (destination / "Plugins/P/Content/plugin.uasset").write_bytes(b"plugin")
        return {"revisionSignature": revision, "completeTree": True}

    def scan_external_changes(self, *, workspace):
        self.calls.append(("scan", workspace))
        return {"success": self.scan_ok}

    def status(self, *, workspace):
        self.calls.append(("status", workspace))
        return {"changedPaths": self.changed, "stagedPaths": self.staged}

    def stage_paths(self, *, workspace, paths):
        self.calls.append(("stage", tuple(paths)))


def prepared(tmp_path):
    client = FakeLoreClient()
    adapter = LoreRepositoryProviderAdapter(client, checkpoint_limit_bytes=32)
    locator = SandboxWorkspaceLocator(workspaceId="run-1", relativePath="repo")
    workspace = adapter.prepare_workspace(
        repository="Tactics",
        branch="main",
        revision_signature="rev-1",
        locator=locator,
        authority_path=tmp_path / "repo",
        connection_ref="repository-connection:lore",
        client_evidence={"clientVersion": "1", "executableSha256": "a" * 64},
    )
    return client, adapter, locator, workspace


def test_prepares_complete_revision_once_and_binds_same_authority(tmp_path):
    client, adapter, _, workspace = prepared(tmp_path)
    assert (workspace.authority_path / "Content/root.uasset").is_file()
    assert (workspace.authority_path / "Plugins/P/Content/plugin.uasset").is_file()
    assert [call[0] for call in client.calls] == ["materialize"]
    managed = adapter.bind_workspace(workspace, runtime_lane="managed_runtime")
    omnigent = adapter.bind_workspace(workspace, runtime_lane="omnigent")
    assert (
        managed.authority_locator
        == omnigent.authority_locator
        == workspace.authority_locator
    )
    assert managed.mount_mode == "direct_path" and omnigent.mount_mode == "bind_mount"


def test_rejects_unsupported_lane_before_launch(tmp_path):
    _, adapter, _, workspace = prepared(tmp_path)
    with pytest.raises(LoreWorkspaceError, match=LORE_UNSUPPORTED_RUNTIME_LANE):
        adapter.bind_workspace(
            workspace, runtime_lane="omnigent", omnigent_isolation_verified=False
        )


def test_scan_precedes_status_and_checkpoint_and_private_state_is_rejected(tmp_path):
    client, adapter, _, workspace = prepared(tmp_path)
    adapter.inspect_workspace(workspace)
    assert [c[0] for c in client.calls[-2:]] == ["scan", "status"]
    client.changed = [".lore/journal"]
    with pytest.raises(LoreWorkspaceError, match="private checkpoint path"):
        adapter.capture_checkpoint(workspace)
    assert [c[0] for c in client.calls[-2:]] == ["scan", "status"]
    client.scan_ok = False
    with pytest.raises(LoreWorkspaceError, match=LORE_EXTERNAL_SCAN_FAILED):
        adapter.inspect_workspace(workspace)
    assert client.calls[-1][0] == "scan"


def test_dirty_restore_is_bounded_scanned_and_restages_only_intended_paths(tmp_path):
    client, adapter, locator, workspace = prepared(tmp_path)
    changed = workspace.authority_path / "Content/root.uasset"
    changed.write_bytes(b"changed")
    client.changed = ["Content/root.uasset", "Plugins/P/Content/plugin.uasset"]
    client.staged = ["Content/root.uasset"]
    (workspace.authority_path / "Plugins/P/Content/plugin.uasset").unlink()
    checkpoint = adapter.capture_checkpoint(workspace)
    restored = adapter.restore_checkpoint(
        checkpoint,
        repository="Tactics",
        branch="main",
        locator=locator,
        authority_path=tmp_path / "restored",
        connection_ref="repository-connection:lore",
        client_evidence={},
    )
    assert (restored.authority_path / "Content/root.uasset").read_bytes() == b"changed"
    assert not (restored.authority_path / "Plugins/P/Content/plugin.uasset").exists()
    assert client.calls[-2][0] == "scan" and client.calls[-1] == (
        "stage",
        ("Content/root.uasset",),
    )


def test_oversized_checkpoint_fails_before_artifact_creation(tmp_path):
    client, _, _, workspace = prepared(tmp_path)
    adapter = LoreRepositoryProviderAdapter(client, checkpoint_limit_bytes=2)
    client.changed = ["Content/root.uasset"]
    with pytest.raises(LoreWorkspaceError, match=LORE_CHECKPOINT_TOO_LARGE):
        adapter.capture_checkpoint(workspace)


def test_restore_rechecks_symlink_containment(tmp_path):
    client, adapter, locator, workspace = prepared(tmp_path)
    client.changed = ["Content/root.uasset"]
    client.staged = []
    checkpoint = adapter.capture_checkpoint(workspace)
    original = client.materialize

    def unsafe(**kwargs):
        result = original(**kwargs)
        (kwargs["destination"] / "Content/root.uasset").unlink()
        (kwargs["destination"] / "Content/root.uasset").symlink_to("/etc/passwd")
        return result

    client.materialize = unsafe
    with pytest.raises(LoreWorkspaceError, match="symlink escapes"):
        adapter.restore_checkpoint(
            checkpoint,
            repository="Tactics",
            branch="main",
            locator=locator,
            authority_path=tmp_path / "unsafe",
            connection_ref="repository-connection:lore",
            client_evidence={},
        )
