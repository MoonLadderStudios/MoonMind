"""Operator-invokable GitHub App enrollment surface (#4022, P1-0610)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.api.routers import repository_connections as router_module
from api_service.auth_providers import get_current_user
from api_service.db.base import get_async_session
from api_service.db.models import Base, ManagedSecret, RepositoryConnectionRecord
from api_service.services.secrets import SecretsService


@asynccontextmanager
async def settings_client(tmp_path, monkeypatch):
    """#4019 exercises authenticated HTTP adapters and their real database owner."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/settings4019.db")
    sessions = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    app = FastAPI()
    app.include_router(router_module.router, prefix="/connections")
    user = SimpleNamespace(id="operator:4019", is_superuser=True)

    async def session():
        async with sessions() as db:
            yield db

    async def current_user():
        return user

    app.dependency_overrides[get_current_user()] = current_user
    app.dependency_overrides[get_async_session] = session
    real_client = httpx.AsyncClient
    provider_calls = []
    unavailable = set()

    def provider(request):
        provider_calls.append(request)
        if request.url.path in unavailable:
            return httpx.Response(503)
        if request.url.path == "/user":
            return httpx.Response(
                200, json={"id": 4019, "login": "connected-account", "type": "User"}
            )
        if request.url.path == "/app":
            return httpx.Response(200, json={"id": 123456, "slug": "moonmind-test"})
        repo = request.url.path.removeprefix("/repos/")
        if repo in {"owner/first", "owner/second", "owner/new"}:
            return httpx.Response(
                200,
                json={
                    "id": {"owner/first": 1, "owner/second": 2, "owner/new": 3}[repo],
                    "full_name": repo,
                    "clone_url": f"https://github.com/{repo}.git",
                },
            )
        return httpx.Response(404)

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(provider), **kwargs),
    )
    async with real_client(
        transport=httpx.ASGITransport(app=app),
        base_url="http://settings.test/connections/",
        follow_redirects=True,
    ) as client:
        yield client, sessions, provider_calls, unavailable, user
    await engine.dispose()


def _create_payload(suffix="first"):
    return {
        "connectionId": f"repository-connection:4019-{suffix}",
        "requestId": f"4019-create-{suffix}",
        "displayName": f"Account {suffix}",
        "plaintext": f"github_pat_4019_{suffix}_sentinel",
        "repositories": [f"owner/{suffix}"],
        "allowedOperations": ["read", "write"],
    }


@pytest.mark.asyncio
async def test_settings_selected_app_begin_derives_trusted_configuration(
    tmp_path, monkeypatch
):
    async with settings_client(tmp_path, monkeypatch) as configured:
        client, sessions, _, _, _ = configured
        from api_service.services.repository_connections import (
            RepositoryConnectionService,
        )
        from moonmind.workflows.executions.repository_contract import (
            ConnectionOwnershipPolicy,
            GitHubAppCredential,
        )
        from tests.helpers.repository_connections import github_pat_connection

        connection = github_pat_connection("existing-app", "UNUSED").model_copy(
            update={
                "credential": GitHubAppCredential(
                    source="github_app",
                    appRef="github-app:123456",
                    installationRef="installation:123",
                    keyRef="db://signing-key",
                    account="owner",
                    permittedRepositories=("owner/first",),
                ),
                "ownership": ConnectionOwnershipPolicy(
                    ownerRef="operator:4019",
                    scopeType="system",
                    allowedPrincipalRefs=("operator:4019",),
                ),
            }
        )
        async with sessions() as db:
            await RepositoryConnectionService(db).create_connection(
                connection,
                actor_ref="operator:4019",
                request_id="4019-app-seed",
                principal_ref="operator:4019",
                principal_scope=("system", None),
            )
        monkeypatch.setenv("MOONMIND_GITHUB_APP_SETUP_SECRET", "router-test-secret")
        router_module._setup_service = None

        async def key(ref):
            assert ref == "db://signing-key"
            return "test-pem"

        monkeypatch.setattr(
            "moonmind.auth.github_app_wiring.default_resolve_secret_ref", key
        )
        monkeypatch.setattr(
            "moonmind.auth.github_app_wiring.make_github_app_jwt",
            lambda key, *, app_id: "test-app-jwt",
        )
        response = await client.post(
            "/github-app/begin",
            json={
                "appConnectionId": "existing-app",
                "requestId": "4019-app-begin",
                "connectionId": "new-app",
                "displayName": "New App account",
                "expectedAccount": "owner",
                "permittedRepositories": ["owner/first"],
            },
        )
        assert response.status_code == 201, response.text
        assert response.json()["setupUrl"].startswith(
            "https://github.com/apps/moonmind-test/installations/new?state="
        )
        pending = router_module.get_setup_service().admit_setup_callback(
            state=response.json()["state"],
            caller_principal="operator:4019",
            caller_scope=("system", None),
            destination_connection_id="new-app",
        )
        assert pending.expected_app_ref == "github-app:123456"
        assert pending.configuration.key_secret_ref == "db://signing-key"
        assert pending.principal_ref == "operator:4019"


