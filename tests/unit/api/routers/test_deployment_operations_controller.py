"""Settings Operations against the real standalone controller endpoint.

These tests run the actual Operations router against an in-process
``deploy/controller`` server (its production threaded HTTP server, bearer
check, operation store, and reattach rules) with an execution service that
fails if Temporal is touched. Only the Compose applier is replaced.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_service.api.routers.deployment_operations import (
    _get_temporal_execution_service,
    router,
)
from api_service.auth_providers import get_current_user, get_current_user_optional
from moonmind.workflows.skills import deployment_controller as controller_client
from moonmind.workflows.skills.deployment_tools import (
    DEPLOYMENT_UPDATE_TOOL_NAME,
    DEPLOYMENT_UPDATE_TOOL_VERSION,
)
from tests.support.deployment_controller import (
    DEFAULT_TARGET,
    InProcessController,
    forget_controller_modules,
    install_controller_state,
    load_controller_modules,
    slow_applier,
)

SECRET = "controller-test-secret-value"
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


@pytest.fixture
def controller_factory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Callable[..., InProcessController]]:
    load_controller_modules(monkeypatch)
    started: list[InProcessController] = []
    state_dir = tmp_path / "controller-state"
    state_dir.mkdir()
    monkeypatch.setenv("MOONMIND_CONTROLLER_STATE_DIR", str(state_dir))
    monkeypatch.delenv("MOONMIND_CONTROLLER_SECRET", raising=False)
    monkeypatch.delenv("MOONMIND_CONTROLLER_SECRET_FILE", raising=False)

    def start(
        applier: Callable[[InProcessController, dict], object] | None = None,
        *,
        api_secret: str = SECRET,
    ) -> InProcessController:
        controller = InProcessController(
            state_dir,
            secret=SECRET,
            applier=applier,
            target_resolver=lambda stack: dict(DEFAULT_TARGET),
        )
        started.append(controller)
        install_controller_state(state_dir, port=controller.port, secret=api_secret)
        # The alias resolves only on the deployment network; tests use the
        # published loopback endpoint the host entrypoint also uses.
        monkeypatch.setenv("MOONMIND_CONTROLLER_URL", controller.url)
        return controller

    yield start
    for controller in started:
        controller.close()
    forget_controller_modules()


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
    controller_factory: Callable[..., InProcessController],
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
    assert recorded["target"] == DEFAULT_TARGET

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
    controller_factory: Callable[..., InProcessController],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = threading.Event()
    controller = controller_factory(slow_applier(release))
    # The controller applies inside the submission request, so the API's
    # bounded wait ends first: exactly the lost-acknowledgment case.
    monkeypatch.setattr(controller_client, "CONTROLLER_SUBMIT_TIMEOUT_SECONDS", 0.5)
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
    controller_factory: Callable[..., InProcessController],
) -> None:
    holder = {"fail": True}

    def flaky_apply(controller: InProcessController, operation: dict) -> None:
        if holder["fail"]:
            controller.store.record_attempt_error(
                operation["operationId"], error="pull failed: manifest unknown"
            )
            raise controller.engine.StageError("pull", 1, "manifest unknown")
        controller.store.confirm_installed(
            operation["operationId"], image=operation["desired"]["image"]
        )

    controller_factory(flaky_apply)
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
    controller_factory: Callable[..., InProcessController],
) -> None:
    def apply_with_failed_postcheck(
        controller: InProcessController, operation: dict
    ) -> None:
        store = controller.store
        store.confirm_installed(
            operation["operationId"], image=operation["desired"]["image"]
        )
        store.record_verification(
            operation["operationId"],
            name="operator-access:http://127.0.0.1:1",
            status="failed",
            detail="Operator URL failed its health check",
        )

    controller_factory(apply_with_failed_postcheck)
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
    controller_factory: Callable[..., InProcessController],
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
    controller_factory: Callable[..., InProcessController],
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
    controller_factory: Callable[..., InProcessController],
) -> None:
    controller_factory()
    client, _temporal = _client(is_superuser=False)

    response = client.post("/api/v1/operations/deployment/operations/op-1/retry")

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "deployment_update_forbidden"


def test_retry_rejects_an_unsafe_operation_id_before_calling_the_controller(
    controller_factory: Callable[..., InProcessController],
) -> None:
    controller = controller_factory()
    client, _temporal = _client()

    response = client.post(
        "/api/v1/operations/deployment/operations/op..%2F..%2Fsecrets/retry"
    )

    assert response.status_code in {404, 422}
    assert controller.applied == []


def test_historical_workflow_actions_remain_readable_beside_controller_operations(
    controller_factory: Callable[..., InProcessController],
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


def test_controller_endpoint_is_resolved_only_from_an_installed_controller(
    tmp_path: Path,
) -> None:
    resolve = controller_client.resolve_controller_endpoint
    state = tmp_path / "state"
    env = {"MOONMIND_CONTROLLER_STATE_DIR": str(state)}
    assert resolve(env) is None
    (state / "secrets").mkdir(parents=True)
    (state / "secrets" / "controller-bearer").write_text("s3cret\n")
    # An install that could not pin its image left an identity only.
    (state / "controller-identity.json").write_text(
        json.dumps({"project": "moonmind-controller-x", "port": 8511, "alias": "evil"})
    )
    (state / "controller-image.json").write_text(json.dumps({"verified": False}))
    assert resolve(env) is None
    (state / "controller-image.json").write_text(json.dumps({"verified": True}))
    endpoint = resolve(env)
    # The host is the fixed network alias; a state file never redirects it.
    assert endpoint == controller_client.ControllerEndpoint(
        base_url="http://moonmind-controller:8511", secret="s3cret"
    )
    explicit = resolve({**env, "MOONMIND_CONTROLLER_URL": "http://controller.test:1/"})
    assert explicit.base_url == "http://controller.test:1"


def test_retry_of_a_non_failed_operation_is_refused_by_the_controller(
    controller_factory: Callable[..., InProcessController],
) -> None:
    controller = controller_factory()
    client, temporal = _client()
    submitted = client.post("/api/v1/operations/deployment/update", json=_update())
    assert submitted.status_code == 202, submitted.text
    operation_id = submitted.json()["operationId"]
    assert submitted.json()["status"] == "SUCCEEDED"

    retried = client.post(
        f"/api/v1/operations/deployment/operations/{operation_id}/retry"
    )
    assert retried.status_code == 409, retried.text
    assert retried.json()["detail"]["code"] == "deployment_controller_conflict"
    # The confirmed installation is never re-applied through Retry.
    assert controller.applied == [operation_id]
    assert controller.store.load(operation_id)["attemptGroup"] == 1
    assert temporal.calls == []


def test_current_image_is_the_newest_installation_the_controller_confirmed(
    controller_factory: Callable[..., InProcessController],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The transitional updater's last desired state names an older image.
    legacy_state = tmp_path / "desired-state.json"
    legacy_state.write_text(
        json.dumps(
            {
                "stack": "moonmind",
                "imageRepository": IMAGE_REPOSITORY,
                "requestedReference": "20260425.1234",
                "createdAt": "2026-04-25T18:04:00Z",
            }
        )
    )
    monkeypatch.setenv("MOONMIND_DEPLOYMENT_DESIRED_STATE_JSON_FILE", str(legacy_state))
    monkeypatch.delenv("MOONMIND_IMAGE", raising=False)
    monkeypatch.delenv("MOONMIND_IMAGE_REQUESTED", raising=False)
    installed = "sha256:" + "b" * 64
    broken = "sha256:" + "c" * 64

    def apply(controller: InProcessController, operation: dict) -> None:
        if operation["desired"]["image"] == _desired(broken):
            controller.store.record_attempt_error(
                operation["operationId"], error="pull failed: manifest unknown"
            )
            raise controller.engine.StageError("pull", 1, "manifest unknown")
        controller.store.confirm_installed(
            operation["operationId"], image=operation["desired"]["image"]
        )

    controller_factory(apply)
    client, _temporal = _client()

    first = client.post("/api/v1/operations/deployment/update", json=_update(installed))
    assert first.json()["status"] == "SUCCEEDED", first.text
    # A newer request that never installed must not become the current image.
    failed = client.post("/api/v1/operations/deployment/update", json=_update(broken))
    assert failed.json()["status"] == "FAILED", failed.text

    state = _stack(client)
    assert state["latestAction"]["status"] == "FAILED"
    current = state["currentImage"]
    assert current["evidence"] == "controller"
    assert current["deployedImage"] == _desired(installed)
    assert current["resolvedDigest"] == installed
    assert current["repository"] == IMAGE_REPOSITORY
