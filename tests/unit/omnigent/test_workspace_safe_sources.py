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
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

import moonmind.omnigent.workspace_artifacts as workspace_artifacts
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


# --- Requirement 3/AC4: expanded-resource budgets and failure classes --------

_FAIL_MSG = "must fail closed with no ready workspace"


def _assert_no_ready_workspace(workspace, *, expect_empty: bool = True):
    assert list(workspace.parent.glob(".moonmind-staging-*")) == []
    if expect_empty:
        assert list(workspace.iterdir()) == []


@pytest.mark.asyncio
async def test_checkpoint_expanded_bytes_budget_rejected(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    monkeypatch.setattr(workspace_artifacts, "MAX_EXPANDED_BYTES", 10)
    checkpoint = _tar_bytes([("a.txt", b"x" * 8), ("b.txt", b"y" * 8)])
    projector = WorkspaceArtifactProjector(_FakeArtifactService({"c": checkpoint}))
    with pytest.raises(WorkspaceArtifactProjectionError, match="expansion"):
        await projector.project(
            workspace,
            checkpoint_ref="artifact://c",
            workflow_id="workflow-1",
            runtime_uid=os.getuid(),
            runtime_gid=os.getgid(),
        )
    _assert_no_ready_workspace(workspace)


@pytest.mark.asyncio
async def test_checkpoint_file_count_budget_rejected(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    monkeypatch.setattr(workspace_artifacts, "MAX_ARCHIVE_FILES", 2)
    checkpoint = _tar_bytes(
        [(f"f{i}.txt", b"x") for i in range(3)]
    )
    projector = WorkspaceArtifactProjector(_FakeArtifactService({"c": checkpoint}))
    with pytest.raises(WorkspaceArtifactProjectionError, match="file count"):
        await projector.project(
            workspace,
            checkpoint_ref="artifact://c",
            workflow_id="workflow-1",
            runtime_uid=os.getuid(),
            runtime_gid=os.getgid(),
        )
    _assert_no_ready_workspace(workspace)


@pytest.mark.asyncio
async def test_checkpoint_per_file_budget_rejected(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    monkeypatch.setattr(workspace_artifacts, "MAX_ARCHIVE_FILE_SIZE", 4)
    checkpoint = _tar_bytes([("big.bin", b"x" * 16)])
    projector = WorkspaceArtifactProjector(_FakeArtifactService({"c": checkpoint}))
    with pytest.raises(WorkspaceArtifactProjectionError, match="per-file"):
        await projector.project(
            workspace,
            checkpoint_ref="artifact://c",
            workflow_id="workflow-1",
            runtime_uid=os.getuid(),
            runtime_gid=os.getgid(),
        )
    _assert_no_ready_workspace(workspace)


@pytest.mark.asyncio
async def test_checkpoint_depth_budget_rejected(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    monkeypatch.setattr(workspace_artifacts, "MAX_ARCHIVE_DEPTH", 2)
    checkpoint = _tar_bytes([("a/b/c/d.txt", b"x")])
    projector = WorkspaceArtifactProjector(_FakeArtifactService({"c": checkpoint}))
    with pytest.raises(WorkspaceArtifactProjectionError, match="depth"):
        await projector.project(
            workspace,
            checkpoint_ref="artifact://c",
            workflow_id="workflow-1",
            runtime_uid=os.getuid(),
            runtime_gid=os.getgid(),
        )
    _assert_no_ready_workspace(workspace)


@pytest.mark.asyncio
async def test_checkpoint_sparse_archive_rejected(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    # The stdlib writer normalizes sparse headers away, so simulate the
    # hostile read side faithfully: keep the real gzip/tar bytes on disk and
    # the real member parsing, and only restore the GNU sparse flag a hostile
    # archive would carry before the production guard inspects it.
    checkpoint = _tar_bytes([("sparse.bin", b"x" * 8)])
    real_iter = tarfile.TarFile.__iter__

    def _sparse_iter(self):
        for member in real_iter(self):
            if member.isreg() and member.name == "sparse.bin":
                member.sparse = [(0, member.size or 8)]
            yield member

    monkeypatch.setattr(tarfile.TarFile, "__iter__", _sparse_iter)
    projector = WorkspaceArtifactProjector(
        _FakeArtifactService({"c": checkpoint})
    )
    with pytest.raises(WorkspaceArtifactProjectionError, match="sparse"):
        await projector.project(
            workspace,
            checkpoint_ref="artifact://c",
            workflow_id="workflow-1",
            runtime_uid=os.getuid(),
            runtime_gid=os.getgid(),
        )
    _assert_no_ready_workspace(workspace)


@pytest.mark.asyncio
async def test_checkpoint_truncated_archive_rejected(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    full = _tar_bytes([("file.txt", b"complete content here")])
    truncated = full[: len(full) // 2]
    projector = WorkspaceArtifactProjector(_FakeArtifactService({"c": truncated}))
    with pytest.raises(WorkspaceArtifactProjectionError):
        await projector.project(
            workspace,
            checkpoint_ref="artifact://c",
            workflow_id="workflow-1",
            runtime_uid=os.getuid(),
            runtime_gid=os.getgid(),
        )
    _assert_no_ready_workspace(workspace)


@pytest.mark.asyncio
async def test_checkpoint_processing_time_budget_rejected(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    # An already-elapsed deadline fails the very first member before any
    # filesystem mutation, proving the explicit processing-time budget.
    monkeypatch.setattr(workspace_artifacts, "MAX_EXTRACTION_SECONDS", -1.0)
    checkpoint = _tar_bytes([("file.txt", b"data")])
    projector = WorkspaceArtifactProjector(_FakeArtifactService({"c": checkpoint}))
    with pytest.raises(WorkspaceArtifactProjectionError, match="processing-time"):
        await projector.project(
            workspace,
            checkpoint_ref="artifact://c",
            workflow_id="workflow-1",
            runtime_uid=os.getuid(),
            runtime_gid=os.getgid(),
        )
    _assert_no_ready_workspace(workspace)


# --- Requirement 2/AC3: artifact-service admission negatives -----------------


class _AdmitFakeArtifactService:
    """Artifact-service double with status/expiry/quarantine knobs."""

    def __init__(
        self,
        payloads: dict[str, bytes],
        *,
        workflow_id: str = "workflow-1",
        status: str = "COMPLETE",
        expires_at=None,
        quarantined: bool = False,
        restricted: bool = False,
        metadata: object = ...,  # ... = derive from knobs; None/{} = missing
    ) -> None:
        self.payloads = payloads
        self.workflow_id = workflow_id
        self.status = status
        self.expires_at = expires_at
        self.quarantined = quarantined
        self.restricted = restricted
        self._metadata = metadata

    async def get_metadata(self, *, artifact_id: str, principal: str):
        if self._metadata is not ...:
            return self._metadata
        payload = self.payloads[artifact_id]
        digest = "sha256:" + hashlib.sha256(payload).hexdigest()
        artifact = SimpleNamespace(
            size_bytes=len(payload),
            sha256=digest,
            status=self.status,
            redaction_level="RESTRICTED" if self.restricted else "NONE",
            quarantined=self.quarantined,
            expires_at=self.expires_at,
        )
        return artifact, [SimpleNamespace(workflow_id=self.workflow_id)]

    async def read_chunks(
        self, *, artifact_id: str, principal: str, allow_restricted_raw: bool,
        chunk_size: int,
    ):
        return SimpleNamespace(), iter((self.payloads[artifact_id],))


@pytest.mark.asyncio
async def test_checkpoint_missing_metadata_fails_closed(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    projector = WorkspaceArtifactProjector(
        _AdmitFakeArtifactService({"c": b"data"}, metadata=(None, []))
    )
    with pytest.raises(
        WorkspaceArtifactProjectionError, match="no artifact metadata"
    ):
        await projector.project(
            workspace,
            checkpoint_ref="artifact://c",
            workflow_id="workflow-1",
            runtime_uid=os.getuid(),
            runtime_gid=os.getgid(),
        )
    _assert_no_ready_workspace(workspace)


@pytest.mark.asyncio
async def test_checkpoint_incomplete_status_fails_closed(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    projector = WorkspaceArtifactProjector(
        _AdmitFakeArtifactService({"c": b"data"}, status="PENDING")
    )
    with pytest.raises(
        WorkspaceArtifactProjectionError, match="not complete"
    ):
        await projector.project(
            workspace,
            checkpoint_ref="artifact://c",
            workflow_id="workflow-1",
            runtime_uid=os.getuid(),
            runtime_gid=os.getgid(),
        )
    _assert_no_ready_workspace(workspace)


@pytest.mark.asyncio
async def test_checkpoint_expired_lifetime_fails_closed(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    projector = WorkspaceArtifactProjector(
        _AdmitFakeArtifactService(
            {"c": b"data"},
            expires_at=datetime.now(tz=UTC) - timedelta(days=1),
        )
    )
    with pytest.raises(
        WorkspaceArtifactProjectionError, match="lifetime has expired"
    ):
        await projector.project(
            workspace,
            checkpoint_ref="artifact://c",
            workflow_id="workflow-1",
            runtime_uid=os.getuid(),
            runtime_gid=os.getgid(),
        )
    _assert_no_ready_workspace(workspace)


@pytest.mark.asyncio
async def test_checkpoint_quarantined_without_policy_fails_closed(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    projector = WorkspaceArtifactProjector(
        _AdmitFakeArtifactService({"c": b"data"}, quarantined=True)
    )
    with pytest.raises(
        WorkspaceArtifactProjectionError, match="restricted or quarantined"
    ):
        await projector.project(
            workspace,
            checkpoint_ref="artifact://c",
            workflow_id="workflow-1",
            runtime_uid=os.getuid(),
            runtime_gid=os.getgid(),
        )
    _assert_no_ready_workspace(workspace)


# --- Requirement 1/AC1: fail before mutation, artifact end-to-end ------------


def test_compile_rejects_unsupported_source_kind():
    with pytest.raises(HarnessPlatformError, match="unsupported"):
        compile_workspace_source({"workspaceSource": {"kind": "snapshot-v9"}})


def test_compile_rejects_unsupported_checkpoint_contract():
    with pytest.raises(HarnessPlatformError, match="unsupported"):
        compile_workspace_source(
            {
                "workspaceSource": {
                    "kind": "checkpoint",
                    "checkpointRef": "artifact://c",
                    "contract": "legacy-tar-v0",
                }
            }
        )


def test_compile_rejects_conflicting_new_write_aliases():
    with pytest.raises(HarnessPlatformError, match="conflicting"):
        compile_workspace_source(
            {
                "workspaceSource": {
                    "kind": "scratch",
                    "overlay": {},
                },
                "artifactRef": "artifact://a",
            }
        )
    with pytest.raises(HarnessPlatformError, match="conflicting"):
        compile_workspace_source(
            {
                "workspaceSource": {"kind": "scratch"},
                "workspaceCheckpointRestoreRef": "artifact://c",
            }
        )


@pytest.mark.asyncio
async def test_materializer_rejects_unsupported_mutation_before_mutation(tmp_path):
    before = set(tmp_path.iterdir())
    materializer = OmnigentWorkspaceMaterializer(
        command_runner=None, workspace_root=tmp_path  # type: ignore[arg-type]
    )
    with pytest.raises(HarnessPlatformError, match="mutation policy"):
        await materializer.materialize(
            _request(
                {
                    "workspaceLocator": {
                        "kind": "sandbox",
                        "workspaceId": _workspace_id(),
                        "relativePath": "repo",
                    },
                }
            ),
            mutation="bogus-policy",
        )
    assert set(tmp_path.iterdir()) == before


@pytest.mark.asyncio
async def test_artifact_kind_restores_end_to_end_without_clone(tmp_path):
    workspace_id = _workspace_id()
    checkpoint = _tar_bytes(
        [("result.txt", b"artifact-kind result"), ("notes.bin", b"\x00\x01")]
    )
    digest = "sha256:" + hashlib.sha256(checkpoint).hexdigest()
    service = _FakeArtifactService({"bundle": checkpoint})

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
                "workspaceSource": {
                    "kind": "artifact",
                    "artifactRef": "artifact://bundle",
                    "digest": digest,
                },
            }
        ),
        runtime_uid=os.getuid(),
        runtime_gid=os.getgid(),
    )
    assert result["kind"] == "bind"
    assert calls == []
    restored = tmp_path / "temporal_sandbox" / workspace_id / "repo"
    assert (restored / "result.txt").read_bytes() == b"artifact-kind result"
    assert not (restored / ".git").exists()


# --- Requirement 8/AC7: grant generation and workspace binding ----------------


def test_grant_workspace_id_mismatch_fails():
    from moonmind.schemas.workspace_locator_models import (
        WorkspaceLocatorResolutionError,
    )

    with pytest.raises(WorkspaceLocatorResolutionError, match="requested workspace"):
        parse_existing_workspace_grant(
            _grant(),
            expected_workflow_id="workflow-1",
            expected_step_execution_id="step-1",
            expected_workspace_id="other-workspace-id",
        )


@pytest.mark.asyncio
async def test_existing_workspace_generation_mismatch_fails_closed(tmp_path):
    from moonmind.workflows.temporal.runtime.workspace_locators import (
        SandboxWorkspaceRecordStore,
    )

    workspace_id = _workspace_id()
    existing = tmp_path / "temporal_sandbox" / workspace_id / "repo"
    existing.mkdir(parents=True)
    store = SandboxWorkspaceRecordStore(tmp_path)
    store.store_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    (store.store_root / f"{workspace_id}.generation").write_text(
        "gen-pinned", encoding="utf-8"
    )

    async def fail_runner(*_a, **_k):  # pragma: no cover - must not run
        raise AssertionError("stale generation must not clone")

    materializer = OmnigentWorkspaceMaterializer(
        command_runner=fail_runner, workspace_root=tmp_path
    )
    with pytest.raises(HarnessPlatformError, match="generation"):
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
                        "grant": _grant(expectedGeneration="gen-stale"),
                    },
                }
            ),
            mutation="allowed",
        )


@pytest.mark.asyncio
async def test_existing_workspace_id_mismatch_fails_closed(tmp_path):
    workspace_id = _workspace_id()
    existing = tmp_path / "temporal_sandbox" / workspace_id / "repo"
    existing.mkdir(parents=True)

    async def fail_runner(*_a, **_k):  # pragma: no cover - must not run
        raise AssertionError("mismatched grant must not clone")

    materializer = OmnigentWorkspaceMaterializer(
        command_runner=fail_runner, workspace_root=tmp_path
    )
    with pytest.raises(HarnessPlatformError, match="requested workspace"):
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
                        "grant": _grant(workspaceId="other-workspace-id"),
                    },
                }
            ),
            mutation="allowed",
        )


# --- Requirements 6/7: incomplete history and git neutralization ---------------


@pytest.mark.asyncio
async def test_thin_git_history_without_head_is_incomplete(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    # A snapshot claiming .git history without a usable HEAD is incomplete,
    # not silently portable: no ready workspace and no staging residue.
    checkpoint = _tar_bytes([(".git/config", b"[core]\n\trepositoryformatversion = 0\n")])
    projector = WorkspaceArtifactProjector(_FakeArtifactService({"c": checkpoint}))
    with pytest.raises(WorkspaceArtifactProjectionError, match="incomplete"):
        await projector.project(
            workspace,
            checkpoint_ref="artifact://c",
            workflow_id="workflow-1",
            runtime_uid=os.getuid(),
            runtime_gid=os.getgid(),
        )
    _assert_no_ready_workspace(workspace)


@pytest.mark.asyncio
async def test_gitdir_pointer_is_rejected(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    checkpoint = _tar_bytes([(".git", b"gitdir: /elsewhere/worktrees/x")])
    projector = WorkspaceArtifactProjector(_FakeArtifactService({"c": checkpoint}))
    with pytest.raises(WorkspaceArtifactProjectionError, match="gitdir"):
        await projector.project(
            workspace,
            checkpoint_ref="artifact://c",
            workflow_id="workflow-1",
            runtime_uid=os.getuid(),
            runtime_gid=os.getgid(),
        )
    _assert_no_ready_workspace(workspace)


@pytest.mark.asyncio
async def test_credentialed_submodule_url_is_neutralized(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    modules = (
        '[submodule "vendor/lib"]\n'
        "\tpath = vendor/lib\n"
        "\turl = https://user:secret@github.com/org/lib.git\n"
    )
    checkpoint = _tar_bytes(
        [
            (".git/HEAD", b"ref: refs/heads/main\n"),
            (".gitmodules", modules.encode("utf-8")),
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
    # History preserved as data; the credentialed URL is neutralized.
    assert (workspace / ".git" / "HEAD").is_file()
    assert (workspace / "run.sh").is_file()
    neutralized = (workspace / ".gitmodules").read_text(encoding="utf-8")
    assert "secret" not in neutralized
    assert "user@" not in neutralized


@pytest.mark.asyncio
async def test_git_include_and_alternates_are_neutralized(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    config = (
        "[core]\n\trepositoryformatversion = 0\n"
        '[include]\n\tpath = /tmp/evil.inc\n'
        '[credential "https://github.com"]\n\thelper = store\n'
    )
    checkpoint = _tar_bytes(
        [
            (".git/HEAD", b"ref: refs/heads/main\n"),
            (".git/config", config.encode("utf-8")),
            (".git/objects/info/alternates", b"/elsewhere/objects\n"),
            (".git/commondir", b"/elsewhere\n"),
            (".git/worktrees/linked/info", b"stale worktree\n"),
            ("app.py", b"print('hi')\n"),
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
    cleaned = (workspace / ".git" / "config").read_text(encoding="utf-8")
    assert "/tmp/evil.inc" not in cleaned
    assert "helper" not in cleaned
    assert not (workspace / ".git" / "objects" / "info" / "alternates").exists()
    assert not (workspace / ".git" / "commondir").exists()
    assert not (workspace / ".git" / "worktrees").exists()
    assert (workspace / "app.py").is_file()


# --- Requirement 5/AC5: crash, retry, and concurrency behavior -----------------


@pytest.mark.asyncio
async def test_mid_extraction_failure_leaves_no_ready_marker_or_residue(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "keep.txt").write_text("keep", encoding="utf-8")
    checkpoint = _tar_bytes([("fresh.txt", b"fresh")])
    projector = WorkspaceArtifactProjector(_FakeArtifactService({"c": checkpoint}))

    def _boom(_archive_path, _staging):
        raise RuntimeError("simulated crash mid-extraction")

    projector._extract_bounded = _boom  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="simulated crash"):
        await projector.project(
            workspace,
            checkpoint_ref="artifact://c",
            workflow_id="workflow-1",
            runtime_uid=os.getuid(),
            runtime_gid=os.getgid(),
        )
    # The destination keeps its prior content; no partial import and no
    # import-owned staging generation is left behind.
    assert (workspace / "keep.txt").read_text() == "keep"
    assert not (workspace / "fresh.txt").exists()
    assert list(workspace.parent.glob(".moonmind-staging-*")) == []


@pytest.mark.asyncio
async def test_retry_reconciles_same_generation_without_reimport(tmp_path):
    workspace_id = _workspace_id()
    checkpoint = _tar_bytes([("result.txt", b"v1")])
    service = _FakeArtifactService({"snapshot": checkpoint})

    async def runner(argv, input_bytes=None):
        raise AssertionError("self-contained restore must not clone")

    materializer = OmnigentWorkspaceMaterializer(
        command_runner=runner, workspace_root=tmp_path, artifact_service=service
    )
    spec = {
        "workspaceLocator": {
            "kind": "sandbox",
            "workspaceId": workspace_id,
            "relativePath": "repo",
        },
        "workspaceCheckpointRestoreRef": "artifact://snapshot",
    }
    first = await materializer.materialize(
        _request(dict(spec)),
        runtime_uid=os.getuid(),
        runtime_gid=os.getgid(),
    )
    assert first["kind"] == "bind"

    calls: list[str] = []
    original = materializer._artifact_projector.project

    async def counting_project(*args, **kwargs):
        calls.append("project")
        return await original(*args, **kwargs)

    materializer._artifact_projector.project = counting_project  # type: ignore[method-assign]
    second = await materializer.materialize(
        _request(dict(spec)),
        runtime_uid=os.getuid(),
        runtime_gid=os.getgid(),
    )
    assert second["kind"] == "bind"
    assert calls == []


@pytest.mark.asyncio
async def test_changed_inputs_force_reimport(tmp_path):
    workspace_id = _workspace_id()
    checkpoint = _tar_bytes([("result.txt", b"v1")])
    payloads = {"snapshot": checkpoint, "extra": b"extra-state"}
    service = _FakeArtifactService(payloads)

    async def runner(argv, input_bytes=None):
        raise AssertionError("self-contained restore must not clone")

    materializer = OmnigentWorkspaceMaterializer(
        command_runner=runner, workspace_root=tmp_path, artifact_service=service
    )
    base_locator = {
        "kind": "sandbox",
        "workspaceId": workspace_id,
        "relativePath": "repo",
    }
    await materializer.materialize(
        _request(
            dict(
                {
                    "workspaceLocator": dict(base_locator),
                    "workspaceCheckpointRestoreRef": "artifact://snapshot",
                }
            )
        ),
        runtime_uid=os.getuid(),
        runtime_gid=os.getgid(),
    )
    calls: list[str] = []
    original = materializer._artifact_projector.project

    async def counting_project(*args, **kwargs):
        calls.append("project")
        return await original(*args, **kwargs)

    materializer._artifact_projector.project = counting_project  # type: ignore[method-assign]
    await materializer.materialize(
        _request(
            dict(
                {
                    "workspaceLocator": dict(base_locator),
                    "workspaceCheckpointRestoreRef": "artifact://snapshot",
                    "restoreInputRefs": ["artifact://extra"],
                }
            )
        ),
        runtime_uid=os.getuid(),
        runtime_gid=os.getgid(),
    )
    assert calls != []


@pytest.mark.asyncio
async def test_concurrent_owners_keep_verified_generations_without_leakage(tmp_path):
    checkpoint = _tar_bytes([("shared.txt", b"shared")])
    service = _FakeArtifactService({"snapshot": checkpoint})

    async def runner(argv, input_bytes=None):
        raise AssertionError("self-contained restore must not clone")

    materializer = OmnigentWorkspaceMaterializer(
        command_runner=runner, workspace_root=tmp_path, artifact_service=service
    )
    owners = [("workflow-a", "step-a"), ("workflow-b", "step-b")]
    restored_dirs: list = []
    for workflow_id, step_id in owners:
        workspace_id = __import__("hashlib").sha256(
            f"{workflow_id}:{step_id}".encode()
        ).hexdigest()[:24]
        request = SimpleNamespace(
            workspace_spec={
                "workspaceLocator": {
                    "kind": "sandbox",
                    "workspaceId": workspace_id,
                    "relativePath": "repo",
                },
                "workspaceCheckpointRestoreRef": "artifact://snapshot",
            },
            parameters={},
            input_refs=[],
            step_execution=None,
            correlation_id=workflow_id,
            idempotency_key=step_id,
        )
        result = await materializer.materialize(
            request,
            runtime_uid=os.getuid(),
            runtime_gid=os.getgid(),
        )
        assert result["kind"] == "bind"
        restored = tmp_path / "temporal_sandbox" / workspace_id / "repo"
        assert (restored / "shared.txt").read_bytes() == b"shared"
        restored_dirs.append(restored)
    assert restored_dirs[0] != restored_dirs[1]
    assert list(tmp_path.rglob(".moonmind-staging-*")) == []



