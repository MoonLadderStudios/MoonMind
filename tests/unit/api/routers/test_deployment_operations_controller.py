"""Operations API against the real standalone controller (MoonLadderStudios/MoonMind#4502).

These tests run the shipped ``deploy/controller`` WSGI app in-process and
drive the actual Operations router against it. The Temporal execution
service fails if touched, so every assertion proves the update is a
controller operation rather than a ``MoonMind.UserWorkflow``.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Iterator
from uuid import uuid4
from wsgiref.simple_server import make_server

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_service.api.routers.deployment_operations import (
    _get_temporal_execution_service,
    router,
)
from api_service.auth_providers import get_current_user, get_current_user_optional

CONTROLLER_DIR = Path(__file__).resolve().parents[4] / "deploy" / "controller"
_CONTROLLER_MODULES = ("redact", "mounts", "lock", "record", "engine", "server")
SECRET = "controller-test-secret-value"


class _TemporalStopped:
    """Execution service standing in for a stopped Temporal: never usable."""

    async def create_execution(self, **_kwargs: object) -> object:
        raise AssertionError("deployment update must not create a workflow")

    async def list_executions(self, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(items=[])


class _Controller:
    def __init__(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        *,
        mounted_state: Path | None = None,
    ) -> None:
        monkeypatch.syspath_prepend(str(CONTROLLER_DIR))
        for name in _CONTROLLER_MODULES:
            sys.modules.pop(name, None)
        import engine  # type: ignore[import-not-found]
        import record  # type: ignore[import-not-found]
        import server  # type: ignore[import-not-found]

        self.engine = engine
        self.record = record
        self.applied: list[str] = []
        self.behavior: Callable[[dict[str, Any]], None] = self._succeed
        if mounted_state is None:
            self.store = record.OperationStore(tmp_path / "controller-state")
            app = server.build_app(store=self.store, secret=SECRET, applier=self._apply)
            self.httpd = make_server(
                "127.0.0.1", 0, app, server_class=server.ThreadingWSGIServer
            )
            self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
            monkeypatch.setenv("MOONMIND_CONTROLLER_URL", self.url)
            monkeypatch.setenv("MOONMIND_CONTROLLER_SECRET", SECRET)
        else:
            # The host bootstrap's layout: secret and record in the
            # controller's own state directory, served on its socket.
            secret_file = mounted_state / "secrets" / "controller-bearer"
            secret_file.parent.mkdir(parents=True)
            secret_file.write_text(SECRET + "\n")
            secret_file.chmod(0o600)
            self.store = record.OperationStore(mounted_state)
            app = server.build_app(
                store=self.store, secret_file=str(secret_file), applier=self._apply
            )
            self.httpd = server.make_unix_server(mounted_state, app)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def _apply(self, operation: dict[str, Any]) -> None:
        self.applied.append(operation["operationId"])
        self.behavior(operation)

    def _succeed(self, operation: dict[str, Any]) -> None:
        self.store.confirm_installed(
            operation["operationId"], image=operation["desired"]["image"]
        )

    def fail(self, operation: dict[str, Any]) -> None:
        self.store.record_attempt_error(
            operation["operationId"],
            error=f"pull failed for attempt {len(self.applied)} token=abc123",
        )
        raise self.engine.StageError("pull", 1, "registry refused")

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=10)
        for name in _CONTROLLER_MODULES:
            sys.modules.pop(name, None)


def _app(*, is_superuser: bool) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    user = SimpleNamespace(
        id=uuid4(),
        email="operator@example.com",
        is_active=True,
        is_superuser=is_superuser,
    )
    dependencies = {
        dep.call
        for route in router.routes
        if route.dependant is not None
        for dep in route.dependant.dependencies
        if getattr(dep.call, "__name__", "")
        in {"_current_user_fallback", "_strict_current_user", "_optional_current_user"}
    } or {get_current_user(), get_current_user_optional()}
    for dependency in dependencies:
        app.dependency_overrides[dependency] = lambda user=user: user
    app.dependency_overrides[_get_temporal_execution_service] = _TemporalStopped
    return app


@pytest.fixture
def controller(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[_Controller]:
    harness = _Controller(tmp_path, monkeypatch)
    try:
        yield harness
    finally:
        harness.close()


@pytest.fixture
def admin() -> Iterator[TestClient]:
    with TestClient(_app(is_superuser=True)) as client:
        yield client


def _payload(reference: str = "20260425.1234") -> dict[str, object]:
    return {
        "stack": "moonmind",
        "image": {
            "repository": "ghcr.io/moonladderstudios/moonmind",
            "reference": reference,
        },
        "mode": "changed_services",
        "reason": "Routine release",
    }


def _submit(client: TestClient, reference: str = "20260425.1234"):
    return client.post("/api/v1/operations/deployment/update", json=_payload(reference))


def _assert_no_secret(response) -> None:
    assert SECRET not in response.text


def test_router_submits_to_the_real_controller_and_observes_the_same_operation(
    controller: _Controller, admin: TestClient
) -> None:
    response = _submit(admin)

    assert response.status_code == 202, response.text
    body = response.json()
    operation_id = body["operationId"]
    assert controller.applied == [operation_id]
    assert body["owner"] == "controller"
    assert body["status"] == "SUCCEEDED"
    assert body["desiredImage"] == "ghcr.io/moonladderstudios/moonmind:20260425.1234"
    assert body["installedImage"] == body["desiredImage"]
    # No workflow identity is manufactured for a local controller operation.
    assert body.get("workflowId") is None
    assert body.get("taskId") is None
    _assert_no_secret(response)

    # A fresh request (as after API replacement or a browser reload)
    # reconnects to the same durable controller operation.
    state = admin.get("/api/v1/operations/deployment/stacks/moonmind")
    assert state.status_code == 200, state.text
    latest = state.json()["latestAction"]
    assert latest["operationId"] == operation_id
    assert latest["owner"] == "controller"
    assert latest["status"] == "SUCCEEDED"
    assert latest["requestedImage"] == body["desiredImage"]
    assert latest["installedImage"] == body["desiredImage"]
    assert latest["runDetailUrl"] is None
    assert latest["logsArtifactUrl"] is None
    assert state.json()["controllerAvailability"] == "available"

    detail = admin.get(f"/api/v1/operations/deployment/operations/{operation_id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["operation"]["operationId"] == operation_id
    _assert_no_secret(detail)


def test_lost_acknowledgment_reattaches_without_a_second_updater(
    controller: _Controller, admin: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "api_service.services.deployment_operations.CONTROLLER_SUBMIT_TIMEOUT_SECONDS",
        1,
    )
    started = threading.Event()
    release = threading.Event()

    def slow(operation: dict[str, Any]) -> None:
        controller.store.mark_stage(operation["operationId"], stage="applying")
        started.set()
        assert release.wait(timeout=20)
        controller._succeed(operation)

    controller.behavior = slow
    try:
        response = _submit(admin)
        assert started.is_set()
        assert response.status_code == 202, response.text
        body = response.json()
        # The client timed out, but the controller's operation identity is
        # recovered instead of forking a workflow or reporting failure.
        assert body["status"] == "RUNNING"
        assert body["operationId"] == controller.applied[0]

        duplicate = _submit(admin)
        assert duplicate.status_code == 202, duplicate.text
        assert duplicate.json()["operationId"] == body["operationId"]

        changed = _submit(admin, reference="stable")
        assert changed.status_code == 409, changed.text
        assert changed.json()["detail"]["code"] == "deployment_controller_conflict"

        running = admin.get("/api/v1/operations/deployment/stacks/moonmind").json()
        assert running["latestAction"]["operationId"] == body["operationId"]
        assert running["latestAction"]["status"] == "RUNNING"
    finally:
        release.set()
    assert len(controller.applied) == 1


def test_explicit_retry_after_exhaustion_preserves_the_first_failure(
    controller: _Controller, admin: TestClient
) -> None:
    controller.behavior = controller.fail
    failed = _submit(admin)

    assert failed.status_code == 502, failed.text
    assert failed.json()["detail"]["code"] == "deployment_controller_failed"
    assert len(controller.applied) == controller.record.MAX_AUTO_ATTEMPTS
    state = admin.get("/api/v1/operations/deployment/stacks/moonmind").json()
    latest = state["latestAction"]
    assert latest["status"] == "FAILED"
    assert latest["retryable"] is True
    assert "attempt 1" in latest["originalError"]
    assert "abc123" not in json.dumps(state)
    operation_id = latest["operationId"]

    controller.behavior = controller._succeed
    retried = admin.post(
        f"/api/v1/operations/deployment/operations/{operation_id}/retry"
    )
    assert retried.status_code == 202, retried.text
    assert retried.json()["operationId"] == operation_id
    assert retried.json()["status"] == "SUCCEEDED"

    detail = admin.get(f"/api/v1/operations/deployment/operations/{operation_id}")
    logs = detail.json()["logs"]
    assert logs["attempts"][0]["attempt"] == 1
    assert "pull failed for attempt 1" in logs["attempts"][0]["error"]
    assert detail.json()["operation"]["originalError"].startswith(
        "pull failed for attempt 1"
    )
    assert "abc123" not in detail.text


def test_retry_requires_an_administrator(controller: _Controller) -> None:
    with TestClient(_app(is_superuser=False)) as client:
        response = client.post("/api/v1/operations/deployment/operations/some-op/retry")
    assert response.status_code == 403
    assert controller.applied == []


def test_failed_postcheck_and_history_reporting_failure_stay_distinct(
    controller: _Controller, admin: TestClient
) -> None:
    def installed_with_gaps(operation: dict[str, Any]) -> None:
        controller._succeed(operation)
        controller.store.record_verification(
            operation["operationId"],
            name="operator-access",
            status="failed",
            detail="http://localhost:5000 failed its health check",
        )
        controller.store.note_reporting_failure(
            operation["operationId"], error="application history upload failed"
        )

    controller.behavior = installed_with_gaps
    response = _submit(admin)

    assert response.status_code == 202, response.text
    body = response.json()
    # The confirmed installation survives: a failed postcheck is a
    # verification gap and a reporting failure never rewrites the outcome.
    assert body["status"] == "PARTIALLY_VERIFIED"
    assert body["installedImage"] == body["desiredImage"]
    detail = admin.get(
        f"/api/v1/operations/deployment/operations/{body['operationId']}"
    ).json()
    assert [check["status"] for check in detail["operation"]["verification"]] == [
        "failed"
    ]
    assert detail["logs"]["reportingFailures"] == ["application history upload failed"]


def test_installed_but_unreachable_controller_is_reported_without_fallback(
    admin: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MOONMIND_CONTROLLER_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("MOONMIND_CONTROLLER_SECRET", SECRET)

    response = _submit(admin)

    assert response.status_code == 503, response.text
    assert response.json()["detail"]["code"] == "deployment_controller_unavailable"
    _assert_no_secret(response)
    state = admin.get("/api/v1/operations/deployment/stacks/moonmind")
    assert state.status_code == 200
    assert state.json()["controllerAvailability"] == "unavailable"


def test_controller_access_denied_is_distinct_and_redacted(
    controller: _Controller, admin: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MOONMIND_CONTROLLER_SECRET", "wrong-deployment-secret")

    response = _submit(admin)

    assert response.status_code == 502, response.text
    assert response.json()["detail"]["code"] == "deployment_controller_access_denied"
    assert "wrong-deployment-secret" not in response.text
    assert controller.applied == []


def test_unknown_operation_reads_are_not_found(
    controller: _Controller, admin: TestClient
) -> None:
    response = admin.get("/api/v1/operations/deployment/operations/missing-op")
    assert response.status_code == 404
    unsafe = admin.get("/api/v1/operations/deployment/operations/..%2Fescape")
    assert unsafe.status_code in (404, 422)


def _clear_controller_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "MOONMIND_CONTROLLER_URL",
        "MOONMIND_CONTROLLER_SECRET",
        "MOONMIND_CONTROLLER_SECRET_FILE",
        "MOONMIND_DEPLOYMENT_DESIRED_STATE_JSON_FILE",
    ):
        monkeypatch.delenv(name, raising=False)


def _api_container(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Lay out the default API container: /app CWD, ./deploy/state mounted.

    docker-compose.yaml mounts the checkout's ``deploy/state`` at
    ``/workspace/deployment_state`` and points the desired-state sidecar
    there; nothing else tells the API where the controller lives.
    """
    _clear_controller_environment(monkeypatch)
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    monkeypatch.chdir(app_dir)
    mounted = tmp_path / "deployment_state"
    mounted.mkdir()
    monkeypatch.setenv(
        "MOONMIND_DEPLOYMENT_DESIRED_STATE_JSON_FILE",
        str(mounted / "desired-state.json"),
    )
    return mounted / "controller"


