"""Hermetic dispatch-boundary coverage for #4273 (A9/R8).

Executes the real comment-posting helpers behind the Jira/GitHub verify
skills with controlled provider doubles (no live provider, no network):

- secret canaries in comment payloads gate the post on both helpers;
- the Jira helper dispatches the trusted-tool payload
  (``jira.add_comment`` with the exact issue key and body) to the
  ``/mcp/tools/call`` endpoint and propagates provider failures without
  claiming success;
- the PR helper dispatches the exact ``gh pr comment`` argv and
  propagates the provider return code so callers can report partial
  success separately.
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
import urllib.error
from contextlib import redirect_stdout
from pathlib import Path

import pytest

_SKILLS_DIR = Path(__file__).resolve().parents[3] / ".agents" / "skills"


def _load_helper(skill: str, filename: str):
    path = _SKILLS_DIR / skill / "tools" / filename
    assert path.exists(), f"helper not materialized: {path}"
    spec = importlib.util.spec_from_file_location(
        f"{skill}_{filename.removesuffix('.py')}", path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def jira_helper():
    return _load_helper("jira-verify", "post_jira_comment.py")


@pytest.fixture()
def pr_helper():
    return _load_helper("jira-pr-verify", "post_pr_comment.py")


SECRET_CANARIES = [
    "ghp_abcDEF1234567890",
    "github_pat_abcDEF1234567890",
    "ATATT12345abcdef",
    "AIzaABCDEF1234567890",
    "AKIAIOSFODNN7EXAMPLE",
    "-----BEGIN RSA PRIVATE KEY-----",
    "token=supersecret",
    "Authorization: Bearer xyz",
]


@pytest.mark.parametrize("canary", SECRET_CANARIES)
def test_jira_helper_secret_canary_blocks_post(jira_helper, tmp_path, canary) -> None:
    body_file = tmp_path / "comment.md"
    body_file.write_text(f"Verification PASS for ENG-1\n{canary}\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        jira_helper._scan(body_file.read_text(encoding="utf-8"))


def test_jira_helper_clean_body_passes_scan(jira_helper) -> None:
    jira_helper._scan("Verification PASS for ENG-1: evidence table only.")


@pytest.mark.parametrize("canary", SECRET_CANARIES)
def test_pr_helper_secret_canary_blocks_dispatch(
    pr_helper, tmp_path, monkeypatch, canary
) -> None:
    body_file = tmp_path / "comment.md"
    body_file.write_text(f"Jira verification for ENG-1: **PASS**\n{canary}\n", encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        ["post_pr_comment.py", "--repo", "o/r", "--pr", "7", "--body-file", str(body_file)],
    )
    called = []
    monkeypatch.setattr(pr_helper.subprocess, "run", lambda *a, **k: called.append((a, k)))
    assert pr_helper.main() == 2
    assert called == []


def test_jira_helper_dispatches_trusted_tool_payload(
    jira_helper, tmp_path, monkeypatch
) -> None:
    body_file = tmp_path / "comment.md"
    body_file.write_text("Verification PASS for ENG-123.", encoding="utf-8")
    monkeypatch.setenv("MOONMIND_URL", "https://moonmind.test")
    for var in (
        "MOONMIND_AUTH_HEADER",
        "MOONMIND_API_TOKEN",
        "MOONMIND_AUTH_TOKEN",
        "MOONMIND_BEARER_TOKEN",
        "MOONMIND_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "post_jira_comment.py",
            "--issue",
            "eng-123",
            "--body-file",
            str(body_file),
        ],
    )
    captured: dict = {}

    class _FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b'{"commentId":"10001"}'

    def _fake_urlopen(request, timeout=60):
        captured["url"] = request.full_url
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return _FakeResponse()

    monkeypatch.setattr(jira_helper.urllib.request, "urlopen", _fake_urlopen)
    out = io.StringIO()
    with redirect_stdout(out):
        assert jira_helper.main() == 0
    assert captured["url"] == "https://moonmind.test/mcp/tools/call"
    assert captured["payload"]["tool"] == "jira.add_comment"
    assert captured["payload"]["arguments"]["issueKey"] == "ENG-123"
    assert captured["payload"]["arguments"]["body"] == "Verification PASS for ENG-123."
    assert "10001" in out.getvalue()


def test_jira_helper_provider_failure_does_not_claim_success(
    jira_helper, tmp_path, monkeypatch
) -> None:
    body_file = tmp_path / "comment.md"
    body_file.write_text("Verification PASS for ENG-123.", encoding="utf-8")
    monkeypatch.setenv("MOONMIND_URL", "https://moonmind.test")
    monkeypatch.setattr(
        sys,
        "argv",
        ["post_jira_comment.py", "--issue", "ENG-123", "--body-file", str(body_file)],
    )

    def _failing_urlopen(request, timeout=60):
        raise urllib.error.HTTPError(
            request.full_url, 500, "boom", {}, io.BytesIO(b"boom")
        )

    monkeypatch.setattr(jira_helper.urllib.request, "urlopen", _failing_urlopen)
    assert jira_helper.main() == 1


def test_pr_helper_dispatches_exact_gh_argv_and_propagates_failure(
    pr_helper, tmp_path, monkeypatch
) -> None:
    body_file = tmp_path / "comment.md"
    body_file.write_text("Jira verification for ENG-1 against PR 7: **PASS**", encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        ["post_pr_comment.py", "--repo", "o/r", "--pr", "7", "--body-file", str(body_file)],
    )
    seen: dict = {}

    class _Completed:
        stdout = "comment-url"
        stderr = "partial write error"
        returncode = 3

    def _fake_run(argv, **kwargs):
        seen["argv"] = argv
        return _Completed()

    monkeypatch.setattr(pr_helper.subprocess, "run", _fake_run)
    # Nonzero provider result propagates so the caller reports the mutation
    # failure separately instead of claiming the post succeeded.
    assert pr_helper.main() == 3
    assert seen["argv"] == [
        "gh",
        "pr",
        "comment",
        "7",
        "--repo",
        "o/r",
        "--body-file",
        str(body_file),
    ]
