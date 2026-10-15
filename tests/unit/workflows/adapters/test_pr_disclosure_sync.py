import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from moonmind.workflows.adapters.github_service import GitHubService
from moonmind.workflows.temporal.activities import jules_activities
from moonmind.workflows.temporal.workflows import run as run_module
from tests.unit.workflows.skills.test_acceptance_source_binding import (
    _disclose_unverified,
)

pytest_plugins = ("tests.unit.workflows.skills.test_acceptance_source_binding",)


def wire(monkeypatch, body):
    document = {"state": "open", "body": body}
    writes = []

    def respond(request):
        if request.method == "PATCH":
            payload = json.loads(request.content)
            writes.append(payload)
            document.update(payload)
        else:
            assert request.method == "GET"
        return httpx.Response(200, json=document)

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
    return service, document, writes


@pytest.mark.asyncio
async def test_managed_block_preserves_human_text_after_its_end(monkeypatch):
    body = (
        "Reviewer introduction.\n\n<!-- moonmind:unverified:start -->\n"
        "## Unverified criteria\n\n- OLD: old reason\n"
        "<!-- moonmind:unverified:end -->\n\n"
        "Reviewer note outside the managed block.\n\n## Validation\nKeep this."
    )
    service, document, _writes = wire(monkeypatch, body)
    await service.sync_pull_request_unverified_criteria(
        repo="o/r",
        pr_url="https://github.com/o/r/pull/7",
        section="## Unverified criteria\n\n- CURRENT: new reason",
    )
    assert "Reviewer note outside the managed block." in document["body"]
    assert "<!-- moonmind:unverified:end -->" in document["body"]


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
    service, document, writes = wire(monkeypatch, "Human introduction.")
    for _ in range(2):
        await service.sync_pull_request_unverified_criteria(
            repo="o/r", pr_url="https://github.com/o/r/pull/7", section=section
        )
    assert len(writes) == 1, document["body"]
    assert document["body"].count("The provider is inaccessible.") == 1


@pytest.mark.asyncio
async def test_activity_honors_admitted_connection_denial(monkeypatch):
    service, _document, writes = wire(monkeypatch, "Human introduction.")
    service._repository_reader = AsyncMock(
        side_effect=ValueError("selected connection denied")
    )
    monkeypatch.setattr(
        "moonmind.workflows.adapters.github_service.GitHubService", lambda: service
    )
    monkeypatch.setattr(jules_activities.activity, "in_activity", lambda: True)
    monkeypatch.setattr(
        jules_activities.activity,
        "info",
        lambda: SimpleNamespace(workflow_id="selected-workflow"),
    )
    with pytest.raises(ValueError, match="selected connection denied"):
        await jules_activities.repo_sync_pr_unverified_criteria_activity(
            {
                "repo": "o/r",
                "prUrl": "https://github.com/o/r/pull/7",
                "section": "## Unverified criteria\n\n- GPU: Hardware unavailable",
            }
        )
    assert writes == []
    service.resolve_github_token.assert_not_awaited()


@pytest.mark.asyncio
async def test_fresh_workflow_clears_stale_managed_disclosure(
    monkeypatch, source_bound_report
):
    parent = run_module.MoonMindRunWorkflow()
    parent._repo = "example/repo"
    parent._publish_context["moonSpecGate"] = source_bound_report
    monkeypatch.setattr(run_module.workflow, "patched", lambda _: True)
    service, document, _writes = wire(
        monkeypatch,
        "Human introduction.\n\n## Unverified criteria\n\n- OLD: obsolete reason\n\n## Validation\nKeep this.",
    )

    async def execute(name, payload, **kwargs):
        assert name == "repo.sync_pr_unverified_criteria"
        return await service.sync_pull_request_unverified_criteria(
            repo=payload["repo"], pr_url=payload["prUrl"], section=payload["section"]
        )

    monkeypatch.setattr(
        run_module.workflow, "execute_activity", AsyncMock(side_effect=execute)
    )
    await parent._sync_pr_unverified_criteria("https://github.com/example/repo/pull/7")
    assert "OLD" not in document["body"]
    assert "Keep this." in document["body"]


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
    document = {"state": "open", "body": "Human introduction."}
    requests = []

    def respond(request):
        requests.append(request)
        assert str(request.url) == "https://git.example.test/api/v3/repos/o/r/pulls/7"
        assert request.headers["authorization"] == "Bearer selected-token"
        if request.method == "PATCH":
            document.update(json.loads(request.content))
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
    monkeypatch.setattr(jules_activities.activity, "in_activity", lambda: True)
    monkeypatch.setattr(
        jules_activities.activity,
        "info",
        lambda: SimpleNamespace(workflow_id="selected-workflow"),
    )
    await jules_activities.repo_sync_pr_unverified_criteria_activity(
        {
            "repo": "o/r",
            "prUrl": "https://git.example.test/o/r/pull/7",
            "section": "## Unverified criteria\n\n- GPU: Hardware unavailable",
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
    assert len(requests) == 2


@pytest.mark.asyncio
async def test_native_multiline_section_is_adopted_without_orphaned_text(monkeypatch):
    section = "## Unverified criteria\n\n- GPU: unavailable\n## Details\nHost missing. Verifiable by: GPU CI"
    body = "Human introduction.\n\n" + section + "\n\n## Review notes\nKeep this."
    service, document, writes = wire(monkeypatch, body)
    for _ in range(2):
        await service.sync_pull_request_unverified_criteria(
            repo="o/r", pr_url="https://github.com/o/r/pull/7", section=section
        )
    assert document["body"].count("Host missing.") == 1
    assert document["body"].endswith("## Review notes\nKeep this.")
    assert len(writes) <= 1


@pytest.mark.asyncio
async def test_lost_ack_sync_retry_reads_before_mutating(monkeypatch):
    service = GitHubService()
    service.resolve_github_token = AsyncMock(return_value=("default-token", None))
    service._repository_reader = AsyncMock(
        return_value=(
            "https://api.github.com",
            {"Authorization": "Bearer selected-token"},
        )
    )
    document = {"state": "open", "body": "Human introduction."}
    writes = []

    def respond(request):
        if request.method == "PATCH":
            document.update(json.loads(request.content))
            writes.append(document["body"])
            raise httpx.ReadError("successful patch lost its acknowledgment")
        return httpx.Response(200, json=document)

    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs),
    )
    payload = {
        "repo": "o/r",
        "pr_url": "https://github.com/o/r/pull/7",
        "section": "## Unverified criteria\n\n- GPU: unavailable\n## Detail\nStill unavailable",
    }
    with pytest.raises(httpx.ReadError):
        await service.sync_pull_request_unverified_criteria(**payload)
    assert await service.sync_pull_request_unverified_criteria(**payload) is False
    assert len(writes) == 1
