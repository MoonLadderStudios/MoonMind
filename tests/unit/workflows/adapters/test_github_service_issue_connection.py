"""Issue collaboration uses the default repository connection (#4003).

Issue labels, comments and state act through the recorded default connection
and its ``issues`` operation, never an ambient token of another account. An
App connection issues for exactly that operation. Only an unrecorded default
derives from the deployment declaration; an explicit token stays explicit.
"""

from __future__ import annotations

import httpx
import pytest

from moonmind.workflows.adapters.github_service import GitHubService
from tests.helpers.repository_connections import (
    github_app_connection,
    github_app_provider_edge,
    github_pat_connection,
    github_repository_assignment,
    record_repository_connections,
)

_REPOSITORY = "MoonLadderStudios/MoonMind"
_DEFAULT = "repository-connection:git-default"
_WITH_ISSUES = ("read", "write", "branch_write", "review_request", "issues")


def _transport(monkeypatch):
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST" and request.url.path.endswith("/comments"):
            return httpx.Response(201, json={"id": 31})
        if request.method == "POST" and request.url.path.endswith("/labels"):
            return httpx.Response(200, json=[{"name": "status: in-progress"}])
        if request.method == "PATCH":
            return httpx.Response(200, json={"state": "closed"})
        raise AssertionError(f"Unexpected request {request.method} {request.url}")

    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs),
    )
    return requests


async def _collaborate(service: GitHubService, **kwargs) -> list[dict]:
    return [
        await service.add_issue_labels(
            repo=_REPOSITORY, issue_number=7, labels=["status: in-progress"], **kwargs
        ),
        await service.create_issue_comment(
            repo=_REPOSITORY, issue_number=7, body="attempt", **kwargs
        ),
        await service.close_issue(repo=_REPOSITORY, issue_number=7, **kwargs),
    ]


async def _record_default(monkeypatch, tmp_path, connection, operations=_WITH_ISSUES):
    connection = connection.model_copy(
        update={"id": _DEFAULT, "allowed_operations": operations}
    )
    return await record_repository_connections(
        monkeypatch,
        tmp_path,
        connection,
        assignments=[
            github_repository_assignment(_DEFAULT, _REPOSITORY, operations=operations)
        ],
    )


