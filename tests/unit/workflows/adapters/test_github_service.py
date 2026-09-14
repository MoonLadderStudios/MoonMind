"""Tests for GitHubService (repo.create_pr / repo.merge_pr)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from moonmind.workflows.adapters.github_service import (
    CreatePRResult,
    GitHubService,
    MergePRResult,
    PullRequestReadinessResult,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_response(status_code: int, json_body: dict) -> httpx.Response:
    """Build a mock httpx response."""
    return httpx.Response(
        status_code,
        json=json_body,
        request=httpx.Request("POST", "https://api.github.com/test"),
    )

def _mock_get_response(status_code: int, json_body: dict | list) -> httpx.Response:
    """Build a mock httpx GET response."""
    return httpx.Response(
        status_code,
        json=json_body,
        request=httpx.Request("GET", "https://api.github.com/test"),
    )

def _mock_get_response_with_headers(
    status_code: int, json_body: dict | list, headers: dict[str, str]
) -> httpx.Response:
    return httpx.Response(
        status_code,
        json=json_body,
        headers=headers,
        request=httpx.Request("GET", "https://api.github.com/test"),
    )


@pytest.fixture(autouse=True)
def _clear_github_probe_throttle_marks():
    """Isolate cross-probe throttle coordination between hermetic tests."""
    GitHubService._throttle_marks.clear()
    GitHubService._throttle_in_flight.clear()
    yield
    GitHubService._throttle_marks.clear()
    GitHubService._throttle_in_flight.clear()

# ---------------------------------------------------------------------------
# create_pull_request
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_pr_success(monkeypatch):
    """Successful PR creation returns created=True and the URL."""
    monkeypatch.setenv("GITHUB_TOKEN", "github-token-fixture")

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(
        return_value=_mock_response(
            201,
            {
                "html_url": "https://github.com/o/r/pull/42",
                "head": {"sha": "abc123"},
            },
        )
    )
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("moonmind.workflows.adapters.github_service.httpx.AsyncClient", return_value=mock_client):
        svc = GitHubService()
        result = await svc.create_pull_request(
            repo="o/r", head="feature", base="main", title="T", body="B",
        )

    assert isinstance(result, CreatePRResult)
    assert result.created is True
    assert result.url == "https://github.com/o/r/pull/42"
    assert result.head_sha == "abc123"
    assert result.adopted is False
    _args, kwargs = mock_client.post.call_args
    assert "draft" not in kwargs["json"]


@pytest.mark.asyncio
async def test_create_pr_draft_flag_reaches_rest_payload(monkeypatch):
    """draft=True is sent to the GitHub create endpoint."""
    monkeypatch.setenv("GITHUB_TOKEN", "github-token-fixture")

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(
        return_value=_mock_response(
            201,
            {
                "html_url": "https://github.com/o/r/pull/43",
                "head": {"sha": "def456"},
            },
        )
    )
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("moonmind.workflows.adapters.github_service.httpx.AsyncClient", return_value=mock_client):
        svc = GitHubService()
        result = await svc.create_pull_request(
            repo="o/r", head="feature", base="main", title="T", body="B",
            draft=True,
        )

    assert result.created is True
    _args, kwargs = mock_client.post.call_args
    assert kwargs["json"]["draft"] is True


@pytest.mark.asyncio
async def test_create_pr_adopts_existing_head_base_pr(monkeypatch):
    """MM-680: existing PRs for the same head/base are adopted before create."""
    monkeypatch.setenv("GITHUB_TOKEN", "github-token-fixture")

    existing_pr = {
        "number": 42,
        "html_url": "https://github.com/o/r/pull/42",
        "head": {"ref": "feature", "sha": "abc123", "repo": {"full_name": "o/r"}},
        "base": {"ref": "main"},
    }
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=_mock_get_response(200, [existing_pr]))
    mock_client.patch = AsyncMock(
        return_value=_mock_response(
            200,
            {
                "html_url": "https://github.com/o/r/pull/42",
                "head": {"sha": "def456"},
            },
        )
    )
    mock_client.post = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch(
        "moonmind.workflows.adapters.github_service.httpx.AsyncClient",
        return_value=mock_client,
    ):
        result = await GitHubService().create_pull_request(
            repo="o/r",
            head="feature",
            base="main",
            title="T",
            body="B",
        )

    assert result.created is False
    assert result.adopted is True
    assert result.url == "https://github.com/o/r/pull/42"
    assert result.head_sha == "def456"
    assert "updated existing PR metadata" in result.summary
    mock_client.patch.assert_awaited_once()
    assert mock_client.patch.await_args.args == (
        "https://api.github.com/repos/o/r/pulls/42",
    )
    assert mock_client.patch.await_args.kwargs["json"] == {"title": "T", "body": "B"}
    mock_client.post.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_pr_draft_request_rejects_existing_non_draft_pr(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "github-token-fixture")

    existing_pr = {
        "number": 42,
        "html_url": "https://github.com/o/r/pull/42",
        "draft": False,
        "head": {"ref": "feature", "sha": "abc123", "repo": {"full_name": "o/r"}},
        "base": {"ref": "main"},
    }
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=_mock_get_response(200, [existing_pr]))
    mock_client.patch = AsyncMock()
    mock_client.post = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch(
        "moonmind.workflows.adapters.github_service.httpx.AsyncClient",
        return_value=mock_client,
    ):
        result = await GitHubService().create_pull_request(
            repo="o/r",
            head="feature",
            base="main",
            title="T",
            body="B",
            draft=True,
        )

    assert result.created is False
    assert result.adopted is False
    assert result.url == "https://github.com/o/r/pull/42"
    assert result.head_sha == "abc123"
    assert "existing non-draft pull request" in result.summary
    mock_client.patch.assert_not_awaited()
    mock_client.post.assert_not_awaited()


def test_pull_request_head_match_fails_closed_on_missing_repo_metadata() -> None:
    svc = GitHubService()

    assert not svc._pull_request_matches_head_base(
        {
            "head": {"ref": "feature", "repo": {"full_name": ""}},
            "base": {"ref": "main"},
        },
        repo="o/r",
        head="feature",
        base="main",
    )
    assert not svc._pull_request_matches_head_base(
        {
            "head": {"ref": "feature", "repo": None},
            "base": {"ref": "main"},
        },
        repo="o/r",
        head="o:feature",
        base="main",
    )


@pytest.mark.asyncio
async def test_create_pr_retries_without_post_when_existing_pr_lookup_fails(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "github-token-fixture")

    lookup_response = _mock_get_response(503, {"message": "try later"})
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(
        side_effect=httpx.HTTPStatusError(
            "503",
            request=lookup_response.request,
            response=lookup_response,
        )
    )
    mock_client.post = AsyncMock(
        return_value=_mock_response(
            201,
            {
                "html_url": "https://github.com/o/r/pull/43",
                "head": {"sha": "def456"},
            },
        )
    )
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch(
        "moonmind.workflows.adapters.github_service.httpx.AsyncClient",
        return_value=mock_client,
    ):
        result = await GitHubService().create_pull_request(
            repo="o/r",
            head="feature",
            base="main",
            title="T",
            body="B",
        )

    assert result.created is False
    assert result.adopted is False
    assert result.url is None
    assert result.retryable is True
    mock_client.post.assert_not_awaited()

@pytest.mark.asyncio
async def test_create_pr_missing_token(monkeypatch):
    """Missing GITHUB_TOKEN should return created=False gracefully."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    svc = GitHubService()
    result = await svc.create_pull_request(
        repo="o/r", head="feature", base="main", title="T", body="B",
    )

    assert isinstance(result, CreatePRResult)
    assert result.created is False
    assert "GitHub auth is not configured" in result.summary


