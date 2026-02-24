from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest


def _load_module(script_path: str) -> dict[str, Any]:
    import runpy

    return runpy.run_path(script_path)


REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def get_pr_comments_module() -> dict[str, Any]:
    return _load_module(
        str(
            REPO_ROOT
            / ".agents"
            / "skills"
            / "fix-comments"
            / "tools"
            / "get_pr_comments.py"
        )
    )


@pytest.fixture
def pr_resolve_snapshot_module() -> dict[str, Any]:
    return _load_module(
        str(
            REPO_ROOT
            / ".agents"
            / "skills"
            / "pr-resolver"
            / "bin"
            / "pr_resolve_snapshot.py"
        )
    )


def test_parse_remote_url_accepts_https_and_ssh_urls(
    get_pr_comments_module: dict[str, Any],
) -> None:
    parse_remote_url = get_pr_comments_module["_parse_remote_url"]

    assert parse_remote_url("https://github.com/org/example.git") == ("org", "example")
    assert parse_remote_url("git@github.com:org/example.git") == ("org", "example")
    assert parse_remote_url("ssh://git@github.com/org/example") == ("org", "example")


def test_parse_remote_url_returns_none_for_unrelated_inputs(
    get_pr_comments_module: dict[str, Any],
) -> None:
    parse_remote_url = get_pr_comments_module["_parse_remote_url"]

    assert parse_remote_url("") is None
    assert parse_remote_url("not-a-repo") is None
    assert parse_remote_url("owner_only/") is None


def test_parse_repo_slug_accepts_remote_urls_and_owner_repo_forms(
    get_pr_comments_module: dict[str, Any],
) -> None:
    parse_repo_slug = get_pr_comments_module["parse_repo_slug"]

    assert parse_repo_slug("org/example") == ("org", "example")
    assert parse_repo_slug("https://github.com/org/example") == ("org", "example")
    assert parse_repo_slug("git@github.com:org/example") == ("org", "example")

    with pytest.raises(ValueError, match="Invalid --repo value"):
        parse_repo_slug("owner_only")


def test_infer_repo_from_pr_url_handles_pull_url(
    pr_resolve_snapshot_module: dict[str, Any],
) -> None:
    infer_repo = pr_resolve_snapshot_module["infer_repo_from_pr_url"]

    assert infer_repo("https://github.com/org/example/pull/123") == "org/example"
    assert infer_repo("https://github.com/org/example.git/pull/123") == "org/example"


def test_infer_repo_from_pr_url_returns_none_for_invalid_url(
    pr_resolve_snapshot_module: dict[str, Any],
) -> None:
    infer_repo = pr_resolve_snapshot_module["infer_repo_from_pr_url"]

    assert infer_repo("") is None
    assert infer_repo("not a url") is None
    assert infer_repo("https://github.com/org") is None
