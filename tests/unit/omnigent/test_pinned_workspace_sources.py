"""Typed repository pins survive source compilation and controller saves."""
from copy import deepcopy

import pytest

from moonmind.omnigent import workspace_sources as sources

PIN = "1234567890abcdef1234567890abcdef12345678"


def pinned_spec():
    return {"workspaceSource": {"kind": "repository", "repositoryTarget": {
        "provider": "git", "connectionRef": "source-connection",
        "repository": {"name": "owner/repo"}, "branch": {"name": "main"},
        "revision": {"kind": "git_commit", "commitSha": PIN},
    }}}


def test_compile_binds_pin_to_readiness_identity():
    spec = pinned_spec()
    compiled = sources.compile_workspace_source(spec)
    assert compiled.repository_commit_sha == PIN
    fingerprint = compiled.readiness_fingerprint(target_owner_workflow_id="owner", attempt_id="attempt")
    assert fingerprint["repositoryCommitSha"] == PIN
    del spec["workspaceSource"]["repositoryTarget"]["revision"]
    unpinned = sources.compile_workspace_source(spec)
    assert "repositoryCommitSha" not in unpinned.readiness_fingerprint(target_owner_workflow_id="owner", attempt_id="attempt")


def test_controller_save_preserves_pin_as_checkpoint_provenance():
    spec = pinned_spec()
    untouched = deepcopy(spec)
    with pytest.raises(sources.WorkspaceSourceError):
        sources.replace_repository_workspace_source(spec, "artifact://save")
    replacement = sources.replace_repository_workspace_source(spec, "artifact://save", allow_pinned=True)
    compiled = sources.compile_workspace_source(replacement)
    assert compiled.kind == "checkpoint"
    assert compiled.repository_commit_sha == PIN
    assert compiled.checkpoint_ref == "artifact://save"
    assert spec == untouched


@pytest.mark.parametrize("revision", [
    {"kind": "git_commit", "commitSha": "1234567"},
    {"kind": "branch", "commitSha": PIN},
    {"kind": "git_commit", "commitSha": "g" * 40},
])
def test_revision_parser_rejects_unsupported_forms(revision):
    spec = pinned_spec()
    spec["workspaceSource"]["repositoryTarget"]["revision"] = revision
    with pytest.raises(sources.WorkspaceSourceError):
        sources.repository_workspace_commit_sha(spec)


def test_revision_alias_conflict_is_not_erased():
    spec = pinned_spec()
    spec["repositoryTarget"] = deepcopy(spec["workspaceSource"]["repositoryTarget"])
    spec["repositoryTarget"]["revision"]["commitSha"] = "b" * 40
    with pytest.raises(sources.WorkspaceSourceError):
        sources.repository_workspace_commit_sha(spec)


def test_clone_transports_pin_as_data_and_verifies_exact_head():
    from moonmind.omnigent.host_services.workspace import build_daemon_git_clone_argv
    argv = build_daemon_git_clone_argv(
        volume="agent_workspaces", target_in_volume="owned/repo",
        source="https://github.com/owner/repo.git", branch="main",
        image="git-image", git_user_name="Test", git_user_email="test@example.test",
        commit_sha=PIN,
    )
    script = argv[argv.index("-ceu") + 1]
    assert argv[-1] == PIN
    assert PIN not in script
    assert 'fetch --no-tags -- "$2" "$6"' in script
    assert 'checkout --detach "$6"' in script
    assert 'rev-parse HEAD' in script


@pytest.mark.asyncio
async def test_pin_verification_is_read_only_and_rejects_wrong_head(tmp_path):
    import asyncio
    import os
    import subprocess

    from moonmind.omnigent.harness_platform.failures import HarnessPlatformError
    from moonmind.omnigent.host_services.workspace import OmnigentWorkspaceMaterializer

    candidate = tmp_path / "candidate"
    subprocess.run(["git", "init", "-q", str(candidate)], check=True)
    subprocess.run(["git", "-C", str(candidate), "-c", "user.name=Fixture",
                    "-c", "user.email=fixture@example.test", "commit", "--allow-empty", "-qm", "Initial"], check=True)
    head = subprocess.check_output(["git", "-C", str(candidate), "rev-parse", "HEAD"], text=True).strip()
    (candidate / "later-work.txt").write_text("Must survive a refused checkout.\n")

    async def daemon(argv, input_bytes=None):
        assert input_bytes is None
        assert argv[argv.index("--network") + 1] == "none"
        assert "readonly" in argv[argv.index("--mount") + 1]
        script = argv[argv.index("-ceu") + 1]
        result = await asyncio.to_thread(subprocess.run, ["sh", "-ceu", script, "--", str(candidate), argv[-1]], capture_output=True, text=True)
        return result.returncode, result.stdout, result.stderr

    materializer = OmnigentWorkspaceMaterializer(
        command_runner=daemon, workspace_root=tmp_path, workspace_volume="agent_workspaces",
    )
    await materializer._verify_initial_repository_pin(candidate, head, runtime_uid=os.getuid(), runtime_gid=os.getgid())
    with pytest.raises(HarnessPlatformError, match="HEAD does not match"):
        await materializer._verify_initial_repository_pin(candidate, PIN, runtime_uid=os.getuid(), runtime_gid=os.getgid())
    assert (candidate / "later-work.txt").read_text() == "Must survive a refused checkout.\n"
    assert subprocess.check_output(["git", "-C", str(candidate), "rev-parse", "HEAD"], text=True).strip() == head


