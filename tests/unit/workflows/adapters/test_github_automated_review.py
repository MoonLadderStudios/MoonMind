"""GitHub-boundary tests for the automated review request/result protocol."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from moonmind.workflows.adapters.github_service import GitHubService

_REPO = "MoonLadderStudios/MoonMind"
_HEAD = "abc1234abc1234abc1234abc1234abc1234abc12"
_OLD_HEAD = "0000000000000000000000000000000000000000"


def _get(status_code: int, body: dict | list, *, headers: dict | None = None):
    return httpx.Response(
        status_code,
        json=body,
        headers=headers or {},
        request=httpx.Request("GET", "https://api.github.com/test"),
    )


def _post(status_code: int, body: dict):
    return httpx.Response(
        status_code,
        json=body,
        request=httpx.Request("POST", "https://api.github.com/test"),
    )


def _client(*, get_responses, post_responses=None):
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=list(get_responses))
    mock_client.post = AsyncMock(side_effect=list(post_responses or []))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return mock_client


def _patch_client(mock_client):
    return patch(
        "moonmind.workflows.adapters.github_service.httpx.AsyncClient",
        return_value=mock_client,
    )


# ---------------------------------------------------------------------------
# request_automated_review
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_request_posts_exactly_the_configured_command(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "github-token-fixture")
    mock_client = _client(
        get_responses=[
            _get(200, {"state": "open", "merged": False, "head": {"sha": _HEAD}}),
            _get(200, []),
        ],
        post_responses=[
            _post(
                201,
                {
                    "id": 98765,
                    "html_url": "https://github.com/x/y/pull/1#issuecomment-98765",
                    "created_at": "2026-08-24T22:15:00Z",
                    "user": {"login": "moonmind-bot"},
                },
            )
        ],
    )

    with _patch_client(mock_client):
        result = await GitHubService().request_automated_review(
            repo=_REPO,
            pr_number=350,
            expected_head_sha=_HEAD,
            provider="codex",
            attempt_started_at="2026-08-24T22:14:00Z",
        )

    assert result.status == "requested"
    assert result.request_comment_id == 98765
    assert result.requested_at == "2026-08-24T22:15:00Z"
    assert result.actor == "moonmind-bot"
    # The command is the trusted provider command, never caller-supplied text.
    assert mock_client.post.await_args.kwargs["json"] == {"body": "@codex review"}


@pytest.mark.asyncio
async def test_request_reconciles_ambiguous_post_instead_of_posting_twice(monkeypatch):
    """A lost response is recovered by adopting the comment it created."""

    monkeypatch.setenv("GITHUB_TOKEN", "github-token-fixture")
    mock_client = _client(
        get_responses=[
            _get(200, {"state": "open", "merged": False, "head": {"sha": _HEAD}}),
            _get(
                200,
                [
                    {
                        "id": 4242,
                        "body": "@codex review",
                        "created_at": "2026-08-24T22:15:30Z",
                        "html_url": "https://github.com/x/y#issuecomment-4242",
                        "user": {"login": "moonmind-bot"},
                    }
                ],
            ),
        ]
    )

    with _patch_client(mock_client):
        result = await GitHubService().request_automated_review(
            repo=_REPO,
            pr_number=350,
            expected_head_sha=_HEAD,
            provider="codex",
            attempt_started_at="2026-08-24T22:14:00Z",
        )

    assert result.status == "reconciled"
    assert result.reconciled is True
    assert result.request_comment_id == 4242
    assert mock_client.post.await_count == 0


@pytest.mark.asyncio
async def test_request_ignores_older_request_comment(monkeypatch):
    """A request comment from before this attempt is not adopted."""

    monkeypatch.setenv("GITHUB_TOKEN", "github-token-fixture")
    mock_client = _client(
        get_responses=[
            _get(200, {"state": "open", "merged": False, "head": {"sha": _HEAD}}),
            _get(
                200,
                [
                    {
                        "id": 11,
                        "body": "@codex review",
                        "created_at": "2026-08-20T10:00:00Z",
                        "user": {"login": "moonmind-bot"},
                    }
                ],
            ),
        ],
        post_responses=[
            _post(
                201,
                {
                    "id": 99,
                    "created_at": "2026-08-24T22:15:00Z",
                    "user": {"login": "moonmind-bot"},
                },
            )
        ],
    )

    with _patch_client(mock_client):
        result = await GitHubService().request_automated_review(
            repo=_REPO,
            pr_number=350,
            expected_head_sha=_HEAD,
            provider="codex",
            attempt_started_at="2026-08-24T22:14:00Z",
        )

    assert result.status == "requested"
    assert result.request_comment_id == 99


@pytest.mark.asyncio
async def test_request_refuses_when_head_advanced(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "github-token-fixture")
    mock_client = _client(
        get_responses=[
            _get(200, {"state": "open", "merged": False, "head": {"sha": _OLD_HEAD}}),
        ]
    )

    with _patch_client(mock_client):
        result = await GitHubService().request_automated_review(
            repo=_REPO,
            pr_number=350,
            expected_head_sha=_HEAD,
            provider="codex",
            attempt_started_at="2026-08-24T22:14:00Z",
        )

    assert result.status == "stale_head"
    assert result.observed_head_sha == _OLD_HEAD
    assert mock_client.post.await_count == 0


@pytest.mark.asyncio
async def test_request_refuses_when_pull_request_is_closed(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "github-token-fixture")
    mock_client = _client(
        get_responses=[
            _get(200, {"state": "closed", "merged": True, "head": {"sha": _HEAD}}),
        ]
    )

    with _patch_client(mock_client):
        result = await GitHubService().request_automated_review(
            repo=_REPO,
            pr_number=350,
            expected_head_sha=_HEAD,
            provider="codex",
            attempt_started_at="2026-08-24T22:14:00Z",
        )

    assert result.status == "pull_request_closed"
    assert mock_client.post.await_count == 0


@pytest.mark.asyncio
async def test_request_adopts_previously_recorded_comment(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "github-token-fixture")
    mock_client = _client(
        get_responses=[
            _get(200, {"state": "open", "merged": False, "head": {"sha": _HEAD}}),
            _get(
                200,
                {
                    "id": 777,
                    "body": "@codex review",
                    "created_at": "2026-08-24T22:15:00Z",
                    "user": {"login": "moonmind-bot"},
                },
            ),
        ]
    )

    with _patch_client(mock_client):
        result = await GitHubService().request_automated_review(
            repo=_REPO,
            pr_number=350,
            expected_head_sha=_HEAD,
            provider="codex",
            attempt_started_at="2026-08-24T22:14:00Z",
            recorded_comment_id=777,
        )

    assert result.status == "recorded"
    assert result.request_comment_id == 777
    assert mock_client.post.await_count == 0


@pytest.mark.asyncio
async def test_request_rejects_unsupported_provider(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "github-token-fixture")
    with pytest.raises(ValueError):
        await GitHubService().request_automated_review(
            repo=_REPO,
            pr_number=350,
            expected_head_sha=_HEAD,
            provider="totally-not-configured",
            attempt_started_at="2026-08-24T22:14:00Z",
        )


# ---------------------------------------------------------------------------
# request-bound readiness
# ---------------------------------------------------------------------------


_ACTIVE_REQUEST = {
    "provider": "codex",
    "headSha": _HEAD,
    "requestKey": "key",
    "requestCommentId": 98765,
    "requestedAt": "2026-08-24T22:15:00Z",
}


def _readiness_prefix():
    return [
        _get(
            200,
            {
                "state": "open",
                "merged": False,
                "head": {"sha": _HEAD},
                "base": {"sha": "base"},
                "mergeable": True,
                "mergeable_state": "clean",
            },
        ),
        _get(200, {"state": "success", "statuses": []}),
        _get(
            200,
            {"check_runs": [{"status": "completed", "conclusion": "success"}]},
        ),
    ]


@pytest.mark.asyncio
async def test_review_loop_without_active_request_opens_the_gate(monkeypatch):
    """The first resolver pass must run before any review is requested."""

    monkeypatch.setenv("GITHUB_TOKEN", "github-token-fixture")
    mock_client = _client(get_responses=_readiness_prefix())

    with _patch_client(mock_client):
        result = await GitHubService().evaluate_pull_request_readiness(
            repo=_REPO,
            pr_number=350,
            head_sha=_HEAD,
            policy={"checks": "required", "automatedReview": "required"},
            review_loop_enabled=True,
            review_request=None,
        )

    assert result.ready is True
    assert result.automated_review_complete is None
    assert result.blockers == []


@pytest.mark.asyncio
async def test_requested_review_ignores_older_codex_review(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "github-token-fixture")
    mock_client = _client(
        get_responses=[
            *_readiness_prefix(),
            _get(
                200,
                [
                    {
                        "id": 1,
                        "state": "COMMENTED",
                        "commit_id": _OLD_HEAD,
                        "submitted_at": "2026-08-24T21:00:00Z",
                        "user": {"login": "chatgpt-codex-connector"},
                    }
                ],
            ),
            _get(200, []),
            _get(200, []),
            _get(200, []),
        ]
    )

    with _patch_client(mock_client):
        result = await GitHubService().evaluate_pull_request_readiness(
            repo=_REPO,
            pr_number=350,
            head_sha=_HEAD,
            policy={"checks": "required", "automatedReview": "required"},
            review_loop_enabled=True,
            review_request=_ACTIVE_REQUEST,
        )

    assert result.ready is False
    assert result.automated_review_complete is False
    assert [b["kind"] for b in result.blockers] == ["automated_review_pending"]


@pytest.mark.asyncio
async def test_requested_review_accepts_review_for_requested_commit(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "github-token-fixture")
    mock_client = _client(
        get_responses=[
            *_readiness_prefix(),
            _get(
                200,
                [
                    {
                        "id": 45678,
                        "state": "COMMENTED",
                        "commit_id": _HEAD,
                        "submitted_at": "2026-08-24T22:19:00Z",
                        "user": {"login": "chatgpt-codex-connector"},
                    }
                ],
            ),
        ]
    )

    with _patch_client(mock_client):
        result = await GitHubService().evaluate_pull_request_readiness(
            repo=_REPO,
            pr_number=350,
            head_sha=_HEAD,
            policy={"checks": "required", "automatedReview": "required"},
            review_loop_enabled=True,
            review_request=_ACTIVE_REQUEST,
        )

    assert result.ready is True
    assert result.automated_review_complete is True
    assert result.automated_review_completion_kind == "review"
    assert result.automated_review_completion_id == 45678
    assert result.automated_review_completed_at == "2026-08-24T22:19:00Z"


@pytest.mark.asyncio
async def test_requested_review_follows_pagination_for_requested_commit(monkeypatch):
    """A requested review can arrive after GitHub's first 100 results."""

    monkeypatch.setenv("GITHUB_TOKEN", "github-token-fixture")
    second_page_url = (
        f"https://api.github.com/repos/{_REPO}/pulls/350/reviews"
        "?page=2&per_page=100"
    )
    first_page = _get(
        200,
        [
            {
                "id": 1,
                "state": "COMMENTED",
                "commit_id": _OLD_HEAD,
                "submitted_at": "2026-08-24T21:00:00Z",
                "user": {"login": "chatgpt-codex-connector"},
            }
        ],
        headers={"Link": f'<{second_page_url}>; rel="next"'},
    )
    mock_client = _client(
        get_responses=[
            *_readiness_prefix(),
            first_page,
            _get(
                200,
                [
                    {
                        "id": 45678,
                        "state": "COMMENTED",
                        "commit_id": _HEAD,
                        "submitted_at": "2026-08-24T22:19:00Z",
                        "user": {"login": "chatgpt-codex-connector[bot]"},
                    }
                ],
            ),
        ]
    )

    with _patch_client(mock_client):
        result = await GitHubService().evaluate_pull_request_readiness(
            repo=_REPO,
            pr_number=350,
            head_sha=_HEAD,
            policy={"checks": "required", "automatedReview": "required"},
            review_loop_enabled=True,
            review_request=_ACTIVE_REQUEST,
        )

    assert result.ready is True
    assert result.automated_review_complete is True
    assert result.automated_review_completion_kind == "review"
    assert result.automated_review_completion_id == 45678
    assert mock_client.get.await_args_list[4].args == (second_page_url,)


