"""Replay the issue-search preset's catalog → Activity → trusted-context handoff."""

from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import Base
from api_service.services.presets.catalog import PresetCatalogService
from moonmind.workflows.adapters.github_service import GitHubService
from moonmind.workflows.skills.tool_dispatcher import ToolActivityDispatcher
from moonmind.workflows.skills.tool_plan_contracts import parse_tool_definition
from moonmind.workflows.skills.tool_registry import ToolRegistrySnapshot
from moonmind.workflows.temporal.activity_runtime import (
    TemporalSkillActivities,
    _default_registry_skill_payload,
)
from moonmind.workflows.temporal.story_output_tools import (
    register_story_output_tool_handlers,
)
from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow

REPOSITORY = "MoonLadderStudios/MoonMind"
PRESET = "github-issue-search-and-implement"


def issue(number=4025, **overrides):
    # Minimized incident mm:66e24e5f: search selected #4025 but the following
    # native load Activity received repository="" and no issueNumber.
    return {
        "number": number,
        "state": "open",
        "title": "Dashboard update stream",
        "body": "Reconcile advertised routes and bounded polling recovery.",
        "html_url": f"https://github.com/{REPOSITORY}/issues/{number}",
        "labels": [{"name": "bug"}],
        **overrides,
    }


@pytest.fixture
def activity_boundary(monkeypatch):
    requests = []
    pages = [[issue()]]
    detail = issue()

    def handler(request):
        requests.append(request)
        if request.url.path.endswith("/issues"):
            payload = pages[int(request.url.params["page"]) - 1]
            if request.url.path == "/search/issues" and isinstance(payload, list):
                payload = {"items": payload, "incomplete_results": False}
        else:
            payload = detail
        return httpx.Response(200, json=payload)

    client_type = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: client_type(transport=httpx.MockTransport(handler), **kwargs),
    )
    monkeypatch.setattr(
        GitHubService,
        "resolve_github_token",
        AsyncMock(return_value=("test-only", None)),
    )
    dispatcher = ToolActivityDispatcher()
    register_story_output_tool_handlers(dispatcher)
    names = (
        "github.load_issue_preset_brief",
        "github.check_issue_blockers",
        "github.update_issue_status",
    )
    snapshot = ToolRegistrySnapshot(
        digest="reg:sha256:incident66e24e5f",
        artifact_ref="art_registry",
        skills=tuple(
            parse_tool_definition(_default_registry_skill_payload(name=name))
            for name in names
        ),
    )
    artifact_service = SimpleNamespace(
        read=AsyncMock(return_value=(None, {"verdict": "NOT_IMPLEMENTED"}))
    )
    activities = TemporalSkillActivities(
        dispatcher=dispatcher, artifact_service=artifact_service
    )

    async def execute(name, inputs):
        return await activities.mm_tool_execute(
            registry_snapshot=snapshot,
            principal="test:issue-search",
            invocation_payload={
                "id": "incident-step",
                "tool": {"type": "skill", "name": name},
                "inputs": inputs,
            },
            context={
                "namespace": "default",
                "workflow_id": "mm:66e24e5f",
                "run_id": "run",
                "node_id": "step",
            },
            idempotency_key="incident-step:execute",
        )

    return SimpleNamespace(
        execute=execute, pages=pages, requests=requests, detail=detail
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "inputs", [{}, {"repository": "", "issue_search": ""}, {"repository": REPOSITORY}]
)
async def test_default_preset_resolves_and_preserves_issue_across_agent_steps(
    tmp_path, activity_boundary, inputs
):
    seeds = tmp_path / "seeds"
    seeds.mkdir()
    shutil.copy(
        Path(__file__).resolve().parents[3]
        / "api_service/data/presets"
        / f"{PRESET}.yaml",
        seeds,
    )
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/catalog.db")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with sessionmaker(engine, class_=AsyncSession)() as session:
            catalog = PresetCatalogService(session)
            await catalog.sync_seed_templates(seed_dir=seeds)
            expanded = await catalog.expand_template(
                slug=PRESET,
                scope="global",
                scope_ref=None,
                inputs=inputs,
                context={"repository": REPOSITORY},
            )
    finally:
        await engine.dispose()
    steps = expanded["steps"]
    assert steps[0]["type"] == "tool"
    tool = steps[0]["tool"]
    result = await activity_boundary.execute(tool["id"], tool["inputs"])
    assert result.status == "COMPLETED"
    assert result.outputs["issue"]["number"] == 4025
    assert result.outputs["searchEvidence"] == {
        "fallbackScanning": True,
        "pagesExamined": 1,
        "candidatesExamined": 1,
    }
    workflow = MoonMindRunWorkflow()
    workflow._record_trusted_issue_context(result.outputs)
    previous = workflow._merge_trusted_issue_context(
        {
            "summary": "Omnigent session completed",
            "assessmentArtifactRef": "art_assessment",
        }
    )
    assert previous["searchEvidence"] == result.outputs["searchEvidence"]
    # No local workspace, issue-number injection, or assistant-text parsing.
    for step in steps[2:4]:
        tool = step["tool"]
        result = await activity_boundary.execute(
            tool["id"], {**tool["inputs"], "previousOutputs": previous}
        )
        assert result.status == "COMPLETED"
        assert (
            result.outputs.get("issueRef") == f"{REPOSITORY}#4025"
            or result.outputs.get("issueUrl") == issue()["html_url"]
        )
        workflow._record_assessment_context(result.outputs)
        previous = workflow._merge_trusted_issue_context(result.outputs)
        assert previous["searchEvidence"]["candidatesExamined"] == 1
        assert previous["searchEvidence"]["pagesExamined"] == 1
    patches = [
        request for request in activity_boundary.requests if request.method == "PATCH"
    ]
    assert len(patches) == 1
    assert patches[0].url.path == f"/repos/{REPOSITORY}/issues/4025"
    # Finalization still requires its verification / PR evidence before mutation.
    final_tool = steps[-1]["tool"]
    final = await activity_boundary.execute(
        final_tool["id"], {**final_tool["inputs"], "previousOutputs": previous}
    )
    assert final.status == "FAILED"
    assert (
        len(
            [
                request
                for request in activity_boundary.requests
                if request.method == "PATCH"
            ]
        )
        == 1
    )


