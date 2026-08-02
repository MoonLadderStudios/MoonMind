import hashlib

import pytest

from moonmind.repositories.lore_adapter import (
    LORE_CHECKPOINT_TOO_LARGE,
    LORE_EXTERNAL_SCAN_FAILED,
    LORE_UNSUPPORTED_RUNTIME_LANE,
    LORE_WORKSPACE_INVALID,
    LoreRepositoryProviderAdapter,
    LoreImmutableObjectCache,
    LoreDeltaCheckpoint,
    LoreWorkspaceError,
)
from moonmind.repositories.lore_checkpoints import LORE_CHECKPOINT_CONTENT_TYPE
from moonmind.repositories.lore_runtime import (
    build_lore_repository_adapter_from_environment,
)
from moonmind.schemas.workspace_locator_models import SandboxWorkspaceLocator
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.workflows.temporal.runtime.launcher import ManagedRuntimeLauncher
from moonmind.workflows.temporal.runtime.store import ManagedRunStore
from moonmind.omnigent.oauth_host_runtime import OmnigentOAuthHostRuntime


class FakeLoreClient:
    def __init__(self):
        self.calls = []
        self.changed = []
        self.staged = []
        self.scan_ok = True

    def materialize(
        self,
        *,
        repository,
        revision,
        destination,
        connection_ref,
        client_evidence,
    ):
        self.calls.append(("connection", connection_ref, dict(client_evidence)))
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
    assert [call[0] for call in client.calls] == ["connection", "materialize"]
    managed = adapter.bind_workspace(workspace, runtime_lane="managed_runtime")
    omnigent = adapter.bind_workspace(workspace, runtime_lane="omnigent")
    assert (
        managed.authority_locator
        == omnigent.authority_locator
        == workspace.authority_locator
    )
    assert managed.mount_mode == "direct_path" and omnigent.mount_mode == "bind_mount"


def test_failed_materialization_leaves_authority_retryable(tmp_path):
    client = FakeLoreClient()
    adapter = LoreRepositoryProviderAdapter(client)
    locator = SandboxWorkspaceLocator(workspaceId="run-1", relativePath="repo")
    original = client.materialize

    def fail_after_writing(**kwargs):
        original(**kwargs)
        raise RuntimeError("transient failure")

    client.materialize = fail_after_writing
    authority = tmp_path / "repo"
    with pytest.raises(RuntimeError, match="transient failure"):
        adapter.prepare_workspace(
            repository="Tactics",
            branch="main",
            revision_signature="rev-1",
            locator=locator,
            authority_path=authority,
            connection_ref="repository-connection:lore",
            client_evidence={},
        )
    assert not authority.exists()
    assert not list(tmp_path.glob(".repo.materializing-*"))

    client.materialize = original
    adapter.prepare_workspace(
        repository="Tactics",
        branch="main",
        revision_signature="rev-1",
        locator=locator,
        authority_path=authority,
        connection_ref="repository-connection:lore",
        client_evidence={},
    )
    assert authority.is_dir()


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
    assert (
        adapter.decode_checkpoint(adapter.encode_checkpoint(checkpoint)) == checkpoint
    )
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


@pytest.mark.parametrize("tamper", ["size", "digest", "paths"])
def test_restore_rejects_tampered_checkpoint_before_materialization(tmp_path, tamper):
    client, adapter, locator, workspace = prepared(tmp_path)
    client.changed = ["Content/root.uasset"]
    checkpoint = adapter.capture_checkpoint(workspace)
    values = dict(checkpoint.__dict__)
    if tamper == "size":
        values["total_bytes"] += 1
    elif tamper == "digest":
        values["digest"] = "sha256:" + "0" * 64
    else:
        values["changed_paths"] = ("Content/other.uasset",)
    invalid = LoreDeltaCheckpoint(**values)

    with pytest.raises(LoreWorkspaceError, match=LORE_WORKSPACE_INVALID):
        adapter.restore_checkpoint(
            invalid,
            repository="Tactics",
            branch="main",
            locator=locator,
            authority_path=tmp_path / f"invalid-{tamper}",
            connection_ref="repository-connection:lore",
            client_evidence={},
        )
    assert [call[0] for call in client.calls].count("materialize") == 1


def test_restore_rejects_actual_oversize_when_metadata_claims_small(tmp_path):
    client, _, locator, _ = prepared(tmp_path)
    adapter = LoreRepositoryProviderAdapter(client, checkpoint_limit_bytes=2)
    checkpoint = LoreDeltaCheckpoint(
        base_revision="rev-1",
        changed_paths=("Content/root.uasset",),
        staged_paths=(),
        files={"Content/root.uasset": b"large"},
        total_bytes=1,
        digest="sha256:" + "0" * 64,
    )
    with pytest.raises(LoreWorkspaceError, match=LORE_CHECKPOINT_TOO_LARGE):
        adapter.restore_checkpoint(
            checkpoint,
            repository="Tactics",
            branch="main",
            locator=locator,
            authority_path=tmp_path / "oversize",
            connection_ref="repository-connection:lore",
            client_evidence={},
        )
    assert [call[0] for call in client.calls].count("materialize") == 1


