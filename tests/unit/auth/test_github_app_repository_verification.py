"""Real-shaped, JWT-only repository installation verification."""

import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from moonmind.auth.github_app_wiring import fetch_installation_record


def _installation(**changes):
    record = {
        "id": 456,
        "app_id": 123,
        "account": {"id": 10, "login": "Acme"},
        "repository_selection": "selected",
        # Never use a provider-returned URL as a JWT destination.
        "repositories_url": "https://capture.example/installation/repositories",
        "suspended_at": None,
    }
    record.update(changes)
    return record


@pytest.fixture
def provider(monkeypatch):
    requests = []
    responses = {}
    client_type = httpx.AsyncClient

    def handle(request):
        requests.append(request)
        assert request.headers["Authorization"] == "Bearer test-jwt"
        result = responses.get(request.url.path, _installation())
        if isinstance(result, httpx.Response):
            return result
        return httpx.Response(200, json=result)

    def client(**kwargs):
        return client_type(transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)
    return requests, responses


@pytest.mark.parametrize(
    "api_base", ["https://api.github.com", "https://git.example/api/v3"]
)
@pytest.mark.parametrize("selection", ["selected", "all"])
def test_fetch_verifies_only_explicit_repository_associations(
    provider, api_base, selection
):
    requests, responses = provider
    prefix = httpx.URL(api_base).path.rstrip("/")
    responses[f"{prefix}/app/installations/456"] = _installation(
        repository_selection=selection, repositories=["unrequested/repo"]
    )
    record = asyncio.run(
        fetch_installation_record(
            jwt="test-jwt",
            installation_id="456",
            api_base=api_base,
            permitted_repositories=("Acme/Repo", "Acme/Other"),
        )
    )
    assert record["repositories"] == ["Acme/Repo", "Acme/Other"]
    assert [str(request.url) for request in requests] == [
        f"{api_base}/app/installations/456",
        f"{api_base}/repos/Acme/Repo/installation",
        f"{api_base}/repos/Acme/Other/installation",
    ]
    assert all(request.method == "GET" for request in requests)


@pytest.mark.parametrize(
    "scope", [(), ("*",), ("Acme/*",), ("Acme/..",), ("../repo",), ("Acme/repo?x=1",)]
)
def test_fetch_rejects_empty_or_invalid_explicit_scope_without_traffic(provider, scope):
    requests, _ = provider
    with pytest.raises(ValueError, match="repositories|owner/name"):
        asyncio.run(
            fetch_installation_record(
                jwt="test-jwt", installation_id="456", permitted_repositories=scope
            )
        )
    assert requests == []


@pytest.mark.parametrize(
    "change",
    [
        {"id": 999},
        {"app_id": 999},
        {"account": {"id": 20, "login": "Acme"}},
        {"account": {"id": 10, "login": "other"}},
        {"account": None},
        {"suspended_at": "2026-10-01T00:00:00Z"},
    ],
    ids=[
        "installation",
        "app",
        "account-id",
        "account-login",
        "missing-account",
        "suspended",
    ],
)
def test_fetch_rejects_repository_installation_mismatch(provider, change):
    requests, responses = provider
    responses["/repos/Acme/Repo/installation"] = _installation(**change)
    with pytest.raises(ValueError, match="installation"):
        asyncio.run(
            fetch_installation_record(
                jwt="test-jwt",
                installation_id="456",
                permitted_repositories=("Acme/Repo",),
            )
        )
    assert len(requests) == 2


@pytest.mark.parametrize("status", [301, 302, 307, 308, 401, 403, 404])
def test_fetch_denies_provider_error_or_redirect_without_following(provider, status):
    requests, responses = provider
    responses["/repos/Acme/Repo/installation"] = httpx.Response(
        status, headers={"Location": "https://capture.example/repo"}
    )
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(
            fetch_installation_record(
                jwt="test-jwt",
                installation_id="456",
                permitted_repositories=("Acme/Repo",),
            )
        )
    assert [request.url.host for request in requests] == ["api.github.com"] * 2
    assert all(request.method == "GET" for request in requests)


def test_metadata_only_fetch_does_not_enumerate_repositories(provider):
    requests, _ = provider
    record = asyncio.run(
        fetch_installation_record(jwt="test-jwt", installation_id="456")
    )
    assert "repositories" not in record
    assert len(requests) == 1


