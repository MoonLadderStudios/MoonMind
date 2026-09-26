"""Real-consumer restore convergence for MoonMind#4014.

Bounded follow-up to the existing 57-case safe-source suite and the
23-case materializer suite: every case below drives the real consumer
(``OmnigentWorkspaceMaterializer.materialize``) instead of the projector
directly, proving fail-before-launch / fail-before-ready and
interrupted-concurrent recovery convergence. No production behavior is
rebuilt here; these tests verify the existing compiler, artifact-service
admission, lock/staging/promotion, sanitizer, and backend-matrix owners.
"""

from __future__ import annotations

import hashlib
import io
import os
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from moonmind.omnigent.harness_platform.failures import HarnessPlatformError
from moonmind.omnigent.host_services.workspace import OmnigentWorkspaceMaterializer
from moonmind.workflows.temporal.runtime.workspace_locators import (
    SandboxWorkspaceRecord,
    SandboxWorkspaceRecordStore,
)


def _digest(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _tar_bytes(entries: list[tuple], *, compress: bool = True) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz" if compress else "w:") as bundle:
        for entry in entries:
            name, data, kind = entry[0], entry[1], entry[2]
            linkname = entry[3] if len(entry) > 3 else ""
            member = tarfile.TarInfo(name)
            if kind == "file":
                member.size = len(data)
                bundle.addfile(member, io.BytesIO(data))
            elif kind == "symlink":
                member.type = tarfile.SYMTYPE
                member.linkname = linkname
                bundle.addfile(member)
            else:  # pragma: no cover - test helper guard
                raise AssertionError(f"unknown entry kind {kind!r}")
    return buffer.getvalue()


class FakeArtifactService:
    """Real 4-tuple metadata surface: (artifact, links, pinned, policy)."""

    def __init__(
        self,
        payloads: dict[str, bytes],
        *,
        workflow_id: str = "workflow-1",
        link_workflow_id: str | None = None,
        status: str = "COMPLETE",
        redaction_level: str = "NONE",
    ) -> None:
        self.payloads = dict(payloads)
        self.workflow_id = workflow_id
        self.link_workflow_id = (
            workflow_id if link_workflow_id is None else link_workflow_id
        )
        self.status = status
        self.redaction_level = redaction_level

    async def get_metadata(self, *, artifact_id: str, principal: str):
        payload = self.payloads[artifact_id]
        artifact = SimpleNamespace(
            artifact_id=artifact_id,
            status=self.status,
            size_bytes=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(),
            expires_at=None,
            redaction_level=self.redaction_level,
            metadata_json={},
        )
        links = (
            []
            if self.link_workflow_id is None
            else [SimpleNamespace(workflow_id=self.link_workflow_id)]
        )
        return artifact, links, False, SimpleNamespace(raw_access_allowed=True)

    async def read_chunks(
        self, *, artifact_id: str, principal: str, allow_restricted_raw: bool,
        chunk_size: int,
    ):
        assert allow_restricted_raw is True
        return SimpleNamespace(), iter((self.payloads[artifact_id],))


def _request(spec: dict, *, workflow_id="workflow-1", step_id="step-1"):
    return SimpleNamespace(
        workspace_spec=spec,
        parameters={},
        input_refs=[],
        step_execution=None,
        correlation_id=workflow_id,
        idempotency_key=step_id,
    )


def _workspace_id(workflow_id="workflow-1", step_id="step-1") -> str:
    return hashlib.sha256(f"{workflow_id}:{step_id}".encode()).hexdigest()[:24]


def _locator_spec(workspace_id: str, **extra) -> dict:
    return {
        "workspaceLocator": {
            "kind": "sandbox",
            "workspaceId": workspace_id,
            "relativePath": "repo",
        },
        **extra,
    }


async def _never_clone(argv, input_bytes=None):  # pragma: no cover
    raise AssertionError("self-contained restore must not clone")


_TEST_GRANT_SECRET = "test-workspace-grant-secret"


def _grant_spec(workspace_id: str, owner=("workflow-1", "step-1"),
                generation=1, mode="exclusive", grantee="workflow-1") -> dict:
    from moonmind.omnigent.workspace_sources import issue_existing_workspace_grant

    grant = issue_existing_workspace_grant(
        workspace_id=workspace_id,
        owner_workflow_id=owner[0],
        owner_step_execution_id=owner[1],
        grantee_workflow_id=grantee,
        mode=mode,
        generation=generation,
        secret=_TEST_GRANT_SECRET,
    )
    return {
        "workspaceSource": {
            "kind": "existing_workspace",
            "existingWorkspaceGrant": {
                "workspaceId": grant.workspace_id,
                "ownerWorkflowId": grant.owner_workflow_id,
                "ownerStepExecutionId": grant.owner_step_execution_id,
                "generation": grant.generation,
                "mode": grant.mode,
                "grantDigest": grant.grant_digest,
                "expiresAt": grant.expires_at.isoformat()
                if grant.expires_at is not None
                else None,
            },
        }
    }


def _ensure_owned_workspace(root: Path, workspace_id: str, owner=(),
                            relative_path="repo") -> Path:
    workflow_id, step_id = owner or ("workflow-1", "step-1")
    workspace = root / "temporal_sandbox" / workspace_id / relative_path
    workspace.mkdir(parents=True)
    (workspace / "owned.txt").write_text("owner content\n")
    SandboxWorkspaceRecordStore(root).ensure(
        SandboxWorkspaceRecord(
            workspace_id=workspace_id,
            workflow_id=workflow_id,
            step_execution_id=step_id,
            relative_path=relative_path,
        )
    )
    return workspace


# REQ-06: self-contained private artifact (not checkpoint) with non-Git
# binary content restores after credential loss without clone or network.
@pytest.mark.asyncio
async def test_materialize_private_artifact_restores_without_clone_or_token(
    tmp_path, monkeypatch
):
    async def fail_token(*args, **kwargs):  # pragma: no cover
        raise AssertionError("private artifact restore must not resolve credentials")

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.managed_api_key_resolve."
        "resolve_github_token_for_launch",
        fail_token,
    )
    binary = bytes(range(256)) * 32 + b"\x00\xff binary tail"
    payload = _tar_bytes(
        [("data/model.bin", binary, "file"), ("notes.txt", b"plain\n", "file")]
    )
    workspace_id = _workspace_id()
    spec = _locator_spec(
        workspace_id,
        workspaceSource={
            "kind": "artifact",
            "artifactRef": "artifact://private-bundle",
            "artifactDigest": _digest(payload),
        },
    )
    materializer = OmnigentWorkspaceMaterializer(
        command_runner=_never_clone,
        workspace_root=tmp_path,
        artifact_service=FakeArtifactService({"private-bundle": payload}),
    )
    result = await materializer.materialize(
        _request(spec), runtime_uid=os.getuid(), runtime_gid=os.getgid()
    )
    assert result["kind"] == "bind"
    workspace = tmp_path / "temporal_sandbox" / workspace_id / "repo"
    assert (workspace / "data" / "model.bin").read_bytes() == binary
    assert (workspace / "notes.txt").read_text() == "plain\n"
    assert not (workspace / ".git").exists()
    store = SandboxWorkspaceRecordStore(tmp_path)
    assert store.is_materialized(workspace_id)
    assert store.read_readiness(workspace_id)["fingerprint"]["sourceDigest"] == (
        _digest(payload)
    )