def test_api_preserves_existing_abbreviated_commit_for_native_owner():
    from api_service.api.routers.executions import _admit_workspace_source
    from moonmind.workflows.executions.repository_contract import compile_repository_target

    target = pinned_spec()["workspaceSource"]["repositoryTarget"]
    target["revision"]["commitSha"] = "abcdef012345"
    assert compile_repository_target(target).revision.commit_sha == "abcdef012345"
    workspace, repository, kind = _admit_workspace_source(
        {"workspaceSource": {"kind": "repository"}}, repository_payload=target,
        repository="owner/repo", workflow_id="native-retained-pin",
    )
    assert repository == "owner/repo"
    assert kind == "repository"
    assert target["revision"]["commitSha"] == "abcdef012345"
    retained = {**workspace, "repositoryTarget": target}
    assert sources.compile_workspace_source(retained).repository_commit_sha is None
    with pytest.raises(sources.WorkspaceSourceError):
        sources.repository_workspace_commit_sha(retained)


@pytest.mark.parametrize("existing", ["absent", "empty-directory", "file", "symlink"])
def test_pin_promotion_never_replaces_a_destination(tmp_path, existing):
    from moonmind.omnigent.harness_platform.failures import HarnessPlatformError
    from moonmind.omnigent.host_services.workspace import _promote_absent_pinned_checkout

    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "input").write_text("verified pin")
    destination = tmp_path / "candidate"
    if existing == "empty-directory":
        destination.mkdir()
    elif existing == "file":
        destination.write_text("existing work")
    elif existing == "symlink":
        destination.symlink_to(tmp_path / "missing")
    if existing == "absent":
        _promote_absent_pinned_checkout(stage, destination)
        assert (destination / "input").read_text() == "verified pin"
        assert not stage.exists()
    else:
        with pytest.raises(HarnessPlatformError):
            _promote_absent_pinned_checkout(stage, destination)
        assert (stage / "input").read_text() == "verified pin"
        if existing == "empty-directory":
            assert list(destination.iterdir()) == []
        elif existing == "file":
            assert destination.read_text() == "existing work"
        else:
            assert destination.is_symlink()


def test_initial_pinned_branch_never_selects_newer_existing_branch_tip(tmp_path):
    import subprocess
    from moonmind.omnigent.host_services.workspace import build_daemon_workspace_branch_argv

    def git(*args):
        return subprocess.check_output(["git", "-C", str(tmp_path), *args], text=True).strip()
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
    git("config", "user.name", "Fixture")
    git("config", "user.email", "fixture@example.test")
    git("commit", "--allow-empty", "-qm", "Initial")
    pin = git("rev-parse", "HEAD")
    git("commit", "--allow-empty", "-qm", "Newer")
    git("checkout", "--detach", pin)
    argv = build_daemon_workspace_branch_argv(
        volume="agent_workspaces", target_in_volume="owned/repo", target_branch="main",
        runtime_uid=1000, runtime_gid=1000, image="git-image", allow_initialization=True,
        initial_commit_sha=pin,
    )
    script = argv[argv.index("-ceu") + 1]
    subprocess.run(["sh", "-ceu", script, "--", str(tmp_path), "main", "initialize", pin], check=True)
    assert git("rev-parse", "HEAD") == pin
    assert git("symbolic-ref", "--short", "HEAD") == "main"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["transient-once", "transient-exhausted", "permanent", "partial-clone"])
