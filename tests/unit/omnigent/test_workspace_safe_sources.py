"""Safe artifact, checkpoint, and authorized existing-workspace sources.

Covers MoonLadderStudios/MoonMind#4014 across the real
API/compiler/artifact/materializer boundaries: exactly-one-source
compilation, artifact-service admission, digest-verified streaming under
explicit budgets, attempt-owned staging with sequential link safety,
authoritative promotion with bound ready markers, Git-authority
neutralization, self-contained credentialless restore, and
server-issued existing-workspace grants with qualified daemon mapping.
"""

from __future__ import annotations

import hashlib
import io
import os
import tarfile
from types import SimpleNamespace

import pytest

from moonmind.omnigent.workspace_artifacts import (
    WorkspaceArtifactProjector,
    WorkspaceArtifactProjectionError,
)
from moonmind.omnigent.workspace_sources import (
    WorkspaceSourceCompilationError,
    check_source_backend_supported,
    compile_workspace_source,
    decode_historical_workspace_path,
    issue_existing_workspace_grant,
    verify_existing_workspace_grant,
)

WORKFLOW_ID = "workflow-1"
GRANT_SECRET = "test-grant-secret-4014"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _sha(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _tar_bytes(entries: dict[str, bytes | None], *, mode: str = "w:gz") -> bytes:
    """Build an archive; None value means an explicit directory entry."""

    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode=mode) as bundle:
        for name, payload in entries.items():
            if payload is None:
                member = tarfile.TarInfo(name)
                member.type = tarfile.DIRTYPE
                bundle.addfile(member)
                continue
            member = tarfile.TarInfo(name)
            member.size = len(payload)
            bundle.addfile(member, io.BytesIO(payload))
    return archive.getvalue()


class FakeArtifactService:
    """Minimal ehrliche artifact service with full metadata semantics."""

    def __init__(self) -> None:
        self.entries: dict[str, dict] = {}
        self.metadata_calls: list[str] = []
        self.restricted_flags: list[bool] = []

    def add(
        self,
        artifact_id: str,
        payload: bytes,
        *,
        workflow_id: str = WORKFLOW_ID,
        status: str = "COMPLETE",
        sha256: str | None = "__payload__",
        redaction_level: str = "NONE",
        quarantined: bool = False,
        expires_at=None,
        owner: str = "owner-1",
    ) -> bytes:
        if sha256 == "__payload__":
            sha256 = _sha(payload)
        artifact = SimpleNamespace(
            artifact_id=artifact_id,
            status=status,
            size_bytes=len(payload),
            sha256=sha256,
            redaction_level=SimpleNamespace(value=redaction_level),
            quarantined=quarantined,
            expires_at=expires_at,
            created_by_principal=owner,
            metadata_json={},
        )
        links = (
            [SimpleNamespace(workflow_id=workflow_id)] if workflow_id else []
        )
        self.entries[artifact_id] = {
            "artifact": artifact,
            "links": links,
            "payload": payload,
        }
        return payload

    async def get_metadata(self, *, artifact_id: str, principal: str):
        self.metadata_calls.append(artifact_id)
        entry = self.entries.get(artifact_id)
        if entry is None:
            raise KeyError(artifact_id)
        return entry["artifact"], entry["links"], False, SimpleNamespace(
            raw_access_allowed=True
        )

    async def read_chunks(
        self, *, artifact_id: str, principal: str, allow_restricted_raw, chunk_size
    ):
        self.restricted_flags.append(bool(allow_restricted_raw))
        entry = self.entries[artifact_id]
        payload = entry["payload"]
        size = max(1, int(chunk_size))
        return entry["artifact"], [
            payload[index : index + size] for index in range(0, len(payload), size)
        ]

    async def read(self, *, artifact_id: str, principal: str, allow_restricted_raw):
        self.restricted_flags.append(bool(allow_restricted_raw))
        entry = self.entries[artifact_id]
        return entry["artifact"], entry["payload"]


def _projector(service: FakeArtifactService) -> WorkspaceArtifactProjector:
    return WorkspaceArtifactProjector(service)


async def _project_checkpoint(
    tmp_path,
    service: FakeArtifactService,
    payload: bytes,
    *,
    artifact_id: str = "checkpoint",
    declare_digest: bool = True,
    workflow_id: str = WORKFLOW_ID,
    grant=None,
    allow_restricted: bool = False,
    overlay: str = "authoritative",
    attempt_id: str = "attempt-1",
):
    from moonmind.omnigent.workspace_sources import CompiledWorkspaceSource

    service.add(artifact_id, payload, workflow_id=workflow_id)
    digest = _sha(payload) if declare_digest else None
    source = CompiledWorkspaceSource(
        kind="checkpoint",
        artifact_ref=f"artifact://{artifact_id}",
        checkpoint_ref=f"artifact://{artifact_id}",
        expected_digest=digest,
        restore_contract="moonmind.workspace-restore.v1",
        overlay=overlay,
        attempt_id=attempt_id,
        generation=1,
    )
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    evidence = await _projector(service).project(
        workspace,
        checkpoint_ref=f"artifact://{artifact_id}",
        workflow_id=workflow_id,
        runtime_uid=os.getuid(),
        runtime_gid=os.getgid(),
        source=source,
        grant=grant,
        allow_restricted=allow_restricted,
        target_owner=f"{workflow_id}:step-1",
    )
    return workspace, evidence


