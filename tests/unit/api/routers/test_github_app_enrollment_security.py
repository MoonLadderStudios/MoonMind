"""HTTP-boundary regressions for state-bound GitHub App enrollment."""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_service.api.routers import repository_connections as routes
from api_service.auth_providers import get_current_user
from api_service.db.base import get_async_session


@pytest.fixture
def enrollment(monkeypatch):
    monkeypatch.setenv("MOONMIND_GITHUB_APP_SETUP_SECRET", "test-setup-secret")
    monkeypatch.setattr(routes, "_setup_service", None)
    current = SimpleNamespace(id="operator-session")
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_current_user()] = lambda: current
    def session_stub():
        return object()

    app.dependency_overrides[get_async_session] = session_stub
    calls = []

    async def resolve(ref):
        calls.append(("secret", ref))
        return "test-key"

    def sign(key, *, app_id):
        calls.append(("jwt", app_id))
        return "test-jwt"

    async def fetch(*, jwt, installation_id, api_base):
        calls.append(("http", api_base, installation_id))
        return {
            "id": 456,
            "app_id": 123,
            "account": {"login": "acme"},
            "repositories": ["acme/repo"],
            "suspended_at": None,
        }

    monkeypatch.setattr(
        "moonmind.auth.github_app_wiring.default_resolve_secret_ref", resolve
    )
    monkeypatch.setattr("moonmind.auth.github_app_wiring.make_github_app_jwt", sign)
    monkeypatch.setattr(
        "moonmind.auth.github_app_wiring.fetch_installation_record", fetch
    )
    return TestClient(app, raise_server_exceptions=False), current, calls


def _begin(client, **changes):
    payload = {
        "appSlug": "moonmind",
        "expectedAppRef": "github-app:123",
        "appId": "123",
        "keySecretRef": "db://github-app-key/configured",
        "requestId": "request:enroll",
        "connectionId": "connection:enroll",
        "expectedAccount": "acme",
        "permittedRepositories": ["acme/repo"],
    }
    payload.update(changes)
    response = client.post("/github-app/begin", json=payload)
    assert response.status_code == 201, response.text
    return response.json()["state"]


def _callback(state, **changes):
    payload = {
        "state": state,
        "installationId": "456",
        "connectionId": "connection:enroll",
    }
    payload.update(changes)
    return payload


@pytest.mark.parametrize(
    "state_kind",
    ["forged", "unknown", "expired", "wrong-principal", "wrong-destination"],
)
def test_invalid_enrollment_never_resolves_secrets_or_contacts_provider(
    enrollment, state_kind
):
    client, current, calls = enrollment
    state = _begin(client)
    overrides = {}
    if state_kind == "forged":
        state = "forged-state"
    elif state_kind == "unknown":
        routes.get_setup_service()._pending.clear()
    elif state_kind == "expired":
        next(iter(routes.get_setup_service()._pending.values())).created_at -= 3600
    elif state_kind == "wrong-principal":
        current.id = "another-admitted-session"
    else:
        overrides["connectionId"] = "connection:substituted"
    response = client.post("/github-app/callback", json=_callback(state, **overrides))
    assert response.status_code == 409, response.text
    assert calls == []


def test_callback_cannot_trust_its_own_api_host(enrollment):
    client, _, calls = enrollment
    state = _begin(client)
    response = client.post(
        "/github-app/callback",
        json=_callback(
            state,
            endpointRef="https://capture.example",
            allowedApiHosts=["capture.example"],
        ),
    )
    assert response.status_code in (409, 422), response.text
    assert calls == []


@pytest.mark.parametrize(
    "change",
    [
        {"appId": "999"},
        {"keySecretRef": "db://another-key"},
        {"keyRef": "db://persisted-substitution"},
        {"endpointRef": "https://api.github.com/changed"},
        {"principalRef": "operator-session"},
        {"callerScopeType": "workspace"},
        {"ownerRef": "someone-else"},
        {"allowedOperations": ["write"]},
        {"permittedRepositories": ["other/repo"]},
        {"expectedAppRef": "github-app:999"},
        {"expectedAccount": "other-account"},
        {"requestId": "request:substituted"},
    ],
)
def test_callback_cannot_change_issuance_configuration(enrollment, change):
    client, _, calls = enrollment
    state = _begin(client)
    response = client.post("/github-app/callback", json=_callback(state, **change))
    assert response.status_code in (409, 422), response.text
    assert calls == []


