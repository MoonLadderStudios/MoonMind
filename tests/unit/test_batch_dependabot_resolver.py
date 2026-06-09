"""Unit tests for the batch-dependabot-resolver skill (MM-803).

These cover the acceptance-criteria behaviors: Dependabot title matching,
author matching, fork skipping, non-Dependabot PR skipping, idempotency key
generation, dry-run behavior, and child ``pr-resolver`` payload generation.
"""

from __future__ import annotations

import asyncio
import runpy
import sys
from pathlib import Path
from typing import Any

import pytest


def _load_module() -> dict[str, Any]:
    repo_root = Path(__file__).resolve().parents[2]
    return runpy.run_path(
        str(
            repo_root
            / ".agents"
            / "skills"
            / "batch-dependabot-resolver"
            / "bin"
            / "batch_dependabot_resolver.py"
        )
    )


def _dependabot_pr(
    *,
    number: int = 42,
    title: str = "Bump anthropic from 0.105.2 to 0.107.1",
    author: str = "dependabot[bot]",
    branch: str = "dependabot/pip/anthropic-0.107.1",
    head_sha: str = "abc123",
    repo: str = "MoonLadderStudios/MoonMind",
    is_cross_repository: bool = False,
    labels: list[Any] | None = None,
) -> dict[str, Any]:
    owner, name = repo.split("/", 1)
    return {
        "number": number,
        "title": title,
        "author": {"login": author},
        "headRefName": branch,
        "headRefOid": head_sha,
        "isCrossRepository": is_cross_repository,
        "headRepository": {"name": name, "nameWithOwner": repo},
        "headRepositoryOwner": {"login": owner},
        "labels": labels or [],
    }


# ---------------------------------------------------------------------------
# Title matching
# ---------------------------------------------------------------------------


def test_title_matches_standard_version_bump() -> None:
    module = _load_module()
    title_matches = module["_title_matches"]
    default_regex = module["DEFAULT_TITLE_REGEX"]

    assert title_matches("Bump anthropic from 0.105.2 to 0.107.1", default_regex)
    assert title_matches("Bump boto3 from 1.43.19 to 1.43.24", default_regex)


def test_title_does_not_match_non_version_bump() -> None:
    module = _load_module()
    title_matches = module["_title_matches"]
    default_regex = module["DEFAULT_TITLE_REGEX"]

    assert not title_matches("Refactor anthropic client", default_regex)
    assert not title_matches("Bump anthropic", default_regex)
    assert not title_matches("", default_regex)


def test_invalid_title_regex_raises() -> None:
    module = _load_module()
    title_matches = module["_title_matches"]

    with pytest.raises(RuntimeError, match="invalid title regex"):
        title_matches("Bump x from 1 to 2", "(")


# ---------------------------------------------------------------------------
# Author matching
# ---------------------------------------------------------------------------


def test_dependabot_author_matches_case_insensitively() -> None:
    module = _load_module()
    is_dependabot_author = module["_is_dependabot_author"]

    assert is_dependabot_author({"author": {"login": "dependabot[bot]"}})
    assert is_dependabot_author({"author": {"login": "Dependabot[bot]"}})


def test_non_dependabot_author_does_not_match() -> None:
    module = _load_module()
    is_dependabot_author = module["_is_dependabot_author"]

    assert not is_dependabot_author({"author": {"login": "octocat"}})
    assert not is_dependabot_author({"author": {"login": "renovate[bot]"}})
    assert not is_dependabot_author({})


# ---------------------------------------------------------------------------
# Classification: fork / non-Dependabot skipping
# ---------------------------------------------------------------------------


def _classify(module: dict[str, Any], pr: dict[str, Any], **overrides: Any) -> str | None:
    kwargs: dict[str, Any] = dict(
        repo="MoonLadderStudios/MoonMind",
        title_regex=module["DEFAULT_TITLE_REGEX"],
        package_managers=[],
        include_security_updates=True,
    )
    kwargs.update(overrides)
    return module["_classify_pr"](pr, **kwargs)


def test_classify_matches_clean_dependabot_pr() -> None:
    module = _load_module()
    assert _classify(module, _dependabot_pr()) is None


def test_classify_skips_fork_pr() -> None:
    module = _load_module()
    pr = _dependabot_pr(is_cross_repository=True)
    assert _classify(module, pr) == "fork-pr"


def test_classify_skips_cross_repo_head_owner() -> None:
    module = _load_module()
    pr = _dependabot_pr()
    pr["headRepository"] = {"name": "MoonMind", "nameWithOwner": "fork-org/MoonMind"}
    pr["headRepositoryOwner"] = {"login": "fork-org"}
    assert _classify(module, pr) == "fork-pr"


def test_classify_skips_non_dependabot_author() -> None:
    module = _load_module()
    pr = _dependabot_pr(author="octocat")
    assert _classify(module, pr) == "non-dependabot-author"


