"""Shared product routes admit the operator without a person (#4354)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from api_service import auth_providers as auth
from api_service.api.routers import deployment_operations, issue_lifecycle
from api_service.api.routers import omnigent_agent_profiles as agent_profiles
from api_service.api.routers import (
    omnigent_catalog,
    omnigent_policies,
    omnigent_session_timeline,
    provider_profiles,
    repository_connections,
)
from api_service.db.base import get_async_session
from moonmind.security import auth_modes_4120
from moonmind.security.operator_admission import OperatorAdmission

ADMISSION = OperatorAdmission(via="loopback")


@pytest.fixture
def converted_instance(monkeypatch):
    """Account-free local install whose guarded conversion has published."""

    monkeypatch.setattr(auth_modes_4120, "_ACTIVE_PRODUCTION_MODE", "disabled")
    for name in (
        "MOONMIND_PUBLIC_BASE_URL",
        "MOONMIND_API_PUBLISH_HOST",
        "MOONMIND_TRUSTED_INGRESS",
        "MOONMIND_DEPLOYMENT_PROJECT_NAME",
    ):
        monkeypatch.delenv(name, raising=False)
    ledger = MagicMock()
    ledger.scalars.return_value.all.return_value = [
        SimpleNamespace(
            status="complete",
            result_json={"published": True, "decision": {"disposition": "fresh_init"}},
        )
    ]
    session = AsyncMock()
    session.execute.return_value = ledger
    session.get.side_effect = AssertionError("admission must not look up a person")
    monkeypatch.setattr(
        omnigent_policies.OmnigentPolicyService,
        "list",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        omnigent_session_timeline.OmnigentSessionTimelineService,
        "timeline",
        AsyncMock(return_value={"sessionId": "s-1", "events": []}),
    )
    app = FastAPI()
    app.include_router(omnigent_policies.router)
    app.include_router(omnigent_session_timeline.router)

    async def _session():
        yield session

    app.dependency_overrides[get_async_session] = _session
    return app, session


def _client(app: FastAPI, peer: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=(peer, 43210)),
        base_url="http://localhost:5000",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/api/omnigent/policies", {"items": []}),
        (
            "/api/omnigent/sessions/s-1/timeline",
            {"sessionId": "s-1", "events": []},
        ),
    ],
)
async def test_admitted_operator_reads_without_person(converted_instance, path, body):
    app, session = converted_instance
    async with _client(app, "127.0.0.1") as client:
        response = await client.get(path)
    assert response.status_code == 200, response.text
    assert response.json() == body
    session.get.assert_not_called()


@pytest.mark.asyncio
async def test_foreign_origin_mutation_is_denied(converted_instance):
    app, _session = converted_instance
    async with _client(app, "127.0.0.1") as client:
        response = await client.post(
            "/api/omnigent/policies",
            json={},
            headers={"origin": "https://foreign.example"},
        )
    assert response.status_code == 403
    assert response.json()["detail"] == {"code": "origin_forbidden"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path", ["/api/omnigent/policies", "/api/omnigent/sessions/s-1/timeline"]
)
async def test_direct_backend_peer_with_old_login_is_denied(converted_instance, path):
    app, session = converted_instance
    async with _client(app, "172.21.0.9") as client:
        response = await client.get(
            path,
            headers={
                "cookie": "moonmind_session=retired-account-session",
                "authorization": "Bearer retired-account-session",
                "x-forwarded-for": "127.0.0.1",
            },
        )
    assert response.status_code == 401
    session.get.assert_not_called()


def test_admission_manages_every_instance_resource():
    legacy_owner = uuid4()
    private_profile = SimpleNamespace(owner_id=legacy_owner, visibility="private")

    agent_profiles._assert_owner(private_profile, ADMISSION)
    assert agent_profiles._owns_profile(private_profile, ADMISSION)
    assert agent_profiles._audit("p", "created", ADMISSION).actor_id is None
    assert omnigent_catalog._can_view_agent_profile(private_profile, ADMISSION)
    provider_profiles._require_privileged_credential_volume_import(ADMISSION)
    deployment_operations._require_admin(ADMISSION)
    assert all(issue_lifecycle._permissions_for_user(ADMISSION).values())


def test_admission_keeps_retained_operator_scope():
    principal = auth.instance_operator_principal()

    assert repository_connections._admitted_principal(ADMISSION) == principal
    assert omnigent_policies._actor(ADMISSION) == principal


def test_retained_account_keeps_owner_checks():
    owner = SimpleNamespace(id=uuid4(), is_superuser=False)
    other = SimpleNamespace(owner_id=uuid4(), visibility="private")

    with pytest.raises(HTTPException):
        agent_profiles._assert_owner(other, owner)
    assert not omnigent_catalog._can_view_agent_profile(other, owner)
    with pytest.raises(HTTPException):
        deployment_operations._require_admin(owner)