@pytest.mark.asyncio
async def test_search_pagination_skips_blocked_issues_and_pull_requests(
    activity_boundary,
):
    activity_boundary.pages[:] = [
        [issue(n, labels=[{"name": "blocked"}]) for n in range(100, 200)],
        [issue(200, pull_request={}), issue()],
    ]
    result = await activity_boundary.execute(
        "github.load_issue_preset_brief", {"repository": REPOSITORY, "issueSearch": ""}
    )
    assert result.status == "COMPLETED"
    assert result.outputs["issue"]["number"] == 4025
    assert result.outputs["searchEvidence"]["pagesExamined"] == 2
    assert result.outputs["searchEvidence"]["candidatesExamined"] == 102


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "pages, query, expected",
    [
        ([[]], "", "exhausted"),
        ([[issue(labels=[{"name": "blocked"}])] * 100] * 5, "", "500-candidate"),
        ([{"items": [issue()], "incomplete_results": True}], "dashboard", "incomplete"),
        ([[issue(state="unknown")]], "", "invalid"),
        (
            [[issue(html_url="https://github.com/other/repo/issues/4025")]],
            "dashboard",
            "invalid",
        ),
        ([[issue(labels=None)]], "", "invalid"),
    ],
)
async def test_search_stops_on_empty_bounded_or_degraded_evidence(
    activity_boundary, pages, query, expected
):
    activity_boundary.pages[:] = pages
    result = await activity_boundary.execute(
        "github.load_issue_preset_brief",
        {"repository": REPOSITORY, "issueSearch": query},
    )
    assert result.status == "FAILED"
    assert expected in result.outputs["error"]
    assert not any(request.method != "GET" for request in activity_boundary.requests)