@pytest.mark.parametrize("first_failure", [None, "before_commit", "after_commit"])
def test_http_enrollment_persists_bound_configuration_and_reconciles_retries(
    enrollment, monkeypatch, tmp_path, first_failure
):
    import asyncio

    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import func, select

    from api_service.db.models import RepositoryConnectionRecord
    from api_service.services.repository_connections import RepositoryConnectionService
    from moonmind.workflows.executions.repository_contract import RepositoryRouteError
    from tests.unit.auth.test_github_app_production_mount_4022 import _connection_db

    client, current, calls = enrollment
    state = _begin(
        client, displayName="Configured App", allowedOperations=["read", "write"]
    )
    pending = next(iter(routes.get_setup_service()._pending.values()))
    assert pending.principal_ref == current.id
    original = RepositoryConnectionService.create_connection
    attempts = 0

    async def create(self, *args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1 and first_failure == "before_commit":
            raise RepositoryRouteError("REPOSITORY_ROUTE_CONFLICT", "save failed")
        saved = await original(self, *args, **kwargs)
        if attempts == 1 and first_failure == "after_commit":
            raise RepositoryRouteError("REPOSITORY_ROUTE_CONFLICT", "response lost")
        return saved

    monkeypatch.setattr(RepositoryConnectionService, "create_connection", create)

    async def run():
        async with _connection_db(tmp_path) as sessions:

            async def db():
                async with sessions() as session:
                    yield session

            client.app.dependency_overrides[get_async_session] = db
            async with AsyncClient(
                transport=ASGITransport(app=client.app), base_url="http://testserver"
            ) as http:
                if first_failure:
                    failed = await http.post(
                        "/github-app/callback", json=_callback(state)
                    )
                    assert failed.status_code == 409, failed.text
                    assert not pending.consumed
                saved = await http.post("/github-app/callback", json=_callback(state))
                assert saved.status_code == 200, saved.text
                assert saved.json() == {"connectionId": "connection:enroll"}
                assert pending.consumed
                assert calls[-3:] == [
                    ("secret", "db://github-app-key/configured"),
                    ("jwt", "123"),
                    ("http", "https://api.github.com", "456"),
                ]
                calls.clear()
                retry = await http.post("/github-app/callback", json=_callback(state))
                assert retry.status_code == 200, retry.text
                assert calls == []
                rejected = await http.post(
                    "/github-app/callback", json=_callback(state, installationId="999")
                )
                assert rejected.status_code == 409
                assert calls == []
            async with sessions() as session:
                assert (
                    await session.scalar(
                        select(func.count()).select_from(RepositoryConnectionRecord)
                    )
                    == 1
                )
                stored = await RepositoryConnectionService(session).get_connection(
                    "connection:enroll",
                    principal_ref=current.id,
                    principal_scope=("system", None),
                )
                assert stored.display_name == "Configured App"
                assert stored.endpoint_ref == "https://github.com"
                assert stored.credential.app_ref == "github-app:123"
                assert stored.credential.key_ref == "db://github-app-key/configured"
                assert stored.credential.permitted_repositories == ("acme/repo",)
                assert stored.allowed_operations == ("read", "write")
                assert stored.ownership.owner_ref == current.id

    asyncio.run(run())


def test_begin_cannot_supply_principal_or_trusted_hosts(enrollment):
    client, _, calls = enrollment
    for extra in (
        {"principalRef": "someone-else"},
        {"allowedApiHosts": ["capture.example"]},
    ):
        response = client.post(
            "/github-app/begin",
            json={
                "appSlug": "moonmind",
                "expectedAppRef": "github-app:123",
                "appId": "123",
                "keySecretRef": "db://github-app-key/configured",
                "requestId": "request:blocked",
                "connectionId": "connection:blocked",
                **extra,
            },
        )
        assert response.status_code == 422, response.text
    assert calls == []
    assert not routes.get_setup_service()._pending


def test_enterprise_enrollment_uses_only_server_trusted_host(enrollment, monkeypatch):
    client, _, calls = enrollment
    from moonmind.auth import github_app_wiring

    monkeypatch.setattr(
        github_app_wiring, "TRUSTED_GITHUB_API_HOSTS", ("github.example",)
    )
    state = _begin(client, endpointRef="https://github.example")
    pending = next(iter(routes.get_setup_service()._pending.values()))
    assert pending.configuration.api_base == "https://github.example/api/v3"
    assert pending.state == state
    assert calls == []
