import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from moonmind.workflows.adapters.github_service import GitHubService
from moonmind.workflows.temporal.activities import repo_activities
from moonmind.workflows.temporal.workflows import run as run_module
from tests.unit.workflows.skills.test_acceptance_source_binding import (
    _disclose_unverified,
)

pytest_plugins = ("tests.unit.workflows.skills.test_acceptance_source_binding",)

HEAD_SHA = "a" * 40


def wire(monkeypatch, body, *, comments=None, on_request=None):
    document = {"state": "open", "body": body, "head": {"sha": HEAD_SHA}}
    comments = [] if comments is None else comments
    requests = []

    def respond(request):
        requests.append(request)
        if on_request:
            response = on_request(request, document, comments)
            if response is not None:
                return response
        if request.url.path.endswith("/comments"):
            if request.method == "POST":
                payload = json.loads(request.content)
                assert set(payload) == {"body"}
                comment = {"id": len(comments) + 1, **payload}
                comments.append(comment)
                return httpx.Response(201, json=comment)
            assert request.method == "GET"
            page = int(request.url.params["page"])
            assert request.url.params["per_page"] == "100"
            links = {}
            if page * 100 < len(comments):
                last = (len(comments) + 99) // 100
                links["Link"] = (
                    f'<{request.url.copy_set_param("page", str(page + 1))}>; rel="next", '
                    f'<{request.url.copy_set_param("page", str(last))}>; rel="last"'
                )
            return httpx.Response(200, json=comments[(page - 1) * 100:page * 100], headers=links)
        assert request.method == "GET", "Shared PR bodies and existing comments are read-only"
        return httpx.Response(200, json=document, headers={"etag": '"ignored-for-writes"'})

    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs),
    )
    service = GitHubService()
    service.resolve_github_token = AsyncMock(return_value=("default-token", None))
    service._repository_reader = AsyncMock(
        return_value=(
            "https://api.github.com",
            {"Authorization": "Bearer selected-token"},
        )
    )
    return service, document, comments, requests


@pytest.mark.asyncio
@pytest.mark.parametrize("observed_head", [{"sha": "b" * 40}, {}, None])
@pytest.mark.parametrize(
    "section",
    [
        "## Unverified criteria\n\n- GPU: unavailable",
        "",
        "## Unverified criteria\n\n- OLD: obsolete reason",
    ],
    ids=["replace", "clear", "no-op"],
)
async def test_activity_rejects_unaccepted_pr_head_before_disclosure(
    monkeypatch, observed_head, section
):
    body = (
        "Human introduction.\n\n<!-- moonmind:unverified:start -->\n"
        "## Unverified criteria\n\n- OLD: obsolete reason\n"
        "<!-- moonmind:unverified:end -->"
    )
    service, document, comments, _requests = wire(monkeypatch, body)
    document["head"] = observed_head
    monkeypatch.setattr(
        "moonmind.workflows.adapters.github_service.GitHubService", lambda: service
    )
    with pytest.raises(ValueError, match="head"):
        await repo_activities.repo_sync_pr_unverified_criteria_activity(
            {
                "repo": "o/r",
                "prUrl": "https://github.com/o/r/pull/7",
                "section": section,
                "expectedHeadSha": HEAD_SHA,
            }
        )
    assert comments == []
    assert document["body"] == body


@pytest.mark.asyncio
@pytest.mark.parametrize("expected_head", ["", "   "])
async def test_activity_rejects_blank_expected_head(monkeypatch, expected_head):
    service, document, comments, _requests = wire(monkeypatch, "Human introduction.")
    monkeypatch.setattr(
        "moonmind.workflows.adapters.github_service.GitHubService", lambda: service
    )
    with pytest.raises(ValueError):
        await repo_activities.repo_sync_pr_unverified_criteria_activity(
            {
                "repo": "o/r",
                "prUrl": "https://github.com/o/r/pull/7",
                "section": "## Unverified criteria\n\n- GPU: unavailable",
                "expectedHeadSha": expected_head,
            }
        )
    assert comments == []
    assert document["body"] == "Human introduction."