def test_immutable_cache_verifies_content_and_excludes_private_state(tmp_path):
    cache = LoreImmutableObjectCache(tmp_path / "cache")
    adapter = LoreRepositoryProviderAdapter(FakeLoreClient(), immutable_cache=cache)
    digest = "sha256:" + __import__("hashlib").sha256(b"object").hexdigest()
    cached = adapter.publish_cache_object(
        endpoint="lore.example",
        repository="Tactics",
        client_compatibility="1",
        object_digest=digest,
        content=b"object",
    )
    identity = {
        "endpoint": "lore.example",
        "repository": "Tactics",
        "client_compatibility": "1",
        "object_digest": digest,
    }
    assert adapter.read_cache_object(**identity) == b"object"
    assert cached.stat().st_mode & 0o222 == 0

    cached.chmod(0o600)
    cached.write_bytes(b"tampered")
    with pytest.raises(LoreWorkspaceError, match=LORE_WORKSPACE_INVALID):
        adapter.read_cache_object(**identity)
    assert not cached.exists()

    with pytest.raises(LoreWorkspaceError, match="private cache identity"):
        adapter.publish_cache_object(
            endpoint="lore.example",
            repository=".lore/journals",
            client_compatibility="1",
            object_digest=digest,
            content=b"object",
        )


@pytest.mark.asyncio
async def test_managed_launcher_delegates_lore_preparation_and_reuses_revision(
    tmp_path,
):
    client = FakeLoreClient()
    adapter = LoreRepositoryProviderAdapter(client)
    launcher = ManagedRuntimeLauncher(
        ManagedRunStore(tmp_path / "managed_runs"),
        lore_repository_adapter=adapter,
    )
    request = AgentExecutionRequest(
        agentKind="managed",
        agentId="agent",
        correlationId="corr",
        idempotencyKey="key",
        workspaceSpec={
            "provider": "lore",
            "repository": "Tactics",
            "branch": "main",
            "revisionSignature": "rev-1",
            "connectionRef": "repository-connection:lore",
            "clientEvidence": {"clientVersion": "1"},
        },
    )
    first = await launcher._prepare_workspace_path(
        run_id="run-1", request=request, workspace_path=None
    )
    second = await launcher._prepare_workspace_path(
        run_id="run-1", request=request, workspace_path=None
    )
    assert first == second
    assert [call[0] for call in client.calls].count("materialize") == 1


@pytest.mark.asyncio
async def test_managed_launcher_rejects_foreign_lore_locator(tmp_path):
    launcher = ManagedRuntimeLauncher(
        ManagedRunStore(tmp_path / "managed_runs"),
        lore_repository_adapter=LoreRepositoryProviderAdapter(FakeLoreClient()),
    )
    request = AgentExecutionRequest(
        agentKind="managed",
        agentId="agent",
        correlationId="corr",
        idempotencyKey="key",
        workspaceSpec={
            "provider": "lore",
            "repository": "Tactics",
            "branch": "main",
            "revisionSignature": "rev-1",
            "workspaceLocator": {
                "kind": "sandbox",
                "workspaceId": "another-run",
                "relativePath": "repo",
            },
        },
    )
    with pytest.raises(RuntimeError, match="does not belong to the current run"):
        await launcher._prepare_workspace_path(
            run_id="run-1", request=request, workspace_path=None
        )


@pytest.mark.asyncio
async def test_omnigent_launcher_binds_prepared_lore_sandbox_without_checkout(tmp_path):
    workflow_id, step_id = "workflow", "step"
    workspace_id = hashlib.sha256(
        f"{workflow_id}:{step_id}".encode("utf-8")
    ).hexdigest()[:24]
    locator = SandboxWorkspaceLocator(workspaceId=workspace_id, relativePath="repo")
    authority = tmp_path / "temporal_sandbox" / workspace_id / "repo"
    client = FakeLoreClient()
    adapter = LoreRepositoryProviderAdapter(client)
    launcher = ManagedRuntimeLauncher(
        ManagedRunStore(tmp_path / "managed_runs"),
        lore_repository_adapter=adapter,
    )
    managed_path = await launcher._prepare_workspace_path(
        run_id="managed-run",
        workspace_path=None,
        request=AgentExecutionRequest(
            agentKind="managed",
            agentId="agent",
            correlationId=workflow_id,
            idempotencyKey=step_id,
            workspaceSpec={
                "provider": "lore",
                "repository": "Tactics",
                "branch": "main",
                "revisionSignature": "rev-1",
                "connectionRef": "repository-connection:lore",
                "workspaceLocator": locator.model_dump(by_alias=True),
            },
        ),
    )
    assert managed_path == str(authority)
    runtime = OmnigentOAuthHostRuntime(
        client=object(),
        workspace_root=tmp_path,
        lore_repository_adapter=adapter,
    )
    resolved = await runtime._prepare_workspace(
        workspace_locator=locator.model_dump(by_alias=True),
        current_workflow_id=workflow_id,
        current_step_execution_id=step_id,
        repository_provider="lore",
        omnigent_isolation_verified=True,
    )
    assert resolved == authority
    assert [call[0] for call in client.calls].count("materialize") == 1
    assert runtime._last_workspace_evidence["materialization"]["action"] == (
        "reused_lore_authority"
    )


