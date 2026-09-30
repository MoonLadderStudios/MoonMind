"""Settings Operations against the real standalone controller endpoint.

These tests run the actual Operations router against an in-process
``deploy/controller`` server (its production threaded HTTP server, bearer
check, operation store, and reattach rules) with an execution service that
fails if Temporal is touched. Only the Compose applier is replaced.
"""

from __future__ import annotations

import importlib
import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, Iterator
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_service.api.routers.deployment_operations import (
    _get_temporal_execution_service,
    router,
)
from api_service.auth_providers import get_current_user, get_current_user_optional
from api_service.services import deployment_operations as operations_service
from moonmind.workflows.skills.deployment_tools import (
    DEPLOYMENT_UPDATE_TOOL_NAME,
    DEPLOYMENT_UPDATE_TOOL_VERSION,
)

CONTROLLER_DIR = Path(__file__).resolve().parents[4] / "deploy" / "controller"
_CONTROLLER_MODULES = ("redact", "mounts", "lock", "record", "engine", "server")
SECRET = "controller-test-secret-value"
TARGET = {
    "project": "moonmind",
    "projectDir": "/srv/moonmind",
    "composeFiles": ["docker-compose.yaml"],
    "services": ["api"],
}
IMAGE_REPOSITORY = "ghcr.io/moonladderstudios/moonmind"


