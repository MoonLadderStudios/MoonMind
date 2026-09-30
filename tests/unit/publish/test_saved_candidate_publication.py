"""Saved-candidate publication through the existing publisher (MoonLadderStudios/MoonMind#4018).

Every case uses real local Git and bare destination remotes. The provider is a
fixture that records reconciliation reads and create calls; no live GitHub.
"""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from moonmind.config.settings import settings
from moonmind.publish import saved_candidate as saved_candidate_module
from moonmind.publish.saved_candidate import (
    SavedContent,
    SavedEntry,
    SavedPublicationAdmission,
    SavedPublicationError,
)
from moonmind.publish.service import PublishService
from moonmind.workflows.adapters.github_service import (
    CreatePRResult,
    PullRequestReconciliation,
)

pytestmark = pytest.mark.asyncio

ADMITTED = "admitted-destination-token"
AMBIENT = "ambient-process-token"
DIGEST = "sha256:" + "5" * 64


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def _commit_env() -> dict[str, str]:
    return {
        **os.environ,
        "GIT_AUTHOR_NAME": "Destination",
        "GIT_AUTHOR_EMAIL": "destination@example.invalid",
        "GIT_COMMITTER_NAME": "Destination",
        "GIT_COMMITTER_EMAIL": "destination@example.invalid",
    }


def _commit_files(clone: Path, files: dict[str, str | None], message: str) -> str:
    for name, text in files.items():
        path = clone / name
        if text is None:
            path.unlink()
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    subprocess.run(["git", "add", "-A"], cwd=clone, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", message], cwd=clone, check=True, env=_commit_env()
    )
    return git("rev-parse", "HEAD", cwd=clone)


def _destination(tmp_path: Path, files: dict[str, str] | None) -> tuple[Path, Path, str | None]:
    remote = tmp_path / "destination.git"
    git("init", "-q", "--bare", "--initial-branch=main", str(remote), cwd=tmp_path)
    writer = tmp_path / "destination-writer"
    git("clone", "-q", str(remote), str(writer), cwd=tmp_path)
    if files is None:
        return remote, writer, None
    git("checkout", "-q", "-b", "main", cwd=writer)
    head = _commit_files(writer, files, "destination base")
    git("push", "-q", "origin", "main", cwd=writer)
    return remote, writer, head


def _remote_refs(remote: Path) -> str:
    return git("for-each-ref", "--format=%(refname) %(objectname)", cwd=remote)


def _saved(
    tmp_path: Path,
    files: dict[str, str],
    *,
    executable: tuple[str, ...] = (),
    baseline: str | None = None,
    excluded: tuple[str, ...] = (),
) -> SavedContent:
    root = tmp_path / "saved-files"
    root.mkdir()
    entries = []
    for name, text in sorted(files.items()):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        entries.append(SavedEntry(path=name, kind="file", executable=name in executable))
    return SavedContent(
        root=root,
        entries=tuple(entries),
        digest=DIGEST,
        baseline_commit=baseline,
        excluded_paths=excluded,
    )


def _admission(**overrides: Any) -> SavedPublicationAdmission:
    payload: dict[str, Any] = {
        "savedWorkDigest": DIGEST,
        "repository": "dest-owner/dest-repo",
        "objective": "pr",
        "baseBranch": "main",
        "headBranch": "saved/publication",
        "strategy": "additive_import",
        "expectedBaseSha": None,
        "authorityRef": "github:secret_ref_env:GITHUB_TOKEN_SECRET_REF",
        "commit": {
            "message": "Publish saved work",
            "authorName": "MoonMind",
            "authorEmail": "moonmind@example.invalid",
            "timestamp": "2026-09-30T09:00:00+00:00",
        },
    }
    payload.update(overrides)
    return SavedPublicationAdmission.model_validate(payload)


def _tree_files(remote: Path, rev: str) -> dict[str, str]:
    names = git("ls-tree", "-r", "--name-only", rev, cwd=remote).splitlines()
    return {name: git("show", f"{rev}:{name}", cwd=remote) for name in names}