def test_classify_skips_non_dependabot_branch() -> None:
    module = _load_module()
    pr = _dependabot_pr(branch="feature/manual-bump")
    assert _classify(module, pr) == "non-dependabot-branch"


def test_classify_skips_non_version_bump_title() -> None:
    module = _load_module()
    pr = _dependabot_pr(title="Update anthropic dependency")
    assert _classify(module, pr) == "non-version-bump-title"


def test_classify_skips_package_manager_not_allowed() -> None:
    module = _load_module()
    pr = _dependabot_pr(branch="dependabot/npm_and_yarn/left-pad-1.0.0")
    assert _classify(module, pr, package_managers=["pip"]) == "package-manager-not-allowed"


def test_classify_allows_package_manager_alias() -> None:
    module = _load_module()
    pr = _dependabot_pr(branch="dependabot/npm_and_yarn/left-pad-1.0.0")
    # operator passes "npm"; branch encodes "npm_and_yarn" — alias must match.
    assert _classify(module, pr, package_managers=["npm"]) is None


def test_classify_skips_security_update_when_excluded() -> None:
    module = _load_module()
    pr = _dependabot_pr(labels=[{"name": "security"}])
    assert _classify(module, pr, include_security_updates=False) == "security-update-excluded"
    # included by default
    assert _classify(module, pr, include_security_updates=True) is None


# ---------------------------------------------------------------------------
# Idempotency key generation
# ---------------------------------------------------------------------------


def test_idempotency_key_is_stable_for_same_head() -> None:
    module = _load_module()
    child_idempotency_key = module["_child_idempotency_key"]

    key = child_idempotency_key(
        repo="MoonLadderStudios/MoonMind", pr_number=42, head_sha="abc123"
    )
    assert key == "batch-dependabot-resolver:MoonLadderStudios/MoonMind:pr:42:head:abc123"
    # deterministic across calls
    assert key == child_idempotency_key(
        repo="MoonLadderStudios/MoonMind", pr_number=42, head_sha="abc123"
    )


def test_idempotency_key_changes_with_head_sha() -> None:
    module = _load_module()
    child_idempotency_key = module["_child_idempotency_key"]

    key_one = child_idempotency_key(
        repo="MoonLadderStudios/MoonMind", pr_number=42, head_sha="abc123"
    )
    key_two = child_idempotency_key(
        repo="MoonLadderStudios/MoonMind", pr_number=42, head_sha="def456"
    )
    assert key_one != key_two


def test_idempotency_key_requires_head_sha() -> None:
    module = _load_module()
    child_idempotency_key = module["_child_idempotency_key"]

    with pytest.raises(RuntimeError, match="head SHA"):
        child_idempotency_key(
            repo="MoonLadderStudios/MoonMind", pr_number=42, head_sha=""
        )


def test_idempotency_key_falls_back_to_hash_for_long_repo() -> None:
    module = _load_module()
    child_idempotency_key = module["_child_idempotency_key"]
    max_len = module["IDEMPOTENCY_KEY_MAX_LENGTH"]

    long_repo = "owner/" + ("x" * 200)
    key = child_idempotency_key(repo=long_repo, pr_number=7, head_sha="abc123")
    assert key.startswith("batch-dependabot-resolver:pr:7:sha256:")
    assert len(key) <= max_len


# ---------------------------------------------------------------------------
# Child pr-resolver payload generation
# ---------------------------------------------------------------------------


def test_build_queue_request_child_pr_resolver_payload() -> None:
    module = _load_module()
    build_queue_request = module["_build_queue_request"]
    runtime_selection = module["RuntimeSelection"]

    request = build_queue_request(
        "MoonLadderStudios/MoonMind",
        42,
        "dependabot/pip/anthropic-0.107.1",
        "abc123",
        runtime=runtime_selection(mode="codex", model="gpt-5-codex", effort="high"),
        merge_method="squash",
        max_iterations=3,
        priority=0,
        max_attempts=3,
    )

    payload = request["payload"]
    task = payload["task"]

    assert request["type"] == "task"
    assert payload["repository"] == "MoonLadderStudios/MoonMind"
    assert payload["requiredCapabilities"] == ["gh"]
    assert task["skill"]["name"] == "pr-resolver"
    assert task["skill"]["version"] == "1.0"
    assert task["inputs"] == {
        "repo": "MoonLadderStudios/MoonMind",
        "pr": "42",
        "branch": "dependabot/pip/anthropic-0.107.1",
        "mergeMethod": "squash",
        "maxIterations": 3,
    }
    assert task["git"]["startingBranch"] == "dependabot/pip/anthropic-0.107.1"
    assert task["publish"]["mode"] == "none"
    assert task["runtime"]["mode"] == "codex"
    assert payload["targetRuntime"] == "codex"
    # idempotency key always present and head-sha scoped
    assert payload["idempotencyKey"] == (
        "batch-dependabot-resolver:MoonLadderStudios/MoonMind:pr:42:head:abc123"
    )