@pytest.mark.asyncio
async def test_resolve_pull_request_selector_resolves_exact_open_branch(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "github-token-fixture")
    pull_request = {
        "number": 3192,
        "html_url": "https://github.com/o/r/pull/3192",
        "head": {"ref": "feature/mm-1200", "repo": {"full_name": "o/r"}},
    }
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=_mock_get_response(200, [pull_request]))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch(
        "moonmind.workflows.adapters.github_service.httpx.AsyncClient",
        return_value=mock_client,
    ):
        result = await GitHubService().resolve_pull_request_selector(
            repo="o/r",
            selector="feature/mm-1200",
        )

    assert result.resolved is True
    assert result.pr_number == 3192
    assert result.pr_url == "https://github.com/o/r/pull/3192"
    assert mock_client.get.await_args.kwargs["params"]["head"] == "o:feature/mm-1200"


@pytest.mark.asyncio
async def test_resolve_pull_request_selector_rejects_cross_repo_url() -> None:
    result = await GitHubService().resolve_pull_request_selector(
        repo="o/r",
        selector="https://github.com/other/repo/pull/42",
    )

    assert result.resolved is False
    assert result.reason_code == "repository_mismatch"


@pytest.mark.asyncio
@pytest.mark.parametrize("branch", ["candidate", "123"])
@pytest.mark.parametrize("observed_sha", ["a" * 40, "b" * 40, None])
@pytest.mark.parametrize("matches", [1, 2])
async def test_resolve_publication_branch_requires_exact_head(
    branch, observed_sha, matches
):
    pull_request = {
        "number": 3192,
        "html_url": "https://github.com/o/r/pull/3192",
        "head": {"ref": branch, "repo": {"full_name": "o/r"}, "sha": observed_sha},
    }
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(
        return_value=_mock_get_response(200, [pull_request] * matches)
    )
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    with patch(
        "moonmind.workflows.adapters.github_service.httpx.AsyncClient",
        return_value=mock_client,
    ):
        result = await GitHubService().resolve_pull_request_selector(
            repo="o/r",
            selector=branch,
            github_token="fixture-credential",
            expected_head_sha="a" * 40,
        )
    assert result.resolved is (matches == 1 and observed_sha == "a" * 40)
    if matches == 2:
        assert result.reason_code == "pull_request_ambiguous"
    elif observed_sha != "a" * 40:
        assert result.reason_code == "head_sha_mismatch"
    assert mock_client.get.await_args.kwargs["params"]["head"] == f"o:{branch}"

@pytest.mark.asyncio
@pytest.mark.parametrize("branch", ["candidate", "123"])
@pytest.mark.parametrize(
    "fields, expectations, reason",
    [
        ({"base": {"ref": "main"}}, {"expected_base_branch": "main"}, "resolved"),
        (
            {"base": {"ref": "release"}},
            {"expected_base_branch": "main"},
            "base_branch_mismatch",
        ),
        ({"base": None}, {"expected_base_branch": "main"}, "base_branch_mismatch"),
        ({"draft": False}, {"expected_draft": False}, "resolved"),
        ({"draft": True}, {"expected_draft": False}, "draft_state_mismatch"),
        ({"draft": "false"}, {"expected_draft": False}, "draft_state_mismatch"),
        ({}, {"expected_draft": False}, "draft_state_mismatch"),
        ({"draft": True}, {"expected_draft": True}, "resolved"),
        ({}, {}, "resolved"),
    ],
)
async def test_resolve_publication_branch_validates_base_and_draft(
    branch,
    fields,
    expectations,
    reason,
):
    pull_request = {
        "number": 3192,
        "html_url": "https://github.com/o/r/pull/3192",
        "head": {"ref": branch, "repo": {"full_name": "o/r"}},
        **fields,
    }
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=_mock_get_response(200, [pull_request]))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    with patch(
        "moonmind.workflows.adapters.github_service.httpx.AsyncClient",
        return_value=mock_client,
    ):
        result = await GitHubService().resolve_pull_request_selector(
            repo="o/r",
            selector=branch,
            github_token="fixture-credential",
            **expectations,
        )
    assert result.resolved is (reason == "resolved")
    assert result.reason_code == reason
    if expectations:
        assert mock_client.get.await_args.kwargs["params"]["head"] == f"o:{branch}"


