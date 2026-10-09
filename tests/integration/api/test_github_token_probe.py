from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from httpx import ASGITransport

from api_service.api.routers import settings as settings_router
from api_service.auth_providers import get_current_user
from api_service.main import app
from api_service.services.repository_connections import (
    RepositoryConnectionService,
    RepositoryRouteError,
)
from moonmind.auth import github_credentials
from moonmind.workflows.executions.repository_contract import (
    RepositoryAssignment,
    RepositoryConnection,
    RepositoryIdentity,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]

SETTINGS_USER_DEP = get_current_user()


@pytest.fixture
def settings_user_override():
    user = SimpleNamespace(
        id=uuid4(),
        email="settings-user@example.com",
        is_superuser=True,
        settings_permissions={
            "settings.catalog.read",
            "settings.effective.read",
            "settings.workspace.write",
        },
        workspace_id=uuid4(),
    )
    app.dependency_overrides[SETTINGS_USER_DEP] = lambda: user
    try:
        yield user
    finally:
        app.dependency_overrides.pop(SETTINGS_USER_DEP, None)


def _connection_b() -> RepositoryConnection:
    return RepositoryConnection.model_validate(
        {
            "schemaVersion": "moonmind.repository-connection.v1",
            "id": "connection-b",
            "provider": "git",
            "displayName": "Connection B",
            "hostingService": "github",
            "endpointRef": "https://github.com",
            "allowedOperations": ["read"],
            "clientPolicy": {
                "pinnedVersion": "system",
                "toolBundleRef": "git:system",
                "executableSha256": "system",
            },
            "credential": {
                "source": "secret_ref",
                "credentialRef": {"provider": "db", "key": "connection-b-pat"},
            },
            "ownership": {"ownerRef": "operator", "scopeType": "system"},
        }
    )


