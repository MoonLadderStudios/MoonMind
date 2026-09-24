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
        git_user_name="MoonMind Worker",
        git_user_email="moonmind-worker@users.noreply.github.com",
    )
    assert argv[:4] == ["docker", "run", "--rm", "-i"]
    assert "agent_workspaces:/work" in argv
    assert argv[-5:] == [
        "feature/branch",
        "https://github.com/org/repo.git",
        "/work/ws-1/repo",
        "MoonMind Worker",
        "moonmind-worker@users.noreply.github.com",
    ]
    assert "alpine/git:v2.43.0" in argv
    joined = " ".join(argv)
    assert " clone " in joined and "--single-branch" in joined
    assert 'git check-ref-format --branch "$1"' in joined
    assert "--entrypoint" in argv
    assert all("x-access-token:tok" not in part for part in argv)


def test_daemon_git_clone_argv_configures_repo_local_commit_identity():
    """The clone leaves behind a repository able to author a commit.

    The shared host image carries a credential helper and no ``[user]``
    section, so a repository-local identity written at clone time is what
    lets a commit-capable skill commit instead of stopping as blocked.
    """

    argv = build_daemon_git_clone_argv(
        volume="agent_workspaces",
        target_in_volume="ws-1/repo",
        source="https://github.com/org/repo.git",
        branch="feature/branch",
        image="alpine/git:v2.43.0",
        git_user_name="MoonMind Worker",
        git_user_email="moonmind-worker@users.noreply.github.com",
    )

    script = argv[argv.index("-ceu") + 1]
    assert 'git -C "$3" config --local user.name "$4"' in script
    assert 'git -C "$3" config --local user.email "$5"' in script
    # Identity is data, never interpolated into the executed script.
    assert "MoonMind Worker" not in script


@pytest.mark.parametrize(
    ("name", "email"),
    [("", "worker@example.test"), ("MoonMind Worker", "   ")],
)
def test_daemon_git_clone_argv_rejects_incomplete_commit_identity(
    name: str, email: str
):
    with pytest.raises(HarnessPlatformError, match="commit identity"):
        build_daemon_git_clone_argv(
            volume="agent_workspaces",
            target_in_volume="ws-1/repo",
            source="https://github.com/org/repo.git",
            branch="feature/branch",
            image="alpine/git:v2.43.0",
            git_user_name=name,
            git_user_email=email,
        )


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
        git_user_name="MoonMind Worker",
        git_user_email="moonmind-worker@users.noreply.github.com",
    )

    assert argv[-5] == branch


