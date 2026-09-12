"""Replay the label-only claim incident across the real tool/artifact/HTTP seams."""

from __future__ import annotations

import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock
import json
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from temporalio.testing import ActivityEnvironment

from api_service.db.models import Base
from moonmind.workflows.adapters.github_service import GitHubService
from moonmind.workflows.skills.artifact_store import InMemoryArtifactStore
from moonmind.workflows.skills.skill_dispatcher import SkillActivityDispatcher
from moonmind.workflows.skills.skill_registry import (
    create_registry_snapshot,
    parse_skill_registry,
)
from moonmind.workflows.temporal import story_output_tools as tools
from moonmind.workflows.temporal.activity_runtime import (
    TemporalSkillActivities,
    _default_registry_skill_payload,
)
from moonmind.workflows.temporal.artifacts import (
    LocalTemporalArtifactStore,
    TemporalArtifactRepository,
    TemporalArtifactService,
)
from moonmind.workflows.temporal.github_issue_attempts import (
    AttemptHandoff,
    parse_attempt_comment,
    render_attempt_comment,
)

from moonmind.workflows.temporal.issue_claim_store import IssueClaimStore

REPO = "MoonLadderStudios/MoonMind"
FIXTURE = Path(__file__).parent / "fixtures/github_issue_claim_label_only.json"