@pytest.mark.parametrize("branch", ["main", ""])
@pytest.mark.parametrize("pin_at_tip", [False, True])
async def test_pinned_fetch_failure_uses_bounded_retry_and_original_diagnostic(
    tmp_path, monkeypatch, failure, branch, pin_at_tip,
):
    import os
    import shutil
    import subprocess
    import sys
    from pathlib import Path

    from moonmind.omnigent.host_services import workspace
    from moonmind.omnigent.harness_platform.failures import HarnessPlatformError

    remote = tmp_path / "remote"
    subprocess.run(["git", "init", "-qb", "main", str(remote)], check=True)
    for message in ("admitted pin", "later branch tip"):
        subprocess.run(["git", "-C", str(remote), "-c", "user.name=Fixture",
                        "-c", "user.email=fixture@example.test", "commit", "--allow-empty", "-qm", message], check=True)
    pin = subprocess.check_output(["git", "-C", str(remote), "rev-parse", "HEAD~1"], text=True).strip()
    original_tip = subprocess.check_output(["git", "-C", str(remote), "rev-parse", "HEAD"], text=True).strip()
    if pin_at_tip:
        pin = original_tip
    tools = tmp_path / "tools"
    tools.mkdir()
    calls = tmp_path / "git-calls"
    diagnostic = "fatal: Could not resolve host: controlled.invalid" if failure != "permanent" else "fatal: repository 'controlled' not found"
    git = tools / "git"
    git.write_text(
        f"#!{sys.executable}\nimport os,sys\nfrom pathlib import Path\n"
        f"calls=Path({str(calls)!r})\nargs=sys.argv[1:]\n"
        "operation='fetch' if 'fetch' in args else 'clone' if 'clone' in args else 'other'\n"
        "prior=calls.read_text().splitlines() if calls.exists() else []\n"
        "with calls.open('a') as f: f.write(operation+'\\n')\n"
        f"if operation=='clone' and {failure!r}=='partial-clone' and 'clone' not in prior:\n"
        " target=Path(args[-1]); (target/'.git').mkdir(parents=True); (target/'.git/HEAD').write_text('ref: refs/heads/main\\n')\n"
        f" print({diagnostic!r},file=sys.stderr); sys.exit(128)\n"
        f"if operation=='fetch' and {failure!r}!='partial-clone' and ({failure!r}!='transient-once' or 'fetch' not in prior):\n"
        f" print({diagnostic!r},file=sys.stderr); sys.exit(128)\n"
        f"os.execv({shutil.which('git')!r}, ['git',*args])\n"
    )
    git.chmod(0o755)
    delays = []
    handoffs = []

    async def sleep(delay):
        delays.append(delay)

    async def daemon(argv, input_bytes=None):
        if '/bin/chown' in argv:
            handoffs.append(argv)
            return 0, '', ''
        args = list(argv[argv.index('--') + 1:])
        args[1:3] = [str(remote), str(tmp_path / 'candidate')]
        result = subprocess.run(
            ['/bin/sh', '-ceu', argv[argv.index('-ceu') + 1], '--', *args],
            input='', capture_output=True, text=True,
            env={**os.environ, 'PATH': f'{tools}{os.pathsep}{os.environ["PATH"]}'},
        )
        return result.returncode, result.stdout, result.stderr

    monkeypatch.setattr(workspace.asyncio, 'sleep', sleep)
    monkeypatch.setattr(workspace, 'resolve_git_identity', lambda: ('Fixture', 'fixture@example.test'))
    materializer = workspace.OmnigentWorkspaceMaterializer(
        command_runner=daemon, workspace_root=tmp_path, workspace_volume='agent_workspaces',
    )
    kwargs = dict(rel=Path('candidate'), source='https://github.com/owner/repo.git',
                  branch=branch, token=b'', runtime_uid=os.getuid(), runtime_gid=os.getgid(), commit_sha=pin)
    if failure in {'transient-once', 'partial-clone'}:
        await materializer._clone_into_volume(**kwargs)
        current = subprocess.check_output(['git', '-C', str(tmp_path / 'candidate'), 'rev-parse', 'HEAD'], text=True).strip()
        assert current == pin
        assert delays == [2]
        assert len(handoffs) == 1
        expected_fetches = 1 if failure == "partial-clone" else 2
    else:
        with pytest.raises(HarnessPlatformError, match=diagnostic):
            await materializer._clone_into_volume(**kwargs)
        assert not handoffs
        assert delays == ([2, 4, 8] if failure == 'transient-exhausted' else [])
        expected_fetches = 4 if failure == 'transient-exhausted' else 1
    recorded = calls.read_text().splitlines()
    assert recorded.count('clone') == (2 if failure == 'partial-clone' else 1)
    assert recorded.count('fetch') == expected_fetches
    assert (original_tip == pin) is pin_at_tip


def test_resuming_clone_requires_admitted_pin():
    from moonmind.omnigent.harness_platform.failures import HarnessPlatformError
    from moonmind.omnigent.host_services.workspace import build_daemon_git_clone_argv

    with pytest.raises(HarnessPlatformError, match="resuming checkout requires an admitted repository pin"):
        build_daemon_git_clone_argv(
            volume="agent_workspaces", target_in_volume="owned/repo",
            source="https://github.com/owner/repo.git", branch="main",
            image="git-image", git_user_name="Test", git_user_email="test@example.test",
            resume_pinned_checkout=True,
        )