# REQ-07: the same violations fail through the real consumer before launch
# or a ready marker, without touching another owner's files.
@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["family", "digest", "quarantine", "unsafe", "raw"])
async def test_materialize_negatives_fail_before_ready(tmp_path, case):
    sibling_id = _workspace_id("sibling-wf", "sibling-st")
    sibling = tmp_path / "temporal_sandbox" / sibling_id / "repo"
    sibling.mkdir(parents=True)
    (sibling / "theirs.txt").write_text("other owner\n")

    target_id = _workspace_id()
    good = _tar_bytes([("ok.txt", b"ok\n", "file")])
    if case == "family":
        service = FakeArtifactService(
            {"checkpoint": good}, link_workflow_id="workflow-1:"
        )
        spec = _locator_spec(
            target_id,
            workspaceSource={
                "kind": "checkpoint",
                "checkpointRef": "artifact://checkpoint",
                "checkpointDigest": _digest(good),
                "restoreContract": "moonmind.worktree-archive.v1",
            },
        )
    elif case == "digest":
        service = FakeArtifactService({"checkpoint": good})
        spec = _locator_spec(
            target_id,
            workspaceSource={
                "kind": "checkpoint",
                "checkpointRef": "artifact://checkpoint",
                "checkpointDigest": _digest(b"something else"),
                "restoreContract": "moonmind.worktree-archive.v1",
            },
        )
    elif case == "quarantine":
        service = FakeArtifactService(
            {"checkpoint": good}, redaction_level="QUARANTINED"
        )
        spec = _locator_spec(
            target_id,
            workspaceSource={
                "kind": "checkpoint",
                "checkpointRef": "artifact://checkpoint",
                "checkpointDigest": _digest(good),
                "restoreContract": "moonmind.worktree-archive.v1",
            },
        )
    elif case == "unsafe":
        hostile = _tar_bytes([("/abs.txt", b"x", "file")])
        service = FakeArtifactService({"checkpoint": hostile})
        spec = _locator_spec(
            target_id,
            workspaceSource={
                "kind": "checkpoint",
                "checkpointRef": "artifact://checkpoint",
                "checkpointDigest": _digest(hostile),
                "restoreContract": "moonmind.worktree-archive.v1",
            },
        )
    else:  # forged legacy raw-path input
        service = FakeArtifactService({"checkpoint": good})
        spec = _locator_spec(target_id, workspacePath="/tmp/sibling-workspace")
    materializer = OmnigentWorkspaceMaterializer(
        command_runner=_never_clone,
        workspace_root=tmp_path,
        artifact_service=service,
    )
    with pytest.raises(HarnessPlatformError):
        await materializer.materialize(
            _request(spec, workflow_id="workflow-1", step_id="step-1"),
            runtime_uid=os.getuid(),
            runtime_gid=os.getgid(),
        )
    store = SandboxWorkspaceRecordStore(tmp_path)
    assert not store.is_materialized(target_id)
    assert store.read_readiness(target_id) is None
    assert (sibling / "theirs.txt").read_text() == "other owner\n"


