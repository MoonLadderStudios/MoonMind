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
        self.preset: dict[str, Any] = {}

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
            elif self.path.startswith("/api/presets/"):
                self._send(200, execution.preset)
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
    assert "closed before cancellation" in capsys.readouterr().err


FUTURE = "2999-01-01T00:00:00+00:00"


def _dashboard_canceled_state(state_file, **overrides):
    execution = {
        "workflowId": "mm:journey",
        "cancel": True,
        "scheduledFor": FUTURE,
        "cancelRequestedAt": "2026-01-01T00:00:00+00:00",
        **overrides,
    }
    state_file.write_text(json.dumps({"executions": [execution]}))


def _canceled(base, state_file):
    return journey.main(
        [
            "canceled",
            "--api-base",
            base,
            "--state-file",
            str(state_file),
            "--timeout",
            "20",
        ]
    )


def test_dashboard_cancel_that_fails_instead_fails_canceled(api_server, tmp_path):
    base, execution = api_server
    execution.statuses = [
        {"status": "running"},
        {"status": "failed", "closeStatus": "failed"},
    ]
    state_file = tmp_path / "state.json"
    _dashboard_canceled_state(state_file)

    assert _canceled(base, state_file) == 1


def test_dashboard_canceled_execution_passes_canceled(api_server, tmp_path):
    base, execution = api_server
    execution.statuses = [
        {"status": "running"},
        {"status": "canceled", "closeStatus": "canceled"},
    ]
    state_file = tmp_path / "state.json"
    _dashboard_canceled_state(state_file)

    assert _canceled(base, state_file) == 0
    assert json.loads(state_file.read_text())["executions"][0]["canceled"] is True


def test_canceled_without_a_dashboard_cancel_fails(api_server, tmp_path, capsys):
    """Work canceled some other way does not prove the dashboard action."""
    base, execution = api_server
    execution.statuses = [{"status": "canceled", "closeStatus": "canceled"}]
    state_file = tmp_path / "state.json"
    _dashboard_canceled_state(state_file, cancelRequestedAt=None)

    assert _canceled(base, state_file) == 1
    assert "not canceled through the dashboard" in capsys.readouterr().err


def test_canceled_with_nothing_recorded_fails(api_server, tmp_path):
    base, _ = api_server
    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps({"executions": []}))

    assert _canceled(base, state_file) == 1


def test_dashboard_cancel_after_the_deferred_start_fails(api_server, tmp_path):
    """Canceling work that may already have run would not prove cancellation."""
    base, execution = api_server
    execution.statuses = [{"status": "canceled", "closeStatus": "canceled"}]
    state_file = tmp_path / "state.json"
    _dashboard_canceled_state(
        state_file,
        scheduledFor="2026-01-01T00:00:00+00:00",
        cancelRequestedAt="2026-01-01T00:00:05+00:00",
    )

    assert _canceled(base, state_file) == 1


def test_preset_read_back_requires_the_saved_version(api_server, tmp_path):
    base, execution = api_server
    saved = {
        "slug": "journey-preset",
        "scope": "personal",
        "scopeRef": None,
        "title": "single-user journey preset",
        "presetDigest": "sha256:saved",
        "steps": [{"instructions": "Say hello."}],
    }
    api = journey.Api(base)
    state = {"preset": journey.preset_identity(saved)}

    execution.preset = dict(saved)
    journey.verify_preset(api, state)

    execution.preset = {**saved, "presetDigest": "sha256:changed"}
    with pytest.raises(journey.JourneyFailure, match="preset journey-preset changed"):
        journey.verify_preset(api, state)


GUARD_ELIGIBLE = (
    "Single-user conversion guard: disposition=eligible_conversion "
    "reason=single_operator (no further detail)"
)


def _conversion(tmp_path, log_text):
    api_log = tmp_path / "api.log"
    api_log.write_text(log_text)
    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps({}))
    code = journey.main(
        [
            "conversion",
            "--api-base",
            "http://127.0.0.1:9",
            "--state-file",
            str(state_file),
            "--api-log",
            str(api_log),
        ]
    )
    return code, json.loads(state_file.read_text())