@pytest.mark.asyncio
async def test_create_pr_uses_secret_ref_when_env_missing(monkeypatch):
    """Secret-ref fallback should be used when raw env token is absent."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(
        return_value=_mock_response(201, {"html_url": "https://github.com/o/r/pull/43"})
    )
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    from moonmind.config.settings import settings as app_settings

    monkeypatch.setattr(
        app_settings.github,
        "github_token_secret_ref",
        "db://github-pat",
    )

    async def _fake_resolve(secret_ref: str) -> str:
        assert secret_ref == "db://github-pat"
        return "resolved-gh-token"

    with (
        patch(
            "moonmind.workflows.adapters.github_service.httpx.AsyncClient",
            return_value=mock_client,
        ),
        patch(
            "moonmind.workflows.temporal.runtime.managed_api_key_resolve.resolve_managed_api_key_reference",
            side_effect=_fake_resolve,
        ),
    ):
        svc = GitHubService()
        result = await svc.create_pull_request(
            repo="o/r", head="feature", base="main", title="T", body="B",
        )

    assert result.created is True
    _, kwargs = mock_client.post.call_args
    assert kwargs["headers"]["Authorization"] == "Bearer resolved-gh-token"

@pytest.mark.asyncio
async def test_create_pr_http_error(monkeypatch):
    """HTTP 422 from GitHub should return created=False."""
    monkeypatch.setenv("GITHUB_TOKEN", "github-token-fixture")

    mock_resp = _mock_response(422, {"message": "Validation Failed"})
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=httpx.HTTPStatusError(
        "422", request=mock_resp.request, response=mock_resp,
    ))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("moonmind.workflows.adapters.github_service.httpx.AsyncClient", return_value=mock_client):
        svc = GitHubService()
        result = await svc.create_pull_request(
            repo="o/r", head="feature", base="main", title="T", body="B",
        )

    assert result.created is False
    assert "422" in result.summary

@pytest.mark.asyncio
async def test_create_pr_http_error_includes_permission_diagnostic(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "github-token-fixture")

    mock_resp = _mock_response(
        403,
        {
            "message": "Resource not accessible by personal access token",
            "documentation_url": "https://docs.github.com/rest/pulls/pulls",
        },
    )
    mock_resp.headers["X-Accepted-GitHub-Permissions"] = "pull_requests=write"
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(
        side_effect=httpx.HTTPStatusError(
            "403", request=mock_resp.request, response=mock_resp,
        )
    )
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch(
        "moonmind.workflows.adapters.github_service.httpx.AsyncClient",
        return_value=mock_client,
    ):
        result = await GitHubService().create_pull_request(
            repo="o/r", head="feature", base="main", title="T", body="B",
        )

    assert result.created is False
    assert "HTTP 403" in result.summary
    assert "Resource not accessible by personal access token" in result.summary
    assert "pull_requests=write" in result.summary
    assert "https://docs.github.com/rest/pulls/pulls" in result.summary


@pytest.mark.asyncio
async def test_github_permission_diagnostic_redacts_token_like_provider_body(
    monkeypatch,
):
    monkeypatch.setenv("GITHUB_TOKEN", "github-token-fixture")
    leaked = "github_pat_1234567890abcdefghijklmnopqrstuvwxyz"
    mock_resp = _mock_response(
        403,
        {
            "message": f"Resource not accessible for {leaked}",
            "documentation_url": "https://docs.github.com/rest/pulls/pulls",
        },
    )
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(
        side_effect=httpx.HTTPStatusError(
            "403", request=mock_resp.request, response=mock_resp,
        )
    )
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch(
        "moonmind.workflows.adapters.github_service.httpx.AsyncClient",
        return_value=mock_client,
    ):
        result = await GitHubService().create_pull_request(
            repo="o/r", head="feature", base="main", title="T", body="B",
        )

    assert leaked not in result.summary
    assert "[REDACTED]" in result.summary


def test_github_permission_profiles_define_required_modes():
    profiles = GitHubService.github_permission_profiles()

    assert profiles["indexing"].required_permissions == {"Contents": "read"}
    assert profiles["publish"].required_permissions["Contents"] == "write"
    assert profiles["publish"].required_permissions["Pull requests"] == "write"
    assert profiles["readiness"].required_permissions["Pull requests"] == "read"
    assert profiles["readiness"].required_permissions["Checks"] == "read"
    assert profiles["readiness"].required_permissions["Commit statuses"] == "read"
    assert profiles["readiness"].required_permissions["Issues"] == "read"


@pytest.mark.asyncio
async def test_probe_github_token_targets_repo_and_reports_publish_checklist(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "github-token-fixture")

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(
        side_effect=[
            _mock_get_response(
                200,
                {"full_name": "owner/repo", "id": 123, "default_branch": "main"},
            ),
            _mock_get_response(200, {"name": "main"}),
            _mock_get_response(200, []),
        ]
    )
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch(
        "moonmind.workflows.adapters.github_service.httpx.AsyncClient",
        return_value=mock_client,
    ):
        result = await GitHubService().probe_token(
            repo="owner/repo", mode="publish", base_branch="main"
        )

    assert result["repo"] == "owner/repo"
    assert result["mode"] == "publish"
    assert result["credentialSource"]["sourceName"] == "GITHUB_TOKEN"
    assert [call.args[0] for call in mock_client.get.call_args_list] == [
        "https://api.github.com/repos/owner/repo",
        "https://api.github.com/repos/owner/repo/branches/main",
        "https://api.github.com/repos/owner/repo/pulls?per_page=1",
    ]
    checklist = {
        item["permission"]: item for item in result["permissionChecklist"]
    }
    assert checklist["Contents"]["level"] == "write"
    assert checklist["Contents"]["status"] == "verified_read_access"
    assert checklist["Pull requests"]["level"] == "write"
    assert checklist["Pull requests"]["status"] == "verified_read_access"
    assert checklist["Checks"]["status"] == "not_checked"
    assert any("resource owner" in item for item in result["limitations"])
    assert any("GitHub App" in item for item in result["limitations"])
    assert result["writesPerformed"] == 0
    assert result["activeWriteTest"] == "not_requested"
    assert result["capabilityBundleVersion"] == "github-capabilities.v1"
    assert result["resolvedRef"] == "main"
    assert result["repositoryIdentity"]["fullName"] == "owner/repo"


@pytest.mark.asyncio
async def test_probe_github_token_uses_indexing_mode_checks(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "github-token-fixture")

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(
        side_effect=[
            _mock_get_response(
                200,
                {"full_name": "owner/repo", "id": 123, "default_branch": "main"},
            ),
            _mock_get_response(200, {"name": "main"}),
        ]
    )
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch(
        "moonmind.workflows.adapters.github_service.httpx.AsyncClient",
        return_value=mock_client,
    ):
        result = await GitHubService().probe_token(
            repo="owner/repo", mode="indexing", base_branch="main"
        )

    assert [call.args[0] for call in mock_client.get.call_args_list] == [
        "https://api.github.com/repos/owner/repo",
        "https://api.github.com/repos/owner/repo/branches/main",
    ]
    assert result["pullRequestAccessible"] is None
    checklist = {
        item["permission"]: item for item in result["permissionChecklist"]
    }
    assert checklist["Contents"]["status"] == "passed"


@pytest.mark.asyncio
async def test_probe_github_token_uses_readiness_mode_checks(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "github-token-fixture")

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(
        side_effect=[
            _mock_get_response(
                200,
                {"full_name": "owner/repo", "id": 123, "default_branch": "main"},
            ),
            _mock_get_response(200, []),
            _mock_get_response(200, {"state": "success"}),
            _mock_get_response(200, {"check_runs": []}),
            _mock_get_response(200, []),
        ]
    )
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch(
        "moonmind.workflows.adapters.github_service.httpx.AsyncClient",
        return_value=mock_client,
    ):
        result = await GitHubService().probe_token(
            repo="owner/repo", mode="readiness", base_branch="main"
        )

    assert [call.args[0] for call in mock_client.get.call_args_list] == [
        "https://api.github.com/repos/owner/repo",
        "https://api.github.com/repos/owner/repo/pulls?per_page=1",
        "https://api.github.com/repos/owner/repo/commits/main/status",
        "https://api.github.com/repos/owner/repo/commits/main/check-runs",
        "https://api.github.com/repos/owner/repo/issues?per_page=1",
    ]
    assert result["defaultBranchAccessible"] is None
    checklist = {
        item["permission"]: item for item in result["permissionChecklist"]
    }
    assert checklist["Pull requests"]["status"] == "passed"
    assert checklist["Commit statuses"]["status"] == "passed"
    assert checklist["Checks"]["status"] == "passed"
    assert checklist["Issues"]["status"] == "passed"


def _mock_http_error(status_code: int, json_body: dict, headers: dict | None = None) -> httpx.Response:
    request = httpx.Request("GET", "https://api.github.com/test")
    response = httpx.Response(status_code, json=json_body, headers=headers or {}, request=request)
    return response


def _probe_client(side_effects: list) -> AsyncMock:
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=side_effects)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return mock_client


@pytest.mark.asyncio
async def test_probe_rejects_unknown_mode_before_network(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "github-token-fixture")
    mock_client = _probe_client([])
    with patch(
        "moonmind.workflows.adapters.github_service.httpx.AsyncClient",
        return_value=mock_client,
    ):
        result = await GitHubService().probe_token(repo="owner/repo", mode="nope")
    assert mock_client.get.await_count == 0
    assert result["reasonCode"] == "unsupported_mode"
    assert result["permissionChecklist"] == []
    assert result["diagnostics"][0]["reasonCode"] == "unsupported_mode"


@pytest.mark.asyncio
async def test_probe_resolves_remote_default_branch_when_omitted(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "github-token-fixture")
    mock_client = _probe_client(
        [
            _mock_get_response(
                200, {"full_name": "o/r", "id": 9, "default_branch": "trunk"}
            ),
            _mock_get_response(200, {"name": "trunk"}),
        ]
    )
    with patch(
        "moonmind.workflows.adapters.github_service.httpx.AsyncClient",
        return_value=mock_client,
    ):
        result = await GitHubService().probe_token(repo="o/r", mode="indexing")
    urls = [call.args[0] for call in mock_client.get.call_args_list]
    assert urls == [
        "https://api.github.com/repos/o/r",
        "https://api.github.com/repos/o/r/branches/trunk",
    ]
    assert result["resolvedRef"] == "trunk"
    assert result["remoteDefaultBranch"] == "trunk"
    assert "main" not in urls[1]


@pytest.mark.asyncio
async def test_probe_empty_repository_has_distinct_outcome(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "github-token-fixture")
    mock_client = _probe_client(
        [_mock_get_response(200, {"full_name": "o/r", "id": 9, "default_branch": None})]
    )
    with patch(
        "moonmind.workflows.adapters.github_service.httpx.AsyncClient",
        return_value=mock_client,
    ):
        result = await GitHubService().probe_token(repo="o/r", mode="publish")
    assert mock_client.get.await_count == 1
    assert result["reasonCode"] == "empty_repository"
    assert result["resolvedRef"] is None


@pytest.mark.asyncio
async def test_probe_concealed_repository_is_not_denied(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "github-token-fixture")

    async def _raise_not_found(*args, **kwargs):
        raise httpx.HTTPStatusError(
            "not found",
            request=httpx.Request("GET", "https://api.github.com/test"),
            response=_mock_http_error(404, {"message": "Not Found"}),
        )

    mock_client = _probe_client([])
    mock_client.get = AsyncMock(side_effect=_raise_not_found)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    with patch(
        "moonmind.workflows.adapters.github_service.httpx.AsyncClient",
        return_value=mock_client,
    ):
        result = await GitHubService().probe_token(repo="o/r", mode="publish")
    assert result["reasonCode"] == "not_found_or_concealed"
    assert result["repositoryAccessible"] is False


@pytest.mark.asyncio
async def test_probe_quota_does_not_mark_permission_failed_and_halts(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "github-token-fixture")
    repo_meta = _mock_get_response(
        200, {"full_name": "o/r", "id": 9, "default_branch": "main"}
    )

    async def _quota(*args, **kwargs):
        url = args[0] if args else ""
        if url == "https://api.github.com/repos/o/r":
            return repo_meta
        raise httpx.HTTPStatusError(
            "limited",
            request=httpx.Request("GET", url),
            response=_mock_http_error(
                403,
                {"message": "API rate limit exceeded"},
                {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "2000000000"},
            ),
        )

    mock_client = _probe_client([])
    mock_client.get = AsyncMock(side_effect=_quota)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    with patch(
        "moonmind.workflows.adapters.github_service.httpx.AsyncClient",
        return_value=mock_client,
    ):
        result = await GitHubService().probe_token(repo="o/r", mode="publish")
    # Only repo metadata + first dependent check run; remaining halted.
    assert mock_client.get.await_count == 2
    assert result["throttled"] is True
    assert result["retryAfterSeconds"] is not None
    checklist = {item["permission"]: item for item in result["permissionChecklist"]}
    assert checklist["Contents"]["status"] == "unavailable"
    assert checklist["Contents"]["status"] != "failed"
    assert result["diagnostics"][0]["reasonCode"] == "quota_exceeded"
    assert result["diagnostics"][0]["retryable"] is True


@pytest.mark.asyncio
async def test_probe_transport_leaves_evidence_unknown(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "github-token-fixture")
    repo_meta = _mock_get_response(
        200, {"full_name": "o/r", "id": 9, "default_branch": "main"}
    )

    async def _flaky(*args, **kwargs):
        url = args[0] if args else ""
        if url == "https://api.github.com/repos/o/r":
            return repo_meta
        raise httpx.ConnectError("dns down")

    mock_client = _probe_client([])
    mock_client.get = AsyncMock(side_effect=_flaky)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    with patch(
        "moonmind.workflows.adapters.github_service.httpx.AsyncClient",
        return_value=mock_client,
    ):
        result = await GitHubService().probe_token(repo="o/r", mode="indexing")
    assert result["defaultBranchAccessible"] is None
    checklist = {item["permission"]: item for item in result["permissionChecklist"]}
    assert checklist["Contents"]["status"] == "unavailable"
    assert result["diagnostics"][0]["reasonCode"] == "outcome_unknown"
    assert result["diagnostics"][0]["retryable"] is True


@pytest.mark.asyncio
async def test_probe_rejects_invalid_repo_slug_without_network(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "github-token-fixture")
    mock_client = _probe_client([])
    with patch(
        "moonmind.workflows.adapters.github_service.httpx.AsyncClient",
        return_value=mock_client,
    ):
        result = await GitHubService().probe_token(repo="not-a-slug", mode="publish")
    assert mock_client.get.await_count == 0
    assert result["reasonCode"] == "invalid_target"


def test_probe_helpers_validate_targets():
    assert GitHubService._repo_api_url("o/r") == "https://api.github.com/repos/o/r"
    assert (
        GitHubService._repo_api_url("o/r", "/branches/a%20b")
        == "https://api.github.com/repos/o/r/branches/a%20b"
    )
    assert (
        GitHubService._validate_discovery_next_page(
            "https://api.github.com/repos/o/r/pulls?page=2"
        )
        is not None
    )
    assert (
        GitHubService._validate_discovery_next_page("https://evil.example/x")
        is None
    )
    assert GitHubService.capability_operations_for_mode("publish") == (
        "branch.write",
        "pull_request.create",
    )
    try:
        GitHubService.capability_operations_for_mode("nope")
    except ValueError:
        pass
    else:
        raise AssertionError("unknown mode must raise")


@pytest.mark.asyncio
async def test_probe_explicit_missing_branch_is_absent_not_denied(monkeypatch):
    """R3: explicitly requested but missing branch ref -> resource_absent.

    Exercises the real probe_token wiring: repository metadata resolves,
    the dependent branch check 404s, the field records False (absent) without
    a permission denial, and the checklist stays unavailable (never failed).
    """
    monkeypatch.setenv("GITHUB_TOKEN", "github-token-fixture")
    GitHubService.clear_throttle_mark(
        GitHubService.throttle_bucket_key(token_fingerprint="github-token-fixture")
    )
    repo_meta = _mock_get_response(
        200, {"full_name": "o/r", "id": 9, "default_branch": "main"}
    )

    async def _branch_absent(*args, **kwargs):
        url = args[0] if args else ""
        if url == "https://api.github.com/repos/o/r":
            return repo_meta
        raise httpx.HTTPStatusError(
            "absent",
            request=httpx.Request("GET", url),
            response=_mock_http_error(404, {"message": "Branch not found"}),
        )

    mock_client = _probe_client([])
    mock_client.get = AsyncMock(side_effect=_branch_absent)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    with patch(
        "moonmind.workflows.adapters.github_service.httpx.AsyncClient",
        return_value=mock_client,
    ):
        result = await GitHubService().probe_token(
            repo="o/r", mode="indexing", base_branch="missing-branch"
        )
    assert result["requestedRef"] == "missing-branch"
    assert result["resolvedRef"] == "missing-branch"
    assert result["defaultBranchAccessible"] is False
    assert result["repositoryAccessible"] is True
    branch_entries = [
        item
        for item in result["diagnostics"]
        if isinstance(item, dict) and item.get("operation") == "branch"
    ]
    assert branch_entries and branch_entries[0]["reasonCode"] == "resource_absent"
    checklist = {item["permission"]: item for item in result["permissionChecklist"]}
    assert checklist["Contents"]["status"] == "unavailable"
    assert checklist["Contents"]["status"] != "failed"
    capability = {
        item["operation"]: item for item in result.get("capabilityEvidence", [])
    }
    assert capability, "probe must attach per-capability observed evidence"
    assert result.get("routeKey"), "probe must attach a shared-compiler route key"


@pytest.mark.asyncio
async def test_probe_selected_connection_without_token_fails_closed(monkeypatch):
    """R1: selected connection without its admitted token never uses ambient."""
    monkeypatch.setenv("GITHUB_TOKEN", "github-token-fixture")
    mock_client = _probe_client([])
    with patch(
        "moonmind.workflows.adapters.github_service.httpx.AsyncClient",
        return_value=mock_client,
    ):
        result = await GitHubService().probe_token(
            repo="o/r",
            mode="publish",
            github_token=None,
            connection_ref="repository-connection:team-a",
            credential_revision=2,
        )
    assert mock_client.get.await_count == 0
    assert result["reasonCode"] == "unadmitted_connection"
    assert result["admissionMode"] == "unadmitted-connection"
    assert result["connectionRef"] == "repository-connection:team-a"


@pytest.mark.asyncio
async def test_probe_many_connections_bounds_fanout(monkeypatch):
    """R1: explicitly requested multi-connection probes stay bounded per connection."""
    monkeypatch.setenv("GITHUB_TOKEN", "github-token-fixture")
    GitHubService.clear_throttle_mark(
        GitHubService.throttle_bucket_key(token_fingerprint="tok-a")
    )
    GitHubService.clear_throttle_mark(
        GitHubService.throttle_bucket_key(token_fingerprint="tok-b")
    )
    over = await GitHubService().probe_many_connections(
        [{"repo": "o/r", "mode": "publish", "github_token": "t"} for _ in range(6)],
        max_connections=5,
    )
    assert over["reasonCode"] == "too_many_connections"
    assert over["results"] == []

    async def _ok(*args, **kwargs):
        url = args[0] if args else ""
        if url == "https://api.github.com/repos/o/r":
            return _mock_get_response(
                200, {"full_name": "o/r", "id": 9, "default_branch": "main"}
            )
        return _mock_get_response(200, {"name": "main"})

    mock_client = _probe_client([])
    mock_client.get = AsyncMock(side_effect=_ok)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    with patch(
        "moonmind.workflows.adapters.github_service.httpx.AsyncClient",
        return_value=mock_client,
    ):
        bounded = await GitHubService().probe_many_connections(
            [
                {
                    "repo": "o/r",
                    "mode": "indexing",
                    "github_token": "tok-a",
                    "connection_ref": "conn-a",
                    "credential_revision": 1,
                },
                {
                    "repo": "o/r",
                    "mode": "indexing",
                    "github_token": "tok-b",
                    "connection_ref": "conn-b",
                    "credential_revision": 1,
                },
            ]
        )
    assert bounded["complete"] is True
    assert bounded["connectionCount"] == 2
    assert bounded["results"][0]["connectionRef"] == "conn-a"
    assert bounded["results"][1]["connectionRef"] == "conn-b"
    for entry in bounded["results"]:
        assert "attach" not in str(entry.get("result", {}).get("reasonCode", "")).lower()


def test_capability_bundle_uses_shared_compiler():
    """R2: probe bundles derive from one canonical table via shared route_key_for."""
    for mode in ("indexing", "publish", "readiness", "full_pr_automation"):
        bundle = GitHubService.capability_bundle_for_mode(mode)
        assert bundle["bundleId"] == f"github:{mode}:github-capabilities.v1"
        assert tuple(bundle["operations"]) == GitHubService.capability_operations_for_mode(mode)
    key_publish = GitHubService.probe_route_key_for(
        repo="o/r", repository_id=9, mode="publish"
    )
    key_indexing = GitHubService.probe_route_key_for(
        repo="o/r", repository_id=9, mode="indexing"
    )
    assert key_publish != key_indexing
    assert GitHubService.probe_route_key_for(
        repo="o/r", repository_id=9, mode="publish"
    ) == key_publish
    try:
        GitHubService.capability_bundle_for_mode("nope")
    except ValueError:
        pass
    else:
        raise AssertionError("unknown mode must raise before network")


def test_probe_evidence_fencing_and_stale_preservation():
    """R4: late old-generation probes never overwrite rotated evidence."""
    prior = {
        "credentialRevision": 2,
        "bindingRevision": 1,
        "policyRevision": 1,
        "observedAt": "2026-09-14T20:00:00+00:00",
        "capabilityEvidence": [
            {"operation": "branch.write", "state": "verified"},
            {"operation": "pull_request.create", "state": "denied"},
        ],
    }
    late = {
        "credentialRevision": 1,
        "bindingRevision": 1,
        "policyRevision": 1,
        "observedAt": "2026-09-14T21:00:00+00:00",
        "capabilityEvidence": [],
    }
    assert GitHubService.fence_probe_write(prior, late) is False
    current = {
        "credentialRevision": 2,
        "bindingRevision": 1,
        "policyRevision": 1,
        "observedAt": "2026-09-14T21:00:00+00:00",
        "capabilityEvidence": [],
    }
    assert GitHubService.fence_probe_write(prior, current) is True
    assert GitHubService.fence_probe_write(None, current) is True
    kept = GitHubService.preserve_prior_on_refresh_failure(
        prior, observed_at="2026-09-14T22:00:00+00:00"
    )
    assert kept is not None
    states = {item["operation"]: item["state"] for item in kept["capabilityEvidence"]}
    assert states == {"branch.write": "stale", "pull_request.create": "stale"}
    assert GitHubService.preserve_prior_on_refresh_failure(None) is None
    evidence = GitHubService.build_capability_evidence(
        operations=["branch.write"],
        checklist=[{"permission": "Contents", "required": True, "status": "failed"}],
        observed_at="t",
        expires_at="e",
    )
    assert evidence[0]["state"] == "denied"
    assert evidence[0]["definitionVersion"] == "github-capabilities.v1"


def test_throttle_buckets_preserve_identity():
    """R6: shared actors share a budget; installations/tenants never merge."""
    shared_a = GitHubService.throttle_bucket_key(
        actor="octocat", resource="rest", token_fingerprint="tok-a"
    )
    shared_b = GitHubService.throttle_bucket_key(
        actor="octocat", resource="rest", token_fingerprint="tok-b"
    )
    assert shared_a == shared_b
    assert GitHubService.throttle_bucket_key(
        actor="octocat", installation_id="123", resource="rest"
    ) != GitHubService.throttle_bucket_key(
        actor="octocat", installation_id="456", resource="rest"
    )
    unknown_a = GitHubService.throttle_bucket_key(token_fingerprint="tok-a")
    unknown_b = GitHubService.throttle_bucket_key(token_fingerprint="tok-b")
    assert unknown_a != unknown_b
    assert "tok-a" not in unknown_a and "tok-b" not in unknown_b


@pytest.mark.asyncio
async def test_probe_throttle_gate_skips_network_when_bucket_throttled(monkeypatch):
    """R6: a known-throttled identity bucket short-circuits before network."""
    monkeypatch.setenv("GITHUB_TOKEN", "github-token-fixture")
    bucket = GitHubService.throttle_bucket_key(token_fingerprint="github-token-fixture")
    GitHubService.clear_throttle_mark(bucket)
    GitHubService.record_throttle_mark(bucket, reason="quota_exceeded", retry_after_seconds=120)
    mock_client = _probe_client([])
    with patch(
        "moonmind.workflows.adapters.github_service.httpx.AsyncClient",
        return_value=mock_client,
    ):
        result = await GitHubService().probe_token(repo="o/r", mode="publish")
    assert mock_client.get.await_count == 0
    assert result["reasonCode"] == "quota_exceeded"
    assert result["throttled"] is True
    assert result["workerSlotReleaseRequired"] is True
    GitHubService.clear_throttle_mark(bucket)


@pytest.mark.asyncio
async def test_discovery_rejects_hostile_next_page_and_preserves_partial(monkeypatch):
    """R7: hostile pagination never widens access; mid-list failure keeps partial."""
    monkeypatch.setenv("GITHUB_TOKEN", "github-token-fixture")
    svc = GitHubService()
    hostile = await svc.discover_repositories(
        start_url="https://evil.example/x",
        connection_ref="conn-a",
        principal="user:1",
        github_token="tok",
    )
    assert hostile["reasonCode"] == "invalid_start_target"
    assert hostile["items"] == []

    first = _mock_get_response_with_headers(
        200,
        [{"full_name": "o/a"}, {"full_name": "o/b"}],
        {"link": '<https://evil.example/next>; rel="next"'},
    )

    async def _one_page_then_hostile(*args, **kwargs):
        return first

    mock_client = _probe_client([])
    mock_client.get = AsyncMock(side_effect=_one_page_then_hostile)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    with patch(
        "moonmind.workflows.adapters.github_service.httpx.AsyncClient",
        return_value=mock_client,
    ):
        partial = await svc.discover_repositories(
            start_url="https://api.github.com/user/repos?per_page=2",
            connection_ref="conn-a",
            principal="user:1",
            query="q",
            github_token="tok",
            credential_revision=1,
            policy_revision=1,
        )
    assert [item["full_name"] for item in partial["items"]] == ["o/a", "o/b"]
    assert partial["complete"] is False
    assert partial["reasonCode"] == "invalid_continuation_target"
    assert partial["continuation"] is not None
    assert partial["continuation"]["connectionRef"] == "conn-a"

    wrong_owner = dict(partial["continuation"])
    resumed = GitHubService.validate_discovery_continuation(
        wrong_owner,
        connection_ref="conn-b",
        principal="user:1",
        query="q",
        credential_revision=1,
        policy_revision=1,
    )
    assert resumed is None
    stale_revision = GitHubService.validate_discovery_continuation(
        dict(partial["continuation"]),
        connection_ref="conn-a",
        principal="user:1",
        query="q",
        credential_revision=2,
        policy_revision=1,
    )
    assert stale_revision is None

# ---------------------------------------------------------------------------
# merge_pull_request
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_merge_pr_success(monkeypatch):
    """Successful merge returns merged=True and SHA."""
    monkeypatch.setenv("GITHUB_TOKEN", "github-token-fixture")

    mock_client = AsyncMock()
    mock_client.put = AsyncMock(
        return_value=_mock_response(200, {"merged": True, "sha": "abc123"})
    )
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("moonmind.workflows.adapters.github_service.httpx.AsyncClient", return_value=mock_client):
        svc = GitHubService()
        result = await svc.merge_pull_request(
            pr_url="https://github.com/owner/repo/pull/99",
            expected_head_sha="expected123",
        )

    assert isinstance(result, MergePRResult)
    assert result.merged is True
    assert result.merge_sha == "abc123"
    assert mock_client.put.await_args.kwargs["json"] == {
        "merge_method": "merge",
        "sha": "expected123",
    }

@pytest.mark.asyncio
async def test_merge_pr_invalid_url():
    """Non-GitHub URL should return merged=False."""
    svc = GitHubService()
    result = await svc.merge_pull_request(pr_url="https://not-github.com/foo")

    assert result.merged is False
    assert "Could not parse" in result.summary

@pytest.mark.asyncio
async def test_merge_pr_missing_token(monkeypatch):
    """Missing GITHUB_TOKEN should return merged=False."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    svc = GitHubService()
    result = await svc.merge_pull_request(
        pr_url="https://github.com/owner/repo/pull/99",
    )

    assert result.merged is False
    assert "GitHub auth is not configured" in result.summary

