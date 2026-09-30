"""Source Control Settings over the existing connection/secret owners (#4019).

Real FastAPI router + SQLite database + the production secret resolver and
bound acquisition. Only GitHub itself is replaced, by an ``httpx`` mock
transport that records every request so tests can prove which credential
each call used.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
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

_REAL_ASYNC_CLIENT = httpx.AsyncClient

_ACCOUNTS = {
    "token-alpha-0001": {"id": 101, "login": "alpha-bot", "type": "User"},
    "token-alpha-0002": {"id": 101, "login": "alpha-bot", "type": "User"},
    "token-beta-0001": {"id": 202, "login": "beta-bot", "type": "User"},
}


class FakeGitHub:
    """Minimal GitHub REST fake keyed by the presented bearer token."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.overrides: dict[str, Callable[[httpx.Request], httpx.Response]] = {}
        self.default_branch = "develop"

    def tokens_used(self) -> list[str]:
        return [
            request.headers.get("authorization", "").removeprefix("Bearer ")
            for request in self.requests
        ]

    def paths(self) -> list[str]:
        return [request.url.path for request in self.requests]

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        for prefix, override in self.overrides.items():
            if path.startswith(prefix):
                return override(request)
        token = request.headers.get("authorization", "").removeprefix("Bearer ")
        account = _ACCOUNTS.get(token)
        if account is None:
            return httpx.Response(401, json={"message": "Bad credentials"})
        if path == "/user":
            return httpx.Response(200, json=account)
        if path == "/user/repos":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 9001,
                        "full_name": "acme/app",
                        "default_branch": self.default_branch,
                        "private": True,
                    }
                ],
            )
        if path.startswith("/repos/acme/app"):
            rest = path.removeprefix("/repos/acme/app")
            if rest == "":
                return httpx.Response(
                    200,
                    json={
                        "id": 9001,
                        "full_name": "acme/app",
                        "default_branch": self.default_branch,
                        "private": True,
                    },
                )
            if rest.startswith("/branches/"):
                return httpx.Response(200, json={"name": rest.split("/")[-1]})
            return httpx.Response(200, json=[])
        return httpx.Response(404, json={"message": "Not Found"})


@pytest_asyncio.fixture
async def harness(tmp_path: Path, monkeypatch):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/source-control-4019.db", future=True
    )
    sessions = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    # The production DB secret resolver reads through the same database.
    monkeypatch.setattr(
        "moonmind.auth.resolvers.db_resolver.async_session_maker", sessions
    )
    # Ambient credentials must never be consulted by connection-bound paths.
    monkeypatch.setenv("GITHUB_TOKEN", "ambient-token-should-not-be-used")

    github = FakeGitHub()
    transport = httpx.MockTransport(github.handler)

    def _client_factory(*args, **kwargs):
        kwargs.setdefault("transport", transport)
        return _REAL_ASYNC_CLIENT(*args, **kwargs)

    app = FastAPI()
    app.include_router(router_module.router, prefix="/api/v1/repository-connections")

    async def _session():
        async with sessions() as session:
            yield session

    async def _user():
        return {"ref": "operator"}

    app.dependency_overrides[get_async_session] = _session
    app.dependency_overrides[get_current_user()] = _user

    api = AsyncClient(transport=ASGITransport(app=app), base_url="http://moonmind")
    with patch(
        "moonmind.workflows.adapters.github_service.httpx.AsyncClient",
        side_effect=_client_factory,
    ):
        try:
            yield api, github, sessions
        finally:
            await api.aclose()
            await engine.dispose()


async def _create(api: AsyncClient, *, connection_id: str, token: str, **extra):
    body = {
        "requestId": extra.pop("requestId", f"req-create-{connection_id}"),
        "connectionId": connection_id,
        "displayName": extra.pop("displayName", f"Connection {connection_id}"),
        "token": token,
        **extra,
    }
    return await api.post("/api/v1/repository-connections/pat", json=body)


async def _count(sessions, model) -> int:
    async with sessions() as db:
        return len((await db.execute(select(model))).scalars().all())


def _assert_no_token(text: str) -> None:
    for token in _ACCOUNTS:
        assert token not in text