def test_api_container_reaches_the_installed_controller_through_mounted_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, admin: TestClient
) -> None:
    controller_state = _api_container(tmp_path, monkeypatch)
    harness = _Controller(tmp_path, monkeypatch, mounted_state=controller_state)
    try:
        response = _submit(admin)

        assert response.status_code == 202, response.text
        body = response.json()
        assert body["owner"] == "controller"
        assert body["workflowId"] is None
        assert body["operationId"] == harness.applied[0]
        _assert_no_secret(response)
        state = admin.get("/api/v1/operations/deployment/stacks/moonmind")
        assert state.status_code == 200, state.text
        assert state.json()["controllerAvailability"] == "available"
        assert state.json()["latestAction"]["operationId"] == body["operationId"]
        _assert_no_secret(state)
    finally:
        harness.close()


def test_installed_controller_with_an_unreadable_credential_never_forks_a_workflow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, admin: TestClient
) -> None:
    if os.geteuid() == 0:
        pytest.skip("root reads any file; the permission boundary is not observable")
    controller_state = _api_container(tmp_path, monkeypatch)
    harness = _Controller(tmp_path, monkeypatch, mounted_state=controller_state)
    secret_file = controller_state / "secrets" / "controller-bearer"
    secret_file.chmod(0)
    try:
        response = _submit(admin)

        assert response.status_code == 502, response.text
        assert (
            response.json()["detail"]["code"] == "deployment_controller_access_denied"
        )
        assert "controller-bearer" in response.json()["detail"]["message"]
        assert harness.applied == []
        state = admin.get("/api/v1/operations/deployment/stacks/moonmind")
        assert state.json()["controllerAvailability"] == "unavailable"
    finally:
        secret_file.chmod(0o600)
        harness.close()


def test_controller_endpoint_and_secret_derive_from_the_bootstrap_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from api_service.services.deployment_operations import (
        controller_base_url,
        controller_secret,
    )

    _clear_controller_environment(monkeypatch)
    monkeypatch.chdir(tmp_path)
    assert controller_secret() is None
    assert controller_base_url() == "http://127.0.0.1:8472"

    state = tmp_path / "deploy" / "state" / "controller"
    (state / "secrets").mkdir(parents=True)
    (state / "secrets" / "controller-bearer").write_text(SECRET + "\n")
    (state / "controller-identity.json").write_text(
        json.dumps({"project": "moonmind-controller-abc", "port": 8533})
    )
    assert controller_secret() == SECRET
    assert controller_base_url() == "http://127.0.0.1:8533"
    monkeypatch.setenv("MOONMIND_CONTROLLER_URL", "http://controller.internal:9000/")
    assert controller_base_url() == "http://controller.internal:9000"