@pytest.mark.asyncio
async def test_omnigent_launcher_materializes_fresh_lore_sandbox(tmp_path):
    workflow_id, step_id = "workflow", "fresh-step"
    workspace_id = hashlib.sha256(
        f"{workflow_id}:{step_id}".encode("utf-8")
    ).hexdigest()[:24]
    locator = SandboxWorkspaceLocator(workspaceId=workspace_id, relativePath="repo")
    client = FakeLoreClient()
    runtime = OmnigentOAuthHostRuntime(
        client=object(),
        workspace_root=tmp_path,
        lore_repository_adapter=LoreRepositoryProviderAdapter(client),
    )

    resolved = await runtime._prepare_workspace(
        workspace_locator=locator.model_dump(by_alias=True),
        current_workflow_id=workflow_id,
        current_step_execution_id=step_id,
        repository_source="Tactics",
        repository_provider="lore",
        repository_connection_ref="repository-connection:lore",
        repository_client_evidence={"clientVersion": "1"},
        starting_branch="main",
        checkout_commit="rev-1",
        omnigent_isolation_verified=True,
    )

    assert resolved == tmp_path / "temporal_sandbox" / workspace_id / "repo"
    assert [call[0] for call in client.calls].count("materialize") == 1


class FakeArtifactService:
    def __init__(self):
        self.payloads = {}
        self.content_types = {}

    async def create(self, *, content_type, **kwargs):
        artifact = type(
            "Artifact",
            (),
            {"artifact_id": "art_lore", "content_type": content_type},
        )()
        self.content_types[artifact.artifact_id] = content_type
        return artifact, object()

    async def write_payload_complete(
        self, *, artifact_id, payload, content_type, **kwargs
    ):
        self.payloads[artifact_id] = payload
        return type("Artifact", (), {"artifact_id": artifact_id})()

    async def read(self, *, artifact_id, **kwargs):
        artifact = type(
            "Artifact",
            (),
            {"artifact_id": artifact_id, "content_type": self.content_types[artifact_id]},
        )()
        return artifact, self.payloads[artifact_id]


@pytest.mark.asyncio
async def test_durable_lore_checkpoint_round_trip_scans_and_restages(tmp_path):
    client, adapter, locator, workspace = prepared(tmp_path)
    client.changed = ["Content/root.uasset"]
    client.staged = ["Content/root.uasset"]
    (workspace.authority_path / "Content/root.uasset").write_bytes(b"dirty")
    artifacts = FakeArtifactService()
    launcher = ManagedRuntimeLauncher(
        ManagedRunStore(tmp_path / "managed_runs"),
        artifact_service=artifacts,
        lore_repository_adapter=adapter,
    )

    checkpoint_ref = await launcher.capture_lore_workspace_checkpoint(
        locator=locator, authority_path=workspace.authority_path
    )
    assert artifacts.content_types[checkpoint_ref] == LORE_CHECKPOINT_CONTENT_TYPE
    restored = await launcher.restore_lore_workspace_checkpoint(
        checkpoint_ref,
        repository="Tactics",
        branch="main",
        locator=locator,
        authority_path=tmp_path / "cold-restored",
        connection_ref="repository-connection:lore",
        client_evidence={},
    )

    assert (restored.authority_path / "Content/root.uasset").read_bytes() == b"dirty"
    assert [call[0] for call in client.calls].count("materialize") == 2
    assert client.calls[-2][0] == "scan"
    assert client.calls[-1] == ("stage", ("Content/root.uasset",))


def test_production_lore_factory_requires_complete_pin(monkeypatch, tmp_path):
    executable = tmp_path / "lore"
    executable.write_bytes(b"binary")
    monkeypatch.setenv("MOONMIND_LORE_EXECUTABLE", str(executable))
    monkeypatch.delenv("MOONMIND_LORE_EXECUTABLE_SHA256", raising=False)
    with pytest.raises(LoreWorkspaceError, match="both required"):
        build_lore_repository_adapter_from_environment()

    monkeypatch.setenv(
        "MOONMIND_LORE_EXECUTABLE_SHA256", hashlib.sha256(b"binary").hexdigest()
    )
    monkeypatch.setenv("MOONMIND_LORE_IMMUTABLE_CACHE_ROOT", str(tmp_path / "cache"))
    assert isinstance(
        build_lore_repository_adapter_from_environment(),
        LoreRepositoryProviderAdapter,
    )
