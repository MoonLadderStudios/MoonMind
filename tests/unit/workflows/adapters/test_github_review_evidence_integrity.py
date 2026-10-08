"""Request-bound review evidence must be complete, current, and causally bound."""

from __future__ import annotations

import httpx
import pytest

from moonmind.workflows.adapters.github_service import GitHubService
from tests.unit.workflows.adapters.test_github_automated_review import _patch_client

REPO = "MoonLadderStudios/MoonMind"
HEAD = "a" * 40
WHEN = "2026-08-24T22:15:00Z"
REQUEST = {
    "provider": "codex",
    "headSha": HEAD,
    "requestCommentId": 100,
    "requestedAt": WHEN,
}
COMMAND = {"id": 100, "body": "@codex review", "created_at": WHEN}
USER = {"login": "chatgpt-codex-connector[bot]"}
REFUSAL = {
    "id": 101,
    "created_at": "2026-08-24T22:16:00Z",
    "user": USER,
    "body": "You have reached your Codex usage limits for code reviews.",
}
CLEAN = {**REFUSAL, "body": "Codex Review: Didn't find any major issues. 🚀"}
REACTION = {
    "id": 102,
    "created_at": "2026-08-24T22:17:00Z",
    "user": USER,
    "content": "+1",
}
REVIEW = {
    "id": 103,
    "submitted_at": "2026-08-24T22:17:00Z",
    "user": USER,
    "commit_id": HEAD,
    "state": "COMMENTED",
}


async def observe(*, comments=None, routes=None, active=None, heads=None):
    routes = dict(routes or {})
    heads = list(heads or [HEAD])
    seen = []

    def respond(request):
        path = request.url.path.removeprefix(f"/repos/{REPO}/")
        page = request.url.params.get("page")
        key = path + (f"?page={page}" if page else "")
        seen.append(key)
        if key in routes:
            result = routes[key]
            if callable(result):
                result = result()
            if isinstance(result, Exception):
                raise result
            if isinstance(result, httpx.Response):
                return result
            body = result
        elif path == "pulls/350":
            sha = heads.pop(0) if len(heads) > 1 else heads[0]
            body = {"state": "open", "merged": False, "head": {"sha": sha}}
        elif path == "issues/350/comments":
            body = [COMMAND, REFUSAL] if comments is None else comments
        elif path == "pulls/350/reviews" or path.endswith("/reactions"):
            body = []
        else:
            raise AssertionError(key)
        return httpx.Response(200, json=body)

    client = httpx.AsyncClient(transport=httpx.MockTransport(respond), trust_env=False)
    with _patch_client(client):
        result = await GitHubService().evaluate_pull_request_readiness(
            repo=REPO,
            pr_number=350,
            head_sha=HEAD,
            github_token="synthetic-token",
            policy={"checks": "ignored", "automatedReview": "required"},
            review_loop_enabled=True,
            review_request=REQUEST if active is None else active,
        )
    return result, seen


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure,kind,retryable",
    [
        (
            httpx.Response(
                403, json={"message": "Resource not accessible by integration"}
            ),
            "policy_denied",
            False,
        ),
        (
            httpx.Response(429, json={"message": "Too many requests"}),
            "external_state_unavailable",
            True,
        ),
        (
            httpx.Response(503, json={"message": "Unavailable"}),
            "external_state_unavailable",
            True,
        ),
        (httpx.ReadTimeout("timeout"), "external_state_unavailable", True),
        (
            httpx.Response(200, json={"unexpected": "object"}),
            "external_state_unavailable",
            False,
        ),
        (
            httpx.Response(200, content=b"invalid json"),
            "external_state_unavailable",
            False,
        ),
    ],
)
async def test_unavailable_reactions_cannot_be_replaced_by_a_refusal(
    failure, kind, retryable
):
    result, _ = await observe(routes={"issues/comments/100/reactions": failure})
    assert result.automated_review_complete is None
    assert result.ready is False
    assert result.blockers[0]["kind"] == kind
    assert result.blockers[0]["retryable"] is retryable