# ---------------------------------------------------------------------------
# parse_github_pr_url
# ---------------------------------------------------------------------------

def test_parse_valid_url():
    assert GitHubService.parse_github_pr_url(
        "https://github.com/owner/repo/pull/42"
    ) == ("owner", "repo", "42")

def test_parse_invalid_url():
    assert GitHubService.parse_github_pr_url("https://example.com/foo") is None

# ---------------------------------------------------------------------------
# evaluate_pull_request_readiness
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_evaluate_pull_request_readiness_waits_for_running_checks(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "github-token-fixture")

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(
        side_effect=[
            _mock_get_response(200, {"state": "open", "head": {"sha": "abc123"}}),
            _mock_get_response(200, {"state": "pending"}),
            _mock_get_response(
                200,
                {
                    "check_runs": [
                        {"status": "in_progress", "conclusion": None},
                    ]
                },
            ),
        ]
    )
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch(
        "moonmind.workflows.adapters.github_service.httpx.AsyncClient",
        return_value=mock_client,
    ):
        result = await GitHubService().evaluate_pull_request_readiness(
            repo="owner/repo",
            pr_number=341,
            head_sha="abc123",
            policy={"checks": "required", "automatedReview": "disabled"},
        )

    assert isinstance(result, PullRequestReadinessResult)
    assert result.ready is False
    assert result.checks_complete is False
    assert result.blockers[0]["kind"] == "checks_running"