@pytest.mark.asyncio
@pytest.mark.parametrize("section", ["## Unverified criteria\n\n- GPU: unavailable", ""])
@pytest.mark.parametrize("body", [
    "Human introduction.\n\n## Unverified criteria\n\nReviewer: keep this.\n\n## Validation\nKeep results.",
    ("Reviewer introduction.\n\n<!-- moonmind:unverified:start -->\n"
    "## Unverified criteria\n\n- OLD: old reason\n<!-- moonmind:unverified:end -->\n\nReviewer note."),
    "Human introduction.\n\n## Unverified criteria\n\n- GPU: unavailable\n## Detail\nHost missing.",
    "Human prose and an incomplete <!-- moonmind:unverified:start --> marker.",
])
async def test_disclosure_preserves_all_existing_body_and_human_comments(monkeypatch, section, body):
    human = {"id": 1, "body": "Reviewer: <!-- moonmind:unverified:start --> keep my comment."}
    service, document, comments, requests = wire(monkeypatch, body, comments=[dict(human)])
    for _ in range(2):
        await service.sync_pull_request_unverified_criteria(
            repo="o/r", pr_url="https://github.com/o/r/pull/7", section=section,
            expected_head_sha=HEAD_SHA,
        )
    assert document["body"] == body
    assert comments[0] == human
    assert len(comments) == 2
    disclosure = comments[-1]["body"]
    assert HEAD_SHA in disclosure
    assert "latest" in disclosure.lower()
    assert "historical" in disclosure.lower()
    if section:
        assert section in disclosure
    else:
        assert "This verification report records no Unverified criteria" in disclosure
    assert sum(request.method == "POST" for request in requests) == 1
    assert all(request.method in {"GET", "POST"} for request in requests)
    assert all("if-match" not in request.headers for request in requests)


@pytest.mark.asyncio
async def test_valid_multiline_disclosure_is_idempotent(
    monkeypatch, source_bound_report
):
    row = _disclose_unverified(source_bound_report)
    row["unverified"]["reason"] = (
        "Credentials unavailable.\n## Diagnostic detail\nThe provider is inaccessible."
    )
    parent = run_module.MoonMindRunWorkflow()
    parent._publish_context["moonSpecGate"] = source_bound_report
    section = parent._moonspec_unverified_body_section()
    service, document, comments, _requests = wire(monkeypatch, "Human introduction.")
    for _ in range(2):
        await service.sync_pull_request_unverified_criteria(
            repo="o/r", pr_url="https://github.com/o/r/pull/7", section=section
        )
    assert len(comments) == 1
    assert document["body"] == "Human introduction."
    assert comments[0]["body"].count("The provider is inaccessible.") == 1


@pytest.mark.asyncio
async def test_activity_honors_admitted_connection_denial(monkeypatch):
    service, _document, comments, _requests = wire(monkeypatch, "Human introduction.")
    service._repository_reader = AsyncMock(
        side_effect=ValueError("selected connection denied")
    )
    monkeypatch.setattr(
        "moonmind.workflows.adapters.github_service.GitHubService", lambda: service
    )
    monkeypatch.setattr(repo_activities.activity, "in_activity", lambda: True)
    monkeypatch.setattr(
        repo_activities.activity,
        "info",
        lambda: SimpleNamespace(workflow_id="selected-workflow"),
    )
    with pytest.raises(ValueError, match="selected connection denied"):
        await repo_activities.repo_sync_pr_unverified_criteria_activity(
            {
                "repo": "o/r",
                "prUrl": "https://github.com/o/r/pull/7",
                "section": "## Unverified criteria\n\n- GPU: Hardware unavailable",
                "expectedHeadSha": HEAD_SHA,
            }
        )
    assert comments == []
    service.resolve_github_token.assert_not_awaited()


@pytest.mark.asyncio
async def test_fresh_workflow_supersedes_stale_disclosure_without_editing_body(
    monkeypatch, source_bound_report
):
    parent = run_module.MoonMindRunWorkflow()
    parent._repo = "example/repo"
    source_bound_report["validatedRefs"]["acceptance"]["subject"]["revision"] = HEAD_SHA
    parent._publish_context["moonSpecGate"] = source_bound_report
    monkeypatch.setattr(run_module.workflow, "patched", lambda _: True)
    service, document, comments, _requests = wire(
        monkeypatch,
        "Human introduction.\n\n<!-- moonmind:unverified:start -->\n"
        "## Unverified criteria\n\n- OLD: obsolete reason\n"
        "<!-- moonmind:unverified:end -->\n\n## Validation\nKeep this.",
    )
    monkeypatch.setattr(
        "moonmind.workflows.adapters.github_service.GitHubService", lambda: service
    )

    async def execute(name, payload, **kwargs):
        assert name == "repo.sync_pr_unverified_criteria"
        assert payload["expectedHeadSha"] == HEAD_SHA
        return await repo_activities.repo_sync_pr_unverified_criteria_activity(payload)

    monkeypatch.setattr(
        run_module.workflow, "execute_activity", AsyncMock(side_effect=execute)
    )
    await parent._sync_pr_unverified_criteria("https://github.com/example/repo/pull/7")
    assert "OLD" in document["body"]  # Initial body is historical and remains untouched.
    assert "Keep this." in document["body"]
    assert len(comments) == 1
    assert "This verification report records no Unverified criteria" in comments[0]["body"]
    assert HEAD_SHA in comments[0]["body"]