def test_selected_installation_acquisition_accepts_repository_case_only(provider):
    from moonmind.auth.github_app_wiring import acquire_bound_credential_for_connection
    from moonmind.workflows.executions.repository_contract import RepositoryConnection

    requests, responses = provider
    responses["/app/installations/456/access_tokens"] = {
        "token": "opaque-test-token",
        "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(),
        "permissions": {"contents": "read", "metadata": "read"},
        "repositories": [{"id": 50, "name": "Repo", "full_name": "Acme/Repo"}],
    }
    connection = RepositoryConnection.model_validate(
        {
            "schemaVersion": "moonmind.repository-connection.v1",
            "displayName": "App connection",
            "clientPolicy": {
                "pinnedVersion": "2.46.0",
                "toolBundleRef": "git:test",
                "executableSha256": "sha256:git",
            },
            "id": "app-connection",
            "provider": "git",
            "endpointRef": "https://github.com",
            "allowedOperations": ["read"],
            "credential": {
                "source": "github_app",
                "appRef": "github-app:123",
                "installationRef": "456",
                "account": "Acme",
                "keyRef": "db://github-app-key",
                "permittedRepositories": ["ACME/REPO"],
            },
            "ownership": {"scopeType": "system", "ownerRef": "operator"},
        }
    )
    acquired = asyncio.run(
        acquire_bound_credential_for_connection(
            connection,
            operations=("read",),
            principal_ref="operator",
            execution_owner="test:case",
            repository="acme/repo",
            repository_display="acme/repo",
            resolve_secret=lambda _ref: b"test-key",
            make_jwt=lambda _key: "test-jwt",
        )
    )
    assert acquired.binding.adapter_kind == "github_app"
    assert requests[-1].method == "POST"
    import json

    assert json.loads(requests[-1].content)["repositories"] == ["repo"]
    assert [request.url.path for request in requests[:-1]] == [
        "/app/installations/456",
        "/repos/ACME/REPO/installation",
    ]


@pytest.mark.parametrize(
    "permitted,candidates,covered",
    [
        ("Acme/Repo", {"acme/repo"}, True),
        ("acme/repo", {"other/repo", "repo"}, False),
        ("acme/repo", {"repo"}, False),
        ("Repo", {"Acme/repo"}, True),
    ],
)
def test_repository_coverage_preserves_qualified_owner(permitted, candidates, covered):
    from moonmind.auth.github_app import permitted_repo_covered

    assert permitted_repo_covered(permitted, candidates) is covered


@pytest.mark.parametrize("selection", ["all", "selected"])
def test_existing_numeric_scope_keeps_exact_restricted_acquisition(provider, selection):
    import json

    from moonmind.auth.bound_acquisition import BoundAccessError
    from moonmind.auth.github_app_wiring import acquire_bound_credential_for_connection
    from tests.unit.auth.test_github_app_wiring_4022 import _app_connection

    requests, responses = provider
    responses["/app/installations/456"] = _installation(repository_selection=selection)
    responses["/app/installations/456/access_tokens"] = {
        "token": "opaque-id-scoped-token",
        "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(),
        "permissions": {"contents": "read", "metadata": "read"},
        "repositories": [{"id": 50, "name": "Repo", "full_name": "Acme/Repo"}],
    }
    connection = _app_connection()
    connection = connection.model_copy(
        update={
            "credential": connection.credential.model_copy(
                update={
                    "app_ref": "github-app:123",
                    "installation_ref": "456",
                    "account": "Acme",
                    "permitted_repositories": ("50",),
                }
            ),
        }
    )
    acquisition = acquire_bound_credential_for_connection(
        connection,
        operations=("read",),
        principal_ref="principal:alice",
        execution_owner="test:numeric",
        repository="50",
        repository_display="50",
        resolve_secret=lambda _ref: b"test-key",
        make_jwt=lambda _key: "test-jwt",
    )
    if selection == "selected":
        with pytest.raises(BoundAccessError, match="repositories are not permitted"):
            asyncio.run(acquisition)
        assert [request.url.path for request in requests] == ["/app/installations/456"]
        return
    acquired = asyncio.run(acquisition)
    assert acquired.binding.adapter_kind == "github_app"
    assert [request.url.path for request in requests] == [
        "/app/installations/456",
        "/app/installations/456/access_tokens",
    ]
    assert json.loads(requests[-1].content) == {
        "repository_ids": [50],
        "permissions": {"contents": "read", "metadata": "read"},
    }