@pytest.mark.asyncio
async def test_evaluate_pull_request_readiness_preserves_failed_and_running_checks(
    monkeypatch,
):
    monkeypatch.setenv("GITHUB_TOKEN", "github-token-fixture")

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(
        side_effect=[
            _mock_get_response(200, {"state": "open", "head": {"sha": "abc123"}}),
            _mock_get_response(200, {"state": "pending"}),
            _mock_get_response(
                200,
                {
                    "check_runs": [
                        {"status": "in_progress", "conclusion": None},
                        {"status": "completed", "conclusion": "failure"},
                    ]
                },
            ),
        ]
    )
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch(
        "moonmind.workflows.adapters.github_service.httpx.AsyncClient",
        return_value=mock_client,
    ):
        result = await GitHubService().evaluate_pull_request_readiness(
            repo="owner/repo",
            pr_number=341,
            head_sha="abc123",
            policy={"checks": "required", "automatedReview": "disabled"},
        )

    assert result.checks_complete is False
    assert result.checks_passing is False
    assert [blocker["kind"] for blocker in result.blockers] == [
        "checks_running",
        "checks_failed",
    ]

@pytest.mark.asyncio
async def test_evaluate_pull_request_readiness_reports_checks_permission_missing(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "github-token-fixture")

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(
        side_effect=[
            _mock_get_response(200, {"state": "open", "head": {"sha": "abc123"}}),
            _mock_get_response(200, {"state": "success", "statuses": []}),
            _mock_get_response_with_headers(
                403,
                {"message": "Resource not accessible by personal access token"},
                {"X-Accepted-GitHub-Permissions": "checks=read"},
            ),
        ]
    )
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch(
        "moonmind.workflows.adapters.github_service.httpx.AsyncClient",
        return_value=mock_client,
    ):
        result = await GitHubService().evaluate_pull_request_readiness(
            repo="owner/repo",
            pr_number=341,
            head_sha="abc123",
            policy={"checks": "required", "automatedReview": "disabled"},
        )

    assert result.checks_complete is None
    assert result.blockers[0]["kind"] == "readiness_evidence_unavailable"
    assert result.blockers[0]["missingPermission"] == "Checks: read"