class ProviderFixture:
    """Recording provider with GitHubService's reconcile/create result types."""

    def __init__(self) -> None:
        self.pull_requests: list[dict[str, Any]] = []
        self.creates = 0
        self.reads = 0
        self.tokens: list[str] = []
        self.unavailable_reads = 0
        self.lose_create_ack = False
        self.head_sha: str | None = None

    async def reconcile_pull_request(self, **kwargs: Any) -> PullRequestReconciliation:
        self.reads += 1
        self.tokens.append(kwargs["github_token"])
        if self.unavailable_reads:
            self.unavailable_reads -= 1
            return PullRequestReconciliation(state="unavailable", retryable=True)
        for pr in self.pull_requests:
            if pr["head"] != kwargs["head"] or pr["base"] != kwargs["base"]:
                continue
            if pr["state"] == "open":
                matched = pr["sha"] == kwargs["expected_head_sha"] and pr[
                    "draft"
                ] == kwargs["draft"]
                return PullRequestReconciliation(
                    state="matched" if matched else "mismatched",
                    url=pr["url"],
                    headSha=pr["sha"],
                )
            return PullRequestReconciliation(
                state="merged" if pr.get("merged") else "closed",
                url=pr["url"],
                headSha=pr["sha"],
            )
        return PullRequestReconciliation(state="absent")

    async def create_pull_request(self, **kwargs: Any) -> CreatePRResult:
        self.creates += 1
        self.tokens.append(kwargs["github_token"])
        url = f"https://github.com/dest-owner/dest-repo/pull/{len(self.pull_requests) + 1}"
        self.pull_requests.append(
            {
                "head": kwargs["head"],
                "base": kwargs["base"],
                "state": "open",
                "draft": kwargs["draft"],
                "sha": self.head_sha,
                "url": url,
                "title": kwargs["title"],
            }
        )
        if self.lose_create_ack:
            self.lose_create_ack = False
            return CreatePRResult(created=False, retryable=True, summary="HTTP 502")
        return CreatePRResult(created=True, url=url, headSha=self.head_sha, summary="ok")


@pytest.fixture(autouse=True)
def _isolated_process(monkeypatch):
    monkeypatch.setattr(settings.security, "high_security_mode", False)
    monkeypatch.setenv("GITHUB_TOKEN", AMBIENT)
    monkeypatch.setenv("GH_TOKEN", AMBIENT)


@pytest.fixture
def git_calls(monkeypatch) -> list[dict[str, Any]]:
    """Record every saved-path Git subprocess, including its environment."""

    calls: list[dict[str, Any]] = []
    real = asyncio.create_subprocess_exec

    async def recording(*args: Any, **kwargs: Any):
        calls.append({"args": [str(arg) for arg in args], "env": dict(kwargs.get("env") or {})})
        return await real(*args, **kwargs)

    monkeypatch.setattr(saved_candidate_module.asyncio, "create_subprocess_exec", recording)
    return calls