@pytest.mark.asyncio
async def test_activity_uses_its_admitted_enterprise_connection(monkeypatch):
    from moonmind.config.settings import settings

    monkeypatch.setattr(settings.github, "github_trusted_api_hosts", "git.example.test")
    service = GitHubService()
    service._repository_reader = AsyncMock(
        return_value=(
            "https://git.example.test/api/v3",
            {"Authorization": "Bearer selected-token"},
        )
    )
    service.resolve_github_token = AsyncMock(
        side_effect=AssertionError("No default credential allowed")
    )
    document = {
        "state": "open", "body": "Human introduction.", "head": {"sha": HEAD_SHA}
    }
    requests = []
    comments = []

    def respond(request):
        requests.append(request)
        assert str(request.url).startswith("https://git.example.test/api/v3/repos/o/r/")
        assert request.headers["authorization"] == "Bearer selected-token"
        if request.url.path.endswith("/comments"):
            if request.method == "POST":
                comments.append({"id": 1, **json.loads(request.content)})
                return httpx.Response(201, json=comments[-1])
            assert request.method == "GET"
            return httpx.Response(200, json=comments)
        assert request.method == "GET"
        return httpx.Response(200, json=document)

    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs),
    )
    monkeypatch.setattr(
        "moonmind.workflows.adapters.github_service.GitHubService", lambda: service
    )
    monkeypatch.setattr(repo_activities.activity, "in_activity", lambda: True)
    monkeypatch.setattr(
        repo_activities.activity,
        "info",
        lambda: SimpleNamespace(workflow_id="selected-workflow"),
    )
    await repo_activities.repo_sync_pr_unverified_criteria_activity(
        {
            "repo": "o/r",
            "prUrl": "https://git.example.test/o/r/pull/7",
            "section": "## Unverified criteria\n\n- GPU: Hardware unavailable",
            "expectedHeadSha": HEAD_SHA,
            "admittedWorkflowId": "forged",
        }
    )
    service._repository_reader.assert_awaited_once_with(
        "o/r",
        None,
        "selected-workflow",
        operation="review_request",
        expected_api_base="https://git.example.test/api/v3",
    )
    service.resolve_github_token.assert_not_awaited()
    assert len(comments) == 1
    assert document["body"] == "Human introduction."


@pytest.mark.asyncio
@pytest.mark.parametrize("moved_head", [False, True])
async def test_lost_ack_sync_retry_reads_before_mutating(monkeypatch, moved_head):
    def lose_ack(request, document, comments):
        if request.method == "POST":
            comments.append({"id": len(comments) + 1, **json.loads(request.content)})
            raise httpx.ReadError("successful comment lost its acknowledgment")

    service, document, comments, requests = wire(
        monkeypatch, "Human introduction.", on_request=lose_ack,
    )
    payload = {
        "repo": "o/r", "pr_url": "https://github.com/o/r/pull/7",
        "section": "## Unverified criteria\n\n- GPU: unavailable\n## Detail\nStill unavailable",
        "expected_head_sha": HEAD_SHA,
    }
    with pytest.raises(httpx.ReadError):
        await service.sync_pull_request_unverified_criteria(**payload)
    if moved_head:
        document["head"]["sha"] = "b" * 40
        with pytest.raises(ValueError, match="head"):
            await service.sync_pull_request_unverified_criteria(**payload)
    else:
        assert await service.sync_pull_request_unverified_criteria(**payload) is False
    assert len(comments) == 1
    assert sum(request.method == "POST" for request in requests) == 1


