"""Settings Operations API against the real standalone controller endpoint.

MoonLadderStudios/MoonMind#4502: the Operations router submits to and
observes the same ``deploy/controller`` operation the host entrypoint uses.
Temporal is stopped in every test here (any workflow creation fails the
test), and the controller runs as its real WSGI app on a loopback port with
a fake applier standing in for Docker.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4
from wsgiref.simple_server import WSGIRequestHandler, make_server

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_service.api.routers.deployment_operations import (
    _get_temporal_execution_service,
    router,
)
from api_service.auth_providers import get_current_user, get_current_user_optional

CONTROLLER_DIR = Path(__file__).resolve().parents[4] / "deploy" / "controller"
CONTROLLER_MODULES = ("redact", "mounts", "lock", "record", "engine", "server")
SECRET = "controller-test-secret"
IMAGE_A = "ghcr.io/moonladderstudios/moonmind:20260425.1234"
IMAGE_B = "ghcr.io/moonladderstudios/moonmind:stable"


class _TemporalStopped:
    """Execution service for a stack whose Temporal cluster is down."""

    async def create_execution(self, **_kwargs: object) -> object:
        raise AssertionError("an update must not create a MoonMind.UserWorkflow")

    async def list_executions(self, **_kwargs: object) -> object:
        raise ConnectionError("Temporal is stopped")


class _QuietHandler(WSGIRequestHandler):
    def log_message(self, *_args: object) -> None:
        return None


class _Controller:
    """The real deploy/controller endpoint on a loopback port."""

    def __init__(self, tmp_path: Path) -> None:
        for name in CONTROLLER_MODULES:
            sys.modules.pop(name, None)
        sys.path.insert(0, str(CONTROLLER_DIR))
        self.modules = {name: self._load(name) for name in CONTROLLER_MODULES}
        self.state_dir = tmp_path / "controller-state"
        self.store = self.modules["record"].OperationStore(self.state_dir)
        self.applied: list[str] = []
        self.behavior: Callable[[dict[str, Any]], None] = self.succeed
        app = self.modules["server"].build_app(
            store=self.store, secret=SECRET, applier=self._apply
        )
        self.httpd = make_server("127.0.0.1", 0, app, handler_class=_QuietHandler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @staticmethod
    def _load(name: str) -> Any:
        spec = importlib.util.spec_from_file_location(
            name, CONTROLLER_DIR / f"{name}.py"
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module

    def _apply(self, operation: dict[str, Any]) -> None:
        self.applied.append(operation["operationId"])
        self.behavior(operation)

    def succeed(self, operation: dict[str, Any]) -> None:
        self.store.confirm_installed(
            operation["operationId"], image=operation["desired"]["image"]
        )

    def fail(self, operation: dict[str, Any]) -> None:
        self.store.record_attempt_error(
            operation["operationId"],
            error=f"apply failed on attempt {len(self.applied)}",
        )
        raise self.modules["engine"].ApplyError("up", 1, "compose up failed")

    def stop(self) -> None:
        if self.thread.is_alive():
            self.httpd.shutdown()
            self.thread.join(timeout=10)
        self.httpd.server_close()

    def close(self) -> None:
        self.stop()
        sys.path.remove(str(CONTROLLER_DIR))
        for name in CONTROLLER_MODULES:
            sys.modules.pop(name, None)


@pytest.fixture
def controller(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[_Controller]:
    running = _Controller(tmp_path)
    monkeypatch.setenv("MOONMIND_CONTROLLER_URL", running.url)
    monkeypatch.setenv("MOONMIND_CONTROLLER_SECRET", SECRET)
    monkeypatch.setenv("MOONMIND_CONTROLLER_STATE_DIR", str(running.state_dir))
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    try:
        yield running
    finally:
        running.close()


def _client(*, is_superuser: bool = True) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    user = SimpleNamespace(
        id=uuid4(),
        email="operator@example.com",
        is_active=True,
        is_superuser=is_superuser,
    )
    for dependency in {
        dep.call
        for route in router.routes
        if route.dependant is not None
        for dep in route.dependant.dependencies
        if getattr(dep.call, "__name__", "")
        in {"_current_user_fallback", "_strict_current_user", "_optional_current_user"}
    } or {get_current_user(), get_current_user_optional()}:
        app.dependency_overrides[dependency] = lambda user=user: user
    app.dependency_overrides[_get_temporal_execution_service] = _TemporalStopped
    return TestClient(app)


def _update(client: TestClient, reference: str = "20260425.1234") -> Any:
    return client.post(
        "/api/v1/operations/deployment/update",
        json={
            "stack": "moonmind",
            "image": {
                "repository": "ghcr.io/moonladderstudios/moonmind",
                "reference": reference,
            },
            "mode": "changed_services",
            "reason": "Operator update",
        },
    )


def _actions(client: TestClient) -> list[dict[str, Any]]:
    response = client.get("/api/v1/operations/deployment/stacks/moonmind")
    assert response.status_code == 200, response.text
    return response.json()["recentActions"]


def test_router_submits_to_the_real_controller_with_temporal_stopped(
    controller: _Controller,
) -> None:
    response = _update(_client())

    assert response.status_code == 202, response.text
    payload = response.json()
    operation_id = payload["operationId"]
    assert controller.applied == [operation_id]
    # No workflow identity is manufactured for a local controller operation.
    assert payload["workflowId"] is None
    assert payload["taskId"] is None
    assert payload["status"] == "SUCCEEDED"
    recorded = controller.store.load(operation_id)
    assert recorded["desired"]["image"] == IMAGE_A
    assert recorded["installed"]["image"] == IMAGE_A


def test_api_replacement_reattaches_to_the_same_operation_without_reapplying(
    controller: _Controller,
) -> None:
    submitted = _update(_client()).json()["operationId"]

    # A fresh app/service instance stands in for an API container
    # replacement or a browser reload: it observes the same operation.
    actions = _actions(_client())
    assert [action["operationId"] for action in actions] == [submitted]
    action = actions[0]
    assert action["status"] == "SUCCEEDED"
    assert action["requestedImage"] == IMAGE_A
    assert action["installedImage"] == IMAGE_A
    assert action["runDetailUrl"] is None
    assert action["logsUrl"] == (
        f"/api/v1/operations/deployment/controller-operations/{submitted}/logs"
    )
    # A duplicate submission of the same target reattaches instead of
    # launching a second updater.
    duplicate = _update(_client()).json()["operationId"]
    assert duplicate == submitted
    assert controller.applied == [submitted]


def test_lost_acknowledgment_never_forks_a_second_updater(
    controller: _Controller, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "api_service.services.deployment_operations.CONTROLLER_SUBMIT_TIMEOUT_SECONDS",
        0.3,
    )
    finished = threading.Event()

    def slow_success(operation: dict[str, Any]) -> None:
        time.sleep(1.0)
        controller.succeed(operation)
        finished.set()

    controller.behavior = slow_success
    lost = _update(_client())

    assert lost.status_code == 504, lost.text
    detail = lost.json()["detail"]
    assert detail["code"] == "deployment_controller_ack_lost"
    assert finished.wait(timeout=10)
    # The still-running operation was not marked failed and no fallback
    # updater ran; resubmitting observes the one recorded operation.
    retried = _update(_client()).json()
    assert retried["status"] == "SUCCEEDED"
    assert controller.applied == [retried["operationId"]]


def test_explicit_retry_after_exhaustion_preserves_the_first_failure(
    controller: _Controller,
) -> None:
    controller.behavior = controller.fail
    client = _client()
    exhausted = _update(client)

    assert exhausted.status_code == 502, exhausted.text
    assert exhausted.json()["detail"]["code"] == "deployment_controller_failed"
    [failed] = _actions(client)
    assert failed["status"] == "FAILED"
    assert failed["retryable"] is True
    assert "attempt 1" in failed["errorSummary"]
    operation_id = failed["operationId"]

    controller.behavior = controller.succeed
    retried = client.post(
        f"/api/v1/operations/deployment/controller-operations/{operation_id}/retry"
    )

    assert retried.status_code == 202, retried.text
    assert retried.json()["operationId"] == operation_id
    assert retried.json()["status"] == "SUCCEEDED"
    [recovered] = _actions(client)
    assert recovered["status"] == "SUCCEEDED"
    assert recovered["retryable"] is False
    # The first failure survives the successful fresh attempt.
    assert "attempt 1" in recovered["errorSummary"]
    logs = client.get(
        f"/api/v1/operations/deployment/controller-operations/{operation_id}/logs"
    )
    assert logs.status_code == 200
    assert logs.json()["attempts"][0]["error"] == "apply failed on attempt 1"


def test_changed_target_is_explicit_new_intent(controller: _Controller) -> None:
    client = _client()
    first = _update(client).json()["operationId"]
    second = _update(client, reference="stable").json()["operationId"]

    assert first != second
    assert controller.applied == [first, second]
    assert controller.store.load(second)["desired"]["image"] == IMAGE_B


def test_failed_postcheck_and_history_upload_failure_are_truthful(
    controller: _Controller,
) -> None:
    def verified_with_gaps(operation: dict[str, Any]) -> None:
        controller.succeed(operation)
        controller.store.record_verification(
            operation["operationId"],
            name="operator_access",
            status="failed",
            detail="operator URL returned HTTP 502",
        )
        controller.store.note_reporting_failure(
            operation["operationId"], error="application history import failed"
        )

    controller.behavior = verified_with_gaps
    client = _client()
    response = _update(client)

    assert response.status_code == 202, response.text
    assert response.json()["status"] == "PARTIALLY_VERIFIED"
    [action] = _actions(client)
    # The confirmed installation is not erased by the failed postcheck or
    # the optional history upload failure; both remain visible.
    assert action["installedImage"] == IMAGE_A
    assert action["status"] == "PARTIALLY_VERIFIED"
    assert action["verification"] == [
        {
            "name": "operator_access",
            "status": "failed",
            "detail": "operator URL returned HTTP 502",
        }
    ]
    assert action["reportingFailures"] == ["application history import failed"]


def test_controller_unavailable_is_distinct_and_never_falls_back(
    controller: _Controller, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller.stop()
    response = _update(_client())

    assert response.status_code == 503, response.text
    assert response.json()["detail"]["code"] == "deployment_controller_unavailable"


def test_access_denied_is_distinct_and_exposes_no_credentials(
    controller: _Controller, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MOONMIND_CONTROLLER_SECRET", "stale-deployment-secret")
    response = _update(_client())

    assert response.status_code == 502, response.text
    assert response.json()["detail"]["code"] == "deployment_controller_access_denied"
    assert "stale-deployment-secret" not in response.text
    assert SECRET not in response.text
    assert controller.applied == []


def test_operator_admission_guards_retry_and_logs(controller: _Controller) -> None:
    operation_id = _update(_client()).json()["operationId"]
    viewer = _client(is_superuser=False)

    for response in (
        viewer.post(
            f"/api/v1/operations/deployment/controller-operations/{operation_id}/retry"
        ),
        viewer.get(
            f"/api/v1/operations/deployment/controller-operations/{operation_id}/logs"
        ),
    ):
        assert response.status_code == 403
    invalid = _client().get(
        "/api/v1/operations/deployment/controller-operations/..%2Fsecrets/logs"
    )
    assert invalid.status_code in (404, 422)


def test_unreadable_controller_keeps_the_last_known_state(
    controller: _Controller, monkeypatch: pytest.MonkeyPatch
) -> None:
    def never_finishes(operation: dict[str, Any]) -> None:
        controller.store.mark_stage(operation["operationId"], stage="applying")

    controller.behavior = never_finishes
    client = _client()
    assert _update(client).json()["status"] == "RUNNING"
    monkeypatch.setenv("MOONMIND_CONTROLLER_URL", "http://127.0.0.1:9")

    # An unavailable observer is not evidence of failure.
    [action] = _actions(client)
    assert action["status"] == "RUNNING"
    assert json.dumps(action).count("FAILED") == 0
