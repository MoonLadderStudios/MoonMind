"""Tests for GitHubService (repo.create_pr / repo.merge_pr)."""

from __future__ import annotations

from types import SimpleNamespace
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
    """MM-680: existing PRs for the same head/base are adopted before create.

    #4018: adoption is not a metadata update; a later actor's title/body stay.
    """
    monkeypatch.setenv("GITHUB_TOKEN", "github-token-fixture")

    existing_pr = {
        "number": 42,
        "html_url": "https://github.com/o/r/pull/42",
        "title": "Edited by reviewer",
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
        )

    assert result.created is False
    assert result.adopted is True
    assert result.url == "https://github.com/o/r/pull/42"
    assert result.head_sha == "abc123"
    assert "adopted existing PR without metadata update" in result.summary
    mock_client.patch.assert_not_awaited()
    mock_client.post.assert_not_awaited()


def _reconcile_client(response_or_error) -> AsyncMock:
    mock_client = AsyncMock()
    if isinstance(response_or_error, Exception):
        mock_client.get = AsyncMock(side_effect=response_or_error)
    else:
        mock_client.get = AsyncMock(return_value=response_or_error)
    mock_client.patch = AsyncMock()
    mock_client.post = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return mock_client


def _pr(number: int, *, state: str = "open", sha: str = "a" * 40, **extra) -> dict:
    return {
        "number": number,
        "html_url": f"https://github.com/o/r/pull/{number}",
        "state": state,
        "draft": False,
        "merged_at": None,
        "head": {"ref": "feature", "sha": sha, "repo": {"full_name": "o/r"}},
        "base": {"ref": "main"},
        **extra,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("listing", "expected_state", "expected_number"),
    [
        ([], "absent", None),
        ([_pr(7)], "matched", 7),
        ([_pr(7, sha="b" * 40)], "mismatched", 7),
        ([_pr(7, draft=True)], "mismatched", 7),
        ([_pr(7, state="closed")], "closed", 7),
        (
            [_pr(7, state="closed", merged_at="2026-09-30T00:00:00Z")],
            "merged",
            7,
        ),
        (
            [
                _pr(8, state="closed"),
                _pr(9, state="closed", merged_at="2026-09-30T00:00:00Z"),
            ],
            "merged",
            9,
        ),
        # A same-named head from another repository is not this operation.
        (
            [
                {
                    **_pr(7),
                    "head": {
                        "ref": "feature",
                        "sha": "a" * 40,
                        "repo": {"full_name": "fork/r"},
                    },
                }
            ],
            "absent",
            None,
        ),
    ],
)
async def test_reconcile_pull_request_is_read_only_and_sees_closed_results(
    listing, expected_state, expected_number
):
    mock_client = _reconcile_client(_mock_get_response(200, listing))
    # "absent" additionally reads the head branch, which holds the candidate.
    mock_client.get = AsyncMock(
        side_effect=[_mock_get_response(200, listing), _branch_ref("a" * 40)]
    )

    with patch(
        "moonmind.workflows.adapters.github_service.httpx.AsyncClient",
        return_value=mock_client,
    ):
        result = await GitHubService().reconcile_pull_request(
            repo="o/r",
            head="feature",
            base="main",
            expected_head_sha="a" * 40,
            draft=False,
            github_token="admitted-token",
        )

    assert result.state == expected_state
    assert result.number == expected_number
    params = mock_client.get.await_args_list[0].kwargs["params"]
    assert params["state"] == "all"
    assert params["head"] == "o:feature"
    assert params["base"] == "main"
    for call in mock_client.get.await_args_list:
        assert call.kwargs["headers"]["Authorization"].endswith("admitted-token")
    mock_client.patch.assert_not_awaited()
    mock_client.post.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("repo", "head", "full_name"),
    [
        ("owner/repo", "feature", "Owner/Repo"),
        ("owner/repo", "Fork-Owner:feature", "fork-owner/repo"),
    ],
)
async def test_reconcile_pull_request_compares_repository_identity_case_insensitively(
    repo, head, full_name
):
    listing = [
        {
            **_pr(7),
            "head": {
                "ref": "feature",
                "sha": "a" * 40,
                "repo": {"full_name": full_name},
            },
        }
    ]
    mock_client = _reconcile_client(_mock_get_response(200, listing))

    with patch(
        "moonmind.workflows.adapters.github_service.httpx.AsyncClient",
        return_value=mock_client,
    ):
        result = await GitHubService().reconcile_pull_request(
            repo=repo,
            head=head,
            base="main",
            expected_head_sha="a" * 40,
            draft=False,
            github_token="admitted-token",
        )

    assert result.state == "matched"
    assert result.number == 7
    mock_client.post.assert_not_awaited()