@pytest.mark.asyncio
async def test_requested_review_rejects_review_for_a_different_commit(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "github-token-fixture")
    mock_client = _client(
        get_responses=[
            *_readiness_prefix(),
            _get(
                200,
                [
                    {
                        "id": 3,
                        "state": "COMMENTED",
                        "commit_id": _OLD_HEAD,
                        "submitted_at": "2026-08-24T22:30:00Z",
                        "user": {"login": "chatgpt-codex-connector"},
                    }
                ],
            ),
            _get(200, []),
            _get(200, []),
            _get(200, []),
        ]
    )

    with _patch_client(mock_client):
        result = await GitHubService().evaluate_pull_request_readiness(
            repo=_REPO,
            pr_number=350,
            head_sha=_HEAD,
            policy={"checks": "required", "automatedReview": "required"},
            review_loop_enabled=True,
            review_request=_ACTIVE_REQUEST,
        )

    assert result.automated_review_complete is False


@pytest.mark.asyncio
async def test_requested_review_surfaces_paginated_provider_usage_failure(monkeypatch):
    """A provider-authored quota response terminates the request wait."""

    monkeypatch.setenv("GITHUB_TOKEN", "github-token-fixture")
    second_page_url = (
        f"https://api.github.com/repos/{_REPO}/issues/350/comments"
        "?page=2&per_page=100"
    )
    first_page = _get(
        200,
        [
            {
                "id": 1,
                "body": "A human mentioned a usage limit.",
                "created_at": "2026-08-24T22:20:00Z",
                "user": {"login": "reviewer-a"},
            }
        ],
        headers={"Link": f'<{second_page_url}>; rel="next"'},
    )
    mock_client = _client(
        get_responses=[
            *_readiness_prefix(),
            _get(200, []),
            _get(200, []),
            _get(200, []),
            first_page,
            _get(
                200,
                [
                    {
                        "id": 99,
                        "body": (
                            "You have reached your Codex usage limits for code "
                            "reviews."
                        ),
                        "created_at": "2026-08-24T22:20:01Z",
                        "user": {
                            "login": "chatgpt-codex-connector[bot]",
                            "type": "Bot",
                        },
                    }
                ],
            ),
        ]
    )

    with _patch_client(mock_client):
        result = await GitHubService().evaluate_pull_request_readiness(
            repo=_REPO,
            pr_number=350,
            head_sha=_HEAD,
            policy={"checks": "required", "automatedReview": "required"},
            review_loop_enabled=True,
            review_request=_ACTIVE_REQUEST,
        )

    assert result.ready is False
    assert result.automated_review_complete is None
    assert [blocker["kind"] for blocker in result.blockers] == [
        "automated_review_request_failed"
    ]
    assert result.blockers[0]["retryable"] is False
    assert result.blockers[0]["source"] == "codex"
    assert result.blockers[0]["providerFailure"]["providerErrorClass"] == (
        "rate_limit"
    )
    assert "Codex usage limits" not in result.blockers[0]["summary"]
    assert mock_client.get.await_args_list[-1].args == (second_page_url,)


