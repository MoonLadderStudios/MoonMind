"""The single-user Compose journey cannot report failure as a pass (#4356)."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from tools import single_user_journey_checks as journey


class _Execution:
    def __init__(self, statuses: list[dict[str, Any]]) -> None:
        self.statuses = statuses
        self.reads = 0

    def next(self) -> dict[str, Any]:
        status = self.statuses[min(self.reads, len(self.statuses) - 1)]
        self.reads += 1
        return {"workflowId": "mm:journey", "runId": "run-1", **status}


@pytest.fixture
def api_server():
    execution = _Execution([])

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            return

        def _send(self, status: int, body: Any) -> None:
            payload = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            if self.path.startswith("/api/executions/"):
                self._send(200, execution.next())
            elif self.path == "/api/ui/info":
                self._send(
                    200,
                    {"dashboardConfig": {"system": {"defaultRepository": "o/r"}}},
                )
            else:
                self._send(200, {"sections": ["ok"]})

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            if self.path.endswith("/cancel"):
                self._send(202, execution.next())
            elif self.path == "/api/executions":
                self._send(201, {"workflowId": "mm:journey", "runId": "run-1"})
            else:
                self._send(201, {})

        def do_PATCH(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            self._send(200, {})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", execution
    finally:
        server.shutdown()


def test_execution_failure_before_cancellation_fails_populate(
    api_server, tmp_path, capsys
):
    base, execution = api_server
    execution.statuses = [
        {"status": "queued"},
        {"status": "failed", "closeStatus": "failed", "summary": "no adapter"},
    ]

    code = journey.main(
        [
            "populate",
            "--api-base",
            base,
            "--state-file",
            str(tmp_path / "state.json"),
            "--timeout",
            "20",
        ]
    )

    assert code == 1
    assert "failed before cancellation" in capsys.readouterr().err


def test_execution_that_fails_instead_of_canceling_fails_cancel(api_server, tmp_path):
    base, execution = api_server
    execution.statuses = [
        {"status": "running"},
        {"status": "failed", "closeStatus": "failed"},
    ]
    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps({"executions": [{"workflowId": "mm:journey"}]}))

    code = journey.main(
        [
            "cancel",
            "--api-base",
            base,
            "--state-file",
            str(state_file),
            "--timeout",
            "20",
        ]
    )

    assert code == 1


def test_canceled_execution_passes_cancel(api_server, tmp_path):
    base, execution = api_server
    execution.statuses = [
        {"status": "running"},
        {"status": "canceled", "closeStatus": "canceled"},
    ]
    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps({"executions": [{"workflowId": "mm:journey"}]}))

    code = journey.main(
        [
            "cancel",
            "--api-base",
            base,
            "--state-file",
            str(state_file),
            "--timeout",
            "20",
        ]
    )

    assert code == 0
    assert json.loads(state_file.read_text())["executions"][0]["canceled"] is True


def test_cancel_with_nothing_recorded_fails(api_server, tmp_path):
    base, _ = api_server
    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps({"executions": []}))

    code = journey.main(["cancel", "--api-base", base, "--state-file", str(state_file)])

    assert code == 1
