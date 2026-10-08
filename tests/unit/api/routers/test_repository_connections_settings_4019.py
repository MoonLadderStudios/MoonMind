"""Source Control connection routes (MoonLadderStudios/MoonMind#4019).

Real-database coverage for first- and two-connection PAT setup, lost
acknowledgment reconciliation, rotation conflicts, disable, verified
assignments, and selected-connection probing. Tokens and SecretRefs never
appear in responses; caller identity comes from the admission dependency.
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.api.routers import repository_connections as router_module
from api_service.auth_providers import get_current_user
from api_service.db.base import get_async_session
from api_service.db.models import (
    Base,
    ManagedSecret,
    RepositoryConnectionAssignment,
    RepositoryConnectionRecord,
)

pytestmark = pytest.mark.asyncio

TOKEN_A = "github_pat_" + "A" * 40
TOKEN_B = "github_pat_" + "B" * 40
TOKEN_ROTATED = "github_pat_" + "R" * 40


@pytest_asyncio.fixture
async def harness(tmp_path, monkeypatch):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/source-control-4019.db", future=True
    )
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    monkeypatch.setattr(
        "moonmind.auth.github_app_setup._deployment_git_client_policy",
        lambda: {
            "pinnedVersion": "2.46.0",
            "toolBundleRef": "repository-client:git-system",
            "executableSha256": "sha256:git",
        },
    )
    app = FastAPI()
    app.include_router(router_module.router, prefix="/api/v1/repository-connections")
    principal = {"id": "principal:operator"}

    async def _user():
        return SimpleNamespace(id=principal["id"])

    async def _session():
        async with maker() as session:
            yield session

    app.dependency_overrides[get_current_user()] = _user
    app.dependency_overrides[get_async_session] = _session
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield SimpleNamespace(client=client, maker=maker, principal=principal)
    await engine.dispose()


def _create_body(name: str, connection_id: str, request_id: str, token: str) -> dict:
    return {
        "requestId": request_id,
        "connectionId": connection_id,
        "displayName": name,
        "token": token,
        "allowedOperations": ["read", "write"],
    }


async def _count(maker, model) -> int:
    async with maker() as session:
        return int(
            (
                await session.execute(select(func.count()).select_from(model))
            ).scalar_one()
        )


def _assert_no_secret_material(text: str) -> None:
    for token in (TOKEN_A, TOKEN_B, TOKEN_ROTATED):
        assert token not in text
    assert "credentialRef" not in text
    assert "repository-connection/" not in text


async def test_first_and_second_pat_connections_list_without_token_exposure(harness):
    client = harness.client
    first = await client.post(
        "/api/v1/repository-connections/pat",
        json=_create_body("Personal GitHub", "personal-github", "req-1", TOKEN_A),
    )
    assert first.status_code == 201, first.text
    _assert_no_secret_material(first.text)
    body = first.json()
    assert body["id"] == "personal-github"
    assert body["displayName"] == "Personal GitHub"
    assert body["credentialKind"] == "personal_access_token"
    assert body["policyRevision"] == 1
    # Zero assignments is explicit, not wildcard authority.
    assert body["assignments"] == []

    second = await client.post(
        "/api/v1/repository-connections/pat",
        json=_create_body("Work GitHub", "work-github", "req-2", TOKEN_B),
    )
    assert second.status_code == 201, second.text

    listed = await client.get("/api/v1/repository-connections")
    assert listed.status_code == 200
    _assert_no_secret_material(listed.text)
    assert [item["id"] for item in listed.json()["items"]] == [
        "personal-github",
        "work-github",
    ]

    async with harness.maker() as session:
        record = (
            await session.execute(
                select(RepositoryConnectionRecord).where(
                    RepositoryConnectionRecord.connection_id == "personal-github"
                )
            )
        ).scalar_one()
        slug = record.credential_config["credentialRef"]["key"]
        secret = (
            await session.execute(
                select(ManagedSecret).where(ManagedSecret.slug == slug)
            )
        ).scalar_one()
        assert secret.ciphertext == TOKEN_A
        assert record.owner_ref == "principal:operator"
    assert await _count(harness.maker, ManagedSecret) == 2


async def test_lost_create_acknowledgment_reconciles_without_duplicates(harness):
    client = harness.client
    body = _create_body("Personal GitHub", "personal-github", "req-1", TOKEN_A)
    first = await client.post("/api/v1/repository-connections/pat", json=body)
    assert first.status_code == 201
    retry = await client.post("/api/v1/repository-connections/pat", json=body)
    assert retry.status_code == 201, retry.text
    assert retry.json() == first.json()
    assert await _count(harness.maker, RepositoryConnectionRecord) == 1
    assert await _count(harness.maker, ManagedSecret) == 1

    # A different request reusing the ID is a conflict, never a suffixed row.
    clash = await client.post(
        "/api/v1/repository-connections/pat",
        json=_create_body("Personal GitHub", "personal-github", "req-9", TOKEN_B),
    )
    assert clash.status_code == 409
    assert await _count(harness.maker, RepositoryConnectionRecord) == 1
    assert await _count(harness.maker, ManagedSecret) == 1


async def test_rotation_advances_credential_and_stale_revision_conflicts(harness):
    client = harness.client
    await client.post(
        "/api/v1/repository-connections/pat",
        json=_create_body("Personal GitHub", "personal-github", "req-1", TOKEN_A),
    )
    rotated = await client.patch(
        "/api/v1/repository-connections/personal-github",
        json={
            "requestId": "req-rotate",
            "expectedPolicyRevision": 1,
            "token": TOKEN_ROTATED,
        },
    )
    assert rotated.status_code == 200, rotated.text
    _assert_no_secret_material(rotated.text)
    assert rotated.json()["credentialRevision"] == 2
    assert rotated.json()["policyRevision"] == 2

    replay = await client.patch(
        "/api/v1/repository-connections/personal-github",
        json={
            "requestId": "req-rotate",
            "expectedPolicyRevision": 1,
            "token": TOKEN_ROTATED,
        },
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["credentialRevision"] == 2
    assert await _count(harness.maker, ManagedSecret) == 2

    stale = await client.patch(
        "/api/v1/repository-connections/personal-github",
        json={
            "requestId": "req-stale",
            "expectedPolicyRevision": 1,
            "token": TOKEN_B,
        },
    )
    assert stale.status_code == 409
    stale_rename = await client.patch(
        "/api/v1/repository-connections/personal-github",
        json={
            "requestId": "req-stale-name",
            "expectedPolicyRevision": 1,
            "displayName": "Renamed",
        },
    )
    assert stale_rename.status_code == 409
    current = await client.get("/api/v1/repository-connections/personal-github")
    assert current.json()["displayName"] == "Personal GitHub"
    assert current.json()["credentialRevision"] == 2
    assert await _count(harness.maker, ManagedSecret) == 2

    renamed = await client.patch(
        "/api/v1/repository-connections/personal-github",
        json={
            "requestId": "req-name",
            "expectedPolicyRevision": 2,
            "displayName": "Renamed",
        },
    )
    assert renamed.status_code == 200
    assert renamed.json()["displayName"] == "Renamed"
    assert renamed.json()["credentialRevision"] == 2


async def test_disable_keeps_the_record_and_rejects_other_principals(harness):
    client = harness.client
    await client.post(
        "/api/v1/repository-connections/pat",
        json=_create_body("Personal GitHub", "personal-github", "req-1", TOKEN_A),
    )
    harness.principal["id"] = "principal:stranger"
    denied = await client.post(
        "/api/v1/repository-connections/personal-github/disable",
        json={"requestId": "req-disable-x"},
    )
    assert denied.status_code in {403, 404}
    hidden = await client.get("/api/v1/repository-connections")
    assert hidden.json()["items"] == []
    harness.principal["id"] = "principal:operator"
    disabled = await client.post(
        "/api/v1/repository-connections/personal-github/disable",
        json={"requestId": "req-disable"},
    )
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["lifecycle"] == "disabled"
    listed = await client.get("/api/v1/repository-connections")
    assert listed.json()["items"][0]["lifecycle"] == "disabled"
    detail = await client.get("/api/v1/repository-connections/personal-github")
    assert detail.status_code == 200
    assert detail.json()["lifecycle"] == "disabled"


async def test_browser_supplied_identity_fields_are_rejected(harness):
    response = await harness.client.post(
        "/api/v1/repository-connections/pat",
        json={
            **_create_body("Personal GitHub", "personal-github", "req-1", TOKEN_A),
            "ownerRef": "principal:someone-else",
        },
    )
    assert response.status_code == 422
    assert await _count(harness.maker, RepositoryConnectionRecord) == 0


async def test_assignment_is_verified_and_outage_preserves_assignments(
    harness, monkeypatch
):
    client = harness.client
    await client.post(
        "/api/v1/repository-connections/pat",
        json=_create_body("Personal GitHub", "personal-github", "req-1", TOKEN_A),
    )
    seen: list[tuple[str, str]] = []

    async def _observe(connection, repository):
        seen.append((connection.id, repository))
        return {"id": "101", "fullName": "Acme/Widgets"}

    monkeypatch.setattr(router_module, "observe_github_repository", _observe)
    assigned = await client.post(
        "/api/v1/repository-connections/personal-github/assignments",
        json={
            "requestId": "req-assign",
            "repository": "acme/widgets",
            "operations": ["read", "write"],
        },
    )
    assert assigned.status_code == 200, assigned.text
    assert seen == [("personal-github", "acme/widgets")]
    assert assigned.json()["assignments"] == [
        {
            "repository": "Acme/Widgets",
            "providerRepoId": "101",
            "operations": ["read", "write"],
            "revision": 1,
            "verified": True,
        }
    ]

    async def _outage(connection, repository):
        raise HTTPException(status_code=503, detail="GitHub is unavailable")

    monkeypatch.setattr(router_module, "observe_github_repository", _outage)
    failed = await client.post(
        "/api/v1/repository-connections/personal-github/assignments",
        json={"requestId": "req-assign-2", "repository": "acme/gadgets"},
    )
    assert failed.status_code == 503
    current = await client.get("/api/v1/repository-connections/personal-github")
    assert [item["repository"] for item in current.json()["assignments"]] == [
        "Acme/Widgets"
    ]

    removed = await client.post(
        "/api/v1/repository-connections/personal-github/assignments/remove",
        json={
            "requestId": "req-remove",
            "providerRepoId": "101",
            "repository": "Acme/Widgets",
        },
    )
    assert removed.status_code == 200, removed.text
    assert removed.json()["assignments"] == []
    assert await _count(harness.maker, RepositoryConnectionAssignment) == 0


async def test_observe_repository_reads_with_the_connection_token(harness, monkeypatch):
    await harness.client.post(
        "/api/v1/repository-connections/pat",
        json=_create_body("Personal GitHub", "personal-github", "req-1", TOKEN_A),
    )
    async with harness.maker() as session:
        from api_service.services.repository_connections import (
            RepositoryConnectionService,
        )

        connection = await RepositoryConnectionService(session).get_connection(
            "personal-github",
            principal_ref="principal:operator",
            principal_scope=("system", None),
        )

    async def _resolve(ref: str) -> tuple[str, str]:
        assert ref == "db://repository-connection/personal-github/credential-1"
        return TOKEN_A, f"{ref}:credential:1:policy:1"

    monkeypatch.setenv("GITHUB_TOKEN", "global-token-must-not-be-used")
    monkeypatch.setattr(
        "moonmind.auth.github_credentials._resolve_secret_ref_with_revision", _resolve
    )
    requests: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"id": 7, "full_name": "acme/widgets"})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        router_module.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(_handler)),
    )
    observed = await router_module.observe_github_repository(connection, "acme/widgets")
    assert observed == {"id": "7", "fullName": "acme/widgets"}
    assert str(requests[0].url) == "https://api.github.com/repos/acme/widgets"
    assert TOKEN_A in requests[0].headers["Authorization"]

    def _down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    monkeypatch.setattr(
        router_module.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(_down)),
    )
    with pytest.raises(HTTPException) as outage:
        await router_module.observe_github_repository(connection, "acme/widgets")
    assert outage.value.status_code == 503


async def test_selected_connection_probe_uses_only_that_connection(
    harness, monkeypatch
):
    from api_service.api.routers import settings as settings_router
    from api_service.db import base as db_base

    await harness.client.post(
        "/api/v1/repository-connections/pat",
        json=_create_body("Personal GitHub", "personal-github", "req-1", TOKEN_A),
    )
    monkeypatch.setattr(db_base, "async_session_maker", harness.maker)

    async def _resolve(ref: str) -> str:
        assert ref == "db://repository-connection/personal-github/credential-1"
        return TOKEN_A

    monkeypatch.setenv("GITHUB_TOKEN", "global-token-must-not-be-used")
    monkeypatch.setattr(
        "moonmind.auth.github_credentials._resolve_secret_ref", _resolve
    )
    seen: dict = {}

    async def _probe(**kwargs):
        seen.update(kwargs)
        return {"repo": kwargs["repo"], "observations": {"read": "verified"}}

    monkeypatch.setattr(settings_router, "probe_github_token", _probe)
    app = FastAPI()
    app.include_router(settings_router.router, prefix="/api/v1")
    app.dependency_overrides[settings_router.SETTINGS_CURRENT_USER_DEP] = (
        lambda: SimpleNamespace(
            id=harness.principal["id"],
            is_superuser=False,
            settings_permissions={"settings.effective.read"},
        )
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/v1/settings/github/token-probe",
            json={
                "repo": "acme/widgets",
                "mode": "publish",
                "connectionId": "personal-github",
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["connectionId"] == "personal-github"
        assert seen["connection"].id == "personal-github"
        assert TOKEN_A not in response.text

        missing = await client.post(
            "/api/v1/settings/github/token-probe",
            json={"repo": "acme/widgets", "mode": "publish", "connectionId": "nope"},
        )
        assert missing.status_code == 404


async def test_pat_connection_test_reads_only_its_assigned_repositories(
    harness, monkeypatch
):
    """MoonLadderStudios/MoonMind#4008: the PAT can see other repositories,
    but Test connection reads only the ones saved on this connection."""

    from api_service.api.routers import settings as settings_router
    from api_service.db import base as db_base

    client = harness.client
    await client.post(
        "/api/v1/repository-connections/pat",
        json=_create_body("Personal GitHub", "personal-github", "req-1", TOKEN_A),
    )

    async def _observe(connection, repository):
        return {"id": "101", "fullName": "Acme/Widgets"}

    monkeypatch.setattr(router_module, "observe_github_repository", _observe)
    assigned = await client.post(
        "/api/v1/repository-connections/personal-github/assignments",
        json={"requestId": "req-assign", "repository": "acme/widgets"},
    )
    assert assigned.status_code == 200, assigned.text

    monkeypatch.setattr(db_base, "async_session_maker", harness.maker)
    monkeypatch.setenv("GITHUB_TOKEN", "global-token-must-not-be-used")
    secret_reads: list[str] = []

    async def _resolve(ref: str) -> str:
        secret_reads.append(ref)
        return TOKEN_A

    monkeypatch.setattr(
        "moonmind.auth.github_credentials._resolve_secret_ref", _resolve
    )
    requests: list[httpx.Request] = []

    def _github(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/repos/acme/widgets":
            return httpx.Response(200, json={"default_branch": "main"})
        return httpx.Response(200, json={"name": "main"})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        "moonmind.workflows.adapters.github_service.httpx.AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(_github)),
    )
    app = FastAPI()
    app.include_router(settings_router.router, prefix="/api/v1")
    app.dependency_overrides[settings_router.SETTINGS_CURRENT_USER_DEP] = (
        lambda: SimpleNamespace(
            id=harness.principal["id"],
            is_superuser=False,
            settings_permissions={"settings.effective.read"},
        )
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as settings_client:
        refused = await settings_client.post(
            "/api/v1/settings/github/token-probe",
            json={"connectionId": "personal-github", "repo": "other/unassigned"},
        )
        assert refused.status_code == 200, refused.text
        body = refused.json()
        assert body["observations"]["read"] == "not_checked"
        assert body["diagnostics"][0]["operation"] == "repository_assignment"
        assert requests == []
        assert secret_reads == []

        tested = await settings_client.post(
            "/api/v1/settings/github/token-probe",
            json={"connectionId": "personal-github", "repo": "acme/widgets"},
        )
    assert tested.status_code == 200, tested.text
    assert tested.json()["observations"]["read"] == "verified"
    assert requests and {request.method for request in requests} == {"GET"}
    assert {request.headers["Authorization"] for request in requests} == {
        f"Bearer {TOKEN_A}"
    }
    assert len(secret_reads) == 1
    _assert_no_secret_material(tested.text)
    # Refusing a test never changes the saved assignments.
    current = await client.get("/api/v1/repository-connections/personal-github")
    assert [item["repository"] for item in current.json()["assignments"]] == [
        "Acme/Widgets"
    ]


@pytest.mark.parametrize("display_name", [" ", "\t\n"])
async def test_blank_display_names_rejected_without_mutating_connections(
    harness, display_name
):
    client = harness.client
    rejected = await client.post(
        "/api/v1/repository-connections/pat",
        json=_create_body(display_name, "personal-github", "req-empty", TOKEN_A),
    )
    assert rejected.status_code == 422
    assert await _count(harness.maker, RepositoryConnectionRecord) == 0
    assert await _count(harness.maker, ManagedSecret) == 0
    created = await client.post(
        "/api/v1/repository-connections/pat",
        json=_create_body(" Personal GitHub ", "personal-github", "req-1", TOKEN_A),
    )
    assert created.status_code == 201
    assert created.json()["displayName"] == "Personal GitHub"
    rejected = await client.patch(
        "/api/v1/repository-connections/personal-github",
        json={
            "requestId": "req-empty-update",
            "expectedPolicyRevision": 1,
            "displayName": display_name,
        },
    )
    assert rejected.status_code == 422
    current = await client.get("/api/v1/repository-connections/personal-github")
    assert current.json()["displayName"] == "Personal GitHub"
    assert current.json()["policyRevision"] == 1


async def test_update_receipt_checks_exact_request_identity_and_admission(harness):
    client = harness.client
    for connection_id in ("personal-github", "other-github"):
        created = await client.post(
            "/api/v1/repository-connections/pat",
            json=_create_body(
                connection_id, connection_id, f"create-{connection_id}", TOKEN_A
            ),
        )
        assert created.status_code == 201
    updated = await client.patch(
        "/api/v1/repository-connections/personal-github",
        json={
            "requestId": "committed-update",
            "expectedPolicyRevision": 1,
            "displayName": "Concurrent edit",
        },
    )
    assert updated.status_code == 200
    prefix = "/api/v1/repository-connections/personal-github/requests"
    missing = await client.get(f"{prefix}/uncommitted-update")
    assert missing.status_code == 200
    assert missing.json() == {"committed": False}
    committed = await client.get(f"{prefix}/committed-update")
    assert committed.status_code == 200
    assert committed.json() == {"committed": True}
    # Another action's receipt cannot confirm this update.
    created = await client.get(f"{prefix}/create-personal-github")
    assert created.json() == {"committed": False}
    wrong_connection = await client.get(
        "/api/v1/repository-connections/other-github/requests/committed-update"
    )
    assert wrong_connection.status_code == 409
    harness.principal["id"] = "principal:stranger"
    denied = await client.get(f"{prefix}/committed-update")
    assert denied.status_code in (403, 404)


@pytest.mark.parametrize("revoke_during_issuance", [False, True])
@pytest.mark.parametrize("permitted_repository", ["acme/widgets", "ACME/Widgets"])
async def test_app_repository_assignment_uses_its_bound_read_credential(
    harness, monkeypatch, revoke_during_issuance, permitted_repository
):
    from datetime import datetime, timedelta, timezone

    from api_service.services.repository_connections import RepositoryConnectionService
    from moonmind.auth import github_app_wiring
    from moonmind.workflows.executions.repository_contract import RepositoryConnection

    connection = RepositoryConnection.model_validate(
        {
            "schemaVersion": "moonmind.repository-connection.v1",
            "id": "app-connection",
            "provider": "git",
            "hostingService": "github",
            "displayName": "App connection",
            "endpointRef": "https://github.com",
            "allowedOperations": ["read"],
            "clientPolicy": {
                "pinnedVersion": "system",
                "toolBundleRef": "git:system",
                "executableSha256": "system",
            },
            "credential": {
                "source": "github_app",
                "appRef": "github-app:123",
                "installationRef": "456",
                "keyRef": "db://app-key",
                "account": "acme",
                "permittedRepositories": [permitted_repository],
            },
            "ownership": {"ownerRef": "principal:operator", "scopeType": "system"},
        }
    )
    async with harness.maker() as session:
        await RepositoryConnectionService(session).create_connection(
            connection,
            actor_ref="principal:operator",
            request_id="create-app",
            principal_ref="principal:operator",
            principal_scope=("system", None),
        )

    async def read_key(ref):
        if revoke_during_issuance:
            async with harness.maker() as session:
                await RepositoryConnectionService(session).disable_connection(
                    "app-connection",
                    actor_ref="principal:operator",
                    request_id="disable-app",
                    principal_ref="principal:operator",
                    principal_scope=("system", None),
                )
        return b"app-key-fixture"

    monkeypatch.setattr(github_app_wiring, "default_resolve_secret_ref", read_key)
    monkeypatch.setattr(
        github_app_wiring,
        "default_make_jwt_for",
        lambda app_id: lambda key: "app-jwt-fixture",
    )
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path in {
            "/app/installations/456",
            f"/repos/{permitted_repository}/installation",
        }:
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
        if request.url.path == "/app/installations/456/access_tokens":
            import json

            assert json.loads(request.content) == {
                "repositories": ["widgets"],
                "permissions": {"contents": "read", "metadata": "read"},
            }
            return httpx.Response(
                201,
                json={
                    "token": "app-installation-fixture",
                    "expires_at": (
                        datetime.now(timezone.utc) + timedelta(minutes=55)
                    ).isoformat(),
                    "permissions": {"contents": "read", "metadata": "read"},
                    "repositories": [
                        {"id": 7, "full_name": "acme/widgets", "name": "widgets"}
                    ],
                },
            )
        assert str(request.url) == "https://api.github.com/repos/acme/widgets"
        assert request.headers["Authorization"] == "Bearer app-installation-fixture"
        return httpx.Response(200, json={"id": 7, "full_name": "acme/widgets"})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler)),
    )
    assigned = await harness.client.post(
        "/api/v1/repository-connections/app-connection/assignments",
        json={"requestId": "assign-app", "repository": "acme/widgets"},
    )
    if revoke_during_issuance:
        assert assigned.status_code in (403, 422)
        assert not any(
            request.headers.get("Authorization") == "Bearer app-installation-fixture"
            for request in requests
        )
        assert await _count(harness.maker, RepositoryConnectionAssignment) == 0
        return
    assert assigned.status_code == 200, assigned.text
    assert assigned.json()["assignments"][0]["providerRepoId"] == "7"
    assert assigned.json()["assignments"][0]["operations"] == ["read"]
    assert len(requests) == 4
    assert "app-installation-fixture" not in assigned.text
    requests.clear()
    denied = await harness.client.post(
        "/api/v1/repository-connections/app-connection/assignments",
        json={"requestId": "assign-outside", "repository": "acme/outside"},
    )
    assert denied.status_code == 422
    assert requests == []
    current = await harness.client.get("/api/v1/repository-connections/app-connection")
    assert len(current.json()["assignments"]) == 1
