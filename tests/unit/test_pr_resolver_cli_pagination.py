"""Portable current-head CI reads through the supported GitHub CLI boundary."""

from __future__ import annotations

import json
import runpy
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def snapshot_module():
    return runpy.run_path(
        str(REPO_ROOT / ".agents/skills/pr-resolver/bin/pr_resolve_snapshot.py")
    )


def test_snapshot_fields_are_supported_by_installed_gh(snapshot_module, tmp_path):
    """Validate CLI parsing with a fixture credential and an unreachable loopback."""
    import os
    import shutil
    import subprocess

    if not shutil.which("gh"):
        pytest.skip("GitHub CLI is absent")
    env = {**os.environ, "GH_CONFIG_DIR": str(tmp_path / "isolated-gh")}
    for name in (
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "GH_ENTERPRISE_TOKEN",
        "GITHUB_ENTERPRISE_TOKEN",
    ):
        env.pop(name, None)
    env.update(GH_HOST="127.0.0.1:1", GH_ENTERPRISE_TOKEN="isolated-fixture-token")
    completed = subprocess.run(
        [
            "gh",
            "pr",
            "view",
            "1",
            "--repo",
            "fixture/repo",
            "--json",
            snapshot_module["_PR_VIEW_FIELDS"],
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert "Unknown JSON field" not in completed.stderr, completed.stderr


def _legacy_cli_replay(tmp_path, monkeypatch, pages):
    gh = tmp_path / "gh"
    gh.write_text(
        f"#!{sys.executable}\n"
        "import sys\n"
        "if '--slurp' in sys.argv: sys.exit('unknown flag: --slurp')\n"
        f"print({pages!r})\n"
    )
    gh.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))


def test_current_head_check_runs_read_every_object_page_without_slurp(
    snapshot_module, tmp_path, monkeypatch
):
    checks = [
        {"id": 1, "name": "PR Preflight", "status": "queued"},
        {"id": 2, "name": "Unreal", "status": "completed", "conclusion": "failure"},
    ]
    _legacy_cli_replay(
        tmp_path,
        monkeypatch,
        "\n".join(json.dumps({"check_runs": [check]}) for check in checks),
    )

    assert (
        snapshot_module["_fetch_commit_check_runs"](
            pr_repo="MoonLadderStudios/Tactics", commit_sha="candidate"
        )
        == checks
    )


def test_current_head_statuses_read_every_array_page_and_keep_newest_context(
    snapshot_module, tmp_path, monkeypatch
):
    newest = {"id": 3, "context": "GitBook", "state": "pending"}
    other = {"id": 2, "context": "required", "state": "failure"}
    older = {"id": 1, "context": "GitBook", "state": "success"}
    _legacy_cli_replay(
        tmp_path,
        monkeypatch,
        json.dumps([newest]) + "\n" + json.dumps([other, older]),
    )

    assert snapshot_module["_fetch_commit_statuses"](
        pr_repo="MoonLadderStudios/Tactics", commit_sha="candidate"
    ) == [newest, other]


@pytest.mark.parametrize(
    "fetcher,pages",
    [
        ("_fetch_commit_check_runs", '{"check_runs": []}\n{"check_runs": []}'),
        ("_fetch_commit_statuses", "[]\n[]"),
    ],
)
def test_current_head_empty_pages_are_confirmed_empty(
    snapshot_module, tmp_path, monkeypatch, fetcher, pages
):
    _legacy_cli_replay(tmp_path, monkeypatch, pages)
    assert snapshot_module[fetcher](pr_repo="owner/repo", commit_sha="head") == []


@pytest.mark.parametrize(
    "fetcher,pages",
    [
        ("_fetch_commit_check_runs", '{"check_runs": []}\n{"check_runs": null}'),
        ("_fetch_commit_check_runs", '{"check_runs": []}\n{"wrong_key": []}'),
        ("_fetch_commit_check_runs", '{"check_runs": []}\n{"check_runs": [null]}'),
        ("_fetch_commit_statuses", "[]\n[null]"),
        ("_fetch_commit_statuses", "[]\n{}"),
        ("_fetch_commit_statuses", "[]\ntruncated"),
        ("_fetch_commit_statuses", ""),
    ],
)
def test_current_head_incomplete_or_malformed_pages_remain_unavailable(
    snapshot_module, tmp_path, monkeypatch, fetcher, pages
):
    _legacy_cli_replay(tmp_path, monkeypatch, pages)
    assert snapshot_module[fetcher](pr_repo="owner/repo", commit_sha="head") is None


@pytest.mark.parametrize("records_key", ["check_runs", None])
def test_current_head_ci_collection_with_real_gh_link_pagination(
    snapshot_module, monkeypatch, records_key
):
    """Exercise both API response shapes against credential-free loopback fixtures."""
    import shutil
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    if not shutil.which("gh"):
        pytest.skip("GitHub CLI is absent; the hermetic legacy CLI replay is required")
    records = [
        {"id": 1, "name": "PR Preflight", "context": "preflight", "state": "pending"},
        {"id": 2, "name": "Unreal", "context": "unreal", "state": "failure"},
    ]
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            first = "page=2" not in self.path
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            if first:
                self.send_header(
                    "Link",
                    f'<http://127.0.0.1:{self.server.server_port}/ci?page=2>; rel="next"',
                )
            self.end_headers()
            page = [records[0 if first else 1]]
            self.wfile.write(
                json.dumps({records_key: page} if records_key else page).encode()
            )

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    fetcher = "_fetch_commit_check_runs" if records_key else "_fetch_commit_statuses"
    scope = snapshot_module[fetcher].__globals__
    resolve = scope["_resolve_command"]

    def route(command):
        return resolve([*command[:-1], f"http://127.0.0.1:{server.server_port}/ci"])

    monkeypatch.setitem(scope, "_resolve_command", route)
    monkeypatch.setenv("GH_TOKEN", "isolated-fixture-token")
    try:
        assert (
            snapshot_module[fetcher](pr_repo="fixture/repo", commit_sha="head")
            == records
        )
        assert len(requests) == 2
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
