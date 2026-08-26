"""Tool-boundary tests for github.resolve_pull_request_target."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from moonmind.workflows.adapters.github_service import PullRequestSelectorResult
from moonmind.workflows.temporal.story_output_tools import resolve_pull_request_target

pytestmark = [pytest.mark.asyncio]

_REPO = "MoonLadderStudios/MoonMind"
_HEAD = "abc1234abc1234abc1234abc1234abc1234abc12"


class _FakeService:
    def __init__(self, *, resolution: PullRequestSelectorResult, token: str = "t") -> None:
        self._resolution = resolution
        self._token = token

    async def resolve_pull_request_selector(self, **_kwargs: Any):
        return self._resolution

    async def resolve_github_token(self, *_args: Any, **_kwargs: Any):
        if self._token:
            return self._token, None
        return "", "GitHub auth is not configured."

    @staticmethod
    def _github_headers(token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}


def _resolved() -> PullRequestSelectorResult:
    return PullRequestSelectorResult(
        resolved=True,
        prNumber=350,
        prUrl=f"https://github.com/{_REPO}/pull/350",
        selectorType="number",
        reasonCode="resolved",
        summary="Resolved PR #350.",
    )


def _client(response: httpx.Response):
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return mock_client


def _response(body: dict, status_code: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code,
        json=body,
        request=httpx.Request("GET", "https://api.github.com/test"),
    )


async def test_open_pull_request_emits_publish_context_values() -> None:
    mock_client = _client(
        _response(
            {
                "state": "open",
                "merged": False,
                "draft": False,
                "html_url": f"https://github.com/{_REPO}/pull/350",
                "head": {"sha": _HEAD, "ref": "feature"},
                "base": {"ref": "main"},
            }
        )
    )

    with patch(
        "moonmind.workflows.temporal.story_output_tools.httpx.AsyncClient",
        return_value=mock_client,
    ):
        result = await resolve_pull_request_target(
            {"repository": _REPO, "pullRequest": "350"},
            github_service_factory=lambda: _FakeService(resolution=_resolved()),
        )

    assert result.status == "COMPLETED"
    # These exact output keys are what MoonMind.UserWorkflow records as the
    # durable publish context merge automation is started from.
    assert result.outputs["pull_request_url"] == f"https://github.com/{_REPO}/pull/350"
    assert result.outputs["head_sha"] == _HEAD
    assert result.outputs["branch"] == "feature"
    assert result.outputs["push_base_ref"] == "main"


async def test_merged_pull_request_is_a_blocker() -> None:
    mock_client = _client(
        _response(
            {
                "state": "closed",
                "merged": True,
                "html_url": f"https://github.com/{_REPO}/pull/350",
                "head": {"sha": _HEAD, "ref": "feature"},
                "base": {"ref": "main"},
            }
        )
    )

    with patch(
        "moonmind.workflows.temporal.story_output_tools.httpx.AsyncClient",
        return_value=mock_client,
    ):
        result = await resolve_pull_request_target(
            {"repository": _REPO, "pullRequest": "350"},
            github_service_factory=lambda: _FakeService(resolution=_resolved()),
        )

    assert result.status == "FAILED"
    assert "not open" in result.outputs["summary"]


async def test_unresolvable_selector_fails_without_guessing() -> None:
    unresolved = PullRequestSelectorResult(
        resolved=False,
        selectorType="branch",
        reasonCode="not_found",
        summary="No open pull request for that head branch.",
    )

    result = await resolve_pull_request_target(
        {"repository": _REPO, "pullRequest": "some-branch"},
        github_service_factory=lambda: _FakeService(resolution=unresolved),
    )

    assert result.status == "FAILED"
    assert result.outputs["reasonCode"] == "not_found"


async def test_missing_inputs_fail_fast() -> None:
    result = await resolve_pull_request_target({"repository": _REPO})

    assert result.status == "FAILED"
    assert "requires a repository" in result.outputs["summary"]