def test_published_account_era_conversion_passes(tmp_path):
    code, state = _conversion(
        tmp_path,
        "api-1 | INFO Single-user guarded upgrade published (eligible_conversion)\n",
    )

    assert code == 0
    assert state["conversion"] == {
        "outcome": "published",
        "disposition": "eligible_conversion",
    }


def test_account_era_source_that_converts_as_fresh_fails(tmp_path):
    code, _ = _conversion(
        tmp_path, "Single-user guarded upgrade published (fresh_init)\n"
    )

    assert code == 1


def test_missing_transform_coverage_is_recorded_as_unpublished(tmp_path, capsys):
    code, state = _conversion(
        tmp_path,
        "Single-user guarded upgrade blocked (missing_transform_coverage: "
        "retained subsystems lack registered transforms: schedules,temporal); "
        "preserving source data, serving release, and operator access without "
        "conversion-side mutation.\n" + GUARD_ELIGIBLE + "\n",
    )

    assert code == 0
    assert state["conversion"] == {
        "outcome": "blocked",
        "reason": "missing_transform_coverage",
        "pendingSubsystems": ["schedules", "temporal"],
    }
    assert "NOT published" in capsys.readouterr().out


def test_missing_coverage_without_single_operator_attribution_fails(tmp_path):
    code, _ = _conversion(
        tmp_path,
        "Single-user guarded upgrade blocked (missing_transform_coverage: "
        "retained subsystems lack registered transforms: temporal); ...\n",
    )

    assert code == 1


@pytest.mark.parametrize(
    "line",
    [
        (
            "Single-user guarded upgrade blocked (multi_person: retained data is "
            "attributable to more than one person); preserving ..."
        ),
        (
            "Single-user guarded upgrade blocked (unowned_rows: unowned retained "
            "rows lack deployment-owned evidence); preserving ..."
        ),
        "Single-user guarded upgrade deferred: StaleAttributionError",
        "Application startup events completed.",
    ],
)
def test_refused_deferred_or_unobserved_conversion_fails(tmp_path, line):
    code, _ = _conversion(tmp_path, line + "\n" + GUARD_ELIGIBLE + "\n")

    assert code == 1


def test_latest_startup_outcome_wins(tmp_path):
    """An API restart after a deferred attempt reports its own outcome."""
    code, state = _conversion(
        tmp_path,
        "Single-user guarded upgrade deferred: OperationalError\n"
        "Single-user guarded upgrade published (eligible_conversion)\n",
    )

    assert code == 0
    assert state["conversion"]["outcome"] == "published"


def test_api_bypasses_egress_proxy_for_loopback(
    api_server, tmp_path, monkeypatch
):
    """The disposable journey always targets the local stack directly.

    Container-job environments export an egress proxy; routing loopback
    journey traffic through it can only fail. The journey helper must not
    honor proxy variables for its own API base.
    """
    base, _ = api_server
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9/")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9/")
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9/")
    monkeypatch.setenv("https_proxy", "http://127.0.0.1:9/")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)

    info = journey.Api(base).json("GET", "/api/ui/info")
    assert info["dashboardConfig"]["system"]["defaultRepository"] == "o/r"


STACK_PATH = "/api/v1/operations/deployment/stacks/moonmind"
REPOSITORY = "ghcr.io/moonladderstudios/moonmind"


class _Operations:
    """Settings Operations and execution reads for the controller journey."""

    def __init__(self) -> None:
        self.update_status = 503
        self.update_response: dict[str, Any] = {
            "detail": {
                "code": "deployment_controller_not_installed",
                "message": "The standalone deployment controller is not installed.",
                "repairCommand": "./tools/update-moonmind.sh",
            }
        }
        self.execution: dict[str, Any] = {"workflowId": "mm:legacy", "status": "canceled"}
        self.controller: dict[str, Any] = {
            "installed": False,
            "reachable": False,
            "message": "Run the host update command (./tools/update-moonmind.sh).",
        }
        self.actions: list[dict[str, Any]] = []
        self.requests: list[tuple[str, str]] = []

    def stack(self) -> dict[str, Any]:
        return {
            "stack": "moonmind",
            "controller": self.controller,
            "recentActions": self.actions,
        }


