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


# ---------------------------------------------------------------------------
# create_pull_request
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_pr_success(monkeypatch):
    """Successful PR creation returns created=True and the URL."""
    monkeypatch.setenv("GITHUB_TOKEN", "github-token-fixture")

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(
        return_value=_mock_response(201, {"html_url": "https://github.com/o/r/pull/42"})
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