# ---------------------------------------------------------------------------
# Impl 1: exactly-one-source compilation
# ---------------------------------------------------------------------------


class TestWorkspaceSourceCompiler:
    def test_default_is_blank_scratch(self):
        assert compile_workspace_source({}).kind == "scratch"

    def test_artifact_union_requires_ref_and_digest(self):
        digest = _sha(b"x")
        source = compile_workspace_source(
            {
                "workspaceSource": {
                    "kind": "artifact",
                    "artifactRef": "artifact://abc",
                    "expectedDigest": digest,
                }
            }
        )
        assert source.kind == "artifact"
        assert source.expected_digest == digest
        with pytest.raises(WorkspaceSourceCompilationError):
            compile_workspace_source(
                {"workspaceSource": {"kind": "artifact", "artifactRef": "artifact://abc"}}
            )

    def test_checkpoint_union_rejects_unknown_contract(self):
        with pytest.raises(WorkspaceSourceCompilationError, match="contract"):
            compile_workspace_source(
                {
                    "workspaceSource": {
                        "kind": "checkpoint",
                        "checkpointRef": "artifact://abc",
                        "restoreContract": "moonmind.unknown.v9",
                    }
                }
            )

    def test_conflicting_aliases_rejected_before_mutation(self, tmp_path):
        sentinel = tmp_path / "sentinel"
        with pytest.raises(WorkspaceSourceCompilationError, match="conflicting"):
            compile_workspace_source(
                {
                    "workspaceSource": {
                        "kind": "artifact",
                        "artifactRef": "artifact://a",
                        "expectedDigest": _sha(b"a"),
                    },
                    "workspaceCheckpointRestoreRef": "artifact://b",
                }
            )
        assert not sentinel.exists()

    def test_raw_path_is_not_a_normal_authoring_route(self):
        with pytest.raises(WorkspaceSourceCompilationError, match="historical"):
            compile_workspace_source({"workspacePath": "/tmp/nowhere"})
        with pytest.raises(WorkspaceSourceCompilationError, match="historical"):
            compile_workspace_source({"path": "/tmp/nowhere"})
        # Necessary historical decoding stays explicit.
        assert (
            decode_historical_workspace_path({"workspacePath": "/tmp/nowhere"})
            == "/tmp/nowhere"
        )
        assert (
            compile_workspace_source(
                {"workspacePath": "/tmp/nowhere"}, allow_historical_path=True
            ).kind
            == "scratch"
        )

    def test_bare_branch_is_not_a_repository_source(self):
        assert compile_workspace_source({"branch": "main"}).kind == "scratch"

    def test_repository_target_compiles(self):
        source = compile_workspace_source(
            {"repositoryTarget": {"repository": {"name": "o/r"}}}
        )
        assert source.kind == "repository"