def _history_row(status: str = "CANCELED") -> dict[str, Any]:
    return {
        "id": "depupd_run1",
        "owner": "workflow",
        "status": status,
        "requestedImage": f"{REPOSITORY}:journey-history",
        "runDetailUrl": "/workflows/mm:legacy",
        "operationId": None,
    }


def _controller_row(**overrides: Any) -> dict[str, Any]:
    return {
        "id": "ctl-ui-1",
        "owner": "controller",
        "operationId": "ui-1",
        "status": "FAILED",
        "requestedImage": f"{REPOSITORY}:journey-controller",
        "installedImage": None,
        "errorSummary": "attempt 1: staging failed (latest attempt 6: staging failed)",
        "attempts": [{"attempt": 1, "error": "staging failed", "at": None}],
        "attemptGroup": 2,
        "retryAllowed": True,
        "runDetailUrl": None,
        **overrides,
    }


@pytest.fixture
def operations_api():
    operations = _Operations()

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
            operations.requests.append(("GET", self.path))
            if self.path == STACK_PATH:
                self._send(200, operations.stack())
            elif self.path.startswith("/api/v1/operations/deployment/image-targets"):
                self._send(200, {"repositories": [{"repository": REPOSITORY}]})
            elif self.path.startswith("/api/executions/"):
                self._send(200, operations.execution)
            else:
                self._send(404, {})

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            operations.requests.append(("POST", self.path))
            if self.path == "/api/v1/operations/deployment/update":
                self._send(operations.update_status, operations.update_response)
            elif self.path.endswith("/cancel"):
                self._send(202, operations.execution)
            else:
                self._send(404, {})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", operations
    finally:
        server.shutdown()


def _phase(phase, base, state_file):
    return journey.main(
        [phase, "--api-base", base, "--state-file", str(state_file), "--timeout", "5"]
    )


def test_controller_absent_refuses_the_dashboard_update_with_the_repair_route(
    operations_api, tmp_path
):
    base, operations = operations_api
    operations.actions = [_history_row()]
    state_file = tmp_path / "state.json"

    assert _phase("controller_absent", base, state_file) == 0
    state = json.loads(state_file.read_text())
    # Pre-existing workflow-backed history stays readable and is recorded.
    assert state["workflowHistory"] == ["/workflows/mm:legacy"]
    assert state["controller"]["repository"] == REPOSITORY
    assert not any(path.endswith("/cancel") for _, path in operations.requests)


def test_controller_absent_update_accepted_by_a_workflow_fails(
    operations_api, tmp_path, capsys
):
    base, operations = operations_api
    operations.update_status = 202
    operations.update_response = {
        "deploymentUpdateRunId": "depupd_run1",
        "workflowId": "mm:legacy",
        "owner": "workflow",
        "status": "QUEUED",
    }
    state_file = tmp_path / "state.json"

    assert _phase("controller_absent", base, state_file) == 1
    assert "workflow" in capsys.readouterr().err


def test_controller_absent_without_the_repair_route_fails(
    operations_api, tmp_path, capsys
):
    base, operations = operations_api
    operations.update_response = {
        "detail": {"code": "deployment_controller_unavailable", "message": "down"}
    }
    state_file = tmp_path / "state.json"

    assert _phase("controller_absent", base, state_file) == 1
    assert "repair route" in capsys.readouterr().err


def test_controller_absent_when_a_controller_is_installed_fails(
    operations_api, tmp_path, capsys
):
    base, operations = operations_api
    operations.controller = {"installed": True, "reachable": True}
    state_file = tmp_path / "state.json"

    assert _phase("controller_absent", base, state_file) == 1
    assert "already installed" in capsys.readouterr().err


