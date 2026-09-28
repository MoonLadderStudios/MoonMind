from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Iterator
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_service.api.routers.deployment_operations import (
    _get_deployment_service,
    _get_temporal_execution_service,
    router,
)
from api_service.auth_providers import get_current_user, get_current_user_optional
from api_service.services.deployment_operations import (
    DeploymentOperationsService,
    DeploymentRecentAction,
    RollbackEligibilityDecision,
    RollbackImageTarget,
)
from moonmind.config.settings import settings
from moonmind.workflows.skills.deployment_tools import (
    DEPLOYMENT_UPDATE_TOOL_NAME,
    DEPLOYMENT_UPDATE_TOOL_VERSION,
)


class _FakeExecutionRecord:
    workflow_id = "mm:deployment-update"
    run_id = "11111111-2222-3333-4444-555555555555"


class _FakeExecutionService:
    def __init__(self) -> None:
        self.requests: list[dict[str, object]] = []
        self.execution_items: list[object] = []

    async def create_execution(self, **kwargs: object) -> _FakeExecutionRecord:
        self.requests.append(kwargs)
        return _FakeExecutionRecord()

    async def list_executions(self, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(items=self.execution_items)


def _override_user(app: FastAPI, *, is_superuser: bool) -> None:
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
        if getattr(dep.call, "__name__", "") in {
            "_current_user_fallback",
            "_strict_current_user",
            "_optional_current_user",
        }
    } or {get_current_user(), get_current_user_optional()}
    for dependency in dependencies:
        app.dependency_overrides[dependency] = lambda user=user: user


def _override_execution_service(app: FastAPI) -> _FakeExecutionService:
    service = _FakeExecutionService()
    app.dependency_overrides[_get_temporal_execution_service] = lambda: service
    return service


def _override_deployment_service(
    app: FastAPI, service: DeploymentOperationsService
) -> None:
    app.dependency_overrides[_get_deployment_service] = lambda: service


@pytest.fixture
def admin_client() -> Iterator[tuple[TestClient, _FakeExecutionService]]:
    app = FastAPI()
    app.include_router(router)
    _override_user(app, is_superuser=True)
    execution_service = _override_execution_service(app)
    with TestClient(app) as client:
        yield client, execution_service


@pytest.fixture
def user_client() -> Iterator[TestClient]:
    app = FastAPI()
    app.include_router(router)
    _override_user(app, is_superuser=False)
    _override_execution_service(app)
    with TestClient(app) as client:
        yield client


def _valid_update_payload() -> dict[str, object]:
    return {
        "stack": "moonmind",
        "image": {
            "repository": "ghcr.io/moonladderstudios/moonmind",
            "reference": "20260425.1234",
        },
        "mode": "changed_services",
        "removeOrphans": True,
        "wait": True,
        "runSmokeCheck": True,
        "pauseWork": False,
        "pruneOldImages": False,
        "reason": "Update to the latest tested MoonMind build",
    }


def _rollback_payload(**overrides: object) -> dict[str, object]:
    payload = _valid_update_payload()
    payload.update(
        {
            "image": {
                "repository": "ghcr.io/moonladderstudios/moonmind",
                "reference": "stable",
            },
            "reason": "Rollback after failed update depupd_recent",
            "operationKind": "rollback",
            "rollbackSourceActionId": "depupd_recent",
            "confirmation": (
                "Rollback to ghcr.io/moonladderstudios/moonmind:stable confirmed"
            ),
        }
    )
    payload.update(overrides)
    return payload


def _recent_action_service(*, eligible: bool = True) -> DeploymentOperationsService:
    eligibility = RollbackEligibilityDecision(
        eligible=eligible,
        target_image=(
            RollbackImageTarget(
                repository="ghcr.io/moonladderstudios/moonmind",
                reference="stable",
            )
            if eligible
            else None
        ),
        source_action_id="depupd_recent",
        evidence_ref="art:sha256:before",
        reason=None if eligible else "Before-state evidence is missing.",
    )
    return DeploymentOperationsService(
        recent_actions={
            "moonmind": (
                DeploymentRecentAction(
                    id="depupd_recent",
                    kind="failure",
                    status="FAILED",
                    requested_image=(
                        "ghcr.io/moonladderstudios/moonmind:20260425.1234"
                    ),
                    resolved_digest=None,
                    operator="admin@example.com",
                    reason="Routine release failed",
                    started_at="2026-04-25T18:00:00Z",
                    completed_at="2026-04-25T18:04:00Z",
                    run_detail_url="/workflows/depupd_recent",
                    logs_artifact_url="/api/artifacts/logs",
                    raw_command_log_url=None,
                    raw_command_log_permitted=False,
                    before_summary="ghcr.io/moonladderstudios/moonmind:stable",
                    after_summary="verification failed",
                    rollback_eligibility=eligibility,
                ),
            )
        }
    )