class TestExistingWorkspaceGrants:
    def test_issue_verify_roundtrip(self, monkeypatch):
        monkeypatch.setenv("MOONMIND_WORKSPACE_GRANT_SECRET", GRANT_SECRET)
        grant = issue_existing_workspace_grant(
            source_workspace_id="ws-1",
            owner_workflow_id="wf-owner",
            owner_step_execution_id="step-1",
            grantee_workflow_id="wf-grantee",
            mode="read_only",
            secret=GRANT_SECRET,
        )
        verify_existing_workspace_grant(
            grant, grantee_workflow_id="wf-grantee", secret=GRANT_SECRET
        )

    def test_wrong_grantee_rejected(self, monkeypatch):
        monkeypatch.setenv("MOONMIND_WORKSPACE_GRANT_SECRET", GRANT_SECRET)
        grant = issue_existing_workspace_grant(
            source_workspace_id="ws-1",
            owner_workflow_id="wf-owner",
            owner_step_execution_id="step-1",
            grantee_workflow_id="wf-grantee",
            secret=GRANT_SECRET,
        )
        with pytest.raises(WorkspaceSourceCompilationError, match="another workflow"):
            verify_existing_workspace_grant(
                grant, grantee_workflow_id="wf-other", secret=GRANT_SECRET
            )

    def test_expired_and_tampered_grants_rejected(self, monkeypatch):
        monkeypatch.setenv("MOONMIND_WORKSPACE_GRANT_SECRET", GRANT_SECRET)
        grant = issue_existing_workspace_grant(
            source_workspace_id="ws-1",
            owner_workflow_id="wf-owner",
            owner_step_execution_id="step-1",
            grantee_workflow_id="wf-grantee",
            lifetime_seconds=1,
            secret=GRANT_SECRET,
        )
        with pytest.raises(WorkspaceSourceCompilationError, match="expired"):
            verify_existing_workspace_grant(
                grant,
                grantee_workflow_id="wf-grantee",
                now=grant.expires_at + 10,
                secret=GRANT_SECRET,
            )
        with pytest.raises(WorkspaceSourceCompilationError, match="generation"):
            verify_existing_workspace_grant(
                grant,
                grantee_workflow_id="wf-grantee",
                expected_generation=grant.expected_generation + 1,
                secret=GRANT_SECRET,
            )

    def test_backend_matrix(self):
        # Exclusive writable use cannot be honored on a remote daemon view.
        with pytest.raises(WorkspaceSourceCompilationError, match="unsupported"):
            check_source_backend_supported(
                "existing_workspace", "docker_remote", grant_mode="exclusive"
            )
        check_source_backend_supported(
            "existing_workspace", "docker_remote", grant_mode="read_only"
        )
        check_source_backend_supported("checkpoint", "docker_local")
        with pytest.raises(WorkspaceSourceCompilationError, match="supported runtime"):
            check_source_backend_supported("scratch", "hypervisor_9")


# ---------------------------------------------------------------------------
# Impl 2: admission through the actual artifact service
# ---------------------------------------------------------------------------