def _branch_ref(sha: str) -> httpx.Response:
    return _mock_get_response(
        200, {"ref": "refs/heads/feature", "object": {"sha": sha, "type": "commit"}}
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("head", "branch_response", "expected_state", "expected_retryable", "ref_url"),
    [
        (
            "feature",
            _branch_ref("a" * 40),
            "absent",
            False,
            "https://api.github.com/repos/o/r/git/ref/heads/feature",
        ),
        # Another actor replaced the head after the push: creating now would
        # open a pull request for that actor's commit.
        (
            "feature",
            _branch_ref("b" * 40),
            "mismatched",
            False,
            "https://api.github.com/repos/o/r/git/ref/heads/feature",
        ),
        (
            "feature",
            _mock_get_response(404, {"message": "Not Found"}),
            "unavailable",
            True,
            "https://api.github.com/repos/o/r/git/ref/heads/feature",
        ),
        (
            "feature",
            httpx.ConnectError("unreachable"),
            "unavailable",
            True,
            "https://api.github.com/repos/o/r/git/ref/heads/feature",
        ),
        (
            "fork:feature",
            _branch_ref("a" * 40),
            "absent",
            False,
            "https://api.github.com/repos/fork/r/git/ref/heads/feature",
        ),
    ],
)
async def test_reconcile_pull_request_is_absent_only_while_the_head_holds_the_candidate(
    head, branch_response, expected_state, expected_retryable, ref_url
):
    mock_client = _reconcile_client(_mock_get_response(200, []))
    mock_client.get = AsyncMock(
        side_effect=[_mock_get_response(200, []), branch_response]
    )

    with patch(
        "moonmind.workflows.adapters.github_service.httpx.AsyncClient",
        return_value=mock_client,
    ):
        result = await GitHubService().reconcile_pull_request(
            repo="o/r",
            head=head,
            base="main",
            expected_head_sha="a" * 40,
            draft=False,
            github_token="admitted-token",
        )

    assert result.state == expected_state
    assert result.retryable is expected_retryable
    assert mock_client.get.await_args_list[1].args[0] == ref_url
    mock_client.patch.assert_not_awaited()
    mock_client.post.assert_not_awaited()


@pytest.mark.asyncio
async def test_reconcile_pull_request_failed_lookup_is_unavailable_not_absent():
    response = _mock_get_response(502, {"message": "bad gateway"})
    mock_client = _reconcile_client(
        httpx.HTTPStatusError("502", request=response.request, response=response)
    )

    with patch(
        "moonmind.workflows.adapters.github_service.httpx.AsyncClient",
        return_value=mock_client,
    ):
        result = await GitHubService().reconcile_pull_request(
            repo="o/r",
            head="feature",
            base="main",
            expected_head_sha="a" * 40,
            draft=False,
            github_token="admitted-token",
        )

    assert result.state == "unavailable"
    assert result.retryable is True
    mock_client.post.assert_not_awaited()