def test_admin_can_submit_policy_valid_deployment_update(
    admin_client: tuple[TestClient, _FakeExecutionService],
) -> None:
    client, execution_service = admin_client
    response = client.post(
        "/api/v1/operations/deployment/update",
        json=_valid_update_payload(),
    )

    assert response.status_code == 202
    payload = response.json()
    assert payload["deploymentUpdateRunId"].startswith("depupd_")
    assert payload["taskId"] == "mm:deployment-update"
    assert payload["workflowId"] == "mm:deployment-update"
    assert payload["status"] == "QUEUED"
    assert len(execution_service.requests) == 1
    request = execution_service.requests[0]
    assert request["workflow_type"] == "MoonMind.UserWorkflow"
    assert request["owner_type"] == "user"
    assert request["integration"] == DEPLOYMENT_UPDATE_TOOL_NAME
    parameters = request["initial_parameters"]
    assert isinstance(parameters, dict)
    plan = parameters["task"]["plan"]
    steps = parameters["task"]["steps"]
    assert plan[0]["tool"]["name"] == DEPLOYMENT_UPDATE_TOOL_NAME
    assert plan[0]["tool"]["version"] == DEPLOYMENT_UPDATE_TOOL_VERSION
    assert plan[0]["inputs"]["stack"] == "moonmind"
    assert steps[0]["type"] == "tool"
    assert steps[0]["tool"]["name"] == DEPLOYMENT_UPDATE_TOOL_NAME
    assert steps[0]["tool"]["version"] == DEPLOYMENT_UPDATE_TOOL_VERSION
    assert steps[0]["tool"]["inputs"]["stack"] == "moonmind"


def test_deployment_update_uses_canonical_policy_stack_for_queued_run(
    admin_client: tuple[TestClient, _FakeExecutionService],
) -> None:
    client, execution_service = admin_client
    payload = _valid_update_payload()
    payload["stack"] = " moonmind "

    response = client.post(
        "/api/v1/operations/deployment/update",
        json=payload,
    )

    assert response.status_code == 202
    parameters = execution_service.requests[0]["initial_parameters"]
    assert isinstance(parameters, dict)
    assert parameters["task"]["plan"][0]["inputs"]["stack"] == "moonmind"


def test_explicit_retry_submission_creates_distinct_audited_update_request(
    admin_client: tuple[TestClient, _FakeExecutionService],
) -> None:
    client, execution_service = admin_client

    first = client.post(
        "/api/v1/operations/deployment/update",
        json=_valid_update_payload(),
    )
    second_payload = _valid_update_payload()
    second_payload["reason"] = "Explicit retry after failed deployment update"
    second = client.post(
        "/api/v1/operations/deployment/update",
        json=second_payload,
    )

    assert first.status_code == 202
    assert second.status_code == 202
    assert len(execution_service.requests) == 2
    assert execution_service.requests[0]["idempotency_key"] != (
        execution_service.requests[1]["idempotency_key"]
    )


def test_repeated_update_submission_without_reason_reuses_idempotency_key(
    admin_client: tuple[TestClient, _FakeExecutionService],
) -> None:
    client, execution_service = admin_client
    payload = _valid_update_payload()
    payload.pop("reason")

    first = client.post(
        "/api/v1/operations/deployment/update",
        json=payload,
    )
    second = client.post(
        "/api/v1/operations/deployment/update",
        json=payload,
    )

    assert first.status_code == 202
    assert second.status_code == 202
    assert len(execution_service.requests) == 2
    assert execution_service.requests[0]["idempotency_key"] == (
        execution_service.requests[1]["idempotency_key"]
    )


def test_mutable_tag_update_submission_without_reason_is_not_stale_idempotent(
    admin_client: tuple[TestClient, _FakeExecutionService],
) -> None:
    client, execution_service = admin_client
    payload = _valid_update_payload()
    payload["image"] = {
        "repository": "ghcr.io/moonladderstudios/moonmind",
        "reference": "latest",
    }
    payload.pop("reason")

    first = client.post(
        "/api/v1/operations/deployment/update",
        json=payload,
    )
    second = client.post(
        "/api/v1/operations/deployment/update",
        json=payload,
    )

    assert first.status_code == 202
    assert second.status_code == 202
    assert len(execution_service.requests) == 2
    assert execution_service.requests[0]["idempotency_key"] != (
        execution_service.requests[1]["idempotency_key"]
    )


