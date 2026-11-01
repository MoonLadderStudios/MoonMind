"""Project selection uses real issues and the shared lifecycle admission path."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from moonmind.workflows.adapters.github_service import GitHubService
from moonmind.workflows.temporal.github_issue_search import resolve_issue


REPOSITORY = "o/r"
PROJECT_URL = "https://github.com/orgs/o/projects/3"


def issue(number, **overrides):
    return {
        "number": number,
        "title": f"Issue {number}",
        "body": "Acceptance criteria",
        "state": "open",
        "labels": [],
        "user": {"id": 123, "login": "author"},
        "html_url": f"https://github.com/{REPOSITORY}/issues/{number}",
        **overrides,
    }


def item(number, column="ready", kind="Issue", **overrides):
    return {
        "id": f"item-{number}",
        "isArchived": False,
        "column": {"optionId": column},
        "priority": None,
        "content": {
            "__typename": kind,
            "id": f"issue-{number}",
            "number": number,
            "state": "OPEN",
            "createdAt": f"2026-01-{number:02d}T00:00:00Z",
            "repository": {"nameWithOwner": REPOSITORY},
        },
        **overrides,
    }


@pytest.fixture
def provider(monkeypatch):
    state = SimpleNamespace(
        options=[
            {"id": name, "name": label}
            for name, label in (
                ("ready", "Ready"),
                ("backlog", "Backlog"),
                ("progress", "In Progress"),
                ("review", "In Review"),
                ("done", "Done"),
                ("attention", "Needs Attention"),
                ("recovery", "Recovery Needed"),
            )
        ],
        items=[],
        requests=[],
        issues={},
        can_update=True,
        filter="",
        has_more=False,
        graphql_errors=None,
        extra_fields=[],
        linked_projects=[],
        linked_project_pages=None,
        owner_projects=[],
        discovery_more=False,
        mutations=[],
        lose_ack=False,
    )

    def handler(request):
        state.requests.append(request)
        if request.url.path == "/graphql":
            body = json.loads(request.content)
            query, variables = body["query"], body.get("variables", {})
            if state.graphql_errors:
                return httpx.Response(200, json={"errors": state.graphql_errors})
            if "RepositoryProjects" in query:
                projects = state.linked_projects
                page_info = {"hasNextPage": state.discovery_more, "endCursor": "next"}
                if state.linked_project_pages is not None:
                    index = int(variables.get("after") or 0)
                    projects = state.linked_project_pages[index]
                    page_info = {
                        "hasNextPage": index + 1 < len(state.linked_project_pages),
                        "endCursor": str(index + 1),
                    }
                return httpx.Response(
                    200,
                    json={
                        "data": {
                            "repository": {
                                "name": "r",
                                "nameWithOwner": REPOSITORY,
                                "owner": {
                                    "id": "owner",
                                    "__typename": "Organization",
                                    "login": "o",
                                },
                                "projectsV2": {
                                    "nodes": projects,
                                    "pageInfo": page_info,
                                },
                            }
                        }
                    },
                )
            if "RepositoryOwnerProjects" in query:
                return httpx.Response(
                    200,
                    json={
                        "data": {
                            "node": {
                                "id": "owner",
                                "projectsV2": {
                                    "nodes": state.owner_projects,
                                    "pageInfo": {
                                        "hasNextPage": state.discovery_more,
                                        "endCursor": "next",
                                    },
                                },
                            }
                        }
                    },
                )
            if "ProjectQueueMetadata" in query:
                project = {
                    "id": "project",
                    "title": "Work",
                    "viewerCanUpdate": state.can_update,
                    "fields": {
                        "nodes": [
                            {
                                "__typename": "ProjectV2SingleSelectField",
                                "id": "status",
                                "name": "Status",
                                "options": state.options,
                            }
                        ]
                        + state.extra_fields,
                        "pageInfo": {"hasNextPage": False},
                    },
                    "view": {"id": "view", "filter": state.filter},
                }
                return httpx.Response(
                    200, json={"data": {"owner": {"projectV2": project}}}
                )
            if "ProjectQueueItems" in query:
                return httpx.Response(
                    200,
                    json={
                        "data": {
                            "node": {
                                "items": {
                                    "nodes": state.items,
                                    "pageInfo": {
                                        "hasNextPage": state.has_more,
                                        "endCursor": "next",
                                    },
                                }
                            }
                        }
                    },
                )
            if "ProjectItemPhase" in query:
                found = next(
                    (i for i in state.items if i["id"] == variables["item"]), None
                )
                return httpx.Response(
                    200,
                    json={
                        "data": {
                            "node": (
                                {
                                    **found,
                                    "project": {"id": "project"},
                                    "fieldValues": {
                                        "nodes": [
                                            {
                                                "optionId": found["column"]["optionId"],
                                                "field": {"id": "status"},
                                            }
                                        ],
                                        "pageInfo": {"hasNextPage": False},
                                    },
                                }
                                if found
                                else None
                            )
                        }
                    },
                )
            if "UpdateProjectPhase" in query:
                state.mutations.append(variables)
                found = next(i for i in state.items if i["id"] == variables["item"])
                found["column"]["optionId"] = variables["option"]
                if state.lose_ack:
                    state.lose_ack = False
                    raise httpx.ReadTimeout(
                        "fixture lost acknowledgement", request=request
                    )
                return httpx.Response(
                    200,
                    json={
                        "data": {
                            "updateProjectV2ItemFieldValue": {
                                "projectV2Item": {"id": variables["item"]},
                            }
                        }
                    },
                )
            raise AssertionError(query)
        if "/issues/" in request.url.path:
            number = int(request.url.path.rsplit("/", 1)[1])
            return httpx.Response(200, json=state.issues.get(number, issue(number)))
        raise AssertionError(f"Unexpected request: {request.url}")

    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original(
            transport=httpx.MockTransport(handler),
            **kwargs,
        ),
    )
    monkeypatch.setattr(
        GitHubService, "resolve_github_token", AsyncMock(return_value=("fixture", None))
    )
    return state


async def select(source=None, **kwargs):
    return await resolve_issue(
        repository=REPOSITORY,
        query="",
        github_service=GitHubService(),
        blockers_from_issue=kwargs.pop(
            "blockers_from_issue", AsyncMock(return_value=[])
        ),
        include_all_authors=True,
        project_source={"url": PROJECT_URL, **(source or {})},
        **kwargs,
    )


@pytest.mark.asyncio
async def test_drafts_never_qualify_even_in_ready(provider):
    provider.items = [
        item(1, kind="DraftIssue"),
        item(2, kind="PullRequest"),
        item(3, content=None),
        item(4, isArchived=True),
        item(5),
    ]
    reserve = AsyncMock(return_value=True)
    number, result = await select(reserve_candidate=reserve)
    assert number == 5
    reserve.assert_awaited_once_with(5)
    assert result["searchEvidence"]["project"]["skipped"]["draft_issue"] == 1
    assert not any("/issues/1" in str(r.url) for r in provider.requests)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name", ["Selected for Development", "Ready for Development", "rEaDy"]
)
async def test_ready_default_recognizes_only_unambiguous_equivalents(provider, name):
    provider.options[0]["name"] = name
    provider.items = [item(1)]
    number, _ = await select()
    assert number == 1


@pytest.mark.asyncio
async def test_ready_wins_over_equivalent_and_backlog(provider):
    provider.options.append({"id": "selected", "name": "Selected for Development"})
    provider.items = [item(1, "backlog"), item(2, "selected"), item(3)]
    number, _ = await select()
    assert number == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "names", [["Backlog"], ["Selected for Development", "Ready for Development"]]
)
async def test_missing_or_ambiguous_ready_requires_configuration(provider, names):
    provider.options = [{"id": str(n), "name": name} for n, name in enumerate(names)]
    number, result = await select()
    assert number is None
    assert result["reasonCode"] == "project_column_selection_required"


@pytest.mark.asyncio
async def test_explicit_column_order_precedes_age_and_blocked_items_are_skipped(
    provider,
):
    provider.items = [item(1, "backlog"), item(3), item(2)]
    blockers = AsyncMock(
        side_effect=lambda candidate: (
            [{"blocked": True}] if candidate["number"] == 2 else []
        )
    )
    number, result = await select(
        {"columns": "Ready\nBacklog"}, blockers_from_issue=blockers
    )
    assert number == 3
    assert result["searchEvidence"]["rejectionCounts"]["blocked_prerequisite"] == 1


@pytest.mark.asyncio
async def test_unknown_view_filter_is_rejected_without_broadening(provider):
    provider.filter = "custom-field:urgent"
    number, result = await select({"url": PROJECT_URL + "/views/1"})
    assert number is None
    assert result["reasonCode"] == "unsupported_project_view_filter"
    assert not provider.mutations


@pytest.mark.asyncio
async def test_project_write_permission_is_required_before_claim(provider):
    provider.can_update = False
    reserve = AsyncMock(return_value=True)
    number, result = await select(reserve_candidate=reserve)
    assert number is None
    assert result["reasonCode"] == "project_permission_denied"
    reserve.assert_not_awaited()


@pytest.mark.asyncio
async def test_incomplete_pagination_is_not_an_empty_queue(provider):
    provider.items = [item(1)]
    provider.has_more = True
    number, result = await select()
    assert number is None
    assert result["reasonCode"] == "project_scan_incomplete"


@pytest.mark.asyncio
async def test_selection_does_not_read_lower_priority_issue_details(provider):
    provider.items = [item(1, "backlog"), item(2)]
    number, _ = await select({"columns": "Ready\nBacklog"})
    assert number == 2
    assert not any(
        str(request.url).endswith("/issues/1") for request in provider.requests
    )


@pytest_asyncio.fixture
async def project_store(tmp_path):
    from api_service.db.models import GitHubIssueClaim
    from moonmind.workflows.temporal.issue_claim_store import IssueClaimStore

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'claims.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(GitHubIssueClaim.__table__.create)
    store = IssueClaimStore(async_sessionmaker(engine, expire_on_commit=False))
    yield store
    await engine.dispose()


async def bound_claim(provider, store):
    from moonmind.workflows.temporal.github_project import discover_project_queue

    provider.items = [item(1)]
    queue = await discover_project_queue(
        GitHubService(), REPOSITORY, {"url": PROJECT_URL}
    )
    receipt = await store.prepare(
        owner="default/project-run",
        repository=REPOSITORY,
        issue_number=1,
        attempt_id="project-attempt",
        actor_id="123",
        comment_body="",
    )
    await store.bind_project(receipt.owner, queue.bindings[1])
    async with store.locked(receipt.owner) as row:
        row.confirmed = True
    return await store.get(receipt.owner)


@pytest.mark.asyncio
async def test_phase_sync_recovers_a_lost_ack_without_repeating_effect(
    provider, project_store
):
    from moonmind.workflows.temporal import github_project

    receipt = await bound_claim(provider, project_store)
    provider.issues[1] = issue(1, labels=[{"name": "status: in-progress"}])
    provider.lose_ack = True
    first = await github_project.sync_claim_project(
        project_store, receipt, GitHubService()
    )
    assert first["status"] == "pending"
    restarted_receipt = await project_store.get(receipt.owner)
    assert restarted_receipt.project_binding_json["pendingOptionId"] == "progress"
    second = await github_project.sync_claim_project(
        project_store, restarted_receipt, GitHubService()
    )
    assert second["status"] == "confirmed"
    assert len(provider.mutations) == 1
    assert (await project_store.get(receipt.owner)).project_binding_json[
        "lastOptionId"
    ] == "progress"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "label,option",
    [
        ("status: in-progress", "progress"),
        ("status: code-review", "review"),
        ("status: recovery-needed", "recovery"),
        ("status: needs-attention", "attention"),
    ],
)
async def test_board_follows_confirmed_lifecycle(
    provider, project_store, label, option
):
    from moonmind.workflows.temporal import github_project

    receipt = await bound_claim(provider, project_store)
    provider.issues[1] = issue(1, labels=[{"name": label}])
    result = await github_project.sync_claim_project(
        project_store, receipt, GitHubService()
    )
    assert result["status"] == "confirmed"
    assert provider.items[0]["column"]["optionId"] == option


@pytest.mark.asyncio
async def test_manual_move_suspends_sync_instead_of_overwriting_it(
    provider, project_store
):
    from moonmind.workflows.temporal import github_project

    receipt = await bound_claim(provider, project_store)
    provider.items[0]["column"]["optionId"] = "backlog"
    provider.issues[1] = issue(1, labels=[{"name": "status: in-progress"}])
    result = await github_project.sync_claim_project(
        project_store, receipt, GitHubService()
    )
    assert result["reasonCode"] == "project_operator_move"
    assert not provider.mutations
    assert (await project_store.get(receipt.owner)).project_binding_json["suspended"]


@pytest.mark.asyncio
async def test_closed_not_planned_never_becomes_done(provider, project_store):
    from moonmind.workflows.temporal import github_project

    receipt = await bound_claim(provider, project_store)
    provider.issues[1] = issue(1, state="closed", state_reason="not_planned")
    result = await github_project.sync_claim_project(
        project_store, receipt, GitHubService()
    )
    assert result["reasonCode"] == "project_closed_without_completion"
    assert not provider.mutations


@pytest.mark.asyncio
async def test_done_requires_verified_completion_not_just_a_closed_issue(
    provider, project_store
):
    from moonmind.workflows.temporal import github_project

    receipt = await bound_claim(provider, project_store)
    provider.issues[1] = issue(1, state="closed", state_reason="completed")
    first = await github_project.sync_claim_project(
        project_store, receipt, GitHubService()
    )
    assert first["status"] == "pending"
    assert not provider.mutations
    second = await github_project.sync_claim_project(
        project_store,
        receipt,
        GitHubService(),
        lifecycle_result={"lifecycleSettled": "closed", "mutationOutcome": "applied"},
    )
    assert second["status"] == "confirmed"
    assert provider.items[0]["column"]["optionId"] == "done"


@pytest.mark.asyncio
async def test_binding_cannot_be_retargeted_on_retry(provider, project_store):
    receipt = await bound_claim(provider, project_store)
    with pytest.raises(ValueError, match="substitution_denied"):
        await project_store.bind_project(
            receipt.owner, {**receipt.project_binding_json, "itemId": "replacement"}
        )


@pytest.mark.asyncio
async def test_periodic_sweep_retries_failed_projection_from_durable_claim(
    provider, project_store
):
    from moonmind.workflows.temporal.github_project import reconcile_project_claims

    receipt = await bound_claim(provider, project_store)
    provider.issues[1] = issue(1, labels=[{"name": "status: code-review"}])
    results = await reconcile_project_claims(
        state={}, store=project_store, service=GitHubService()
    )
    assert results[0]["status"] == "confirmed"
    assert provider.items[0]["column"]["optionId"] == "review"
    assert (await project_store.get(receipt.owner)).project_binding_json[
        "phase"
    ] == "code_review"


@pytest.mark.asyncio
async def test_late_merge_uses_exact_candidate_and_completion_target(
    provider, project_store, monkeypatch
):
    from moonmind.workflows.temporal import github_project

    receipt = await bound_claim(provider, project_store)
    provider.issues[1] = issue(1, labels=[{"name": "status: code-review"}])
    candidate = {
        "prUrl": f"https://github.com/{REPOSITORY}/pull/7",
        "prHead": "a" * 40,
        "prBase": "main",
    }
    result = await github_project.sync_claim_project(
        project_store,
        receipt,
        GitHubService(),
        lifecycle_result={
            "mutationOutcome": "applied",
            "attemptHandoff": candidate,
        },
    )
    assert result["phase"] == "code_review"
    provider.issues[1] = issue(1, state="closed", state_reason="completed")
    service = GitHubService()
    reader = AsyncMock(
        return_value={
            "number": 7,
            "state": "closed",
            "merged": True,
            "merge_commit_sha": "c" * 40,
            "title": "Fix o/r#1",
            "body": "Closes o/r#1",
            "base": {"ref": "main", "repo": {"full_name": REPOSITORY}},
            "head": {
                "ref": "feature",
                "sha": "a" * 40,
                "repo": {"full_name": REPOSITORY},
            },
        }
    )
    monkeypatch.setattr(service, "read_pull_request", reader)
    monkeypatch.setattr(
        service,
        "read_repository_target",
        AsyncMock(return_value={"ref": "refs/heads/main", "revision": "c" * 40}),
    )
    result = await github_project.sync_claim_project(
        project_store, await project_store.get(receipt.owner), service
    )
    assert result["status"] == "confirmed", result
    assert provider.items[0]["column"]["optionId"] == "done"
    assert reader.await_args.kwargs["admitted_workflow_id"] == "project-run"


@pytest.mark.asyncio
async def test_draft_only_queue_is_idle_with_a_reason(provider):
    provider.items = [item(1, kind="DraftIssue")]
    number, result = await select()
    assert number is None
    assert result["disposition"] == "idle"
    assert "draft_issue=1" in result["summary"]


@pytest.mark.asyncio
async def test_supported_view_filter_is_applied_before_reservation(provider):
    provider.filter = "is:issue is:open label:bug -label:hold"
    provider.items = [item(1), item(2)]
    provider.issues[1] = issue(1, labels=[{"name": "bug"}, {"name": "hold"}])
    provider.issues[2] = issue(2, labels=[{"name": "bug"}])
    number, _ = await select({"url": PROJECT_URL + "/views/1"})
    assert number == 2


@pytest.mark.asyncio
async def test_renamed_option_keeps_the_pinned_phase_identity(provider, project_store):
    from moonmind.workflows.temporal.github_project import sync_claim_project

    receipt = await bound_claim(provider, project_store)
    provider.options[2]["name"] = "Building"
    provider.issues[1] = issue(1, labels=[{"name": "status: in-progress"}])
    result = await sync_claim_project(project_store, receipt, GitHubService())
    assert result["status"] == "confirmed"
    assert provider.items[0]["column"]["optionId"] == "progress"


@pytest.mark.asyncio
async def test_restoring_managed_column_resumes_suspended_projection(
    provider, project_store
):
    from moonmind.workflows.temporal.github_project import sync_claim_project

    receipt = await bound_claim(provider, project_store)
    provider.issues[1] = issue(1, labels=[{"name": "status: in-progress"}])
    provider.items[0]["column"]["optionId"] = "backlog"
    assert (await sync_claim_project(project_store, receipt, GitHubService()))[
        "status"
    ] == "suspended"
    provider.items[0]["column"]["optionId"] = "ready"
    result = await sync_claim_project(
        project_store, await project_store.get(receipt.owner), GitHubService()
    )
    assert result["status"] == "confirmed"
    assert provider.items[0]["column"]["optionId"] == "progress"


@pytest.mark.asyncio
async def test_retained_completion_receipt_survives_a_later_reporting_failure(
    provider, project_store
):
    from moonmind.workflows.temporal.github_project import sync_claim_project

    receipt = await bound_claim(provider, project_store)
    provider.issues[1] = issue(1, state="closed", state_reason="completed")
    await project_store.record_finalization(
        receipt.owner,
        "plan",
        {
            "releasable": True,
            "transition": {"allowed": True, "toTarget": "to_closed"},
        },
    )
    await project_store.record_finalization(
        receipt.owner,
        "observation",
        {
            "mutationOutcome": {"outcome": "applied"},
        },
    )
    result = await sync_claim_project(project_store, receipt, GitHubService())
    assert result["status"] == "confirmed"
    assert provider.items[0]["column"]["optionId"] == "done"


def test_projects_uses_its_own_explicit_connection_operation():
    from moonmind.auth.github_app import operations_to_permissions

    assert operations_to_permissions(["projects"]) == {"organization_projects": "write"}
    assert operations_to_permissions(["issues"]) == {"issues": "write"}


@pytest.mark.asyncio
@pytest.mark.parametrize("numeric", [False, True])
async def test_priority_ordering_precedes_age_and_missing_values(provider, numeric):
    provider.extra_fields = [
        {
            "__typename": "ProjectV2Field" if numeric else "ProjectV2SingleSelectField",
            "id": "priority",
            "name": "Priority",
            "dataType": "NUMBER" if numeric else "SINGLE_SELECT",
            "options": (
                []
                if numeric
                else [{"id": "high", "name": "High"}, {"id": "low", "name": "Low"}]
            ),
        }
    ]
    provider.items = [
        item(1, priority={"number": 5} if numeric else {"optionId": "low"}),
        item(2, priority={"number": 1} if numeric else {"optionId": "high"}),
        item(3),
    ]
    number, result = await select({"order": "priority"})
    assert number == 2
    assert result["searchEvidence"]["project"]["order"] == "priority"


@pytest.mark.asyncio
async def test_personal_project_url_uses_user_lookup(provider):
    provider.items = [item(1)]
    number, _ = await select(
        {"url": "https://github.com/users/another-owner/projects/7"}
    )
    assert number == 1
    metadata = json.loads(provider.requests[0].content)
    assert "owner: user(login:" in metadata["query"]
    assert metadata["variables"]["number"] == 7


@pytest.mark.asyncio
async def test_graphql_permission_error_is_not_an_idle_queue_or_raw_message(provider):
    provider.graphql_errors = [
        {"type": "FORBIDDEN", "message": "private provider details"}
    ]
    number, result = await select()
    assert number is None
    assert result["reasonCode"] == "project_permission_denied"
    assert result.get("disposition") != "idle"
    assert "private provider details" not in str(result)


def project(title="r", *, project_id="project", number=3, closed=False):
    return {
        "id": project_id,
        "title": title,
        "url": f"https://github.com/orgs/o/projects/{number}",
        "closed": closed,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["", "   ", None])
async def test_blank_url_discovers_the_repository_linked_project(provider, url):
    provider.linked_projects = [project("Shared work")]
    provider.items = [item(1, kind="DraftIssue"), item(2)]
    number, result = await select({"url": url})
    assert number == 2, result
    evidence = result["searchEvidence"]["project"]
    assert evidence["url"] == PROJECT_URL
    assert evidence["resolution"] == "repository_link"
    assert result["selectedProjectItem"]["projectUrl"] == PROJECT_URL
    assert result["searchEvidence"]["project"]["skipped"]["draft_issue"] == 1


@pytest.mark.asyncio
async def test_same_name_breaks_a_tie_between_linked_projects(provider):
    provider.linked_projects = [
        project("Shared", project_id="other", number=4),
        project("R"),
    ]
    provider.items = [item(1)]
    number, result = await select({"url": ""})
    assert number == 1, result
    assert result["searchEvidence"]["project"]["url"] == PROJECT_URL


@pytest.mark.asyncio
async def test_no_linked_project_falls_back_to_owner_project_with_same_name(provider):
    provider.owner_projects = [
        project("Other", project_id="other", number=4),
        project(" R "),
    ]
    provider.items = [item(1)]
    number, result = await select({"url": ""})
    assert number == 1, result
    assert result["searchEvidence"]["project"]["resolution"] == "repository_name"


@pytest.mark.asyncio
@pytest.mark.parametrize("linked", [True, False])
async def test_ambiguous_repository_projects_require_an_explicit_url(provider, linked):
    candidates = [project("r"), project("r", project_id="other", number=4)]
    if linked:
        provider.linked_projects = candidates
    else:
        provider.owner_projects = candidates
    reserve = AsyncMock(return_value=True)
    number, result = await select({"url": ""}, reserve_candidate=reserve)
    assert number is None
    assert result["reasonCode"] == "project_selection_required"
    reserve.assert_not_awaited()


@pytest.mark.asyncio
async def test_discovery_does_not_choose_an_unrelated_or_closed_project(provider):
    provider.linked_projects = [project(closed=True)]
    provider.owner_projects = [
        project("Unrelated"),
        project("r", project_id="closed", number=4, closed=True),
    ]
    number, result = await select({"url": ""})
    assert number is None
    assert result["reasonCode"] == "project_not_found"


@pytest.mark.asyncio
async def test_incomplete_project_discovery_cannot_claim_a_unique_match(provider):
    provider.linked_projects = [project()]
    provider.discovery_more = True
    number, result = await select({"url": ""})
    assert number is None
    assert result["reasonCode"] == "project_discovery_incomplete"


@pytest.mark.asyncio
async def test_explicit_url_does_not_query_repository_projects(provider):
    provider.linked_projects = [
        project("a"),
        project("b", project_id="other", number=4),
    ]
    provider.items = [item(1)]
    number, _ = await select()
    assert number == 1
    queries = [
        json.loads(request.content)["query"]
        for request in provider.requests
        if request.url.path == "/graphql"
    ]
    assert not any("RepositoryProjects" in query for query in queries)


@pytest.mark.asyncio
async def test_linked_project_selection_finishes_pagination_before_choosing(provider):
    provider.linked_project_pages = [
        [project("Shared", project_id="other", number=4)],
        [project("r")],
    ]
    provider.items = [item(1)]
    number, result = await select({"url": ""})
    assert number == 1, result
    lookups = [
        json.loads(request.content)
        for request in provider.requests
        if request.url.path == "/graphql"
        and "RepositoryProjects" in json.loads(request.content)["query"]
    ]
    assert [lookup["variables"]["after"] for lookup in lookups] == [None, "1"]


@pytest.mark.asyncio
async def test_project_discovery_uses_the_selected_repository_identity(provider):
    provider.linked_projects = [project()]
    provider.items = [item(1)]
    number, _ = await select({"url": ""})
    assert number == 1
    variables = json.loads(provider.requests[0].content)["variables"]
    assert variables["owner"] == "o"
    assert variables["name"] == "r"