@pytest.mark.asyncio
async def test_evaluate_pull_request_readiness_opens_after_checks_and_review(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "github-token-fixture")

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(
        side_effect=[
            _mock_get_response(200, {"state": "open", "head": {"sha": "abc123"}}),
            _mock_get_response(200, {"state": "success"}),
            _mock_get_response(
                200,
                {
                    "check_runs": [
                        {"status": "completed", "conclusion": "success"},
                    ]
                },
            ),
            _mock_get_response(200, [{"state": "APPROVED"}]),
        ]
    )
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch(
        "moonmind.workflows.adapters.github_service.httpx.AsyncClient",
        return_value=mock_client,
    ):
        result = await GitHubService().evaluate_pull_request_readiness(
            repo="owner/repo",
            pr_number=341,
            head_sha="abc123",
            policy={"checks": "required", "automatedReview": "required"},
        )

    assert result.ready is True
    assert result.blockers == []
    assert result.checks_passing is True
    assert result.automated_review_complete is True

@pytest.mark.asyncio
async def test_evaluate_pull_request_readiness_ignores_empty_combined_status_pending_when_checks_pass(
    monkeypatch,
):
    monkeypatch.setenv("GITHUB_TOKEN", "github-token-fixture")

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(
        side_effect=[
            _mock_get_response(200, {"state": "open", "head": {"sha": "abc123"}}),
            _mock_get_response(200, {"state": "pending", "statuses": []}),
            _mock_get_response(
                200,
                {
                    "check_runs": [
                        {"status": "completed", "conclusion": "success"},
                    ]
                },
            ),
        ]
    )
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch(
        "moonmind.workflows.adapters.github_service.httpx.AsyncClient",
        return_value=mock_client,
    ):
        result = await GitHubService().evaluate_pull_request_readiness(
            repo="owner/repo",
            pr_number=341,
            head_sha="abc123",
            policy={"checks": "required", "automatedReview": "disabled"},
        )

    assert result.ready is True
    assert result.checks_complete is True
    assert result.checks_passing is True
    assert result.blockers == []

@pytest.mark.asyncio
async def test_evaluate_pull_request_readiness_opens_for_merge_conflicts_before_checks(
    monkeypatch,
):
    monkeypatch.setenv("GITHUB_TOKEN", "github-token-fixture")

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(
        return_value=_mock_get_response(
            200,
            {
                "state": "open",
                "merged": False,
                "mergeable": False,
                "mergeable_state": "dirty",
                "head": {"sha": "abc123"},
            },
        )
    )
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch(
        "moonmind.workflows.adapters.github_service.httpx.AsyncClient",
        return_value=mock_client,
    ):
        result = await GitHubService().evaluate_pull_request_readiness(
            repo="owner/repo",
            pr_number=341,
            head_sha="abc123",
            policy={"checks": "required", "automatedReview": "required"},
        )

    assert result.ready is True
    assert result.blockers == [
        {
            "kind": "merge_conflict",
            "summary": "Pull request has merge conflicts.",
            "retryable": False,
            "source": "github",
        }
    ]
    assert result.checks_complete is None
    assert result.automated_review_complete is None
    mock_client.get.assert_called_once()