class TestSourceAdmission:
    @pytest.mark.asyncio
    async def test_missing_metadata_fails(self, tmp_path):
        service = FakeArtifactService()
        workspace = tmp_path / "ws"
        workspace.mkdir()
        with pytest.raises(
            WorkspaceArtifactProjectionError, match="metadata is unavailable"
        ):
            await _projector(service).project(
                workspace,
                checkpoint_ref="artifact://absent",
                workflow_id=WORKFLOW_ID,
                runtime_uid=os.getuid(),
                runtime_gid=os.getgid(),
            )
        assert not (workspace / ".staging-ws-g1-adhoc").exists()

    @pytest.mark.asyncio
    async def test_read_bytes_only_adapter_cannot_admit(self, tmp_path):
        class ReadBytesOnly:
            async def read_bytes(self, ref: str) -> bytes:  # pragma: no cover
                return b"nope"

        workspace = tmp_path / "ws"
        workspace.mkdir()
        with pytest.raises(
            WorkspaceArtifactProjectionError, match="read-bytes-only"
        ):
            await _projector(ReadBytesOnly()).project(
                workspace,
                checkpoint_ref="artifact://abc",
                workflow_id=WORKFLOW_ID,
                runtime_uid=os.getuid(),
                runtime_gid=os.getgid(),
            )

    @pytest.mark.asyncio
    async def test_incomplete_status_fails(self, tmp_path):
        service = FakeArtifactService()
        service.add("checkpoint", _tar_bytes({"a.txt": b"a"}), status="PENDING_UPLOAD")
        workspace = tmp_path / "ws"
        workspace.mkdir()
        with pytest.raises(
            WorkspaceArtifactProjectionError, match="not a complete artifact"
        ):
            await _projector(service).project(
                workspace,
                checkpoint_ref="artifact://checkpoint",
                workflow_id=WORKFLOW_ID,
                runtime_uid=os.getuid(),
                runtime_gid=os.getgid(),
            )

    @pytest.mark.asyncio
    async def test_wrong_owner_link_fails(self, tmp_path):
        service = FakeArtifactService()
        payload = _tar_bytes({"a.txt": b"a"})
        service.add("checkpoint", payload, workflow_id="other-workflow")
        workspace = tmp_path / "ws"
        workspace.mkdir()
        with pytest.raises(
            WorkspaceArtifactProjectionError, match="not linked to the target"
        ):
            await _projector(service).project(
                workspace,
                checkpoint_ref="artifact://checkpoint",
                workflow_id=WORKFLOW_ID,
                runtime_uid=os.getuid(),
                runtime_gid=os.getgid(),
            )

    @pytest.mark.asyncio
    async def test_forged_family_prefix_link_fails(self, tmp_path):
        service = FakeArtifactService()
        payload = _tar_bytes({"a.txt": b"a"})
        # A sibling minting "workflow-1:evil" must not authorize workflow-1.
        service.add("checkpoint", payload, workflow_id="workflow-1:evil")
        workspace = tmp_path / "ws"
        workspace.mkdir()
        with pytest.raises(
            WorkspaceArtifactProjectionError, match="not linked to the target"
        ):
            await _projector(service).project(
                workspace,
                checkpoint_ref="artifact://checkpoint",
                workflow_id=WORKFLOW_ID,
                runtime_uid=os.getuid(),
                runtime_gid=os.getgid(),
            )

    @pytest.mark.asyncio
    async def test_mismatched_digest_fails(self, tmp_path):
        from moonmind.omnigent.workspace_sources import CompiledWorkspaceSource

        service = FakeArtifactService()
        payload = _tar_bytes({"a.txt": b"a"})
        service.add("checkpoint", payload)
        workspace = tmp_path / "ws"
        workspace.mkdir()
        source = CompiledWorkspaceSource(
            kind="checkpoint",
            artifact_ref="artifact://checkpoint",
            checkpoint_ref="artifact://checkpoint",
            expected_digest=_sha(b"something else entirely"),
            restore_contract="moonmind.workspace-restore.v1",
        )
        with pytest.raises(
            WorkspaceArtifactProjectionError, match="does not match the admitted"
        ):
            await _projector(service).project(
                workspace,
                checkpoint_ref="artifact://checkpoint",
                workflow_id=WORKFLOW_ID,
                runtime_uid=os.getuid(),
                runtime_gid=os.getgid(),
                source=source,
                target_owner=f"{WORKFLOW_ID}:step-1",
            )

    @pytest.mark.asyncio
    async def test_restricted_needs_explicit_policy(self, tmp_path):
        service = FakeArtifactService()
        payload = _tar_bytes({"a.txt": b"a"})
        service.add("checkpoint", payload, redaction_level="RESTRICTED")
        workspace = tmp_path / "ws"
        workspace.mkdir()
        with pytest.raises(
            WorkspaceArtifactProjectionError, match="restricted.*explicit"
        ):
            await _projector(service).project(
                workspace,
                checkpoint_ref="artifact://checkpoint",
                workflow_id=WORKFLOW_ID,
                runtime_uid=os.getuid(),
                runtime_gid=os.getgid(),
            )
        # Generic restore never releases protected raw content implicitly:
        # the recorded service flag must stay False.
        assert service.restricted_flags == []
        workspace_ok, _evidence = await _project_checkpoint(
            tmp_path / "second",
            service,
            payload,
            allow_restricted=True,
        )
        assert (workspace_ok / "a.txt").read_bytes() == b"a"
        assert service.restricted_flags and all(service.restricted_flags)

    @pytest.mark.asyncio
    async def test_quarantined_never_enters_workspace(self, tmp_path):
        service = FakeArtifactService()
        payload = _tar_bytes({"a.txt": b"a"})
        service.add("checkpoint", payload, quarantined=True)
        workspace = tmp_path / "ws"
        workspace.mkdir()
        with pytest.raises(
            WorkspaceArtifactProjectionError, match="quarantined"
        ):
            await _projector(service).project(
                workspace,
                checkpoint_ref="artifact://checkpoint",
                workflow_id=WORKFLOW_ID,
                runtime_uid=os.getuid(),
                runtime_gid=os.getgid(),
                allow_restricted=True,
            )


# ---------------------------------------------------------------------------
# Impl 3: budgets and archive classification
# ---------------------------------------------------------------------------


