"""Generic Omnigent workspace materialization rules."""

from __future__ import annotations

import hashlib
import io
import os
import stat
import tarfile
from types import SimpleNamespace

import pytest

from moonmind.omnigent.harness_platform.failures import HarnessPlatformError
from moonmind.omnigent.host_services.workspace import (
    OmnigentWorkspaceMaterializer,
    build_daemon_git_clone_argv,
    build_daemon_workspace_chown_argv,
    normalize_github_clone_source,
)
from moonmind.omnigent.workspace_artifacts import WorkspaceArtifactProjector
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
        input_refs=[],
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
        source="https://github.com/org/repo.git",
        branch="feature/branch",
        image="alpine/git:v2.43.0",
    )
    assert argv[:4] == ["docker", "run", "--rm", "-i"]
    assert "agent_workspaces:/work" in argv
    assert argv[-3:] == [
        "feature/branch",
        "https://github.com/org/repo.git",
        "/work/ws-1/repo",
    ]
    assert "alpine/git:v2.43.0" in argv
    joined = " ".join(argv)
    assert " clone " in joined and "--single-branch" in joined
    assert 'git check-ref-format --branch "$1"' in joined
    assert "--entrypoint" in argv
    assert all("x-access-token:tok" not in part for part in argv)


@pytest.mark.parametrize(
    "branch",
    ["feature/foo!bar", "feature/foo=bar", "développement"],
)
def test_daemon_git_clone_argv_preserves_git_valid_branch_names(branch: str):
    argv = build_daemon_git_clone_argv(
        volume="agent_workspaces",
        target_in_volume="ws-1/repo",
        source="https://github.com/org/repo.git",
        branch=branch,
        image="alpine/git:v2.43.0",
    )

    assert argv[-3] == branch


def test_daemon_git_clone_argv_rejects_credentialed_source():
    with pytest.raises(HarnessPlatformError, match="clone source"):
        build_daemon_git_clone_argv(
            volume="agent_workspaces",
            target_in_volume="ws-1/repo",
            source="https://x-access-token:fixture@github.com/org/repo.git",
            branch="feature/branch",
            image="alpine/git:v2.43.0",
        )


def test_daemon_workspace_chown_argv_pins_target_and_runtime_owner():
    argv = build_daemon_workspace_chown_argv(
        volume="agent_workspaces",
        target_in_volume="temporal_sandbox/ws-1/repo",
        runtime_uid=1000,
        runtime_gid=1000,
        image="alpine/git:v2.43.0",
    )

    assert argv == [
        "docker",
        "run",
        "--rm",
        "-v",
        "agent_workspaces:/work",
        "--entrypoint",
        "/bin/chown",
        "alpine/git:v2.43.0",
        "-R",
        "--",
        "1000:1000",
        "/work/temporal_sandbox/ws-1/repo",
    ]


