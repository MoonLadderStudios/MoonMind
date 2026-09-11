"""Self-authored default with all-authors opt-in (MoonLadderStudios/MoonMind#4257).

Selector-level (resolve_issue) and trusted-loader-level coverage for the
author-scope boundary: credential-bound identity, both discovery paths,
candidate and fresh-detail validation, strict scope parsing, frozen-plan
compatibility, and durable evidence.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from moonmind.workflows.adapters.github_service import GitHubService
from moonmind.workflows.temporal import github_issue_search as search_tools
from moonmind.workflows.temporal import story_output_tools as story_tools
from moonmind.workflows.temporal.github_issue_search import (
    candidate_author_id,
    parse_include_all_authors,
    plan_author_scoped_query,
    resolve_issue,
)

REPO = "o/r"
IDENTITY = {"id": 4257001, "login": "ScopeSearcher", "type": "User"}
OTHER_ID = 4242001
OTHER_LOGIN = "other-author"


def make_issue(
    number: int,
    author_id: int | None = IDENTITY["id"],
    author_login: str = IDENTITY["login"],
    labels: list[str] | None = None,
    body: str = "Scope acceptance body.",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "number": number,
        "state": "open",
        "title": f"Scope candidate {number}",
        "body": body,
        "html_url": f"https://github.com/{REPO}/issues/{number}",
        "labels": [{"name": name} for name in (labels or [])],
    }
    if author_id is not None:
        payload["user"] = {"id": author_id, "login": author_login}
    return payload


class _ScopeHttpClient:
    """Controllable provider boundary: identity, search, fallback, detail."""

    user_payload: dict[str, Any] = dict(IDENTITY)
    user_status: int = 200
    search_items: list[dict[str, Any]] = []
    repo_pages: list[list[dict[str, Any]]] = []
    detail: dict[str, Any] = {}
    requests: list[dict[str, Any]] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    def _record(self, method: str, url: str, **kwargs: Any) -> None:
        type(self).requests.append(
            {"method": method, "url": url, "params": dict(kwargs.get("params") or {})}
        )

    async def get(self, url: str, **kwargs: Any):
        self._record("GET", url, **kwargs)
        if url.rstrip("/").endswith("/user"):
            return _ScopeResponse(
                dict(type(self).user_payload), status=type(self).user_status
            )
        if "search/issues" in url:
            return _ScopeResponse(
                {"items": list(type(self).search_items), "incomplete_results": False}
            )
        if "/comments" in url:
            return _ScopeResponse([])
        path = httpx.URL(url).path
        if path == f"/repos/{REPO}/issues":
            page = int((kwargs.get("params") or {}).get("page", 1))
            pages = type(self).repo_pages
            items = list(pages[page - 1]) if 1 <= page <= len(pages) else []
            return _ScopeResponse(items)
        if path.startswith(f"/repos/{REPO}/issues/"):
            return _ScopeResponse(dict(type(self).detail))
        return _ScopeResponse(dict(type(self).detail))

    async def post(self, url: str, **kwargs: Any):
        self._record("POST", url, **kwargs)
        if url.rstrip("/").endswith("/labels"):
            import json

            return _ScopeResponse(json.loads(kwargs.get("content") or b"{}").get("labels", []))
        return _ScopeResponse({"id": 1})


class _ScopeResponse:
    def __init__(self, payload: Any, status: int = 200) -> None:
        self._payload = payload
        self.status_code = status
        self.headers: dict[str, str] = {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("GET", "https://api.github.com/user")
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=request,
                response=httpx.Response(self.status_code, request=request),
            )

    def json(self) -> Any:
        return self._payload


class _ScopeService:
    def __init__(self, labels: list[str] | None = None) -> None:
        self.labels = list(labels) if labels is not None else []
        self.operations: list[tuple[str, str]] = []

    async def resolve_github_token(self, *, repo: str | None = None, **kwargs: Any):
        return "scope-token", None

    def _github_headers(self, token: str) -> dict[str, str]:
        return GitHubService._github_headers(token)

    async def check_issue_label_readiness(self, *, repo: str, issue_number: int,
                                          required_labels: list[str], github_token: str | None = None):
        return {"ready": True, "reasonCode": "ready", "summary": "ready"}

    async def add_issue_labels(self, *, repo: str, issue_number: int,
                               labels: list[str], github_token: str | None = None):
        for label in labels:
            self.operations.append(("add", label))
            self.labels.append(label)
        # The loader re-reads through the HTTP boundary, so mirror the
        # write into the shared fake detail (as the real GitHub API would).
        detail = _ScopeHttpClient.detail
        if isinstance(detail, dict):
            current = detail.setdefault("labels", [])
            for label in labels:
                if {"name": label} not in current:
                    current.append({"name": label})
        return {"ok": True, "reasonCode": "added", "summary": "added"}

    async def remove_issue_label(self, *, repo: str, issue_number: int,
                                 label: str, github_token: str | None = None):
        self.operations.append(("remove", label))
        self.labels = [existing for existing in self.labels if existing != label]
        detail = _ScopeHttpClient.detail
        if isinstance(detail, dict):
            detail["labels"] = [
                entry for entry in detail.get("labels", [])
                if not (isinstance(entry, dict) and entry.get("name") == label)
            ]
        return {"ok": True, "reasonCode": "removed", "summary": "removed"}


@pytest.fixture
def scope_boundary(monkeypatch):
    _ScopeHttpClient.requests = []
    _ScopeHttpClient.user_payload = dict(IDENTITY)
    _ScopeHttpClient.user_status = 200
    _ScopeHttpClient.search_items = []
    _ScopeHttpClient.repo_pages = []
    _ScopeHttpClient.detail = {}
    monkeypatch.setattr(httpx, "AsyncClient", _ScopeHttpClient)
    monkeypatch.setattr(
        GitHubService, "resolve_github_token", AsyncMock(return_value=("scope-token", None))
    )
    return _ScopeHttpClient


async def _no_blockers(issue: dict[str, Any]) -> list[dict[str, Any]]:
    return []


def _search_requests() -> list[dict[str, Any]]:
    return [
        request
        for request in _ScopeHttpClient.requests
        if "search/issues" in request["url"]
    ]


def _user_requests() -> list[dict[str, Any]]:
    return [request for request in _ScopeHttpClient.requests if request["url"].rstrip("/").endswith("/user")]


# -- R3: nonblank search path -------------------------------------------------


@pytest.mark.asyncio
async def test_self_only_query_scopes_provider_and_selects_self_authored(scope_boundary):
    other = make_issue(11, author_id=OTHER_ID, author_login=OTHER_LOGIN)
    own = make_issue(12)
    scope_boundary.search_items = [other, own]

    number, evidence = await resolve_issue(
        repository=REPO,
        query="task",
        github_service=_ScopeService(),  # type: ignore[arg-type]
        blockers_from_issue=_no_blockers,
    )

    assert number == 12
    outgoing = _search_requests()
    assert len(outgoing) == 1
    assert outgoing[0]["params"]["q"] == f"task author:{IDENTITY['login']} repo:{REPO} is:issue is:open"
    search_evidence = evidence["searchEvidence"]
    assert search_evidence["authorScope"] == "authenticated_user"
    assert search_evidence["authenticatedUser"] == {"id": IDENTITY["id"], "login": IDENTITY["login"]}
    assert search_evidence["selectedIssueAuthor"]["id"] == IDENTITY["id"]
    assert search_evidence["authorMismatchesSkipped"] == 1


@pytest.mark.asyncio
async def test_self_only_never_broadens_when_only_other_authors_match(scope_boundary):
    scope_boundary.search_items = [
        make_issue(11, author_id=OTHER_ID, author_login=OTHER_LOGIN)
    ]

    number, evidence = await resolve_issue(
        repository=REPO,
        query="task",
        github_service=_ScopeService(),  # type: ignore[arg-type]
        blockers_from_issue=_no_blockers,
    )

    assert number is None
    assert evidence["reasonCode"] == "no_eligible_self_authored_issue"
    assert evidence["searchEvidence"]["authorMismatchesSkipped"] == 1


# -- R3: blank fallback path --------------------------------------------------


@pytest.mark.asyncio
async def test_blank_fallback_sends_creator_and_keeps_newest_self_authored(scope_boundary):
    newer_other = make_issue(21, author_id=OTHER_ID, author_login=OTHER_LOGIN)
    older_own = make_issue(20)
    scope_boundary.repo_pages = [[newer_other, older_own]]

    number, evidence = await resolve_issue(
        repository=REPO,
        query="   ",
        github_service=_ScopeService(),  # type: ignore[arg-type]
        blockers_from_issue=_no_blockers,
    )

    assert number == 20
    fallback = [r for r in _ScopeHttpClient.requests if r["url"].endswith("/issues")]
    assert fallback and fallback[0]["params"]["creator"] == IDENTITY["login"]
    assert fallback[0]["params"]["sort"] == "created"
    assert evidence["searchEvidence"]["fallbackScanning"] is True
    assert evidence["searchEvidence"]["authorMismatchesSkipped"] == 1


# -- R2/R3: identity authority ------------------------------------------------


@pytest.mark.asyncio
async def test_identity_failure_stops_before_discovery(scope_boundary):
    scope_boundary.user_status = 401
    scope_boundary.user_payload = {"message": "Bad credentials"}
    scope_boundary.search_items = [make_issue(11)]

    number, evidence = await resolve_issue(
        repository=REPO,
        query="task",
        github_service=_ScopeService(),  # type: ignore[arg-type]
        blockers_from_issue=_no_blockers,
    )

    assert number is None
    assert evidence["reasonCode"] == "authentication"
    assert "Include issues created by other users" in evidence["error"]
    assert _search_requests() == []
    assert _user_requests() != []


@pytest.mark.asyncio
async def test_installation_identity_cannot_select(scope_boundary):
    scope_boundary.user_payload = {"id": 1, "login": "octocat", "type": "Organization"}

    number, evidence = await resolve_issue(
        repository=REPO,
        query="task",
        github_service=_ScopeService(),  # type: ignore[arg-type]
        blockers_from_issue=_no_blockers,
    )

    assert number is None
    assert evidence["reasonCode"] == "identity_unavailable"
    assert _search_requests() == []


@pytest.mark.asyncio
async def test_same_login_with_different_id_does_not_qualify(scope_boundary):
    scope_boundary.search_items = [
        make_issue(11, author_id=OTHER_ID, author_login=IDENTITY["login"])
    ]

    number, evidence = await resolve_issue(
        repository=REPO,
        query="task",
        github_service=_ScopeService(),  # type: ignore[arg-type]
        blockers_from_issue=_no_blockers,
    )

    assert number is None
    assert evidence["searchEvidence"]["authorMismatchesSkipped"] == 1


@pytest.mark.asyncio
async def test_same_id_with_changed_login_is_same_account(scope_boundary):
    scope_boundary.search_items = [make_issue(11, author_login="renamed-login")]

    number, evidence = await resolve_issue(
        repository=REPO,
        query="task",
        github_service=_ScopeService(),  # type: ignore[arg-type]
        blockers_from_issue=_no_blockers,
    )

    assert number == 11
    assert evidence["searchEvidence"]["selectedIssueAuthor"]["id"] == IDENTITY["id"]


@pytest.mark.asyncio
async def test_missing_author_evidence_fails_closed(scope_boundary):
    candidate = make_issue(11)
    del candidate["user"]
    scope_boundary.search_items = [candidate]

    number, evidence = await resolve_issue(
        repository=REPO,
        query="task",
        github_service=_ScopeService(),  # type: ignore[arg-type]
        blockers_from_issue=_no_blockers,
    )

    assert number is None
    assert evidence["reasonCode"] == "invalid_author_evidence"


@pytest.mark.asyncio
async def test_malformed_author_id_fails_closed(scope_boundary):
    candidate = make_issue(11)
    candidate["user"] = {"id": "4257001", "login": IDENTITY["login"]}
    scope_boundary.search_items = [candidate]

    number, evidence = await resolve_issue(
        repository=REPO,
        query="task",
        github_service=_ScopeService(),  # type: ignore[arg-type]
        blockers_from_issue=_no_blockers,
    )

    assert number is None
    assert evidence["reasonCode"] == "invalid_author_evidence"


# -- R3/R5: all-author opt-in --------------------------------------------------


@pytest.mark.asyncio
async def test_all_author_mode_needs_no_identity_and_selects_other_author(scope_boundary):
    scope_boundary.user_status = 500
    scope_boundary.search_items = [
        make_issue(11, author_id=OTHER_ID, author_login=OTHER_LOGIN)
    ]

    number, evidence = await resolve_issue(
        repository=REPO,
        query=f"author:{OTHER_LOGIN}",
        github_service=_ScopeService(),  # type: ignore[arg-type]
        blockers_from_issue=_no_blockers,
        include_all_authors=True,
    )

    assert number == 11
    assert _user_requests() == []
    assert evidence["searchEvidence"]["authorScope"] == "all"
    assert "authenticatedUser" not in evidence["searchEvidence"]
    outgoing = _search_requests()
    assert outgoing[0]["params"]["q"].startswith(f"author:{OTHER_LOGIN} repo:{REPO}")


@pytest.mark.asyncio
async def test_non_boolean_scope_rejected(scope_boundary):
    with pytest.raises(ValueError, match="must be a boolean"):
        await resolve_issue(
            repository=REPO,
            query="task",
            github_service=_ScopeService(),  # type: ignore[arg-type]
            blockers_from_issue=_no_blockers,
            include_all_authors="true",  # type: ignore[arg-type]
        )


# -- R4: author qualifier interpretation --------------------------------------


def test_parse_scope_strict():
    assert parse_include_all_authors(None) is False
    assert parse_include_all_authors(True) is True
    assert parse_include_all_authors(False) is False
    for malformed in ("true", "false", 1, 0, [], {}, 1.0):
        with pytest.raises(ValueError, match="must be a boolean"):
            parse_include_all_authors(malformed)


def test_candidate_author_id_validation():
    assert candidate_author_id({"user": {"id": 7, "login": "a"}}) == 7
    assert candidate_author_id({}) is None
    assert candidate_author_id({"user": None}) is None
    assert candidate_author_id({"user": {"id": "7"}}) is None
    assert candidate_author_id({"user": {"id": True}}) is None
    assert candidate_author_id({"user": {"id": 0}}) is None
    assert candidate_author_id({"user": {"id": -3}}) is None


@pytest.mark.asyncio
async def test_conflicting_author_filter_rejected_with_field_error(scope_boundary):
    number, evidence = await resolve_issue(
        repository=REPO,
        query="author:other-author task",
        github_service=_ScopeService(),  # type: ignore[arg-type]
        blockers_from_issue=_no_blockers,
    )

    assert number is None
    assert evidence["reasonCode"] == "conflicting_author_filter"
    assert evidence["field"] == "issueSearch"
    assert evidence["inputPath"] == "preset.inputs.issue_search"
    assert _search_requests() == []


@pytest.mark.asyncio
async def test_author_at_me_normalized_without_duplication(scope_boundary):
    scope_boundary.search_items = [make_issue(11)]

    number, _ = await resolve_issue(
        repository=REPO,
        query="author:@me task",
        github_service=_ScopeService(),  # type: ignore[arg-type]
        blockers_from_issue=_no_blockers,
    )

    assert number == 11
    outgoing = _search_requests()
    assert outgoing[0]["params"]["q"] == f"task author:{IDENTITY['login']} repo:{REPO} is:issue is:open"


@pytest.mark.asyncio
async def test_same_login_qualifier_normalized(scope_boundary):
    scope_boundary.search_items = [make_issue(11)]

    number, _ = await resolve_issue(
        repository=REPO,
        query=f"author:{IDENTITY['login']} task",
        github_service=_ScopeService(),  # type: ignore[arg-type]
        blockers_from_issue=_no_blockers,
    )

    assert number == 11
    outgoing = _search_requests()
    assert outgoing[0]["params"]["q"].count("author:") == 1


@pytest.mark.asyncio
async def test_quoted_author_literal_is_not_a_qualifier(scope_boundary):
    scope_boundary.search_items = [make_issue(11)]

    number, _ = await resolve_issue(
        repository=REPO,
        query='"author:other" task',
        github_service=_ScopeService(),  # type: ignore[arg-type]
        blockers_from_issue=_no_blockers,
    )

    assert number == 11
    outgoing = _search_requests()
    assert f"author:{IDENTITY['login']}" in outgoing[0]["params"]["q"]


@pytest.mark.asyncio
async def test_top_level_or_parenthesized_under_scope(scope_boundary):
    scope_boundary.search_items = [make_issue(11)]

    number, _ = await resolve_issue(
        repository=REPO,
        query="foo OR bar",
        github_service=_ScopeService(),  # type: ignore[arg-type]
        blockers_from_issue=_no_blockers,
    )

    assert number == 11
    outgoing = _search_requests()
    assert outgoing[0]["params"]["q"].startswith(f"(foo OR bar) author:{IDENTITY['login']}")


@pytest.mark.asyncio
async def test_excluded_self_author_rejected(scope_boundary):
    number, evidence = await resolve_issue(
        repository=REPO,
        query=f"-author:{IDENTITY['login']} task",
        github_service=_ScopeService(),  # type: ignore[arg-type]
        blockers_from_issue=_no_blockers,
    )

    assert number is None
    assert evidence["reasonCode"] == "conflicting_author_filter"


@pytest.mark.asyncio
async def test_not_excluded_self_author_rejected(scope_boundary):
    number, evidence = await resolve_issue(
        repository=REPO,
        query=f"NOT author:{IDENTITY['login']} task",
        github_service=_ScopeService(),  # type: ignore[arg-type]
        blockers_from_issue=_no_blockers,
    )

    assert number is None
    assert evidence["reasonCode"] == "conflicting_author_filter"


@pytest.mark.asyncio
async def test_not_excluded_other_author_narrows_safely(scope_boundary):
    scope_boundary.search_items = [make_issue(11)]

    number, _ = await resolve_issue(
        repository=REPO,
        query=f"NOT author:{OTHER_LOGIN} task",
        github_service=_ScopeService(),  # type: ignore[arg-type]
        blockers_from_issue=_no_blockers,
    )

    assert number == 11
    outgoing = _search_requests()
    assert outgoing[0]["params"]["q"] == f"task author:{IDENTITY['login']} repo:{REPO} is:issue is:open"


@pytest.mark.asyncio
async def test_conflicting_duplicate_authors_rejected(scope_boundary):
    number, evidence = await resolve_issue(
        repository=REPO,
        query="author:@me author:other-author",
        github_service=_ScopeService(),  # type: ignore[arg-type]
        blockers_from_issue=_no_blockers,
    )

    assert number is None
    assert evidence["reasonCode"] == "conflicting_author_filter"


def test_plan_query_all_author_passthrough():
    effective, failure = plan_author_scoped_query(
        query="author:someone task", login="me", include_all_authors=True
    )
    assert (effective, failure) == ("author:someone task", None)


# -- R5/R6/R7: trusted loader ---------------------------------------------------


def _loader_inputs(**overrides: Any) -> dict[str, Any]:
    inputs: dict[str, Any] = {"repository": REPO, "issueSearch": "task"}
    inputs.update(overrides)
    return inputs


@pytest.mark.asyncio
async def test_loader_defaults_to_self_only(scope_boundary):
    own = make_issue(12)
    scope_boundary.search_items = [own]
    scope_boundary.detail = dict(own)

    result = await story_tools.load_github_issue_preset_brief(
        _loader_inputs(), github_service_factory=lambda: _ScopeService()
    )

    assert result.status == "COMPLETED"
    search_evidence = result.outputs["searchEvidence"]
    assert search_evidence["authorScope"] == "authenticated_user"
    assert search_evidence["authenticatedUser"] == {"id": IDENTITY["id"], "login": IDENTITY["login"]}
    assert search_evidence["selectedIssueAuthor"]["id"] == IDENTITY["id"]
    assert result.outputs["issue"]["author"] == {"id": IDENTITY["id"], "login": IDENTITY["login"]}


@pytest.mark.asyncio
async def test_loader_explicit_opt_in_selects_other_author(scope_boundary):
    other = make_issue(11, author_id=OTHER_ID, author_login=OTHER_LOGIN)
    scope_boundary.search_items = [other]
    scope_boundary.detail = dict(other)

    result = await story_tools.load_github_issue_preset_brief(
        _loader_inputs(includeAllAuthors=True),
        github_service_factory=lambda: _ScopeService(),
    )

    assert result.status == "COMPLETED"
    assert result.outputs["searchEvidence"]["authorScope"] == "all"
    assert result.outputs["issue"]["number"] == 11


@pytest.mark.asyncio
async def test_loader_rejects_malformed_scope_without_network(scope_boundary):
    result = await story_tools.load_github_issue_preset_brief(
        _loader_inputs(includeAllAuthors="true"),
        github_service_factory=lambda: _ScopeService(),
    )

    assert result.status == "FAILED"
    assert result.outputs["reasonCode"] == "invalid_author_scope"
    assert _ScopeHttpClient.requests == []


@pytest.mark.asyncio
async def test_loader_frozen_saved_plan_requires_refresh(scope_boundary):
    scope_boundary.search_items = [make_issue(12)]

    result = await story_tools.load_github_issue_preset_brief(
        _loader_inputs(),
        {"system": {"recurrence": {"definitionId": "sched-1"}}},
        github_service_factory=lambda: _ScopeService(),
    )

    assert result.status == "FAILED"
    assert result.outputs["reasonCode"] == "author_scope_refresh_required"
    assert result.outputs["refreshRequired"] is True
    assert _ScopeHttpClient.requests == []


@pytest.mark.asyncio
async def test_loader_frozen_plan_with_recorded_false_proceeds(scope_boundary):
    own = make_issue(12)
    scope_boundary.search_items = [own]
    scope_boundary.detail = dict(own)

    result = await story_tools.load_github_issue_preset_brief(
        _loader_inputs(includeAllAuthors=False),
        {"system": {"recurrence": {"definitionId": "sched-1"}}},
        github_service_factory=lambda: _ScopeService(),
    )

    assert result.status == "COMPLETED"
    assert result.outputs["searchEvidence"]["authorScope"] == "authenticated_user"


@pytest.mark.asyncio
async def test_loader_fresh_detail_mismatch_blocks_before_handoff(scope_boundary):
    own = make_issue(12)
    scope_boundary.search_items = [own]
    scope_boundary.detail = make_issue(12, author_id=OTHER_ID, author_login=OTHER_LOGIN)

    result = await story_tools.load_github_issue_preset_brief(
        _loader_inputs(), github_service_factory=lambda: _ScopeService()
    )

    assert result.status == "FAILED"
    assert result.outputs["reasonCode"] == "selected_issue_wrong_author"
    assert "briefArtifactRef" not in result.outputs


@pytest.mark.asyncio
async def test_loader_explicit_issue_number_ignores_author_scope(scope_boundary):
    other = make_issue(11, author_id=OTHER_ID, author_login=OTHER_LOGIN)
    scope_boundary.detail = dict(other)

    result = await story_tools.load_github_issue_preset_brief(
        {"repository": REPO, "issueNumber": 11},
        github_service_factory=lambda: _ScopeService(),
    )

    assert result.status == "COMPLETED"
    assert result.outputs["issue"]["number"] == 11
    assert result.outputs["issue"]["author"]["id"] == OTHER_ID


# -- R7: cutover compat (drain-and-replace, rollback ordering) ------------------


@pytest.mark.asyncio
async def test_stale_worker_dropped_scope_fails_closed_to_self_only(scope_boundary):
    """An old worker that drops the unknown scope field fails closed.

    Fresh inputs without a recorded scope choice (what a stale worker
    forwards) must enforce self-only selection, never all-author.
    """
    own = make_issue(12)
    scope_boundary.search_items = [own]
    scope_boundary.detail = dict(own)

    result = await story_tools.load_github_issue_preset_brief(
        {"repository": REPO, "issueSearch": "task"},
        github_service_factory=lambda: _ScopeService(),
    )

    assert result.status == "COMPLETED"
    assert result.outputs["searchEvidence"]["authorScope"] == "authenticated_user"
    assert result.outputs["issue"]["author"]["id"] == IDENTITY["id"]


@pytest.mark.asyncio
async def test_recorded_opt_in_reapplies_deterministically_on_retry(scope_boundary):
    """Reset/retry of a recorded all-author execution re-applies opt-in.

    Inputs carrying a recorded ``True`` (no recurrence provenance, as on a
    manual reset) must run the all-author search deterministically instead
    of stopping for refresh or reverting to self-only.
    """
    other = make_issue(11, author_id=OTHER_ID, author_login=OTHER_LOGIN)
    scope_boundary.search_items = [other]
    scope_boundary.detail = dict(other)

    result = await story_tools.load_github_issue_preset_brief(
        _loader_inputs(includeAllAuthors=True),
        github_service_factory=lambda: _ScopeService(),
    )

    assert result.status == "COMPLETED"
    assert result.outputs["searchEvidence"]["authorScope"] == "all"
    assert result.outputs["issue"]["number"] == 11


def test_reset_without_recurrence_uses_fresh_default():
    """Resets/retries without recurrence provenance are fresh invocations.

    They materialize the self-only default without a refresh block; only
    pre-change frozen *scheduled* plans (recurrence provenance without a
    recorded choice) stop for refresh.
    """
    from moonmind.workflows.temporal.story_output_tools import (
        _parse_issue_search_author_scope,
    )

    assert _parse_issue_search_author_scope({}, None) == (False, None)
    assert _parse_issue_search_author_scope({}, {"namespace": "default"}) == (
        False,
        None,
    )
    assert _parse_issue_search_author_scope(
        {}, {"workflow": {"appliedStepTemplates": []}}
    ) == (False, None)