def test_daemon_git_clone_argv_rejects_credentialed_source():
    with pytest.raises(HarnessPlatformError, match="clone source"):
        build_daemon_git_clone_argv(
            volume="agent_workspaces",
            target_in_volume="ws-1/repo",
            source="https://x-access-token:fixture@github.com/org/repo.git",
            branch="feature/branch",
            image="alpine/git:v2.43.0",
            git_user_name="MoonMind Worker",
            git_user_email="moonmind-worker@users.noreply.github.com",
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
        target = argv[-3]
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
@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        (
            ("Deployment Operator", "operator@example.test"),
            ("Deployment Operator", "operator@example.test"),
        ),
        (
            (None, None),
            ("MoonMind Worker", "moonmind-worker@users.noreply.github.com"),
        ),
    ],
)
async def test_materializer_provisions_commit_identity_for_the_clone(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    configured: tuple[str | None, str | None],
    expected: tuple[str, str],
):
    """A materialized repository can author a commit without operator setup.

    Agent-owned publication skills commit inside the host, where no global
    identity exists. An undeclared identity resolves to the documented
    default rather than blocking the run.
    """

    from moonmind.config.settings import settings

    monkeypatch.setattr(settings.workflow, "git_user_name", configured[0])
    monkeypatch.setattr(settings.workflow, "git_user_email", configured[1])

    captured: list[list[str]] = []

    async def runner(argv, input_bytes=None):
        captured.append(argv)
        if input_bytes is None:
            return 0, "", ""
        import pathlib

        local = tmp_path / pathlib.Path(argv[-3].removeprefix("/work/"))
        local.mkdir(parents=True, exist_ok=True)
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
    await materializer.materialize(
        _request(
            {
                "workspaceLocator": {
                    "kind": "sandbox",
                    "workspaceId": _workspace_id(),
                    "relativePath": "repo",
                },
                "repository": "MoonLadderStudios/MoonMind",
                "branch": "codex/automated-verification-handoffs",
            }
        )
    )

    clone_argv = captured[0]
    assert clone_argv[-2:] == list(expected)
    script = clone_argv[clone_argv.index("-ceu") + 1]
    assert 'config --local user.name "$4"' in script
    assert 'config --local user.email "$5"' in script


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
async def test_materializer_reapplies_commit_identity_to_existing_workspace(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    """An existing workspace keeps a usable commit identity without a clone.

    Retries reuse the attempt workspace, so clone-time identity never runs
    for them. Materialization reapplies the resolved identity to the final
    writable Git workspace without discarding preserved work.
    """

    from moonmind.config.settings import settings

    monkeypatch.setattr(settings.workflow, "git_user_name", "Deployment Operator")
    monkeypatch.setattr(settings.workflow, "git_user_email", "operator@example.test")

    workspace_id = _workspace_id()
    existing = tmp_path / "temporal_sandbox" / workspace_id / "repo"
    (existing / ".git").mkdir(parents=True)
    (existing / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (existing / ".git" / "config").write_text(
        '[core]\n\trepositoryformatversion = 0\n', encoding="utf-8"
    )
    (existing / "KEEP").write_text("x", encoding="utf-8")

    async def fail_runner(argv):  # pragma: no cover - must not run
        raise AssertionError("clone must not run for an existing workspace")

    await OmnigentWorkspaceMaterializer(
        command_runner=fail_runner, workspace_root=tmp_path
    ).materialize(
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
        ),
        runtime_uid=os.getuid(),
        runtime_gid=os.getgid(),
    )

    assert (existing / "KEEP").read_text() == "x"
    config = (existing / ".git" / "config").read_text(encoding="utf-8")
    assert "repositoryformatversion = 0" in config
    assert "Deployment Operator" in config
    assert "operator@example.test" in config


@pytest.mark.asyncio
async def test_materializer_reapplies_commit_identity_after_checkpoint_restore(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    """A checkpoint restore cannot leave a stale or missing commit identity.

    An authoritative restore replaces the cloned ``.git/config`` and an
    additive restore can overwrite it with the imported config, so the
    resolved identity is reapplied after projection while restored work
    is preserved.
    """

    from moonmind.config.settings import settings

    monkeypatch.setattr(settings.workflow, "git_user_name", "Deployment Operator")
    monkeypatch.setattr(settings.workflow, "git_user_email", "operator@example.test")

    workspace_id = _workspace_id()
    existing = tmp_path / "temporal_sandbox" / workspace_id / "repo"
    (existing / ".git").mkdir(parents=True)
    (existing / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (existing / ".git" / "config").write_text(
        '[core]\n\trepositoryformatversion = 0\n', encoding="utf-8"
    )
    (existing / "KEEP").write_text("x", encoding="utf-8")

    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w:gz") as bundle:
        head = b"ref: refs/heads/main\n"
        member = tarfile.TarInfo(".git/HEAD")
        member.size = len(head)
        bundle.addfile(member, io.BytesIO(head))
        stale_config = (
            b'[core]\n\trepositoryformatversion = 0\n'
            b'[user]\n\tname = Stale Import\n\temail = stale@example.test\n'
        )
        member = tarfile.TarInfo(".git/config")
        member.size = len(stale_config)
        bundle.addfile(member, io.BytesIO(stale_config))
        payload = b"implementation checkpoint\n"
        member = tarfile.TarInfo("tracked.txt")
        member.size = len(payload)
        bundle.addfile(member, io.BytesIO(payload))

    class Artifacts:
        async def get_metadata(self, **_kwargs):
            return SimpleNamespace(size_bytes=len(archive.getvalue())), []

        async def read_chunks(self, **_kwargs):
            return SimpleNamespace(), iter((archive.getvalue(),))

    async def fail_runner(*_args, **_kwargs):  # pragma: no cover - must not run
        raise AssertionError("existing authoritative checkout must not be cloned")

    await OmnigentWorkspaceMaterializer(
        command_runner=fail_runner,
        workspace_root=tmp_path,
        artifact_service=Artifacts(),
    ).materialize(
        _request(
            {
                "workspaceLocator": {
                    "kind": "sandbox",
                    "workspaceId": workspace_id,
                    "relativePath": "repo",
                },
                "repository": "MoonLadderStudios/MoonMind",
                "branch": "main",
                "workspaceCheckpointRestoreRef": "artifact://checkpoint",
            }
        ),
        runtime_uid=os.getuid(),
        runtime_gid=os.getgid(),
    )

    assert (existing / "tracked.txt").read_text() == "implementation checkpoint\n"
    config = (existing / ".git" / "config").read_text(encoding="utf-8")
    assert "Stale Import" not in config
    assert "stale@example.test" not in config
    assert "Deployment Operator" in config
    assert "operator@example.test" in config


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


@pytest.mark.asyncio
async def test_restored_publication_directory_uses_selected_runtime_owner(
    tmp_path,
    monkeypatch,
):
    """Replay mm:f23c3090: restored verifier output blocked the PR artifact."""
    workspace_id = _workspace_id()
    existing = tmp_path / "temporal_sandbox" / workspace_id / "repo"
    existing.mkdir(parents=True)
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w:gz") as bundle:
        member = tarfile.TarInfo("artifacts/github-issue-implement-verify.json")
        payload = b'{"verdict":"FULLY_IMPLEMENTED"}'
        member.size = len(payload)
        bundle.addfile(member, io.BytesIO(payload))

    class Artifacts:
        async def get_metadata(self, **_kwargs):
            return SimpleNamespace(size_bytes=len(archive.getvalue())), []

        async def read_chunks(self, **_kwargs):
            return SimpleNamespace(), iter((archive.getvalue(),))

    selected_uid, selected_gid = os.getuid() + 1, os.getgid() + 1
    assigned = []
    monkeypatch.setattr(
        os,
        "chown",
        lambda path, uid, gid, **kwargs: assigned.append((path, uid, gid, kwargs)),
    )

    await OmnigentWorkspaceMaterializer(
        command_runner=None,
        workspace_root=tmp_path,
        artifact_service=Artifacts(),
    ).materialize(
        _request(
            {
                "workspaceLocator": {
                    "kind": "sandbox",
                    "workspaceId": workspace_id,
                    "relativePath": "repo",
                },
                "workspaceCheckpointRestoreRef": "artifact://verifier-checkpoint",
            }
        ),
        runtime_uid=selected_uid,
        runtime_gid=selected_gid,
    )
    expected = {
        existing / "artifacts",
        existing / "artifacts/github-issue-implement-verify.json",
    }
    assert {
        path
        for path, uid, gid, options in assigned
        if (uid, gid) == (selected_uid, selected_gid)
        and options == {"follow_symlinks": False}
    } == expected
    assert (
        existing / "artifacts/github-issue-implement-verify.json"
    ).read_bytes() == payload


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

    # Raw workspacePath aliases are rejected as a normal authoring route
    # (MoonLadderStudios/MoonMind#4014); an existing workspace needs a
    # server-issued ownership/use grant instead of a bare path.
    with pytest.raises(HarnessPlatformError, match="workspacePath"):
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


@pytest.mark.asyncio
async def test_materializer_retries_transient_clone_dns_failure(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    """A transient DNS blip during clone retries instead of failing the run.

    Regression for mm:68d074f1-...-2026-09-22T00:00:00Z, which failed with
    ``Could not resolve host: github.com`` on its first clone attempt.
    """
    import asyncio

    _real_sleep = asyncio.sleep

    async def _no_sleep(_delay: float) -> None:
        await _real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)

    materializer = OmnigentWorkspaceMaterializer(
        command_runner=None, workspace_root=tmp_path  # type: ignore[arg-type]
    )

    calls: list[list[str]] = []

    async def flaky_runner(argv, input_bytes=None):
        calls.append(argv)
        if input_bytes is not None:
            # First clone attempt hits the observed transient DNS failure.
            if len([c for c in calls if c[0] == "docker"]) == 1:
                return (
                    128,
                    "",
                    "Cloning into '/work/temporal_sandbox/abc/repo'...\n"
                    "fatal: unable to access "
                    "'https://github.com/MoonLadderStudios/MoonMind.git/': "
                    "Could not resolve host: github.com",
                )
            import pathlib

            local = tmp_path / pathlib.Path(argv[-3].removeprefix("/work/"))
            local.mkdir(parents=True, exist_ok=True)
            (local / "README.md").write_text("cloned", encoding="utf-8")
            return 0, "", ""
        return 0, "", ""

    object.__setattr__(materializer, "_runner", flaky_runner)

    async def fake_token(*args, **kwargs):
        return "tok" * 5

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.managed_api_key_resolve."
        "resolve_github_token_for_launch",
        fake_token,
    )
    workspace = await materializer.materialize(
        _request(
            {
                "workspaceLocator": {
                    "kind": "sandbox",
                    "workspaceId": _workspace_id(),
                    "relativePath": "repo",
                },
                "repository": "MoonLadderStudios/MoonMind",
                "branch": "main",
            }
        )
    )

    assert workspace["kind"] == "bind"
    # Two clone attempts plus the ownership handoff.
    assert len(calls) == 3
    assert (
        tmp_path / "temporal_sandbox" / _workspace_id() / "repo" / "README.md"
    ).exists()


@pytest.mark.asyncio
async def test_materializer_gives_up_after_repeated_transient_clone_failures(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    """Persistent DNS failures still fail closed with the original detail."""
    import asyncio

    _real_sleep = asyncio.sleep

    async def _no_sleep(_delay: float) -> None:
        await _real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)

    materializer = OmnigentWorkspaceMaterializer(
        command_runner=None, workspace_root=tmp_path  # type: ignore[arg-type]
    )

    calls: list[list[str]] = []

    async def always_dns_failure(argv, input_bytes=None):
        calls.append(argv)
        if input_bytes is not None:
            return (
                128,
                "",
                "Cloning into '/work/temporal_sandbox/abc/repo'...\n"
                "fatal: unable to access "
                "'https://github.com/MoonLadderStudios/MoonMind.git/': "
                "Could not resolve host: github.com",
            )
        return 0, "", ""

    object.__setattr__(materializer, "_runner", always_dns_failure)

    async def fake_token(*args, **kwargs):
        return "tok" * 5

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.managed_api_key_resolve."
        "resolve_github_token_for_launch",
        fake_token,
    )
    with pytest.raises(HarnessPlatformError, match="Could not resolve host"):
        await materializer.materialize(
            _request(
                {
                    "workspaceLocator": {
                        "kind": "sandbox",
                        "workspaceId": _workspace_id(),
                        "relativePath": "repo",
                    },
                    "repository": "MoonLadderStudios/MoonMind",
                    "branch": "main",
                }
            )
        )
    # Bounded retries: more than one attempt, but not unbounded.
    clone_attempts = len([c for c in calls if c[0] == "docker"])
    assert 2 <= clone_attempts <= 5


@pytest.mark.asyncio
async def test_materializer_reuses_overlapping_completed_checkout(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    """An overlapping materialization's success is reused, never deleted.

    If another invocation completes the checkout during this attempt's
    backoff, cleanup must not recursively delete that valid checkout.
    """
    import asyncio
    import pathlib

    _real_sleep = asyncio.sleep

    async def _no_sleep(_delay: float) -> None:
        await _real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)

    materializer = OmnigentWorkspaceMaterializer(
        command_runner=None, workspace_root=tmp_path  # type: ignore[arg-type]
    )

    calls: list[list[str]] = []

    async def racing_runner(argv, input_bytes=None):
        calls.append(argv)
        if input_bytes is not None:
            target = tmp_path / pathlib.Path(argv[-3].removeprefix("/work/"))
            if len([c for c in calls if c[0] == "docker"]) == 1:
                # The overlapping invocation wins during our backoff: leave
                # a completed checkout behind, then report our own DNS error.
                (target / ".git").mkdir(parents=True, exist_ok=True)
                (target / ".git" / "HEAD").write_text(
                    "ref: refs/heads/main\n", encoding="utf-8"
                )
                (target / "README.md").write_text("theirs", encoding="utf-8")
                return (
                    128,
                    "",
                    "Cloning into '/work/temporal_sandbox/abc/repo'...\n"
                    "fatal: unable to access "
                    "'https://github.com/MoonLadderStudios/MoonMind.git/': "
                    "Could not resolve host: github.com",
                )
            raise AssertionError("completed checkout must be reused, not recloned")
        return 0, "", ""

    object.__setattr__(materializer, "_runner", racing_runner)

    async def fake_token(*args, **kwargs):
        return "tok" * 5

    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.managed_api_key_resolve."
        "resolve_github_token_for_launch",
        fake_token,
    )
    workspace = await materializer.materialize(
        _request(
            {
                "workspaceLocator": {
                    "kind": "sandbox",
                    "workspaceId": _workspace_id(),
                    "relativePath": "repo",
                },
                "repository": "MoonLadderStudios/MoonMind",
                "branch": "main",
            }
        ),
        runtime_uid=os.getuid(),
        runtime_gid=os.getgid(),
    )

    assert workspace["kind"] == "bind"
    # One clone attempt plus the ownership handoff; no second clone.
    assert len(calls) == 2
    kept = tmp_path / "temporal_sandbox" / _workspace_id() / "repo"
    assert (kept / "README.md").read_text(encoding="utf-8") == "theirs"
    assert (kept / ".git" / "HEAD").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('separate_remaining_work', [False, True])