def test_non_admin_cannot_submit_deployment_update(
    user_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.oidc, "AUTH_PROVIDER", "accounts")
    response = user_client.post(
        "/api/v1/operations/deployment/update",
        json=_valid_update_payload(),
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "deployment_update_forbidden"
    assert response.json()["detail"]["failureClass"] == "authorization_failure"


def test_disabled_mode_still_enforces_admin_after_identity_resolution(
    user_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#4125: disabled mode grants no bypass; a non-admin is denied like any mode."""
    monkeypatch.setattr(settings.oidc, "AUTH_PROVIDER", "disabled")

    response = user_client.post(
        "/api/v1/operations/deployment/update",
        json=_valid_update_payload(),
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "deployment_update_forbidden"
    assert response.json()["detail"]["failureClass"] == "authorization_failure"


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("stack", "unlisted", "deployment_stack_not_allowed"),
        (
            "image",
            {"repository": "docker.io/library/nginx", "reference": "latest"},
            "deployment_repository_not_allowed",
        ),
        (
            "image",
            {
                "repository": "ghcr.io/moonladderstudios/moonmind",
                "reference": "../latest",
            },
            "deployment_image_reference_invalid",
        ),
        ("mode", "shell", "deployment_mode_not_allowed"),
    ],
)
def test_invalid_deployment_update_policy_inputs_are_rejected_before_execution(
    admin_client: tuple[TestClient, _FakeExecutionService],
    field: str,
    value: object,
    code: str,
) -> None:
    client, execution_service = admin_client
    payload = _valid_update_payload()
    payload[field] = value

    response = client.post(
        "/api/v1/operations/deployment/update",
        json=payload,
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == code
    assert execution_service.requests == []


def test_deployment_update_reason_is_optional(
    admin_client: tuple[TestClient, _FakeExecutionService],
) -> None:
    client, execution_service = admin_client
    payload = _valid_update_payload()
    payload.pop("reason")

    response = client.post(
        "/api/v1/operations/deployment/update",
        json=payload,
    )

    assert response.status_code == 202
    parameters = execution_service.requests[0]["initial_parameters"]
    assert isinstance(parameters, dict)
    plan_inputs = parameters["task"]["plan"][0]["inputs"]
    assert "reason" not in plan_inputs


def test_arbitrary_shell_and_path_fields_are_not_accepted(
    admin_client: tuple[TestClient, _FakeExecutionService],
) -> None:
    client, execution_service = admin_client
    payload = _valid_update_payload()
    payload["command"] = "docker compose up"
    payload["composeFile"] = "/tmp/docker-compose.yaml"

    response = client.post(
        "/api/v1/operations/deployment/update",
        json=payload,
    )

    assert response.status_code == 422
    details = response.json()["detail"]
    assert any(error["loc"][-1] == "command" for error in details)
    assert any(error["loc"][-1] == "composeFile" for error in details)
    assert execution_service.requests == []


def test_current_deployment_state_returns_typed_shape(
    admin_client: tuple[TestClient, _FakeExecutionService],
) -> None:
    client, _execution_service = admin_client
    response = client.get("/api/v1/operations/deployment/stacks/moonmind")

    assert response.status_code == 200
    payload = response.json()
    assert payload["stack"] == "moonmind"
    assert payload["projectName"] == "moonmind"
    assert "buildId" in payload
    current_image = payload["currentImage"]
    assert current_image["evidence"] in {
        "desired_state",
        "environment",
        "policy",
        "unavailable",
    }
    assert payload["policy"]["repository"].startswith(
        "ghcr.io/moonladderstudios/moonmind"
    )
    assert "latestAction" in payload


def test_deployment_state_returns_recent_failure_action_with_rollback_eligibility(
    admin_client: tuple[TestClient, _FakeExecutionService],
) -> None:
    client, _execution_service = admin_client
    _override_deployment_service(client.app, _recent_action_service())

    response = client.get("/api/v1/operations/deployment/stacks/moonmind")

    assert response.status_code == 200
    action = response.json()["recentActions"][0]
    assert action["kind"] == "failure"
    assert action["status"] == "FAILED"
    assert action["rollbackEligibility"] == {
        "eligible": True,
        "sourceActionId": "depupd_recent",
        "targetImage": {
            "repository": "ghcr.io/moonladderstudios/moonmind",
            "reference": "stable",
        },
        "reason": None,
        "evidenceRef": "art:sha256:before",
    }


def test_deployment_state_withholds_rollback_for_missing_before_state_evidence(
    admin_client: tuple[TestClient, _FakeExecutionService],
) -> None:
    client, _execution_service = admin_client
    _override_deployment_service(client.app, _recent_action_service(eligible=False))

    response = client.get("/api/v1/operations/deployment/stacks/moonmind")

    assert response.status_code == 200
    eligibility = response.json()["recentActions"][0]["rollbackEligibility"]
    assert eligibility["eligible"] is False
    assert eligibility["targetImage"] is None
    assert eligibility["reason"] == "Before-state evidence is missing."


def test_deployment_state_projects_recent_actions_from_execution_history(
    admin_client: tuple[TestClient, _FakeExecutionService],
) -> None:
    client, execution_service = admin_client
    execution_service.execution_items = [
        SimpleNamespace(
            workflow_id="mm:workflow-history",
            run_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            owner_id="admin@example.com",
            state="failed",
            close_status="failed",
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
                                    "repository": (
                                        "ghcr.io/moonladderstudios/moonmind"
                                    ),
                                    "reference": "20260425.1234",
                                },
                                "mode": "changed_services",
                                "reason": "Routine release failed",
                                "operationKind": "update",
                            },
                        }
                    ],
                }
            },
            memo={"summary": "Deployment update failed."},
            artifact_refs=["art_before"],
            started_at="2026-04-25T18:00:00Z",
            closed_at="2026-04-25T18:04:00Z",
        )
    ]

    response = client.get("/api/v1/operations/deployment/stacks/moonmind")

    assert response.status_code == 200
    action = response.json()["recentActions"][0]
    assert action["id"] == "depupd_aaaaaaaabbbbccccddddeeeeeeeeeeee"
    assert action["kind"] == "failure"
    assert action["status"] == "FAILED"
    assert action["requestedImage"] == (
        "ghcr.io/moonladderstudios/moonmind:20260425.1234"
    )
    assert action["reason"] == "Routine release failed"
    assert action["runDetailUrl"] == "/workflows/mm:workflow-history"
    assert action["rollbackEligibility"]["eligible"] is False
    assert action["rollbackEligibility"]["evidenceRef"] == "art_before"