class TestArchiveBudgets:
    @pytest.mark.asyncio
    async def test_expanded_bytes_bound_enforced(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ):
        import moonmind.omnigent.workspace_artifacts as artifacts

        monkeypatch.setattr(artifacts, "MAX_EXPANDED_BYTES", 1024)
        service = FakeArtifactService()
        payload = _tar_bytes({f"f{i}.bin": b"y" * 512 for i in range(4)})
        with pytest.raises(
            WorkspaceArtifactProjectionError, match="expanded-bytes bound"
        ):
            await _project_checkpoint(tmp_path, service, payload)

    @pytest.mark.asyncio
    async def test_file_count_bound_enforced(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ):
        import moonmind.omnigent.workspace_artifacts as artifacts

        monkeypatch.setattr(artifacts, "MAX_ARCHIVE_FILES", 3)
        service = FakeArtifactService()
        payload = _tar_bytes({f"f{i}.txt": b"x" for i in range(5)})
        with pytest.raises(
            WorkspaceArtifactProjectionError, match="file-count bound"
        ):
            await _project_checkpoint(tmp_path, service, payload)

    @pytest.mark.asyncio
    async def test_per_file_bound_enforced_on_header(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ):
        import moonmind.omnigent.workspace_artifacts as artifacts

        monkeypatch.setattr(artifacts, "MAX_ARCHIVE_FILE_BYTES", 8)
        service = FakeArtifactService()
        payload = _tar_bytes({"big.bin": b"0" * 64})
        with pytest.raises(
            WorkspaceArtifactProjectionError, match="per-file bound"
        ):
            await _project_checkpoint(tmp_path, service, payload)

    @pytest.mark.asyncio
    async def test_truncated_archive_classified(self, tmp_path):
        service = FakeArtifactService()
        payload = _tar_bytes({"a.txt": b"hello"})[:40]
        with pytest.raises(WorkspaceArtifactProjectionError) as excinfo:
            await _project_checkpoint(tmp_path, service, payload)
        assert excinfo.value.code in {
            "WORKSPACE_ARCHIVE_MALFORMED",
            "WORKSPACE_ARCHIVE_UNSUPPORTED",
        }

    @pytest.mark.asyncio
    async def test_unsupported_format_classified(self, tmp_path):
        service = FakeArtifactService()
        with pytest.raises(
            WorkspaceArtifactProjectionError, match="unsupported"
        ) as excinfo:
            await _project_checkpoint(tmp_path, service, b"PK\x03\x04 not a tar")
        assert excinfo.value.code == "WORKSPACE_ARCHIVE_UNSUPPORTED"

    @pytest.mark.asyncio
    async def test_no_completed_marker_on_failure(self, tmp_path):
        service = FakeArtifactService()
        payload = _tar_bytes({"a.txt": b"hello"})[:40]
        workspace = tmp_path / "ws"
        workspace.mkdir()
        with pytest.raises(WorkspaceArtifactProjectionError):
            await _project_checkpoint(tmp_path, service, payload)
        assert (workspace / "a.txt").exists() is False


# ---------------------------------------------------------------------------
# Impl 4: staging safety — paths, links, duplicates, collisions
# ---------------------------------------------------------------------------


class TestStagingSafety:
    @pytest.mark.asyncio
    async def test_absolute_and_escaping_paths_rejected(self, tmp_path):
        service = FakeArtifactService()
        for index, name in enumerate(("/abs.txt", "../escape.txt", "a/../../escape.txt")):
            archive = io.BytesIO()
            with tarfile.open(fileobj=archive, mode="w:gz") as bundle:
                member = tarfile.TarInfo(name)
                member.size = 1
                bundle.addfile(member, io.BytesIO(b"x"))
            with pytest.raises(
                WorkspaceArtifactProjectionError, match="absolute|escaping"
            ):
                await _project_checkpoint(tmp_path / f"case{index}", service, archive.getvalue())

    @pytest.mark.asyncio
    async def test_symlink_escape_rejected(self, tmp_path):
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w:gz") as bundle:
            member = tarfile.TarInfo("evil")
            member.type = tarfile.SYMTYPE
            member.linkname = "../../etc/passwd"
            bundle.addfile(member)
        service = FakeArtifactService()
        with pytest.raises(
            WorkspaceArtifactProjectionError, match="escapes the workspace"
        ):
            await _project_checkpoint(tmp_path, service, archive.getvalue())

    @pytest.mark.asyncio
    async def test_hardlink_forward_reference_rejected(self, tmp_path):
        # Link-order attack: the hardlink names a file created later.
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w:gz") as bundle:
            link = tarfile.TarInfo("first")
            link.type = tarfile.LNKTYPE
            link.linkname = "second"
            bundle.addfile(link)
            member = tarfile.TarInfo("second")
            member.size = 3
            bundle.addfile(member, io.BytesIO(b"abc"))
        service = FakeArtifactService()
        with pytest.raises(
            WorkspaceArtifactProjectionError, match="unavailable entry"
        ):
            await _project_checkpoint(tmp_path, service, archive.getvalue())

    @pytest.mark.asyncio
    async def test_hardlink_to_earlier_file_allowed(self, tmp_path):
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w:gz") as bundle:
            member = tarfile.TarInfo("second")
            member.size = 3
            bundle.addfile(member, io.BytesIO(b"abc"))
            link = tarfile.TarInfo("first")
            link.type = tarfile.LNKTYPE
            link.linkname = "second"
            bundle.addfile(link)
        service = FakeArtifactService()
        workspace, _evidence = await _project_checkpoint(
            tmp_path, service, archive.getvalue()
        )
        assert (workspace / "first").read_bytes() == b"abc"

    @pytest.mark.asyncio
    async def test_duplicate_and_collision_rejected(self, tmp_path):
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w") as bundle:
            for name in ("dup.txt", "./dup.txt"):
                member = tarfile.TarInfo(name)
                member.size = 1
                bundle.addfile(member, io.BytesIO(b"x"))
        raw = archive.getvalue()
        import gzip

        payload = gzip.compress(raw)
        service = FakeArtifactService()
        with pytest.raises(
            WorkspaceArtifactProjectionError, match="duplicate|conflicting"
        ):
            await _project_checkpoint(tmp_path, service, payload)

        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w:gz") as bundle:
            for name in ("Case.txt", "case.txt"):
                member = tarfile.TarInfo(name)
                member.size = 1
                bundle.addfile(member, io.BytesIO(b"x"))
        with pytest.raises(
            WorkspaceArtifactProjectionError, match="conflicting filesystem"
        ):
            await _project_checkpoint(tmp_path, service, archive.getvalue())

    @pytest.mark.asyncio
    async def test_device_node_rejected(self, tmp_path):
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w:gz") as bundle:
            member = tarfile.TarInfo("fifo-entry")
            member.type = tarfile.FIFOTYPE
            bundle.addfile(member)
        service = FakeArtifactService()
        with pytest.raises(
            WorkspaceArtifactProjectionError, match="unsupported entry"
        ):
            await _project_checkpoint(tmp_path, service, archive.getvalue())

    @pytest.mark.asyncio
    async def test_privileged_mode_normalized(self, tmp_path):
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w:gz") as bundle:
            member = tarfile.TarInfo("suid-bin")
            member.size = 2
            member.mode = 0o4755
            bundle.addfile(member, io.BytesIO(b"hi"))
            script = tarfile.TarInfo("run.sh")
            script.size = 2
            script.mode = 0o755
            bundle.addfile(script, io.BytesIO(b"hi"))
        service = FakeArtifactService()
        workspace, _evidence = await _project_checkpoint(
            tmp_path, service, archive.getvalue()
        )
        import stat as statmod

        assert statmod.S_IMODE((workspace / "suid-bin").stat().st_mode) == 0o755
        assert statmod.S_IMODE((workspace / "run.sh").stat().st_mode) == 0o755