@pytest_asyncio.fixture
async def journey(tmp_path, monkeypatch):
    fixture = json.loads(FIXTURE.read_text())
    issue = copy.deepcopy(fixture["issueAtSelection"])
    occupied = {
        **copy.deepcopy(issue),
        "number": 4280,
        "html_url": f"https://github.com/{REPO}/issues/4280",
        "labels": [{"name": "status: in-progress"}],
    }
    state = {"comments": [], "writes": [], "read_error": False, "create_error": False}

    def serve(request):
        path = request.url.path.casefold()
        if request.method == "GET":
            if path == "/user":
                return httpx.Response(200, json={**issue["user"], "type": "User"})
            if path.endswith("/comments"):
                return httpx.Response(
                    503 if state["read_error"] else 200, json=state["comments"]
                )
            if path == f"/repos/{REPO.casefold()}/issues":
                return httpx.Response(200, json=[occupied, issue])
            if path == "/search/issues":
                return httpx.Response(
                    200, json={"incomplete_results": False, "items": [occupied, issue]}
                )
            if "/labels/" in path:
                return httpx.Response(200, json={"name": path.rsplit("/", 1)[1]})
            if path == f"/repos/{REPO.casefold()}/issues/4271":
                return httpx.Response(200, json=issue)
        if request.method == "POST":
            body = json.loads(request.content)
            state["writes"].append((path, body))
            if path.endswith("/labels"):
                issue["labels"] = [{"name": name} for name in body["labels"]]
                return httpx.Response(200, json=issue["labels"])
            if path.endswith("/comments"):
                if state["create_error"]:
                    return httpx.Response(403, json={"message": "denied"})
                comment = {"id": 1, "body": body["body"], "user": issue["user"]}
                state["comments"].append(comment)
                if state.get("lost_response"):
                    raise httpx.ReadTimeout(
                        "response lost after creation", request=request
                    )
                return httpx.Response(201, json=comment)
        raise AssertionError(f"Unexpected provider request: {request.method} {path}")

    original_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda *a, **kw: original_client(
            *a, transport=httpx.MockTransport(serve), **kw
        ),
    )

    async def token(self, *args, **kwargs):
        return "fixture-credential", None

    monkeypatch.setattr(GitHubService, "resolve_github_token", token)
    monkeypatch.setenv("MOONMIND_INSTALLATION_ID", "inst-claim-replay")
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/artifacts.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(tools, "IssueClaimStore", lambda: IssueClaimStore(sessions))
    # ActivityEnvironment supplies real native identity. Only Temporal's
    # ancestry lookup is simulated at its service boundary (a root workflow).
    from temporalio.api.workflowservice.v1 import DescribeWorkflowExecutionResponse

    monkeypatch.setattr(
        "moonmind.workflows.temporal.client.get_temporal_client",
        AsyncMock(
            return_value=SimpleNamespace(
                workflow_service=SimpleNamespace(
                    describe_workflow_execution=AsyncMock(
                        return_value=DescribeWorkflowExecutionResponse()
                    )
                )
            )
        ),
    )
    try:
        async with sessions() as session:
            artifacts = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "store"),
            )
            dispatcher = SkillActivityDispatcher()
            tools.register_story_output_tool_handlers(dispatcher)
            names = [
                tools.GITHUB_LOAD_ISSUE_PRESET_BRIEF_TOOL_NAME,
                tools.GITHUB_CHECK_ISSUE_BLOCKERS_TOOL_NAME,
                tools.GITHUB_UPDATE_ISSUE_STATUS_TOOL_NAME,
            ]
            snapshot = create_registry_snapshot(
                skills=parse_skill_registry(
                    {"skills": [_default_registry_skill_payload(name=n) for n in names]}
                ),
                artifact_store=InMemoryArtifactStore(),
            )
            activities = TemporalSkillActivities(
                dispatcher=dispatcher, artifact_service=artifacts
            )
            environment = ActivityEnvironment()

            async def invoke(name, inputs):
                # The worker-bound keyword shape persists the brief through the
                # real artifact service; later steps receive only its ref.
                result = await environment.run(
                    activities.mm_tool_execute,
                    invocation_payload={
                        "id": name,
                        "tool": {"name": name},
                        "inputs": inputs,
                    },
                    registry_snapshot=snapshot,
                    principal="fixture-owner",
                )
                state["receipt"] = await IssueClaimStore(sessions).active_for_issue(
                    REPO, 4271
                )
                return result

            yield state, issue, invoke, fixture, artifacts
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "search",
    [
        {"issueSearch": " "},
        {"issueSearch": ""},
        {"issueSearch": "", "includeAllAuthors": False},
        {"issueSearch": "skills"},
        {"issueSearch": "", "includeAllAuthors": True},
    ],
)
async def test_default_search_claim_survives_assessment_and_blocker_handoffs(
    journey, search
):
    state, issue, invoke, fixture, artifacts = journey
    loaded = await invoke(
        tools.GITHUB_LOAD_ISSUE_PRESET_BRIEF_TOOL_NAME, {"repository": REPO, **search}
    )
    assert loaded.status == "COMPLETED", loaded.outputs
    assert loaded.outputs["issue"]["number"] == 4271
    assert loaded.outputs["searchEvidence"]["candidatesExamined"] == 2
    claim = loaded.outputs["admissionClaimExecuted"]
    assert claim["rereadOk"] and not claim["blocked"]
    assert len(state["comments"]) == 1
    receipt = state["receipt"]
    assert receipt.confirmed
    assert loaded.outputs["attemptId"] == receipt.attempt_id
    assert (
        parse_attempt_comment(receipt.comment_body).handoff.workflow_id == receipt.owner
    )
    assert issue["labels"] == [{"name": "status: in-progress"}]
    _, persisted = await artifacts.read(
        artifact_id=loaded.outputs["briefArtifactRef"],
        principal="fixture-owner",
        allow_restricted_raw=True,
    )
    assert json.loads(persisted)["admissionClaimExecuted"] == claim
    checked = await invoke(
        tools.GITHUB_CHECK_ISSUE_BLOCKERS_TOOL_NAME,
        {
            "repository": REPO,
            "issueNumber": 4271,
            "assessmentVerdict": "PARTIALLY_IMPLEMENTED",
        },
    )
    assert checked.outputs["decision"] == "continue"
    previous = {
        **checked.outputs,
        "briefArtifactRef": loaded.outputs["briefArtifactRef"],
    }
    before = copy.deepcopy(state["writes"])
    for _ in range(2):
        started = await invoke(
            tools.GITHUB_UPDATE_ISSUE_STATUS_TOOL_NAME,
            {
                "repository": REPO,
                "issueNumber": 4271,
                "mode": "start",
                "previousOutputs": previous,
            },
        )
        assert started.status == "COMPLETED", started.outputs
        assert started.outputs["decision"] == "already_applied"
    assert state["writes"] == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "damage",
    [
        "missing",
        "untrusted",
        "foreign",
        "competing",
        "stopped",
        "held",
        "unknown",
        "duplicate",
        "read_error",
    ],
)
async def test_owned_label_never_bypasses_live_ownership_evidence(journey, damage):
    state, issue, invoke, _, _ = journey
    loaded = await invoke(
        tools.GITHUB_LOAD_ISSUE_PRESET_BRIEF_TOOL_NAME,
        {"repository": REPO, "issueSearch": ""},
    )
    assert loaded.status == "COMPLETED", loaded.outputs
    handoff = parse_attempt_comment(state["receipt"].comment_body).handoff.to_dict()
    if damage == "missing":
        state["comments"].clear()
    elif damage == "untrusted":
        state["comments"][0]["user"] = {"login": "untrusted"}
    elif damage == "read_error":
        state["read_error"] = True
    else:
        if damage in {"competing", "duplicate"}:
            state["comments"].append(copy.deepcopy(state["comments"][0]))
            state["comments"][-1]["id"] = 2
        if damage == "foreign":
            handoff["workflowId"] = "another-workflow"
        elif damage == "competing":
            handoff["attemptId"] = "att-another-attempt"
        elif damage == "stopped":
            handoff["writersStopped"] = True
        elif damage == "held":
            handoff["operatorHold"] = True
        elif damage == "unknown":
            handoff["activity"] = "future-state"
        elif damage == "duplicate":
            handoff["lastReport"] = "conflicting report"
        state["comments"][-1]["body"] = render_attempt_comment(
            AttemptHandoff.from_dict(handoff)
        )
    before = copy.deepcopy(state["writes"])
    result = await invoke(
        tools.GITHUB_UPDATE_ISSUE_STATUS_TOOL_NAME,
        {
            "repository": REPO,
            "issueNumber": 4271,
            "mode": "start",
            "previousOutputs": {"briefArtifactRef": loaded.outputs["briefArtifactRef"]},
        },
    )
    assert result.status == "FAILED", result.outputs
    assert state["writes"] == before
    assert issue["labels"] == [{"name": "status: in-progress"}]