@pytest.mark.asyncio
async def test_reconcile_pull_request_requires_admitted_token(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ambient-token")
    mock_client = _reconcile_client(_mock_get_response(200, []))

    with patch(
        "moonmind.workflows.adapters.github_service.httpx.AsyncClient",
        return_value=mock_client,
    ):
        result = await GitHubService().reconcile_pull_request(
            repo="o/r",
            head="feature",
            base="main",
            expected_head_sha="a" * 40,
            draft=False,
            github_token="",
        )

    assert result.state == "unavailable"
    assert result.retryable is False
    mock_client.get.assert_not_awaited()


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
    assert checklist["Contents"]["status"] == "verified_read_access"
    assert checklist["Pull requests"]["level"] == "write"
    assert checklist["Pull requests"]["status"] == "verified_read_access"
    assert checklist["Checks"]["status"] == "not_checked"
    assert any("resource owner" in item for item in result["limitations"])
    assert any("GitHub App" in item for item in result["limitations"])


@pytest.mark.asyncio
async def test_probe_github_token_uses_indexing_mode_checks(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "github-token-fixture")

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(
        side_effect=[
            _mock_get_response(200, {"full_name": "owner/repo"}),
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
            _mock_get_response(200, {"full_name": "owner/repo"}),
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


@pytest.mark.asyncio
async def test_probe_token_uses_remote_default_branch_and_reports_untested_write(
    monkeypatch,
):
    """MoonLadderStudios/MoonMind#4019: a read is not a verified write."""

    monkeypatch.setenv("GITHUB_TOKEN", "github-token-fixture")
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(
        side_effect=[
            _mock_get_response(
                200, {"full_name": "owner/repo", "default_branch": "trunk"}
            ),
            _mock_get_response(200, {"name": "trunk"}),
            _mock_get_response(200, []),
        ]
    )
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch(
        "moonmind.workflows.adapters.github_service.httpx.AsyncClient",
        return_value=mock_client,
    ):
        result = await GitHubService().probe_token(repo="owner/repo", mode="publish")

    assert [call.args[0] for call in mock_client.get.call_args_list][1] == (
        "https://api.github.com/repos/owner/repo/branches/trunk"
    )
    assert result["remoteDefaultBranch"] == "trunk"
    assert result["observations"] == {"read": "verified", "write": "untested"}


@pytest.mark.asyncio
async def test_probe_token_transport_outage_is_unavailable_not_denied(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "github-token-fixture")
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(
        side_effect=httpx.ConnectError(
            "down", request=httpx.Request("GET", "https://api.github.com/x")
        )
    )
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch(
        "moonmind.workflows.adapters.github_service.httpx.AsyncClient",
        return_value=mock_client,
    ):
        result = await GitHubService().probe_token(repo="owner/repo", mode="publish")

    assert result["repositoryAccessible"] is None
    assert result["remoteDefaultBranch"] is None
    assert result["observations"]["read"] == "unavailable"
    # Branch-dependent checks are not guessed against "main" without a branch.
    urls = [call.args[0] for call in mock_client.get.call_args_list]
    assert not any("/branches/" in url for url in urls)
    assert result["defaultBranchAccessible"] is None
    assert all(item["status"] != "failed" for item in result["permissionChecklist"])
    assert result["diagnostics"][0]["retryable"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [403, 404])
async def test_probe_token_denied_read_is_reported_as_denied(monkeypatch, status):
    monkeypatch.setenv("GITHUB_TOKEN", "github-token-fixture")
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(
        return_value=_mock_get_response(status, {"message": "Resource not accessible"})
    )
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch(
        "moonmind.workflows.adapters.github_service.httpx.AsyncClient",
        return_value=mock_client,
    ):
        result = await GitHubService().probe_token(repo="owner/repo", mode="publish")

    assert result["repositoryAccessible"] is False
    assert result["observations"]["read"] == "denied"


@pytest.mark.asyncio
async def test_probe_token_uses_selected_credential_without_global_fallback(
    monkeypatch,
):
    from moonmind.auth.github_credentials import (
        GitHubCredentialSource,
        ResolvedGitHubCredential,
    )

    monkeypatch.setenv("GITHUB_TOKEN", "global-token-must-not-be-used")
    mock_client = AsyncMock()
    mock_client.get = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    unresolved = ResolvedGitHubCredential(
        source=GitHubCredentialSource.UNRESOLVABLE,
        sourceName="repository-connection:a",
        diagnostic="Repository connection credential could not be read",
        retryable=True,
    )

    with patch(
        "moonmind.workflows.adapters.github_service.httpx.AsyncClient",
        return_value=mock_client,
    ):
        result = await GitHubService().probe_token(
            repo="owner/repo", mode="publish", credential=unresolved
        )

    mock_client.get.assert_not_called()
    assert result["credentialSource"]["sourceName"] == "repository-connection:a"
    assert result["credentialSource"]["resolved"] is False
    assert result["observations"]["read"] == "unavailable"

    resolved = ResolvedGitHubCredential(
        token="connection-token",
        source=GitHubCredentialSource.SECRET_REF_ENV,
        sourceName="repository-connection:a",
    )
    mock_client.get = AsyncMock(
        return_value=_mock_get_response(200, {"default_branch": "main"})
    )
    with patch(
        "moonmind.workflows.adapters.github_service.httpx.AsyncClient",
        return_value=mock_client,
    ):
        await GitHubService().probe_token(
            repo="owner/repo", mode="indexing", credential=resolved
        )
    headers = mock_client.get.call_args_list[0].kwargs["headers"]
    assert "connection-token" in headers["Authorization"]
    assert "global-token-must-not-be-used" not in str(headers)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,headers,message",
    [
        (429, {}, "Too many requests"),
        (403, {"x-ratelimit-remaining": "0"}, "API rate limit exceeded"),
        (403, {"retry-after": "60"}, "Please wait before retrying"),
        (403, {}, "You have exceeded a secondary rate limit."),
    ],
)
async def test_probe_token_rate_limits_are_unavailable_not_denied(
    monkeypatch, status, headers, message
):
    monkeypatch.setenv("GITHUB_TOKEN", "github-token-fixture")
    mock_client = AsyncMock()
    mock_client.get.return_value = _mock_get_response_with_headers(
        status, {"message": message}, headers
    )
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = False
    with patch(
        "moonmind.workflows.adapters.github_service.httpx.AsyncClient",
        return_value=mock_client,
    ):
        result = await GitHubService().probe_token(
            repo="owner/repo", mode="publish", base_branch="main"
        )

    assert result["repositoryAccessible"] is None
    assert result["defaultBranchAccessible"] is None
    assert result["pullRequestAccessible"] is None
    assert result["observations"]["read"] == "unavailable"
    assert all(
        item["status"] == "not_checked" for item in result["permissionChecklist"]
    )
    assert all(diagnostic["retryable"] for diagnostic in result["diagnostics"])


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
@pytest.mark.parametrize("review_state", ["pending", "complete", "unavailable", "disabled"])
@pytest.mark.parametrize("known_failure", [True, False])
async def test_readiness_activity_preserves_required_review_for_retained_gate(
    monkeypatch, review_state, known_failure
):
    from moonmind.workflows.temporal import activity_runtime
    from moonmind.workflows.temporal.activity_runtime import (
        TemporalIntegrationActivities,
    )
    from moonmind.workflows.temporal.workflows import merge_automation as module
    from moonmind.workflows.temporal.workflows.merge_gate import classify_readiness

    monkeypatch.setattr(module.workflow, "patched", lambda _: True)
    monkeypatch.setattr(
        activity_runtime.temporal_activity, "info",
        lambda: SimpleNamespace(activity_id="readiness-341"),
    )
    # The gate reads with the default connection, unrecorded here, so the
    # deployment declaration supplies the fixture token (#4010).
    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.managed_api_key_resolve."
        "load_repository_connection_for_launch",
        AsyncMock(return_value=None),
    )

    monkeypatch.setenv("GITHUB_TOKEN", "github-token-fixture")
    requested_urls = []

    async def get(url, **_kwargs):
        requested_urls.append(url)
        if url.endswith("/pulls/341"):
            return _mock_get_response(
                200, {"state": "open", "head": {"sha": "abc123"}}
            )
        if "/commits/abc123/status" in url:
            return _mock_get_response(200, {"state": "pending", "statuses": []})
        if "/commits/abc123/check-runs" in url:
            return _mock_get_response(
                200,
                {
                    "check_runs": [
                        {
                            "name": "Build",
                            "status": "completed",
                            "conclusion": "failure" if known_failure else "success",
                        },
                        {"name": "CI Gate", "status": "queued", "conclusion": None},
                    ]
                },
            )
        if url.endswith("/reviews"):
            if review_state == "unavailable":
                return _mock_get_response(503, {})
            return _mock_get_response(
                200, [{"state": "APPROVED"}] if review_state == "complete" else []
            )
        if "/issues/341/reactions" in url:
            return _mock_get_response(200, [])
        raise AssertionError(url)

    client = AsyncMock()
    client.get = get
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    with patch(
        "moonmind.workflows.adapters.github_service.httpx.AsyncClient",
        return_value=client,
    ):
        result = await TemporalIntegrationActivities.merge_automation_evaluate_readiness(
            SimpleNamespace(),
            {
                "pullRequest": {"repo": "owner/repo", "number": 341, "headSha": "abc123"},
                "mergeAutomationConfig": {
                    "gate": {"github": {
                        "checks": "required",
                        "automatedReview": "disabled" if review_state == "disabled" else "required",
                    }},
                    "reviewLoop": {"enabled": False},
                },
            },
        )

    assert result["actionableCiFailuresVersion"] == "v1"
    assert result["readinessObservationId"] == "readiness-341"
    gate = module.MoonMindMergeAutomationWorkflow()
    evidence = classify_readiness(
        result,
        tracked_head_sha="abc123",
        actionable_ci_failures=gate._actionable_ci_failures_enabled(result),
    )
    assert result["checksComplete"] is False
    assert result["checksPassing"] is False
    assert any(url.endswith("/reviews") for url in requested_urls) is (
        known_failure and review_state != "disabled"
    )
    assert result["automatedReviewComplete"] is (
        {
            "pending": False,
            "complete": True,
            "unavailable": None,
            "disabled": None,
        }[review_state]
        if known_failure else None
    )
    assert evidence.ready is (known_failure and review_state in {"complete", "disabled"})
    if not known_failure:
        assert {blocker.kind for blocker in evidence.blockers} == {"checks_running"}
    elif review_state == "pending":
        assert {blocker.kind for blocker in evidence.blockers} == {"automated_review_pending"}
    elif review_state == "unavailable":
        assert {blocker.kind for blocker in evidence.blockers} == {"external_state_unavailable"}


@pytest.mark.asyncio
@pytest.mark.parametrize("jira_allowed", [False, None])
async def test_readiness_activity_capability_preserves_jira_barrier(
    monkeypatch, jira_allowed
):
    from moonmind.workflows.temporal import activity_runtime
    from moonmind.workflows.temporal.activity_runtime import (
        TemporalIntegrationActivities,
    )
    from moonmind.workflows.temporal.workflows import merge_automation as module
    from moonmind.workflows.temporal.workflows.merge_gate import classify_readiness

    github_result = PullRequestReadinessResult(
        headSha="abc123",
        pullRequestOpen=True,
        checksComplete=False,
        checksPassing=False,
        automatedReviewComplete=True,
        blockers=[{"kind": "checks_failed"}, {"kind": "checks_running"}],
    )
    monkeypatch.setattr(
        GitHubService, "evaluate_pull_request_readiness",
        AsyncMock(return_value=github_result),
    )
    monkeypatch.setattr(module.workflow, "patched", lambda _: True)
    monkeypatch.setattr(
        activity_runtime.temporal_activity, "info",
        lambda: SimpleNamespace(activity_id="readiness-341"),
    )
    jira_blocker = {
        "kind": "jira_status_pending" if jira_allowed is False else "external_state_unavailable",
        "summary": "Jira status is pending or unavailable.",
        "source": "jira",
    }
    jira_reader = AsyncMock(return_value=(jira_allowed, jira_blocker))

    result = await TemporalIntegrationActivities.merge_automation_evaluate_readiness(
        SimpleNamespace(_merge_gate_jira_status_allowed=jira_reader),
        {
            "pullRequest": {"repo": "owner/repo", "number": 341, "headSha": "abc123"},
            "jiraIssueKey": "MM-341",
            "mergeAutomationConfig": {"gate": {"jira": {"status": "required"}}},
        },
    )

    jira_reader.assert_awaited_once_with("MM-341")
    assert result["actionableCiFailuresVersion"] == "v1"
    assert result["readinessObservationId"] == "readiness-341"
    assert result["jiraStatusAllowed"] is jira_allowed
    evidence = classify_readiness(
        result,
        tracked_head_sha="abc123",
        actionable_ci_failures=module.MoonMindMergeAutomationWorkflow()._actionable_ci_failures_enabled(result),
    )
    assert evidence.ready is False
    assert jira_blocker["kind"] in {blocker.kind for blocker in evidence.blockers}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "protected,status_state,expected_ready",
    [
        (False, "pending", True),
        (False, "failure", True),
        (True, "pending", False),
        (True, "failure", False),
        (None, "pending", False),
    ],
)
async def test_durable_readiness_uses_branch_policy_for_current_head_statuses(
    monkeypatch,
    protected,
    status_state,
    expected_ready,
):
    monkeypatch.setenv("GITHUB_TOKEN", "github-token-fixture")

    async def get(url, **kwargs):
        if url.endswith("/pulls/341"):
            return _mock_get_response(
                200,
                {"state": "open", "head": {"sha": "current"}, "base": {"ref": "main"}},
            )
        if "/branches/main/protection" in url:
            return _mock_get_response(
                200,
                {
                    "required_status_checks": {
                        "contexts": [],
                        "checks": [{"context": "GitBook", "app_id": 123}],
                    }
                },
            )
        if "/rules/branches/main" in url:
            return _mock_get_response(200, [])
        if "/branches/main" in url:
            return (
                _mock_get_response(200, {"protected": protected})
                if protected is not None
                else _mock_get_response(403, {})
            )
        if "/commits/current/status" in url:
            return _mock_get_response(
                200,
                {
                    "state": status_state,
                    "statuses": [{"context": "GitBook", "state": status_state}],
                },
            )
        if "/commits/current/check-runs" in url:
            return _mock_get_response(
                200,
                {
                    "check_runs": [
                        {
                            "name": "Unreal",
                            "status": "completed",
                            "conclusion": "success",
                        }
                    ]
                },
            )
        raise AssertionError(url)

    client = AsyncMock()
    client.get = get
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    with patch(
        "moonmind.workflows.adapters.github_service.httpx.AsyncClient",
        return_value=client,
    ):
        result = await GitHubService().evaluate_pull_request_readiness(
            repo="owner/repo",
            pr_number=341,
            head_sha="prior",
            policy={"checks": "required", "automatedReview": "disabled"},
        )
    assert result.head_sha == "current"
    assert result.ready is expected_ready
    assert result.checks_passing is expected_ready


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
@pytest.mark.parametrize("mergeable_state", ["dirty", "clean"])
@pytest.mark.parametrize(
    "checks_complete,review_complete,unavailable,expected_ready",
    [
        (False, True, False, False),
        (True, False, False, False),
        (None, True, True, False),
        (True, True, False, True),
    ],
)
async def test_merge_conflicts_preserve_required_readiness_gates(
    monkeypatch,
    mergeable_state,
    checks_complete,
    review_complete,
    unavailable,
    expected_ready,
):
    from moonmind.workflows.temporal.workflows.merge_gate import classify_readiness

    monkeypatch.setenv("GITHUB_TOKEN", "github-token-fixture")
    mock_client = AsyncMock()
    mock_client.get.return_value = _mock_get_response(
        200,
        {
            "state": "open",
            "merged": False,
            "mergeable": False,
            "mergeable_state": mergeable_state,
            "head": {"sha": "abc123"},
        },
    )
    mock_client.__aenter__.return_value = mock_client
    service = GitHubService()
    service._evaluate_github_checks = AsyncMock(
        return_value={
            "complete": checks_complete,
            "passing": checks_complete,
            "blockers": (
                [
                    {
                        "kind": "external_state_unavailable",
                        "summary": "Checks unavailable.",
                        "retryable": True,
                        "source": "github",
                    }
                ]
                if unavailable
                else []
            ),
        }
    )
    service._evaluate_automated_review = AsyncMock(
        return_value={"complete": review_complete, "blockers": []}
    )
    with patch(
        "moonmind.workflows.adapters.github_service.httpx.AsyncClient",
        return_value=mock_client,
    ):
        result = await service.evaluate_pull_request_readiness(
            repo="owner/repo",
            pr_number=341,
            head_sha="abc123",
            policy={"checks": "required", "automatedReview": "required"},
        )
    service._evaluate_github_checks.assert_awaited_once()
    assert result.checks_complete is checks_complete
    if not unavailable:
        service._evaluate_automated_review.assert_awaited_once()
        assert result.automated_review_complete is review_complete
    assert any(blocker["kind"] == "merge_conflict" for blocker in result.blockers)
    evidence = classify_readiness(
        result.model_dump(by_alias=True), tracked_head_sha="abc123"
    )
    assert evidence.ready is expected_ready


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