class _TemporalStopped:
    """An execution service whose every use fails like a stopped Temporal."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.execution_items: list[object] = []

    async def create_execution(self, **_kwargs: object) -> object:
        self.calls.append("create_execution")
        raise AssertionError("an update must not create a MoonMind.UserWorkflow")

    async def list_executions(self, **_kwargs: object) -> SimpleNamespace:
        if self.execution_items:
            return SimpleNamespace(items=self.execution_items)
        raise ConnectionError("Temporal is stopped")


class _Controller:
    def __init__(self, state_dir: Path, applier: Callable[[dict], object]) -> None:
        self.server = importlib.import_module("server")
        record = importlib.import_module("record")
        self.engine = importlib.import_module("engine")
        self.record = record
        self.store = record.OperationStore(state_dir)
        self.applied: list[str] = []

        def tracking_applier(operation: dict) -> object:
            self.applied.append(operation["operationId"])
            return applier(operation)

        app = self.server.build_app(
            store=self.store,
            secret=SECRET,
            applier=tracking_applier,
            target_resolver=lambda stack: dict(TARGET),
        )
        self.httpd = self.server.make_http_server("127.0.0.1", 0, app)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def close(self) -> None:
        if self.thread.is_alive():
            self.httpd.shutdown()
            self.thread.join(timeout=10)
        self.httpd.server_close()


@pytest.fixture
def controller_modules(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.syspath_prepend(str(CONTROLLER_DIR))
    for name in _CONTROLLER_MODULES:
        sys.modules.pop(name, None)
    yield
    for name in _CONTROLLER_MODULES:
        sys.modules.pop(name, None)


def _install_controller_state(state_dir: Path, *, port: int, secret: str) -> None:
    """Write what bootstrap install leaves in the deployment state mount."""
    (state_dir / "secrets").mkdir(parents=True, exist_ok=True)
    (state_dir / "secrets" / "controller-bearer").write_text(secret + "\n")
    (state_dir / "controller-identity.json").write_text(
        json.dumps({"project": "moonmind-controller-abc", "port": port})
    )
    (state_dir / "controller-image.json").write_text(
        json.dumps({"pinned": "ctl@sha256:" + "a" * 64, "verified": True})
    )


@pytest.fixture
def controller_factory(
    controller_modules: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Callable[..., _Controller]]:
    started: list[_Controller] = []
    state_dir = tmp_path / "controller-state"
    state_dir.mkdir()
    monkeypatch.setenv("MOONMIND_CONTROLLER_STATE_DIR", str(state_dir))
    monkeypatch.delenv("MOONMIND_CONTROLLER_SECRET", raising=False)
    monkeypatch.delenv("MOONMIND_CONTROLLER_SECRET_FILE", raising=False)

    def start(
        applier: Callable[[dict], object] | None = None,
        *,
        api_secret: str = SECRET,
    ) -> _Controller:
        holder: dict[str, _Controller] = {}

        def install(operation: dict) -> None:
            holder["controller"].store.confirm_installed(
                operation["operationId"], image=operation["desired"]["image"]
            )

        controller = _Controller(state_dir, applier or install)
        holder["controller"] = controller
        started.append(controller)
        _install_controller_state(state_dir, port=controller.port, secret=api_secret)
        # The alias resolves only on the deployment network; tests use the
        # published loopback endpoint the host entrypoint also uses.
        monkeypatch.setenv(
            "MOONMIND_CONTROLLER_URL", f"http://127.0.0.1:{controller.port}"
        )
        return controller

    yield start
    for controller in started:
        controller.close()


def _override_user(app: FastAPI, *, is_superuser: bool) -> None:
    user = SimpleNamespace(
        id=uuid4(),
        email="operator@example.com",
        is_active=True,
        is_superuser=is_superuser,
    )
    for dependency in {get_current_user(), get_current_user_optional()}:
        app.dependency_overrides[dependency] = lambda user=user: user
    for route in router.routes:
        for dep in getattr(route, "dependant", None).dependencies:
            if getattr(dep.call, "__name__", "") in {
                "_current_user_fallback",
                "_strict_current_user",
                "_optional_current_user",
            }:
                app.dependency_overrides[dep.call] = lambda user=user: user


def _client(*, is_superuser: bool = True) -> tuple[TestClient, _TemporalStopped]:
    app = FastAPI()
    app.include_router(router)
    _override_user(app, is_superuser=is_superuser)
    temporal = _TemporalStopped()
    app.dependency_overrides[_get_temporal_execution_service] = lambda: temporal
    return TestClient(app), temporal


def _update(reference: str = "sha256:" + "b" * 64) -> dict[str, object]:
    return {
        "stack": "moonmind",
        "image": {"repository": IMAGE_REPOSITORY, "reference": reference},
        "mode": "changed_services",
        "reason": "Operator update",
    }


def _desired(reference: str) -> str:
    separator = "@" if reference.startswith("sha256:") else ":"
    return f"{IMAGE_REPOSITORY}{separator}{reference}"


def _stack(client: TestClient) -> dict:
    response = client.get("/api/v1/operations/deployment/stacks/moonmind")
    assert response.status_code == 200, response.text
    return response.json()


def test_operations_router_submits_to_the_real_controller_with_temporal_stopped(
    controller_factory: Callable[..., _Controller],
) -> None:
    controller = controller_factory()
    client, temporal = _client()

    response = client.post("/api/v1/operations/deployment/update", json=_update())

    assert response.status_code == 202, response.text
    accepted = response.json()
    operation_id = accepted["operationId"]
    assert accepted["owner"] == "controller"
    assert accepted["status"] == "SUCCEEDED"
    # No workflow identity is manufactured for a local controller operation.
    assert accepted["workflowId"] is None
    assert accepted["taskId"] is None
    assert accepted["deploymentUpdateRunId"] == f"ctl-{operation_id}"
    assert controller.applied == [operation_id]
    assert temporal.calls == []
    recorded = controller.store.load(operation_id)
    assert recorded["desired"]["image"] == _desired("sha256:" + "b" * 64)
    # The controller derived the deployment target; the API sent none.
    assert recorded["target"] == TARGET

    state = _stack(client)
    assert state["controller"] == {
        "installed": True,
        "reachable": True,
        "message": None,
    }
    latest = state["latestAction"]
    assert latest["operationId"] == operation_id
    assert latest["owner"] == "controller"
    assert latest["status"] == "SUCCEEDED"
    assert latest["requestedImage"] == _desired("sha256:" + "b" * 64)
    assert latest["installedImage"] == _desired("sha256:" + "b" * 64)
    assert latest["runDetailUrl"] is None
    assert latest["logsArtifactUrl"] is None
    assert latest["rollbackEligibility"] is None
    assert SECRET not in response.text
    assert SECRET not in json.dumps(state)


def test_lost_acknowledgment_and_duplicates_keep_one_update_owner(
    controller_factory: Callable[..., _Controller],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = threading.Event()
    holder: dict[str, _Controller] = {}

    def slow_apply(operation: dict) -> None:
        store = holder["controller"].store
        store.mark_stage(operation["operationId"], stage="applying")
        assert release.wait(timeout=30)
        store.confirm_installed(
            operation["operationId"], image=operation["desired"]["image"]
        )

    controller = controller_factory(slow_apply)
    holder["controller"] = controller
    # The controller applies inside the submission request, so the API's
    # bounded wait ends first: exactly the lost-acknowledgment case.
    monkeypatch.setattr(operations_service, "CONTROLLER_SUBMIT_TIMEOUT_SECONDS", 0.5)
    client, temporal = _client()
    try:
        first = client.post("/api/v1/operations/deployment/update", json=_update())
        assert first.status_code == 202, first.text
        operation_id = first.json()["operationId"]
        assert first.json()["status"] == "RUNNING"

        # Browser refresh / double submit of the same target reattaches.
        again = client.post("/api/v1/operations/deployment/update", json=_update())
        assert again.status_code == 202, again.text
        assert again.json()["operationId"] == operation_id

        # A changed target is new intent, refused while the stack is owned.
        changed = client.post(
            "/api/v1/operations/deployment/update",
            json=_update("sha256:" + "c" * 64),
        )
        assert changed.status_code == 409, changed.text
        assert changed.json()["detail"]["code"] == "deployment_controller_busy"
        assert changed.json()["detail"]["activeOperationId"] == operation_id

        # A replacement API process (fresh service instance) reconnects to
        # the same in-progress operation through ordinary bounded reads.
        running = _stack(client)["latestAction"]
        assert (running["operationId"], running["status"]) == (operation_id, "RUNNING")
        assert running["completedAt"] is None
    finally:
        release.set()
    for _ in range(100):
        latest = _stack(client)["latestAction"]
        if latest["status"] == "SUCCEEDED":
            break
        threading.Event().wait(0.05)
    assert latest["operationId"] == operation_id
    assert latest["status"] == "SUCCEEDED"
    assert controller.applied == [operation_id]
    assert len(controller.store.list_open()) == 0
    assert len(controller.store.list_terminal()) == 1
    assert temporal.calls == []


def test_explicit_retry_after_exhaustion_preserves_the_first_failure(
    controller_factory: Callable[..., _Controller],
) -> None:
    holder: dict[str, object] = {"fail": True}

    def flaky_apply(operation: dict) -> None:
        controller = holder["controller"]
        if holder["fail"]:
            controller.store.record_attempt_error(
                operation["operationId"], error="pull failed: manifest unknown"
            )
            raise controller.engine.StageError("pull", 1, "manifest unknown")
        controller.store.confirm_installed(
            operation["operationId"], image=operation["desired"]["image"]
        )

    controller = controller_factory(flaky_apply)
    holder["controller"] = controller
    client, temporal = _client()

    submitted = client.post("/api/v1/operations/deployment/update", json=_update())
    assert submitted.status_code == 202, submitted.text
    operation_id = submitted.json()["operationId"]
    assert submitted.json()["status"] == "FAILED"

    failed = _stack(client)["latestAction"]
    assert failed["status"] == "FAILED"
    assert failed["retryAllowed"] is True
    assert failed["installedImage"] is None
    assert failed["errorSummary"].startswith("attempt 1: pull failed: manifest unknown")
    assert [attempt["attempt"] for attempt in failed["attempts"]] == [1, 2, 3]

    holder["fail"] = False
    retried = client.post(
        f"/api/v1/operations/deployment/operations/{operation_id}/retry"
    )
    assert retried.status_code == 202, retried.text
    assert retried.json()["operationId"] == operation_id
    assert retried.json()["status"] == "SUCCEEDED"

    recovered = _stack(client)["latestAction"]
    assert recovered["operationId"] == operation_id
    assert recovered["status"] == "SUCCEEDED"
    assert recovered["retryAllowed"] is False
    assert recovered["attemptGroup"] == 2
    # The original failure stays visible after the fresh attempt succeeds.
    assert recovered["errorSummary"].startswith("attempt 1: pull failed")
    assert temporal.calls == []


def test_failed_postcheck_is_partially_verified_with_the_failed_check(
    controller_factory: Callable[..., _Controller],
) -> None:
    holder: dict[str, _Controller] = {}

    def apply_with_failed_postcheck(operation: dict) -> None:
        store = holder["controller"].store
        store.confirm_installed(operation["operationId"], image=operation["desired"]["image"])
        store.record_verification(
            operation["operationId"],
            name="operator-access:http://127.0.0.1:1",
            status="failed",
            detail="Operator URL failed its health check",
        )

    holder["controller"] = controller_factory(apply_with_failed_postcheck)
    client, _temporal = _client()

    submitted = client.post("/api/v1/operations/deployment/update", json=_update())
    assert submitted.status_code == 202, submitted.text
    assert submitted.json()["status"] == "PARTIALLY_VERIFIED"
    latest = _stack(client)["latestAction"]
    assert latest["status"] == "PARTIALLY_VERIFIED"
    assert latest["installedImage"] == _desired("sha256:" + "b" * 64)
    assert latest["verification"] == [
        {
            "name": "operator-access:http://127.0.0.1:1",
            "status": "failed",
            "detail": "Operator URL failed its health check",
        }
    ]
    assert latest["retryAllowed"] is False


def test_unavailable_controller_is_reported_and_never_becomes_a_workflow(
    controller_factory: Callable[..., _Controller],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = controller_factory()
    controller.close()
    client, temporal = _client()

    response = client.post("/api/v1/operations/deployment/update", json=_update())

    assert response.status_code == 503, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "deployment_controller_unavailable"
    assert detail["operationId"]
    assert temporal.calls == []
    state = _stack(client)
    assert state["controller"]["installed"] is True
    assert state["controller"]["reachable"] is False
    assert "unreachable" in state["controller"]["message"]
    assert SECRET not in response.text


def test_controller_rejecting_the_api_credential_is_distinct_and_redacted(
    controller_factory: Callable[..., _Controller],
) -> None:
    controller_factory(api_secret="stale-api-secret-value")
    client, temporal = _client()

    response = client.post("/api/v1/operations/deployment/update", json=_update())

    assert response.status_code == 503, response.text
    assert response.json()["detail"]["code"] == "deployment_controller_access_denied"
    assert "stale-api-secret-value" not in response.text
    assert SECRET not in response.text
    assert temporal.calls == []


def test_non_admin_cannot_retry_a_controller_operation(
    controller_factory: Callable[..., _Controller],
) -> None:
    controller_factory()
    client, _temporal = _client(is_superuser=False)

    response = client.post("/api/v1/operations/deployment/operations/op-1/retry")

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "deployment_update_forbidden"


def test_retry_rejects_an_unsafe_operation_id_before_calling_the_controller(
    controller_factory: Callable[..., _Controller],
) -> None:
    controller = controller_factory()
    client, _temporal = _client()

    response = client.post(
        "/api/v1/operations/deployment/operations/op..%2F..%2Fsecrets/retry"
    )

    assert response.status_code in {404, 422}
    assert controller.applied == []


def test_historical_workflow_actions_remain_readable_beside_controller_operations(
    controller_factory: Callable[..., _Controller],
) -> None:
    controller_factory()
    client, temporal = _client()
    temporal.execution_items = [
        SimpleNamespace(
            workflow_id="mm:workflow-history",
            run_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            owner_id="admin@example.com",
            state="completed",
            close_status="completed",
            parameters={
                "workflow": {
                    "operation": {"kind": "update"},
                    "plan": [
                        {
                            "tool": {
                                "name": DEPLOYMENT_UPDATE_TOOL_NAME,
                                "version": DEPLOYMENT_UPDATE_TOOL_VERSION,
                            },
                            "inputs": {
                                "stack": "moonmind",
                                "image": {
                                    "repository": IMAGE_REPOSITORY,
                                    "reference": "20260425.1234",
                                },
                                "mode": "changed_services",
                            },
                        }
                    ],
                }
            },
            memo={},
            artifact_refs=["art_history"],
            started_at="2026-04-25T18:00:00Z",
            closed_at="2026-04-25T18:04:00Z",
        )
    ]
    submitted = client.post("/api/v1/operations/deployment/update", json=_update())
    assert submitted.status_code == 202, submitted.text

    actions = _stack(client)["recentActions"]
    assert [action["owner"] for action in actions] == ["controller", "workflow"]
    history = actions[1]
    assert history["runDetailUrl"] == "/workflows/mm:workflow-history"
    assert history["logsArtifactUrl"] == "/api/artifacts/art_history"
    assert history["operationId"] is None
    assert history["retryAllowed"] is False
    assert temporal.calls == []
