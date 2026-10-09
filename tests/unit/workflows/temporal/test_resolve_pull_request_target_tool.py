"""Tool-boundary tests for github.resolve_pull_request_target."""

from __future__ import annotations

from contextlib import asynccontextmanager
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
    def __init__(
        self,
        *,
        resolution: PullRequestSelectorResult,
        pull_request: dict[str, Any] | None = None,
    ) -> None:
        self._resolution = resolution
        self._pull_request = pull_request or {}
        self.reads: list[tuple[str, Any]] = []

    async def resolve_pull_request_selector(self, **kwargs: Any):
        self.reads.append(("selector", kwargs.get("admitted_workflow_id")))
        return self._resolution

    async def read_pull_request(self, repository, url, *, admitted_workflow_id=""):
        self.reads.append(("pull_request", admitted_workflow_id))
        assert url == f"https://github.com/{repository}/pull/350"
        return self._pull_request

    async def resolve_github_token(self, *_args: Any, **_kwargs: Any):
        pytest.fail("pull request target reads must not resolve ambient auth")

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
    service = _FakeService(
        resolution=_resolved(),
        pull_request={
            "state": "open",
            "merged": False,
            "draft": False,
            "html_url": f"https://github.com/{_REPO}/pull/350",
            "head": {"sha": _HEAD, "ref": "feature"},
            "base": {"ref": "main"},
        },
    )

    result = await resolve_pull_request_target(
        {"repository": _REPO, "pullRequest": "350"},
        {"workflow_id": "mm:run-b"},
        github_service_factory=lambda: service,
    )

    assert result.status == "COMPLETED"
    # These exact output keys are what MoonMind.UserWorkflow records as the
    # durable publish context merge automation is started from.
    assert result.outputs["pull_request_url"] == f"https://github.com/{_REPO}/pull/350"
    assert result.outputs["head_sha"] == _HEAD
    assert result.outputs["branch"] == "feature"
    assert result.outputs["push_base_ref"] == "main"
    # Both reads use the run's admitted connection, never an ambient token.
    assert service.reads == [("selector", "mm:run-b"), ("pull_request", "mm:run-b")]


