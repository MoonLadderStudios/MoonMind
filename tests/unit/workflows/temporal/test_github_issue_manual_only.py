"""Issues whose remaining work needs more than a Linux container become manual-only.

The assessment declares it; the trusted blocker tool records it on GitHub and
ends the run without implementation; selection skips the labelled issue until a
person removes the label. Only the GitHub HTTP boundary is replaced.
"""

from __future__ import annotations

import json
from urllib.parse import unquote

import httpx
import pytest

from moonmind.workflows.adapters.github_service import GitHubService
from moonmind.workflows.temporal import story_output_tools as tools
from tests.unit.workflows.temporal import test_issue_claim_journey as claim_journey

# Reuse the claim journey's real GitHub HTTP fixture for selection tests.
journey = claim_journey.journey

REPOSITORY = "example/repo"
ISSUE_NUMBER = 2713
MANUAL_ONLY = {
    "reason": "Every unmet requirement needs rendered timings from both Windows D3D12 event PCs.",
    "manualActions": [
        "Package the Windows build and record per-stage timings on both event PCs.",
        "Attach the timing logs and provenance to this issue.",
    ],
}


class _AssessmentArtifacts:
    def __init__(self, payload):
        self.payload = payload

    async def read(self, *, artifact_id, principal, allow_restricted_raw=False):
        assert artifact_id == "art_assessment"
        return object(), json.dumps(self.payload).encode("utf-8")


@pytest.fixture
def github(monkeypatch):
    # comment_fault: "lost_ack" stores the comment then drops the response,
    # "dropped" drops the request, "denied" rejects it.
    state = {"labels": [], "comments": [], "repo_labels": [], "writes": [], "comment_fault": None}

    def handle(request: httpx.Request) -> httpx.Response:
        path = unquote(request.url.path)
        body = json.loads(request.content) if request.content else {}
        issue_path = f"/repos/{REPOSITORY}/issues/{ISSUE_NUMBER}"
        if request.method != "GET":
            state["writes"].append((request.method, path))
        if request.method == "GET" and path == issue_path:
            return httpx.Response(
                200,
                json={
                    "number": ISSUE_NUMBER,
                    "title": "Optimize loading on both rendered stations",
                    "body": "Measure cold and warm loads on both event PCs.",
                    "state": "open",
                    "user": {"id": 123, "login": "fixture-owner"},
                    "html_url": f"https://github.com/{REPOSITORY}/issues/{ISSUE_NUMBER}",
                    "labels": [{"name": name} for name in state["labels"]],
                },
            )
        if path == f"{issue_path}/comments":
            if request.method == "GET":
                return httpx.Response(200, json=state["comments"])
            fault = state["comment_fault"]
            if fault == "denied":
                return httpx.Response(403, json={"message": "Resource not accessible"})
            if fault == "dropped":
                raise httpx.ConnectTimeout("fixture dropped the request", request=request)
            comment = {"id": len(state["comments"]) + 1, "body": body["body"]}
            state["comments"].append(comment)
            if fault == "lost_ack":
                raise httpx.ReadTimeout("fixture lost the response", request=request)
            return httpx.Response(201, json=comment)
        if request.method == "POST" and path == f"/repos/{REPOSITORY}/labels":
            if body["name"] in state["repo_labels"]:
                return httpx.Response(422, json={"message": "already_exists"})
            state["repo_labels"].append(body["name"])
            return httpx.Response(201, json=body)
        if request.method == "POST" and path == f"{issue_path}/labels":
            state["labels"] = sorted(set(state["labels"]) | set(body["labels"]))
            return httpx.Response(200, json=[{"name": name} for name in state["labels"]])
        raise AssertionError(f"Unexpected GitHub request: {request.method} {path}")

    original_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original_client(transport=httpx.MockTransport(handle), **kwargs),
    )

    async def token(*_args, **_kwargs):
        return "fixture-only", None

    monkeypatch.setattr(GitHubService, "resolve_github_token", staticmethod(token))
    return state