# ---------------------------------------------------------------------------
# Admitted repository readers (MoonLadderStudios/MoonMind#4010)
# ---------------------------------------------------------------------------

_PR_URL = "https://github.com/acme/repo/pull/7"
_PAT_B_REF = "db://team-b-pat"


def _pat_connection_b(**overrides):
    from moonmind.workflows.executions.repository_contract import RepositoryConnection

    payload = {
        "schemaVersion": "moonmind.repository-connection.v1",
        "id": "repository-connection:pat-b",
        "provider": "git",
        "displayName": "Selected PAT B",
        "endpointRef": "https://github.com",
        "allowedOperations": ["read"],
        "clientPolicy": {
            "pinnedVersion": "2.46.0",
            "toolBundleRef": "tool-bundle:git-2.46",
            "executableSha256": "sha256:git",
        },
        "credential": {
            "source": "secret_ref",
            "credentialRef": {"provider": "db", "key": "team-b-pat"},
        },
        "hostingService": "github",
    }
    payload.update(overrides)
    return RepositoryConnection.model_validate(payload)


def _reader_transport(monkeypatch):
    """Record every provider request and answer the reader endpoints."""

    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = request.url.path
        if path == "/repos/acme/repo/pulls/7":
            return httpx.Response(
                200,
                json={
                    "number": 7,
                    "base": {"ref": "main", "repo": {"full_name": "acme/repo"}},
                    "head": {"ref": "feature", "sha": "a" * 40, "repo": {"full_name": "acme/repo"}},
                },
            )
        if path == "/repos/acme/repo":
            return httpx.Response(200, json={"default_branch": "main"})
        if path == "/repos/acme/repo/commits/refs%2Fheads%2Fmain" or path.endswith(
            "/commits/refs/heads/main"
        ):
            return httpx.Response(
                200, json={"sha": "b" * 40, "commit": {"tree": {"sha": "c" * 40}}}
            )
        raise AssertionError(f"Unexpected provider request: {request.method} {path}")

    original_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original_client(
            transport=httpx.MockTransport(handle), **kwargs
        ),
    )
    return requests


