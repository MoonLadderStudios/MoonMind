"""Hermetic integration coverage for MM-680 publish reconciliation."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from moonmind.workflows.adapters.github_service import GitHubService
from moonmind.workflows.temporal.activity_runtime import classify_git_push_failure

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]


def _mock_get_response(status_code: int, json_body: list[dict]) -> httpx.Response:
    return httpx.Response(
        status_code,
        json=json_body,
        request=httpx.Request("GET", "https://api.github.com/test"),
    )


async def test_repo_create_pr_adopts_existing_head_base_without_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "github-token-fixture")
    existing_pr = {
        "html_url": "https://github.com/o/r/pull/42",
        "head": {"ref": "feature", "sha": "abc123", "repo": {"full_name": "o/r"}},
        "base": {"ref": "main"},
    }
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=_mock_get_response(200, [existing_pr]))
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
            title="MM-680",
            body="publish reconciliation",
        )

    assert result.adopted is True
    assert result.created is False
    assert result.url == "https://github.com/o/r/pull/42"
    mock_client.post.assert_not_awaited()


async def test_branch_publish_lease_miss_is_retryable_conflict() -> None:
    result = classify_git_push_failure(
        stderr="! [rejected] feature -> feature (fetch first)",
        branch="feature",
        base_branch="main",
    )

    assert result["push_status"] == "lease_conflict"
    assert result["push_base_branch"] == "main"
    assert result["retryable"] is True
    assert result["diagnostic_kind"] == "publish_lease_conflict"