@pytest.mark.asyncio
async def test_retryable_alternative_outweighs_a_denied_reaction_endpoint():
    result, _ = await observe(
        routes={
            "issues/comments/100/reactions": httpx.Response(
                403, json={"message": "Resource not accessible by integration"}
            ),
            "issues/350/reactions": httpx.Response(
                503, json={"message": "Unavailable"}
            ),
        }
    )
    assert result.blockers[0]["kind"] == "external_state_unavailable"
    assert result.blockers[0]["retryable"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "endpoint", ["issues/comments/100/reactions", "issues/350/reactions"]
)
@pytest.mark.parametrize("last_page", ["clean", "unavailable"])
async def test_reaction_pagination_preserves_completion_or_missing_evidence(
    endpoint, last_page
):
    result, seen = await observe(
        routes={
            endpoint: httpx.Response(
                200,
                json=[],
                headers={
                    "Link": f'<https://api.github.com/repos/{REPO}/{endpoint}?page=2>; rel="next"'
                },
            ),
            endpoint + "?page=2": (
                [REACTION]
                if last_page == "clean"
                else httpx.Response(503, json={"message": "Unavailable"})
            ),
        }
    )
    assert endpoint + "?page=2" in seen
    assert result.automated_review_complete is (True if last_page == "clean" else None)
    assert result.ready is (last_page == "clean")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_page",
    [httpx.Response(200, json={}), httpx.Response(200, content=b"invalid json")],
)
async def test_malformed_review_inventory_cannot_prove_no_completion(bad_page):
    result, _ = await observe(routes={"pulls/350/reviews": bad_page})
    assert result.automated_review_complete is None
    assert result.blockers[0]["kind"] == "external_state_unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize("timestamp", [None, "", "malformed", "2026-08-24T22:15:00"])
@pytest.mark.parametrize("recoverable", [True, False])
async def test_missing_timestamp_recovers_only_the_exact_active_comment(
    timestamp, recoverable
):
    old_request = {**COMMAND, "id": 10, "created_at": "2020-01-01T00:00:00Z"}
    old_clean = {**CLEAN, "id": 11, "created_at": "2020-01-01T00:00:01Z"}
    comments = [old_request, old_clean] + ([COMMAND] if recoverable else [])
    result, _ = await observe(
        comments=comments, active={**REQUEST, "requestedAt": timestamp}
    )
    assert result.ready is False
    assert result.automated_review_complete is (False if recoverable else None)
    if recoverable:
        assert result.automated_review_request_comment_id == 100
        assert result.automated_review_requested_at == WHEN
    else:
        assert result.blockers[0]["kind"] == "external_state_unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize("heads", [[None], [HEAD, "b" * 40], [HEAD, None]])
async def test_completion_requires_an_observed_unchanged_head(heads):
    result, _ = await observe(comments=[COMMAND, CLEAN], heads=heads)
    assert result.ready is False
    assert result.automated_review_complete is not True
    from moonmind.workflows.temporal.workflows.merge_gate import classify_readiness

    evidence = classify_readiness(
        result.model_dump(by_alias=True), tracked_head_sha=HEAD
    )
    assert evidence.ready is False and evidence.head_sha == HEAD


@pytest.mark.asyncio
@pytest.mark.parametrize("completion", ["comment", "review", "other_reaction"])
async def test_qualified_completion_can_supersede_an_unavailable_alternative(
    completion,
):
    result, _ = await observe(
        comments=[COMMAND, CLEAN] if completion == "comment" else None,
        routes={
            "issues/comments/100/reactions": httpx.Response(503, json={}),
            "pulls/350/reviews": [REVIEW] if completion == "review" else [],
            "issues/350/reactions": (
                [REACTION] if completion == "other_reaction" else []
            ),
        },
    )
    assert result.automated_review_complete is True
    assert result.ready is True


