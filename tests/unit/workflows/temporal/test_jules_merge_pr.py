"""Unit tests for the Jules merge-PR client method and activity."""

from __future__ import annotations

import pytest

from moonmind.workflows.adapters.github_service import MergePRResult
from moonmind.workflows.adapters.jules_client import JulesClient

pytestmark = [pytest.mark.asyncio]

# ---------------------------------------------------------------------------
# JulesClient.merge_pull_request
# ---------------------------------------------------------------------------

async def test_merge_pull_request_rejects_invalid_url():
    """Non-GitHub PR URLs should return merged=False without calling the API."""
    client = JulesClient(base_url="https://unused", api_key="unused")
    result = await client.merge_pull_request(pr_url="https://not-github.com/foo")
    assert isinstance(result, MergePRResult)
    assert result.merged is False
    assert "Could not parse" in result.summary

async def test_merge_pull_request_requires_github_token(monkeypatch):
    """Without GITHUB_TOKEN, the method should report an error instead of crashing."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    client = JulesClient(base_url="https://unused", api_key="unused")
    result = await client.merge_pull_request(
        pr_url="https://github.com/owner/repo/pull/42",
    )
    assert result.merged is False
    assert "GITHUB_TOKEN" in result.summary

async def test_merge_pull_request_success(monkeypatch):
    """When the GitHub API returns 200, we should see merged=True with the SHA."""
    import httpx

    async def fake_put(self, url, *, headers=None, json=None, **kwargs):
        return httpx.Response(
            status_code=200,
            json={"merged": True, "sha": "abc123def456"},
            request=httpx.Request("PUT", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "put", fake_put)

    client = JulesClient(base_url="https://unused", api_key="unused")
    result = await client.merge_pull_request(
        pr_url="https://github.com/owner/repo/pull/42",
        github_token="test-token",
    )
    assert result.merged is True
    assert result.merge_sha == "abc123def456"
    assert "owner/repo#42" in result.summary

async def test_merge_pull_request_http_error(monkeypatch):
    """When the GitHub API returns an error status, merged=False with details."""
    import httpx

    async def fake_put(self, url, *, headers=None, json=None, **kwargs):
        response = httpx.Response(
            status_code=405,
            text="Pull Request is not mergeable",
            request=httpx.Request("PUT", url),
        )
        response.raise_for_status()

    monkeypatch.setattr(httpx.AsyncClient, "put", fake_put)

    client = JulesClient(base_url="https://unused", api_key="unused")
    result = await client.merge_pull_request(
        pr_url="https://github.com/owner/repo/pull/99",
        github_token="test-token",
    )
    assert result.merged is False
    assert "405" in result.summary

# ---------------------------------------------------------------------------
# JulesClient.update_pull_request_base
# ---------------------------------------------------------------------------

async def test_update_pull_request_base_rejects_invalid_url():
    """Non-GitHub PR URLs should fail without calling the API."""
    client = JulesClient(base_url="https://unused", api_key="unused")
    ok, summary = await client.update_pull_request_base(
        pr_url="https://not-github.com/foo",
        new_base="develop",
    )
    assert ok is False
    assert "Could not parse" in summary

async def test_update_pull_request_base_requires_github_token(monkeypatch):
    """Without GITHUB_TOKEN, the method should report an error."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    client = JulesClient(base_url="https://unused", api_key="unused")
    ok, summary = await client.update_pull_request_base(
        pr_url="https://github.com/owner/repo/pull/42",
        new_base="develop",
    )
    assert ok is False
    assert "GITHUB_TOKEN" in summary

async def test_update_pull_request_base_success(monkeypatch):
    """When the GitHub API returns 200, ok should be True."""
    import httpx

    async def fake_patch(self, url, *, headers=None, json=None, **kwargs):
        return httpx.Response(
            status_code=200,
            json={"base": {"ref": json["base"]}},
            request=httpx.Request("PATCH", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "patch", fake_patch)

    client = JulesClient(base_url="https://unused", api_key="unused")
    ok, summary = await client.update_pull_request_base(
        pr_url="https://github.com/owner/repo/pull/42",
        new_base="develop",
        github_token="test-token",
    )
    assert ok is True
    assert "develop" in summary

async def test_update_pull_request_base_http_error(monkeypatch):
    """When the GitHub API returns an error, ok should be False."""
    import httpx

    async def fake_patch(self, url, *, headers=None, json=None, **kwargs):
        response = httpx.Response(
            status_code=422,
            text="Validation Failed",
            request=httpx.Request("PATCH", url),
        )
        response.raise_for_status()

    monkeypatch.setattr(httpx.AsyncClient, "patch", fake_patch)

    client = JulesClient(base_url="https://unused", api_key="unused")
    ok, summary = await client.update_pull_request_base(
        pr_url="https://github.com/owner/repo/pull/42",
        new_base="nonexistent-branch",
        github_token="test-token",
    )
    assert ok is False
    assert "422" in summary