# ---------------------------------------------------------------------------
# Impl 5: authoritative promotion, bound markers, crash safety
# ---------------------------------------------------------------------------


class TestAuthoritativePromotion:
    @pytest.mark.asyncio
    async def test_destination_residue_not_retained(self, tmp_path):
        service = FakeArtifactService()
        payload = _tar_bytes({"fresh.txt": b"new"})
        workspace = tmp_path / "ws"
        workspace.mkdir()
        (workspace / "prior-clone-residue.txt").write_text("stale")
        (workspace / ".git" / "info").mkdir(parents=True)
        service.add("checkpoint", payload)
        from moonmind.omnigent.workspace_sources import CompiledWorkspaceSource

        source = CompiledWorkspaceSource(
            kind="checkpoint",
            artifact_ref="artifact://checkpoint",
            checkpoint_ref="artifact://checkpoint",
            expected_digest=_sha(payload),
            restore_contract="moonmind.workspace-restore.v1",
            attempt_id="attempt-1",
            generation=1,
        )
        await _projector(service).project(
            workspace,
            checkpoint_ref="artifact://checkpoint",
            workflow_id=WORKFLOW_ID,
            runtime_uid=os.getuid(),
            runtime_gid=os.getgid(),
            source=source,
            target_owner=f"{WORKFLOW_ID}:step-1",
        )
        assert (workspace / "fresh.txt").read_bytes() == b"new"
        assert not (workspace / "prior-clone-residue.txt").exists()
        assert not (workspace / ".git").exists()

    @pytest.mark.asyncio
    async def test_retry_reconciles_same_generation(self, tmp_path):
        service = FakeArtifactService()
        payload = _tar_bytes({"a.txt": b"v1"})
        workspace, evidence = await _project_checkpoint(tmp_path, service, payload)
        binding = evidence["checkpointRestore"]
        assert binding["sourceDigest"] == _sha(payload)
        assert binding["generation"] == 1
        # A retry with the same inputs reconciles to the same content.
        workspace2, evidence2 = await _project_checkpoint(
            tmp_path, service, payload, attempt_id="attempt-1"
        )
        assert (workspace2 / "a.txt").read_bytes() == b"v1"
        assert evidence2["checkpointRestore"]["sourceDigest"] == binding["sourceDigest"]

    @pytest.mark.asyncio
    async def test_concurrent_import_fails_closed(self, tmp_path):
        service = FakeArtifactService()
        payload = _tar_bytes({"a.txt": b"v1"})
        service.add("checkpoint", payload)
        workspace = tmp_path / "ws"
        workspace.mkdir()
        lock = workspace.parent / ".import-ws.lock"
        lock.write_text("foreign-attempt", encoding="utf-8")
        from moonmind.omnigent.workspace_sources import CompiledWorkspaceSource

        source = CompiledWorkspaceSource(
            kind="checkpoint",
            artifact_ref="artifact://checkpoint",
            checkpoint_ref="artifact://checkpoint",
            expected_digest=_sha(payload),
            restore_contract="moonmind.workspace-restore.v1",
            attempt_id="attempt-2",
            generation=1,
        )
        with pytest.raises(
            WorkspaceArtifactProjectionError, match="already in flight"
        ):
            await _projector(service).project(
                workspace,
                checkpoint_ref="artifact://checkpoint",
                workflow_id=WORKFLOW_ID,
                runtime_uid=os.getuid(),
                runtime_gid=os.getgid(),
                source=source,
                target_owner=f"{WORKFLOW_ID}:step-1",
            )
        assert not (workspace / "a.txt").exists()

    @pytest.mark.asyncio
    async def test_additive_overlay_preserves_destination(self, tmp_path):
        service = FakeArtifactService()
        payload = _tar_bytes({"overlay.txt": b"added"})
        workspace, _evidence = await _project_checkpoint(
            tmp_path, service, payload, overlay="additive", attempt_id="attempt-9"
        )
        assert (workspace / "overlay.txt").read_bytes() == b"added"