@pytest.mark.asyncio
async def test_clean_comment_can_complete_despite_unavailable_review_api():
    result, _ = await observe(
        comments=[COMMAND, CLEAN],
        routes={"pulls/350/reviews": httpx.Response(503, json={})},
    )
    assert result.ready is True and result.automated_review_complete is True


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["comment", "review", "reaction", "refusal"])
@pytest.mark.parametrize("same_second", [False, True])
async def test_terminal_evidence_rechecks_the_latest_request(outcome, same_second):
    first_comments = [COMMAND, CLEAN if outcome == "comment" else REFUSAL]
    second = {
        **COMMAND,
        "id": 110,
        "created_at": WHEN if same_second else "2026-08-24T22:18:00Z",
    }
    inventories = iter([first_comments, [*first_comments, second]])
    result, seen = await observe(
        routes={
            "issues/350/comments": lambda: next(inventories),
            "pulls/350/reviews": [REVIEW] if outcome == "review" else [],
            "issues/comments/100/reactions": [REACTION]
            if outcome == "reaction"
            else [],
        }
    )
    assert seen.count("issues/350/comments") == 2
    assert result.automated_review_complete is False
    assert result.automated_review_request_comment_id == 110
    assert result.automated_review_requested_at == second["created_at"]
    assert result.automated_review_completion_id is None
    assert result.automated_review_request_stale is False
    assert [b["kind"] for b in result.blockers] == ["automated_review_pending"]


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["comment", "refusal"])
@pytest.mark.parametrize(
    "final_pr",
    [
        {"state": "open", "head": {"sha": "b" * 40}},
        {"state": "closed", "head": {"sha": HEAD}},
        {"state": "open", "merged": True, "head": {"sha": HEAD}},
    ],
)
async def test_terminal_evidence_requires_unchanged_open_pr(outcome, final_pr):
    states = iter([{"state": "open", "head": {"sha": HEAD}}, final_pr])
    result, _ = await observe(
        comments=[COMMAND, CLEAN if outcome == "comment" else REFUSAL],
        routes={"pulls/350": lambda: next(states)},
    )
    assert result.ready is False
    assert result.automated_review_complete is False
    assert result.automated_review_request_stale is True
    assert result.automated_review_completion_id is None
    assert [b["kind"] for b in result.blockers] == ["automated_review_pending"]


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["comment", "refusal"])
@pytest.mark.parametrize("endpoint", ["issues/350/comments", "pulls/350"])
async def test_terminal_revalidation_preserves_unavailable_evidence(outcome, endpoint):
    comments = [COMMAND, CLEAN if outcome == "comment" else REFUSAL]
    results = iter(
        [
            comments
            if endpoint.endswith("comments")
            else {"state": "open", "head": {"sha": HEAD}},
            httpx.Response(429, headers={"Retry-After": "23"}, json={}),
        ]
    )
    result, _ = await observe(
        comments=comments, routes={endpoint: lambda: next(results)}
    )
    assert result.ready is False
    assert result.automated_review_complete is None
    assert result.automated_review_completion_id is None
    blocker = result.blockers[0]
    assert blocker["kind"] == "external_state_unavailable"
    assert blocker["retryable"] is True
    assert blocker["providerFailure"]["retryAfterSeconds"] == 23


@pytest.mark.asyncio
@pytest.mark.parametrize("was_refusal", [False, True])
@pytest.mark.parametrize("same_second", [False, True])
@pytest.mark.parametrize("removed", [False, True])
async def test_changed_terminal_reply_requires_a_fresh_observation(
    was_refusal, same_second, removed
):
    first = REFUSAL if was_refusal else CLEAN
    second = {
        **(CLEAN if was_refusal else REFUSAL),
        "id": 111,
        "created_at": first["created_at"] if same_second else "2026-08-24T22:18:00Z",
    }
    refreshed = [COMMAND] if removed else [COMMAND, first, second]
    inventories = iter([[COMMAND, first], refreshed])
    result, _ = await observe(routes={"issues/350/comments": lambda: next(inventories)})
    assert result.ready is False
    assert result.automated_review_complete is False
    assert result.automated_review_request_stale is False
    assert result.automated_review_request_comment_id == 100
    assert result.automated_review_completion_id is None
    assert [b["kind"] for b in result.blockers] == ["automated_review_pending"]
    # The next complete observation settles the new result instead of waiting forever.
    if not removed:
        settled, _ = await observe(comments=refreshed)
        assert settled.automated_review_complete is (True if was_refusal else None)
        assert settled.ready is was_refusal


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "retained_at", ["2026-08-24T22:14:00Z", "2026-08-24T22:18:00Z"]
)
@pytest.mark.parametrize("outcome", ["comment", "refusal", "review", "reaction"])
async def test_exact_github_timestamp_cannot_be_overruled_by_retained_receipt(
    retained_at, outcome
):
    result, _ = await observe(
        comments=[COMMAND, CLEAN if outcome == "comment" else REFUSAL],
        active={**REQUEST, "requestedAt": retained_at},
        routes={
            "pulls/350/reviews": [REVIEW] if outcome == "review" else [],
            "issues/comments/100/reactions": [REACTION]
            if outcome == "reaction"
            else [],
        },
    )
    assert result.ready is False
    assert result.automated_review_complete is None
    assert result.automated_review_completion_id is None
    assert result.blockers[0]["kind"] == "external_state_unavailable"
    assert "timestamp" in result.blockers[0]["summary"]
    assert result.blockers[0]["retryable"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["comment", "refusal"])
async def test_final_exact_request_timestamp_must_still_agree(outcome):
    initial = [COMMAND, CLEAN if outcome == "comment" else REFUSAL]
    final = [{**COMMAND, "created_at": "2026-08-24T22:14:00Z"}, initial[1]]
    inventories = iter([initial, final])
    result, _ = await observe(routes={"issues/350/comments": lambda: next(inventories)})
    assert result.ready is False
    assert result.automated_review_complete is None
    assert result.automated_review_completion_id is None
    assert result.blockers[0]["kind"] == "external_state_unavailable"
    assert "timestamp" in result.blockers[0]["summary"]
