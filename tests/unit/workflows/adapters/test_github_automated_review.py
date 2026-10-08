"""GitHub-boundary tests for the automated review request/result protocol."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
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


def _review_clock(monkeypatch, initial):
    from moonmind.workflows.adapters import github_service

    current = [datetime.fromisoformat(initial)]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return current[0].astimezone(tz or timezone.utc)

    monkeypatch.setattr(github_service, "datetime", Clock)
    return current


# ---------------------------------------------------------------------------
# request_automated_review
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("expires_at", [None, "2026-08-24T22:16:00Z"])
async def test_request_posts_exactly_the_configured_command(monkeypatch, expires_at):
    monkeypatch.setenv("GITHUB_TOKEN", "github-token-fixture")
    _review_clock(monkeypatch, "2026-08-24T22:15:00+00:00")
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
            expires_at=expires_at,
        )

    assert result.status == "requested"
    assert result.request_comment_id == 98765
    assert result.requested_at == "2026-08-24T22:15:00Z"
    assert result.actor == "moonmind-bot"
    # The command is the trusted provider command, never caller-supplied text.
    assert mock_client.post.await_args.kwargs["json"] == {"body": "@codex review"}


@pytest.mark.asyncio
@pytest.mark.parametrize("expires_at", [None, "2026-08-24T22:16:00Z"])
async def test_request_reconciles_ambiguous_post_instead_of_posting_twice(monkeypatch, expires_at):
    """A lost response is recovered by adopting the comment it created."""

    monkeypatch.setenv("GITHUB_TOKEN", "github-token-fixture")
    _review_clock(monkeypatch, "2026-08-24T22:17:00+00:00")
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
            expires_at=expires_at,
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
@pytest.mark.parametrize("expires_at", [None, "2026-08-24T22:16:00Z"])
async def test_request_adopts_previously_recorded_comment(monkeypatch, expires_at):
    monkeypatch.setenv("GITHUB_TOKEN", "github-token-fixture")
    _review_clock(monkeypatch, "2026-08-24T22:17:00+00:00")
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
            expires_at=expires_at,
        )

    assert result.status == "recorded"
    assert result.request_comment_id == 777
    assert mock_client.post.await_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("expiry_case", ["already_expired", "during_read", "on_retry"])
async def test_request_deadline_prevents_a_new_post(monkeypatch, expiry_case):
    current = _review_clock(
        monkeypatch,
        "2026-08-24T22:17:00+00:00"
        if expiry_case == "already_expired"
        else "2026-08-24T22:15:00+00:00",
    )
    responses = [
        _get(200, {"state": "open", "merged": False, "head": {"sha": _HEAD}}),
        _get(200, []),
    ]
    if expiry_case == "on_retry":
        responses.insert(0, httpx.ReadTimeout("first attempt could not read PR"))
    client = _client(get_responses=[])

    async def get(*_args, **_kwargs):
        response = responses.pop(0)
        current[0] = datetime.fromisoformat("2026-08-24T22:17:00+00:00")
        if isinstance(response, Exception):
            raise response
        return response

    client.get.side_effect = get
    request = {
        "repo": _REPO, "pr_number": 350, "expected_head_sha": _HEAD,
        "provider": "codex", "attempt_started_at": "2026-08-24T22:14:00Z",
        "github_token": "selected-token", "expires_at": "2026-08-24T22:16:00Z",
    }
    with _patch_client(client):
        service = GitHubService()
        if expiry_case == "on_retry":
            first = await service.request_automated_review(**request)
            assert first.status == "unavailable" and first.retryable is True
        result = await service.request_automated_review(**request)
    assert result.status == "expired"
    assert result.retryable is False
    assert result.request_comment_id is None
    client.post.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("expires_at", ["", "not-a-timestamp"])
async def test_request_rejects_invalid_deadline_without_posting(expires_at):
    with pytest.raises(ValueError, match="expires_at must be an ISO timestamp"):
        await GitHubService().request_automated_review(
            repo=_REPO, pr_number=350, expected_head_sha=_HEAD, provider="codex",
            attempt_started_at="2026-08-24T22:14:00Z", expires_at=expires_at,
        )


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


@pytest.mark.asyncio
@pytest.mark.parametrize("blocker", ["ci", "conflict"])
async def test_pending_requested_review_blocks_remediation(monkeypatch, blocker):
    from moonmind.workflows.temporal.workflows.merge_gate import classify_readiness

    monkeypatch.setenv("GITHUB_TOKEN", "github-token-fixture")
    prefix = _readiness_prefix()
    if blocker == "ci":
        prefix[1] = _get(200, {"state": "failure", "statuses": []})
        prefix[2] = _get(
            200, {"check_runs": [{"status": "completed", "conclusion": "failure"}]}
        )
    else:
        prefix[0] = _get(
            200,
            {
                "state": "open",
                "merged": False,
                "head": {"sha": _HEAD},
                "mergeable": False,
                "mergeable_state": "dirty",
            },
        )
    mock_client = _client(get_responses=[*prefix, *[_get(200, []) for _ in range(4)]])
    with _patch_client(mock_client):
        result = await GitHubService().evaluate_pull_request_readiness(
            repo=_REPO,
            pr_number=350,
            head_sha=_HEAD,
            review_loop_enabled=True,
            review_request=_ACTIVE_REQUEST,
        )
    assert result.automated_review_complete is False
    assert (
        classify_readiness(
            result.model_dump(by_alias=True), tracked_head_sha=_HEAD
        ).ready
        is False
    )


@pytest.mark.asyncio
async def test_requested_review_accepts_paginated_clean_comment(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "github-token-fixture")
    page2 = f"https://api.github.com/repos/{_REPO}/issues/350/comments?page=2"
    mock_client = _client(
        get_responses=[
            *_readiness_prefix(),
            _get(200, []),
            _get(200, [], headers={"Link": f'<{page2}>; rel="next"'}),
            _get(
                200,
                [
                    {
                        "id": 56,
                        "body": "**Codex Review:** Didn't find any major issues. 🚀",
                        "created_at": "2026-08-24T22:20:00Z",
                        "user": {"login": "chatgpt-codex-connector[bot]"},
                    }
                ],
            ),
            _get(200, []),
            _get(200, []),
        ]
    )
    with _patch_client(mock_client):
        result = await GitHubService().evaluate_pull_request_readiness(
            repo=_REPO,
            pr_number=350,
            head_sha=_HEAD,
            review_loop_enabled=True,
            review_request=_ACTIVE_REQUEST,
        )
    assert result.automated_review_complete is True
    assert result.automated_review_completion_kind == "issue_comment"
    assert result.automated_review_completion_id == 56
    assert result.ready is True


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["PENDING", "DISMISSED", "", "FUTURE_STATE"])
async def test_requested_review_requires_a_submitted_known_state(monkeypatch, state):
    monkeypatch.setenv("GITHUB_TOKEN", "github-token-fixture")
    mock_client = _client(
        get_responses=[
            *_readiness_prefix(),
            _get(
                200,
                [
                    {
                        "id": 50,
                        "state": state,
                        "commit_id": _HEAD,
                        "submitted_at": "2026-08-24T22:19:00Z",
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
            review_loop_enabled=True,
            review_request=_ACTIVE_REQUEST,
        )
    assert result.ready is False
    assert result.automated_review_complete is False


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
        f"https://api.github.com/repos/{_REPO}/pulls/350/reviews" "?page=2&per_page=100"
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
    assert result.automated_review_complete is None
    assert [blocker["kind"] for blocker in result.blockers] == [
        "automated_review_request_failed"
    ]
    assert result.blockers[0]["retryable"] is False
    assert result.blockers[0]["source"] == "codex"
    assert result.blockers[0]["providerFailure"]["providerErrorClass"] == ("rate_limit")
    assert "Codex usage limits" not in result.blockers[0]["summary"]
    assert any(
        call.args == (second_page_url,) for call in mock_client.get.await_args_list
    )


# The provider's status comment: short SHAs and timestamps are not HTTP status
# codes.
_CODEX_STATUS_SUMMARY = (
    "<!-- codex-pull-request-review-summary -->\n\n## Codex Review Summary\n\n"
    "| Review | Status | Commit | Review trigger |\n| --- | --- | --- | --- |\n"
    "| 📝 **Code Review** | ⏳ **Running** | `4290abc` | Manual request |"
)
# A task reply that merely discusses failures it fixed.
_CODEX_TASK_REPLY = (
    "### Summary\n* Return 403 Forbidden when the token is unauthorized and "
    "retry later on the API rate limit."
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [_CODEX_STATUS_SUMMARY, _CODEX_TASK_REPLY],
    ids=["status_summary", "task_reply"],
)
async def test_requested_review_ignores_provider_chatter(monkeypatch, body):
    monkeypatch.setenv("GITHUB_TOKEN", "github-token-fixture")
    mock_client = _client(
        get_responses=[
            *_readiness_prefix(),
            _get(200, []),
            _get(
                200,
                [
                    {
                        "id": 99,
                        "body": body,
                        "created_at": "2026-08-24T22:20:01Z",
                        "user": {"login": "chatgpt-codex-connector[bot]"},
                    }
                ],
            ),
            _get(200, []),
            _get(200, []),
        ]
    )

    with _patch_client(mock_client):
        result = await GitHubService().evaluate_pull_request_readiness(
            repo=_REPO,
            pr_number=350,
            head_sha=_HEAD,
            review_loop_enabled=True,
            review_request=_ACTIVE_REQUEST,
        )

    assert result.automated_review_complete is False
    assert "automated_review_request_failed" not in [
        blocker["kind"] for blocker in result.blockers
    ]


@pytest.mark.asyncio
async def test_requested_review_accepts_reaction_on_request_comment(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "github-token-fixture")
    mock_client = _client(
        get_responses=[
            *_readiness_prefix(),
            _get(200, []),
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
@pytest.mark.parametrize(
    "reaction_path", ["issues/comments/98765/reactions", "issues/350/reactions"]
)
async def test_app_review_permissions_allow_request_bound_reaction_completion(
    reaction_path,
):
    from moonmind.auth.github_app import build_installation_token_request

    payload = build_installation_token_request(
        operations=["read", "review_request"], repositories=[_REPO]
    )
    permissions = payload["permissions"]
    seen_reaction_paths = []
    reaction = {
        "id": 55,
        "content": "+1",
        "created_at": "2026-08-24T22:20:00Z",
        "user": {"login": "chatgpt-codex-connector[bot]"},
    }

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.headers["Authorization"] == "Bearer synthetic-app-token"
        path = request.url.path.removeprefix(f"/repos/{_REPO}/")
        if path == "pulls/350":
            body = {"state": "open", "merged": False, "head": {"sha": _HEAD}}
        elif path in {"pulls/350/reviews", "issues/350/comments"}:
            body = []
        elif path in {"issues/comments/98765/reactions", "issues/350/reactions"}:
            seen_reaction_paths.append(path)
            # GitHub's two issue-reaction reads require Issues read, unlike
            # issue-comment reads that also accept Pull requests read.
            if permissions.get("issues") not in {"read", "write"}:
                return httpx.Response(
                    403,
                    json={"message": "Resource not accessible by integration"},
                    headers={"X-Accepted-GitHub-Permissions": "issues=read"},
                )
            body = [reaction] if path == reaction_path else []
        else:
            raise AssertionError(f"Unexpected GitHub endpoint: {path}")
        return httpx.Response(200, json=body)

    client = httpx.AsyncClient(transport=httpx.MockTransport(respond), trust_env=False)
    with _patch_client(client):
        result = await GitHubService().evaluate_pull_request_readiness(
            repo=_REPO,
            pr_number=350,
            head_sha=_HEAD,
            github_token="synthetic-app-token",
            policy={"checks": "ignored", "automatedReview": "required"},
            review_loop_enabled=True,
            review_request=_ACTIVE_REQUEST,
        )

    assert result.automated_review_complete is True
    assert result.automated_review_completion_kind == "reaction"
    assert result.automated_review_completion_id == 55
    assert result.ready is True
    assert seen_reaction_paths == (
        ["issues/comments/98765/reactions"]
        if reaction_path == "issues/comments/98765/reactions"
        else ["issues/comments/98765/reactions", "issues/350/reactions"]
    )
    assert payload == {
        "repositories": ["MoonMind"],
        "permissions": {
            "contents": "read",
            "metadata": "read",
            "pull_requests": "write",
            "issues": "read",
        },
    }


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


@pytest.mark.asyncio
@pytest.mark.parametrize("latest_clean", [True, False])
async def test_requested_review_uses_latest_comment_across_pages(
    monkeypatch, latest_clean
):
    monkeypatch.setenv("GITHUB_TOKEN", "github-token-fixture")
    clean = {
        "id": 56,
        "body": "Codex Review: Didn't find any major issues. 🚀",
        "created_at": (
            "2026-08-24T22:20:00Z" if latest_clean else "2026-08-24T22:19:00Z"
        ),
        "user": {"login": "chatgpt-codex-connector[bot]"},
    }
    failure = {
        "id": 55,
        "body": "You have reached your Codex usage limits. Please try again later.",
        "created_at": (
            "2026-08-24T22:19:00Z" if latest_clean else "2026-08-24T22:20:00Z"
        ),
        "user": {"login": "chatgpt-codex-connector[bot]"},
    }
    first, last = (failure, clean) if latest_clean else (clean, failure)
    page2 = f"https://api.github.com/repos/{_REPO}/issues/350/comments?page=2"
    mock_client = _client(
        get_responses=[
            *_readiness_prefix(),
            _get(200, []),
            _get(200, [first], headers={"Link": f'<{page2}>; rel="next"'}),
            _get(200, [last]),
            _get(200, []),
            _get(200, []),
        ]
    )
    with _patch_client(mock_client):
        result = await GitHubService().evaluate_pull_request_readiness(
            repo=_REPO,
            pr_number=350,
            head_sha=_HEAD,
            review_loop_enabled=True,
            review_request=_ACTIVE_REQUEST,
        )
    assert (result.automated_review_complete is True) is latest_clean
    assert result.ready is latest_clean


@pytest.mark.asyncio
@pytest.mark.parametrize("reviewed_head", [True, False])
async def test_requested_review_real_codex_clean_reply_completes_only_its_head(
    monkeypatch, reviewed_head
):
    monkeypatch.setenv("GITHUB_TOKEN", "github-token-fixture")
    reply = json.loads(
        (
            Path(__file__).resolve().parents[4]
            / "tests/fixtures/pr_resolver/codex_clean_replies.json"
        ).read_text(encoding="utf-8")
    )[-1]
    head = reply["headSha"] if reviewed_head else _HEAD
    prefix = _readiness_prefix()
    prefix[0] = _get(
        200,
        {
            "state": "open",
            "merged": False,
            "head": {"sha": head},
            "base": {"sha": "base"},
            "mergeable": True,
            "mergeable_state": "clean",
        },
    )
    mock_client = _client(
        get_responses=[
            *prefix,
            _get(200, []),
            _get(
                200,
                [
                    {
                        "id": reply["commentId"],
                        "body": reply["body"],
                        "created_at": reply["createdAt"],
                        "user": {"login": "chatgpt-codex-connector[bot]"},
                    }
                ],
            ),
            _get(200, []),
            _get(200, []),
        ]
    )
    with _patch_client(mock_client):
        result = await GitHubService().evaluate_pull_request_readiness(
            repo=_REPO,
            pr_number=350,
            head_sha=head,
            review_loop_enabled=True,
            review_request={**_ACTIVE_REQUEST, "headSha": head},
        )

    assert (result.automated_review_complete is True) is reviewed_head
    assert result.automated_review_completion_kind == (
        "issue_comment" if reviewed_head else None
    )
    assert result.ready is reviewed_head


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "alternative",
    [
        "none",
        "old_comment",
        "other_provider_comment",
        "different_commit_comment",
        "malformed_comment_timestamp",
        "stale_pr_reaction",
        "other_provider_reaction",
        "malformed_pr_reaction_timestamp",
    ],
)
async def test_requested_review_reaction_denial_is_terminal_without_fresh_evidence(
    monkeypatch, alternative
):
    from moonmind.workflows.temporal.workflows.merge_gate import (
        TERMINAL_BLOCKER_KINDS,
        classify_readiness,
    )

    monkeypatch.setenv("GITHUB_TOKEN", "github-token-fixture")
    comment = {
        "id": 56,
        "body": "Codex Review: Didn't find any major issues. \U0001f680",
        "created_at": "2026-08-24T22:20:00Z",
        "user": {"login": "chatgpt-codex-connector[bot]"},
    }
    reaction = {
        "id": 55,
        "content": "+1",
        "created_at": "2026-08-24T22:20:00Z",
        "user": {"login": "chatgpt-codex-connector[bot]"},
    }
    comments = []
    reactions = []
    if alternative.endswith("comment") or alternative == "malformed_comment_timestamp":
        comments = [comment]
        if alternative == "old_comment":
            comment["created_at"] = "2026-08-24T22:14:00Z"
        elif alternative == "other_provider_comment":
            comment["user"] = {"login": "unrelated-reviewer[bot]"}
        elif alternative == "different_commit_comment":
            comment["commit_id"] = _OLD_HEAD
        else:
            comment["created_at"] = "not-a-timestamp"
    elif alternative != "none":
        reactions = [reaction]
        if alternative == "stale_pr_reaction":
            reaction["created_at"] = "2026-08-24T22:14:00Z"
        elif alternative == "other_provider_reaction":
            reaction["user"] = {"login": "unrelated-reviewer[bot]"}
        else:
            reaction["created_at"] = "not-a-timestamp"
    mock_client = _client(
        get_responses=[
            *_readiness_prefix(),
            _get(200, []),
            _get(200, comments),
            _get(
                403,
                {"message": "Resource not accessible; token=fixture-secret-value"},
                headers={"X-Accepted-GitHub-Permissions": "issues=read"},
            ),
            _get(200, reactions),
        ]
    )
    with _patch_client(mock_client):
        result = await GitHubService().evaluate_pull_request_readiness(
            repo=_REPO,
            pr_number=350,
            head_sha=_HEAD,
            review_loop_enabled=True,
            review_request=_ACTIVE_REQUEST,
        )

    assert result.automated_review_complete is None
    assert result.ready is False
    assert result.automated_review_completion_kind is None
    assert [blocker["kind"] for blocker in result.blockers] == ["policy_denied"]
    blocker = result.blockers[0]
    assert blocker["retryable"] is False
    assert blocker["source"] == "github"
    assert blocker["evidenceSource"] == "issue_reactions"
    assert blocker["missingPermission"] == "Issues: read"
    assert "fixture-secret-value" not in blocker["summary"]
    assert "issues=read" in blocker["summary"]
    evidence = classify_readiness(
        result.model_dump(by_alias=True), tracked_head_sha=_HEAD
    )
    assert evidence.ready is False
    assert [blocker.kind for blocker in evidence.blockers] == ["policy_denied"]
    assert evidence.blockers[0].kind in TERMINAL_BLOCKER_KINDS
    mock_client.post.assert_not_awaited()


@pytest.mark.asyncio
async def test_requested_review_fresh_clean_comment_completes_despite_reaction_denial(
    monkeypatch,
):
    monkeypatch.setenv("GITHUB_TOKEN", "github-token-fixture")
    mock_client = _client(
        get_responses=[
            *_readiness_prefix(),
            _get(200, []),
            _get(
                200,
                [
                    {
                        "id": 56,
                        "body": "Codex Review: Didn't find any major issues. \U0001f680",
                        "created_at": "2026-08-24T22:20:00Z",
                        "user": {"login": "chatgpt-codex-connector[bot]"},
                    }
                ],
            ),
            _get(403, {"message": "Resource not accessible by integration"}),
            _get(403, {"message": "Resource not accessible by integration"}),
        ]
    )
    with _patch_client(mock_client):
        result = await GitHubService().evaluate_pull_request_readiness(
            repo=_REPO,
            pr_number=350,
            head_sha=_HEAD,
            review_loop_enabled=True,
            review_request=_ACTIVE_REQUEST,
        )

    assert result.automated_review_complete is True
    assert result.automated_review_completion_kind == "issue_comment"
    assert result.automated_review_completion_id == 56
    assert result.ready is True
    assert result.blockers == []
    mock_client.post.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "headers", "message"),
    [
        (403, {"x-ratelimit-remaining": "0"}, "API rate limit exceeded"),
        (403, {}, "API rate limit exceeded"),
        (403, {"retry-after": "60"}, "Secondary rate limit"),
        (429, {}, "Too many requests"),
        (401, {}, "Bad credentials"),
        (503, {}, "Service unavailable"),
    ],
)
async def test_requested_review_nonpermission_reaction_errors_keep_existing_wait(
    monkeypatch, status, headers, message
):
    monkeypatch.setenv("GITHUB_TOKEN", "github-token-fixture")
    mock_client = _client(
        get_responses=[
            *_readiness_prefix(),
            _get(200, []),
            _get(200, []),
            _get(status, {"message": message}, headers=headers),
            _get(200, []),
        ]
    )
    with _patch_client(mock_client):
        result = await GitHubService().evaluate_pull_request_readiness(
            repo=_REPO,
            pr_number=350,
            head_sha=_HEAD,
            review_loop_enabled=True,
            review_request=_ACTIVE_REQUEST,
        )

    assert result.ready is False
    assert result.automated_review_complete is False
    assert [blocker["kind"] for blocker in result.blockers] == [
        "automated_review_pending"
    ]
    assert result.blockers[0]["retryable"] is True
    mock_client.post.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("same_second", [True, False])
@pytest.mark.parametrize(
    "reply",
    [
        "none",
        "failure",
        "clean",
        "review",
        "reaction",
        "old_review",
        "old_reaction",
        "old_clean",
        "old_pr_reaction",
    ],
)
async def test_new_request_supersedes_old_refusal_and_completion(same_second, reply):
    first_time = _ACTIVE_REQUEST["requestedAt"]
    later_time = first_time if same_second else "2026-08-24T22:20:00Z"
    old_failure = {
        "id": 98766,
        "body": "You have reached your Codex usage limits for code reviews.",
        "created_at": first_time if same_second else "2026-08-24T22:16:00Z",
        "user": {"login": "chatgpt-codex-connector[bot]"},
    }
    new_request = {"id": 98767, "body": "@codex review", "created_at": later_time}
    comments = [new_request]
    if reply == "old_clean":
        old_failure["body"] = "Codex Review: Didn't find any major issues. 🚀"
    if reply in {"clean", "failure"}:
        comments.append(
            {
                **old_failure,
                "id": 98768,
                "created_at": later_time,
                "body": (
                    "Codex Review: Didn't find any major issues. 🚀"
                    if reply == "clean"
                    else old_failure["body"]
                ),
            }
        )
    requests = []

    def respond(request):
        path = request.url.path.removeprefix(f"/repos/{_REPO}/")
        requests.append(path)
        if path == "pulls/350":
            body = {"state": "open", "merged": False, "head": {"sha": _HEAD}}
        elif path == "pulls/350/reviews":
            body = (
                [
                    {
                        "id": 777,
                        "state": "COMMENTED",
                        "commit_id": _HEAD,
                        "submitted_at": (
                            old_failure["created_at"]
                            if reply == "old_review"
                            else "2026-08-24T22:21:00Z"
                        ),
                        "user": old_failure["user"],
                    }
                ]
                if reply in {"review", "old_review"}
                else []
            )
        elif path == "issues/350/comments":
            if request.url.params.get("page") != "2":
                return httpx.Response(
                    200,
                    json=[old_failure],
                    headers={
                        "Link": f'<https://api.github.com/repos/{_REPO}/issues/350/comments?page=2>; rel="next"'
                    },
                )
            body = comments
        elif path == "issues/comments/98767/reactions":
            body = (
                [{"id": 778, "content": "+1", "user": old_failure["user"]}]
                if reply == "reaction"
                else []
            )
        elif path in {"issues/comments/98765/reactions", "issues/350/reactions"}:
            body = (
                [
                    {
                        "id": 999999,
                        "content": "+1",
                        "created_at": old_failure["created_at"],
                        "user": old_failure["user"],
                    }
                ]
                if (
                    reply == "old_reaction"
                    and path == "issues/comments/98765/reactions"
                )
                or (reply == "old_pr_reaction" and path == "issues/350/reactions")
                else []
            )
        else:
            raise AssertionError(path)
        return httpx.Response(200, json=body)

    client = httpx.AsyncClient(transport=httpx.MockTransport(respond), trust_env=False)
    with _patch_client(client):
        result = await GitHubService().evaluate_pull_request_readiness(
            repo=_REPO,
            pr_number=350,
            head_sha=_HEAD,
            github_token="synthetic-token",
            policy={"checks": "ignored", "automatedReview": "required"},
            review_loop_enabled=True,
            review_request=_ACTIVE_REQUEST,
        )
    expected_complete = (
        None if reply == "failure" else reply in {"clean", "review", "reaction"}
    )
    assert result.automated_review_complete is expected_complete
    assert result.automated_review_request_comment_id == 98767
    assert result.automated_review_requested_at == later_time
    assert result.ready is (expected_complete is True)
    if expected_complete is False:
        assert [b["kind"] for b in result.blockers] == ["automated_review_pending"]
    if reply == "reaction":
        assert "issues/comments/98767/reactions" in requests


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        "finding",
        "quote",
        "indented",
        "other_provider",
        "old_head",
        "earlier_id",
        "missing_id",
    ],
)
async def test_same_second_runtime_reply_keeps_identity_head_and_body_guards(change):
    reply = {
        "id": 98766,
        "body": "You have reached your Codex usage limits for code reviews.",
        "created_at": _ACTIVE_REQUEST["requestedAt"],
        "user": {"login": "chatgpt-codex-connector[bot]"},
    }
    if change == "finding":
        reply["body"] = "[P1] Preserve rate limit metadata"
    elif change == "quote":
        reply["body"] = "> " + reply["body"]
    elif change == "indented":
        reply["body"] = "    " + reply["body"]
    elif change == "other_provider":
        reply["user"] = {"login": "codex[bot]"}
    elif change == "old_head":
        reply["commit_id"] = _OLD_HEAD
    elif change == "earlier_id":
        reply["id"] = 98764
    else:
        reply["id"] = None
    mock_client = _client(
        get_responses=[
            *_readiness_prefix(),
            _get(200, []),
            _get(200, [reply]),
            _get(200, []),
            _get(200, []),
        ]
    )
    with _patch_client(mock_client):
        result = await GitHubService().evaluate_pull_request_readiness(
            repo=_REPO,
            pr_number=350,
            head_sha=_HEAD,
            github_token="synthetic-token",
            review_loop_enabled=True,
            review_request=_ACTIVE_REQUEST,
        )
    assert result.automated_review_complete is False
    assert result.ready is False
    assert [b["kind"] for b in result.blockers] == ["automated_review_pending"]


@pytest.mark.asyncio
@pytest.mark.parametrize("problem", ["malformed", "http_error", "partial_page", "denied"])
async def test_unavailable_request_inventory_cannot_accept_an_older_review(problem):
    reviews = [
        {
            "id": 777,
            "state": "COMMENTED",
            "commit_id": _HEAD,
            "submitted_at": "2026-08-24T22:20:00Z",
            "user": {"login": "chatgpt-codex-connector[bot]"},
        }
    ]
    bad = (
        _get(200, {"message": "not a collection"})
        if problem == "malformed"
        else _get(503, {"message": "unavailable"})
    )
    if problem == "denied":
        bad = _get(403, {"message": "denied"})
    pages = [bad]
    if problem == "partial_page":
        pages.insert(
            0,
            _get(
                200, [], headers={"Link": '<https://api.github.com/page2>; rel="next"'}
            ),
        )
    mock_client = _client(
        get_responses=[*_readiness_prefix(), _get(200, reviews), *pages]
    )
    with _patch_client(mock_client):
        result = await GitHubService().evaluate_pull_request_readiness(
            repo=_REPO,
            pr_number=350,
            head_sha=_HEAD,
            github_token="synthetic-token",
            review_loop_enabled=True,
            review_request=_ACTIVE_REQUEST,
        )
    assert result.automated_review_complete is None
    assert result.ready is False
    assert [b["kind"] for b in result.blockers] == ["external_state_unavailable"]

    assert result.blockers[0]["retryable"] is (problem in {"http_error", "partial_page"})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,headers,message,limited,retryable",
    [
        (429, {"retry-after": "90"}, "Too many requests", True, True),
        (
            403,
            {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1800000000"},
            "Forbidden",
            True,
            True,
        ),
        (403, {"retry-after": "30"}, "Secondary rate limit", True, True),
        (403, {}, "API rate limit exceeded", True, True),
        (403, {}, "Resource not accessible by integration", False, False),
        (401, {}, "Bad credentials", False, False),
        (503, {}, "Unavailable", False, True),
    ],
)
async def test_request_inventory_preserves_quota_retry_and_permission_denial(
    status, headers, message, limited, retryable
):
    client = _client(
        get_responses=[
            *_readiness_prefix(),
            _get(200, []),
            _get(status, {"message": message}, headers=headers),
        ]
    )
    with _patch_client(client):
        result = await GitHubService().evaluate_pull_request_readiness(
            repo=_REPO,
            pr_number=350,
            head_sha=_HEAD,
            github_token="synthetic-token",
            review_loop_enabled=True,
            review_request=_ACTIVE_REQUEST,
        )
    assert result.ready is False
    assert result.automated_review_complete is None
    blocker = result.blockers[0]
    assert blocker["kind"] == "external_state_unavailable"
    assert blocker["retryable"] is retryable
    assert bool(blocker.get("providerFailure")) is limited
    if limited:
        assert blocker["providerFailure"]["providerErrorClass"] == "rate_limit"
        if "retry-after" in headers:
            assert blocker["providerFailure"]["retryAfterSeconds"] == float(
                headers["retry-after"]
            )