def _select_admitted(monkeypatch, *, access=("", False), connection=None, error=None):
    """Answer the canonical-record and connection-store reads at their seam."""

    from moonmind.workflows.temporal.runtime import managed_api_key_resolve as resolve

    seen: dict[str, object] = {}

    async def load_access(workflow_id):
        seen["workflow_id"] = workflow_id
        return access

    async def load_connection(connection_ref, *, repository=None):
        seen["connection_ref"] = connection_ref
        seen["repository"] = repository
        if error is not None:
            raise error
        return connection

    monkeypatch.setattr(resolve, "load_admitted_repository_access", load_access)
    monkeypatch.setattr(resolve, "load_repository_connection_for_launch", load_connection)
    return seen


def _secrets(monkeypatch, values: dict[str, str]):
    from moonmind.auth import github_credentials
    from moonmind.auth.secret_refs import SecretMissingError

    reads: list[str] = []

    async def resolve(ref: str) -> str:
        reads.append(ref)
        if ref not in values:
            raise SecretMissingError(f"{ref} is not set")
        return values[ref]

    monkeypatch.setattr(github_credentials, "_resolve_secret_ref", resolve)
    return reads


@pytest.mark.asyncio
@pytest.mark.parametrize("reader", ["pull_request", "repository_target"])
async def test_admitted_reader_uses_selected_pat_over_ambient_token(monkeypatch, reader):
    """Selected PAT B is the request credential even with ambient A present."""

    monkeypatch.setenv("GITHUB_TOKEN", "ambient-token-a")
    requests = _reader_transport(monkeypatch)
    seen = _select_admitted(
        monkeypatch,
        access=("repository-connection:pat-b", False),
        connection=_pat_connection_b(),
    )
    _secrets(monkeypatch, {_PAT_B_REF: "selected-token-b"})

    service = GitHubService()
    if reader == "pull_request":
        result = await service.read_pull_request(
            "acme/repo", _PR_URL, admitted_workflow_id="mm:run-b"
        )
        assert result["number"] == 7
    else:
        result = await service.read_repository_target(
            "acme/repo", admitted_workflow_id="mm:run-b"
        )
        assert result["ref"] == "refs/heads/main"

    assert seen == {
        "workflow_id": "mm:run-b",
        "connection_ref": "repository-connection:pat-b",
        "repository": "acme/repo",
    }
    assert requests
    assert {r.headers["Authorization"] for r in requests} == {"Bearer selected-token-b"}