async def test_two_connections_list_actual_accounts_and_zero_assignments_grant_nothing(
    harness,
):
    api, _github, sessions = harness

    first = await _create(api, connection_id="alpha", token="token-alpha-0001")
    second = await _create(
        api, connection_id="beta", token="token-beta-0001", allowedOperations=["read", "write"]
    )
    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text

    listing = await api.get("/api/v1/repository-connections")
    assert listing.status_code == 200, listing.text
    items = {item["id"]: item for item in listing.json()["items"]}
    assert set(items) == {"alpha", "beta"}
    assert items["alpha"]["account"] == "alpha-bot"
    assert items["beta"]["account"] == "beta-bot"
    assert items["beta"]["allowedOperations"] == ["read", "write"]
    for item in items.values():
        assert item["credentialKind"] == "pat"
        assert item["assignments"] == []
        # Zero assignments is reported as no access, never as wildcard access.
        assert item["state"] == "no_repositories"
    assert {mode["mode"] for mode in listing.json()["probeModes"]} >= {
        "indexing",
        "publish",
    }
    for text in (first.text, second.text, listing.text):
        _assert_no_token(text)
    assert await _count(sessions, RepositoryConnectionRecord) == 2
    assert await _count(sessions, ManagedSecret) == 2


async def test_lost_save_acknowledgment_replays_the_committed_connection(harness):
    api, github, sessions = harness

    created = await _create(
        api, connection_id="alpha", token="token-alpha-0001", requestId="req-lost-ack"
    )
    assert created.status_code == 201, created.text
    validations = github.paths().count("/user")

    # The browser never saw the acknowledgment and resubmits the same request.
    replayed = await _create(
        api, connection_id="alpha", token="token-alpha-0001", requestId="req-lost-ack"
    )
    assert replayed.status_code == 201, replayed.text
    assert replayed.json()["id"] == "alpha"
    assert replayed.json()["policyRevision"] == created.json()["policyRevision"]
    # Replay reconciles against the committed row without another validation.
    assert github.paths().count("/user") == validations
    assert await _count(sessions, RepositoryConnectionRecord) == 1
    assert await _count(sessions, ManagedSecret) == 1

    # A different request for the same ID is a conflict, never a suffixed row.
    conflict = await _create(
        api, connection_id="alpha", token="token-alpha-0001", requestId="req-other"
    )
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["detail"]["kind"] == "conflict"
    assert await _count(sessions, RepositoryConnectionRecord) == 1
    assert await _count(sessions, ManagedSecret) == 1


