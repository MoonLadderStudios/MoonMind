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