@pytest.mark.asyncio
@pytest.mark.parametrize("reader", ["repository_target", "pull_request"])
async def test_supplied_pat_connection_never_reads_ambient_token(monkeypatch, reader):
    """A per-call PAT connection uses only its own SecretRef."""

    monkeypatch.setenv("GITHUB_TOKEN", "ambient-token-a")
    requests = _reader_transport(monkeypatch)
    _secrets(monkeypatch, {_PAT_B_REF: "selected-token-b"})

    if reader == "pull_request":
        await GitHubService().read_pull_request(
            "acme/repo", _PR_URL, connection=_pat_connection_b()
        )
    else:
        await GitHubService().read_repository_target(
            "acme/repo", "refs/heads/main", connection=_pat_connection_b()
        )

    assert requests
    assert {r.headers["Authorization"] for r in requests} == {"Bearer selected-token-b"}


@pytest.mark.asyncio
async def test_missing_selected_pat_fails_without_ambient_substitution(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ambient-token-a")
    requests = _reader_transport(monkeypatch)
    _select_admitted(
        monkeypatch,
        access=("repository-connection:pat-b", False),
        connection=_pat_connection_b(),
    )
    reads = _secrets(monkeypatch, {})

    with pytest.raises(ValueError, match="does not try another GitHub credential"):
        await GitHubService().read_pull_request(
            "acme/repo", _PR_URL, admitted_workflow_id="mm:run-b"
        )

    assert reads == [_PAT_B_REF]
    assert requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["disabled", "deleted", "unassigned"])
async def test_revoked_or_changed_selection_fails_without_substitution(monkeypatch, state):
    from moonmind.workflows.executions.repository_contract import (
        REPOSITORY_DENIED,
        RepositoryContractError,
        RepositoryRouteError,
    )

    monkeypatch.setenv("GITHUB_TOKEN", "ambient-token-a")
    requests = _reader_transport(monkeypatch)
    _select_admitted(
        monkeypatch,
        access=("repository-connection:pat-b", False),
        error=RepositoryRouteError(REPOSITORY_DENIED, f"connection is {state}"),
    )
    reads = _secrets(monkeypatch, {_PAT_B_REF: "selected-token-b"})

    with pytest.raises(RepositoryContractError, match="no other connection is substituted"):
        await GitHubService().read_repository_target(
            "acme/repo", admitted_workflow_id="mm:run-b"
        )

    assert reads == []
    assert requests == []