@pytest.mark.asyncio
async def test_recorded_default_is_used_over_an_ambient_token(monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_TOKEN", "ambient-account-a")
    monkeypatch.setenv("CONNECTION_B_PAT", "selected-account-b")
    requests = _transport(monkeypatch)
    engine = await _record_default(
        monkeypatch, tmp_path, github_pat_connection(_DEFAULT, "CONNECTION_B_PAT")
    )
    try:
        results = await _collaborate(GitHubService())
    finally:
        await engine.dispose()

    assert [result["ok"] for result in results] == [True, True, True], results
    assert [request.method for request in requests] == ["POST", "POST", "PATCH"]
    assert {request.headers["Authorization"] for request in requests} == {
        "Bearer selected-account-b"
    }


@pytest.mark.asyncio
async def test_app_default_issues_for_exactly_the_issues_operation(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("GITHUB_TOKEN", "ambient-account-a")
    issued = github_app_provider_edge(monkeypatch, "ghs_app_issue_token")
    requests = _transport(monkeypatch)
    app = github_app_connection(_DEFAULT, _REPOSITORY)
    # The enrolled restriction persists on the credential across reloads.
    app = app.model_copy(
        update={
            "credential": app.credential.model_copy(
                update={"permitted_repositories": (_REPOSITORY,)}
            )
        }
    )
    engine = await _record_default(monkeypatch, tmp_path, app)
    try:
        results = await _collaborate(GitHubService())
    finally:
        await engine.dispose()

    assert [result["ok"] for result in results] == [True, True, True], results
    assert {request.headers["Authorization"] for request in requests} == {
        "Bearer ghs_app_issue_token"
    }
    assert issued and all(
        payload["permissions"] == {"issues": "write"}
        and payload["repositories"] == ["MoonMind"]
        for payload in issued
    )


@pytest.mark.asyncio
async def test_default_without_issues_operation_fails_without_substitution(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("GITHUB_TOKEN", "ambient-account-a")
    monkeypatch.setenv("CONNECTION_B_PAT", "selected-account-b")
    requests = _transport(monkeypatch)
    engine = await _record_default(
        monkeypatch,
        tmp_path,
        github_pat_connection(_DEFAULT, "CONNECTION_B_PAT"),
        operations=("read", "write", "branch_write", "review_request"),
    )
    try:
        results = await _collaborate(GitHubService())
    finally:
        await engine.dispose()

    assert requests == []
    for result in results:
        assert result["ok"] is False
        assert result["reasonCode"] == "auth_unavailable"
        assert "issues" in result["summary"]
        assert "ambient-account-a" not in result["summary"]


@pytest.mark.asyncio
async def test_unreadable_selected_credential_is_not_replaced(monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_TOKEN", "ambient-account-a")
    monkeypatch.delenv("CONNECTION_B_PAT", raising=False)
    requests = _transport(monkeypatch)
    engine = await _record_default(
        monkeypatch, tmp_path, github_pat_connection(_DEFAULT, "CONNECTION_B_PAT")
    )
    try:
        results = await _collaborate(GitHubService())
    finally:
        await engine.dispose()

    assert requests == []
    assert all(result["reasonCode"] == "auth_unavailable" for result in results)
    assert all(
        "does not try another GitHub credential" in result["summary"]
        for result in results
    )


@pytest.mark.asyncio
async def test_default_serving_another_host_never_receives_the_request(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("CONNECTION_B_PAT", "selected-account-b")
    from moonmind.config.settings import settings

    monkeypatch.setattr(
        settings.github, "github_trusted_api_hosts", "github.example.com"
    )
    requests = _transport(monkeypatch)
    connection = github_pat_connection(_DEFAULT, "CONNECTION_B_PAT").model_copy(
        update={"endpoint_ref": "https://github.example.com"}
    )
    assignment = github_repository_assignment(
        _DEFAULT, _REPOSITORY, operations=_WITH_ISSUES
    )
    engine = await record_repository_connections(
        monkeypatch,
        tmp_path,
        connection.model_copy(update={"allowed_operations": _WITH_ISSUES}),
        assignments=[
            assignment.model_copy(
                update={
                    "identity": assignment.identity.model_copy(
                        update={"endpoint": "https://github.example.com"}
                    )
                }
            )
        ],
    )
    try:
        results = await _collaborate(GitHubService())
    finally:
        await engine.dispose()

    assert requests == []
    assert all("does not serve https://api.github.com" in r["summary"] for r in results)


@pytest.mark.asyncio
async def test_unrecorded_default_uses_the_deployment_declaration(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("GITHUB_TOKEN", "deployment-declared")
    requests = _transport(monkeypatch)
    engine = await record_repository_connections(monkeypatch, tmp_path)
    try:
        results = await _collaborate(GitHubService())
    finally:
        await engine.dispose()

    assert [result["ok"] for result in results] == [True, True, True], results
    assert {request.headers["Authorization"] for request in requests} == {
        "Bearer deployment-declared"
    }


@pytest.mark.asyncio
async def test_explicit_token_stays_explicit(monkeypatch, tmp_path):
    monkeypatch.setenv("CONNECTION_B_PAT", "selected-account-b")
    requests = _transport(monkeypatch)
    engine = await _record_default(
        monkeypatch, tmp_path, github_pat_connection(_DEFAULT, "CONNECTION_B_PAT")
    )
    try:
        results = await _collaborate(GitHubService(), github_token="explicit-token")
    finally:
        await engine.dispose()

    assert [result["ok"] for result in results] == [True, True, True], results
    assert {request.headers["Authorization"] for request in requests} == {
        "Bearer explicit-token"
    }