# ---------------------------------------------------------------------------
# Impl 6: Git-authority neutralization
# ---------------------------------------------------------------------------


class TestGitNeutralization:
    @pytest.mark.asyncio
    async def test_hooks_config_credentials_sessions_neutralized(self, tmp_path):
        config = (
            "[core]\n\trepositoryformatversion = 0\n\tsshCommand = ssh -i /evil\n"
            "\thooksPath = /evil/hooks\n[credential]\n\thelper = evil\n"
            "[filter \"lfs\"]\n\tclean = git-lfs clean %f\n"
            "[alias]\n\tambush = !evil\n"
            "[url \"https://evil.example/\"]\n\tinsteadOf = https://github.com/\n"
            '[include]\n\tpath = /evil/inc\n[remote "origin"]\n\turl = https://github.com/o/r.git\n'
        )
        payload = _tar_bytes(
            {
                "tracked.txt": b"work",
                "run.sh": b"#!/bin/sh\necho hi\n",
                ".git/HEAD": b"ref: refs/heads/main\n",
                ".git/config": config.encode(),
                ".git/hooks/pre-commit": b"#!/bin/sh\nevil\n",
                ".git/objects/pack/keep": b"objects stay as data",
                ".ssh/id_rsa": b"private",
                ".moonmind/session/current.json": b"{}",
                ".moonmind/restore/keep": b"current authority inputs stay",
            }
        )
        service = FakeArtifactService()
        workspace = tmp_path / "ws"
        workspace.mkdir()
        (workspace / ".moonmind" / "restore").mkdir(parents=True)
        (workspace / ".moonmind" / "restore" / "keep").write_bytes(
            b"current authority inputs stay"
        )
        workspace, _evidence = await _project_checkpoint(tmp_path, service, payload)
        # Safe content and history stay as data.
        assert (workspace / "tracked.txt").read_bytes() == b"work"
        assert (workspace / "run.sh").read_bytes() == b"#!/bin/sh\necho hi\n"
        assert (workspace / ".git" / "HEAD").exists()
        assert (workspace / ".git" / "objects" / "pack" / "keep").exists()
        assert "github.com/o/r" in (workspace / ".git" / "config").read_text()
        # Executable/credential/redirect mechanisms are neutralized.
        assert not (workspace / ".git" / "hooks" / "pre-commit").exists()
        sanitized = (workspace / ".git" / "config").read_text()
        assert "sshCommand" not in sanitized
        assert "credential" not in sanitized.lower() or "helper" not in sanitized
        assert "insteadOf" not in sanitized
        assert "/evil/inc" not in sanitized
        assert "git-lfs clean" not in sanitized
        assert not (workspace / ".ssh").exists()
        assert not (workspace / ".moonmind" / "session").exists()

    @pytest.mark.asyncio
    async def test_external_gitdir_rejected(self, tmp_path):
        payload = _tar_bytes(
            {".git": b"gitdir: /etc/external.git\n", "a.txt": b"a"}
        )
        service = FakeArtifactService()
        with pytest.raises(
            WorkspaceArtifactProjectionError, match="external git directory"
        ):
            await _project_checkpoint(tmp_path, service, payload)

    @pytest.mark.asyncio
    async def test_incomplete_restores_fail(self, tmp_path):
        service = FakeArtifactService()
        # External object alternates.
        payload = _tar_bytes(
            {
                "a.txt": b"a",
                ".git/objects/info/alternates": b"/external/objects\n",
            }
        )
        with pytest.raises(
            WorkspaceArtifactProjectionError, match="incomplete"
        ) as excinfo:
            await _project_checkpoint(tmp_path, service, payload)
        assert excinfo.value.code == "WORKSPACE_RESTORE_INCOMPLETE"
        # Missing submodule content.
        payload = _tar_bytes(
            {
                "a.txt": b"a",
                ".git/HEAD": b"ref: refs/heads/main\n",
                ".gitmodules": b'[submodule "dep"]\n\tpath = dep\n\turl = https://example.com/dep.git\n',
            }
        )
        with pytest.raises(
            WorkspaceArtifactProjectionError, match="submodule.*admission"
        ):
            await _project_checkpoint(tmp_path, service, payload)
        # LFS pointers without objects.
        payload = _tar_bytes(
            {
                "a.txt": b"a",
                "blob.bin": b"version https://git-lfs.github.com/spec/v1\noid sha256:abc\n",
            }
        )
        with pytest.raises(
            WorkspaceArtifactProjectionError, match="large-file objects"
        ):
            await _project_checkpoint(tmp_path, service, payload)