@pytest.mark.asyncio
async def test_evaluate_pull_request_readiness_detects_boolean_mergeable_conflict(
    monkeypatch,
):
    monkeypatch.setenv("GITHUB_TOKEN", "github-token-fixture")

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(
        return_value=_mock_get_response(
            200,
            {
                "state": "open",
                "merged": False,
                "mergeable": False,
                "mergeable_state": "clean",
                "head": {"sha": "abc123"},
            },
        )
    )
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch(
        "moonmind.workflows.adapters.github_service.httpx.AsyncClient",
        return_value=mock_client,
    ):
        result = await GitHubService().evaluate_pull_request_readiness(
            repo="owner/repo",
            pr_number=341,
            head_sha="abc123",
            policy={"checks": "required", "automatedReview": "required"},
        )

    assert result.ready is True
    assert result.blockers[0]["kind"] == "merge_conflict"
    assert result.checks_complete is None
    assert result.automated_review_complete is None
    mock_client.get.assert_called_once()

@pytest.mark.asyncio
async def test_evaluate_pull_request_readiness_respects_failed_combined_status_without_check_runs(
    monkeypatch,
):
    monkeypatch.setenv("GITHUB_TOKEN", "github-token-fixture")

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(
        side_effect=[
            _mock_get_response(200, {"state": "open", "head": {"sha": "abc123"}}),
            _mock_get_response(200, {"state": "failure", "statuses": []}),
            _mock_get_response(200, {"check_runs": []}),
        ]
    )
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch(
        "moonmind.workflows.adapters.github_service.httpx.AsyncClient",
        return_value=mock_client,
    ):
        result = await GitHubService().evaluate_pull_request_readiness(
            repo="owner/repo",
            pr_number=341,
            head_sha="abc123",
            policy={"checks": "required", "automatedReview": "disabled"},
        )

    assert result.ready is False
    assert result.checks_complete is True
    assert result.checks_passing is False
    assert [blocker["kind"] for blocker in result.blockers] == ["checks_failed"]

@pytest.mark.asyncio
async def test_evaluate_pull_request_readiness_treats_commented_automated_review_as_complete(
    monkeypatch,
):
    monkeypatch.setenv("GITHUB_TOKEN", "github-token-fixture")

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(
        side_effect=[
            _mock_get_response(200, {"state": "open", "head": {"sha": "abc123"}}),
            _mock_get_response(200, {"state": "success"}),
            _mock_get_response(
                200,
                {
                    "check_runs": [
                        {"status": "completed", "conclusion": "success"},
                    ]
                },
            ),
            _mock_get_response(
                200,
                [
                    {
                        "state": "COMMENTED",
                        "submitted_at": "2026-04-19T20:18:26Z",
                        "user": {"login": "chatgpt-codex-connector"},
                    },
                ],
            ),
        ]
    )
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch(
        "moonmind.workflows.adapters.github_service.httpx.AsyncClient",
        return_value=mock_client,
    ):
        result = await GitHubService().evaluate_pull_request_readiness(
            repo="owner/repo",
            pr_number=341,
            head_sha="abc123",
            policy={"checks": "required", "automatedReview": "required"},
        )

    assert result.ready is True
    assert result.automated_review_complete is True
    assert result.blockers == []

@pytest.mark.asyncio
async def test_evaluate_pull_request_readiness_treats_codex_thumbs_up_reaction_as_complete(
    monkeypatch,
):
    monkeypatch.setenv("GITHUB_TOKEN", "github-token-fixture")

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(
        side_effect=[
            _mock_get_response(200, {"state": "open", "head": {"sha": "abc123"}}),
            _mock_get_response(200, {"state": "success"}),
            _mock_get_response(
                200,
                {
                    "check_runs": [
                        {"status": "completed", "conclusion": "success"},
                    ]
                },
            ),
            _mock_get_response(200, []),
            _mock_get_response(
                200,
                [
                    {
                        "content": "+1",
                        "created_at": "2026-04-23T00:33:48Z",
                        "user": {
                            "login": "chatgpt-codex-connector[bot]",
                            "type": "User",
                        },
                    },
                ],
            ),
        ]
    )
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch(
        "moonmind.workflows.adapters.github_service.httpx.AsyncClient",
        return_value=mock_client,
    ):
        result = await GitHubService().evaluate_pull_request_readiness(
            repo="owner/repo",
            pr_number=341,
            head_sha="abc123",
            policy={"checks": "required", "automatedReview": "required"},
        )

    assert result.ready is True
    assert result.automated_review_complete is True
    assert result.blockers == []

@pytest.mark.asyncio
async def test_evaluate_pull_request_readiness_ignores_non_codex_thumbs_up_reaction(
    monkeypatch,
):
    monkeypatch.setenv("GITHUB_TOKEN", "github-token-fixture")

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(
        side_effect=[
            _mock_get_response(200, {"state": "open", "head": {"sha": "abc123"}}),
            _mock_get_response(200, {"state": "success"}),
            _mock_get_response(
                200,
                {
                    "check_runs": [
                        {"status": "completed", "conclusion": "success"},
                    ]
                },
            ),
            _mock_get_response(200, []),
            _mock_get_response(
                200,
                [
                    {
                        "content": "+1",
                        "created_at": "2026-04-23T00:33:48Z",
                        "user": {
                            "login": "gemini-code-assist[bot]",
                            "type": "Bot",
                        },
                    },
                ],
            ),
        ]
    )
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch(
        "moonmind.workflows.adapters.github_service.httpx.AsyncClient",
        return_value=mock_client,
    ):
        result = await GitHubService().evaluate_pull_request_readiness(
            repo="owner/repo",
            pr_number=341,
            head_sha="abc123",
            policy={"checks": "required", "automatedReview": "required"},
        )

    assert result.ready is False
    assert result.automated_review_complete is False
    assert result.blockers[0]["kind"] == "automated_review_pending"

@pytest.mark.asyncio
async def test_evaluate_pull_request_readiness_checks_paginated_codex_reactions(
    monkeypatch,
):
    monkeypatch.setenv("GITHUB_TOKEN", "github-token-fixture")

    first_reaction_page = _mock_get_response(
        200,
        [
            {
                "content": "+1",
                "created_at": "2026-04-23T00:33:48Z",
                "user": {"login": "reviewer-a", "type": "User"},
            },
        ],
    )
    first_reaction_page.headers["link"] = (
        '<https://api.github.com/repos/owner/repo/issues/341/reactions'
        '?page=2&per_page=100>; rel="next"'
    )

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(
        side_effect=[
            _mock_get_response(200, {"state": "open", "head": {"sha": "abc123"}}),
            _mock_get_response(200, {"state": "success"}),
            _mock_get_response(
                200,
                {
                    "check_runs": [
                        {"status": "completed", "conclusion": "success"},
                    ]
                },
            ),
            _mock_get_response(200, []),
            first_reaction_page,
            _mock_get_response(
                200,
                [
                    {
                        "content": "+1",
                        "created_at": "2026-04-23T00:33:49Z",
                        "user": {
                            "login": "chatgpt-codex-connector[bot]",
                            "type": "User",
                        },
                    },
                ],
            ),
        ]
    )
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch(
        "moonmind.workflows.adapters.github_service.httpx.AsyncClient",
        return_value=mock_client,
    ):
        result = await GitHubService().evaluate_pull_request_readiness(
            repo="owner/repo",
            pr_number=341,
            head_sha="abc123",
            policy={"checks": "required", "automatedReview": "required"},
        )

    assert result.ready is True
    assert result.automated_review_complete is True
    assert result.blockers == []

@pytest.mark.asyncio
async def test_evaluate_pull_request_readiness_waits_for_human_commented_review(
    monkeypatch,
):
    monkeypatch.setenv("GITHUB_TOKEN", "github-token-fixture")

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(
        side_effect=[
            _mock_get_response(200, {"state": "open", "head": {"sha": "abc123"}}),
            _mock_get_response(200, {"state": "success"}),
            _mock_get_response(
                200,
                {
                    "check_runs": [
                        {"status": "completed", "conclusion": "success"},
                    ]
                },
            ),
            _mock_get_response(
                200,
                [
                    {
                        "state": "COMMENTED",
                        "submitted_at": "2026-04-19T20:18:26Z",
                        "user": {"login": "reviewer-a", "type": "User"},
                    },
                ],
            ),
            _mock_get_response(200, []),
        ]
    )
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch(
        "moonmind.workflows.adapters.github_service.httpx.AsyncClient",
        return_value=mock_client,
    ):
        result = await GitHubService().evaluate_pull_request_readiness(
            repo="owner/repo",
            pr_number=341,
            head_sha="abc123",
            policy={"checks": "required", "automatedReview": "required"},
        )

    assert result.ready is False
    assert result.automated_review_complete is False
    assert result.blockers[0]["kind"] == "automated_review_pending"

