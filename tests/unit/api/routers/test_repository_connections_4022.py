"""Operator-invokable GitHub App enrollment surface (#4022, P1-0610)."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_service.api.routers import repository_connections as router_module
from api_service.auth_providers import get_current_user


def _client(monkeypatch) -> TestClient:
    monkeypatch.setenv("MOONMIND_GITHUB_APP_SETUP_SECRET", "router-test-secret")
    router_module._setup_service = None
    app = FastAPI()
    app.include_router(router_module.router)

    async def _user():
        return {"ref": "principal:alice"}

    app.dependency_overrides[get_current_user()] = _user
    return TestClient(app)


def test_begin_returns_setup_url_and_state(monkeypatch) -> None:
    client = _client(monkeypatch)
    response = client.post(
        "/github-app/begin",
        json={
            "appSlug": "moonmind-test",
            "expectedAppRef": "github-app:moonmind-test",
            "requestId": "req:router-1",
            "connectionId": "repository-connection:app",
            "principalRef": "principal:alice",
            "principalScopeType": "system",
            "expectedAccount": "acme-org",
            "permittedRepositories": ["acme/repo"],
        },
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["setupUrl"].startswith(
        "https://github.com/apps/moonmind-test/installations/new?state="
    )
    assert payload["state"]
    assert payload["requestId"] == "req:router-1"
    assert payload["connectionId"] == "repository-connection:app"


def test_begin_rejects_anonymous_enrollment(monkeypatch) -> None:
    from fastapi import HTTPException

    monkeypatch.setenv("MOONMIND_GITHUB_APP_SETUP_SECRET", "router-test-secret")
    router_module._setup_service = None
    app = FastAPI()
    app.include_router(router_module.router)

    async def _deny():
        raise HTTPException(status_code=401, detail="unauthenticated")

    app.dependency_overrides[get_current_user()] = _deny
    client = TestClient(app, raise_server_exceptions=False)
    response = client.post(
        "/github-app/begin",
        json={
            "appSlug": "moonmind-test",
            "expectedAppRef": "github-app:moonmind-test",
            "requestId": "req:router-denied",
            "connectionId": "repository-connection:app",
            "principalRef": "principal:alice",
        },
    )
    assert response.status_code == 401


def test_callback_rejects_forged_state_before_touching_writer(monkeypatch) -> None:
    async def _session():
        return object()

    app = FastAPI()
    app.include_router(router_module.router)

    async def _user():
        return {"ref": "principal:alice"}

    from api_service.db.base import get_async_session

    app.dependency_overrides[get_current_user()] = _user
    app.dependency_overrides[get_async_session] = _session

    async def _fetch(*, jwt: str, installation_id: str, api_base: str):
        return {
            "app_ref": "github-app:moonmind-test",
            "installation_ref": "installation:123",
            "account": "acme-org",
            "repositories": ["acme/repo"],
            "suspended": False,
        }

    async def _resolve(ref: str):
        return "fake-pem"

    monkeypatch.setenv("MOONMIND_GITHUB_APP_SETUP_SECRET", "router-test-secret")
    monkeypatch.setattr(
        "moonmind.auth.github_app_wiring.fetch_installation_record", _fetch
    )
    monkeypatch.setattr(
        "moonmind.auth.github_app_wiring.default_resolve_secret_ref", _resolve
    )
    monkeypatch.setattr(
        "moonmind.auth.github_app_wiring.make_github_app_jwt",
        lambda key_material, *, app_id, ttl_seconds=540.0: "test-jwt",
    )
    router_module._setup_service = None
    client = TestClient(app, raise_server_exceptions=False)
    response = client.post(
        "/github-app/callback",
        json={
            "state": "forged-state",
            "installationId": "123",
            "expectedAppRef": "github-app:moonmind-test",
            "requestId": "req:router-forged",
            "connectionId": "repository-connection:app",
            "appId": "123456",
            "keySecretRef": "db://github-app-key",
            "principalRef": "principal:alice",
        },
    )
    assert response.status_code in (400, 409, 422), response.text
