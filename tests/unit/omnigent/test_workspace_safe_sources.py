"""Safe artifact, checkpoint, and authorized existing-workspace sources.

Production-boundary regression tests for
MoonLadderStudios/MoonMind#4014 (CONTRACT-002, CONTRACT-011, INV-005, INV-006,
DOC-REQ-002, TEST-003). Every source variant crosses the real
compiler/artifact/materializer boundaries; unsupported combinations fail
before execution.
"""

from __future__ import annotations

import hashlib
import io
import os
import tarfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from moonmind.omnigent.harness_platform.failures import HarnessPlatformError
from moonmind.omnigent.host_services.workspace import OmnigentWorkspaceMaterializer
from moonmind.omnigent.workspace_artifacts import (
    MAX_FILE_BYTES,
    WorkspaceArtifactProjectionError,
    WorkspaceArtifactProjector,
    cleanup_import_staging,
)
from moonmind.omnigent.workspace_sources import (
    ExistingWorkspaceGrantLedger,
    WorkspaceSourceError,
    compile_workspace_source,
    decode_legacy_workspace_path,
    parse_existing_workspace_grant,
)
from moonmind.workflows.temporal.runtime.workspace_locators import (
    SandboxWorkspaceRecord,
    SandboxWorkspaceRecordStore,
)