@pytest.mark.asyncio
async def test_settings_first_and_two_connections_reconcile_without_duplicate_or_token_exposure(
    tmp_path, monkeypatch
):
    async with settings_client(tmp_path, monkeypatch) as configured:
        client, sessions, calls, _, _ = configured
        for suffix in ("first", "second"):
            payload = _create_payload(suffix)
            saved = await client.post("", json=payload)
            assert saved.status_code == 201, saved.text
            assert saved.json()["account"] == "connected-account"
            assert saved.json()["repositories"] == [f"owner/{suffix}"]
            receipt = await client.get(
                f"/{payload['connectionId']}/operations/{payload['requestId']}"
            )
            assert receipt.status_code == 200, receipt.text
            assert receipt.json()["committed"] is True
            assert receipt.json()["connection"]["id"] == payload["connectionId"]
            before = len(calls)
            replay = await client.post("", json=payload)
            assert replay.json() == saved.json()
            assert len(calls) == before
            assert payload["plaintext"] not in saved.text + receipt.text
            assert "credentialRef" not in saved.text
        listed = await client.get("")
        assert len(listed.json()["items"]) == 2
        async with sessions() as db:
            records = (
                (await db.execute(select(RepositoryConnectionRecord))).scalars().all()
            )
            assert len(records) == 2
            assert all(record.owner_ref == "operator:4019" for record in records)
            assert "sentinel" not in str(
                [record.credential_config for record in records]
            )


@pytest.mark.asyncio
async def test_settings_lost_acknowledgment_reads_committed_receipt_after_restart(
    tmp_path, monkeypatch
):
    async with settings_client(tmp_path, monkeypatch) as configured:
        client, sessions, calls, _, _ = configured
        transport = client._transport

        class LostAcknowledgment(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request):
                response = await transport.handle_async_request(request)
                if request.method == "POST" and response.status_code == 201:
                    await response.aread()
                    raise httpx.ReadError("lost acknowledgment", request=request)
                return response

        client._transport = LostAcknowledgment()
        payload = _create_payload()
        with pytest.raises(httpx.ReadError):
            await client.post("", json=payload)
        before = len(calls)
        result = await client.get(
            f"/{payload['connectionId']}/operations/{payload['requestId']}"
        )
        assert result.status_code == 200, result.text
        assert result.json()["committed"] is True
        assert result.json()["connection"]["id"] == payload["connectionId"]
        assert len(calls) == before
        async with sessions() as db:
            records = (
                (await db.execute(select(RepositoryConnectionRecord))).scalars().all()
            )
            assert len(records) == 1


@pytest.mark.asyncio
async def test_settings_rotation_conflict_and_partial_assignment_discovery_preserve_saved_state(
    tmp_path, monkeypatch
):
    async with settings_client(tmp_path, monkeypatch) as configured:
        client, sessions, calls, unavailable, _ = configured
        original = _create_payload()
        created = await client.post("", json=original)
        assert created.status_code == 201, created.text
        cid = original["connectionId"]
        rotated = await client.patch(
            f"/{cid}",
            json={
                "requestId": "4019-rotate",
                "expectedPolicyRevision": 1,
                "expectedCredentialRevision": 1,
                "plaintext": "github_pat_4019_rotated_sentinel",
                "displayName": "Renamed",
                "repositories": ["owner/first"],
                "allowedOperations": ["read", "write"],
            },
        )
        assert rotated.status_code == 200, rotated.text
        assert rotated.json()["credentialRevision"] == 2
        assert rotated.json()["policyRevision"] == 2
        before = len(calls)
        conflict = await client.patch(
            f"/{cid}",
            json={
                "requestId": "4019-stale-rotate",
                "expectedPolicyRevision": 1,
                "expectedCredentialRevision": 1,
                "plaintext": "github_pat_4019_stale_sentinel",
                "displayName": "Must not replace",
            },
        )
        assert conflict.status_code == 409, conflict.text
        assert len(calls) == before
        unavailable.add("/repos/owner/new")
        partial = await client.patch(
            f"/{cid}",
            json={
                "requestId": "4019-partial",
                "expectedPolicyRevision": 2,
                "expectedCredentialRevision": 2,
                "displayName": "Safe draft",
                "repositories": ["owner/first", "owner/new"],
            },
        )
        assert partial.status_code == 503, partial.text
        unchanged = (await client.get(f"/{cid}")).json()
        assert unchanged["displayName"] == "Renamed"
        assert unchanged["repositories"] == ["owner/first"]
        # An advisory provider outage does not block an unrelated permitted edit.
        renamed = await client.patch(
            f"/{cid}",
            json={
                "requestId": "4019-name-only",
                "expectedPolicyRevision": 2,
                "expectedCredentialRevision": 2,
                "displayName": "Safe draft",
            },
        )
        assert renamed.status_code == 200, renamed.text
        async with sessions() as db:
            secret = (await db.execute(select(ManagedSecret))).scalars().one()
            assert (
                await SecretsService.get_secret(db, secret.slug)
                == "github_pat_4019_rotated_sentinel"
            )