@pytest.mark.asyncio
async def test_requested_review_accepts_reaction_on_request_comment(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "github-token-fixture")
    mock_client = _client(
        get_responses=[
            *_readiness_prefix(),
            _get(200, []),
            _get(
                200,
                [
                    {
                        "id": 55,
                        "content": "+1",
                        "created_at": "2026-08-24T22:20:00Z",
                        "user": {"login": "chatgpt-codex-connector[bot]"},
                    }
                ],
            ),
        ]
    )

    with _patch_client(mock_client):
        result = await GitHubService().evaluate_pull_request_readiness(
            repo=_REPO,
            pr_number=350,
            head_sha=_HEAD,
            policy={"checks": "required", "automatedReview": "required"},
            review_loop_enabled=True,
            review_request=_ACTIVE_REQUEST,
        )

    assert result.automated_review_complete is True
    assert result.automated_review_completion_kind == "reaction"
    assert result.automated_review_completion_id == 55


@pytest.mark.asyncio
async def test_requested_review_reports_stale_when_head_moves(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "github-token-fixture")
    moved_prefix = [
        _get(
            200,
            {
                "state": "open",
                "merged": False,
                "head": {"sha": "ffffffffffffffffffffffffffffffffffffffff"},
                "base": {"sha": "base"},
                "mergeable": True,
                "mergeable_state": "clean",
            },
        ),
        _get(200, {"state": "success", "statuses": []}),
        _get(
            200,
            {"check_runs": [{"status": "completed", "conclusion": "success"}]},
        ),
    ]
    mock_client = _client(get_responses=moved_prefix)

    with _patch_client(mock_client):
        result = await GitHubService().evaluate_pull_request_readiness(
            repo=_REPO,
            pr_number=350,
            head_sha=_HEAD,
            policy={"checks": "required", "automatedReview": "required"},
            review_loop_enabled=True,
            review_request=_ACTIVE_REQUEST,
        )

    assert result.automated_review_request_stale is True
    assert result.automated_review_complete is False
    assert result.ready is False


@pytest.mark.asyncio
async def test_review_loop_disabled_keeps_legacy_evaluation(monkeypatch):
    """With no review loop the historical any-Codex-result gate still applies."""

    monkeypatch.setenv("GITHUB_TOKEN", "github-token-fixture")
    mock_client = _client(
        get_responses=[
            *_readiness_prefix(),
            _get(
                200,
                [
                    {
                        "id": 1,
                        "state": "COMMENTED",
                        "submitted_at": "2020-01-01T00:00:00Z",
                        "user": {"login": "chatgpt-codex-connector", "type": "Bot"},
                    }
                ],
            ),
        ]
    )

    with _patch_client(mock_client):
        result = await GitHubService().evaluate_pull_request_readiness(
            repo=_REPO,
            pr_number=350,
            head_sha=_HEAD,
            policy={"checks": "required", "automatedReview": "required"},
        )

    assert result.automated_review_complete is True
    assert result.ready is True