def _digest(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _tar_bytes(entries: list[tuple], *, compress: bool = True) -> bytes:
    """Build a gzip tar from (name, data, kind[, linkname]) tuples."""

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz" if compress else "w:") as bundle:
        for entry in entries:
            name, data, kind = entry[0], entry[1], entry[2]
            linkname = entry[3] if len(entry) > 3 else ""
            member = tarfile.TarInfo(name)
            if kind == "file":
                member.size = len(data)
                bundle.addfile(member, io.BytesIO(data))
            elif kind == "dir":
                member.type = tarfile.DIRTYPE
                bundle.addfile(member)
            elif kind == "symlink":
                member.type = tarfile.SYMTYPE
                member.linkname = linkname
                bundle.addfile(member)
            elif kind == "hardlink":
                member.type = tarfile.LNKTYPE
                member.linkname = linkname
                bundle.addfile(member)
            else:  # pragma: no cover - test helper guard
                raise AssertionError(f"unknown entry kind {kind!r}")
    return buffer.getvalue()


class FakeArtifactService:
    """Mimic the real TemporalArtifactService boundary (not a read shortcut).

    ``get_metadata`` returns the real 4-tuple shape
    ``(artifact, links, pinned, read_policy)`` so admission exercises status,
    digest, lifetime, redaction, and workflow-family link checks through the
    actual service surface.
    """

    def __init__(
        self,
        payloads: dict[str, bytes],
        *,
        workflow_id: str = "workflow-1",
        link_workflow_id: str | None = None,
        status: str = "COMPLETE",
        redaction_level: str = "NONE",
        omit_digest: bool = False,
    ) -> None:
        self.payloads = dict(payloads)
        self.workflow_id = workflow_id
        self.link_workflow_id = (
            workflow_id if link_workflow_id is None else link_workflow_id
        )
        self.status = status
        self.redaction_level = redaction_level
        self.omit_digest = omit_digest
        self.calls: list[tuple] = []

    async def get_metadata(self, *, artifact_id: str, principal: str):
        self.calls.append(("metadata", artifact_id, principal))
        payload = self.payloads[artifact_id]
        artifact = SimpleNamespace(
            artifact_id=artifact_id,
            status=self.status,
            size_bytes=len(payload),
            sha256=None
            if self.omit_digest
            else hashlib.sha256(payload).hexdigest(),
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
        self.calls.append(("read", artifact_id, principal))
        return SimpleNamespace(), iter((self.payloads[artifact_id],))


class ReadOnlyGateway:
    """A read-bytes-only adapter with no metadata surface."""

    def __init__(self, payloads: dict[str, bytes]) -> None:
        self.payloads = dict(payloads)

    async def read_bytes(self, ref: str) -> bytes:
        return self.payloads[ref.removeprefix("artifact://")]


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


def _authored_checkpoint_spec(workspace_id: str, payload: bytes, **extra) -> dict:
    return _locator_spec(
        workspace_id,
        workspaceSource={
            "kind": "checkpoint",
            "checkpointRef": "artifact://checkpoint",
            "checkpointDigest": _digest(payload),
            "restoreContract": "moonmind.worktree-archive.v1",
        },
        **extra,
    )


async def _never_clone(argv, input_bytes=None):  # pragma: no cover - must not run
    raise AssertionError("self-contained restore must not clone")


# ---------------------------------------------------------------------------
# Impl 1: exactly one source plus an explicit overlay/input policy.
# ---------------------------------------------------------------------------


def test_compiler_rejects_conflicting_source_aliases():
    with pytest.raises(WorkspaceSourceError, match="WORKSPACE_SOURCE_CONFLICT"):
        compile_workspace_source(
            {
                "workspaceArtifactRef": "artifact://aaa",
                "workspaceCheckpointRestoreRef": "artifact://bbb",
            }
        )


def test_compiler_rejects_raw_path_precedence_but_preserves_history():
    with pytest.raises(
        WorkspaceSourceError, match="WORKSPACE_SOURCE_RAW_PATH_REJECTED"
    ):
        compile_workspace_source({"workspacePath": "/tmp/nowhere"})
    with pytest.raises(
        WorkspaceSourceError, match="WORKSPACE_SOURCE_RAW_PATH_REJECTED"
    ):
        compile_workspace_source({"path": "/tmp/nowhere"})
    # Necessary historical decoding stays explicit, not a silent route.
    assert (
        decode_legacy_workspace_path({"workspacePath": "/tmp/nowhere"})
        == "/tmp/nowhere"
    )


def test_compiler_requires_digest_for_authored_artifact():
    with pytest.raises(WorkspaceSourceError, match="expected sha256"):
        compile_workspace_source(
            {"workspaceSource": {"kind": "artifact", "artifactRef": "artifact://a"}}
        )


def test_compiler_rejects_unsupported_restore_contract():
    with pytest.raises(WorkspaceSourceError, match="not supported"):
        compile_workspace_source(
            {
                "workspaceSource": {
                    "kind": "checkpoint",
                    "checkpointRef": "artifact://c",
                    "restoreContract": "moonmind.future-format.v9",
                }
            }
        )


def test_compiler_pairs_repository_base_with_checkpoint_overlay():
    source = compile_workspace_source(
        {
            "repository": "MoonLadderStudios/MoonMind",
            "branch": "main",
            "workspaceCheckpointRestoreRef": "artifact://chk",
        }
    )
    assert source.kind == "checkpoint"
    assert source.repository_ref == "MoonLadderStudios/MoonMind"
    assert source.overlay_policy == "additive_overlay"
    assert source.historical is True


def test_compiler_rejects_grant_with_competing_source():
    with pytest.raises(WorkspaceSourceError, match="WORKSPACE_SOURCE_CONFLICT"):
        compile_workspace_source(
            {
                "workspaceSource": {
                    "kind": "existing_workspace",
                    "existingWorkspaceGrant": {
                        "workspaceId": "ws-1",
                        "ownerWorkflowId": "wf-1",
                        "ownerStepExecutionId": "st-1",
                    },
                },
                "workspaceCheckpointRestoreRef": "artifact://chk",
            }
        )


def test_compiler_rejects_unsupported_runtime_combination(monkeypatch):
    monkeypatch.setenv("MOONMIND_WORKSPACE_GRANT_SECRET", "test-secret")
    with pytest.raises(
        WorkspaceSourceError, match="WORKSPACE_SOURCE_RUNTIME_UNSUPPORTED"
    ):
        compile_workspace_source(
            {
                "workspaceSource": {
                    "kind": "artifact",
                    "artifactRef": "artifact://a",
                    "artifactDigest": _digest(b"x"),
                }
            },
            runtime="codex_cli",
        )
    with pytest.raises(
        WorkspaceSourceError, match="WORKSPACE_SOURCE_RUNTIME_UNSUPPORTED"
    ):
        from moonmind.omnigent.workspace_sources import issue_existing_workspace_grant

        _signed = issue_existing_workspace_grant(
            workspace_id="ws-1",
            owner_workflow_id="wf-1",
            owner_step_execution_id="st-1",
            grantee_workflow_id="wf-x",
            mode="read_only",
            secret="test-secret",
        )
        compile_workspace_source(
            {
                "workspaceSource": {
                    "kind": "existing_workspace",
                    "existingWorkspaceGrant": {
                        "workspaceId": "ws-1",
                        "ownerWorkflowId": "wf-1",
                        "ownerStepExecutionId": "st-1",
                        "generation": 1,
                        "mode": "read_only",
                        "grantDigest": _signed.grant_digest,
                        "expiresAt": _signed.expires_at.isoformat()
                        if _signed.expires_at is not None
                        else None,
                    },
                }
            },
            workflow_id="wf-x",
            runtime="codex_cli",
        )


def test_grant_ledger_rejects_stale_and_expired_generations(tmp_path):
    ledger = ExistingWorkspaceGrantLedger(tmp_path)

    def _grant(generation: int, **overrides):
        payload = {
            "workspaceId": "ws-1",
            "ownerWorkflowId": "wf-1",
            "ownerStepExecutionId": "st-1",
            "generation": generation,
            "mode": "exclusive",
        }
        payload.update(overrides)
        return parse_existing_workspace_grant(payload)

    assert ledger.admitted_generation("ws-1") is None
    ledger.admit(_grant(2))
    assert ledger.admitted_generation("ws-1") == 2
    with pytest.raises(WorkspaceSourceError, match="stale"):
        ledger.admit(_grant(1))
    expired = _grant(
        3, expiresAt=(datetime.now(tz=UTC) - timedelta(seconds=1)).isoformat()
    )
    with pytest.raises(WorkspaceSourceError, match="expired"):
        ledger.admit(expired)


# ---------------------------------------------------------------------------
# Impl 2: admission through the actual artifact service.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_strict_admission_rejects_metadata_less_adapter(tmp_path):
    service = WorkspaceArtifactProjector(ReadOnlyGateway({"c": b"data"}))
    workspace = tmp_path / "ws"
    workspace.mkdir()
    with pytest.raises(
        WorkspaceArtifactProjectionError, match="linked artifact metadata"
    ):
        await service.project(
            workspace,
            checkpoint_ref="artifact://c",
            workflow_id="workflow-1",
            runtime_uid=os.getuid(),
            runtime_gid=os.getgid(),
            strict_admission=True,
        )
    assert not any(workspace.iterdir())


@pytest.mark.asyncio
async def test_strict_admission_rejects_wrong_owner(tmp_path):
    payload = _tar_bytes([("ok.txt", b"ok", "file")])
    service = WorkspaceArtifactProjector(
        FakeArtifactService({"checkpoint": payload}, link_workflow_id="other-workflow")
    )
    workspace = tmp_path / "ws"
    workspace.mkdir()
    with pytest.raises(
        WorkspaceArtifactProjectionError, match="not linked to the current workflow"
    ):
        await service.project(
            workspace,
            checkpoint_ref="artifact://checkpoint",
            checkpoint_digest=_digest(payload),
            workflow_id="workflow-1",
            runtime_uid=os.getuid(),
            runtime_gid=os.getgid(),
            strict_admission=True,
        )


@pytest.mark.asyncio
async def test_strict_admission_rejects_forged_family_prefix_link(tmp_path):
    payload = _tar_bytes([("ok.txt", b"ok", "file")])
    service = WorkspaceArtifactProjector(
        FakeArtifactService({"checkpoint": payload}, link_workflow_id="workflow-1:")
    )
    workspace = tmp_path / "ws"
    workspace.mkdir()
    with pytest.raises(
        WorkspaceArtifactProjectionError, match="not linked to the current workflow"
    ):
        await service.project(
            workspace,
            checkpoint_ref="artifact://checkpoint",
            checkpoint_digest=_digest(payload),
            workflow_id="workflow-1",
            runtime_uid=os.getuid(),
            runtime_gid=os.getgid(),
            strict_admission=True,
        )


@pytest.mark.asyncio
async def test_strict_admission_rejects_digest_mismatch(tmp_path):
    payload = _tar_bytes([("ok.txt", b"ok", "file")])
    service = WorkspaceArtifactProjector(FakeArtifactService({"checkpoint": payload}))
    workspace = tmp_path / "ws"
    workspace.mkdir()
    with pytest.raises(
        WorkspaceArtifactProjectionError, match="digest does not match"
    ):
        await service.project(
            workspace,
            checkpoint_ref="artifact://checkpoint",
            checkpoint_digest=_digest(b"something else"),
            workflow_id="workflow-1",
            runtime_uid=os.getuid(),
            runtime_gid=os.getgid(),
            strict_admission=True,
        )
    store = SandboxWorkspaceRecordStore(tmp_path)
    assert store.read_readiness("ws") is None
    assert not any(workspace.iterdir())


@pytest.mark.asyncio
async def test_strict_admission_rejects_restricted_and_incomplete(tmp_path):
    payload = _tar_bytes([("ok.txt", b"ok", "file")])
    for kwargs in (
        {"redaction_level": "RESTRICTED"},
        {"redaction_level": "QUARANTINED"},
        {"status": "PENDING_UPLOAD"},
        {"status": "FAILED"},
    ):
        service = WorkspaceArtifactProjector(
            FakeArtifactService({"c": payload}, **kwargs)
        )
        workspace = tmp_path / "ws"
        workspace.mkdir(exist_ok=True)
        with pytest.raises(WorkspaceArtifactProjectionError):
            await service.project(
                workspace,
                checkpoint_ref="artifact://c",
                checkpoint_digest=_digest(payload),
                workflow_id="workflow-1",
                runtime_uid=os.getuid(),
                runtime_gid=os.getgid(),
                strict_admission=True,
            )


# ---------------------------------------------------------------------------
# Impl 3: streamed verification under explicit budgets; hostile archives.
# ---------------------------------------------------------------------------


def _hostile_cases():
    deep = "/".join(f"d{i}" for i in range(40)) + "/deep.txt"
    return {
        "absolute": [("/abs.txt", b"x", "file")],
        "traversal": [("../escape.txt", b"x", "file")],
        "duplicate": [("dup.txt", b"a", "file"), ("dup.txt", b"b", "file")],
        "collision": [("Name.txt", b"a", "file"), ("name.txt", b"b", "file")],
        "symlink_escape": [("link", b"", "symlink", "/etc/passwd")],
        "symlink_dotdot": [("sub/link", b"", "symlink", "../../outside")],
        "hardlink_forward": [("hl", b"", "hardlink", "later.txt")],
        "deep": [(deep, b"x", "file")],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("name", sorted(_hostile_cases()))
async def test_hostile_archives_stay_within_bounds_without_ready_workspace(
    tmp_path, name
):
    payload = _tar_bytes(_hostile_cases()[name])
    service = WorkspaceArtifactProjector(FakeArtifactService({"c": payload}))
    workspace = tmp_path / "ws"
    workspace.mkdir()
    with pytest.raises(WorkspaceArtifactProjectionError):
        await service.project(
            workspace,
            checkpoint_ref="artifact://c",
            checkpoint_digest=_digest(payload),
            workflow_id="workflow-1",
            runtime_uid=os.getuid(),
            runtime_gid=os.getgid(),
            strict_admission=True,
        )
    assert list(workspace.iterdir()) == []


@pytest.mark.asyncio
async def test_truncated_and_malformed_archives_are_classified(tmp_path):
    payload = _tar_bytes([("ok.txt", b"hello world", "file")])
    service = WorkspaceArtifactProjector(FakeArtifactService({"c": payload[:40]}))
    workspace = tmp_path / "ws"
    workspace.mkdir()
    with pytest.raises(WorkspaceArtifactProjectionError):
        await service.project(
            workspace,
            checkpoint_ref="artifact://c",
            workflow_id="workflow-1",
            runtime_uid=os.getuid(),
            runtime_gid=os.getgid(),
        )
    garbage = FakeArtifactService({"c": b"not a gzip stream at all" * 10})
    with pytest.raises(WorkspaceArtifactProjectionError):
        await WorkspaceArtifactProjector(garbage).project(
            workspace,
            checkpoint_ref="artifact://c",
            workflow_id="workflow-1",
            runtime_uid=os.getuid(),
            runtime_gid=os.getgid(),
        )


@pytest.mark.asyncio
async def test_per_file_and_expanded_budgets_are_enforced(tmp_path, monkeypatch):
    # Craft a member whose header claims more than the per-file budget: patch
    # the size octal (offset 124) of an otherwise valid tiny member, since
    # tarfile refuses to write inconsistent claims itself.
    import gzip

    raw = _tar_bytes([("big.bin", b"tiny", "file")], compress=False)
    claimed = MAX_FILE_BYTES + 1
    patched = bytearray(raw)
    patched[124:136] = f"{claimed:011o}\x00".encode("ascii")
    # Repair the header checksum (offset 148) so the claim reaches the
    # per-file budget check instead of failing header validation.
    patched[148:156] = b" " * 8
    checksum = sum(patched[0:512])
    patched[148:156] = f"{checksum:06o}\x00 ".encode("ascii")
    payload = gzip.compress(bytes(patched))
    service = WorkspaceArtifactProjector(FakeArtifactService({"c": payload}))
    workspace = tmp_path / "ws"
    workspace.mkdir()
    with pytest.raises(
        WorkspaceArtifactProjectionError, match="per-file budget"
    ):
        await service.project(
            workspace,
            checkpoint_ref="artifact://c",
            workflow_id="workflow-1",
            runtime_uid=os.getuid(),
            runtime_gid=os.getgid(),
        )

    monkeypatch.setattr(
        "moonmind.omnigent.workspace_artifacts.MAX_EXPANDED_BYTES", 10
    )
    small = _tar_bytes([("a.txt", b"0123456789abcdef", "file")])
    service = WorkspaceArtifactProjector(FakeArtifactService({"c": small}))
    with pytest.raises(
        WorkspaceArtifactProjectionError, match="expanded-bytes budget"
    ):
        await service.project(
            workspace,
            checkpoint_ref="artifact://c",
            workflow_id="workflow-1",
            runtime_uid=os.getuid(),
            runtime_gid=os.getgid(),
        )


@pytest.mark.asyncio
async def test_non_archive_artifact_bytes_are_unsupported(tmp_path):
    payload = b"\x00\x01binary-but-not-a-tarball\xff\xfe"
    service = WorkspaceArtifactProjector(
        FakeArtifactService({"snapshot": payload}, workflow_id="workflow-1")
    )
    workspace = tmp_path / "ws"
    workspace.mkdir()
    with pytest.raises(WorkspaceArtifactProjectionError):
        await service.project(
            workspace,
            checkpoint_ref="artifact://snapshot",
            checkpoint_digest=_digest(payload),
            workflow_id="workflow-1",
            runtime_uid=os.getuid(),
            runtime_gid=os.getgid(),
            strict_admission=True,
        )


# ---------------------------------------------------------------------------
# Impl 4/5: staging generations, authoritative vs additive, ready binding.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_self_contained_restore_needs_no_clone_or_credentials(
    tmp_path, monkeypatch
):
    """A checkpoint restores into a missing dir with no clone and no PAT."""

    async def fail_token(*args, **kwargs):  # pragma: no cover - must not run
        raise AssertionError("self-contained restore must not resolve credentials")

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.managed_api_key_resolve."
        "resolve_github_token_for_launch",
        fail_token,
    )
    workspace_id = _workspace_id()
    payload = _tar_bytes(
        [
            ("report.md", b"# saved work\n", "file"),
            ("src/main.py", b"print(1)\n", "file"),
        ]
    )
    service = FakeArtifactService({"checkpoint": payload})
    materializer = OmnigentWorkspaceMaterializer(
        command_runner=_never_clone,
        workspace_root=tmp_path,
        artifact_service=service,
    )
    result = await materializer.materialize(
        _request(_authored_checkpoint_spec(workspace_id, payload)),
        runtime_uid=os.getuid(),
        runtime_gid=os.getgid(),
    )
    assert result["kind"] == "bind"
    workspace = tmp_path / "temporal_sandbox" / workspace_id / "repo"
    assert (workspace / "report.md").read_text() == "# saved work\n"
    assert (workspace / "src" / "main.py").read_text() == "print(1)\n"
    store = SandboxWorkspaceRecordStore(tmp_path)
    assert store.is_materialized(workspace_id)
    readiness = store.read_readiness(workspace_id)
    assert readiness is not None
    assert readiness["fingerprint"]["sourceDigest"] == _digest(payload)


@pytest.mark.asyncio
async def test_authoritative_restore_drops_destination_only_files(tmp_path):
    workspace_id = _workspace_id()
    workspace = tmp_path / "temporal_sandbox" / workspace_id / "repo"
    (workspace / ".git" / "refs").mkdir(parents=True)
    (workspace / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (workspace / "stale-clone.txt").write_text("prior clone residue\n")
    payload = _tar_bytes([("fresh.txt", b"snapshot\n", "file")])
    service = FakeArtifactService({"checkpoint": payload})
    materializer = OmnigentWorkspaceMaterializer(
        command_runner=_never_clone,
        workspace_root=tmp_path,
        artifact_service=service,
    )
    await materializer.materialize(
        _request(_authored_checkpoint_spec(workspace_id, payload)),
        runtime_uid=os.getuid(),
        runtime_gid=os.getgid(),
    )
    assert (workspace / "fresh.txt").read_text() == "snapshot\n"
    assert not (workspace / "stale-clone.txt").exists()
    assert not (workspace / ".git").exists()


@pytest.mark.asyncio
async def test_retry_reconciles_same_generation_without_reprojection(tmp_path):
    workspace_id = _workspace_id()
    payload = _tar_bytes([("ok.txt", b"v1\n", "file")])
    service = FakeArtifactService({"checkpoint": payload})
    materializer = OmnigentWorkspaceMaterializer(
        command_runner=_never_clone,
        workspace_root=tmp_path,
        artifact_service=service,
    )
    spec = _authored_checkpoint_spec(workspace_id, payload)
    await materializer.materialize(
        _request(spec), runtime_uid=os.getuid(), runtime_gid=os.getgid()
    )
    reads_after_first = len(service.calls)
    await materializer.materialize(
        _request(spec), runtime_uid=os.getuid(), runtime_gid=os.getgid()
    )
    assert len(service.calls) == reads_after_first
    workspace = tmp_path / "temporal_sandbox" / workspace_id / "repo"
    assert (workspace / "ok.txt").read_text() == "v1\n"


@pytest.mark.asyncio
async def test_changed_inputs_require_a_new_verified_import(tmp_path):
    workspace_id = _workspace_id()
    payload_a = _tar_bytes([("a.txt", b"A\n", "file")])
    payload_b = _tar_bytes([("b.txt", b"B\n", "file")])
    service = FakeArtifactService({"a": payload_a, "b": payload_b})
    materializer = OmnigentWorkspaceMaterializer(
        command_runner=_never_clone,
        workspace_root=tmp_path,
        artifact_service=service,
    )

    def _spec(ref: str, payload: bytes) -> dict:
        return _locator_spec(
            workspace_id,
            workspaceSource={
                "kind": "checkpoint",
                "checkpointRef": f"artifact://{ref}",
                "checkpointDigest": _digest(payload),
                "restoreContract": "moonmind.worktree-archive.v1",
            },
        )

    await materializer.materialize(
        _request(_spec("a", payload_a)),
        runtime_uid=os.getuid(),
        runtime_gid=os.getgid(),
    )
    workspace = tmp_path / "temporal_sandbox" / workspace_id / "repo"
    assert (workspace / "a.txt").read_text() == "A\n"
    await materializer.materialize(
        _request(_spec("b", payload_b)),
        runtime_uid=os.getuid(),
        runtime_gid=os.getgid(),
    )
    assert (workspace / "b.txt").read_text() == "B\n"
    assert not (workspace / "a.txt").exists()
    readiness = SandboxWorkspaceRecordStore(tmp_path).read_readiness(workspace_id)
    assert readiness is not None
    assert readiness["fingerprint"]["sourceDigest"] == _digest(payload_b)


@pytest.mark.asyncio
async def test_interrupted_extraction_leaves_no_partial_content(tmp_path):
    workspace_id = _workspace_id()
    sibling_id = _workspace_id("workflow-1", "sibling-step")
    sibling = tmp_path / "temporal_sandbox" / sibling_id / "repo"
    sibling.mkdir(parents=True)
    (sibling / "theirs.txt").write_text("other owner\n")
    payload = _tar_bytes(
        [("first.txt", b"partial\n", "file"), ("../escape.txt", b"x", "file")]
    )
    service = FakeArtifactService({"checkpoint": payload})
    materializer = OmnigentWorkspaceMaterializer(
        command_runner=_never_clone,
        workspace_root=tmp_path,
        artifact_service=service,
    )
    with pytest.raises(HarnessPlatformError):
        await materializer.materialize(
            _request(_authored_checkpoint_spec(workspace_id, payload)),
            runtime_uid=os.getuid(),
            runtime_gid=os.getgid(),
        )
    workspace = tmp_path / "temporal_sandbox" / workspace_id / "repo"
    assert list(workspace.iterdir()) == []
    assert SandboxWorkspaceRecordStore(tmp_path).read_readiness(workspace_id) is None
    # Another owner's content is untouched by the failed import.
    assert (sibling / "theirs.txt").read_text() == "other owner\n"
    # A retry with a valid snapshot reconciles the same generation.
    good = _tar_bytes([("good.txt", b"recovered\n", "file")])
    service.payloads["checkpoint"] = good
    await materializer.materialize(
        _request(_authored_checkpoint_spec(workspace_id, good)),
        runtime_uid=os.getuid(),
        runtime_gid=os.getgid(),
    )
    assert (workspace / "good.txt").read_text() == "recovered\n"


def test_late_cleanup_removes_only_staging_generations(tmp_path):
    live = tmp_path / "temporal_sandbox" / "ws-live" / "repo"
    live.mkdir(parents=True)
    (live / "work.txt").write_text("live\n")
    staging = tmp_path / "temporal_sandbox" / ".moonmind-import-abc123"
    staging.mkdir()
    (staging / "partial.txt").write_text("partial\n")
    assert cleanup_import_staging(tmp_path / "temporal_sandbox") == 1
    assert (live / "work.txt").read_text() == "live\n"
    assert not staging.exists()
    assert cleanup_import_staging(tmp_path / "temporal_sandbox") == 0


# ---------------------------------------------------------------------------
# Impl 6: neutralize imported authority, preserve history as data.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_restored_git_is_usable_without_imported_authority(tmp_path):
    payload = _tar_bytes(
        [
            (".git/HEAD", b"ref: refs/heads/main\n", "file"),
            (".git/refs/heads/main", b"abc123\n", "file"),
            (".git/objects/pack/data.pack", b"\x00packdata", "file"),
            (".git/hooks/pre-commit", b"#!/bin/sh\nevil\n", "file"),
            (
                ".git/config",
                b"[core]\n\thooksPath = /tmp/e\n"
                b"[credential]\n\thelper = store\n",
                "file",
            ),
            (
                ".gitmodules",
                b'[submodule "x"]\n'
                b"\tpath = x\n"
                b"\turl = https://example.com/x.git\n",
                "file",
            ),
            ("x/ok.txt", b"submodule content\n", "file"),
            (".aws/credentials", b"[default]\n", "file"),
            (".netrc", b"machine example.com password s3cr3t\n", "file"),
            (".moonmind/session-lease.json", b'{"lease": true}\n', "file"),
            ("run.sh", b"#!/bin/sh\necho hi\n", "file"),
        ]
    )
    workspace_id = _workspace_id()
    service = FakeArtifactService({"checkpoint": payload})
    materializer = OmnigentWorkspaceMaterializer(
        command_runner=_never_clone,
        workspace_root=tmp_path,
        artifact_service=service,
    )
    await materializer.materialize(
        _request(_authored_checkpoint_spec(workspace_id, payload)),
        runtime_uid=os.getuid(),
        runtime_gid=os.getgid(),
    )
    workspace = tmp_path / "temporal_sandbox" / workspace_id / "repo"
    # History stays usable as data.
    assert (workspace / ".git" / "refs" / "heads" / "main").read_text() == "abc123\n"
    assert (workspace / ".git" / "objects" / "pack" / "data.pack").exists()
    assert (workspace / ".gitmodules").exists()
    # Ordinary executable source files are preserved, not blanket-rejected.
    assert (workspace / "run.sh").exists()
    # Imported hooks, credential bindings, and session authority are gone.
    assert not (workspace / ".git" / "hooks" / "pre-commit").exists()
    config = (workspace / ".git" / "config").read_text()
    assert "hooksPath" not in config and "helper = store" not in config
    assert not (workspace / ".aws").exists()
    assert not (workspace / ".netrc").exists()
    assert not (workspace / ".moonmind" / "session-lease.json").exists()


@pytest.mark.asyncio
async def test_external_git_bindings_and_thin_bundles_fail_closed(tmp_path):
    workspace_id = _workspace_id()
    service = FakeArtifactService({})
    materializer = OmnigentWorkspaceMaterializer(
        command_runner=_never_clone,
        workspace_root=tmp_path,
        artifact_service=service,
    )

    async def _fails(payload: bytes) -> None:
        ref = f"artifact://case-{len(service.payloads)}"
        service.payloads[ref.removeprefix("artifact://")] = payload
        spec = _locator_spec(
            workspace_id,
            workspaceSource={
                "kind": "checkpoint",
                "checkpointRef": ref,
                "checkpointDigest": _digest(payload),
                "restoreContract": "moonmind.worktree-archive.v1",
            },
        )
        with pytest.raises(HarnessPlatformError):
            await materializer.materialize(
                _request(spec),
                runtime_uid=os.getuid(),
                runtime_gid=os.getgid(),
            )

    await _fails(_tar_bytes([(".git", b"gitdir: /etc/evil\n", "file")]))
    await _fails(
        _tar_bytes(
            [
                (".git/HEAD", b"ref: refs/heads/main\n", "file"),
                (".git/objects/info/alternates", b"/mnt/external-objects\n", "file"),
            ]
        )
    )
    await _fails(
        _tar_bytes(
            [
                (
                    ".gitmodules",
                    b'[submodule "x"]\n\turl = https://user:pass@example.com/x.git\n',
                    "file",
                ),
            ]
        )
    )
    assert SandboxWorkspaceRecordStore(tmp_path).read_readiness(workspace_id) is None


# ---------------------------------------------------------------------------
# Impl 7: non-Git/binary round-trip with truthful completeness.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_non_git_binary_content_round_trips_without_synthetic_repo(tmp_path):
    binary = bytes(range(256)) * 64 + b"\x00\xff binary tail"
    payload = _tar_bytes(
        [("data/model.bin", binary, "file"), ("notes.txt", b"plain\n", "file")]
    )
    workspace_id = _workspace_id()
    service = FakeArtifactService({"checkpoint": payload})
    materializer = OmnigentWorkspaceMaterializer(
        command_runner=_never_clone,
        workspace_root=tmp_path,
        artifact_service=service,
    )
    await materializer.materialize(
        _request(_authored_checkpoint_spec(workspace_id, payload)),
        runtime_uid=os.getuid(),
        runtime_gid=os.getgid(),
    )
    workspace = tmp_path / "temporal_sandbox" / workspace_id / "repo"
    assert (workspace / "data" / "model.bin").read_bytes() == binary
    # Non-Git content needs no synthetic commits or repository IDs.
    assert not (workspace / ".git").exists()

    manifest_workspace = tmp_path / "manifest-check"
    manifest_workspace.mkdir()
    evidence = await WorkspaceArtifactProjector(service).project(
        manifest_workspace,
        checkpoint_ref="artifact://checkpoint",
        checkpoint_digest=_digest(payload),
        workflow_id="workflow-1",
        runtime_uid=os.getuid(),
        runtime_gid=os.getgid(),
        strict_admission=True,
    )
    manifest = evidence["checkpointManifest"]
    assert manifest["fileCount"] == 2
    assert manifest["symlinkCount"] == 0
    assert manifest["manifestDigest"].startswith("sha256:")
    assert manifest["expandedBytes"] == len(binary) + len(b"plain\n")


@pytest.mark.asyncio
async def test_valid_relative_symlink_round_trips_with_truthful_manifest(tmp_path):
    payload = _tar_bytes(
        [("real.txt", b"data\n", "file"), ("link.txt", b"", "symlink", "real.txt")]
    )
    workspace = tmp_path / "ws"
    workspace.mkdir()
    evidence = await WorkspaceArtifactProjector(
        FakeArtifactService({"c": payload})
    ).project(
        workspace,
        checkpoint_ref="artifact://c",
        checkpoint_digest=_digest(payload),
        workflow_id="workflow-1",
        runtime_uid=os.getuid(),
        runtime_gid=os.getgid(),
        strict_admission=True,
    )
    assert (workspace / "link.txt").is_symlink()
    assert (workspace / "link.txt").read_text() == "data\n"
    manifest = evidence["checkpointManifest"]
    assert manifest["fileCount"] == 1
    assert manifest["symlinkCount"] == 1


# ---------------------------------------------------------------------------
# Impl 8: grants, UID/GID handoff, qualified daemon mappings.
# ---------------------------------------------------------------------------


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


_TEST_GRANT_SECRET = "test-workspace-grant-secret"


def _grant_spec(workspace_id: str, owner=("workflow-1", "step-1"),
                generation=1, mode="exclusive", grantee="workflow-1") -> dict:
    """Build an authored existing-workspace source with a server-issued grant.

    Newly authored grants must carry an HMAC issuance signature bound to
    the target (grantee) workflow; unsigned mappings are rejected at
    compile time so downgraded digests cannot verify as historical.
    """

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


@pytest.mark.asyncio
async def test_sibling_directory_without_grant_is_rejected(tmp_path):
    workspace_id = "sibling-ws"
    _ensure_owned_workspace(tmp_path, workspace_id, owner=("other-workflow", "s1"))
    materializer = OmnigentWorkspaceMaterializer(
        command_runner=_never_clone, workspace_root=tmp_path
    )
    # No grant at all: root containment alone never authorizes reuse.
    with pytest.raises(HarnessPlatformError, match="locator"):
        await materializer.materialize(
            _request(
                {
                    "workspaceLocator": {
                        "kind": "sandbox",
                        "workspaceId": "x" * 24,
                    }
                }
            ),
            runtime_uid=os.getuid(),
            runtime_gid=os.getgid(),
        )


@pytest.mark.asyncio
async def test_existing_workspace_grant_positive_and_negative(tmp_path, monkeypatch):
    monkeypatch.setenv("MOONMIND_WORKSPACE_GRANT_SECRET", _TEST_GRANT_SECRET)
    workspace_id = "shared-ws"
    _ensure_owned_workspace(tmp_path, workspace_id, owner=("owner-wf", "owner-st"))
    materializer = OmnigentWorkspaceMaterializer(
        command_runner=_never_clone, workspace_root=tmp_path
    )
    # Exclusive grant for another workflow fails: no sibling reuse. The
    # grant is issued to the intruder so the signature verifies and the
    # exclusive-use fence is what rejects it.
    with pytest.raises(HarnessPlatformError, match="exclusive"):
        await materializer.materialize(
            _request(
                _grant_spec(
                    workspace_id,
                    owner=("owner-wf", "owner-st"),
                    grantee="intruder-wf",
                ),
                workflow_id="intruder-wf",
                step_id="intruder-st",
            ),
            runtime_uid=os.getuid(),
            runtime_gid=os.getgid(),
        )
    # Read-only sharing with another workflow succeeds as read-only.
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
    workspace = tmp_path / "temporal_sandbox" / workspace_id / "repo"
    assert (workspace / "owned.txt").read_text() == "owner content\n"
    # The reader's finalized execution releases its claim; owner exclusive
    # use then succeeds read-write instead of conflicting indefinitely.
    from moonmind.omnigent.workspace_sources import parse_existing_workspace_grant
    from moonmind.workflows.temporal.runtime.workspace_locators import (
        SandboxWorkspaceRecordStore as _RecordStore,
    )

    _store = _RecordStore(tmp_path)
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


@pytest.mark.asyncio
async def test_stale_grant_generation_fails_after_advance(tmp_path, monkeypatch):
    monkeypatch.setenv("MOONMIND_WORKSPACE_GRANT_SECRET", _TEST_GRANT_SECRET)
    workspace_id = "gen-ws"
    _ensure_owned_workspace(tmp_path, workspace_id, owner=("owner-wf", "owner-st"))
    materializer = OmnigentWorkspaceMaterializer(
        command_runner=_never_clone, workspace_root=tmp_path
    )
    await materializer.materialize(
        _request(
            _grant_spec(
                workspace_id,
                owner=("owner-wf", "owner-st"),
                generation=2,
                grantee="owner-wf",
            ),
            workflow_id="owner-wf",
            step_id="owner-st",
        ),
        runtime_uid=os.getuid(),
        runtime_gid=os.getgid(),
    )
    with pytest.raises(HarnessPlatformError, match="stale"):
        await materializer.materialize(
            _request(
                _grant_spec(
                    workspace_id,
                    owner=("owner-wf", "owner-st"),
                    generation=1,
                    grantee="owner-wf",
                ),
                workflow_id="owner-wf",
                step_id="owner-st",
            ),
            runtime_uid=os.getuid(),
            runtime_gid=os.getgid(),
        )


@pytest.mark.asyncio
async def test_authored_grant_without_hmac_is_rejected(tmp_path, monkeypatch):
    """Newly authored grants must carry a server-issued HMAC signature."""

    monkeypatch.setenv("MOONMIND_WORKSPACE_GRANT_SECRET", _TEST_GRANT_SECRET)
    workspace_id = "hmac-ws"
    _ensure_owned_workspace(tmp_path, workspace_id, owner=("owner-wf", "owner-st"))
    materializer = OmnigentWorkspaceMaterializer(
        command_runner=_never_clone, workspace_root=tmp_path
    )
    unsigned = {
        "workspaceSource": {
            "kind": "existing_workspace",
            "existingWorkspaceGrant": {
                "workspaceId": workspace_id,
                "ownerWorkflowId": "owner-wf",
                "ownerStepExecutionId": "owner-st",
                "generation": 1,
                "mode": "read_only",
            },
        }
    }
    with pytest.raises(HarnessPlatformError, match="HMAC"):
        await materializer.materialize(
            _request(unsigned, workflow_id="reader-wf", step_id="reader-st"),
            runtime_uid=os.getuid(),
            runtime_gid=os.getgid(),
        )
    downgraded = {
        "workspaceSource": {
            "kind": "existing_workspace",
            "existingWorkspaceGrant": {
                "workspaceId": workspace_id,
                "ownerWorkflowId": "owner-wf",
                "ownerStepExecutionId": "owner-st",
                "generation": 1,
                "mode": "read_only",
                "grantDigest": "sha256:" + "0" * 64,
            },
        }
    }
    with pytest.raises(HarnessPlatformError, match="HMAC"):
        await materializer.materialize(
            _request(downgraded, workflow_id="reader-wf", step_id="reader-st"),
            runtime_uid=os.getuid(),
            runtime_gid=os.getgid(),
        )


@pytest.mark.asyncio
async def test_forged_grant_does_not_poison_generation_ledger(tmp_path, monkeypatch):
    """A forged HMAC grant fails before advancing the recorded generation."""

    monkeypatch.setenv("MOONMIND_WORKSPACE_GRANT_SECRET", _TEST_GRANT_SECRET)
    workspace_id = "poison-ws"
    _ensure_owned_workspace(tmp_path, workspace_id, owner=("owner-wf", "owner-st"))
    materializer = OmnigentWorkspaceMaterializer(
        command_runner=_never_clone, workspace_root=tmp_path
    )
    forged = {
        "workspaceSource": {
            "kind": "existing_workspace",
            "existingWorkspaceGrant": {
                "workspaceId": workspace_id,
                "ownerWorkflowId": "owner-wf",
                "ownerStepExecutionId": "owner-st",
                "generation": 99,
                "mode": "read_only",
                "grantDigest": "hmac-sha256:" + "0" * 64,
                "expiresAt": "2099-01-01T00:00:00+00:00",
            },
        }
    }
    with pytest.raises(HarnessPlatformError, match="signature"):
        await materializer.materialize(
            _request(forged, workflow_id="reader-wf", step_id="reader-st"),
            runtime_uid=os.getuid(),
            runtime_gid=os.getgid(),
        )
    # The legitimate generation-1 grant still admits: the forged
    # high generation never reached the ledger.
    from moonmind.omnigent.workspace_sources import ExistingWorkspaceGrantLedger

    assert (
        ExistingWorkspaceGrantLedger(
            SandboxWorkspaceRecordStore(tmp_path).store_root
        ).admitted_generation(workspace_id)
        is None
    )
    shared = await materializer.materialize(
        _request(
            _grant_spec(
                workspace_id,
                owner=("owner-wf", "owner-st"),
                mode="read_only",
                grantee="reader-wf",
            ),
            workflow_id="reader-wf",
            step_id="reader-st",
        ),
        runtime_uid=os.getuid(),
        runtime_gid=os.getgid(),
    )
    assert shared["accessMode"] == "read-only"


@pytest.mark.asyncio
async def test_remote_daemon_without_mapping_fails_before_launch(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("WORKFLOW_DOCKER_DAEMON_MODE", "remote")
    monkeypatch.delenv("WORKFLOW_WORKSPACE_DAEMON_ROOT", raising=False)
    monkeypatch.setenv("WORKFLOW_WORKSPACE_ROOT", str(tmp_path))
    workspace_id = _workspace_id()
    (tmp_path / "temporal_sandbox" / workspace_id / "repo").mkdir(parents=True)

    async def runner(argv, input_bytes=None):
        return 0, "", ""

    materializer = OmnigentWorkspaceMaterializer(
        command_runner=runner, workspace_root=tmp_path
    )
    with pytest.raises(HarnessPlatformError, match="daemon"):
        await materializer.materialize(
            _request(_locator_spec(workspace_id)),
            runtime_uid=os.getuid(),
            runtime_gid=os.getgid(),
        )


@pytest.mark.asyncio
async def test_invalid_runtime_identity_fails_before_mutation(tmp_path):
    materializer = OmnigentWorkspaceMaterializer(
        command_runner=_never_clone, workspace_root=tmp_path
    )
    with pytest.raises(HarnessPlatformError, match="runtime identity"):
        await materializer.materialize(
            _request(_locator_spec(_workspace_id())),
            runtime_uid=-1,
            runtime_gid=-1,
        )


# ---------------------------------------------------------------------------
# Review remediation: import exclusion, strict links, LFS identity,
# submodule worktrees, promisor state, daemon modes, grant issuance.
# ---------------------------------------------------------------------------


def test_import_lock_mutual_exclusion_and_takeover(tmp_path):
    from moonmind.omnigent.workspace_artifacts import WorkspaceArtifactProjector as P

    workspace = tmp_path / "ws"
    workspace.mkdir()
    lock = P._import_lock_path(workspace)
    # The lock name must not match the staging cleanup prefix, or reaping
    # leftover generations could delete an active import lock.
    assert not lock.name.startswith(".moonmind-import-")
    P._acquire_import_lock(lock, token="t1")
    # A foreign token fails closed.
    with pytest.raises(WorkspaceArtifactProjectionError, match="in flight"):
        P._acquire_import_lock(lock, token="t2")
    # The owner releases; a non-owner never unlinks a foreign lock.
    P._release_import_lock(lock, token="t2")
    assert lock.exists()
    P._release_import_lock(lock, token="t1")
    assert not lock.exists()
    # A stale lock from a crashed attempt is taken over for the same token.
    lock.write_text("t9\n99999999\n", encoding="utf-8")
    P._acquire_import_lock(lock, token="t9")
    P._release_import_lock(lock, token="t9")
    assert not lock.exists()


@pytest.mark.asyncio
async def test_dangling_staged_symlink_fails_closed(tmp_path):
    payload = _tar_bytes(
        [
            ("real.txt", b"data\n", "file"),
            ("dangling.txt", b"", "symlink", "removed.txt"),
        ]
    )
    service = FakeArtifactService({"checkpoint": payload})
    workspace = tmp_path / "ws"
    workspace.mkdir()
    with pytest.raises(WorkspaceArtifactProjectionError, match="unresolvable"):
        await WorkspaceArtifactProjector(service).project(
            workspace,
            checkpoint_ref="artifact://checkpoint",
            checkpoint_digest=_digest(payload),
            workflow_id="workflow-1",
            runtime_uid=os.getuid(),
            runtime_gid=os.getgid(),
            strict_admission=True,
        )
    assert not any(workspace.iterdir())


@pytest.mark.asyncio
async def test_lfs_pointer_requires_its_own_object(tmp_path):
    oid = "ab" * 32
    pointer = (
        b"version https://git-lfs.github.com/spec/v1\n"
        b"oid sha256:" + oid.encode() + b"\nsize 3\n"
    )
    service = FakeArtifactService({})
    workspace = tmp_path / "ws"
    workspace.mkdir()

    async def _fails(payload: bytes) -> None:
        ref = f"artifact://case-{len(service.payloads)}"
        service.payloads[ref.removeprefix("artifact://")] = payload
        with pytest.raises(
            WorkspaceArtifactProjectionError, match="large-file objects"
        ):
            await WorkspaceArtifactProjector(service).project(
                workspace,
                checkpoint_ref=ref,
                checkpoint_digest=_digest(payload),
                workflow_id="workflow-1",
                runtime_uid=os.getuid(),
                runtime_gid=os.getgid(),
                strict_admission=True,
            )

    # No object at all, and an unrelated object, both fail.
    await _fails(_tar_bytes([("big.bin", pointer, "file")]))
    await _fails(
        _tar_bytes(
            [
                (".git/lfs/objects/cd/cd/" + "cd" * 32, b"OTHER", "file"),
                ("big.bin", pointer, "file"),
            ]
        )
    )
    # The matching object admits the restore.
    payload = _tar_bytes(
        [
            (f".git/lfs/objects/ab/ab/{oid}", b"OBJ", "file"),
            ("big.bin", pointer, "file"),
        ]
    )
    ref = "artifact://good"
    service.payloads["good"] = payload
    evidence = await WorkspaceArtifactProjector(service).project(
        workspace,
        checkpoint_ref=ref,
        checkpoint_digest=_digest(payload),
        workflow_id="workflow-1",
        runtime_uid=os.getuid(),
        runtime_gid=os.getgid(),
        strict_admission=True,
    )
    assert evidence["checkpointManifest"]["fileCount"] == 2


@pytest.mark.asyncio
async def test_submodule_requires_populated_worktree(tmp_path):
    payload = _tar_bytes(
        [
            (".git/HEAD", b"ref: refs/heads/main\n", "file"),
            (".git/modules/x/HEAD", b"ref: refs/heads/main\n", "file"),
            (
                ".gitmodules",
                b'[submodule "x"]\n\tpath = x\n'
                b"\turl = https://example.com/x.git\n",
                "file",
            ),
        ]
    )
    service = FakeArtifactService({"checkpoint": payload})
    workspace = tmp_path / "ws"
    workspace.mkdir()
    with pytest.raises(WorkspaceArtifactProjectionError, match="submodule"):
        await WorkspaceArtifactProjector(service).project(
            workspace,
            checkpoint_ref="artifact://checkpoint",
            checkpoint_digest=_digest(payload),
            workflow_id="workflow-1",
            runtime_uid=os.getuid(),
            runtime_gid=os.getgid(),
            strict_admission=True,
        )
    assert not any(workspace.iterdir())


@pytest.mark.asyncio
async def test_partial_clone_promisor_fails_closed(tmp_path):
    payload = _tar_bytes(
        [
            (".git/HEAD", b"ref: refs/heads/main\n", "file"),
            (
                ".git/config",
                b"[core]\n\trepositoryformatversion = 1\n"
                b'[remote "origin"]\n'
                b"\turl = https://example.com/r.git\n"
                b"\tpromisor = true\n",
                "file",
            ),
        ]
    )
    service = FakeArtifactService({"checkpoint": payload})
    workspace = tmp_path / "ws"
    workspace.mkdir()
    with pytest.raises(WorkspaceArtifactProjectionError, match="promisor"):
        await WorkspaceArtifactProjector(service).project(
            workspace,
            checkpoint_ref="artifact://checkpoint",
            checkpoint_digest=_digest(payload),
            workflow_id="workflow-1",
            runtime_uid=os.getuid(),
            runtime_gid=os.getgid(),
            strict_admission=True,
        )
    assert not any(workspace.iterdir())


def test_workspace_backend_rejects_unknown_daemon_mode(monkeypatch):
    from moonmind.omnigent.workspace_sources import (
        WorkspaceSourceError,
        resolve_workspace_backend,
    )

    monkeypatch.setenv("WORKFLOW_DOCKER_DAEMON_MODE", "sidecar")
    monkeypatch.delenv("WORKFLOW_WORKSPACE_DAEMON_ROOT", raising=False)
    with pytest.raises(WorkspaceSourceError, match="must be 'local' or 'remote'"):
        resolve_workspace_backend()
    monkeypatch.setenv("WORKFLOW_DOCKER_DAEMON_MODE", "local")
    assert resolve_workspace_backend() == "docker_local"
    monkeypatch.setenv("WORKFLOW_DOCKER_DAEMON_MODE", "remote")
    assert resolve_workspace_backend() == "docker_remote"


def test_grant_issuance_requires_explicit_grantee():
    from moonmind.omnigent.workspace_sources import issue_existing_workspace_grant

    with pytest.raises(TypeError):
        issue_existing_workspace_grant(
            workspace_id="w",
            owner_workflow_id="o",
            owner_step_execution_id="s",
            secret="test-secret",
        )


@pytest.mark.asyncio
async def test_expired_claim_is_reaped_for_new_grant(tmp_path, monkeypatch):
    """An unreleased expired claim cannot conflict indefinitely."""

    monkeypatch.setenv("MOONMIND_WORKSPACE_GRANT_SECRET", _TEST_GRANT_SECRET)
    workspace_id = "reap-ws"
    _ensure_owned_workspace(tmp_path, workspace_id, owner=("owner-wf", "owner-st"))
    materializer = OmnigentWorkspaceMaterializer(
        command_runner=_never_clone, workspace_root=tmp_path
    )
    store = SandboxWorkspaceRecordStore(tmp_path)
    expired = SimpleNamespace(
        workspace_id=workspace_id,
        owner_workflow_id="owner-wf",
        owner_step_execution_id="owner-st",
        generation=1,
        mode="exclusive",
        grant_digest=None,
        expires_at=datetime.now(tz=UTC) - timedelta(hours=2),
        expected_generation=None,
        grantee_workflow_id="",
    )
    store.claim_existing_workspace(workspace_id, expired)
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
