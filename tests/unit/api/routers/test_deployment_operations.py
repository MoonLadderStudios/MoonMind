from __future__ import annotations

from types import SimpleNamespace
from typing import Iterator
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_service.api.routers.deployment_operations import router
from api_service.auth_providers import get_current_user


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


@pytest.fixture
def admin_client() -> Iterator[TestClient]:
    app = FastAPI()
    app.include_router(router)
    _override_user(app, is_superuser=True)
    with TestClient(app) as client:
        yield client


@pytest.fixture
def user_client() -> Iterator[TestClient]:
    app = FastAPI()
    app.include_router(router)
    _override_user(app, is_superuser=False)
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


def test_admin_can_submit_policy_valid_deployment_update(
    admin_client: TestClient,
) -> None:
    response = admin_client.post(
        "/api/v1/operations/deployment/update",
        json=_valid_update_payload(),
    )

    assert response.status_code == 202
    payload = response.json()
    assert payload["deploymentUpdateRunId"].startswith("depupd_")
    assert payload["taskId"] or payload["workflowId"]
    assert payload["status"] == "QUEUED"


def test_non_admin_cannot_submit_deployment_update(user_client: TestClient) -> None:
    response = user_client.post(
        "/api/v1/operations/deployment/update",
        json=_valid_update_payload(),
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "deployment_update_forbidden"


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
        ("reason", "   ", "deployment_reason_required"),
    ],
)
def test_invalid_deployment_update_policy_inputs_are_rejected_before_execution(
    admin_client: TestClient,
    field: str,
    value: object,
    code: str,
) -> None:
    payload = _valid_update_payload()
    payload[field] = value

    response = admin_client.post(
        "/api/v1/operations/deployment/update",
        json=payload,
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == code


def test_arbitrary_shell_and_path_fields_are_not_accepted(
    admin_client: TestClient,
) -> None:
    payload = _valid_update_payload()
    payload["command"] = "docker compose up"
    payload["composeFile"] = "/tmp/docker-compose.yaml"

    response = admin_client.post(
        "/api/v1/operations/deployment/update",
        json=payload,
    )

    assert response.status_code == 422
    details = response.json()["detail"]
    assert any(error["loc"][-1] == "command" for error in details)
    assert any(error["loc"][-1] == "composeFile" for error in details)


def test_current_deployment_state_returns_typed_shape(admin_client: TestClient) -> None:
    response = admin_client.get("/api/v1/operations/deployment/stacks/moonmind")

    assert response.status_code == 200
    payload = response.json()
    assert payload["stack"] == "moonmind"
    assert payload["projectName"] == "moonmind"
    assert payload["configuredImage"].startswith("ghcr.io/moonladderstudios/moonmind:")
    assert payload["services"][0]["name"] == "api"
    assert payload["services"][0]["state"] == "unknown"


def test_allowed_image_targets_return_digest_guidance(admin_client: TestClient) -> None:
    response = admin_client.get(
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
