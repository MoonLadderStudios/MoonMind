"""Selected-connection probes retain authority through acquisition and HTTP."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from api_service.api.routers import settings as settings_router
from api_service.services.repository_connections import RepositoryConnectionService
from moonmind.auth import github_app_wiring, github_credentials
from moonmind.config.settings import settings
from moonmind.workflows.executions.repository_contract import RepositoryConnection

pytestmark = pytest.mark.asyncio


def _connection(
    source="secret_ref",
    endpoint="https://github.com",
    permitted_repository="acme/widgets",
):
    credential = {
        "source": "secret_ref",
        "credentialRef": {"provider": "db", "key": "selected-pat"},
    }
    if source == "github_app":
        credential = {
            "source": source,
            "appRef": "github-app:123",
            "installationRef": "github-installation:456",
            "keyRef": "db://selected-app-key",
            "account": "acme",
            "permittedRepositories": [permitted_repository],
        }
    return RepositoryConnection.model_validate(
        {
            "schemaVersion": "moonmind.repository-connection.v1",
            "id": "selected-connection",
            "provider": "git",
            "displayName": "Selected GitHub connection",
            "hostingService": "github",
            "endpointRef": endpoint,
            "allowedRepositoryIds": ["acme/widgets"],
            "allowedOperations": ["read"],
            "clientPolicy": {
                "pinnedVersion": "system",
                "toolBundleRef": "git:system",
                "executableSha256": "system",
            },
            "credential": credential,
            "ownership": {"ownerRef": "operator", "scopeType": "system"},
        }
    )


@pytest.fixture
def probe_route(monkeypatch):
    state = SimpleNamespace(connection=_connection(), requests=[], lookups=[])
    session = AsyncMock()
    monkeypatch.setattr(settings_router.db_base, "async_session_maker", lambda: session)

    async def get_connection(self, connection_id, **kwargs):
        state.lookups.append((connection_id, kwargs))
        if (
            getattr(state, "revoke_before_acquisition", False)
            and len(state.lookups) > 1
        ):
            return None
        assert connection_id == "selected-connection"
        assert kwargs == {
            "principal_ref": "operator",
            "principal_scope": ("system", None),
        }
        return state.connection

    monkeypatch.setattr(RepositoryConnectionService, "get_connection", get_connection)
    # The fixture connection admits acme/widgets through its own allowlist.
    monkeypatch.setattr(
        RepositoryConnectionService, "list_assignments", AsyncMock(return_value=[])
    )
    monkeypatch.setenv("GITHUB_TOKEN", "ambient-must-not-be-used")
    secret = AsyncMock(
        return_value=("selected-pat-token", "fixture-selected-pat-revision")
    )
    monkeypatch.setattr(github_credentials, "_resolve_secret_ref_with_revision", secret)
    state.secret = secret
    monkeypatch.setattr(settings.github, "github_trusted_api_hosts", "ghe.example.com")

    def handler(request):
        state.requests.append(request)
        if request.url.path.endswith(
            (
                "/app/installations/456",
                "/repos/acme/widgets/installation",
                "/repos/ACME/Widgets/installation",
            )
        ):
            return httpx.Response(
                200,
                json={
                    "id": 456,
                    "app_id": 123,
                    "account": {"id": 10, "login": "acme"},
                    "repository_selection": "selected",
                    "suspended_at": None,
                },
            )
        if request.url.path.endswith("/app/installations/456/access_tokens"):
            payload = json.loads(request.content)
            if getattr(state, "revoke_after_issuance", False):
                state.connection = None
            assert payload == {
                "repositories": ["widgets"],
                "permissions": {"contents": "read", "metadata": "read"},
            }
            return httpx.Response(
                201,
                json={
                    "token": "selected-installation-token",
                    "expires_at": (
                        datetime.now(timezone.utc) + timedelta(minutes=55)
                    ).isoformat(),
                    "permissions": payload["permissions"],
                    "repositories": [
                        {"id": 7, "name": "widgets", "full_name": "acme/widgets"}
                    ],
                },
            )
        if request.headers.get(
            "Authorization"
        ) == "Bearer selected-installation-token" and not request.url.path.endswith(
            ("/widgets", "/branches/trunk")
        ):
            return httpx.Response(
                403, json={"message": "Resource not accessible by integration"}
            )
        return httpx.Response(200, json={"id": 7, "default_branch": "trunk"})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler)),
    )
    app = FastAPI()
    app.include_router(settings_router.router, prefix="/api/v1")
    app.dependency_overrides[settings_router.SETTINGS_CURRENT_USER_DEP] = (
        lambda: SimpleNamespace(
            id="operator", settings_permissions={"settings.effective.read"}
        )
    )

    async def probe(mode=None, **overrides):
        payload = {"connectionId": "selected-connection", "repo": "acme/widgets"}
        if mode is not None:
            payload["mode"] = mode
        payload.update(overrides)
        payload = {key: value for key, value in payload.items() if value is not None}
        async with real_client(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            return await client.post(
                "/api/v1/settings/github/token-probe", json=payload
            )

    state.probe = probe
    return state


@pytest.mark.parametrize("endpoint", ["https://github.com", "https://ghe.example.com"])
@pytest.mark.parametrize("permitted_repository", ["acme/widgets", "ACME/Widgets"])
async def test_selected_app_probe_acquires_bound_installation_token(
    probe_route, monkeypatch, endpoint, permitted_repository
):
    probe_route.connection = _connection("github_app", endpoint, permitted_repository)
    key_reader = AsyncMock(return_value=b"selected-app-signing-key-fixture")
    monkeypatch.setattr(github_app_wiring, "default_resolve_secret_ref", key_reader)
    monkeypatch.setattr(
        github_app_wiring,
        "default_make_jwt_for",
        lambda app_id: lambda key: "selected-app-jwt",
    )

    response = await probe_route.probe()

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["observations"] == {
        "read": "verified",
        "branch": "verified",
        "write": "untested",
    }
    assert body["credentialSource"] == {
        "sourceKind": "github_app",
        "sourceName": "selected-connection",
        "resolved": True,
    }
    assert body["defaultBranchAccessible"] is True
    key_reader.assert_awaited_once_with("db://selected-app-key")
    probe_route.secret.assert_not_called()
    base = (
        "https://api.github.com"
        if endpoint == "https://github.com"
        else f"{endpoint}/api/v3"
    )
    assert [str(request.url) for request in probe_route.requests] == [
        f"{base}/app/installations/456",
        f"{base}/repos/{permitted_repository}/installation",
        f"{base}/app/installations/456/access_tokens",
        f"{base}/repos/acme/widgets",
        f"{base}/repos/acme/widgets/branches/trunk",
    ]
    assert [request.headers["Authorization"] for request in probe_route.requests] == [
        "Bearer selected-app-jwt",
        "Bearer selected-app-jwt",
        "Bearer selected-app-jwt",
        "Bearer selected-installation-token",
        "Bearer selected-installation-token",
    ]
    assert "selected-installation-token" not in response.text
    assert len(probe_route.lookups) >= 2  # Acquisition rechecks active authority.


@pytest.mark.parametrize("mode", ["publish", "readiness"])
async def test_selected_enterprise_pat_probe_keeps_every_check_on_trusted_host(
    probe_route, mode
):
    probe_route.connection = _connection(endpoint="https://ghe.example.com")

    response = await probe_route.probe(mode)

    assert response.status_code == 200, response.text
    assert response.json()["observations"]["read"] == "verified"
    assert len(probe_route.requests) == (3 if mode == "publish" else 5)
    assert all(
        str(request.url).startswith("https://ghe.example.com/api/v3/repos/acme/widgets")
        for request in probe_route.requests
    )
    assert all(
        request.headers["Authorization"] == "Bearer selected-pat-token"
        for request in probe_route.requests
    )


@pytest.mark.parametrize("source", ["secret_ref", "github_app"])
async def test_selected_probe_rejects_untrusted_endpoint_before_acquisition(
    probe_route, monkeypatch, source
):
    probe_route.connection = _connection(source, "https://untrusted.example.com")
    key_reader = AsyncMock()
    monkeypatch.setattr(github_app_wiring, "default_resolve_secret_ref", key_reader)

    response = await probe_route.probe()

    assert response.status_code == 200, response.text
    assert response.json()["credentialSource"]["resolved"] is False
    assert probe_route.requests == []
    probe_route.secret.assert_not_called()
    key_reader.assert_not_called()
    assert "allowlisted" in response.text


@pytest.mark.parametrize("phase", ["before_acquisition", "after_issuance"])
async def test_selected_app_probe_rechecks_revocation_without_fallback(
    probe_route, monkeypatch, phase
):
    probe_route.connection = _connection("github_app")
    setattr(probe_route, f"revoke_{phase}", True)
    key_reader = AsyncMock(return_value=b"selected-app-signing-key-fixture")
    monkeypatch.setattr(github_app_wiring, "default_resolve_secret_ref", key_reader)
    monkeypatch.setattr(
        github_app_wiring,
        "default_make_jwt_for",
        lambda app_id: lambda key: "selected-app-jwt",
    )

    response = await probe_route.probe()

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["credentialSource"]["resolved"] is False
    assert body["repositoryAccessible"] is None
    assert body["observations"]["read"] == "not_checked"
    assert "BOUND_REVOKED" in response.text
    probe_route.secret.assert_not_called()
    assert not any(
        request.headers.get("Authorization") == "Bearer selected-installation-token"
        for request in probe_route.requests
    )
    if phase == "before_acquisition":
        key_reader.assert_not_called()
    else:
        assert len(probe_route.requests) == 3


async def test_selected_app_probe_issuer_outage_is_unavailable_without_fallback(
    probe_route, monkeypatch
):
    probe_route.connection = _connection("github_app")
    key_reader = AsyncMock(side_effect=RuntimeError("secret store unavailable"))
    monkeypatch.setattr(github_app_wiring, "default_resolve_secret_ref", key_reader)

    response = await probe_route.probe()

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["credentialSource"]["resolved"] is False
    assert body["repositoryAccessible"] is None
    assert body["observations"]["read"] == "unavailable"
    assert body["diagnostics"][0]["retryable"] is True
    probe_route.secret.assert_not_called()
    assert probe_route.requests == []


@pytest.mark.parametrize("mode", ["publish", "readiness"])
async def test_selected_app_probe_does_not_deny_unrequested_collaboration_scope(
    probe_route, monkeypatch, mode
):
    probe_route.connection = _connection("github_app")
    monkeypatch.setattr(
        github_app_wiring,
        "default_resolve_secret_ref",
        AsyncMock(return_value=b"selected-app-signing-key-fixture"),
    )
    monkeypatch.setattr(
        github_app_wiring,
        "default_make_jwt_for",
        lambda app_id: lambda key: "selected-app-jwt",
    )

    response = await probe_route.probe(mode)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["observations"]["read"] == "verified"
    assert body["observations"]["write"] == "untested"
    assert body["pullRequestAccessible"] is None
    assert all(
        item["status"] == "not_checked"
        for item in body["permissionChecklist"]
        if item["permission"] != "Contents"
    )
    assert all(
        request.url.path.endswith(
            ("/456", "/installation", "/access_tokens", "/widgets", "/branches/trunk")
        )
        for request in probe_route.requests
    )
    assert any("read-scoped" in limitation for limitation in body["limitations"])


@pytest.mark.parametrize(
    "overrides",
    [{"connectionId": None}, {"connectionId": ""}, {"connectionId": "   "}],
)
async def test_probe_without_selected_connection_never_uses_ambient_token(
    probe_route, monkeypatch, overrides
):
    """MoonLadderStudios/MoonMind#4008: no selection, no fallback."""

    ambient = AsyncMock(side_effect=AssertionError("ambient resolver must not run"))
    monkeypatch.setattr(github_credentials, "resolve_github_credential", ambient)

    response = await probe_route.probe("publish", **overrides)

    assert response.status_code in {404, 422}, response.text
    assert probe_route.requests == []
    assert probe_route.lookups == []
    probe_route.secret.assert_not_called()
    ambient.assert_not_called()
    assert "ambient-must-not-be-used" not in response.text


async def test_probe_rejects_retired_indexing_mode(probe_route):
    response = await probe_route.probe("indexing")

    assert response.status_code == 422
    assert probe_route.requests == []
    probe_route.secret.assert_not_called()


async def test_probe_omitted_mode_uses_the_panel_default(probe_route):
    response = await probe_route.probe()

    assert response.status_code == 200, response.text
    assert response.json()["mode"] == "publish"
    assert [request.method for request in probe_route.requests] == ["GET"] * 3
    assert all(
        request.headers["Authorization"] == "Bearer selected-pat-token"
        for request in probe_route.requests
    )