def _pushes(calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [call for call in calls if "push" in call["args"]]


async def _prepare(tmp_path, admission, content, remote, name="publication"):
    return await PublishService().prepare_saved_candidate(
        admission=admission,
        content=content,
        workspace=tmp_path / name,
        github_token=ADMITTED,
        remote_url=str(remote),
    )


async def _push(tmp_path, admission, candidate, remote, name="publication"):
    return await PublishService().push_candidate(
        repo_dir=tmp_path / name,
        repository=admission.repository,
        head_branch=admission.head_branch,
        candidate_sha=candidate.head_sha,
        base_sha=candidate.base_sha,
        expected_remote_sha=admission.expected_head_sha,
        github_token=ADMITTED,
        remote_url=str(remote),
    )


async def test_additive_import_publishes_with_admitted_authority_only(tmp_path, git_calls):
    remote, _writer, base = _destination(
        tmp_path, {"keep.txt": "destination only\n", "README.md": "dest\n"}
    )
    admission = _admission(expectedBaseSha=base, pathPrefix="imported", objective="branch")
    content = _saved(tmp_path, {"new.txt": "saved\n", "docs/a.md": "doc\n"})

    candidate = await _prepare(tmp_path, admission, content, remote)
    outcome = await _push(tmp_path, admission, candidate, remote)

    assert outcome.status == "pushed" and outcome.remote_verified
    assert git("rev-parse", "refs/heads/saved/publication", cwd=remote) == candidate.head_sha
    assert git("rev-parse", "refs/heads/main", cwd=remote) == base
    assert git("rev-parse", f"{candidate.head_sha}^", cwd=remote) == base
    assert _tree_files(remote, candidate.head_sha) == {
        "README.md": "dest",
        "keep.txt": "destination only",
        "imported/docs/a.md": "doc",
        "imported/new.txt": "saved",
    }
    assert candidate.changed_paths == ("imported/docs/a.md", "imported/new.txt")
    assert candidate.deleted_paths == ()
    # Fresh destination authority: only the admitted token reaches Git, the
    # ambient process token and host Git configuration never do.
    assert git_calls
    for call in git_calls:
        assert AMBIENT not in call["env"].values()
        assert call["env"]["GIT_CONFIG_NOSYSTEM"] == "1"
        assert call["env"]["GIT_CONFIG_GLOBAL"] == os.devnull
        assert "core.hooksPath=/dev/null" in call["args"]
    assert all(call["env"].get("GITHUB_TOKEN") == ADMITTED for call in _pushes(git_calls))


async def test_missing_admitted_authority_fails_before_any_git_command(tmp_path, git_calls):
    remote, _writer, base = _destination(tmp_path, {"keep.txt": "x\n"})
    admission = _admission(expectedBaseSha=base)

    with pytest.raises(SavedPublicationError) as exc:
        await PublishService().prepare_saved_candidate(
            admission=admission,
            content=_saved(tmp_path, {"new.txt": "saved\n"}),
            workspace=tmp_path / "publication",
            remote_url=str(remote),
        )

    assert exc.value.code == "PUBLICATION_AUTHORITY_UNAVAILABLE"
    assert git_calls == []


async def test_bound_credential_is_admitted_destination_authority(tmp_path, git_calls):
    remote, _writer, base = _destination(tmp_path, {"keep.txt": "x\n"})
    admission = _admission(expectedBaseSha=base, objective="branch")

    class Credential:
        def use_now(self, fn):
            return fn(b"bound-destination-token")

    bound = type("Acquired", (), {"credential": Credential()})()
    candidate = await PublishService().prepare_saved_candidate(
        admission=admission,
        content=_saved(tmp_path, {"new.txt": "saved\n"}),
        workspace=tmp_path / "publication",
        bound_credential=bound,
        remote_url=str(remote),
    )
    outcome = await PublishService().push_candidate(
        repo_dir=tmp_path / "publication",
        repository=admission.repository,
        head_branch=admission.head_branch,
        candidate_sha=candidate.head_sha,
        base_sha=candidate.base_sha,
        expected_remote_sha=None,
        bound_credential=bound,
        remote_url=str(remote),
    )

    assert outcome.status == "pushed"
    assert {call["env"].get("GITHUB_TOKEN") for call in _pushes(git_calls)} == {
        "bound-destination-token"
    }


@pytest.mark.parametrize(
    ("saved_files", "expected_code", "expected_path"),
    [
        ({"keep.txt": "changed by saved work\n"}, "destination_path_conflict", "keep.txt"),
        ({"readme.md": "case collides\n"}, "case_collision", "readme.md"),
        ({"keep.txt/child": "under a file\n"}, "path_type_conflict", "keep.txt/child"),
    ],
)
async def test_additive_import_surfaces_conflicts_and_never_overwrites(
    tmp_path, saved_files, expected_code, expected_path
):
    remote, _writer, base = _destination(
        tmp_path, {"keep.txt": "destination only\n", "README.md": "dest\n"}
    )
    before = _remote_refs(remote)

    with pytest.raises(SavedPublicationError) as exc:
        await _prepare(tmp_path, _admission(expectedBaseSha=base), _saved(tmp_path, saved_files), remote)

    assert exc.value.code == "PUBLICATION_APPLICATION_CONFLICT"
    assert f"{expected_code}:{expected_path}" in exc.value.details
    assert _remote_refs(remote) == before


async def test_saved_entries_cannot_escape_the_saved_root(tmp_path, git_calls):
    remote, _writer, base = _destination(tmp_path, {"keep.txt": "keep\n"})
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("outside the saved root\n")
    content = _saved(tmp_path, {"n.txt": "n\n"})
    (content.root / "linked").symlink_to(outside, target_is_directory=True)
    escaped = SavedContent(
        root=content.root,
        entries=(*content.entries, SavedEntry(path="linked/secret.txt")),
        digest=DIGEST,
    )

    with pytest.raises(SavedPublicationError) as exc:
        await _prepare(tmp_path, _admission(expectedBaseSha=base), escaped, remote)

    assert exc.value.code == "PUBLICATION_CONTENT_INVALID"
    assert "uncontained_path:linked/secret.txt" in exc.value.details
    assert git_calls == []


async def test_additive_import_changes_executable_bit_only_as_a_conflict(tmp_path):
    remote, _writer, base = _destination(tmp_path, {"run.sh": "echo hi\n"})

    with pytest.raises(SavedPublicationError) as exc:
        await _prepare(
            tmp_path,
            _admission(expectedBaseSha=base),
            _saved(tmp_path, {"run.sh": "echo hi\n"}, executable=("run.sh",)),
            remote,
        )

    assert "destination_path_conflict:run.sh" in exc.value.details


async def test_deletion_requires_explicit_authorized_intent(tmp_path):
    remote, _writer, base = _destination(
        tmp_path, {"keep.txt": "keep\n", "obsolete.txt": "remove\n"}
    )
    content = _saved(tmp_path, {"new.txt": "saved\n"})

    implicit = await _prepare(tmp_path, _admission(expectedBaseSha=base), content, remote, "implicit")
    explicit = await _prepare(
        tmp_path,
        _admission(expectedBaseSha=base, authorizedDeletions=["obsolete.txt"]),
        content,
        remote,
        "explicit",
    )

    assert set(_tree_files(tmp_path / "implicit", implicit.head_sha)) == {
        "keep.txt",
        "obsolete.txt",
        "new.txt",
    }
    assert set(_tree_files(tmp_path / "explicit", explicit.head_sha)) == {"keep.txt", "new.txt"}
    assert explicit.deleted_paths == ("obsolete.txt",)
    with pytest.raises(SavedPublicationError) as exc:
        await _prepare(
            tmp_path,
            _admission(expectedBaseSha=base, authorizedDeletions=["missing.txt"]),
            content,
            remote,
            "missing",
        )
    assert "deletion_target_missing:missing.txt" in exc.value.details


async def test_same_baseline_applies_recorded_delta_onto_advanced_destination(tmp_path):
    remote, writer, baseline = _destination(
        tmp_path,
        {
            "a.txt": "baseline a\n",
            "b.txt": "baseline b\n",
            "c.txt": "baseline c\n",
            ".env": "tracked but excluded from capture\n",
        },
    )
    advanced = _commit_files(writer, {"e.txt": "another writer\n"}, "advance")
    git("push", "-q", "origin", "main", cwd=writer)
    # Saved work: a.txt modified, b.txt deleted, d.txt added, .env not captured.
    content = _saved(
        tmp_path,
        {"a.txt": "saved a\n", "c.txt": "baseline c\n", "d.txt": "saved d\n"},
        baseline=baseline,
        excluded=(".env",),
    )
    admission = _admission(strategy="baseline_delta", expectedBaseSha=advanced)

    candidate = await _prepare(tmp_path, admission, content, remote)

    assert _tree_files(tmp_path / "publication", candidate.head_sha) == {
        "a.txt": "saved a",
        "c.txt": "baseline c",
        "d.txt": "saved d",
        "e.txt": "another writer",
        ".env": "tracked but excluded from capture",
    }
    assert candidate.deleted_paths == ("b.txt",)
    assert candidate.base_sha == advanced


async def test_recorded_delta_is_the_only_deletion_evidence(tmp_path):
    remote, _writer, baseline = _destination(
        tmp_path,
        {"a.txt": "baseline a\n", "gone.txt": "deleted\n", "unlisted.txt": "kept\n"},
    )
    # unlisted.txt is absent from the snapshot (e.g. an exclusion the bounded
    # manifest list truncated) but capture never recorded deleting it.
    content = dataclasses.replace(
        _saved(tmp_path, {"a.txt": "saved a\n"}, baseline=baseline),
        recorded_deletions=("gone.txt",),
    )
    admission = _admission(strategy="baseline_delta", expectedBaseSha=baseline)

    candidate = await _prepare(tmp_path, admission, content, remote)

    assert candidate.deleted_paths == ("gone.txt",)
    assert _tree_files(tmp_path / "publication", candidate.head_sha) == {
        "a.txt": "saved a",
        "unlisted.txt": "kept",
    }


async def test_same_baseline_rejects_destination_changes_to_the_same_paths(tmp_path):
    remote, writer, baseline = _destination(tmp_path, {"a.txt": "baseline a\n"})
    advanced = _commit_files(writer, {"a.txt": "another writer\n"}, "advance")
    git("push", "-q", "origin", "main", cwd=writer)
    content = _saved(tmp_path, {"a.txt": "saved a\n"}, baseline=baseline)

    with pytest.raises(SavedPublicationError) as exc:
        await _prepare(
            tmp_path, _admission(strategy="baseline_delta", expectedBaseSha=advanced), content, remote
        )

    assert "destination_changed_since_baseline:a.txt" in exc.value.details


async def test_unrelated_destination_cannot_use_a_baseline_delta(tmp_path):
    remote, _writer, base = _destination(tmp_path, {"a.txt": "unrelated\n"})
    content = _saved(tmp_path, {"a.txt": "saved a\n"}, baseline="1" * 40)

    with pytest.raises(SavedPublicationError) as exc:
        await _prepare(tmp_path, _admission(strategy="baseline_delta", expectedBaseSha=base), content, remote)

    assert exc.value.code == "PUBLICATION_BASELINE_UNAVAILABLE"


async def test_stale_base_expectation_is_rejected_before_effects(tmp_path):
    remote, writer, base = _destination(tmp_path, {"keep.txt": "keep\n"})
    _commit_files(writer, {"moved.txt": "moved\n"}, "moved")
    git("push", "-q", "origin", "main", cwd=writer)
    before = _remote_refs(remote)

    with pytest.raises(SavedPublicationError) as exc:
        await _prepare(tmp_path, _admission(expectedBaseSha=base), _saved(tmp_path, {"n.txt": "n\n"}), remote)

    assert exc.value.code == "PUBLICATION_STALE_EXPECTATION"
    assert _remote_refs(remote) == before


async def test_no_change_candidate_publishes_nothing(tmp_path):
    remote, _writer, base = _destination(tmp_path, {"keep.txt": "keep\n"})

    candidate = await _prepare(
        tmp_path, _admission(expectedBaseSha=base), _saved(tmp_path, {"keep.txt": "keep\n"}), remote
    )

    assert candidate.no_change is True
    assert candidate.head_sha == base


async def test_empty_initialization_requires_confirmed_emptiness(tmp_path):
    remote, _writer, _ = _destination(tmp_path, None)
    admission = _admission(
        strategy="empty_initialization", objective="branch", baseBranch=None, headBranch="main"
    )
    content = _saved(tmp_path, {"init.txt": "first\n"})

    candidate = await _prepare(tmp_path, admission, content, remote)
    outcome = await _push(tmp_path, admission, candidate, remote)

    assert outcome.status == "pushed"
    assert candidate.base_sha is None
    assert git("rev-parse", "refs/heads/main", cwd=remote) == candidate.head_sha
    assert _tree_files(remote, "main") == {"init.txt": "first"}
    # Now non-empty: the same admission is refused rather than overwriting.
    with pytest.raises(SavedPublicationError) as exc:
        await _prepare(tmp_path, admission, content, remote, "second")
    assert exc.value.code == "PUBLICATION_DESTINATION_NOT_EMPTY"


async def test_failed_emptiness_read_is_unavailable_not_empty(tmp_path):
    admission = _admission(
        strategy="empty_initialization", objective="branch", baseBranch=None, headBranch="main"
    )

    with pytest.raises(SavedPublicationError) as exc:
        await _prepare(tmp_path, admission, _saved(tmp_path, {"i.txt": "i\n"}), tmp_path / "absent.git")

    assert exc.value.code == "PUBLICATION_DESTINATION_UNAVAILABLE"
    assert exc.value.retryable is True
    assert not (tmp_path / "absent.git").exists()


@pytest.mark.parametrize(
    "overrides",
    [
        {"destinationProvider": "lore"},
        {"strategy": "empty_initialization", "objective": "pr", "baseBranch": None},
        {"strategy": "baseline_delta", "pathPrefix": "mapped", "expectedBaseSha": "a" * 40},
        {"expectedBaseSha": None},
        {"pathPrefix": "../escape", "expectedBaseSha": "a" * 40},
        {"authorizedDeletions": [".git/config"], "expectedBaseSha": "a" * 40},
        {"headBranch": "main", "expectedBaseSha": "a" * 40},
    ],
)
async def test_unsupported_admissions_fail_closed(overrides):
    with pytest.raises(ValueError) as exc:
        _admission(**overrides)
    if overrides.get("destinationProvider") == "lore":
        assert "PUBLICATION_LORE_AUTHORITATIVE" in str(exc.value)


async def test_changed_content_base_mapping_or_authority_invalidates_the_decision():
    original = _admission(expectedBaseSha="a" * 40)
    changed = [
        _admission(expectedBaseSha="a" * 40, savedWorkDigest="sha256:" + "6" * 64),
        _admission(expectedBaseSha="b" * 40),
        _admission(expectedBaseSha="a" * 40, pathPrefix="mapped"),
        _admission(expectedBaseSha="a" * 40, authorityRef="github:explicit:other"),
        _admission(expectedBaseSha="a" * 40, authorizedDeletions=["x.txt"]),
    ]

    assert original.decision_digest() == _admission(expectedBaseSha="a" * 40).decision_digest()
    assert len({item.decision_digest() for item in [original, *changed]}) == 6


async def test_competing_head_tip_is_not_promoted_into_overwrite_permission(tmp_path, git_calls):
    remote, writer, base = _destination(tmp_path, {"keep.txt": "keep\n"})
    admission = _admission(expectedBaseSha=base, objective="branch")
    candidate = await _prepare(tmp_path, admission, _saved(tmp_path, {"n.txt": "n\n"}), remote)
    # Another actor creates the head branch after admission.
    git("checkout", "-q", "-b", "saved/publication", cwd=writer)
    competing = _commit_files(writer, {"other.txt": "other actor\n"}, "other")
    git("push", "-q", "origin", "saved/publication", cwd=writer)

    outcome = await _push(tmp_path, admission, candidate, remote)

    assert outcome.status == "conflict"
    assert outcome.reason_code == "remote_head_changed"
    assert outcome.remote_head_sha == competing
    assert git("rev-parse", "refs/heads/saved/publication", cwd=remote) == competing
    assert _pushes(git_calls) == []


async def test_admitted_head_expectation_is_the_only_lease(tmp_path):
    remote, writer, base = _destination(tmp_path, {"keep.txt": "keep\n"})
    git("checkout", "-q", "-b", "saved/publication", cwd=writer)
    admitted_tip = _commit_files(writer, {"old.txt": "old candidate\n"}, "old")
    git("push", "-q", "origin", "saved/publication", cwd=writer)
    admission = _admission(expectedBaseSha=base, expectedHeadSha=admitted_tip)
    candidate = await _prepare(tmp_path, admission, _saved(tmp_path, {"n.txt": "n\n"}), remote)

    outcome = await _push(tmp_path, admission, candidate, remote)

    assert outcome.status == "pushed"
    assert git("rev-parse", "refs/heads/saved/publication", cwd=remote) == candidate.head_sha


@pytest.mark.parametrize("strategy", ["additive_import", "empty_initialization"])
async def test_existing_push_scan_blocks_secrets_before_any_push(
    tmp_path, git_calls, monkeypatch, strategy
):
    monkeypatch.setattr(settings.security, "high_security_mode", True)
    if strategy == "empty_initialization":
        remote, _writer, base = _destination(tmp_path, None)
        admission = _admission(
            strategy=strategy, objective="branch", baseBranch=None, headBranch="main"
        )
    else:
        remote, _writer, base = _destination(tmp_path, {"keep.txt": "keep\n"})
        admission = _admission(expectedBaseSha=base, objective="branch")
    secret = "token = ghp_" + "A" * 36 + "\n"
    candidate = await _prepare(tmp_path, admission, _saved(tmp_path, {"cfg.txt": secret}), remote)
    before = _remote_refs(remote)

    with pytest.raises(SavedPublicationError) as exc:
        await _push(tmp_path, admission, candidate, remote)

    assert exc.value.code == "PUBLICATION_SCAN_BLOCKED"
    assert "A" * 36 not in str(exc.value)
    assert _pushes(git_calls) == []
    assert _remote_refs(remote) == before


async def test_existing_push_scan_allows_clean_root_candidate(tmp_path, monkeypatch):
    monkeypatch.setattr(settings.security, "high_security_mode", True)
    remote, _writer, _ = _destination(tmp_path, None)
    admission = _admission(
        strategy="empty_initialization", objective="branch", baseBranch=None, headBranch="main"
    )
    candidate = await _prepare(tmp_path, admission, _saved(tmp_path, {"a.txt": "clean\n"}), remote)

    outcome = await _push(tmp_path, admission, candidate, remote)

    assert outcome.status == "pushed" and outcome.remote_verified


async def test_lost_push_acknowledgment_reconciles_without_a_second_push(tmp_path, git_calls, monkeypatch):
    remote, _writer, base = _destination(tmp_path, {"keep.txt": "keep\n"})
    admission = _admission(expectedBaseSha=base, objective="branch")
    candidate = await _prepare(tmp_path, admission, _saved(tmp_path, {"n.txt": "n\n"}), remote)
    recording = saved_candidate_module.asyncio.create_subprocess_exec

    async def lose_push_ack(*args: Any, **kwargs: Any):
        process = await recording(*args, **kwargs)
        if "push" not in [str(arg) for arg in args]:
            return process
        await process.communicate()

        class LostAck:
            returncode = 128

            async def communicate(self, input=None):
                return b"", b"fatal: the remote end hung up unexpectedly"

        return LostAck()

    monkeypatch.setattr(saved_candidate_module.asyncio, "create_subprocess_exec", lose_push_ack)
    first = await _push(tmp_path, admission, candidate, remote)
    retry = await _push(tmp_path, admission, candidate, remote)

    assert first.status == "pushed" and first.reason_code == "push_acknowledgment_reconciled"
    assert retry.status == "reconciled"
    assert len(_pushes(git_calls)) == 1
    assert git("rev-parse", "refs/heads/saved/publication", cwd=remote) == candidate.head_sha


async def test_restart_rebuilds_the_identical_candidate_and_reconciles(tmp_path, git_calls):
    remote, _writer, base = _destination(tmp_path, {"keep.txt": "keep\n"})
    admission = _admission(expectedBaseSha=base, objective="branch")
    content = _saved(tmp_path, {"n.txt": "n\n"})

    first = await _prepare(tmp_path, admission, content, remote, "before-restart")
    pushed = await _push(tmp_path, admission, first, remote, "before-restart")
    rebuilt = await _prepare(tmp_path, admission, content, remote, "after-restart")
    retried = await _push(tmp_path, admission, rebuilt, remote, "after-restart")

    assert pushed.status == "pushed"
    assert rebuilt.head_sha == first.head_sha
    assert retried.status == "reconciled"
    assert len(_pushes(git_calls)) == 1


async def test_persisted_candidate_rebuilds_on_its_admitted_base_after_the_base_moves(
    tmp_path, git_calls
):
    remote, writer, base = _destination(tmp_path, {"keep.txt": "keep\n"})
    admission = _admission(expectedBaseSha=base, objective="branch")
    content = _saved(tmp_path, {"n.txt": "n\n"})
    persisted = await _prepare(tmp_path, admission, content, remote, "prepare")
    _commit_files(writer, {"other.txt": "another writer\n"}, "advance")
    git("push", "-q", "origin", "main", cwd=writer)

    # A fresh admission would now be stale; the persisted decision is not.
    with pytest.raises(SavedPublicationError) as stale:
        await _prepare(tmp_path, admission, content, remote, "fresh")
    rebuilt = await PublishService().prepare_saved_candidate(
        admission=admission,
        content=content,
        workspace=tmp_path / "rebuild",
        github_token=ADMITTED,
        remote_url=str(remote),
        persisted_head_sha=persisted.head_sha,
    )
    pushed = await _push(tmp_path, admission, rebuilt, remote, "rebuild")

    assert stale.value.code == "PUBLICATION_STALE_EXPECTATION"
    assert rebuilt.head_sha == persisted.head_sha
    assert rebuilt.base_sha == base
    assert pushed.status == "pushed"
    assert git("rev-parse", "refs/heads/saved/publication", cwd=remote) == persisted.head_sha


async def test_rebuild_rejects_a_candidate_that_differs_from_the_persisted_one(tmp_path):
    remote, _writer, base = _destination(tmp_path, {"keep.txt": "keep\n"})
    admission = _admission(expectedBaseSha=base, objective="branch")

    with pytest.raises(SavedPublicationError) as exc:
        await PublishService().prepare_saved_candidate(
            admission=admission,
            content=_saved(tmp_path, {"n.txt": "n\n"}),
            workspace=tmp_path / "rebuild",
            github_token=ADMITTED,
            remote_url=str(remote),
            persisted_head_sha="e" * 40,
        )

    assert exc.value.code == "PUBLICATION_CANDIDATE_MISMATCH"
    assert "refs/heads/saved/publication" not in _remote_refs(remote)


async def test_destination_branch_is_observed_once_without_treating_failure_as_absence(
    tmp_path,
):
    remote, _writer, base = _destination(tmp_path, {"keep.txt": "keep\n"})
    (tmp_path / "observe").mkdir()

    async def observe(branch, url):
        return await PublishService().observe_destination_branch(
            workspace=tmp_path / "observe",
            repository="dest-owner/dest-repo",
            branch=branch,
            github_token=ADMITTED,
            remote_url=str(url),
        )

    assert await observe("main", remote) == base
    assert await observe("absent", remote) is None
    with pytest.raises(SavedPublicationError) as exc:
        await observe("main", tmp_path / "missing.git")
    assert exc.value.code == "PUBLICATION_DESTINATION_UNAVAILABLE"
    assert exc.value.retryable is True


async def _publish_pr(provider, candidate, admission):
    return await PublishService(github_service=provider).publish_pull_request(
        repository=admission.repository,
        head_branch=admission.head_branch,
        base_branch=admission.base_branch,
        candidate_sha=candidate,
        draft=False,
        title="Publish saved work",
        body="Saved work",
        github_token=ADMITTED,
    )


@pytest.mark.parametrize(
    ("existing", "expected_status"),
    [
        ({"state": "open", "sha": "c" * 40}, "adopted"),
        ({"state": "open", "sha": "d" * 40}, "conflict"),
        ({"state": "closed", "sha": "c" * 40}, "closed"),
        ({"state": "closed", "sha": "c" * 40, "merged": True}, "merged"),
    ],
)
async def test_existing_pull_request_results_are_consumed_without_writes(existing, expected_status):
    provider = ProviderFixture()
    provider.pull_requests.append(
        {
            "head": "saved/publication",
            "base": "main",
            "draft": False,
            "url": "https://github.com/dest-owner/dest-repo/pull/9",
            "title": "Edited by another actor",
            **existing,
        }
    )

    outcome = await _publish_pr(provider, "c" * 40, _admission(expectedBaseSha="a" * 40))

    assert outcome.status == expected_status
    assert provider.creates == 0
    assert provider.pull_requests[0]["title"] == "Edited by another actor"
    assert set(provider.tokens) == {ADMITTED}


async def test_unavailable_reconciliation_performs_no_create():
    provider = ProviderFixture()
    provider.unavailable_reads = 1

    outcome = await _publish_pr(provider, "c" * 40, _admission(expectedBaseSha="a" * 40))

    assert outcome.status == "unavailable" and outcome.retryable
    assert provider.creates == 0


async def test_lost_create_acknowledgment_adopts_the_same_pull_request():
    provider = ProviderFixture()
    provider.head_sha = "c" * 40
    provider.lose_create_ack = True
    admission = _admission(expectedBaseSha="a" * 40)

    first = await _publish_pr(provider, "c" * 40, admission)
    retry = await _publish_pr(provider, "c" * 40, admission)

    assert first.status == "unavailable" and first.retryable
    assert retry.status == "adopted"
    assert retry.url == "https://github.com/dest-owner/dest-repo/pull/1"
    assert provider.creates == 1


async def test_push_success_followed_by_pr_failure_retries_only_the_pr(tmp_path, git_calls):
    remote, _writer, base = _destination(tmp_path, {"keep.txt": "keep\n"})
    admission = _admission(expectedBaseSha=base)
    candidate = await _prepare(tmp_path, admission, _saved(tmp_path, {"n.txt": "n\n"}), remote)
    provider = ProviderFixture()
    provider.head_sha = candidate.head_sha
    provider.unavailable_reads = 1

    pushed = await _push(tmp_path, admission, candidate, remote)
    failed_pr = await _publish_pr(provider, candidate.head_sha, admission)
    # Retry: the push phase reconciles the already confirmed effect.
    repushed = await _push(tmp_path, admission, candidate, remote)
    created = await _publish_pr(provider, candidate.head_sha, admission)

    assert pushed.status == "pushed"
    assert failed_pr.status == "unavailable"
    assert repushed.status == "reconciled"
    assert created.status == "created" and created.head_sha == candidate.head_sha
    assert provider.creates == 1
    assert len(_pushes(git_calls)) == 1


async def test_saved_bytes_are_not_altered_by_publication(tmp_path):
    remote, _writer, base = _destination(tmp_path, {"keep.txt": "keep\n"})
    content = _saved(tmp_path, {"n.txt": "n\n", "sub/m.txt": "m\n"})
    before = {
        path.relative_to(content.root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in content.root.rglob("*")
        if path.is_file()
    }

    candidate = await _prepare(tmp_path, _admission(expectedBaseSha=base), content, remote)
    await _push(tmp_path, _admission(expectedBaseSha=base), candidate, remote)

    after = {
        path.relative_to(content.root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in content.root.rglob("*")
        if path.is_file()
    }
    assert after == before
