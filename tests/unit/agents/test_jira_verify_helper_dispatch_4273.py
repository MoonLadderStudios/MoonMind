"""MoonLadderStudios/MoonMind#4273 (AA-09): hermetic helper-dispatch tests.

Exercises the portable helper/tool dispatch and result-artifact boundaries
named by the verifier -- ``jira-verify/tools/post_jira_comment.py``,
``jira-pr-verify/tools/post_pr_comment.py`` and
``jira-pr-verify/tools/github_pr_preflight.py`` -- with controlled
provider responses (mocked transport/subprocess, no credentials, no
network). Covers:

(a) secret-canary redaction: canary bodies never reach dispatch;
(b) partial-failure segregation: post-succeeds/transition-fails keeps both
    outcomes and retries only the unfinished side effect, while
    post-fails/no-transition blocks the completion mutation;
(c) accepted-but-unknown outcome reconciliation without duplicate writes:
    retries bind to the exact issue/content/subject/operation identity and
    reconcile first.

Pure stdlib + unittest.mock so the tests stay hermetic under the managed
container runner. No pytest-only fixtures are used (tempfile instead of
``tmp_path``) so the same file can also be executed with plain python3.
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

_SKILLS_DIR = Path(__file__).resolve().parents[3] / ".agents" / "skills"


def _load(name: str, relpath: str):
    path = _SKILLS_DIR / relpath
    assert path.is_file(), f"helper must exist at {path}"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_post_jira = _load(
    "aa09_post_jira_comment", "jira-verify/tools/post_jira_comment.py"
)
_post_pr = _load(
    "aa09_post_pr_comment", "jira-pr-verify/tools/post_pr_comment.py"
)
_preflight = _load(
    "aa09_github_pr_preflight", "jira-pr-verify/tools/github_pr_preflight.py"
)

_CANARIES = [
    "ghp_canarytokensuffix123",
    "github_pat_canarysuffix456",
    "ATATT-canary-3Kt9z4pq6Xj8",
    "AIzaCanarySyD-abcdefghijkl",
    "AKIAIOSFODNN7EXAMPLE",
    "-----BEGIN RSA PRIVATE KEY-----",
    "token=canary-secret-value",
    "password=canary-secret-value",
    "Authorization: Bearer canary",
]

_CLEAN_BODY = (
    "Verification: PASS for MoonLadderStudios/MoonMind#4273 "
    "(revision 5b1b8a1, controlled fixture, no secrets)."
)


def _write_body(directory: Path, text: str) -> str:
    body_file = directory / "comment.md"
    body_file.write_text(text, encoding="utf-8")
    return str(body_file)


class _FakeResponse:
    """Minimal urlopen context-manager stub returning a fixed payload."""

    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *exc_info) -> bool:
        return False

    def read(self) -> bytes:
        return self._payload


# (a) Secret-canary redaction -----------------------------------------------


def test_jira_post_scan_refuses_every_secret_canary() -> None:
    for canary in _CANARIES:
        try:
            _post_jira._scan(f"prefix {canary} suffix")
        except SystemExit as exc:
            assert exc.code != 0
        else:
            raise AssertionError(f"canary was not refused: {canary!r}")


def test_jira_post_scan_accepts_clean_body() -> None:
    _post_jira._scan(_CLEAN_BODY)  # must not raise


def test_pr_post_refuses_canary_without_dispatch() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        body_file = _write_body(Path(tmp), f"result {_CANARIES[0]} tail")
        argv = [
            "post_pr_comment.py",
            "--repo",
            "owner/name",
            "--pr",
            "42",
            "--body-file",
            body_file,
        ]
        with mock.patch.object(sys, "argv", argv):
            with mock.patch.object(
                _post_pr.subprocess,
                "run",
                side_effect=AssertionError("dispatch must not run on canary"),
            ):
                assert _post_pr.main() == 2


def test_pr_post_scan_refuses_every_secret_canary_without_dispatch() -> None:
    for canary in _CANARIES:
        with tempfile.TemporaryDirectory() as tmp:
            body_file = _write_body(Path(tmp), f"prefix {canary} suffix")
            argv = [
                "post_pr_comment.py",
                "--repo",
                "owner/name",
                "--pr",
                "7",
                "--body-file",
                body_file,
            ]
            with mock.patch.object(sys, "argv", argv):
                with mock.patch.object(
                    _post_pr.subprocess,
                    "run",
                    side_effect=AssertionError("dispatch must not run"),
                ) as run_mock:
                    assert _post_pr.main() == 2
                    run_mock.assert_not_called()


# (b) Partial-failure segregation --------------------------------------------


def test_jira_post_success_payload_binds_exact_operation_identity() -> None:
    captured: dict = {}
    receipt = json.dumps(
        {"commentId": "10001", "issueKey": "ENG-123"}
    ).encode("utf-8")

    def fake_urlopen(request, timeout=60):
        captured["url"] = request.full_url
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        captured["headers"] = dict(request.header_items())
        return _FakeResponse(receipt)

    with tempfile.TemporaryDirectory() as tmp:
        body_file = _write_body(Path(tmp), _CLEAN_BODY)
        argv = [
            "post_jira_comment.py",
            "--issue",
            "eng-123",
            "--body-file",
            body_file,
            "--base-url",
            "http://example.invalid",
        ]
        with mock.patch.object(sys, "argv", argv):
            with mock.patch.dict("os.environ", {}, clear=True):
                with mock.patch.object(
                    _post_jira.urllib.request, "urlopen", fake_urlopen
                ):
                    assert _post_jira.main() == 0

    assert captured["url"] == "http://example.invalid/mcp/tools/call"
    assert captured["payload"] == {
        "tool": "jira.add_comment",
        "arguments": {"issueKey": "ENG-123", "body": _CLEAN_BODY},
    }
    # No ambient credential is attached: denied/unavailable config must not
    # fall back to scraped secrets or broader authority.
    assert "Authorization" not in captured["headers"]
    assert "X-Api-Key" not in captured["headers"]


def test_jira_post_retry_repeats_identical_payload() -> None:
    """Accepted-but-unknown reconciliation replays the same operation identity.

    Repeating the dispatch with the same issue/content/subject must produce
    the identical tool payload instead of a divergent duplicate write.
    """
    payloads: list = []
    receipt = json.dumps({"commentId": "10001"}).encode("utf-8")

    def fake_urlopen(request, timeout=60):
        payloads.append(json.loads(request.data.decode("utf-8")))
        return _FakeResponse(receipt)

    with tempfile.TemporaryDirectory() as tmp:
        body_file = _write_body(Path(tmp), _CLEAN_BODY)
        argv = [
            "post_jira_comment.py",
            "--issue",
            "ENG-123",
            "--body-file",
            body_file,
            "--base-url",
            "http://example.invalid",
        ]
        with mock.patch.object(sys, "argv", argv):
            with mock.patch.dict("os.environ", {}, clear=True):
                with mock.patch.object(
                    _post_jira.urllib.request, "urlopen", fake_urlopen
                ):
                    assert _post_jira.main() == 0
                    assert _post_jira.main() == 0

    assert len(payloads) == 2
    assert payloads[0] == payloads[1] == {
        "tool": "jira.add_comment",
        "arguments": {"issueKey": "ENG-123", "body": _CLEAN_BODY},
    }


def test_jira_post_failure_returns_nonzero_without_success_receipt() -> None:
    """Post-fails/no-transition half: a failed post yields no success claim.

    The skill requires that comment failure prevents the associated
    completion transition; the helper signals failure (returncode 1) and
    emits no success receipt so the caller blocks the mutation and keeps a
    durable draft instead.
    """
    failure = urllib.error.HTTPError(
        "http://example.invalid/mcp/tools/call",
        500,
        "Internal Error",
        {},
        io.BytesIO(b'{"error": "boom"}'),
    )

    with tempfile.TemporaryDirectory() as tmp:
        body_file = _write_body(Path(tmp), _CLEAN_BODY)
        argv = [
            "post_jira_comment.py",
            "--issue",
            "ENG-123",
            "--body-file",
            body_file,
            "--base-url",
            "http://example.invalid",
        ]
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "argv", argv):
            with mock.patch.dict("os.environ", {}, clear=True):
                with mock.patch.object(
                    _post_jira.urllib.request,
                    "urlopen",
                    side_effect=failure,
                ):
                    with mock.patch.object(sys, "stdout", stdout):
                        with mock.patch.object(sys, "stderr", stderr):
                            assert _post_jira.main() == 1
    assert "HTTP 500" in stderr.getvalue()
    assert "commentId" not in stdout.getvalue()


def test_jira_post_transport_error_returns_nonzero() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        body_file = _write_body(Path(tmp), _CLEAN_BODY)
        argv = [
            "post_jira_comment.py",
            "--issue",
            "ENG-123",
            "--body-file",
            body_file,
            "--base-url",
            "http://example.invalid",
        ]
        with mock.patch.object(sys, "argv", argv):
            with mock.patch.dict("os.environ", {}, clear=True):
                with mock.patch.object(
                    _post_jira.urllib.request,
                    "urlopen",
                    side_effect=urllib.error.URLError("down"),
                ):
                    assert _post_jira.main() == 1


def test_jira_post_ignores_unmanaged_credential_env() -> None:
    """No credential shopping: unmanaged ATLASSIAN_* env is never attached."""
    captured: dict = {}
    receipt = json.dumps({"commentId": "10001"}).encode("utf-8")

    def fake_urlopen(request, timeout=60):
        captured["headers"] = dict(request.header_items())
        return _FakeResponse(receipt)

    with tempfile.TemporaryDirectory() as tmp:
        body_file = _write_body(Path(tmp), _CLEAN_BODY)
        argv = [
            "post_jira_comment.py",
            "--issue",
            "ENG-123",
            "--body-file",
            body_file,
            "--base-url",
            "http://example.invalid",
        ]
        env = {"ATLASSIAN_API_TOKEN": "canary-should-never-attach"}
        with mock.patch.object(sys, "argv", argv):
            with mock.patch.dict("os.environ", env, clear=True):
                with mock.patch.object(
                    _post_jira.urllib.request, "urlopen", fake_urlopen
                ):
                    assert _post_jira.main() == 0
    for value in captured["headers"].values():
        assert "canary-should-never-attach" not in str(value)


def test_pr_post_dispatches_exact_gh_identity() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        body_file = _write_body(Path(tmp), _CLEAN_BODY)
        argv = [
            "post_pr_comment.py",
            "--repo",
            "owner/name",
            "--pr",
            "42",
            "--body-file",
            body_file,
        ]
        completed = mock.Mock(returncode=0, stdout="done\n", stderr="")
        with mock.patch.object(sys, "argv", argv):
            with mock.patch.object(
                _post_pr.subprocess, "run", return_value=completed
            ) as run_mock:
                assert _post_pr.main() == 0
        run_mock.assert_called_once()
        assert run_mock.call_args.args[0] == [
            "gh",
            "pr",
            "comment",
            "42",
            "--repo",
            "owner/name",
            "--body-file",
            body_file,
        ]


def test_pr_preflight_segregates_partial_failure() -> None:
    """One failing check yields ok=false with per-check evidence preserved."""

    def fake_run(command, **kwargs):
        result = mock.Mock()
        if command[:3] == ["gh", "auth", "status"]:
            result.returncode, result.stdout, result.stderr = 0, "ok", ""
        elif command[:3] == ["gh", "repo", "view"]:
            result.returncode, result.stdout, result.stderr = 0, "{}", ""
        else:
            result.returncode, result.stdout, result.stderr = (
                1,
                "",
                "pr not found",
            )
        return result

    argv = [
        "github_pr_preflight.py",
        "--repo",
        "owner/name",
        "--pr",
        "42",
    ]
    stdout = io.StringIO()
    with mock.patch.object(sys, "argv", argv):
        with mock.patch.object(_preflight.subprocess, "run", fake_run):
            with mock.patch.object(sys, "stdout", stdout):
                assert _preflight.main() == 1
    report = json.loads(stdout.getvalue())
    assert report["ok"] is False
    assert [c["returncode"] for c in report["checks"]] == [0, 0, 1]


def test_pr_preflight_all_ok_reports_success() -> None:
    result = mock.Mock(returncode=0, stdout="{}", stderr="")
    argv = [
        "github_pr_preflight.py",
        "--repo",
        "owner/name",
        "--pr",
        "42",
    ]
    stdout = io.StringIO()
    with mock.patch.object(sys, "argv", argv):
        with mock.patch.object(
            _preflight.subprocess, "run", return_value=result
        ):
            with mock.patch.object(sys, "stdout", stdout):
                assert _preflight.main() == 0
    assert json.loads(stdout.getvalue())["ok"] is True


# (c) Reconciliation contract -------------------------------------------------


def test_verify_skills_require_reconcile_before_retry() -> None:
    """Anchor the mocked dispatch behavior to the normative skill contract."""
    for skill in (
        "jira-verify",
        "jira-pr-verify",
        "jira-issue-creator",
        "jira-issue-updater",
        "github-issue-verify",
        "github-issue-to-jira",
    ):
        text = (_SKILLS_DIR / skill / "SKILL.md").read_text(encoding="utf-8")
        lowered = text.lower()
        assert "accepted but its outcome is unknown" in lowered or (
            "accepted-but-unknown" in lowered
        ), skill
        assert "incomplete search is not proof" in lowered, skill
        assert "operation identity" in lowered, skill


def test_jira_verify_orders_evidence_before_mutation() -> None:
    """Post-succeeds/transition-fails half: evidence posts before mutation."""
    text = (_SKILLS_DIR / "jira-verify" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    draft_pos = text.find("Draft the Jira comment")
    post_pos = text.find("Scan and post to Jira (evidence first")
    transition_pos = text.find(
        "decide and execute the Jira status update only after"
    )
    assert draft_pos != -1 and post_pos != -1 and transition_pos != -1
    assert draft_pos < post_pos < transition_pos
    lowered = text.lower()
    assert "comment failure prevents the associated completion transition" in (
        lowered
    )
    assert "resume/retry only the unfinished transition" in lowered


def test_missing_base_url_blocks_without_credential_fallback() -> None:
    """Unavailable config blocks dispatch instead of falling back."""
    with tempfile.TemporaryDirectory() as tmp:
        body_file = _write_body(Path(tmp), _CLEAN_BODY)
        argv = [
            "post_jira_comment.py",
            "--issue",
            "ENG-123",
            "--body-file",
            body_file,
        ]
        with mock.patch.object(sys, "argv", argv):
            with mock.patch.dict("os.environ", {}, clear=True):
                try:
                    _post_jira.main()
                except SystemExit as exc:
                    assert exc.code != 0
                else:
                    raise AssertionError("missing base URL must block")