# REQ-03/08: an interrupted import leaves no partial content and a retry
# with a valid snapshot converges without affecting another owner.
@pytest.mark.asyncio
async def test_materialize_interrupted_import_converges_on_retry(tmp_path):
    sibling_id = _workspace_id("sibling-wf", "sibling-st")
    sibling = tmp_path / "temporal_sandbox" / sibling_id / "repo"
    sibling.mkdir(parents=True)
    (sibling / "theirs.txt").write_text("other owner\n")

    target_id = _workspace_id()
    bad = _tar_bytes([("ok.txt", b"hello world", "file")])[:40]
    service = FakeArtifactService({"checkpoint": bad})
    materializer = OmnigentWorkspaceMaterializer(
        command_runner=_never_clone,
        workspace_root=tmp_path,
        artifact_service=service,
    )
    spec = _locator_spec(
        target_id,
        workspaceSource={
            "kind": "checkpoint",
            "checkpointRef": "artifact://checkpoint",
            "checkpointDigest": _digest(bad),
            "restoreContract": "moonmind.worktree-archive.v1",
        },
    )
    with pytest.raises(HarnessPlatformError):
        await materializer.materialize(
            _request(spec),
            runtime_uid=os.getuid(),
            runtime_gid=os.getgid(),
        )
    workspace = tmp_path / "temporal_sandbox" / target_id / "repo"
    assert not list(workspace.iterdir()) if workspace.exists() else True
    assert not SandboxWorkspaceRecordStore(tmp_path).is_materialized(target_id)
    assert (sibling / "theirs.txt").read_text() == "other owner\n"
    assert list((workspace.parent).glob("*.lock")) == []

    good = _tar_bytes([("good.txt", b"recovered\n", "file")])
    service.payloads["checkpoint"] = good
    fixed_spec = _locator_spec(
        target_id,
        workspaceSource={
            "kind": "checkpoint",
            "checkpointRef": "artifact://checkpoint",
            "checkpointDigest": _digest(good),
            "restoreContract": "moonmind.worktree-archive.v1",
        },
    )
    await materializer.materialize(
        _request(fixed_spec),
        runtime_uid=os.getuid(),
        runtime_gid=os.getgid(),
    )
    assert (workspace / "good.txt").read_text() == "recovered\n"
    assert SandboxWorkspaceRecordStore(tmp_path).is_materialized(target_id)


