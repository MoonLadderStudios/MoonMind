"""A published checkpoint must not exhaust its budget on unrelated refs."""

import subprocess

import pytest

from moonmind.workflows.temporal.runtime import checkpoint_history


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


@pytest.mark.parametrize("self_contained", [False, True])
@pytest.mark.parametrize("remote_tip", ["head", "descendant"])
def test_available_head_capture_has_a_bounded_git_execution_cost(
    tmp_path,
    monkeypatch,
    self_contained,
    remote_tip,
):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.name", "Fixture")
    git(repo, "config", "user.email", "fixture@example.invalid")
    (repo / "tracked.txt").write_text("retained checkpoint bytes\n")
    git(repo, "add", "tracked.txt")
    git(repo, "commit", "-qm", "base")
    tree = git(repo, "rev-parse", "HEAD^{tree}")
    for index in range(24):
        previous = git(repo, "rev-parse", "HEAD")
        git(repo, "update-ref", f"refs/remotes/origin/history-{index:02}", previous)
        commit = git(
            repo, "commit-tree", tree, "-p", previous, "-m", f"History {index}"
        )
        git(repo, "update-ref", "refs/heads/main", commit)
    head = git(repo, "rev-parse", "HEAD")
    if remote_tip == "head":
        # Deliberately last in ref order, as with long-lived workflow branches.
        git(repo, "update-ref", "refs/remotes/origin/zz-selected", head)
    else:
        descendant = git(repo, "commit-tree", tree, "-p", head, "-m", "Remote advance")
        git(repo, "update-ref", "refs/remotes/origin/aa-selected", descendant)
    original = checkpoint_history._git
    calls = []

    def bounded_git(workspace, arguments, **kwargs):
        calls.append(arguments)
        if len(calls) > 12:
            raise ValueError("fixture Git execution budget exhausted")
        return original(workspace, arguments, **kwargs)

    before = git(repo, "status", "--porcelain=v1")
    monkeypatch.setattr(checkpoint_history, "_git", bounded_git)
    payload, evidence = checkpoint_history.capture_git_history(
        repo,
        head,
        self_contained=self_contained,
    )

    assert evidence["headCommit"] == evidence["baselineCommit"] == head
    assert evidence["requiresSourceObjects"] is not self_contained
    if self_contained:
        assert payload.startswith(b"PACK")
        assert evidence["shallowCommits"] == [head]
    else:
        assert payload is None
    assert git(repo, "rev-parse", "HEAD") == head
    assert git(repo, "status", "--porcelain=v1") == before


