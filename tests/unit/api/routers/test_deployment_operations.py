from __future__ import annotations

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
from api_service.auth_providers import get_current_user
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
        if getattr(dep.call, "__name__", "") == "_current_user_fallback"
    } or {get_current_user()}
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
                    run_detail_url="/tasks/depupd_recent",
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
    assert request["workflow_type"] == "MoonMind.Run"
    assert request["owner_type"] == "user"
    assert request["integration"] == DEPLOYMENT_UPDATE_TOOL_NAME
    parameters = request["initial_parameters"]
    assert isinstance(parameters, dict)
    plan = parameters["task"]["plan"]
    assert plan[0]["tool"]["name"] == DEPLOYMENT_UPDATE_TOOL_NAME
    assert plan[0]["tool"]["version"] == DEPLOYMENT_UPDATE_TOOL_VERSION
    assert plan[0]["inputs"]["stack"] == "moonmind"


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


def test_non_admin_cannot_submit_deployment_update(
    user_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.oidc, "AUTH_PROVIDER", "default")
    response = user_client.post(
        "/api/v1/operations/deployment/update",
        json=_valid_update_payload(),
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "deployment_update_forbidden"
    assert response.json()["detail"]["failureClass"] == "authorization_failure"


def test_disabled_auth_user_can_submit_deployment_update_as_default_admin(
    user_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.oidc, "AUTH_PROVIDER", "disabled")

    response = user_client.post(
        "/api/v1/operations/deployment/update",
        json=_valid_update_payload(),
    )

    assert response.status_code == 202
    assert response.json()["status"] == "QUEUED"


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
    assert payload["configuredImage"].startswith("ghcr.io/moonladderstudios/moonmind:")
    assert payload["services"][0]["name"] == "api"
    assert payload["services"][0]["state"] == "unknown"


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
                "task": {
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
    assert action["runDetailUrl"] == "/tasks/mm:workflow-history"
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