# REQ-03/08: stale import-owned staging is reclaimed on the next run while
# the live workspace and another owner's files are preserved.
@pytest.mark.asyncio
async def test_materialize_reclaims_stale_staging_without_touching_live(tmp_path):
    target_id = _workspace_id()
    candidate_parent = tmp_path / "temporal_sandbox" / target_id
    existing = candidate_parent / "repo"
    existing.mkdir(parents=True)
    stale = candidate_parent / ".moonmind-import-stale9"
    stale.mkdir()
    (stale / "partial.txt").write_text("partial\n")
    sibling_id = _workspace_id("sibling-wf", "sibling-st")
    sibling = tmp_path / "temporal_sandbox" / sibling_id / "repo"
    sibling.mkdir(parents=True)
    (sibling / "theirs.txt").write_text("other owner\n")

    payload = _tar_bytes([("fresh.txt", b"snapshot\n", "file")])
    materializer = OmnigentWorkspaceMaterializer(
        command_runner=_never_clone,
        workspace_root=tmp_path,
        artifact_service=FakeArtifactService({"checkpoint": payload}),
    )
    await materializer.materialize(
        _request(
            _locator_spec(
                target_id,
                workspaceSource={
                    "kind": "checkpoint",
                    "checkpointRef": "artifact://checkpoint",
                    "checkpointDigest": _digest(payload),
                    "restoreContract": "moonmind.worktree-archive.v1",
                },
            ),
        ),
        runtime_uid=os.getuid(),
        runtime_gid=os.getgid(),
    )
    assert not stale.exists()
    assert (existing / "fresh.txt").read_text() == "snapshot\n"
    assert (sibling / "theirs.txt").read_text() == "other owner\n"


# REQ-04: neutralized authority stays neutralized across a retry; a changed
# snapshot re-import is sanitized again instead of resurrecting authority.
@pytest.mark.asyncio
async def test_materialize_retry_does_not_resurrect_authority(tmp_path):
    payload = _tar_bytes(
        [
            (".git/HEAD", b"ref: refs/heads/main\n", "file"),
            (".git/refs/heads/main", b"abc123\n", "file"),
            (".git/hooks/pre-commit", b"#!/bin/sh\nevil\n", "file"),
            (
                ".git/config",
                b"[core]\n\thooksPath = /tmp/e\n"
                b"[credential]\n\thelper = store\n",
                "file",
            ),
            (".netrc", b"machine example.com password s3cr3t\n", "file"),
            ("run.sh", b"#!/bin/sh\necho hi\n", "file"),
        ]
    )
    target_id = _workspace_id()
    service = FakeArtifactService({"checkpoint": payload})
    materializer = OmnigentWorkspaceMaterializer(
        command_runner=_never_clone,
        workspace_root=tmp_path,
        artifact_service=service,
    )
    spec = _locator_spec(
        target_id,
        workspaceSource={
            "kind": "checkpoint",
            "checkpointRef": "artifact://checkpoint",
            "checkpointDigest": _digest(payload),
            "restoreContract": "moonmind.worktree-archive.v1",
        },
    )
    await materializer.materialize(
        _request(spec),
        runtime_uid=os.getuid(),
        runtime_gid=os.getgid(),
    )
    workspace = tmp_path / "temporal_sandbox" / target_id / "repo"
    assert (workspace / ".git" / "refs" / "heads" / "main").read_text() == "abc123\n"
    assert (workspace / "run.sh").exists()
    assert not (workspace / ".git" / "hooks" / "pre-commit").exists()
    assert not (workspace / ".netrc").exists()

    # Same-generation retry reconciles without reprojection; authority stays gone.
    await materializer.materialize(
        _request(spec),
        runtime_uid=os.getuid(),
        runtime_gid=os.getgid(),
    )
    assert not (workspace / ".git" / "hooks" / "pre-commit").exists()
    assert not (workspace / ".netrc").exists()
    assert (workspace / ".git" / "refs" / "heads" / "main").read_text() == "abc123\n"