@pytest.mark.asyncio
async def test_materializer_clones_missing_sandbox_workspace_via_daemon(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    """A fresh sandbox locator materializes by cloning the requested branch."""

    captured: list[tuple[list[str], bytes | None]] = []

    async def runner(argv, input_bytes=None):
        captured.append((argv, input_bytes))
        if input_bytes is None:
            return 0, "", ""
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
    assert len(captured) == 2
    argv, clone_input = captured[0]
    assert argv[0] == "docker"
    assert f"{materializer._workspace_volume}:/work" in argv
    assert "/work/temporal_sandbox/" + workspace_id + "/repo" in argv
    joined = " ".join(argv)
    assert "https://x-access-token:" not in joined
    assert "https://github.com/MoonLadderStudios/MoonMind.git" in joined
    assert clone_input == b"tokeeeeeeeeee"
    assert "dependabot/npm_and_yarn/multi-2181bdc769" in joined
    assert captured[1][0][-2:] == [
        "1000:1000",
        "/work/temporal_sandbox/" + workspace_id + "/repo",
    ]
    assert captured[1][1] is None
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
async def test_materializer_projects_checkpoint_and_declared_inputs_before_mount(
    tmp_path,
):
    """The generic host must launch from the same workspace authority as Codex."""

    workspace_id = _workspace_id()
    existing = tmp_path / "temporal_sandbox" / workspace_id / "repo"
    (existing / ".git" / "info").mkdir(parents=True)
    (existing / "tracked.txt").write_text("base", encoding="utf-8")

    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w:gz") as bundle:
        payload = b"implementation checkpoint\n"
        member = tarfile.TarInfo("tracked.txt")
        member.size = len(payload)
        bundle.addfile(member, io.BytesIO(payload))
        added = b"new candidate file\n"
        member = tarfile.TarInfo("candidate.txt")
        member.size = len(added)
        bundle.addfile(member, io.BytesIO(added))
        stale = b"prior verifier context"
        member = tarfile.TarInfo(".moonmind/attachments/stale-verifier")
        member.size = len(stale)
        bundle.addfile(member, io.BytesIO(stale))
        stale_restore = b"stale restore state"
        member = tarfile.TarInfo(".moonmind/restore/stale-restore")
        member.size = len(stale_restore)
        bundle.addfile(member, io.BytesIO(stale_restore))

    checkpoint_ref = "artifact://checkpoint"
    restore_ref = "artifact://restore"
    attachment_ref = "artifact://assessment"

    class ArtifactService:
        def __init__(self) -> None:
            self.reads: list[tuple[str, str]] = []
            self.payloads = {
                "checkpoint": archive.getvalue(),
                "restore": b"restore state",
                "assessment": b'{"verdict":"PARTIALLY_IMPLEMENTED"}',
            }

        async def get_metadata(self, *, artifact_id, principal):
            self.reads.append((artifact_id, principal))
            artifact = SimpleNamespace(size_bytes=len(self.payloads[artifact_id]))
            links = [SimpleNamespace(workflow_id="workflow-1")]
            return artifact, links

        async def read_chunks(
            self, *, artifact_id, principal, allow_restricted_raw, chunk_size
        ):
            assert allow_restricted_raw is True
            self.reads.append((artifact_id, principal))
            return SimpleNamespace(), iter((self.payloads[artifact_id],))

    service = ArtifactService()

    async def fail_runner(*_args, **_kwargs):  # pragma: no cover - must not run
        raise AssertionError("existing authoritative checkout must not be cloned")

    request = _request(
        {
            "workspaceLocator": {
                "kind": "sandbox",
                "workspaceId": workspace_id,
                "relativePath": "repo",
            },
            "repository": "MoonLadderStudios/MoonMind",
            "branch": "main",
            "workspaceCheckpointRestoreRef": checkpoint_ref,
            "restoreInputRefs": [restore_ref],
        }
    )
    request.input_refs = [attachment_ref]

    await OmnigentWorkspaceMaterializer(
        command_runner=fail_runner,
        workspace_root=tmp_path,
        artifact_service=service,
    ).materialize(
        request,
        runtime_uid=os.getuid(),
        runtime_gid=os.getgid(),
    )

    assert (existing / "tracked.txt").read_text() == "implementation checkpoint\n"
    assert (existing / "candidate.txt").read_text() == "new candidate file\n"
    restore_path = existing / ".moonmind" / "restore" / hashlib.sha256(
        restore_ref.encode()
    ).hexdigest()[:24]
    attachment_path = existing / ".moonmind" / "attachments" / hashlib.sha256(
        attachment_ref.encode()
    ).hexdigest()[:24]
    assert restore_path.read_bytes() == b"restore state"
    assert attachment_path.read_bytes() == b'{"verdict":"PARTIALLY_IMPLEMENTED"}'
    assert not (existing / ".moonmind" / "attachments" / "stale-verifier").exists()
    assert not (existing / ".moonmind" / "restore" / "stale-restore").exists()
    assert stat.S_IMODE(restore_path.stat().st_mode) == 0o400
    assert stat.S_IMODE(attachment_path.stat().st_mode) == 0o400
    assert restore_path.stat().st_uid == os.getuid()
    assert restore_path.stat().st_gid == os.getgid()
    assert attachment_path.stat().st_uid == os.getuid()
    assert attachment_path.stat().st_gid == os.getgid()
    assert "/.moonmind/attachments/" in (
        existing / ".git" / "info" / "exclude"
    ).read_text()
    assert SandboxWorkspaceRecordStore(tmp_path).is_materialized(workspace_id)


def test_runtime_input_ownership_handoff_targets_selected_identity(
    tmp_path, monkeypatch
):
    target = tmp_path / "restore-input"
    target.write_bytes(b"restore state")
    selected_uid = os.getuid() + 1
    selected_gid = os.getgid() + 1
    chown_calls: list[tuple[object, int, int, bool]] = []

    def record_chown(path, uid, gid, *, follow_symlinks):
        chown_calls.append((path, uid, gid, follow_symlinks))

    monkeypatch.setattr(os, "chown", record_chown)

    WorkspaceArtifactProjector._make_runtime_readable(
        target,
        runtime_uid=selected_uid,
        runtime_gid=selected_gid,
        noun="restore inputs",
    )

    assert chown_calls == [(target, selected_uid, selected_gid, False)]
    assert stat.S_IMODE(target.stat().st_mode) == 0o400


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

    async def failing_runner(argv, input_bytes=None):
        calls.append(argv)
        assert input_bytes == b"toktoktoktoktok"
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
