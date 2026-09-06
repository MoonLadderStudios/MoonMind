"""Safe artifact, checkpoint, and authorized existing-workspace sources.

Production-boundary regression coverage for MoonLadderStudios/MoonMind#4014:
single-source compilation, grant-bound existing workspaces, artifact-service
admission, streamed digest verification with resource budgets, staged
promotion, git-authority neutralization, and self-contained restore.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import tarfile
from types import SimpleNamespace

import pytest

from moonmind.omnigent.harness_platform.failures import HarnessPlatformError
from moonmind.omnigent.host_services.workspace import (
    OmnigentWorkspaceMaterializer,
    compile_workspace_source,
)
from moonmind.omnigent.workspace_artifacts import (
    WorkspaceArtifactProjectionError,
    WorkspaceArtifactProjector,
)
from moonmind.workflows.temporal.runtime.workspace_locators import (
    build_materialization_fingerprint,
    parse_existing_workspace_grant,
)


def _request(spec: dict) -> SimpleNamespace:
    return SimpleNamespace(
        workspace_spec=spec,
        parameters={},
        input_refs=[],
        step_execution=None,
        correlation_id="workflow-1",
        idempotency_key="step-1",
    )


def _workspace_id() -> str:
    return hashlib.sha256(b"workflow-1:step-1").hexdigest()[:24]


def _grant(**overrides: object) -> dict:
    payload: dict[str, object] = {
        "workspaceId": _workspace_id(),
        "grantedWorkflowId": "workflow-1",
        "grantedStepExecutionId": "step-1",
    }
    payload.update(overrides)
    return payload


def _tar_bytes(members: list[tuple[str, bytes] | tuple[str, bytes, str]]) -> bytes:
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w:gz") as bundle:
        for entry in members:
            name, payload = entry[0], entry[1]
            linkname = entry[2] if len(entry) > 2 else ""
            member = tarfile.TarInfo(name)
            if linkname:
                member.type = tarfile.SYMTYPE
                member.linkname = linkname
            else:
                member.size = len(payload)
                bundle.addfile(member, io.BytesIO(payload))
                continue
            bundle.addfile(member)
    return archive.getvalue()


class _FakeArtifactService:
    """Minimal artifact-service double with metadata + chunked reads."""

    def __init__(
        self,
        payloads: dict[str, bytes],
        *,
        workflow_id: str = "workflow-1",
        restricted: bool = False,
    ) -> None:
        self.payloads = payloads
        self.workflow_id = workflow_id
        self.restricted = restricted

    async def get_metadata(self, *, artifact_id: str, principal: str):
        payload = self.payloads[artifact_id]
        digest = "sha256:" + hashlib.sha256(payload).hexdigest()
        artifact = SimpleNamespace(
            size_bytes=len(payload),
            sha256=digest,
            status="COMPLETE",
            redaction_level="RESTRICTED" if self.restricted else "NONE",
            expires_at=None,
        )
        links = [SimpleNamespace(workflow_id=self.workflow_id)]
        return artifact, links

    async def read_chunks(
        self, *, artifact_id: str, principal: str, allow_restricted_raw: bool,
        chunk_size: int,
    ):
        return SimpleNamespace(), iter((self.payloads[artifact_id],))


# --- Requirement 1: exactly one source, no raw-path precedence -------------


def test_compile_rejects_conflicting_source_aliases():
    with pytest.raises(HarnessPlatformError, match="conflicting"):
        compile_workspace_source(
            {
                "workspaceSource": {"kind": "scratch"},
                "workspacePath": "/work/agent_jobs/other",
            }
        )
    with pytest.raises(HarnessPlatformError, match="conflicting"):
        compile_workspace_source(
            {
                "workspacePath": "/work/agent_jobs/other",
                "workspaceLocator": {
                    "kind": "sandbox",
                    "workspaceId": _workspace_id(),
                },
            }
        )


def test_compile_raw_path_without_grant_fails_closed():
    with pytest.raises(HarnessPlatformError, match="grant"):
        compile_workspace_source({"workspacePath": "/work/agent_jobs/other"})


def test_compile_raw_path_with_grant_decodes_historical_existing_workspace():
    compiled = compile_workspace_source(
        {
            "workspacePath": "/work/agent_jobs/other",
            "existingWorkspaceGrant": _grant(),
        }
    )
    assert compiled["kind"] == "existing_workspace"


def test_compile_artifact_source_requires_digest():
    with pytest.raises(HarnessPlatformError, match="digest"):
        compile_workspace_source(
            {"workspaceSource": {"kind": "artifact", "artifactRef": "artifact://a"}}
        )
    compiled = compile_workspace_source(
        {
            "workspaceSource": {
                "kind": "artifact",
                "artifactRef": "artifact://a",
                "digest": "sha256:abc",
            }
        }
    )
    assert compiled["digest"] == "sha256:abc"


# --- Requirement 8: grant validation ----------------------------------------


def test_grant_wrong_owner_fails():
    with pytest.raises(Exception, match="not issued to the current execution"):
        parse_existing_workspace_grant(
            _grant(grantedWorkflowId="workflow-9"),
            expected_workflow_id="workflow-1",
            expected_step_execution_id="step-1",
            expected_workspace_id=_workspace_id(),
        )


def test_grant_stale_expiry_fails():
    with pytest.raises(Exception, match="expired"):
        parse_existing_workspace_grant(
            _grant(expiresAt="2000-01-01T00:00:00Z"),
            expected_workflow_id="workflow-1",
            expected_step_execution_id="step-1",
            expected_workspace_id=_workspace_id(),
        )


@pytest.mark.asyncio
async def test_existing_workspace_grant_is_required_and_read_only_checked(
    tmp_path,
):
    workspace_id = _workspace_id()
    existing = tmp_path / "temporal_sandbox" / workspace_id / "repo"
    existing.mkdir(parents=True)

    async def fail_runner(*_a, **_k):  # pragma: no cover - must not run
        raise AssertionError("existing workspace must not clone")

    materializer = OmnigentWorkspaceMaterializer(
        command_runner=fail_runner, workspace_root=tmp_path
    )
    # No grant at all: historical raw path fails closed.
    with pytest.raises(HarnessPlatformError, match="grant"):
        await materializer.materialize(_request({"workspacePath": str(existing)}))
    # Read-only grant with a writable mutation fails closed.
    with pytest.raises(HarnessPlatformError, match="read-only"):
        await materializer.materialize(
            _request(
                {
                    "workspaceLocator": {
                        "kind": "sandbox",
                        "workspaceId": workspace_id,
                        "relativePath": "repo",
                    },
                    "workspaceSource": {
                        "kind": "existing_workspace",
                        "grant": _grant(readOnly=True),
                    },
                }
            ),
            mutation="allowed",
        )
    # Matching writable grant reuses the directory with no clone.
    result = await materializer.materialize(
        _request(
            {
                "workspaceLocator": {
                    "kind": "sandbox",
                    "workspaceId": workspace_id,
                    "relativePath": "repo",
                },
                "workspaceSource": {
                    "kind": "existing_workspace",
                    "grant": _grant(),
                },
            }
        ),
        mutation="allowed",
    )
    assert result["kind"] == "bind"


# --- Requirement 2: artifact-service admission -------------------------------


@pytest.mark.asyncio
async def test_read_bytes_only_gateway_fails_closed(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()

    class BytesOnly:
        async def read_bytes(self, ref: str) -> bytes:
            return b"data"

    projector = WorkspaceArtifactProjector(BytesOnly())
    with pytest.raises(WorkspaceArtifactProjectionError, match="read-bytes-only"):
        await projector.project(
            workspace,
            checkpoint_ref="artifact://checkpoint",
            workflow_id="workflow-1",
            runtime_uid=os.getuid(),
            runtime_gid=os.getgid(),
        )


@pytest.mark.asyncio
async def test_forged_family_prefix_link_is_rejected(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    payloads = {"assessment": b"{}"}
    service = _FakeArtifactService(payloads, workflow_id="workflow-1-evil")
    projector = WorkspaceArtifactProjector(service)
    with pytest.raises(WorkspaceArtifactProjectionError, match="workflow family"):
        await projector.project(
            workspace,
            attachment_refs=("artifact://assessment",),
            workflow_id="workflow-1",
            runtime_uid=os.getuid(),
            runtime_gid=os.getgid(),
        )


@pytest.mark.asyncio
async def test_restricted_bytes_require_explicit_policy(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    payloads = {"assessment": b"secret"}
    service = _FakeArtifactService(payloads, restricted=True)
    projector = WorkspaceArtifactProjector(service)
    with pytest.raises(WorkspaceArtifactProjectionError, match="restricted"):
        await projector.project(
            workspace,
            attachment_refs=("artifact://assessment",),
            workflow_id="workflow-1",
            runtime_uid=os.getuid(),
            runtime_gid=os.getgid(),
        )
    evidence = await projector.project(
        workspace,
        attachment_refs=("artifact://assessment",),
        workflow_id="workflow-1",
        runtime_uid=os.getuid(),
        runtime_gid=os.getgid(),
        allow_restricted=True,
    )
    assert evidence["attachments"][0]["ref"] == "artifact://assessment"


@pytest.mark.asyncio
async def test_digest_mismatch_fails_without_ready_marker(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    checkpoint = _tar_bytes([("file.txt", b"data")])
    service = _FakeArtifactService({"checkpoint": checkpoint})
    projector = WorkspaceArtifactProjector(service)
    with pytest.raises(WorkspaceArtifactProjectionError, match="digest"):
        await projector.project(
            workspace,
            checkpoint_ref="artifact://checkpoint",
            workflow_id="workflow-1",
            runtime_uid=os.getuid(),
            runtime_gid=os.getgid(),
            expected_digests={"artifact://checkpoint": "sha256:" + "0" * 64},
        )
    assert not (workspace / "file.txt").exists()


# --- Requirements 3/4: budgets, paths, staging -------------------------------


@pytest.mark.asyncio
async def test_checkpoint_absolute_path_is_rejected_without_partial_content(
    tmp_path,
):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "keep.txt").write_text("keep", encoding="utf-8")
    checkpoint = _tar_bytes([("/abs.txt", b"evil")])
    projector = WorkspaceArtifactProjector(_FakeArtifactService({"c": checkpoint}))
    with pytest.raises(WorkspaceArtifactProjectionError, match="unsafe"):
        await projector.project(
            workspace,
            checkpoint_ref="artifact://c",
            workflow_id="workflow-1",
            runtime_uid=os.getuid(),
            runtime_gid=os.getgid(),
        )
    assert (workspace / "keep.txt").read_text() == "keep"
    assert not (workspace / "abs.txt").exists()


@pytest.mark.asyncio
async def test_checkpoint_link_order_attack_is_rejected(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    # A symlink extracted first must not authorize a later write through it.
    checkpoint = _tar_bytes(
        [("link", b"", "."), ("link/evil.txt", b"evil")]
    )
    projector = WorkspaceArtifactProjector(_FakeArtifactService({"c": checkpoint}))
    with pytest.raises(WorkspaceArtifactProjectionError, match="symlink|conflict"):
        await projector.project(
            workspace,
            checkpoint_ref="artifact://c",
            workflow_id="workflow-1",
            runtime_uid=os.getuid(),
            runtime_gid=os.getgid(),
        )
    assert not (workspace / "link" / "evil.txt").exists()


@pytest.mark.asyncio
async def test_checkpoint_duplicate_entries_are_rejected(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w:gz") as bundle:
        for _ in range(2):
            member = tarfile.TarInfo("dup.txt")
            payload = b"x"
            member.size = len(payload)
            bundle.addfile(member, io.BytesIO(payload))
    projector = WorkspaceArtifactProjector(
        _FakeArtifactService({"c": archive.getvalue()})
    )
    with pytest.raises(WorkspaceArtifactProjectionError, match="duplicate"):
        await projector.project(
            workspace,
            checkpoint_ref="artifact://c",
            workflow_id="workflow-1",
            runtime_uid=os.getuid(),
            runtime_gid=os.getgid(),
        )


@pytest.mark.asyncio
async def test_malformed_archive_creates_no_ready_content(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    projector = WorkspaceArtifactProjector(
        _FakeArtifactService({"c": b"not a gzip stream at all"})
    )
    with pytest.raises(WorkspaceArtifactProjectionError):
        await projector.project(
            workspace,
            checkpoint_ref="artifact://c",
            workflow_id="workflow-1",
            runtime_uid=os.getuid(),
            runtime_gid=os.getgid(),
        )
    assert list(workspace.iterdir()) == []
    assert list(workspace.parent.glob(".moonmind-staging-*")) == []


# --- Requirement 5: authoritative vs additive, marker binding -----------------


@pytest.mark.asyncio
async def test_authoritative_restore_drops_destination_residue(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "stale.txt").write_text("stale", encoding="utf-8")
    checkpoint = _tar_bytes([("fresh.txt", b"fresh")])
    projector = WorkspaceArtifactProjector(_FakeArtifactService({"c": checkpoint}))
    await projector.project(
        workspace,
        checkpoint_ref="artifact://c",
        workflow_id="workflow-1",
        runtime_uid=os.getuid(),
        runtime_gid=os.getgid(),
        authoritative_restore=True,
    )
    assert (workspace / "fresh.txt").read_text() == "fresh"
    assert not (workspace / "stale.txt").exists()


@pytest.mark.asyncio
async def test_overlay_restore_preserves_checkout_history(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / ".git" / "info").mkdir(parents=True)
    (workspace / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (workspace / "tracked.txt").write_text("base", encoding="utf-8")
    checkpoint = _tar_bytes([("tracked.txt", b"updated")])
    projector = WorkspaceArtifactProjector(_FakeArtifactService({"c": checkpoint}))
    await projector.project(
        workspace,
        checkpoint_ref="artifact://c",
        workflow_id="workflow-1",
        runtime_uid=os.getuid(),
        runtime_gid=os.getgid(),
        authoritative_restore=False,
    )
    assert (workspace / "tracked.txt").read_text() == "updated"
    assert (workspace / ".git" / "HEAD").is_file()


def test_ready_marker_binds_inputs(tmp_path):
    from moonmind.workflows.temporal.runtime.workspace_locators import (
        SandboxWorkspaceRecordStore,
    )

    store = SandboxWorkspaceRecordStore(tmp_path)
    fingerprint = build_materialization_fingerprint(
        source_kind="checkpoint",
        source_digest="sha256:abc",
        restore_contract="workspace-snapshot-v1",
        restore_version="workspace-snapshot-v1",
        input_manifest_digest="sha256:m",
        owner_workflow_id="workflow-1",
        owner_step_execution_id="step-1",
        workspace_id="ws-1",
    )
    assert store.is_materialized_for("ws-1", fingerprint) is False
    store.mark_materialized_for("ws-1", fingerprint)
    assert store.is_materialized_for("ws-1", fingerprint) is True
    changed = dict(fingerprint)
    changed["sourceDigest"] = "sha256:other"
    assert store.is_materialized_for("ws-1", changed) is False


# --- Requirement 6: git/session neutralization --------------------------------


@pytest.mark.asyncio
async def test_imported_git_authority_is_neutralized_but_history_kept(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    hooks_script = b"#!/bin/sh\necho pwned\n"
    session_secret = b"lease-token"
    checkpoint = _tar_bytes(
        [
            (".git/HEAD", b"ref: refs/heads/main\n"),
            (".git/hooks/post-checkout", hooks_script),
            (".git/config", b'[credential "https://github.com"]\n\thelper = store\n'),
            (".moonmind/session/auth.json", session_secret),
            ("run.sh", b"#!/bin/sh\necho hello\n"),
        ]
    )
    projector = WorkspaceArtifactProjector(_FakeArtifactService({"c": checkpoint}))
    await projector.project(
        workspace,
        checkpoint_ref="artifact://c",
        workflow_id="workflow-1",
        runtime_uid=os.getuid(),
        runtime_gid=os.getgid(),
    )
    # History preserved as data; executable source files are not rejected.
    assert (workspace / ".git" / "HEAD").is_file()
    assert (workspace / "run.sh").is_file()
    # Imported setup logic and old session authority are not restored.
    assert not (workspace / ".git" / "hooks" / "post-checkout").exists()
    assert "helper" not in (workspace / ".git" / "config").read_text()
    assert not (workspace / ".moonmind" / "session").exists()


# --- Requirement 7: self-contained restore ------------------------------------


@pytest.mark.asyncio
async def test_self_contained_checkpoint_restores_without_clone(tmp_path):
    workspace_id = _workspace_id()
    checkpoint = _tar_bytes(
        [("result.txt", b"private-source result"), ("notes.bin", b"\x00\x01\x02")]
    )
    service = _FakeArtifactService({"snapshot": checkpoint})

    calls: list = []

    async def runner(argv, input_bytes=None):
        calls.append(argv)
        return 0, "", ""

    materializer = OmnigentWorkspaceMaterializer(
        command_runner=runner, workspace_root=tmp_path, artifact_service=service
    )
    result = await materializer.materialize(
        _request(
            {
                "workspaceLocator": {
                    "kind": "sandbox",
                    "workspaceId": workspace_id,
                    "relativePath": "repo",
                },
                "workspaceCheckpointRestoreRef": "artifact://snapshot",
            }
        ),
        runtime_uid=os.getuid(),
        runtime_gid=os.getgid(),
    )
    assert result["kind"] == "bind"
    assert calls == []
    restored = tmp_path / "temporal_sandbox" / workspace_id / "repo"
    assert (restored / "result.txt").read_bytes() == b"private-source result"
    assert (restored / "notes.bin").read_bytes() == b"\x00\x01\x02"
    # Non-Git content round-trips with no synthetic repository identity.
    assert not (restored / ".git").exists()
    marker = json.loads(
        (
            tmp_path
            / "temporal_sandbox"
            / ".workspace_records"
            / f"{workspace_id}.materialized"
        ).read_text(encoding="utf-8")
    )
    assert marker["version"] == "materialized-v3"