async def test_failed_candidate_validation_saves_nothing_and_allows_the_same_request(
    harness,
):
    api, github, sessions = harness

    rejected = await _create(
        api, connection_id="alpha", token="token-unknown-0001", requestId="req-partial"
    )
    assert rejected.status_code == 422, rejected.text
    assert rejected.json()["detail"]["kind"] == "authentication"
    _assert_no_token(rejected.text)
    assert "token-unknown-0001" not in rejected.text
    assert await _count(sessions, RepositoryConnectionRecord) == 0
    assert await _count(sessions, ManagedSecret) == 0

    def _offline(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    github.overrides["/user"] = _offline
    unavailable = await _create(
        api, connection_id="alpha", token="token-alpha-0001", requestId="req-partial"
    )
    assert unavailable.status_code == 502, unavailable.text
    detail = unavailable.json()["detail"]
    assert detail["kind"] == "unavailable"
    assert "permission" not in detail["message"].lower()
    assert "classic" not in detail["message"].lower()
    assert await _count(sessions, ManagedSecret) == 0

    github.overrides.clear()
    accepted = await _create(
        api, connection_id="alpha", token="token-alpha-0001", requestId="req-partial"
    )
    assert accepted.status_code == 201, accepted.text
    # Only the selected candidate was ever presented to GitHub.
    assert "ambient-token-should-not-be-used" not in github.tokens_used()


async def test_rotation_is_revision_fenced_and_never_switches_accounts(harness):
    api, github, _sessions = harness
    created = await _create(api, connection_id="alpha", token="token-alpha-0001")
    assert created.status_code == 201, created.text
    revision = created.json()["secretRevision"]

    stale = await api.post(
        "/api/v1/repository-connections/alpha/rotate",
        json={
            "requestId": "req-rotate-stale",
            "token": "token-alpha-0002",
            "expectedSecretRevision": revision + 5,
        },
    )
    assert stale.status_code == 409, stale.text
    assert stale.json()["detail"]["kind"] == "conflict"

    switched = await api.post(
        "/api/v1/repository-connections/alpha/rotate",
        json={
            "requestId": "req-rotate-switch",
            "token": "token-beta-0001",
            "expectedSecretRevision": revision,
        },
    )
    assert switched.status_code == 409, switched.text
    assert switched.json()["detail"]["kind"] == "account_mismatch"

    rotated = await api.post(
        "/api/v1/repository-connections/alpha/rotate",
        json={
            "requestId": "req-rotate-ok",
            "token": "token-alpha-0002",
            "expectedSecretRevision": revision,
        },
    )
    assert rotated.status_code == 200, rotated.text
    assert rotated.json()["secretRevision"] == revision + 1
    assert rotated.json()["account"] == "alpha-bot"
    _assert_no_token(rotated.text)

    github.requests.clear()
    probe = await api.post(
        "/api/v1/repository-connections/alpha/probe",
        json={"repo": "acme/app", "mode": "indexing"},
    )
    assert probe.status_code == 200, probe.text
    assert set(github.tokens_used()) == {"token-alpha-0002"}


async def test_probe_uses_only_the_selected_connection_and_the_remote_default_branch(
    harness,
):
    api, github, _sessions = harness
    await _create(api, connection_id="alpha", token="token-alpha-0001")
    await _create(api, connection_id="beta", token="token-beta-0001")

    github.requests.clear()
    probe = await api.post(
        "/api/v1/repository-connections/beta/probe",
        json={"repo": "acme/app", "mode": "publish"},
    )
    assert probe.status_code == 200, probe.text
    result = probe.json()
    assert set(github.tokens_used()) == {"token-beta-0001"}
    assert result["connectionId"] == "beta"
    assert result["resolvedBranch"] == "develop"
    assert result["branchSource"] == "remote_default"
    assert "/repos/acme/app/branches/develop" in github.paths()
    assert all("/branches/main" not in path for path in github.paths())
    checklist = {item["permission"]: item for item in result["permissionChecklist"]}
    # Completed read checks never prove write permission.
    assert checklist["Contents"]["status"] == "verified_read_access"
    assert checklist["Pull requests"]["status"] == "verified_read_access"
    assert result["writeVerified"] is False
    _assert_no_token(probe.text)


async def test_probe_rejects_unknown_modes_before_network_access(harness):
    api, github, _sessions = harness
    await _create(api, connection_id="alpha", token="token-alpha-0001")
    github.requests.clear()

    response = await api.post(
        "/api/v1/repository-connections/alpha/probe",
        json={"repo": "acme/app", "mode": "everything"},
    )
    assert response.status_code == 422, response.text
    assert github.requests == []


async def test_probe_distinguishes_throttling_and_stops_probing(harness):
    api, github, _sessions = harness
    await _create(api, connection_id="alpha", token="token-alpha-0001")

    def _throttled(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            headers={"retry-after": "30"},
            json={"message": "API rate limit exceeded"},
        )

    github.overrides["/repos/acme/app/branches/"] = _throttled
    github.requests.clear()
    response = await api.post(
        "/api/v1/repository-connections/alpha/probe",
        json={"repo": "acme/app", "mode": "full_pr_automation", "baseBranch": "release"},
    )
    assert response.status_code == 200, response.text
    result = response.json()
    kinds = [entry["kind"] for entry in result["diagnostics"]]
    assert "rate_limited" in kinds
    assert "permission" not in kinds
    # No further endpoints are probed after a known throttle.
    assert github.paths()[-1] == "/repos/acme/app/branches/release"
    checklist = {item["permission"]: item for item in result["permissionChecklist"]}
    assert checklist["Contents"]["status"] != "failed"
    assert checklist["Pull requests"]["status"] == "not_checked"


async def test_unavailable_probe_is_not_reported_as_denied(harness):
    api, github, _sessions = harness
    await _create(api, connection_id="alpha", token="token-alpha-0001")

    def _timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    github.overrides["/repos/acme/app"] = _timeout
    response = await api.post(
        "/api/v1/repository-connections/alpha/probe",
        json={"repo": "acme/app", "mode": "indexing"},
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["repositoryAccessible"] is None
    assert [entry["kind"] for entry in result["diagnostics"]] == ["unavailable"]
    assert result["diagnostics"][0]["retryable"] is True


async def test_assignments_are_verified_through_the_selected_connection_and_partial_discovery_keeps_them(
    harness,
):
    api, github, sessions = harness
    await _create(api, connection_id="alpha", token="token-alpha-0001")

    github.requests.clear()
    assigned = await api.post(
        "/api/v1/repository-connections/alpha/assignments",
        json={"requestId": "req-assign-1", "repository": "acme/app"},
    )
    assert assigned.status_code == 200, assigned.text
    assert set(github.tokens_used()) == {"token-alpha-0001"}
    view = assigned.json()
    assert view["state"] == "ready"
    assert view["assignments"] == [
        {
            "repository": "acme/app",
            "providerRepoId": "9001",
            "operations": ["read"],
            "revision": 1,
        }
    ]

    pages = {"count": 0}

    def _partial(request: httpx.Request) -> httpx.Response:
        pages["count"] += 1
        if request.url.params.get("page") == "1":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 7000 + index,
                        "full_name": f"acme/repo-{index}",
                        "default_branch": "trunk",
                        "private": False,
                    }
                    for index in range(100)
                ],
            )
        return httpx.Response(502, json={"message": "Server Error"})

    github.overrides["/user/repos"] = _partial
    discovery = await api.get("/api/v1/repository-connections/alpha/repositories")
    assert discovery.status_code == 200, discovery.text
    found = discovery.json()
    assert found["complete"] is False
    assert len(found["repositories"]) == 100
    assert found["repositories"][0]["defaultBranch"] == "trunk"
    assert [entry["kind"] for entry in found["diagnostics"]] == ["unavailable"]

    listing = await api.get("/api/v1/repository-connections")
    alpha = listing.json()["items"][0]
    assert [row["repository"] for row in alpha["assignments"]] == ["acme/app"]
    assert await _count(sessions, RepositoryConnectionAssignment) == 1