def _dashboard_controller_state(state_file, **controller: Any) -> None:
    state_file.write_text(
        json.dumps(
            {
                "workflowHistory": ["/workflows/mm:legacy"],
                "controller": {
                    "repository": REPOSITORY,
                    "reference": "journey-controller",
                    "operationId": "ui-1",
                    "reloadedOperationId": "ui-1",
                    "dashboardSubmissions": 1,
                    "submission": {
                        "owner": "controller",
                        "operationId": "ui-1",
                        "workflowId": None,
                        "taskId": None,
                    },
                    "retry": {
                        "owner": "controller",
                        "operationId": "ui-1",
                        "workflowId": None,
                        "taskId": None,
                    },
                    **controller,
                },
            }
        )
    )


def _installed(operations, *actions):
    operations.controller = {"installed": True, "reachable": True}
    operations.actions = list(actions)


def test_controller_journey_with_one_owner_and_the_first_failure_passes(
    operations_api, tmp_path
):
    base, operations = operations_api
    _installed(operations, _controller_row(), _history_row())
    state_file = tmp_path / "state.json"
    _dashboard_controller_state(state_file)

    assert _phase("controller", base, state_file) == 0


def test_second_controller_operation_is_a_second_mutation_owner(
    operations_api, tmp_path, capsys
):
    base, operations = operations_api
    _installed(
        operations,
        _controller_row(operationId="ui-2", id="ctl-ui-2"),
        _controller_row(),
        _history_row(),
    )
    state_file = tmp_path / "state.json"
    _dashboard_controller_state(state_file)

    assert _phase("controller", base, state_file) == 1
    assert "one mutation owner" in capsys.readouterr().err


def test_retry_that_lost_the_first_failure_fails(operations_api, tmp_path, capsys):
    base, operations = operations_api
    _installed(
        operations,
        _controller_row(errorSummary="attempt 4: staging failed"),
        _history_row(),
    )
    state_file = tmp_path / "state.json"
    _dashboard_controller_state(state_file)

    assert _phase("controller", base, state_file) == 1
    assert "first failure" in capsys.readouterr().err


def test_retry_without_a_fresh_attempt_group_fails(operations_api, tmp_path):
    base, operations = operations_api
    _installed(operations, _controller_row(attemptGroup=1), _history_row())
    state_file = tmp_path / "state.json"
    _dashboard_controller_state(state_file)

    assert _phase("controller", base, state_file) == 1


def test_controller_submission_that_created_a_workflow_fails(
    operations_api, tmp_path, capsys
):
    base, operations = operations_api
    _installed(operations, _controller_row(), _history_row())
    state_file = tmp_path / "state.json"
    _dashboard_controller_state(
        state_file,
        submission={
            "owner": "controller",
            "operationId": "ui-1",
            "workflowId": "mm:revived",
            "taskId": "mm:revived",
        },
    )

    assert _phase("controller", base, state_file) == 1
    assert "workflow" in capsys.readouterr().err


def test_new_workflow_backed_update_after_the_controller_fails(
    operations_api, tmp_path, capsys
):
    base, operations = operations_api
    revived = {**_history_row("RUNNING"), "id": "depupd_run2", "runDetailUrl": "/workflows/mm:new"}
    _installed(operations, _controller_row(), revived, _history_row())
    state_file = tmp_path / "state.json"
    _dashboard_controller_state(state_file)

    assert _phase("controller", base, state_file) == 1
    assert "workflow-backed" in capsys.readouterr().err


def test_reload_that_did_not_reconnect_fails(operations_api, tmp_path, capsys):
    base, operations = operations_api
    _installed(operations, _controller_row(), _history_row())
    state_file = tmp_path / "state.json"
    _dashboard_controller_state(state_file, reloadedOperationId=None)

    assert _phase("controller", base, state_file) == 1
    assert "reload" in capsys.readouterr().err


def test_dashboard_that_submitted_again_after_reconnecting_fails(
    operations_api, tmp_path, capsys
):
    base, operations = operations_api
    _installed(operations, _controller_row(), _history_row())
    state_file = tmp_path / "state.json"
    _dashboard_controller_state(state_file, dashboardSubmissions=2)

    assert _phase("controller", base, state_file) == 1
    assert "submitted 2 updates" in capsys.readouterr().err