async def test_merged_pull_request_is_a_blocker() -> None:
    service = _FakeService(
        resolution=_resolved(),
        pull_request={
            "state": "closed",
            "merged": True,
            "html_url": f"https://github.com/{_REPO}/pull/350",
            "head": {"sha": _HEAD, "ref": "feature"},
            "base": {"ref": "main"},
        },
    )

    result = await resolve_pull_request_target(
        {"repository": _REPO, "pullRequest": "350"},
        github_service_factory=lambda: service,
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
    result = await resolve_pull_request_target({})

    assert result.status == "FAILED"
    assert "requires a repository" in result.outputs["summary"]


async def test_empty_selector_reports_successful_skip() -> None:
    result = await resolve_pull_request_target({"repository": _REPO})

    assert result.status == "COMPLETED"
    assert result.outputs["skipped"] is True
    assert "skipped" in result.outputs["summary"].lower()


async def test_review_only_target_uses_context_authority_for_both_reads(monkeypatch):
    from moonmind.workflows.temporal import merge_automation_repository_access

    authority = {
        "executionOwner": "parent",
        "parentExecutionPlan": {"planRef": "frozen"},
    }
    uses = []

    @asynccontextmanager
    async def selected_token(value, *, repository, operation):
        uses.append((value, repository, operation))
        yield "selected-token"

    class BoundService(_FakeService):
        async def resolve_pull_request_selector(self, **kwargs):
            assert kwargs["github_token"] == "selected-token"
            return self._resolution

        async def resolve_github_token(self, *_args, **_kwargs):
            pytest.fail("admitted target reads must not resolve ambient auth")

    monkeypatch.setattr(
        merge_automation_repository_access,
        "merge_automation_repository_token",
        selected_token,
    )
    client = _client(
        _response(
            {
                "state": "open",
                "merged": False,
                "head": {"sha": _HEAD, "ref": "feature"},
                "base": {"ref": "main"},
            }
        )
    )
    with patch(
        "moonmind.workflows.temporal.story_output_tools.httpx.AsyncClient",
        return_value=client,
    ):
        result = await resolve_pull_request_target(
            {
                "repository": _REPO,
                "pullRequest": "feature",
                "repositoryAuthority": {"forged": True},
            },
            {"repositoryAuthority": authority},
            github_service_factory=lambda: BoundService(resolution=_resolved()),
        )
    assert result.status == "COMPLETED"
    assert uses == [(authority, _REPO, "read")]
    assert (
        client.get.call_args.kwargs["headers"]["Authorization"]
        == "Bearer selected-token"
    )


async def test_invalid_review_authority_never_falls_back_to_ambient(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ambient-token")
    client = _client(_response({}, status_code=403))
    with patch(
        "moonmind.workflows.temporal.story_output_tools.httpx.AsyncClient",
        return_value=client,
    ), pytest.raises(ValueError, match="authority is incomplete"):
        await resolve_pull_request_target(
            {"repository": _REPO, "pullRequest": "350"},
            {"repositoryAuthority": {}},
        )
    client.get.assert_not_awaited()


async def _run_target_in_owning_run(monkeypatch, tmp_path, *, secret, assigned):
    """Resolve a branch selector inside the recorded run's Activity (#4010).

    Uses the production connection records, selector, and repository reader;
    only the provider is replaced by a transport that records each request.
    """

    import dataclasses

    from temporalio.testing import ActivityEnvironment

    from tests.helpers.repository_connections import (
        github_pat_connection,
        github_repository_assignment,
        record_repository_connections,
    )

    owner = "mm:owner-run"
    pr = {
        "number": 350,
        "state": "open",
        "merged": False,
        "draft": False,
        "html_url": f"https://github.com/{_REPO}/pull/350",
        "head": {"sha": _HEAD, "ref": "feature", "repo": {"full_name": _REPO}},
        "base": {"ref": "main", "repo": {"full_name": _REPO}},
    }
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == f"/repos/{_REPO}/pulls":
            return httpx.Response(200, json=[pr])
        if request.url.path == f"/repos/{_REPO}/pulls/350":
            return httpx.Response(200, json=pr)
        raise AssertionError(f"Unexpected provider request: {request.url}")

    original_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original_client(
            transport=httpx.MockTransport(handle), **kwargs
        ),
    )
    monkeypatch.setenv("GITHUB_TOKEN", "ambient-token-a")
    monkeypatch.setenv("GH_TOKEN", "ambient-token-a")
    if secret:
        monkeypatch.setenv("TEAM_B_PAT", secret)
    else:
        monkeypatch.delenv("TEAM_B_PAT", raising=False)
    engine = await record_repository_connections(
        monkeypatch,
        tmp_path,
        github_pat_connection("repository-connection:team-b", "TEAM_B_PAT"),
        assignments=(
            [github_repository_assignment("repository-connection:team-b", _REPO)]
            if assigned
            else []
        ),
        admitted_runs={
            owner: {
                "repository": {
                    "provider": "git",
                    "connectionRef": "repository-connection:team-b",
                    "repository": {"name": _REPO},
                }
            }
        },
    )
    env = ActivityEnvironment()
    env.info = dataclasses.replace(env.info, workflow_id=owner)
    try:
        result = await env.run(
            resolve_pull_request_target,
            {"repository": _REPO, "pullRequest": "feature"},
            {"workflow_id": owner},
        )
    finally:
        await engine.dispose()
    return result, requests


async def test_ordinary_run_resolves_target_with_selected_pat_over_ambient(
    monkeypatch, tmp_path
):
    result, requests = await _run_target_in_owning_run(
        monkeypatch, tmp_path, secret="selected-token-b", assigned=True
    )

    assert result.status == "COMPLETED", result.outputs
    assert result.outputs["headSha"] == _HEAD
    assert [r.url.path for r in requests] == [
        f"/repos/{_REPO}/pulls",
        f"/repos/{_REPO}/pulls/350",
    ]
    assert {r.headers["Authorization"] for r in requests} == {
        "Bearer selected-token-b"
    }


@pytest.mark.parametrize(
    ("secret", "assigned"),
    [("", True), ("selected-token-b", False)],
    ids=["missing-credential", "revoked-assignment"],
)
async def test_ordinary_run_without_selected_pat_sends_nothing(
    monkeypatch, tmp_path, secret, assigned
):
    result, requests = await _run_target_in_owning_run(
        monkeypatch, tmp_path, secret=secret, assigned=assigned
    )

    assert result.status == "FAILED"
    assert result.outputs["reasonCode"] == "auth_unavailable"
    assert "no other GitHub credential is used" in result.outputs["summary"]
    assert "ambient-token-a" not in str(result.outputs)
    assert requests == []