async def test_assignment_rejects_operations_the_connection_does_not_allow(harness):
    api, _github, sessions = harness
    await _create(api, connection_id="alpha", token="token-alpha-0001")

    response = await api.post(
        "/api/v1/repository-connections/alpha/assignments",
        json={
            "requestId": "req-assign-write",
            "repository": "acme/app",
            "operations": ["read", "write"],
        },
    )
    assert response.status_code == 422, response.text
    assert await _count(sessions, RepositoryConnectionAssignment) == 0


async def test_remove_deletes_the_connection_and_its_internal_secret_only_when_unassigned(
    harness,
):
    api, _github, sessions = harness
    await _create(api, connection_id="alpha", token="token-alpha-0001")
    await api.post(
        "/api/v1/repository-connections/alpha/assignments",
        json={"requestId": "req-assign-1", "repository": "acme/app"},
    )

    blocked = await api.delete(
        "/api/v1/repository-connections/alpha", params={"requestId": "req-delete-1"}
    )
    assert blocked.status_code == 409, blocked.text
    assert await _count(sessions, ManagedSecret) == 1

    unassigned = await api.post(
        "/api/v1/repository-connections/alpha/assignments/remove",
        json={
            "requestId": "req-unassign-1",
            "providerRepoId": "9001",
            "repository": "acme/app",
        },
    )
    assert unassigned.status_code == 200, unassigned.text
    assert unassigned.json()["assignments"] == []

    removed = await api.delete(
        "/api/v1/repository-connections/alpha", params={"requestId": "req-delete-2"}
    )
    assert removed.status_code == 200, removed.text
    assert removed.json() == {"connectionId": "alpha", "credentialRemoved": True}
    listing = await api.get("/api/v1/repository-connections")
    assert listing.json()["items"] == []
    assert await _count(sessions, ManagedSecret) == 0

    reused = await _create(
        api, connection_id="alpha", token="token-alpha-0001", requestId="req-reuse"
    )
    assert reused.status_code == 409, reused.text


async def test_setup_rejects_ids_that_cannot_name_a_managed_secret(harness):
    api, github, _sessions = harness
    response = await _create(api, connection_id="Alpha Team!", token="token-alpha-0001")
    assert response.status_code == 422, response.text
    assert github.requests == []
    assert "token-alpha-0001" not in json.dumps(response.json())