# REQ-05: backend locator/grant boundaries fail before launch on the real
# consumer; read-only sharing stays supported on a remote daemon view.
@pytest.mark.asyncio
async def test_materialize_backend_grant_matrix(tmp_path, monkeypatch):
    monkeypatch.setenv("MOONMIND_WORKSPACE_GRANT_SECRET", _TEST_GRANT_SECRET)
    workspace_id = "matrix-ws"
    _ensure_owned_workspace(tmp_path, workspace_id, owner=("owner-wf", "owner-st"))
    materializer = OmnigentWorkspaceMaterializer(
        command_runner=_never_clone, workspace_root=tmp_path
    )

    monkeypatch.setenv("WORKFLOW_DOCKER_DAEMON_MODE", "remote")
    monkeypatch.delenv("WORKFLOW_WORKSPACE_DAEMON_ROOT", raising=False)
    monkeypatch.setenv("WORKFLOW_WORKSPACE_ROOT", str(tmp_path))
    with pytest.raises(HarnessPlatformError, match="remote daemon"):
        await materializer.materialize(
            _request(
                _grant_spec(
                    workspace_id,
                    owner=("owner-wf", "owner-st"),
                    grantee="owner-wf",
                ),
                workflow_id="owner-wf",
                step_id="owner-st",
            ),
            runtime_uid=os.getuid(),
            runtime_gid=os.getgid(),
        )
    reader_grant = _grant_spec(
        workspace_id,
        owner=("owner-wf", "owner-st"),
        mode="read_only",
        grantee="reader-wf",
    )
    shared = await materializer.materialize(
        _request(
            reader_grant,
            workflow_id="reader-wf",
            step_id="reader-st",
        ),
        runtime_uid=os.getuid(),
        runtime_gid=os.getgid(),
    )
    assert shared["accessMode"] == "read-only"

    monkeypatch.setenv("WORKFLOW_DOCKER_DAEMON_MODE", "local")
    from moonmind.workflows.temporal.runtime.workspace_locators import (
        SandboxWorkspaceRecordStore as _RecordStore,
    )
    from moonmind.omnigent.workspace_sources import parse_existing_workspace_grant

    _store = _RecordStore(tmp_path)
    # The digest includes issuance time; releasing a newly issued grant can
    # leave the claim above active when the clock crosses a second boundary.
    _parsed = parse_existing_workspace_grant(
        reader_grant["workspaceSource"]["existingWorkspaceGrant"]
    )
    _claim_id, _ = _RecordStore._grant_claim_identity(_parsed)
    _store.release_existing_workspace(workspace_id, _claim_id)
    owned = await materializer.materialize(
        _request(
            _grant_spec(
                workspace_id,
                owner=("owner-wf", "owner-st"),
                grantee="owner-wf",
            ),
            workflow_id="owner-wf",
            step_id="owner-st",
        ),
        runtime_uid=os.getuid(),
        runtime_gid=os.getgid(),
    )
    assert owned["accessMode"] == "read-write"

    monkeypatch.setenv("WORKFLOW_DOCKER_DAEMON_MODE", "sidecar")
    with pytest.raises(HarnessPlatformError):
        await materializer.materialize(
            _request(_locator_spec(_workspace_id())),
            runtime_uid=os.getuid(),
            runtime_gid=os.getgid(),
        )