@pytest.mark.asyncio
async def test_settings_rejects_browser_authority_and_redacts_refused_credentials(
    tmp_path, monkeypatch
):
    async with settings_client(tmp_path, monkeypatch) as configured:
        client, _, calls, _, user = configured
        payload = {**_create_payload(), "principalRef": "forged"}
        denied = await client.post("", json=payload)
        assert denied.status_code == 422
        assert payload["plaintext"] not in denied.text
        assert "forged" not in denied.text
        assert not calls
        user.is_superuser = False
        user.settings_permissions = {"settings.effective.read"}
        forbidden = await client.post("", json=_create_payload())
        assert forbidden.status_code == 403
        assert not calls


@pytest.mark.asyncio
async def test_settings_disable_is_revision_fenced_and_preserves_credentials_and_assignments(
    tmp_path, monkeypatch
):
    async with settings_client(tmp_path, monkeypatch) as configured:
        client, sessions, _, _, _ = configured
        payload = _create_payload()
        assert (await client.post("", json=payload)).status_code == 201
        cid = payload["connectionId"]
        request = {
            "requestId": "4019-disable",
            "expectedPolicyRevision": 1,
            "expectedCredentialRevision": 1,
        }
        saved = await client.post(f"/{cid}/disable", json=request)
        assert saved.status_code == 200, saved.text
        assert saved.json()["lifecycle"] == "disabled"
        assert saved.json()["repositories"] == ["owner/first"]
        replay = await client.post(f"/{cid}/disable", json=request)
        assert replay.json() == saved.json()
        conflict = await client.post(
            f"/{cid}/disable", json={**request, "requestId": "4019-stale-disable"}
        )
        assert conflict.status_code == 409
        async with sessions() as db:
            assert len((await db.execute(select(ManagedSecret))).scalars().all()) == 1


def _client(monkeypatch) -> TestClient:
    monkeypatch.setenv("MOONMIND_GITHUB_APP_SETUP_SECRET", "router-test-secret")
    router_module._setup_service = None
    app = FastAPI()
    app.include_router(router_module.router, prefix="/connections")

    async def _user():
        return SimpleNamespace(id="principal:alice")

    app.dependency_overrides[get_current_user()] = _user
    return TestClient(app, base_url="http://testserver/connections/")


def test_begin_returns_setup_url_and_state(monkeypatch) -> None:
    client = _client(monkeypatch)
    response = client.post(
        "/github-app/begin",
        json={
            "appSlug": "moonmind-test",
            "expectedAppRef": "github-app:123456",
            "appId": "123456",
            "keySecretRef": "db://github-app-key",
            "requestId": "req:router-1",
            "connectionId": "repository-connection:app",
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
    app.include_router(router_module.router, prefix="/connections")

    async def _deny():
        raise HTTPException(status_code=401, detail="unauthenticated")

    app.dependency_overrides[get_current_user()] = _deny
    client = TestClient(
        app, base_url="http://testserver/connections/", raise_server_exceptions=False
    )
    response = client.post(
        "/github-app/begin",
        json={
            "appSlug": "moonmind-test",
            "expectedAppRef": "github-app:123456",
            "appId": "123456",
            "keySecretRef": "db://github-app-key",
            "requestId": "req:router-denied",
            "connectionId": "repository-connection:app",
        },
    )
    assert response.status_code == 401


def test_callback_rejects_forged_state_before_touching_writer(monkeypatch) -> None:
    async def _session():
        return object()

    app = FastAPI()
    app.include_router(router_module.router, prefix="/connections")

    async def _user():
        return SimpleNamespace(id="principal:alice")

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
    client = TestClient(
        app, base_url="http://testserver/connections/", raise_server_exceptions=False
    )
    response = client.post(
        "/github-app/callback",
        json={
            "state": "forged-state",
            "installationId": "123",
            "connectionId": "repository-connection:app",
        },
    )
    assert response.status_code in (400, 409, 422), response.text