@pytest.mark.asyncio
async def test_evaluate_pull_request_readiness_reports_reaction_permission_missing(
    monkeypatch,
):
    monkeypatch.setenv("GITHUB_TOKEN", "github-token-fixture")

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(
        side_effect=[
            _mock_get_response(200, {"state": "open", "head": {"sha": "abc123"}}),
            _mock_get_response(200, {"state": "success", "statuses": []}),
            _mock_get_response(200, {"check_runs": []}),
            _mock_get_response(200, []),
            _mock_get_response_with_headers(
                403,
                {"message": "Resource not accessible by personal access token"},
                {"X-Accepted-GitHub-Permissions": "issues=read"},
            ),
        ]
    )
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch(
        "moonmind.workflows.adapters.github_service.httpx.AsyncClient",
        return_value=mock_client,
    ):
        result = await GitHubService().evaluate_pull_request_readiness(
            repo="owner/repo",
            pr_number=341,
            head_sha="abc123",
            policy={"checks": "required", "automatedReview": "required"},
        )

    assert result.automated_review_complete is None
    assert result.blockers[0]["kind"] == "readiness_evidence_unavailable"
    assert result.blockers[0]["missingPermission"] == "Issues: read"

@pytest.mark.asyncio
async def test_evaluate_pull_request_readiness_reports_merged_closed_pr(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "github-token-fixture")

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(
        return_value=_mock_get_response(
            200,
            {"state": "closed", "merged": True, "head": {"sha": "def456"}},
        )
    )
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch(
        "moonmind.workflows.adapters.github_service.httpx.AsyncClient",
        return_value=mock_client,
    ):
        result = await GitHubService().evaluate_pull_request_readiness(
            repo="owner/repo",
            pr_number=341,
            head_sha="abc123",
            policy={"checks": "required", "automatedReview": "required"},
        )

    assert result.ready is False
    assert result.pull_request_open is False
    assert result.pull_request_merged is True
    assert result.head_sha == "def456"
    assert result.blockers == []
    mock_client.get.assert_called_once()

@pytest.mark.asyncio
async def test_evaluate_pull_request_readiness_blocks_changes_requested_review(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "github-token-fixture")

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(
        side_effect=[
            _mock_get_response(200, {"state": "open", "head": {"sha": "abc123"}}),
            _mock_get_response(200, {"state": "success"}),
            _mock_get_response(
                200,
                {
                    "check_runs": [
                        {"status": "completed", "conclusion": "success"},
                    ]
                },
            ),
            _mock_get_response(
                200,
                [
                    {"state": "APPROVED", "user": {"login": "reviewer-a"}},
                    {"state": "CHANGES_REQUESTED", "user": {"login": "reviewer-b"}},
                ],
            ),
        ]
    )
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch(
        "moonmind.workflows.adapters.github_service.httpx.AsyncClient",
        return_value=mock_client,
    ):
        result = await GitHubService().evaluate_pull_request_readiness(
            repo="owner/repo",
            pr_number=341,
            head_sha="abc123",
            policy={"checks": "required", "automatedReview": "required"},
        )

    assert result.ready is False
    assert result.automated_review_complete is False
    assert result.blockers[0]["summary"] == "Automated review has requested changes."


# ---------------------------------------------------------------------------
# close_issue (#4179 failed-attempt finalization close step)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_close_issue_success_patches_state_closed(monkeypatch):
    """close_issue PATCHes state=closed and reports closed."""
    monkeypatch.setenv("GITHUB_TOKEN", "github-token-fixture")

    mock_client = AsyncMock()
    mock_client.patch = AsyncMock(return_value=_mock_response(200, {"state": "closed"}))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("moonmind.workflows.adapters.github_service.httpx.AsyncClient", return_value=mock_client):
        svc = GitHubService()
        result = await svc.close_issue(repo="o/r", issue_number=4179)

    assert result["ok"] is True
    assert result["reasonCode"] == "closed"
    _args, kwargs = mock_client.patch.call_args
    assert kwargs["json"] == {"state": "closed"}
    assert "o/r/issues/4179" in _args[0]


@pytest.mark.asyncio
async def test_close_issue_denied_never_reports_closed(monkeypatch):
    """A 403 close reports denied, never closed."""
    monkeypatch.setenv("GITHUB_TOKEN", "github-token-fixture")

    mock_client = AsyncMock()
    denied = _mock_response(403, {"message": "forbidden"})
    mock_client.patch = AsyncMock(
        side_effect=httpx.HTTPStatusError("forbidden", request=denied.request, response=denied)
    )
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("moonmind.workflows.adapters.github_service.httpx.AsyncClient", return_value=mock_client):
        svc = GitHubService()
        result = await svc.close_issue(repo="o/r", issue_number=4179)

    assert result["ok"] is False
    assert result["reasonCode"] == "denied"


@pytest.mark.asyncio
async def test_close_issue_transport_loss_is_outcome_unknown(monkeypatch):
    """Transport loss during close is outcome_unknown, not failure proof."""
    monkeypatch.setenv("GITHUB_TOKEN", "github-token-fixture")

    mock_client = AsyncMock()
    mock_client.patch = AsyncMock(side_effect=httpx.ConnectError("lost"))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("moonmind.workflows.adapters.github_service.httpx.AsyncClient", return_value=mock_client):
        svc = GitHubService()
        result = await svc.close_issue(repo="o/r", issue_number=4179)

    assert result["ok"] is False
    assert result["reasonCode"] == "outcome_unknown"


@pytest.mark.asyncio
async def test_close_issue_missing_token_is_auth_unavailable(monkeypatch):
    """No token resolves to auth_unavailable before any GitHub write."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("MOONMIND_GITHUB_TOKEN", raising=False)

    svc = GitHubService()
    result = await svc.close_issue(repo="o/r", issue_number=4179)

    assert result["ok"] is False
    assert result["reasonCode"] == "auth_unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize('headers,message,retryable', [
    ({'x-ratelimit-remaining': '0'}, 'API rate limit exceeded', True),
    ({'retry-after': '60'}, 'Request temporarily forbidden', True),
    ({}, 'You have exceeded a secondary rate limit.', True),
    ({'x-ratelimit-remaining': '4999'}, 'Resource not accessible by personal access token', False),
])
async def test_create_pr_distinguishes_403_rate_limits_from_permissions(monkeypatch, headers, message, retryable):
    monkeypatch.setenv('GITHUB_TOKEN', 'github-token-fixture')
    calls = []
    def respond(request):
        calls.append(request.method)
        return httpx.Response(403, headers=headers, json={'message': message})
    client_class = httpx.AsyncClient
    with patch('moonmind.workflows.adapters.github_service.httpx.AsyncClient',
               side_effect=lambda **kwargs: client_class(transport=httpx.MockTransport(respond), **kwargs)):
        result = await GitHubService().create_pull_request(repo='o/r', head='feature', base='main', title='T', body='B')
    assert result.retryable is retryable
    if retryable:
        assert result.retry_after_seconds >= 60
    else:
        assert result.retry_after_seconds is None
    assert not result.created
    assert calls == ['GET']


def test_github_primary_rate_limit_preserves_reset_time():
    from datetime import datetime, timezone
    response = httpx.Response(403, headers={'x-ratelimit-remaining': '0',
                                          'x-ratelimit-reset': '1800000000'},
                              json={'message': 'API rate limit exceeded'})
    event = GitHubService._github_rate_limit_event(response)
    assert event is not None
    assert event.reset_at == datetime.fromtimestamp(1800000000, timezone.utc).isoformat()