async def test_historical_remediation_request_materializes_named_evidence_and_reuses_candidate(
    tmp_path, separate_remaining_work,
):
    """Replay the recurring request: gate refs existed only in parameters."""
    workspace = tmp_path / 'temporal_sandbox' / _workspace_id() / 'repo'
    (workspace / '.git' / 'info').mkdir(parents=True)
    (workspace / 'candidate.txt').write_text('uncommitted candidate')
    payloads = {
        'brief': b'issue brief',
        'gate': b'{"verdict":"ADDITIONAL_WORK_NEEDED","remainingWork":["fix"]}',
        'remaining': b'["fix"]',
    }

    class Artifacts:
        async def get_metadata(self, *, artifact_id, principal):
            assert principal == 'service:omnigent_workspace_attachment'
            return (SimpleNamespace(size_bytes=len(payloads[artifact_id])),
                    [SimpleNamespace(workflow_id='workflow-1')])

        async def read_chunks(self, *, artifact_id, **kwargs):
            return SimpleNamespace(), iter((payloads[artifact_id],))

    async def no_clone(*args, **kwargs):
        raise AssertionError('must preserve the existing candidate')

    request = _request({
        'workspaceLocator': {'kind': 'sandbox', 'workspaceId': _workspace_id(),
                             'relativePath': 'repo'},
        'repository': 'MoonLadderStudios/MoonMind', 'branch': 'main',
    })
    request.input_refs = ['artifact://brief']
    request.parameters = {
        'gateResultRef': 'artifact://gate',
        'remainingWorkRef': 'artifact://remaining' if separate_remaining_work else 'artifact://gate',
    }
    materializer = OmnigentWorkspaceMaterializer(
        command_runner=no_clone, workspace_root=tmp_path, artifact_service=Artifacts(),
    )
    # A historical ready workspace may contain only the issue brief. Admission
    # of the current evidence must not depend on replaying a new workflow patch.
    historical = _request(request.workspace_spec)
    historical.input_refs = request.input_refs
    await materializer.materialize(historical, runtime_uid=os.getuid(), runtime_gid=os.getgid())
    for _ in range(2):
        attachment = await materializer.materialize(request, runtime_uid=os.getuid(), runtime_gid=os.getgid())
        paths = attachment['materializedInputPaths']
        assert (workspace / paths['gateResultPath']).read_bytes() == payloads['gate']
        assert (workspace / paths['remainingWorkPath']).read_bytes() == payloads['remaining' if separate_remaining_work else 'gate']
        assert (paths['gateResultPath'] != paths['remainingWorkPath']) is separate_remaining_work
        assert (workspace / 'candidate.txt').read_text() == 'uncommitted candidate'