@pytest.mark.asyncio
async def test_label_only_history_and_arbitrary_attempt_id_do_not_grant_ownership(
    journey,
):
    state, issue, invoke, fixture, _ = journey
    issue["labels"] = [{"name": "status: in-progress"}]
    result = await invoke(
        tools.GITHUB_UPDATE_ISSUE_STATUS_TOOL_NAME,
        {
            "repository": REPO,
            "issueNumber": 4271,
            "mode": "start",
            "attemptId": "unproven-owner",
            "previousOutputs": fixture["legacyBrief"],
        },
    )
    assert result.status == "FAILED"
    assert result.outputs["reasonCode"] == "manual_in_progress_without_trusted_owner"
    assert state["writes"] == []


@pytest.mark.asyncio
async def test_rejected_announcement_cannot_release_successful_brief(journey):
    state, _, invoke, _, _ = journey
    state["create_error"] = True
    result = await invoke(
        tools.GITHUB_LOAD_ISSUE_PRESET_BRIEF_TOOL_NAME,
        {"repository": REPO, "issueSearch": ""},
    )
    assert result.status == "FAILED"
    assert "briefArtifactRef" not in result.outputs
    assert result.outputs["admissionClaimExecuted"]["blocked"]


@pytest.mark.asyncio
async def test_lost_comment_response_is_confirmed_without_duplicate_creation(journey):
    state, _, invoke, _, _ = journey
    state["lost_response"] = True
    loaded = await invoke(
        tools.GITHUB_LOAD_ISSUE_PRESET_BRIEF_TOOL_NAME,
        {"repository": REPO, "issueSearch": ""},
    )
    assert loaded.status == "COMPLETED", loaded.outputs
    assert len(state["comments"]) == 1
    assert loaded.outputs["admissionClaimExecuted"]["rereadOk"]


@pytest.mark.asyncio
async def test_another_execution_cannot_adopt_the_durable_brief(journey, monkeypatch):
    state, _, invoke, _, _ = journey
    loaded = await invoke(
        tools.GITHUB_LOAD_ISSUE_PRESET_BRIEF_TOOL_NAME,
        {"repository": REPO, "issueSearch": ""},
    )
    assert loaded.status == "COMPLETED", loaded.outputs
    monkeypatch.setattr(
        "temporalio.activity.info",
        lambda: SimpleNamespace(
            namespace="default",
            workflow_id="another-workflow",
            workflow_run_id="another-run",
        ),
    )
    before = copy.deepcopy(state["writes"])
    result = await invoke(
        tools.GITHUB_UPDATE_ISSUE_STATUS_TOOL_NAME,
        {
            "repository": REPO,
            "issueNumber": 4271,
            "mode": "start",
            "previousOutputs": {"briefArtifactRef": loaded.outputs["briefArtifactRef"]},
        },
    )
    assert result.status == "FAILED"
    assert state["writes"] == before