@pytest.mark.asyncio
async def test_racing_human_edit_survives_disclosure(monkeypatch):
    def edit(request, document, comments):
        if request.method == "POST":
            document["body"] = "Human edited this after our last read."
            comments[0]["body"] = "Human edited their comment concurrently."

    service, document, comments, requests = wire(
        monkeypatch, "Original human introduction.",
        comments=[{"id": 1, "body": "Original reviewer comment."}], on_request=edit,
    )
    await service.sync_pull_request_unverified_criteria(
        repo="o/r", pr_url="https://github.com/o/r/pull/7", section="Current disclosure",
        expected_head_sha=HEAD_SHA,
    )
    assert document["body"] == "Human edited this after our last read."
    assert comments[0]["body"] == "Human edited their comment concurrently."
    assert len(comments) == 2
    assert all(request.method in {"GET", "POST"} for request in requests)


@pytest.mark.asyncio
async def test_changed_disclosure_and_new_head_append_without_rewriting_history(monkeypatch):
    service, document, comments, _ = wire(monkeypatch, "Original body")
    for head, section in [(HEAD_SHA, "GPU unavailable"), (HEAD_SHA, ""), ("b" * 40, "")]:
        document["head"]["sha"] = head
        assert await service.sync_pull_request_unverified_criteria(
            repo="o/r", pr_url="https://github.com/o/r/pull/7", section=section,
            expected_head_sha=head,
        ) is True
    assert len(comments) == 3
    assert "GPU unavailable" in comments[0]["body"]
    assert "This verification report records no Unverified criteria" in comments[1]["body"]
    assert HEAD_SHA in comments[1]["body"]
    assert "b" * 40 in comments[2]["body"]
    assert document["body"] == "Original body"


@pytest.mark.asyncio
async def test_retry_adopts_disclosure_on_later_comment_page(monkeypatch):
    service, _, comments, requests = wire(monkeypatch, "Original body")
    payload = {"repo": "o/r", "pr_url": "https://github.com/o/r/pull/7", "section": "GPU unavailable"}
    await service.sync_pull_request_unverified_criteria(**payload)
    comments[:0] = [{"id": i + 100, "body": f"Human comment {i}"} for i in range(100)]
    assert await service.sync_pull_request_unverified_criteria(**payload) is False
    assert sum(request.method == "POST" for request in requests) == 1
    assert any(request.url.params.get("page") == "2" for request in requests)


@pytest.mark.asyncio
async def test_large_comment_history_uses_bounded_recent_window_and_adopts_retry(monkeypatch):
    existing = [{"id": i + 1, "body": f"Human comment {i}"} for i in range(1000)]
    service, document, comments, requests = wire(monkeypatch, "Original body", comments=existing)
    payload = {"repo": "o/r", "pr_url": "https://github.com/o/r/pull/7", "section": "GPU unavailable"}
    assert await service.sync_pull_request_unverified_criteria(**payload) is True
    assert len(comments) == 1001
    pages = [int(request.url.params["page"]) for request in requests if request.method == "GET" and request.url.path.endswith("/comments")]
    assert pages == [1, 10, 9, 8, 7, 6]
    assert await service.sync_pull_request_unverified_criteria(**payload) is False
    assert len(comments) == 1001
    assert document["body"] == "Original body"


@pytest.mark.asyncio
async def test_head_change_while_reading_comments_prevents_publication(monkeypatch):
    def move_head(request, document, comments):
        if request.url.path.endswith("/comments"):
            document["head"]["sha"] = "b" * 40

    service, _, comments, _ = wire(monkeypatch, "Original body", on_request=move_head)
    with pytest.raises(ValueError, match="head"):
        await service.sync_pull_request_unverified_criteria(
            repo="o/r", pr_url="https://github.com/o/r/pull/7", section="GPU unavailable",
            expected_head_sha=HEAD_SHA,
        )
    assert comments == []


@pytest.mark.asyncio
async def test_racing_disclosures_only_append_and_preserve_human_text(monkeypatch):
    service = GitHubService()
    service.resolve_github_token = AsyncMock(return_value=("token", None))
    comments = [{"id": 1, "body": "Human review"}]
    document = {"state": "open", "body": "Human introduction", "head": {"sha": HEAD_SHA}}
    readers = 0
    both_read = asyncio.Event()

    async def respond(request):
        nonlocal readers
        if request.url.path.endswith("/comments"):
            if request.method == "GET":
                snapshot = [dict(comment) for comment in comments]
                readers += 1
                if readers == 2:
                    both_read.set()
                await both_read.wait()
                return httpx.Response(200, json=snapshot)
            assert request.method == "POST"
            document["body"] = "Concurrent human introduction"
            comments[0]["body"] = "Concurrent human review"
            comment = {"id": len(comments) + 1, **json.loads(request.content)}
            comments.append(comment)
            return httpx.Response(201, json=comment)
        assert request.method == "GET"
        return httpx.Response(200, json=document)

    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs))
    payload = {"repo": "o/r", "pr_url": "https://github.com/o/r/pull/7", "section": "GPU unavailable", "expected_head_sha": HEAD_SHA}
    assert await asyncio.gather(*(service.sync_pull_request_unverified_criteria(**payload) for _ in range(2))) == [True, True]
    assert len(comments) == 3
    assert comments[1]["body"] == comments[2]["body"]
    assert document["body"] == "Concurrent human introduction"
    assert comments[0]["body"] == "Concurrent human review"


