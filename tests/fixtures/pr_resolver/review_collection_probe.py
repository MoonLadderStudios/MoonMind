"""Exercise portable review collection with real gh and loopback-only fixtures."""

import json
import os
import runpy
import subprocess
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

root = Path(__file__).resolve().parents[3]
fixture = json.loads(
    (root / "tests/fixtures/pr_resolver/review_wait_833.json").read_text()
)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        first = "page=2" not in self.path
        if first:
            self.send_header(
                "Link",
                f'<http://127.0.0.1:{self.server.server_port}/reviews?page=2>; rel="next"',
            )
        self.end_headers()
        self.wfile.write(
            json.dumps(
                [
                    {
                        **fixture["reviews"][0],
                        "id": 1,
                        "commit_id": "0" * 40,
                        "submitted_at": "2026-09-11T00:00:00Z",
                    }
                ]
                if first
                else fixture["reviews"]
            ).encode()
        )

    def log_message(self, *a):
        pass


server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
os.environ["GH_TOKEN"] = "isolated-fixture-token"
m = runpy.run_path(str(root / ".agents/skills/pr-resolver/bin/pr_resolve_snapshot.py"))
fn = m["build_automated_review_evidence"]
g = fn.__globals__
resolve = g["_resolve_command"]


def route(cmd):
    return resolve([*cmd[:-1], f"http://127.0.0.1:{server.server_port}/reviews"])


g["_resolve_command"] = route
try:
    result = fn(
        provider="codex",
        require_fresh_review=True,
        pr_repo="fixture/repo",
        pr_number=833,
        head_sha=fixture["headSha"],
        comments=fixture["comments"],
        head_committed_at=datetime.fromisoformat(fixture["headCommittedAt"]),
        reactions_for_request=[],
        reactions_for_pr=[],
    )
    assert result["freshReviewForHead"] and not result["requestPending"], result
    print(subprocess.check_output(["gh", "--version"], text=True).splitlines()[0])
    print(
        json.dumps(
            {
                "freshReviewForHead": result["freshReviewForHead"],
                "requestPending": result["requestPending"],
                "completionId": result["completionId"],
            }
        )
    )
finally:
    server.shutdown()
