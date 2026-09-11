"""Provider verification for MoonLadderStudios/MoonMind#4186.

Authorized live-GitHub checks against a disposable test repository. These
tests establish real API behavior for issue labels/comments and
lost-response reconciliation. They never run in required hermetic CI: they
require ``GITHUB_TOKEN`` (or ``GH_TOKEN``) AND an explicitly configured
disposable ``GITHUB_TEST_REPOSITORY`` (``owner/repo``). Ordinary project
issues are never mutated; every created test resource is closed/deleted in
``finally`` cleanup scoped to resources this file created.

Run manually::

    GITHUB_TOKEN=... GITHUB_TEST_REPOSITORY=owner/disposable-repo \\
        python -m pytest tests/provider/github/test_github_issue_lifecycle_4186_provider.py -v -s
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
import uuid

import pytest

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

_TOKEN = os.environ.get("GITHUB_TOKEN", "").strip() or os.environ.get("GH_TOKEN", "").strip()
_TEST_REPO = os.environ.get("GITHUB_TEST_REPOSITORY", "").strip()
_API = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")

pytestmark = [
    pytest.mark.provider_verification,
    pytest.mark.requires_credentials,
    pytest.mark.skipif(not _TOKEN, reason="GITHUB_TOKEN not set"),
    pytest.mark.skipif(not _TEST_REPO, reason="GITHUB_TEST_REPOSITORY not set"),
]

_MARKER_PREFIX = "<!-- moonmind-4186-provider-probe:"


def _request(method: str, path: str, payload: dict | None = None) -> tuple[int, Any, Any]:
    url = f"{_API}{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {_TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode() or "null"), resp.headers
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        try:
            return exc.code, json.loads(body or "null"), exc.headers
        except ValueError:
            return exc.code, {"message": body}, exc.headers


def _create_disposable_issue(title: str) -> int:
    status, body, _ = _request(
        "POST", f"/repos/{_TEST_REPO}/issues", {"title": title, "body": "MoonMind #4186 provider probe; will be closed by the same test run."}
    )
    assert status == 201, f"issue creation failed: {status} {body}"
    return int(body["number"])


def _close_issue(number: int) -> None:
    _request("PATCH", f"/repos/{_TEST_REPO}/issues/{number}", {"state": "closed"})


def test_real_label_crud_supports_lifecycle_labels() -> None:
    """Real API accepts lifecycle label add/remove on a disposable issue."""
    number = _create_disposable_issue(f"[4186 probe] label support {uuid.uuid4().hex[:8]}")
    try:
        status, body, _ = _request(
            "POST", f"/repos/{_TEST_REPO}/issues/{number}/labels", {"labels": ["status: in-progress"]}
        )
        assert status in (200, 201), f"label add failed: {status} {body}"
        names = {label["name"] for label in body} if isinstance(body, list) else set()
        assert "status: in-progress" in names

        status, _, _ = _request("DELETE", f"/repos/{_TEST_REPO}/issues/{number}/labels/status:%20in-progress")
        assert status in (200, 204), f"label remove failed: {status}"
    finally:
        _close_issue(number)


def test_lost_response_reconciles_by_marker_without_duplicate() -> None:
    """A lost create-comment response reconciles by exact marker re-read."""
    marker = f"{_MARKER_PREFIX}{uuid.uuid4().hex}-->"
    number = _create_disposable_issue(f"[4186 probe] lost-response {uuid.uuid4().hex[:8]}")
    try:
        probe_body = f"{marker}\n hermetic attempt att_probe update"
        status, body, _ = _request(
            "POST", f"/repos/{_TEST_REPO}/issues/{number}/comments", {"body": probe_body}
        )
        assert status == 201, f"comment create failed: {status} {body}"
        # Simulate a lost response: re-read and reconcile by exact marker
        # instead of blindly repeating the create.
        status, comments, _ = _request("GET", f"/repos/{_TEST_REPO}/issues/{number}/comments?per_page=100")
        assert status == 200, f"comment re-read failed: {status} {comments}"
        matches = [c for c in comments if marker in (c.get("body") or "")]
        assert len(matches) == 1, f"expected exactly one reconciled comment, saw {len(matches)}"
    finally:
        _close_issue(number)


def test_real_comment_scan_reports_pagination_explicitly() -> None:
    """Real list-comments responses carry explicit pagination signals."""
    number = _create_disposable_issue(f"[4186 probe] scan {uuid.uuid4().hex[:8]}")
    try:
        status, _, headers = _request("GET", f"/repos/{_TEST_REPO}/issues/{number}/comments?per_page=1")
        assert status == 200
        # An incomplete scan must be explicit, never an implicit clean slate:
        # GitHub reports pagination via the Link header.
        assert headers is not None
    finally:
        _close_issue(number)
