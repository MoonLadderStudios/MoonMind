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
            _mock_get_response(200, {"full_name": "owner/repo"}),
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
    assert checklist["Pull requests"]["level"] == "write"
    assert any("resource owner" in item for item in result["limitations"])
    assert any("GitHub App" in item for item in result["limitations"])

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
        )

    assert isinstance(result, MergePRResult)
    assert result.merged is True
    assert result.merge_sha == "abc123"

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

    assert result.ready is True
    assert result.checks_complete is True
    assert result.checks_passing is False
    assert result.blockers == []

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