@pytest.mark.asyncio
async def test_explicit_anonymous_read_acquires_no_credential(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ambient-token-a")
    requests = _reader_transport(monkeypatch)
    seen = _select_admitted(monkeypatch, access=("", True))
    reads = _secrets(monkeypatch, {})

    await GitHubService().read_pull_request(
        "acme/repo", _PR_URL, admitted_workflow_id="mm:anonymous"
    )

    assert "connection_ref" not in seen
    assert reads == []
    assert [r.headers.get("Authorization") for r in requests] == [None]


@pytest.mark.asyncio
async def test_reader_without_admitted_work_selects_default_connection(monkeypatch):
    """Omission is the documented default connection, never another one."""

    from moonmind.workflows.executions.repository_contract import (
        DEFAULT_GIT_CONNECTION_REF,
    )

    monkeypatch.setenv("GITHUB_TOKEN", "deployment-token")
    requests = _reader_transport(monkeypatch)
    seen = _select_admitted(monkeypatch, connection=None)

    await GitHubService().read_pull_request("acme/repo", _PR_URL)

    assert "workflow_id" not in seen
    assert seen["connection_ref"] == DEFAULT_GIT_CONNECTION_REF
    assert [r.headers["Authorization"] for r in requests] == ["Bearer deployment-token"]


@pytest.mark.asyncio
async def test_recorded_default_connection_wins_over_ambient_token(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ambient-token-a")
    requests = _reader_transport(monkeypatch)
    _select_admitted(
        monkeypatch,
        connection=_pat_connection_b(id="repository-connection:git-default"),
    )
    _secrets(monkeypatch, {_PAT_B_REF: "recorded-default-token"})

    await GitHubService().read_repository_target(
        "acme/repo", "refs/heads/main", admitted_workflow_id="mm:default"
    )

    assert [r.headers["Authorization"] for r in requests] == [
        "Bearer recorded-default-token"
    ]


@pytest.mark.asyncio
async def test_untrusted_connection_endpoint_reads_nothing(monkeypatch):
    from moonmind.auth.bound_acquisition import BoundAccessError

    requests = _reader_transport(monkeypatch)
    reads = _secrets(monkeypatch, {_PAT_B_REF: "selected-token-b"})

    with pytest.raises(BoundAccessError, match="not an allowlisted API host"):
        await GitHubService().read_pull_request(
            "acme/repo",
            _PR_URL,
            connection=_pat_connection_b(endpointRef="https://git.example.test"),
        )

    assert reads == []
    assert requests == []


@pytest.mark.parametrize(
    ("parameters", "expected"),
    [
        ({"repository": "acme/repo"}, ("", False)),
        (
            {"repository": {"provider": "git", "connectionRef": "repository-connection:b",
                            "repository": {"name": "acme/repo"}}},
            ("repository-connection:b", False),
        ),
        (
            {"workspaceSpec": {"repositoryTarget": {"provider": "git",
                                                    "connectionRef": "repository-connection:c"}}},
            ("repository-connection:c", False),
        ),
        ({"workspaceSpec": {"connectionRef": "repository-connection:legacy"}},
         ("repository-connection:legacy", False)),
        (
            {"workflow": {"workspace": {"workspaceSource": {"accessMode": "anonymous"}}},
             "repository": "acme/repo"},
            ("", True),
        ),
    ],
)
def test_authored_repository_access_reads_recorded_parameters(parameters, expected):
    from moonmind.workflows.executions.repository_contract import (
        authored_repository_access,
    )

    assert authored_repository_access(parameters) == expected


# ---------------------------------------------------------------------------
# Admitted pull-request writers (MoonLadderStudios/MoonMind#4010)
# ---------------------------------------------------------------------------


def _writer_transport(monkeypatch):
    """Record every provider request; answer merges and base updates."""

    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "PUT" and request.url.path.endswith("/pulls/7/merge"):
            return httpx.Response(200, json={"merged": True, "sha": "c" * 40})
        if request.method == "PATCH" and request.url.path.endswith("/pulls/7"):
            return httpx.Response(200, json={})
        return httpx.Response(404, json={"message": "Not Found"})

    original_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original_client(
            transport=httpx.MockTransport(handle), **kwargs
        ),
    )
    return requests


async def _call_writer(service: GitHubService, writer: str, **authority):
    if writer == "merge":
        result = await service.merge_pull_request(
            pr_url=_PR_URL, expected_head_sha="a" * 40, **authority
        )
        return result.merged, result.summary
    if writer == "update_base":
        return await service.update_pull_request_base(
            pr_url=_PR_URL, new_base="main", **authority
        )
    if writer == "readiness":
        result = await service.evaluate_pull_request_readiness(
            repo="acme/repo", pr_number=7, head_sha="a" * 40, **authority
        )
        return result.ready, " ".join(str(b.get("summary") or "") for b in result.blockers)
    if writer == "review_request":
        result = await service.request_automated_review(
            repo="acme/repo",
            pr_number=7,
            expected_head_sha="a" * 40,
            provider="codex",
            attempt_started_at="2026-10-07T00:00:00Z",
            **authority,
        )
        return result.status == "requested", result.summary
    result = await service.resolve_pull_request_selector(
        repo="acme/repo", selector="feature", **authority
    )
    return result.resolved, result.summary


_WRITERS = ["merge", "update_base", "readiness", "review_request", "selector"]


@pytest.mark.asyncio
@pytest.mark.parametrize("writer", _WRITERS)
async def test_admitted_writer_uses_selected_pat_over_ambient_token(monkeypatch, writer):
    """The work's writes use the same selected PAT B as its reads, never ambient A."""

    monkeypatch.setenv("GITHUB_TOKEN", "ambient-token-a")
    requests = _writer_transport(monkeypatch)
    seen = _select_admitted(
        monkeypatch,
        access=("repository-connection:pat-b", False),
        connection=_pat_connection_b(),
    )
    _secrets(monkeypatch, {_PAT_B_REF: "selected-token-b"})

    await _call_writer(GitHubService(), writer, admitted_workflow_id="mm:run-b")

    assert seen["workflow_id"] == "mm:run-b"
    assert seen["connection_ref"] == "repository-connection:pat-b"
    assert requests
    assert {r.headers["Authorization"] for r in requests} == {"Bearer selected-token-b"}


@pytest.mark.asyncio
@pytest.mark.parametrize("writer", _WRITERS)
async def test_missing_selected_pat_writer_sends_nothing(monkeypatch, writer):
    """A missing B is unavailable at the writer; ambient A is never substituted."""

    monkeypatch.setenv("GITHUB_TOKEN", "ambient-token-a")
    requests = _writer_transport(monkeypatch)
    _select_admitted(
        monkeypatch,
        access=("repository-connection:pat-b", False),
        connection=_pat_connection_b(),
    )
    reads = _secrets(monkeypatch, {})

    succeeded, summary = await _call_writer(
        GitHubService(), writer, admitted_workflow_id="mm:run-b"
    )

    assert succeeded is False
    assert "no other GitHub credential is used" in summary
    assert "ambient-token-a" not in summary
    assert reads == [_PAT_B_REF]
    assert requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["disabled", "deleted", "unassigned"])
