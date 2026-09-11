"""Author-scope acceptance coverage for MoonLadderStudios/MoonMind#4257.

Default self-authored search with an explicit all-authors opt-in, exercised
through the production selector, trusted loader, preset seed, and catalog
boundary with provider/network behavior controlled at the HTTP edge.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
import yaml

from moonmind.workflows.adapters.github_service import GitHubService
from moonmind.workflows.temporal import github_issue_search as search_module
from moonmind.workflows.temporal.github_issue_search import resolve_issue
from moonmind.workflows.temporal.story_output_tools import (
    _parse_include_all_authors,
    load_github_issue_preset_brief,
)

REPOSITORY = "o/r"
PRESET_PATH = Path("api_service/data/presets/github-issue-search-and-implement.yaml")
SELF = {"id": 111, "login": "search-user"}
OTHER = {"id": 222, "login": "other-user"}


def _candidate(number: int, user: dict[str, Any] | None, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "number": number,
        "title": f"issue {number}",
        "body": "body",
        "html_url": f"https://github.com/{REPOSITORY}/issues/{number}",
        "state": "open",
        "labels": [{"name": "bug"}],
    }
    if user is not None:
        payload["user"] = dict(user)
    payload.update(overrides)
    return payload


class _Response:
    def __init__(self, payload: Any, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.headers: dict[str, str] = {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("GET", "https://api.github.com/")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("error", request=request, response=response)

    def json(self) -> Any:
        return self._payload


class _Client:
    """Fake AsyncClient routing search, list, detail, and /user by test plan."""

    plan: dict[str, Any] = {}
    requests: list[dict[str, Any]] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def __aenter__(self) -> "_Client":
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    async def get(self, url: str, **kwargs: Any) -> _Response:
        params = dict((kwargs.get("params") or {}))
        type(self).requests.append({"url": url, "params": params})
        plan = type(self).plan
        if url == "https://api.github.com/user":
            status = int(plan.get("user_status", 200))
            if status >= 400:
                return _Response({}, status_code=status)
            return _Response(dict(plan.get("authenticated_user", SELF)))
        if url == "https://api.github.com/search/issues":
            return _Response(dict(plan.get("search_payload", {})))
        if url == f"https://api.github.com/repos/{REPOSITORY}/issues":
            return _Response(list(plan.get("list_payload", [])))
        if url.startswith(f"https://api.github.com/repos/{REPOSITORY}/issues/"):
            return _Response(dict(plan.get("detail_payload", {})))
        return _Response({}, status_code=404)


class _Service:
    async def resolve_github_token(self, **kwargs: Any) -> tuple[str, None]:
        return "test-token", None

    def _github_headers(self, token: str) -> dict[str, str]:
        return GitHubService._github_headers(token)

    async def get_authenticated_user(self, *, token: str) -> tuple[Any, Any]:
        return await GitHubService().get_authenticated_user(token=token)


async def _no_blockers(issue: Any) -> list[dict[str, Any]]:
    return []


@pytest.fixture
def http_plan(monkeypatch: pytest.MonkeyPatch) -> type[_Client]:
    monkeypatch.setattr(search_module.httpx, "AsyncClient", _Client)
    _Client.plan = {}
    _Client.requests = []
    return _Client


def test_preset_seed_declares_unchecked_all_authors_opt_in() -> None:
    seed = yaml.safe_load(PRESET_PATH.read_text(encoding="utf-8"))
    props = seed["annotations"]["inputSchema"]["properties"]
    assert props["include_all_authors"]["type"] == "boolean"
    assert props["include_all_authors"]["title"] == "Include issues created by other users"
    assert (
        props["include_all_authors"]["description"]
        == "By default, only issues created by the GitHub account used for this search are eligible."
    )
    assert props["include_all_authors"]["default"] is False
    assert seed["annotations"]["defaults"]["include_all_authors"] is False
    names = [item["name"] for item in seed["inputs"]]
    assert names.index("include_all_authors") == names.index("issue_search") + 1
    include_input = next(item for item in seed["inputs"] if item["name"] == "include_all_authors")
    assert include_input["type"] == "boolean"
    assert include_input["default"] is False
    assert seed["annotations"]["uiSchema"]["include_all_authors"] == {"widget": "checkbox"}
    binding = seed["steps"][0]["tool"]["inputs"]
    assert binding["includeAllAuthors"] == "{{ inputs.include_all_authors }}"
    assert "created by the authenticated" in seed["description"]
    assert "Include issues created by other users" in seed["description"]


@pytest.mark.asyncio
async def test_nonblank_self_only_skips_other_author(http_plan: type[_Client]) -> None:
    http_plan.plan = {
        "authenticated_user": {**SELF, "type": "user"},
        "search_payload": {
            "incomplete_results": False,
            "items": [_candidate(1, OTHER), _candidate(2, SELF)],
        },
    }
    number, evidence = await resolve_issue(
        repository=REPOSITORY,
        query="dashboard",
        github_service=_Service(),  # type: ignore[arg-type]
        blockers_from_issue=_no_blockers,
    )
    assert number == 2
    scoped = http_plan.requests[1]["params"]["q"]
    assert "author:search-user" in scoped
    assert scoped.startswith("(dashboard)")
    search_evidence = evidence["searchEvidence"]
    assert search_evidence["authorScope"] == "authenticated_user"
    assert search_evidence["authenticatedUser"] == SELF
    assert search_evidence["selectedIssueAuthor"] == SELF
    assert search_evidence["authorMismatchesSkipped"] == 1


@pytest.mark.asyncio
async def test_blank_fallback_uses_creator_and_same_restriction(http_plan: type[_Client]) -> None:
    http_plan.plan = {
        "authenticated_user": {**SELF, "type": "user"},
        "list_payload": [_candidate(9, OTHER), _candidate(10, SELF)],
    }
    number, evidence = await resolve_issue(
        repository=REPOSITORY,
        query="   ",
        github_service=_Service(),  # type: ignore[arg-type]
        blockers_from_issue=_no_blockers,
    )
    assert number == 10
    assert http_plan.requests[1]["params"]["creator"] == "search-user"
    assert evidence["searchEvidence"]["fallbackScanning"] is True
    assert evidence["searchEvidence"]["authorMismatchesSkipped"] == 1


@pytest.mark.asyncio
async def test_all_author_opt_in_selects_other_author_without_identity(
    http_plan: type[_Client],
) -> None:
    http_plan.plan = {
        "search_payload": {
            "incomplete_results": False,
            "items": [_candidate(3, OTHER)],
        },
    }
    number, evidence = await resolve_issue(
        repository=REPOSITORY,
        query="dashboard",
        github_service=_Service(),  # type: ignore[arg-type]
        blockers_from_issue=_no_blockers,
        include_all_authors=True,
    )
    assert number == 3
    assert [request["url"] for request in http_plan.requests] != [
        "https://api.github.com/user"
    ]
    assert "author:search-user" not in http_plan.requests[0]["params"]["q"]
    assert evidence["searchEvidence"]["authorScope"] == "all"
    assert "authenticatedUser" not in evidence["searchEvidence"]
    assert evidence["searchEvidence"]["selectedIssueAuthor"] == OTHER


@pytest.mark.asyncio
async def test_identity_failure_is_fail_closed(http_plan: type[_Client]) -> None:
    http_plan.plan = {"user_status": 401}
    number, evidence = await resolve_issue(
        repository=REPOSITORY,
        query="dashboard",
        github_service=_Service(),  # type: ignore[arg-type]
        blockers_from_issue=_no_blockers,
    )
    assert number is None
    assert evidence["reasonCode"] == "identity_auth_failure"
    assert "Include issues created by other users" in evidence["error"]
    assert len([r for r in http_plan.requests if "/search/" in r["url"]]) == 0


@pytest.mark.asyncio
async def test_conflicting_author_filter_rejected(http_plan: type[_Client]) -> None:
    http_plan.plan = {"authenticated_user": {**SELF, "type": "user"}}
    number, evidence = await resolve_issue(
        repository=REPOSITORY,
        query="dashboard author:other-user",
        github_service=_Service(),  # type: ignore[arg-type]
        blockers_from_issue=_no_blockers,
    )
    assert number is None
    assert evidence["reasonCode"] == "conflicting_author_filter"
    assert evidence["field"] == "issueSearch"


@pytest.mark.asyncio
async def test_author_me_normalizes_without_duplicate(http_plan: type[_Client]) -> None:
    http_plan.plan = {
        "authenticated_user": {**SELF, "type": "user"},
        "search_payload": {"incomplete_results": False, "items": [_candidate(4, SELF)]},
    }
    number, _ = await resolve_issue(
        repository=REPOSITORY,
        query="dashboard author:@me",
        github_service=_Service(),  # type: ignore[arg-type]
        blockers_from_issue=_no_blockers,
    )
    assert number == 4
    scoped = http_plan.requests[1]["params"]["q"]
    assert scoped.count("author:") == 1
    assert "author:search-user" in scoped


@pytest.mark.asyncio
async def test_quoted_author_text_is_not_a_qualifier(http_plan: type[_Client]) -> None:
    http_plan.plan = {
        "authenticated_user": {**SELF, "type": "user"},
        "search_payload": {"incomplete_results": False, "items": [_candidate(5, SELF)]},
    }
    number, _ = await resolve_issue(
        repository=REPOSITORY,
        query='"author:other-user" crash',
        github_service=_Service(),  # type: ignore[arg-type]
        blockers_from_issue=_no_blockers,
    )
    assert number == 5
    assert "author:search-user" in http_plan.requests[1]["params"]["q"]


@pytest.mark.asyncio
async def test_missing_author_evidence_is_invalid(http_plan: type[_Client]) -> None:
    payload = _candidate(6, None)
    http_plan.plan = {
        "authenticated_user": {**SELF, "type": "user"},
        "search_payload": {"incomplete_results": False, "items": [payload]},
    }
    number, evidence = await resolve_issue(
        repository=REPOSITORY,
        query="dashboard",
        github_service=_Service(),  # type: ignore[arg-type]
        blockers_from_issue=_no_blockers,
    )
    assert number is None
    assert evidence["reasonCode"] == "invalid_author_evidence"


@pytest.mark.asyncio
async def test_login_match_with_different_id_does_not_qualify(
    http_plan: type[_Client],
) -> None:
    impostor = {"id": 999, "login": "search-user"}
    http_plan.plan = {
        "authenticated_user": {**SELF, "type": "user"},
        "search_payload": {"incomplete_results": False, "items": [_candidate(7, impostor)]},
    }
    number, evidence = await resolve_issue(
        repository=REPOSITORY,
        query="dashboard",
        github_service=_Service(),  # type: ignore[arg-type]
        blockers_from_issue=_no_blockers,
    )
    assert number is None
    assert evidence["reasonCode"] == "no_eligible_self_authored_issue"
    assert evidence["searchEvidence"]["authorMismatchesSkipped"] == 1


@pytest.mark.asyncio
async def test_stable_id_with_changed_login_is_same_account(
    http_plan: type[_Client],
) -> None:
    renamed = {"id": 111, "login": "renamed-user"}
    http_plan.plan = {
        "authenticated_user": {**SELF, "type": "user"},
        "search_payload": {"incomplete_results": False, "items": [_candidate(8, renamed)]},
    }
    number, evidence = await resolve_issue(
        repository=REPOSITORY,
        query="dashboard",
        github_service=_Service(),  # type: ignore[arg-type]
        blockers_from_issue=_no_blockers,
    )
    assert number == 8
    assert evidence["searchEvidence"]["selectedIssueAuthor"] == renamed


@pytest.mark.asyncio
async def test_no_self_authored_match_never_broadens(http_plan: type[_Client]) -> None:
    http_plan.plan = {
        "authenticated_user": {**SELF, "type": "user"},
        "search_payload": {"incomplete_results": False, "items": [_candidate(9, OTHER)]},
    }
    number, evidence = await resolve_issue(
        repository=REPOSITORY,
        query="dashboard",
        github_service=_Service(),  # type: ignore[arg-type]
        blockers_from_issue=_no_blockers,
    )
    assert number is None
    assert evidence["reasonCode"] == "no_eligible_self_authored_issue"
    assert "No other author's" in evidence["error"]


def test_include_all_authors_parsing_is_strict() -> None:
    assert _parse_include_all_authors({}) is False
    assert _parse_include_all_authors({"includeAllAuthors": False}) is False
    assert _parse_include_all_authors({"includeAllAuthors": True}) is True
    for malformed in ("false", "true", 1, 0, None, [], {}):
        result = _parse_include_all_authors({"includeAllAuthors": malformed})
        assert getattr(result, "status", None) == "FAILED"
        assert result.outputs["reasonCode"] == "invalid_author_scope_input"  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_loader_revalidates_fresh_detail_author(monkeypatch: pytest.MonkeyPatch) -> None:
    import moonmind.workflows.temporal.story_output_tools as tools

    selected = _candidate(11, SELF)
    requests: list[str] = []

    class _LoaderClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> "_LoaderClient":
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        async def get(self, url: str, **kwargs: Any) -> _Response:
            requests.append(url)
            if url == "https://api.github.com/user":
                return _Response({**SELF, "type": "user"})
            if url == "https://api.github.com/search/issues":
                return _Response({"incomplete_results": False, "items": [selected]})
            if url == f"https://api.github.com/repos/{REPOSITORY}/issues/11":
                # Fresh detail shows another author's issue: must not brief.
                return _Response(_candidate(11, OTHER))
            if "/comments" in url:
                return _Response([])
            if url.endswith("/labels"):
                return _Response([])
            return _Response({}, status_code=404)

    monkeypatch.setattr(tools.httpx, "AsyncClient", _LoaderClient)
    monkeypatch.setattr(
        GitHubService, "resolve_github_token", AsyncMock(return_value=("t", None))
    )
    result = await load_github_issue_preset_brief(
        {"repository": REPOSITORY, "issueSearch": "dashboard"}, None
    )
    assert result.status == "FAILED"
    assert result.outputs["reasonCode"] == "author_mismatch"
    assert "trustedSource" not in result.outputs


@pytest.mark.asyncio
async def test_loader_rejects_stringified_boolean() -> None:
    result = await load_github_issue_preset_brief(
        {"repository": REPOSITORY, "issueSearch": "", "includeAllAuthors": "false"},
        None,
    )
    assert result.status == "FAILED"
    assert result.outputs["reasonCode"] == "invalid_author_scope_input"