async def _check_blockers(assessment):
    return await tools.check_github_issue_blockers(
        {
            "repository": REPOSITORY,
            "issueNumber": ISSUE_NUMBER,
            "assessmentArtifactPath": "artifacts/github-issue-implement-assessment.json",
            "assessmentArtifactRef": "art_assessment",
        },
        {
            "temporal_artifact_service": _AssessmentArtifacts(assessment),
            "execution_owner": "default/mm:manual-only-run",
        },
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("verdict", ["PARTIALLY_IMPLEMENTED", "NOT_IMPLEMENTED", "BLOCKED"])
async def test_manual_only_assessment_marks_issue_and_stops_without_failing(github, verdict):
    result = await _check_blockers({"verdict": verdict, "manualOnly": MANUAL_ONLY})

    assert result.status == "COMPLETED", result.outputs
    # No automatable work remains, so the run ends like an idle search rather
    # than failing or continuing into implementation.
    assert result.completion_disposition == "idle"
    assert result.outputs["decision"] == "blocked"
    assert result.outputs["manualOnly"]["manualActions"] == MANUAL_ONLY["manualActions"]
    assert "manual-only" in result.outputs["summary"]
    assert github["labels"] == ["manual-only"]
    assert len(github["comments"]) == 1
    comment = github["comments"][0]["body"]
    assert MANUAL_ONLY["reason"] in comment
    for action in MANUAL_ONLY["manualActions"]:
        assert action in comment

    # A retried Activity must not post the explanation twice.
    repeated = await _check_blockers({"verdict": verdict, "manualOnly": MANUAL_ONLY})
    assert repeated.status == "COMPLETED"
    assert repeated.completion_disposition == "idle"
    assert github["labels"] == ["manual-only"]
    assert len(github["comments"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "assessment",
    [
        {"verdict": "PARTIALLY_IMPLEMENTED"},
        {"verdict": "PARTIALLY_IMPLEMENTED", "manualOnly": None},
        {"verdict": "PARTIALLY_IMPLEMENTED", "manualOnly": False},
        # A completed implementation has no manual remainder to hand off.
        {"verdict": "FULLY_IMPLEMENTED", "manualOnly": MANUAL_ONLY},
        # Incomplete declarations cannot tell a person what to do, so they do
        # not exclude the issue from automation.
        {"verdict": "PARTIALLY_IMPLEMENTED", "manualOnly": True},
        {"verdict": "PARTIALLY_IMPLEMENTED", "manualOnly": {}},
        {"verdict": "PARTIALLY_IMPLEMENTED", "manualOnly": {"reason": MANUAL_ONLY["reason"]}},
        {"verdict": "PARTIALLY_IMPLEMENTED", "manualOnly": {"reason": " ", "manualActions": ["x"]}},
        {"verdict": "PARTIALLY_IMPLEMENTED", "manualOnly": {"reason": "x", "manualActions": [" ", 7]}},
    ],
)
async def test_automatable_assessment_continues_without_marking(github, assessment):
    result = await _check_blockers(assessment)

    assert result.status == "COMPLETED", result.outputs
    assert result.completion_disposition is None
    assert result.outputs["decision"] == "continue"
    assert github["writes"] == []


@pytest.mark.asyncio
async def test_search_skips_manual_only_issue_until_the_label_is_removed(journey):
    state, service, _sessions = journey
    state["labels"] = ["manual-only"]
    search = {"repository": "example/repo", "issueSearch": "", "includeAllAuthors": False}

    skipped = await tools.load_github_issue_preset_brief(
        search, {"execution_owner": "default/search-a"}, github_service_factory=lambda: service
    )
    assert skipped.status == "COMPLETED", skipped.outputs
    assert skipped.completion_disposition == "idle"
    assert state["posts"] == 0

    state["labels"] = []
    selected = await tools.load_github_issue_preset_brief(
        search, {"execution_owner": "default/search-b"}, github_service_factory=lambda: service
    )
    assert selected.status == "COMPLETED", selected.outputs
    assert selected.completion_disposition is None
    assert selected.outputs["issue"]["number"] == 3970


@pytest.mark.asyncio
async def test_lost_comment_acknowledgement_is_reconciled_before_labelling(github):
    github["comment_fault"] = "lost_ack"

    result = await _check_blockers({"verdict": "BLOCKED", "manualOnly": MANUAL_ONLY})

    assert result.status == "COMPLETED", result.outputs
    assert result.completion_disposition == "idle"
    assert len(github["comments"]) == 1
    assert github["labels"] == ["manual-only"]


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["dropped", "denied"])
async def test_issue_is_not_labelled_without_its_explanation(github, fault):
    github["comment_fault"] = fault

    result = await _check_blockers({"verdict": "BLOCKED", "manualOnly": MANUAL_ONLY})

    # The run stops rather than continuing into implementation, and a later
    # assessment can mark the issue once the explanation can be posted.
    assert result.status == "FAILED", result.outputs
    assert result.outputs["decision"] == "blocked"
    assert github["comments"] == []
    assert github["labels"] == []

    github["comment_fault"] = None
    retried = await _check_blockers({"verdict": "BLOCKED", "manualOnly": MANUAL_ONLY})
    assert retried.completion_disposition == "idle"
    assert len(github["comments"]) == 1
    assert github["labels"] == ["manual-only"]


@pytest.mark.asyncio
async def test_manual_only_blocker_result_completes_run_without_implementation(monkeypatch):
    """The workflow honors the blocker step's idle result mid-plan.

    Implementation never starts, publication is not required, and the run is
    not recorded as blocked (a blocked run fails its terminal state).
    """
    from datetime import datetime, timezone

    from moonmind.workflows.temporal.workflows import run as run_module
    from tests.unit.workflows.temporal.workflows.test_run_deterministic_tool_refs_973 import (
        _immediate_wait_condition,
        _mock_plan_payload,
        _normalize_payload,
        _tool_definition_payload,
    )

    manual_only_result = tools.ToolResult(
        status="COMPLETED",
        outputs={
            "decision": "blocked",
            "manualOnly": MANUAL_ONLY,
            "summary": f"Marked {REPOSITORY}#{ISSUE_NUMBER} manual-only: {MANUAL_ONLY['reason']}",
        },
        completion_disposition="idle",
    ).to_payload()
    names = {
        "load": "github.load_issue_preset_brief",
        "blockers": "github.check_issue_blockers",
        "implement": "github.update_issue_status",
    }
    dispatched: list[str] = []

    async def fake_execute_activity(activity_type, payload, **_kwargs):
        normalized = _normalize_payload(payload)
        if activity_type == "artifact.read":
            if normalized.get("artifact_ref") == "art:sha256:456":
                return json.dumps(
                    {"skills": [_tool_definition_payload(name) for name in names.values()]}
                ).encode("utf-8")
            return _mock_plan_payload(
                [
                    {"id": node, "tool": {"type": "skill", "name": name}, "inputs": {}}
                    for node, name in names.items()
                ],
                edges=[{"from": "load", "to": "blockers"}, {"from": "blockers", "to": "implement"}],
            )
        if activity_type == "mm.tool.execute":
            node = normalized["invocation_payload"]["id"]
            dispatched.append(node)
            if node == "blockers":
                return manual_only_result
            return {"status": "COMPLETED", "outputs": {"summary": f"{node} done"}}
        return {"status": "COMPLETED", "outputs": {}}

    enabled = {
        run_module.RUN_DETERMINISTIC_TOOL_REF_RESOLUTION_PATCH,
        run_module.RUN_BLOCKED_OUTCOME_SHORT_CIRCUIT_PATCH,
        "tool-idle-objective-outcome-v1",
    }
    monkeypatch.setattr(run_module.workflow, "patched", lambda patch_id: patch_id in enabled)
    monkeypatch.setattr(run_module.workflow, "execute_activity", fake_execute_activity)

    async def no_child_workflows(*_args, **_kwargs):
        raise AssertionError("manual-only issues must not start implementation")

    monkeypatch.setattr(run_module.workflow, "execute_child_workflow", no_child_workflows)
    monkeypatch.setattr(run_module.workflow, "upsert_memo", lambda _memo: None)
    monkeypatch.setattr(run_module.workflow, "upsert_search_attributes", lambda _attrs: None)
    monkeypatch.setattr(run_module.workflow, "wait_condition", _immediate_wait_condition)
    monkeypatch.setattr(run_module.workflow, "now", lambda: datetime.now(timezone.utc))
    monkeypatch.setattr(
        run_module.workflow,
        "info",
        type("Info", (), {"task_queue": "mm.workflow.user.v2", "namespace": "default", "workflow_id": "wf-1", "run_id": "run-1", "search_attributes": {}}),
    )
    monkeypatch.setattr(
        run_module.workflow,
        "logger",
        type("Logger", (), {"info": lambda *a, **k: None, "warning": lambda *a, **k: None}),
    )
    workflow = run_module.MoonMindRunWorkflow()
    workflow._owner_id = "owner-1"
    workflow._repo = REPOSITORY

    await workflow._run_execution_stage(parameters={}, plan_ref="art:sha256:plan")

    assert dispatched == ["load", "blockers"]
    assert workflow._plan_blocked_message is None
    assert workflow._publish_status == "not_required"
    assert workflow._publish_context["objectiveOutcome"] == "idle"
    assert workflow._summary.startswith(f"Marked {REPOSITORY}#{ISSUE_NUMBER} manual-only")


@pytest.mark.asyncio
async def test_labelled_issue_blocks_an_explicit_run_the_assessment_would_continue(github):
    # An explicit run reaches the blocker step; the person-owned label still
    # stops it until someone removes the label.
    github["labels"] = ["manual-only"]

    result = await _check_blockers({"verdict": "PARTIALLY_IMPLEMENTED"})

    assert result.status == "COMPLETED", result.outputs
    assert result.completion_disposition is None
    assert result.outputs["decision"] == "blocked"
    assert {"source": "label", "label": "manual-only", "statusKnown": False, "done": False} in (
        result.outputs["blockingIssues"]
    )
    assert github["writes"] == []


@pytest.mark.asyncio
async def test_already_labelled_issue_is_not_commented_again(github):
    github["labels"] = ["manual-only"]

    result = await _check_blockers({"verdict": "PARTIALLY_IMPLEMENTED", "manualOnly": MANUAL_ONLY})

    assert result.status == "COMPLETED", result.outputs
    assert result.completion_disposition == "idle"
    assert github["writes"] == []


@pytest.mark.asyncio
async def test_manual_only_comment_redacts_credentials(github):
    token = "ghp_" + "A" * 36
    declaration = {
        "reason": f"The event PCs reject automation; setup used token {token}.",
        "manualActions": [f"Run the kiosk installer with GITHUB_TOKEN={token}."],
    }

    result = await _check_blockers({"verdict": "BLOCKED", "manualOnly": declaration})

    assert result.status == "COMPLETED", result.outputs
    assert token not in github["comments"][0]["body"]
    assert token not in json.dumps(result.outputs)