def test_allowed_image_targets_return_digest_guidance(
    admin_client: tuple[TestClient, _FakeExecutionService],
) -> None:
    client, _execution_service = admin_client
    response = client.get(
        "/api/v1/operations/deployment/image-targets",
        params={"stack": "moonmind"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["stack"] == "moonmind"
    repository = payload["repositories"][0]
    assert repository["repository"] == "ghcr.io/moonladderstudios/moonmind"
    assert repository["digestPinningRecommended"] is True
    assert "latest" in repository["allowedReferences"]


def test_admin_can_submit_rollback_through_typed_deployment_update(
    admin_client: tuple[TestClient, _FakeExecutionService],
) -> None:
    client, execution_service = admin_client

    response = client.post(
        "/api/v1/operations/deployment/update",
        json=_rollback_payload(),
    )

    assert response.status_code == 202
    parameters = execution_service.requests[0]["initial_parameters"]
    assert isinstance(parameters, dict)
    operation = parameters["task"]["operation"]
    plan_inputs = parameters["task"]["plan"][0]["inputs"]
    assert operation["kind"] == "rollback"
    assert operation["rollbackSourceActionId"] == "depupd_recent"
    assert plan_inputs["operationKind"] == "rollback"
    assert plan_inputs["rollbackSourceActionId"] == "depupd_recent"
    assert plan_inputs["confirmation"].startswith("Rollback to")
    assert plan_inputs["image"]["reference"] == "stable"


def test_repeated_rollback_submissions_are_distinct_explicit_actions(
    admin_client: tuple[TestClient, _FakeExecutionService],
) -> None:
    client, execution_service = admin_client

    first = client.post(
        "/api/v1/operations/deployment/update",
        json=_rollback_payload(),
    )
    second = client.post(
        "/api/v1/operations/deployment/update",
        json=_rollback_payload(),
    )

    assert first.status_code == 202
    assert second.status_code == 202
    assert len(execution_service.requests) == 2
    assert execution_service.requests[0]["idempotency_key"] != (
        execution_service.requests[1]["idempotency_key"]
    )


def test_rollback_submission_requires_explicit_confirmation(
    admin_client: tuple[TestClient, _FakeExecutionService],
) -> None:
    client, execution_service = admin_client
    payload = _rollback_payload(confirmation=" ")

    response = client.post(
        "/api/v1/operations/deployment/update",
        json=payload,
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "deployment_confirmation_required"
    assert execution_service.requests == []


class _TemporalStopped:
    """Execution service stand-in for a stopped Temporal/workflow engine."""

    async def create_execution(self, **_kwargs: object) -> object:
        raise AssertionError("a controller-owned update must not create a workflow")

    async def list_executions(self, **_kwargs: object) -> object:
        raise ConnectionError("temporal is stopped")


_CONTROLLER_DIR = Path(__file__).resolve().parents[4] / "deploy" / "controller"


def _load_controller_module(name: str, monkeypatch: pytest.MonkeyPatch):
    import importlib.util
    import sys

    monkeypatch.syspath_prepend(str(_CONTROLLER_DIR))
    for module in ("redact", "mounts", "lock", "record", "engine", "server"):
        monkeypatch.delitem(sys.modules, module, raising=False)
    spec = importlib.util.spec_from_file_location(name, _CONTROLLER_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


class _RealController:
    """The shipped deploy/controller endpoint on its state-dir Unix socket.

    The API reaches it exactly as in a deployment: through the controller
    state directory (socket, deployment-owned secret, durable records).
    """

    def __init__(self, monkeypatch: pytest.MonkeyPatch, applier_factory) -> None:
        import shutil
        import tempfile
        import threading

        # Short path: AF_UNIX socket paths are length-limited.
        self.state_dir = Path(tempfile.mkdtemp(prefix="mmctl", dir="/tmp"))
        self._cleanup = lambda: shutil.rmtree(self.state_dir, ignore_errors=True)
        (self.state_dir / "secrets").mkdir()
        (self.state_dir / "secrets" / "controller-bearer").write_text("ctl-secret\n")
        self.engine = _load_controller_module("engine", monkeypatch)
        self.record = _load_controller_module("record", monkeypatch)
        self.server = _load_controller_module("server", monkeypatch)
        self.store = self.record.OperationStore(self.state_dir)
        self.applied: list[str] = []
        self.app = self.server.build_app(
            store=self.store, secret="ctl-secret", applier=applier_factory(self)
        )
        self.httpd = self.server.make_unix_server(
            str(self.state_dir / "controller.sock"), self.app
        )
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self._thread.start()
        monkeypatch.setenv("MOONMIND_CONTROLLER_STATE_DIR", str(self.state_dir))

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self._thread.join(timeout=10)

    def close(self) -> None:
        self.app.join_applies(timeout=10)
        self.stop()
        self._cleanup()


def _installs(controller: _RealController):
    def apply(operation: dict) -> None:
        controller.applied.append(operation["operationId"])
        controller.store.confirm_installed(
            operation["operationId"], image=operation["desired"]["image"]
        )

    return apply


@pytest.fixture(autouse=True)
def _no_installed_controller(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Isolate every test from any controller state on the machine running it.
    monkeypatch.setenv("MOONMIND_CONTROLLER_STATE_DIR", str(tmp_path / "no-controller"))
    monkeypatch.delenv("MOONMIND_CONTROLLER_URL", raising=False)
    monkeypatch.delenv("MOONMIND_CONTROLLER_SECRET_FILE", raising=False)


@pytest.fixture
def temporal_stopped_admin() -> Iterator[TestClient]:
    app = FastAPI()
    app.include_router(router)
    _override_user(app, is_superuser=True)
    app.dependency_overrides[_get_temporal_execution_service] = _TemporalStopped
    with TestClient(app) as client:
        yield client


_IMAGE = "ghcr.io/moonladderstudios/moonmind:20260425.1234"


def test_update_submits_to_the_real_controller_with_temporal_stopped(
    temporal_stopped_admin: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller = _RealController(monkeypatch, _installs)
    try:
        response = temporal_stopped_admin.post(
            "/api/v1/operations/deployment/update", json=_valid_update_payload()
        )
        assert response.status_code == 202, response.text
        payload = response.json()
        controller.app.join_applies(timeout=10)
        [record] = controller.store.list_terminal(stack="moonmind")
        # The response identifies the controller's own durable operation;
        # no workflow identity is manufactured for it.
        assert payload["owner"] == "controller"
        assert payload["operationId"] == record["operationId"]
        assert payload["desiredImage"] == _IMAGE
        assert payload["workflowId"] is None
        assert payload["taskId"] is None
        assert controller.applied == [record["operationId"]]

        state = temporal_stopped_admin.get(
            "/api/v1/operations/deployment/stacks/moonmind"
        )
        assert state.status_code == 200
        latest = state.json()["latestAction"]
        assert latest["operationId"] == record["operationId"]
        assert latest["status"] == "SUCCEEDED"
        assert latest["requestedImage"] == _IMAGE
        assert latest["resolvedDigest"] == _IMAGE
        assert latest["runDetailUrl"] is None

        detail = temporal_stopped_admin.get(
            f"/api/v1/operations/deployment/controller-operations/{record['operationId']}"
        )
        assert detail.status_code == 200
        assert detail.json()["status"] == "SUCCEEDED"
        assert detail.json()["installedImage"] == _IMAGE
        assert detail.json()["observedVia"] == "controller"
        assert "ctl-secret" not in state.text + detail.text + response.text
    finally:
        controller.close()


def test_duplicate_ui_submission_reattaches_to_one_mutation_owner(
    temporal_stopped_admin: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading

    release = threading.Event()

    def blocking(controller: _RealController):
        def apply(operation: dict) -> None:
            controller.applied.append(operation["operationId"])
            assert release.wait(timeout=10)
            controller.store.confirm_installed(
                operation["operationId"], image=operation["desired"]["image"]
            )

        return apply

    controller = _RealController(monkeypatch, blocking)
    try:
        first = temporal_stopped_admin.post(
            "/api/v1/operations/deployment/update", json=_valid_update_payload()
        )
        # Browser refresh / lost response: the same request again.
        second = temporal_stopped_admin.post(
            "/api/v1/operations/deployment/update", json=_valid_update_payload()
        )
        assert first.status_code == second.status_code == 202
        assert first.json()["operationId"] == second.json()["operationId"]
        # A changed target while the first owns the stack is refused and
        # names the owner instead of queueing a competing writer.
        changed = _valid_update_payload()
        changed["image"] = {
            "repository": "ghcr.io/moonladderstudios/moonmind",
            "reference": "stable",
        }
        conflict = temporal_stopped_admin.post(
            "/api/v1/operations/deployment/update", json=changed
        )
        assert conflict.status_code == 409, conflict.text
        assert conflict.json()["detail"]["code"] == "deployment_controller_busy"
        assert conflict.json()["detail"]["operationId"] == first.json()["operationId"]
        running = temporal_stopped_admin.get(
            "/api/v1/operations/deployment/stacks/moonmind"
        ).json()["latestAction"]
        assert running["status"] in {"QUEUED", "RUNNING"}
        release.set()
        controller.app.join_applies(timeout=10)
        assert controller.applied == [first.json()["operationId"]]
    finally:
        release.set()
        controller.close()


def test_api_replacement_recovers_progress_without_repeating_the_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _RealController(monkeypatch, _installs)

    def fresh_api() -> TestClient:
        app = FastAPI()
        app.include_router(router)
        _override_user(app, is_superuser=True)
        app.dependency_overrides[_get_temporal_execution_service] = _TemporalStopped
        return TestClient(app)

    try:
        with fresh_api() as before:
            submitted = before.post(
                "/api/v1/operations/deployment/update", json=_valid_update_payload()
            ).json()
        controller.app.join_applies(timeout=10)
        # A replaced API process (new app, new service instance) reconnects
        # to the same durable operation and never re-submits it.
        with fresh_api() as after:
            latest = after.get("/api/v1/operations/deployment/stacks/moonmind").json()[
                "latestAction"
            ]
        assert latest["operationId"] == submitted["operationId"]
        assert latest["status"] == "SUCCEEDED"
        assert controller.applied == [submitted["operationId"]]
    finally:
        controller.close()


def test_explicit_retry_after_exhaustion_works_and_preserves_first_failure(
    temporal_stopped_admin: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    behaviour = {"fail": True}

    def flaky(controller: _RealController):
        def apply(operation: dict) -> None:
            if behaviour["fail"]:
                controller.store.record_attempt_error(
                    operation["operationId"],
                    error="apply failed: up failed (exit 1) init-db exited 1",
                )
                raise controller.engine.ApplyError("up", 1, "init-db exited 1")
            controller.store.confirm_installed(
                operation["operationId"], image=operation["desired"]["image"]
            )

        return apply

    controller = _RealController(monkeypatch, flaky)
    try:
        submitted = temporal_stopped_admin.post(
            "/api/v1/operations/deployment/update", json=_valid_update_payload()
        ).json()
        controller.app.join_applies(timeout=10)
        operation_id = submitted["operationId"]
        failed = temporal_stopped_admin.get(
            f"/api/v1/operations/deployment/controller-operations/{operation_id}"
        ).json()
        assert failed["status"] == "FAILED"
        assert failed["retryPermitted"] is True
        assert "init-db exited 1" in failed["errorSummary"]
        assert any("init-db exited 1" in line for line in failed["logLines"])

        behaviour["fail"] = False
        retried = temporal_stopped_admin.post(
            f"/api/v1/operations/deployment/controller-operations/{operation_id}/retry"
        )
        assert retried.status_code == 202, retried.text
        assert retried.json()["operationId"] == operation_id
        controller.app.join_applies(timeout=10)
        recovered = temporal_stopped_admin.get(
            f"/api/v1/operations/deployment/controller-operations/{operation_id}"
        ).json()
        assert recovered["status"] == "SUCCEEDED"
        assert recovered["installedImage"] == _IMAGE
        # The first failure stays visible after the successful retry.
        assert recovered["errorSummary"].startswith("attempt 1: apply failed")
    finally:
        controller.close()


def test_non_admin_cannot_retry_a_controller_operation(
    user_client: TestClient,
) -> None:
    response = user_client.post(
        "/api/v1/operations/deployment/controller-operations/op-1/retry"
    )
    assert response.status_code == 403


def test_failed_postcheck_and_history_upload_failure_are_distinct_from_success(
    temporal_stopped_admin: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def verified_with_gaps(controller: _RealController):
        def apply(operation: dict) -> None:
            op_id = operation["operationId"]
            image = operation["desired"]["image"]
            if image.endswith(":stable"):
                controller.store.confirm_installed(op_id, image=image)
                controller.store.record_verification(
                    op_id,
                    name="operator-access:http://127.0.0.1:7000",
                    status="failed",
                    detail="healthz returned 502",
                )
            else:
                controller.store.confirm_installed(op_id, image=image)
                controller.store.note_reporting_failure(
                    op_id, error="application history import failed: artifact store down"
                )

        return apply

    controller = _RealController(monkeypatch, verified_with_gaps)
    try:
        temporal_stopped_admin.post(
            "/api/v1/operations/deployment/update", json=_valid_update_payload()
        )
        controller.app.join_applies(timeout=10)
        stable = _valid_update_payload()
        stable["image"] = {
            "repository": "ghcr.io/moonladderstudios/moonmind",
            "reference": "stable",
        }
        temporal_stopped_admin.post("/api/v1/operations/deployment/update", json=stable)
        controller.app.join_applies(timeout=10)
        actions = temporal_stopped_admin.get(
            "/api/v1/operations/deployment/stacks/moonmind"
        ).json()["recentActions"]
        by_image = {action["requestedImage"]: action for action in actions}
        postcheck = by_image["ghcr.io/moonladderstudios/moonmind:stable"]
        assert postcheck["status"] == "PARTIALLY_VERIFIED"
        assert any("healthz returned 502" in line for line in postcheck["logLines"])
        history = by_image[_IMAGE]
        # A reporting/history failure cannot change the confirmed outcome.
        assert history["status"] == "SUCCEEDED"
        assert any("artifact store down" in line for line in history["logLines"])
    finally:
        controller.close()


def test_installed_controller_unavailable_is_a_distinct_result_never_a_workflow(
    temporal_stopped_admin: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller = _RealController(monkeypatch, _installs)
    try:
        # The controller recorded work, so it keeps recovery authority.
        controller.store.begin(stack="moonmind", desired_image="img:0", source_revision="")
        controller.stop()
        response = temporal_stopped_admin.post(
            "/api/v1/operations/deployment/update", json=_valid_update_payload()
        )
        assert response.status_code == 503, response.text
        detail = response.json()["detail"]
        assert detail["code"] == "deployment_controller_unavailable"
        assert "bootstrap.py" in detail["message"]
        assert "ctl-secret" not in response.text
    finally:
        controller._cleanup()


def test_controller_rejecting_the_api_credential_is_access_denied(
    temporal_stopped_admin: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller = _RealController(monkeypatch, _installs)
    try:
        (controller.state_dir / "secrets" / "controller-bearer").write_text("stale\n")
        response = temporal_stopped_admin.post(
            "/api/v1/operations/deployment/update", json=_valid_update_payload()
        )
        assert response.status_code == 502, response.text
        assert response.json()["detail"]["code"] == "deployment_controller_unauthorized"
        assert "stale" not in response.text
        assert controller.store.list_open() == []
    finally:
        controller.close()


def test_unanswered_submission_is_uncertain_not_failed_or_forked(
    temporal_stopped_admin: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from api_service.services import deployment_operations as service_module

    controller = _RealController(monkeypatch, _installs)
    original = service_module._ControllerConnection.getresponse

    def lost_response(self):  # the request was sent; the reply never arrives
        original(self).read()
        raise TimeoutError("timed out")

    monkeypatch.setattr(service_module._ControllerConnection, "getresponse", lost_response)
    try:
        response = temporal_stopped_admin.post(
            "/api/v1/operations/deployment/update", json=_valid_update_payload()
        )
        assert response.status_code == 504, response.text
        assert response.json()["detail"]["code"] == "deployment_controller_uncertain"
        monkeypatch.setattr(service_module._ControllerConnection, "getresponse", original)
        controller.app.join_applies(timeout=10)
        # The accepted operation is still observable and was applied once.
        latest = temporal_stopped_admin.get(
            "/api/v1/operations/deployment/stacks/moonmind"
        ).json()["latestAction"]
        assert latest["status"] == "SUCCEEDED"
        assert len(controller.applied) == 1
    finally:
        controller.close()


def test_controller_record_stays_readable_while_the_controller_is_down(
    temporal_stopped_admin: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller = _RealController(monkeypatch, _installs)
    try:
        submitted = temporal_stopped_admin.post(
            "/api/v1/operations/deployment/update", json=_valid_update_payload()
        ).json()
        controller.app.join_applies(timeout=10)
        controller.stop()
        detail = temporal_stopped_admin.get(
            "/api/v1/operations/deployment/controller-operations/"
            f"{submitted['operationId']}"
        )
        assert detail.status_code == 200
        assert detail.json()["observedVia"] == "record"
        assert detail.json()["status"] == "SUCCEEDED"
        missing = temporal_stopped_admin.get(
            "/api/v1/operations/deployment/controller-operations/..%2Fsecrets"
        )
        assert missing.status_code in {404, 422}
    finally:
        controller._cleanup()


def test_bootstrap_that_never_started_keeps_the_transitional_workflow_path(
    admin_client: tuple[TestClient, _FakeExecutionService],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # `bootstrap.py install` wrote a secret, but no verified image could
    # start a controller and it owns no operation: the same transitional
    # rule as the host entrypoint applies until the image is published.
    state_dir = tmp_path / "controller"
    (state_dir / "secrets").mkdir(parents=True)
    (state_dir / "secrets" / "controller-bearer").write_text("x\n")
    (state_dir / "controller-image.json").write_text('{"verified": false}')
    monkeypatch.setenv("MOONMIND_CONTROLLER_STATE_DIR", str(state_dir))
    client, execution_service = admin_client
    response = client.post(
        "/api/v1/operations/deployment/update", json=_valid_update_payload()
    )
    assert response.status_code == 202, response.text
    assert response.json()["owner"] == "legacy_workflow"
    assert len(execution_service.requests) == 1


def test_historical_workflow_actions_stay_readable_beside_controller_actions(
    admin_client: tuple[TestClient, _FakeExecutionService],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _RealController(monkeypatch, _installs)
    client, execution_service = admin_client
    execution_service.execution_items = [
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
                                    "repository": "ghcr.io/moonladderstudios/moonmind",
                                    "reference": "20260101.0001",
                                },
                                "mode": "changed_services",
                            },
                        }
                    ],
                }
            },
            memo={},
            artifact_refs=[],
            started_at="2026-01-01T00:00:00Z",
            closed_at="2026-01-01T00:04:00Z",
        )
    ]
    try:
        client.post("/api/v1/operations/deployment/update", json=_valid_update_payload())
        controller.app.join_applies(timeout=10)
        actions = client.get("/api/v1/operations/deployment/stacks/moonmind").json()[
            "recentActions"
        ]
        assert actions[0]["operationId"]
        assert actions[-1]["runDetailUrl"] == "/workflows/mm:workflow-history"
        assert actions[-1]["operationId"] is None
        assert execution_service.requests == []
    finally:
        controller.close()