@pytest.mark.asyncio
async def test_superseded_or_human_edited_disclosure_is_not_adopted(monkeypatch):
    service, document, comments, _ = wire(monkeypatch, "Human introduction")
    payload = {"repo": "o/r", "pr_url": "https://github.com/o/r/pull/7", "expected_head_sha": HEAD_SHA}
    for section in ["GPU unavailable", "", "GPU unavailable"]:
        assert await service.sync_pull_request_unverified_criteria(**payload, section=section) is True
    assert len(comments) == 3
    assert comments[0]["body"] == comments[2]["body"]
    comments[2]["body"] += "\nHuman clarification to preserve."
    edited = comments[2]["body"]
    assert await service.sync_pull_request_unverified_criteria(**payload, section="GPU unavailable") is True
    assert len(comments) == 4
    assert comments[2]["body"] == edited
    assert comments[3]["body"] == comments[0]["body"]
    assert document["body"] == "Human introduction"


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [{}, [None], [{"body": None}], [{"body": 17}]])
async def test_malformed_comment_history_does_not_authorize_write(monkeypatch, payload):
    def malformed(request, document, comments):
        if request.url.path.endswith("/comments"):
            return httpx.Response(200, json=payload)

    service, _, comments, requests = wire(monkeypatch, "Human introduction", on_request=malformed)
    with pytest.raises(ValueError, match="history"):
        await service.sync_pull_request_unverified_criteria(
            repo="o/r", pr_url="https://github.com/o/r/pull/7", section="GPU unavailable",
        )
    assert comments == []
    assert all(request.method == "GET" for request in requests)


@pytest.mark.asyncio
async def test_comment_pagination_never_forwards_credentials_to_link_host(monkeypatch):
    def external_link(request, document, comments):
        if request.url.params.get("page") == "1":
            return httpx.Response(200, json=[], headers={
                "Link": '<https://untrusted.test/steal?page=2>; rel="next", <https://untrusted.test/steal?page=2>; rel="last"',
            })

    service, _, comments, requests = wire(monkeypatch, "Human introduction", on_request=external_link)
    assert await service.sync_pull_request_unverified_criteria(
        repo="o/r", pr_url="https://github.com/o/r/pull/7", section="GPU unavailable",
    ) is True
    assert len(comments) == 1
    assert all(request.url.host == "api.github.com" for request in requests)
    assert any(request.url.params.get("page") == "2" for request in requests)


@pytest.mark.asyncio
@pytest.mark.parametrize("last", ["", "?page=-1", "?page=two", "?page=2&page=3"])
async def test_malformed_last_page_hint_does_not_authorize_write(monkeypatch, last):
    def bad_pagination(request, document, comments):
        if request.url.path.endswith("/comments"):
            return httpx.Response(200, json=[], headers={
                "Link": '<https://api.github.com/?page=2>; rel="next", '
                f'<https://api.github.com/{last}>; rel="last"',
            })

    service, _, comments, requests = wire(monkeypatch, "Human introduction", on_request=bad_pagination)
    with pytest.raises(ValueError, match="pagination"):
        await service.sync_pull_request_unverified_criteria(
            repo="o/r", pr_url="https://github.com/o/r/pull/7", section="GPU unavailable",
        )
    assert comments == []
    assert all(request.method == "GET" for request in requests)


@pytest.mark.asyncio
async def test_native_multiline_body_remains_unchanged(monkeypatch):
    section = "## Unverified criteria\n\n- GPU: unavailable\n## Details\nHost missing. Verifiable by: GPU CI"
    body = "Human introduction.\n\n" + section + "\n\n## Review notes\nKeep this."
    service, document, comments, _ = wire(monkeypatch, body)
    for _ in range(2):
        await service.sync_pull_request_unverified_criteria(
            repo="o/r", pr_url="https://github.com/o/r/pull/7", section=section,
        )
    assert document["body"] == body
    assert len(comments) == 1
    assert section in comments[0]["body"]