@pytest.mark.asyncio
async def test_repository_scope_is_case_insensitive(activity_boundary):
    result = await activity_boundary.execute(
        "github.load_issue_preset_brief",
        {"repository": REPOSITORY.lower(), "issueSearch": ""},
    )
    assert result.status == "COMPLETED"
    assert result.outputs["issue"]["number"] == 4025


@pytest.mark.asyncio
async def test_query_search_and_previous_explicit_issue_payload(activity_boundary):
    result = await activity_boundary.execute(
        "github.load_issue_preset_brief",
        {"repository": REPOSITORY, "issueSearch": "dashboard"},
    )
    assert result.status == "COMPLETED"
    assert (
        activity_boundary.requests[0].url.params["q"]
        == f"dashboard repo:{REPOSITORY} is:issue is:open"
    )
    activity_boundary.requests.clear()
    # Existing persisted explicit issue inputs keep their original direct lookup.
    result = await activity_boundary.execute(
        "github.load_issue_preset_brief",
        {"repository": REPOSITORY, "issueNumber": 4025},
    )
    assert result.status == "COMPLETED"
    assert len(activity_boundary.requests) == 1
    assert "searchEvidence" not in result.outputs
    workflow = MoonMindRunWorkflow()
    workflow._record_trusted_issue_context(result.outputs)
    previous = workflow._merge_trusted_issue_context({"summary": "Legacy agent result"})
    assert "searchEvidence" not in previous
    assert previous["issue"]["number"] == 4025


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changed", [{"state": "closed"}, {"number": 99}, {"labels": [{"name": "blocked"}]}]
)
async def test_selected_issue_is_revalidated_before_loading_brief(
    activity_boundary, changed
):
    activity_boundary.detail.update(changed)
    result = await activity_boundary.execute(
        "github.load_issue_preset_brief", {"repository": REPOSITORY, "issueSearch": ""}
    )
    assert result.status == "FAILED"
    assert "could not be confirmed" in result.outputs["error"]
    assert "trustedSource" not in result.outputs


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["title", "body", "labels"])
@pytest.mark.parametrize("mode", ["missing", "malformed"])
async def test_incomplete_selected_details_cannot_produce_a_trusted_brief(
    activity_boundary, field, mode
):
    if mode == "missing":
        del activity_boundary.detail[field]
    else:
        activity_boundary.detail[field] = {"unexpected": "shape"}
    result = await activity_boundary.execute(
        "github.load_issue_preset_brief", {"repository": REPOSITORY, "issueSearch": ""}
    )
    assert result.status == "FAILED"
    assert "trustedSource" not in result.outputs
    assert "presetBrief" not in result.outputs
    assert not any(request.method != "GET" for request in activity_boundary.requests)


def test_compact_issue_context_preserves_search_evidence():
    import json

    evidence = {"fallbackScanning": True, "pagesExamined": 5, "candidatesExamined": 499}
    context = {
        "trustedSource": "moonmind.github.get_issue",
        "searchEvidence": evidence,
        "issue": {"repository": REPOSITORY, **issue()},
        **{key: "x" * 30000 for key in ("title", "body", "summary", "presetBrief")},
    }
    payload, truncated = MoonMindRunWorkflow._trusted_context_payload(context)
    assert truncated
    assert json.loads(payload)["searchEvidence"] == evidence


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "previous, repository",
    [
        ({"issue": issue()}, REPOSITORY),
        (
            {
                "trustedSource": "moonmind.github.get_issue",
                "issue": {**issue(), "repository": REPOSITORY},
            },
            "other/repo",
        ),
    ],
)
async def test_identity_handoff_rejects_untrusted_or_conflicting_issue(
    activity_boundary, previous, repository
):
    with pytest.raises(Exception, match="required|conflict"):
        await activity_boundary.execute(
            "github.update_issue_status",
            {"repository": repository, "previousOutputs": previous, "mode": "start"},
        )
    assert activity_boundary.requests == []