@pytest.fixture
def selected_b(monkeypatch, settings_user_override):
    """Connection B is selected while ambient credential A is configured."""

    state = SimpleNamespace(requests=[], routes={})
    monkeypatch.setenv("GITHUB_TOKEN", "ambient-token-a")
    monkeypatch.setenv("GH_TOKEN", "ambient-token-a")
    monkeypatch.setattr(settings_router.db_base, "async_session_maker", AsyncMock)

    async def get_connection(self, connection_id, **kwargs):
        if connection_id != "connection-b":
            raise RepositoryRouteError("connection_not_found", "not found")
        return _connection_b()

    monkeypatch.setattr(RepositoryConnectionService, "get_connection", get_connection)
    state.assignment_lookups = []

    async def list_assignments(self, connection_id):
        # B's saved assignment, with GitHub's own display casing.
        state.assignment_lookups.append(connection_id)
        return [
            RepositoryAssignment(
                connectionId=connection_id,
                identity=RepositoryIdentity(
                    endpoint="https://github.com",
                    providerRepoId="101",
                    displayName="Acme/Widgets",
                ),
                operations=("read",),
            )
        ]

    monkeypatch.setattr(
        RepositoryConnectionService, "list_assignments", list_assignments
    )
    secret = AsyncMock(return_value=("token-b", "fixture-connection-b-revision"))
    monkeypatch.setattr(github_credentials, "_resolve_secret_ref_with_revision", secret)
    state.secret = secret

    def handler(request: httpx.Request) -> httpx.Response:
        state.requests.append(request)
        route = state.routes.get(request.url.raw_path.decode())
        if route is None:
            return httpx.Response(200, json=[])
        return route() if callable(route) else route

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler)),
    )

    async def probe(payload):
        async with real_client(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            return await client.post("/api/v1/settings/github/token-probe", json=payload)

    state.probe = probe
    return state


def _assert_only_b_reads(state) -> None:
    assert state.requests, "expected GitHub reads"
    assert {request.method for request in state.requests} == {"GET"}
    assert {request.headers["Authorization"] for request in state.requests} == {
        "Bearer token-b"
    }


async def test_selected_b_reports_non_main_default_without_writes(selected_b):
    selected_b.routes["/repos/acme/widgets"] = httpx.Response(
        200, json={"default_branch": "trunk", "permissions": {"push": True}}
    )
    selected_b.routes["/repos/acme/widgets/branches/trunk"] = httpx.Response(
        200, json={"name": "trunk"}
    )

    response = await selected_b.probe(
        {"connectionId": "connection-b", "repo": "acme/widgets"}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["connectionId"] == "connection-b"
    assert body["mode"] == "publish"
    assert body["remoteDefaultBranch"] == "trunk"
    assert body["testedBranch"] == "trunk"
    assert body["observations"] == {
        "read": "verified",
        "branch": "verified",
        "write": "untested",
    }
    assert body["reportedPermissions"] == {"push": True}
    _assert_only_b_reads(selected_b)
    assert "token-b" not in response.text
    assert "ambient-token-a" not in response.text


@pytest.mark.parametrize(
    "listing,expected", [([], "empty_repository"), ([{"name": "trunk"}], "missing")]
)
async def test_selected_b_distinguishes_empty_repository_and_missing_branch(
    selected_b, listing, expected
):
    selected_b.routes["/repos/acme/widgets"] = httpx.Response(
        200, json={"default_branch": "main"}
    )
    selected_b.routes["/repos/acme/widgets/branches/release"] = httpx.Response(
        404, json={"message": "Branch not found"}
    )
    selected_b.routes["/repos/acme/widgets/branches?per_page=1"] = httpx.Response(
        200, json=listing
    )

    response = await selected_b.probe(
        {
            "connectionId": "connection-b",
            "repo": "acme/widgets",
            "mode": "full_pr_automation",
            "baseBranch": "release",
        }
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["observations"]["read"] == "verified"
    assert body["observations"]["branch"] == expected
    assert body["testedBranch"] == "release"
    assert not any("/commits/" in str(request.url) for request in selected_b.requests)
    _assert_only_b_reads(selected_b)


@pytest.mark.parametrize("status", [401, 403])
async def test_selected_b_denied_branch_listing_is_not_a_missing_branch(
    selected_b, status
):
    selected_b.routes["/repos/acme/widgets"] = httpx.Response(
        200, json={"default_branch": "main"}
    )
    selected_b.routes["/repos/acme/widgets/branches/release"] = httpx.Response(
        404, json={"message": "Branch not found"}
    )
    selected_b.routes["/repos/acme/widgets/branches?per_page=1"] = httpx.Response(
        status, json={"message": "Resource not accessible"}
    )

    response = await selected_b.probe(
        {
            "connectionId": "connection-b",
            "repo": "acme/widgets",
            "baseBranch": "release",
        }
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["observations"]["read"] == "verified"
    assert body["observations"]["branch"] == "denied"
    assert body["defaultBranchAccessible"] is False
    assert body["diagnostics"][-1]["httpStatus"] == status
    assert body["diagnostics"][-1]["retryable"] is False
    assert len(selected_b.requests) == 4
    _assert_only_b_reads(selected_b)


async def test_selected_b_unknown_repository_is_not_found(selected_b):
    selected_b.routes["/repos/acme/widgets"] = httpx.Response(
        404, json={"message": "Not Found"}
    )

    response = await selected_b.probe(
        {"connectionId": "connection-b", "repo": "acme/widgets"}
    )

    body = response.json()
    assert body["observations"]["read"] == "not_found"
    assert body["repositoryAccessible"] is False
    assert len(selected_b.requests) == 1
    _assert_only_b_reads(selected_b)


@pytest.mark.parametrize("headers,delay", [({"retry-after": "90"}, 90), ({}, 60)])
async def test_selected_b_throttle_is_unavailable_and_stops(selected_b, headers, delay):
    selected_b.routes["/repos/acme/widgets"] = httpx.Response(
        429, json={"message": "Too many requests"}, headers=headers
    )

    response = await selected_b.probe(
        {"connectionId": "connection-b", "repo": "acme/widgets"}
    )

    body = response.json()
    assert body["observations"]["read"] == "unavailable"
    assert body["retryAfterSeconds"] == delay
    assert body["repositoryAccessible"] is None
    assert len(selected_b.requests) == 1
    _assert_only_b_reads(selected_b)


@pytest.mark.parametrize(
    "payload",
    [
        {"repo": "acme/widgets", "mode": "publish", "baseBranch": "main"},
        {"repo": "acme/widgets", "connectionId": " "},
    ],
)
async def test_probe_without_selected_connection_is_rejected(selected_b, payload):
    response = await selected_b.probe(payload)

    assert response.status_code == 422, response.text
    assert selected_b.requests == []
    selected_b.secret.assert_not_called()


async def test_unknown_selected_connection_is_not_found_without_fallback(selected_b):
    response = await selected_b.probe(
        {"connectionId": "connection-a", "repo": "acme/widgets"}
    )

    assert response.status_code == 404
    assert selected_b.requests == []
    selected_b.secret.assert_not_called()


async def test_settings_token_ref_reaches_canonical_github_resolver(monkeypatch):
    from moonmind.auth.github_credentials import resolve_github_credential

    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("WORKFLOW_GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN_SECRET_REF", raising=False)
    monkeypatch.delenv("WORKFLOW_GITHUB_TOKEN_SECRET_REF", raising=False)
    monkeypatch.setenv("MOONMIND_GITHUB_TOKEN_REF", "db://github-pat-main")

    async def _fake_secret_ref(ref: str) -> tuple[str, str]:
        assert ref == "db://github-pat-main"
        return "resolved-token", "fixture-settings-revision"

    monkeypatch.setattr(
        "moonmind.auth.github_credentials._resolve_secret_ref_with_revision",
        _fake_secret_ref,
    )

    resolved = await resolve_github_credential(repo="owner/repo")

    assert resolved.token == "resolved-token"
    assert resolved.source_name == "MOONMIND_GITHUB_TOKEN_REF"
    assert resolved.repo == "owner/repo"


async def test_selected_b_refuses_unassigned_repository_without_reading(selected_b):
    """B's token may see other repositories; the test reads only B's own."""

    response = await selected_b.probe(
        {"connectionId": "connection-b", "repo": "other/unassigned"}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["connectionId"] == "connection-b"
    assert body["observations"] == {
        "read": "not_checked",
        "branch": "not_checked",
        "write": "untested",
    }
    assert body["repositoryAccessible"] is None
    assert body["credentialSource"]["resolved"] is False
    assert body["diagnostics"][0]["operation"] == "repository_assignment"
    assert selected_b.assignment_lookups == ["connection-b"]
    assert selected_b.requests == []
    selected_b.secret.assert_not_called()
    assert "ambient-token-a" not in response.text