async def test_revoked_selection_merge_writes_nothing(monkeypatch, state):
    from moonmind.workflows.executions.repository_contract import (
        REPOSITORY_DENIED,
        RepositoryRouteError,
    )

    monkeypatch.setenv("GITHUB_TOKEN", "ambient-token-a")
    requests = _writer_transport(monkeypatch)
    _select_admitted(
        monkeypatch,
        access=("repository-connection:pat-b", False),
        error=RepositoryRouteError(REPOSITORY_DENIED, f"connection is {state}"),
    )
    reads = _secrets(monkeypatch, {_PAT_B_REF: "selected-token-b"})

    merged, summary = await _call_writer(
        GitHubService(), "merge", admitted_workflow_id="mm:run-b"
    )

    assert merged is False
    assert "no other GitHub credential is used" in summary
    assert reads == []
    assert requests == []


@pytest.mark.asyncio
async def test_anonymous_run_cannot_merge(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ambient-token-a")
    requests = _writer_transport(monkeypatch)
    _select_admitted(monkeypatch, access=("", True))
    reads = _secrets(monkeypatch, {})

    merged, summary = await _call_writer(
        GitHubService(), "merge", admitted_workflow_id="mm:anonymous"
    )

    assert merged is False
    assert "anonymous run cannot merge_request" in summary
    assert reads == []
    assert requests == []


@pytest.mark.asyncio
async def test_admitted_writer_rejects_connection_for_another_host(monkeypatch):
    """A GitHub.com pull request is never written with another host's credential."""

    requests = _writer_transport(monkeypatch)
    _select_admitted(
        monkeypatch,
        access=("repository-connection:pat-b", False),
        connection=_pat_connection_b(endpointRef="https://ghe.example.test"),
    )
    _secrets(monkeypatch, {_PAT_B_REF: "selected-token-b"})
    from moonmind.config.settings import settings

    monkeypatch.setattr(settings.github, "github_trusted_api_hosts", "ghe.example.test")

    merged, summary = await _call_writer(
        GitHubService(), "merge", admitted_workflow_id="mm:run-b"
    )

    assert merged is False
    assert "does not serve https://api.github.com" in summary
    assert requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("writer", "operation"),
    [
        ("merge", "merge_request"),
        ("update_base", "merge_request"),
        ("readiness", "read"),
        ("review_request", "review_request"),
        ("selector", "read"),
    ],
)
async def test_admitted_app_writer_acquires_only_its_operation(
    monkeypatch, writer, operation
):
    """App connections issue through the bound acquirer for exactly the write."""

    app_connection = _pat_connection_b(
        id="repository-connection:app-b",
        credential={
            "source": "github_app",
            "appRef": "github-app:b",
            "installationRef": "installation:b",
        },
    )
    requests = _writer_transport(monkeypatch)
    _select_admitted(
        monkeypatch,
        access=("repository-connection:app-b", False),
        connection=app_connection,
    )
    reads = _secrets(monkeypatch, {})
    acquired: list[tuple[str, tuple[str, ...]]] = []

    async def bound_headers(self, connection, *, repository, operations=("read",), **_):
        acquired.append((connection.id, tuple(operations)))
        return {"Authorization": "Bearer app-installation-b"}

    monkeypatch.setattr(GitHubService, "bound_app_headers_for_connection", bound_headers)
    monkeypatch.setenv("GITHUB_TOKEN", "ambient-token-a")

    await _call_writer(GitHubService(), writer, admitted_workflow_id="mm:run-b")

    assert acquired == [("repository-connection:app-b", (operation,))]
    assert reads == []
    assert {r.headers["Authorization"] for r in requests} == {
        "Bearer app-installation-b"
    }


@pytest.mark.asyncio
async def test_writer_without_admitted_authority_keeps_explicit_token(monkeypatch):
    """Callers that supply their own admitted token keep it (review-only, publication)."""

    monkeypatch.setenv("GITHUB_TOKEN", "ambient-token-a")
    requests = _writer_transport(monkeypatch)
    seen = _select_admitted(monkeypatch)

    await _call_writer(GitHubService(), "merge", github_token="explicit-token")

    assert seen == {}
    assert [r.headers["Authorization"] for r in requests] == ["Bearer explicit-token"]