def test_initial_local_source_exports_full_selected_ancestry(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.name", "Fixture")
    git(repo, "config", "user.email", "fixture@example.invalid")
    for number in range(3):
        (repo / "tracked.txt").write_text(f"revision {number}\n")
        git(repo, "add", "tracked.txt")
        git(repo, "commit", "-qm", f"revision {number}")
    head = git(repo, "rev-parse", "HEAD")
    git(repo, "update-ref", "refs/remotes/origin/main", head)
    payload, evidence = checkpoint_history.capture_git_history(
        repo,
        head,
        self_contained=True,
        full_ancestry=True,
    )
    restored = tmp_path / "restored"
    restored.mkdir()
    git(restored, "init", "-q")
    subprocess.run(
        ["git", "-C", str(restored), "index-pack", "--stdin"],
        input=payload,
        check=True,
        capture_output=True,
    )
    git(restored, "checkout", "-q", head)
    assert git(restored, "rev-list", "HEAD") == git(repo, "rev-list", "HEAD")
    assert evidence["requiresSourceObjects"] is False
    assert evidence["shallowCommits"] == []
    assert (restored / "tracked.txt").read_text() == "revision 2\n"


def test_initial_source_history_uses_explicit_isolated_git_environment(
    tmp_path, monkeypatch
):
    import os

    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.name", "Fixture")
    git(repo, "config", "user.email", "fixture@example.invalid")
    (repo / "a").write_text("safe\n")
    git(repo, "add", "a")
    git(repo, "commit", "-qm", "safe")
    head = git(repo, "rev-parse", "HEAD")
    poison = tmp_path / "poison"
    poison.write_text("[invalid Git config\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(poison))
    env = {
        "PATH": os.defpath,
        "HOME": str(tmp_path),
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_NO_REPLACE_OBJECTS": "1",
    }
    payload, evidence = checkpoint_history.capture_git_history(
        repo, head, self_contained=True, full_ancestry=True, env=env
    )
    assert payload.startswith(b"PACK")
    assert evidence["requiresSourceObjects"] is False


def test_initial_descriptor_retains_required_archive_and_manifest_dependencies():
    import json
    from moonmind.schemas.saved_work_models import saved_work_artifact_dependencies

    payload = {
        "kind": "initial_repository_source",
        "workspace": {
            "archiveRef": "artifact:art_archive",
            "archiveDigest": "sha256:" + "a" * 64,
            "manifestRef": "artifact:art_manifest",
            "manifestDigest": "sha256:" + "b" * 64,
        },
    }
    dependencies = saved_work_artifact_dependencies(
        "initial_repository_source", json.dumps(payload).encode()
    )
    assert {(d["artifact_id"], d["sha256"], d["required"]) for d in dependencies} == {
        ("art_archive", "a" * 64, True),
        ("art_manifest", "b" * 64, True),
    }


def test_isolated_git_timeout_stops_its_source_reading_children(tmp_path):
    import os
    import sys
    import time

    command = tmp_path / "git"
    sentinel = tmp_path / "late-source-read"
    command.write_text(
        f"#!{sys.executable}\nimport os,time\nif os.fork()==0:\n time.sleep(0.5)\n open({str(sentinel)!r},'w').write('read')\nelse:\n time.sleep(10)\n"
    )
    command.chmod(0o700)
    with pytest.raises(ValueError, match="budget"):
        checkpoint_history._git(
            tmp_path,
            ["clone"],
            env={"PATH": str(tmp_path)},
            deadline=time.monotonic() + 0.1,
        )
    time.sleep(0.6)
    assert not sentinel.exists()


def test_full_history_preserves_tracking_tip_after_head_reset(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.name", "Fixture")
    git(repo, "config", "user.email", "fixture@example.invalid")
    (repo / "a").write_text("first\n")
    git(repo, "add", "a")
    git(repo, "commit", "-qm", "first")
    old = git(repo, "rev-parse", "HEAD")
    (repo / "a").write_text("second\n")
    git(repo, "commit", "-qam", "second")
    tip = git(repo, "rev-parse", "HEAD")
    git(repo, "update-ref", "refs/remotes/origin/main", tip)
    git(repo, "reset", "--hard", old)
    payload, evidence = checkpoint_history.capture_git_history(
        repo, old, self_contained=True, full_ancestry=True, extra_heads=(tip,)
    )
    restored = tmp_path / "restored"
    restored.mkdir()
    git(restored, "init", "-q")
    subprocess.run(
        ["git", "-C", str(restored), "index-pack", "--stdin", "--strict"],
        input=payload,
        check=True,
        capture_output=True,
    )
    assert git(restored, "rev-list", tip).splitlines() == [tip, old]
    assert evidence["headCommit"] == old
    assert evidence["requiresSourceObjects"] is False


def test_successor_history_keeps_surviving_refs_without_resurrecting_deleted_ones(
    tmp_path,
):
    from moonmind.workflows.temporal.runtime.git_auth import (
        build_isolated_git_environment,
    )
    from moonmind.workflows.temporal.runtime.checkpoint_restore import (
        ManagedCheckpointRestoreService,
    )
    import os

    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.name", "Fixture")
    git(repo, "config", "user.email", "fixture@example.invalid")
    (repo / "a").write_text("first\n")
    git(repo, "add", "a")
    git(repo, "commit", "-qm", "first")
    old = git(repo, "rev-parse", "HEAD")
    (repo / "a").write_text("tip\n")
    git(repo, "commit", "-qam", "tip")
    tip = git(repo, "rev-parse", "HEAD")
    git(repo, "update-ref", "refs/remotes/origin/main", tip)
    git(repo, "checkout", "-qb", "work", old)
    git(repo, "branch", "-D", "main")
    env = build_isolated_git_environment(None, base_env=dict(os.environ))
    payload, history = checkpoint_history.capture_initial_source_successor_history(
        repo,
        old,
        admitted_refs=(
            "refs/heads/main",
            "refs/remotes/origin/main",
            "refs/remotes/origin/HEAD",
        ),
        high_security_mode=False,
        env=env,
    )
    assert set(history["localRefs"]) == {"refs/heads/work", "refs/remotes/origin/main"}
    restored = tmp_path / "restored"
    restored.mkdir()
    git(restored, "init", "-q")
    subprocess.run(
        ["git", "-C", str(restored), "index-pack", "--stdin", "--strict"],
        input=payload,
        check=True,
        capture_output=True,
    )
    service = object.__new__(ManagedCheckpointRestoreService)
    service._git_environment = env
    service._restore_saved_local_refs(restored, history, {"git": {"branch": "work"}})
    assert git(restored, "rev-parse", "refs/remotes/origin/main") == tip
    assert git(restored, "rev-parse", "refs/heads/work") == old
    assert (
        subprocess.run(
            ["git", "-C", str(restored), "show-ref", "--verify", "refs/heads/main"],
            capture_output=True,
        ).returncode
        != 0
    )