def test_build_request_records_matches_and_skips() -> None:
    module = _load_module()
    build_request_records = module["_build_request_records"]
    runtime_selection = module["RuntimeSelection"]

    open_prs = [
        _dependabot_pr(number=1),
        _dependabot_pr(number=2, author="octocat"),  # skipped: author
        _dependabot_pr(number=3, is_cross_repository=True),  # skipped: fork
        _dependabot_pr(number=4, title="Update foo"),  # skipped: title
    ]
    args = type(
        "Args",
        (),
        {
            "title_regex": module["DEFAULT_TITLE_REGEX"],
            "package_managers": None,
            "include_security_updates": True,
            "max_prs": None,
            "merge_method": "squash",
            "max_iterations": 3,
            "priority": 0,
            "max_attempts": 3,
            "skill_version": "1.0",
        },
    )()

    queue_requests, matched, skipped = build_request_records(
        "MoonLadderStudios/MoonMind",
        open_prs,
        args,
        runtime_selection(mode="codex", model=None, effort=None),
    )

    assert len(queue_requests) == 1
    assert [m["pr"] for m in matched] == [1]
    skip_reasons = {s["pr"]: s["reason"] for s in skipped}
    assert skip_reasons == {
        2: "non-dependabot-author",
        3: "fork-pr",
        4: "non-version-bump-title",
    }


def test_build_request_records_respects_max_prs_cap() -> None:
    module = _load_module()
    build_request_records = module["_build_request_records"]
    runtime_selection = module["RuntimeSelection"]

    open_prs = [_dependabot_pr(number=n) for n in (1, 2, 3)]
    args = type(
        "Args",
        (),
        {
            "title_regex": module["DEFAULT_TITLE_REGEX"],
            "package_managers": None,
            "include_security_updates": True,
            "max_prs": 2,
            "merge_method": "squash",
            "max_iterations": 3,
            "priority": 0,
            "max_attempts": 3,
            "skill_version": "1.0",
        },
    )()

    queue_requests, matched, skipped = build_request_records(
        "MoonLadderStudios/MoonMind",
        open_prs,
        args,
        runtime_selection(mode="codex", model=None, effort=None),
    )

    assert len(queue_requests) == 2
    assert len(matched) == 2
    assert [s["reason"] for s in skipped] == ["max-prs-cap"]


# ---------------------------------------------------------------------------
# Dry-run behavior
# ---------------------------------------------------------------------------


def test_dry_run_does_not_submit_jobs(monkeypatch: Any, tmp_path: Path) -> None:
    module = _load_module()
    main = module["main"]

    open_prs = [
        _dependabot_pr(number=1),
        _dependabot_pr(number=2, author="octocat"),
    ]

    submit_called: list[Any] = []

    async def fake_submit(queue_requests: Any) -> tuple[list, list]:
        submit_called.append(queue_requests)
        return [], []

    monkeypatch.setitem(main.__globals__, "_run_pr_list", lambda repo, state: open_prs)
    monkeypatch.setitem(main.__globals__, "_submit_jobs", fake_submit)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "batch_dependabot_resolver.py",
            "--repo",
            "MoonLadderStudios/MoonMind",
            "--dry-run",
            "--artifacts-dir",
            str(tmp_path),
        ],
    )

    rc = asyncio.run(main())

    assert rc == 0
    # No submission attempted in dry-run.
    assert submit_called == []

    result = module["json"].loads(
        (tmp_path / "batch_dependabot_resolver_result.json").read_text()
    )
    assert result["dryRun"] is True
    assert result["created"] == 0
    assert result["matched"] == 1
    assert result["discovered"] == 2
    assert len(result["planned"]) == 1
    assert result["planned"][0]["pr"] == 1
    assert result["planned"][0]["idempotencyKey"] == (
        "batch-dependabot-resolver:MoonLadderStudios/MoonMind:pr:1:head:abc123"
    )


def test_non_dry_run_submits_jobs(monkeypatch: Any, tmp_path: Path) -> None:
    module = _load_module()
    main = module["main"]

    open_prs = [_dependabot_pr(number=5)]

    submit_called: list[Any] = []

    async def fake_submit(queue_requests: Any) -> tuple[list, list]:
        submit_called.append(queue_requests)
        return (
            [{"pr": 5, "branch": "dependabot/pip/anthropic-0.107.1", "jobId": "mm:wf-5"}],
            [],
        )

    monkeypatch.setitem(main.__globals__, "_run_pr_list", lambda repo, state: open_prs)
    monkeypatch.setitem(main.__globals__, "_submit_jobs", fake_submit)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "batch_dependabot_resolver.py",
            "--repo",
            "MoonLadderStudios/MoonMind",
            "--artifacts-dir",
            str(tmp_path),
        ],
    )

    rc = asyncio.run(main())

    assert rc == 0
    assert len(submit_called) == 1
    result = module["json"].loads(
        (tmp_path / "batch_dependabot_resolver_result.json").read_text()
    )
    assert result["dryRun"] is False
    assert result["created"] == 1
    assert result["queued"][0]["jobId"] == "mm:wf-5"