# ---------------------------------------------------------------------------
# Impl 7: self-contained and non-Git/binary round-trips
# ---------------------------------------------------------------------------


class TestSelfContainedRestore:
    @pytest.mark.asyncio
    async def test_non_git_binary_roundtrip_without_synthetic_repo(self, tmp_path):
        binary = bytes(range(256)) * 64
        payload = _tar_bytes({"model.bin": binary, "notes/report.md": b"# report\n"})
        service = FakeArtifactService()
        workspace, evidence = await _project_checkpoint(tmp_path, service, payload)
        assert (workspace / "model.bin").read_bytes() == binary
        assert not (workspace / ".git").exists()
        assert evidence["checkpointRestore"]["manifest"]["files"] >= 2

    @pytest.mark.asyncio
    async def test_digest_verified_bytes(self, tmp_path):
        service = FakeArtifactService()
        payload = _tar_bytes({"a.txt": b"exact bytes"})
        workspace, evidence = await _project_checkpoint(tmp_path, service, payload)
        assert evidence["checkpointRestore"]["sourceDigest"] == _sha(payload)

    @pytest.mark.asyncio
    async def test_grant_carries_cross_workflow_admission(self, tmp_path, monkeypatch):
        from moonmind.omnigent.workspace_sources import (
            CompiledWorkspaceSource,
            issue_existing_workspace_grant,
        )

        monkeypatch.setenv("MOONMIND_WORKSPACE_GRANT_SECRET", "suite-secret")
        service = FakeArtifactService()
        payload = _tar_bytes({"shared.txt": b"shared work"})
        # The artifact is owned/linked by another workflow; only the explicit
        # grant admits this target.
        service.add("checkpoint", payload, workflow_id="wf-owner")
        grant = issue_existing_workspace_grant(
            source_workspace_id="ws-owner",
            owner_workflow_id="wf-owner",
            owner_step_execution_id="step-9",
            grantee_workflow_id=WORKFLOW_ID,
            mode="read_only",
            secret="suite-secret",
        )
        source = CompiledWorkspaceSource(
            kind="checkpoint",
            artifact_ref="artifact://checkpoint",
            checkpoint_ref="artifact://checkpoint",
            expected_digest=_sha(payload),
            restore_contract="moonmind.workspace-restore.v1",
            attempt_id="attempt-grant",
            generation=1,
        )
        workspace = tmp_path / "ws"
        workspace.mkdir(parents=True, exist_ok=True)
        evidence = await _projector(service).project(
            workspace,
            checkpoint_ref="artifact://checkpoint",
            workflow_id=WORKFLOW_ID,
            runtime_uid=os.getuid(),
            runtime_gid=os.getgid(),
            source=source,
            grant=grant,
            target_owner=f"{WORKFLOW_ID}:step-1",
        )
        assert (workspace / "shared.txt").read_bytes() == b"shared work"
        assert evidence["checkpointRestore"]["sourceDigest"] == _sha(payload)

    @pytest.mark.asyncio
    async def test_artifact_kind_source_imports(self, tmp_path):
        from moonmind.omnigent.workspace_sources import compile_workspace_source

        service = FakeArtifactService()
        payload = _tar_bytes({"imported.txt": b"immutable import"})
        service.add("snapshot", payload)
        source = compile_workspace_source(
            {
                "workspaceSource": {
                    "kind": "artifact",
                    "artifactRef": "artifact://snapshot",
                    "expectedDigest": _sha(payload),
                }
            }
        )
        assert source.kind == "artifact"
        workspace = tmp_path / "ws"
        workspace.mkdir(parents=True, exist_ok=True)
        evidence = await _projector(service).project(
            workspace,
            workflow_id=WORKFLOW_ID,
            runtime_uid=os.getuid(),
            runtime_gid=os.getgid(),
            source=source,
            target_owner=f"{WORKFLOW_ID}:step-1",
        )
        assert (workspace / "imported.txt").read_bytes() == b"immutable import"
        assert (
            evidence["checkpointRestore"]["restoreContract"]
            == "moonmind.artifact-import.v1"
        )
