"""Replay the issue-search preset's catalog → Activity → trusted-context handoff."""

from __future__ import annotations

import json
import os
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
from moonmind.workflows.temporal.github_issue_search import declared_prerequisites

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
        "user": {"id": 4257001, "login": "issue-searcher"},
        "labels": [{"name": "bug"}],
        **overrides,
    }


SEARCH_IDENTITY = {"id": 4257001, "login": "issue-searcher", "type": "User"}


@pytest.fixture
def activity_boundary(monkeypatch):
    requests = []
    pages = [[issue()]]
    detail = issue()
    dependency_details = {}

    def handler(request):
        requests.append(request)
        if request.url.path.rstrip("/").endswith("/user"):
            return httpx.Response(200, json=dict(SEARCH_IDENTITY))
        if "/comments" in request.url.path:
            # Live comment readability gate expects a GitHub comment list.
            # GET returns the list; POST creates one comment.
            if request.method == "GET":
                return httpx.Response(200, json=[])
            return httpx.Response(200, json={"id": 1})
        if request.method == "POST" and request.url.path.endswith("/labels"):
            for label in json.loads(request.content)["labels"]:
                if {"name": label} not in detail["labels"]:
                    detail["labels"].append({"name": label})
            payload = detail["labels"]
        elif request.url.path.endswith("/issues"):
            payload = pages[int(request.url.params["page"]) - 1]
            if request.url.path == "/search/issues" and isinstance(payload, list):
                payload = {"items": payload, "incomplete_results": False}
        else:
            payload = dependency_details.get(request.url.path, detail)
            if callable(payload):
                payload = payload()
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
        read=AsyncMock(return_value=(None, {"verdict": "NOT_IMPLEMENTED"})),
        create=AsyncMock(return_value=(SimpleNamespace(artifact_id="art_brief"), None)),
        write_complete=AsyncMock(
            return_value=SimpleNamespace(
                artifact_id="art_brief",
                sha256="a" * 64,
                size_bytes=100,
                content_type="application/json",
                encryption=SimpleNamespace(value="none"),
            )
        ),
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
        execute=execute,
        pages=pages,
        requests=requests,
        detail=detail,
        dependency_details=dependency_details,
        activities=activities,
        artifact_service=artifact_service,
        dispatcher=dispatcher,
        snapshot=snapshot,
    )


@pytest.mark.asyncio
async def test_full_issue_brief_reaches_fresh_assessment_workspace(
    activity_boundary,
    tmp_path,
    monkeypatch,
):
    """Replay mm:6901d5c7 04:20: full provider body must survive prompt compaction."""
    from moonmind.omnigent.workspace_artifacts import WorkspaceArtifactProjector
    from moonmind.workflows.temporal.artifacts import (
        LocalTemporalArtifactStore,
        TemporalArtifactRepository,
        TemporalArtifactService,
    )
    from moonmind.workflows.temporal.workflows import run as run_module
    from tests.unit.workflows.temporal.test_activity_runtime import temporal_db

    replay = json.loads(
        (
            Path(__file__).resolve().parents[2]
            / "integration/reliability/replays/issue-brief-verification-handoff/manifest.json"
        ).read_text()
    )
    body = replay["bodyPrefix"] * replay["repeatCount"] + replay["acceptanceTail"]
    candidate = issue(number=3950, body=body)
    activity_boundary.pages[:] = [[candidate]]
    activity_boundary.detail.update(candidate)
    info = SimpleNamespace(
        namespace="default",
        workflow_id="workflow-1",
        workflow_run_id="run-1",
        run_id="run-1",
    )
    monkeypatch.setattr("temporalio.activity.info", lambda: info)
    monkeypatch.setattr(run_module.workflow, "info", lambda: info)
    monkeypatch.setattr(run_module.workflow, "patched", lambda _patch: True)

    async with temporal_db(tmp_path) as sessions:
        async with sessions() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            activity_boundary.activities._artifact_service = service
            result = await activity_boundary.execute(
                "github.load_issue_preset_brief",
                {"repository": REPOSITORY, "issueSearch": ""},
            )
            assert result.status == "COMPLETED"
            wf = MoonMindRunWorkflow()
            wf._record_assessment_context(result.outputs)
            wf._record_trusted_issue_context(result.outputs)
            request = wf._build_agent_execution_request(
                node_inputs={
                    "targetRuntime": "omnigent",
                    "repository": REPOSITORY,
                    "instructions": "Assess the full issue.",
                    "previousOutputs": result.outputs,
                },
                node_id="assessment",
                tool_name="omnigent",
            )
            assert "contextTruncation" in request.instruction_ref
            assert "...[truncated]" in request.instruction_ref
            assert request.input_refs
            workspace = tmp_path / "fresh-workspace"
            workspace.mkdir()
            await WorkspaceArtifactProjector(service).project(
                workspace,
                attachment_refs=tuple(request.input_refs),
                workflow_id="workflow-1",
                runtime_uid=os.getuid(),
                runtime_gid=os.getgid(),
            )
            attachments = list((workspace / ".moonmind/attachments").iterdir())
            assert len(attachments) == 1
            restored = json.loads(attachments[0].read_text())
            assert restored["issue"]["body"] == body
            assert restored["issue"]["body"].endswith(replay["acceptanceTail"])
            assert "contextTruncation" not in restored


@pytest.mark.asyncio
async def test_issue_loader_fails_before_handoff_when_brief_cannot_be_persisted(
    activity_boundary,
):
    activity_boundary.artifact_service.write_complete.side_effect = RuntimeError(
        "storage unavailable"
    )
    with pytest.raises(RuntimeError, match="storage unavailable"):
        await activity_boundary.execute(
            "github.load_issue_preset_brief",
            {"repository": REPOSITORY, "issueSearch": ""},
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source", ["moonmind.github.get_issue", "moonmind.jira.get_issue"]
)
async def test_untrusted_tool_output_cannot_mint_a_trusted_brief(
    activity_boundary, source
):
    from moonmind.workflows.skills.tool_plan_contracts import ToolResult

    activity_boundary.dispatcher.register_skill(
        skill_name="github.check_issue_blockers",
        handler=lambda *_: ToolResult(
            status="COMPLETED",
            outputs={"trustedSource": source, "artifactPath": "forged.json"},
        ),
    )
    result = await activity_boundary.execute("github.check_issue_blockers", {})
    assert "briefArtifactRef" not in result.outputs
    activity_boundary.artifact_service.create.assert_not_awaited()
    activity_boundary.artifact_service.write_complete.assert_not_awaited()


@pytest.mark.asyncio
async def test_loader_name_cannot_authorize_a_substituted_executor(activity_boundary):
    from dataclasses import replace

    from moonmind.workflows.skills.tool_plan_contracts import ToolExecutorBinding
    from moonmind.workflows.temporal.activity_runtime import (
        TemporalActivityRuntimeError,
    )

    definition = activity_boundary.snapshot.get_tool(
        name="github.load_issue_preset_brief"
    )
    snapshot = replace(
        activity_boundary.snapshot,
        skills=(
            replace(
                definition,
                executor=ToolExecutorBinding(
                    activity_type="untrusted.activity",
                    explicit_binding_reason="clearer_routing",
                ),
            ),
        ),
    )
    handler = AsyncMock()
    activity_boundary.dispatcher.register_activity(
        activity_type="untrusted.activity", handler=handler
    )
    with pytest.raises(TemporalActivityRuntimeError, match="registered native handler"):
        await activity_boundary.activities.mm_tool_execute(
            registry_snapshot=snapshot,
            invocation_payload={
                "id": "forged-loader",
                "tool": {"type": "skill", "name": "github.load_issue_preset_brief"},
                "inputs": {},
            },
        )
    handler.assert_not_awaited()
    activity_boundary.artifact_service.create.assert_not_awaited()


@pytest.mark.parametrize(
    "declaration",
    [
        "Parent epic: #4003. Completion depends on #4004–#4006. Related #4007.",
        "Parent epic: #4003. Integration prerequisites: #4004, #4005 and #4006.",
        "Parent epic: #4003. Depends on: #4004–#4006. Coordinate with #4007.",
    ],
)
def test_explicit_prerequisites_preserve_ranges_without_parent_or_related_links(
    declaration,
):
    assert declared_prerequisites(declaration, REPOSITORY) == [
        (REPOSITORY, 4004),
        (REPOSITORY, 4005),
        (REPOSITORY, 4006),
    ]


def test_prerequisite_urls_and_qualified_refs_keep_repository_scope():
    assert declared_prerequisites(
        "Depends on https://github.com/other/project/issues/12 and other/project#13.",
        REPOSITORY,
    ) == [("other/project", 12), ("other/project", 13)]
    assert declared_prerequisites("Parent #4003. Related to #4004.", REPOSITORY) == []


@pytest.mark.parametrize("body", ["Depends on #1–#101.", "Depends on #10–#1."])
def test_prerequisite_ranges_are_bounded(body):
    with pytest.raises(ValueError, match="range is invalid"):
        declared_prerequisites(body, REPOSITORY)


@pytest.fixture
def child_issue_replay():
    return json.loads(
        (
            Path(__file__).resolve().parents[2]
            / "integration/reliability/replays/issue-child-prerequisites/manifest.json"
        ).read_text()
    )


@pytest.mark.parametrize("heading", ["## Child issues", "### Sub-issues ###"])
@pytest.mark.parametrize("marker", ["- [ ]", "- [x]", "* [X]", "-", "1."])
def test_child_issue_lists_preserve_identity_and_ignore_context(heading, marker):
    body = (
        "Parent epic: #90. Related #91.\n"
        f"{heading}\n\n"
        f"{marker} #10 — implement with help from #92.\n"
        f"{marker} other/project#11 — preserve identity.\n"
        f"{marker} https://github.com/other/project/issues/12 — qualify.\n"
        f"{marker} [Child task](https://github.com/other/project/issues/13).\n"
        "## Related issues\n- [ ] #93\n"
    )
    assert declared_prerequisites(body, REPOSITORY) == [
        (REPOSITORY, 10),
        ("other/project", 11),
        ("other/project", 12),
        ("other/project", 13),
    ]


@pytest.mark.asyncio
async def test_rendered_child_lists_control_selection_and_preflight(
    activity_boundary, child_issue_replay
):
    for case in child_issue_replay["markdownCases"]:
        parent = issue(body=case["body"])
        leaf = issue(4004)
        activity_boundary.pages[:] = [[parent, leaf]]
        expected = leaf if case["children"] else parent
        activity_boundary.detail.update(expected)
        activity_boundary.requests.clear()
        for number in case["children"]:
            activity_boundary.dependency_details[
                f"/repos/{REPOSITORY}/issues/{number}"
            ] = issue(number, state="open")
        selected = await activity_boundary.execute(
            "github.load_issue_preset_brief",
            {"repository": REPOSITORY, "issueSearch": ""},
        )
        assert selected.status == "COMPLETED", case["name"]
        assert selected.outputs["issue"]["number"] == expected["number"], case["name"]
        activity_boundary.detail.update(parent)
        preflight = await activity_boundary.execute(
            "github.check_issue_blockers",
            {"repository": REPOSITORY, "issueNumber": parent["number"]},
        )
        assert preflight.outputs["decision"] == (
            "blocked" if case["children"] else "continue"
        ), case["name"]
        requested_children = {
            int(request.url.path.rsplit("/", 1)[1])
            for request in activity_boundary.requests
            if request.url.path.rsplit("/", 1)[1].isdigit()
            and not request.url.path.endswith(f"/{expected['number']}")
            and not request.url.path.endswith(f"/{parent['number']}")
        }
        assert requested_children == set(case["children"]), case["name"]
        # Req-2 advisory claim (issue #4178) adds in-progress POSTs plus a
        # comment-readability GET; prerequisite fetching itself stays read-only.
        assert all(
            request.method == "GET"
            or request.url.path.endswith("/labels")
            or "/comments" in request.url.path
            for request in activity_boundary.requests
        )


@pytest.mark.parametrize("separator", ["-", "–", "—"])
@pytest.mark.parametrize("title", ["2FA support", "12-factor cleanup", "2026 rollout"])
def test_numeric_child_titles_are_not_ranges(separator, title):
    assert declared_prerequisites(
        f"## Child issues\n- [ ] #10 {separator} {title}\n", REPOSITORY
    ) == [(REPOSITORY, 10)]
    assert declared_prerequisites(
        f"## Child issues\n- #10{separator}#12 {separator} {title}\n", REPOSITORY
    ) == [(REPOSITORY, number) for number in range(10, 13)]
    assert declared_prerequisites(
        f"## Child issues\n- #10 {separator}   #12 {separator} {title}\n", REPOSITORY
    ) == [(REPOSITORY, number) for number in range(10, 13)]


def test_numeric_markdown_link_title_preserves_child_identity():
    assert declared_prerequisites(
        "## Child issues\n"
        f"- [Login — 2FA support](https://github.com/{REPOSITORY}/issues/10)\n",
        REPOSITORY,
    ) == [(REPOSITORY, 10)]


@pytest.mark.parametrize("indent", ["", " ", "  ", "   "])
def test_nested_child_headings_keep_section_authority(indent):
    body = (
        f"{indent}## Child issues\n\n"
        "### Backend\n- #10\n#### Storage\n- #11\n"
        "### Frontend\n- #12\n## Related issues\n- #90\n"
    )
    assert declared_prerequisites(body, REPOSITORY) == [
        (REPOSITORY, number) for number in range(10, 13)
    ]


@pytest.mark.parametrize(
    "example",
    [
        "```md\n## Child issues\n- #90\n```\n",
        "~~~\n## Child issues\n- #90\n~~~\n",
        "````md\n```\n## Child issues\n- #90\n```\n````\n",
        "<!--\n## Child issues\n- #90\n-->\n",
        "    ## Child issues\n    - #90\n",
    ],
)
def test_child_list_examples_do_not_declare_dependencies(example):
    assert declared_prerequisites(
        example + "\n## Child issues\n- #10\n", REPOSITORY
    ) == [(REPOSITORY, 10)]


def test_child_prerequisites_share_deduplication_and_bounds():
    assert declared_prerequisites(
        "Depends on #10.\n## Child issues\n- [ ] #10–#12\n", REPOSITORY
    ) == [(REPOSITORY, number) for number in range(10, 13)]
    with pytest.raises(ValueError, match="declaration exceeds 100"):
        declared_prerequisites(
            "Depends on #1–#100.\n## Child issues\n- [ ] #101\n", REPOSITORY
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("checked", [False, True])
@pytest.mark.parametrize("child_state", ["open", "closed"])
async def test_child_issue_replay_selection_and_fresh_preflight(
    activity_boundary, child_issue_replay, child_state, checked
):
    replay = child_issue_replay
    parent = issue(**replay["parent"])
    if checked:
        parent["body"] = parent["body"].replace("[ ]", "[x]")
    leaf = issue(replay["eligibleLeaf"])
    activity_boundary.pages[:] = [[parent, leaf]]
    expected = parent if child_state == "closed" else leaf
    activity_boundary.detail.update(expected)
    for number in replay["childNumbers"]:
        activity_boundary.dependency_details[f"/repos/{REPOSITORY}/issues/{number}"] = (
            issue(number, state=child_state)
        )
    result = await activity_boundary.execute(
        "github.load_issue_preset_brief", {"repository": REPOSITORY, "issueSearch": ""}
    )
    assert result.status == "COMPLETED"
    assert result.outputs["issue"]["number"] == expected["number"]
    assert result.outputs["searchEvidence"]["candidatesExamined"] == (
        1 if child_state == "closed" else 2
    )
    # Explicit selection and an already-loaded brief must also see reopened
    # children at the shared preflight, before marking the parent in progress.
    activity_boundary.detail.update(parent)
    first_child = replay["childNumbers"][0]
    activity_boundary.dependency_details[
        f"/repos/{REPOSITORY}/issues/{first_child}"
    ] = issue(first_child, state="open")
    preflight = await activity_boundary.execute(
        "github.check_issue_blockers",
        {"repository": REPOSITORY, "issueNumber": parent["number"]},
    )
    assert preflight.outputs["decision"] == "blocked"
    assert preflight.outputs["blockingIssues"][0]["number"] == first_child
    assert not any(
        request.url.path.endswith("/4103") for request in activity_boundary.requests
    )
    assert all(
        request.method == "GET"
        or request.url.path.endswith("/labels")
        or "/comments" in request.url.path
        for request in activity_boundary.requests
    )


@pytest.mark.asyncio
async def test_child_reopening_at_confirmation_cannot_admit_parent(activity_boundary):
    parent = issue(body="## Child issues\n- [x] #10\n")
    activity_boundary.pages[:] = [[parent]]
    activity_boundary.detail.update(parent)
    states = iter(["closed", "open"])
    activity_boundary.dependency_details[f"/repos/{REPOSITORY}/issues/10"] = (
        lambda: issue(10, state=next(states))
    )
    result = await activity_boundary.execute(
        "github.load_issue_preset_brief", {"repository": REPOSITORY, "issueSearch": ""}
    )
    assert result.status == "FAILED"
    assert "changed or could not be confirmed" in result.outputs["error"]
    activity_boundary.artifact_service.create.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["unknown", "", "archived"])
async def test_unknown_child_state_cannot_admit_parent(activity_boundary, state):
    activity_boundary.pages[:] = [[issue(body="## Child issues\n- [ ] #10\n")]]
    activity_boundary.dependency_details[f"/repos/{REPOSITORY}/issues/10"] = issue(
        10, state=state
    )
    result = await activity_boundary.execute(
        "github.load_issue_preset_brief", {"repository": REPOSITORY, "issueSearch": ""}
    )
    assert result.status == "FAILED"
    assert "prerequisite identity or state is invalid" in result.outputs["error"]
    assert all(request.method == "GET" for request in activity_boundary.requests)
    activity_boundary.artifact_service.create.assert_not_awaited()


@pytest.mark.parametrize(
    "context",
    [
        "; see related issue #20",
        ", related issue #20",
        " (parent #20)",
        " and see #20",
        "; coordinate with #20",
    ],
)
def test_dependency_list_stops_before_contextual_references(context):
    assert declared_prerequisites(
        f"Completion depends on #10 and #11{context}.", REPOSITORY
    ) == [(REPOSITORY, 10), (REPOSITORY, 11)]


@pytest.mark.asyncio
async def test_related_open_issue_does_not_block_selection_or_preflight(
    activity_boundary,
):
    candidate = issue(body="Completion depends on #10; see related issue #20.")
    activity_boundary.pages[:] = [[candidate]]
    activity_boundary.detail.update(candidate)
    activity_boundary.dependency_details[f"/repos/{REPOSITORY}/issues/10"] = issue(
        10, state="closed"
    )
    activity_boundary.dependency_details[f"/repos/{REPOSITORY}/issues/20"] = issue(20)
    result = await activity_boundary.execute(
        "github.load_issue_preset_brief", {"repository": REPOSITORY, "issueSearch": ""}
    )
    assert result.status == "COMPLETED"
    preflight = await activity_boundary.execute(
        "github.check_issue_blockers", {"repository": REPOSITORY, "issueNumber": 4025}
    )
    assert preflight.outputs["decision"] == "continue"
    assert not any(
        request.url.path.endswith("/20") for request in activity_boundary.requests
    )


@pytest.mark.asyncio
async def test_scan_reuses_prerequisite_evidence_across_all_500_candidates(
    activity_boundary,
):
    candidates = [
        issue(
            1000 + n,
            body=f"Depends on {REPOSITORY if n % 2 else REPOSITORY.lower()}#10–#20.",
        )
        for n in range(499)
    ] + [issue()]
    activity_boundary.pages[:] = [
        candidates[start : start + 100] for start in range(0, 500, 100)
    ]
    for number in range(10, 21):
        for repository in (REPOSITORY, REPOSITORY.lower()):
            activity_boundary.dependency_details[
                f"/repos/{repository}/issues/{number}"
            ] = issue(number, state="closed" if number < 20 else "open")
    result = await activity_boundary.execute(
        "github.load_issue_preset_brief", {"repository": REPOSITORY, "issueSearch": ""}
    )
    assert result.status == "COMPLETED"
    assert result.outputs["searchEvidence"]["candidatesExamined"] == 500
    # +5 for live comment readability (1 GET) + advisory claim (2 POSTs + 2 re-reads).
    # +1 for the credential-bound GET /user identity lookup (self-only default).
    assert len(activity_boundary.requests) == 5 + 11 + 1 + 5 + 1


@pytest.mark.asyncio
async def test_scan_stops_before_exceeding_aggregate_prerequisite_budget(
    activity_boundary,
):
    candidates = [
        issue(1000 + n, body=f"Depends on #{2000 + 2*n} and #{2001 + 2*n}.")
        for n in range(100)
    ]
    activity_boundary.pages[:] = [candidates, [issue()]]
    for number in range(2000, 2200):
        activity_boundary.dependency_details[f"/repos/{REPOSITORY}/issues/{number}"] = (
            issue(number, state="open" if number % 2 else "closed")
        )
    result = await activity_boundary.execute(
        "github.load_issue_preset_brief", {"repository": REPOSITORY, "issueSearch": ""}
    )
    assert result.status == "FAILED"
    assert "100-request prerequisite lookup budget" in result.outputs["error"]
    # +1 for the credential-bound GET /user identity lookup (self-only default).
    assert len(activity_boundary.requests) == 1 + 100 + 1
    assert all(request.method == "GET" for request in activity_boundary.requests)


@pytest.mark.asyncio
async def test_selected_issue_confirmation_refreshes_cached_prerequisite_state(
    activity_boundary,
):
    candidate = issue(body="Depends on #10.")
    activity_boundary.pages[:] = [[candidate]]
    activity_boundary.detail.update(candidate)
    states = iter(["closed", "open"])
    activity_boundary.dependency_details[f"/repos/{REPOSITORY}/issues/10"] = (
        lambda: issue(10, state=next(states))
    )
    result = await activity_boundary.execute(
        "github.load_issue_preset_brief", {"repository": REPOSITORY, "issueSearch": ""}
    )
    assert result.status == "FAILED"
    assert "changed or could not be confirmed" in result.outputs["error"]
    # +1 for live comment readability GET before confirmation re-read.
    # +1 for the credential-bound GET /user identity lookup (self-only default).
    assert len(activity_boundary.requests) == 4 + 1 + 1


@pytest.mark.asyncio
async def test_maximum_prerequisite_list_has_fresh_confirmation_budget(
    activity_boundary,
):
    candidate = issue(body="Depends on #10–#109.")
    activity_boundary.pages[:] = [[candidate]]
    activity_boundary.detail.update(candidate)
    for number in range(10, 110):
        activity_boundary.dependency_details[f"/repos/{REPOSITORY}/issues/{number}"] = (
            issue(number, state="closed")
        )
    result = await activity_boundary.execute(
        "github.load_issue_preset_brief", {"repository": REPOSITORY, "issueSearch": ""}
    )
    assert result.status == "COMPLETED"
    # +5 for live comment readability + advisory claim.
    # +1 for the credential-bound GET /user identity lookup (self-only default).
    assert len(activity_boundary.requests) == 1 + 100 + 1 + 100 + 5 + 1


@pytest.mark.asyncio
async def test_prerequisite_cache_preserves_cross_repository_identity(
    activity_boundary,
):
    activity_boundary.pages[:] = [
        [
            issue(1000, body="Depends on one/project#10 and one/project#11."),
            issue(1001, body="Depends on two/project#10."),
            issue(),
        ]
    ]
    for repository, number, state in (
        ("one/project", 10, "closed"),
        ("one/project", 11, "open"),
        ("two/project", 10, "open"),
    ):
        activity_boundary.dependency_details[f"/repos/{repository}/issues/{number}"] = (
            issue(
                number,
                state=state,
                html_url=f"https://github.com/{repository}/issues/{number}",
            )
        )
    result = await activity_boundary.execute(
        "github.load_issue_preset_brief", {"repository": REPOSITORY, "issueSearch": ""}
    )
    assert result.status == "COMPLETED"
    assert result.outputs["searchEvidence"]["candidatesExamined"] == 3
    # +5 for live comment readability + advisory claim, plus the single
    # credential-bound identity lookup for the self-only default.
    assert len(activity_boundary.requests) == 5 + 5 + 1


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["open", "closed"])
async def test_dependency_gate_selection_and_preflight_use_fresh_github_state(
    activity_boundary,
    state,
):
    # Follow-up incident mm:ec76f857: the release gate was admitted while
    # its explicitly required implementation issues remained open.
    gate = issue(4024, body="Parent epic: #4003. Completion depends on #2615.")
    candidate = gate if state == "closed" else issue(4004)
    activity_boundary.pages[:] = [[gate, issue(4004)]]
    activity_boundary.detail.update(candidate)
    dependency_path = f"/repos/{REPOSITORY}/issues/2615"
    activity_boundary.dependency_details[dependency_path] = issue(2615, state=state)
    result = await activity_boundary.execute(
        "github.load_issue_preset_brief",
        {"repository": REPOSITORY, "issueSearch": ""},
    )
    assert result.status == "COMPLETED"
    assert result.outputs["issue"]["number"] == candidate["number"]
    assert any(
        request.url.path == dependency_path for request in activity_boundary.requests
    )
    # A fresh preflight also protects an explicitly selected issue and sees
    # a prerequisite that reopened after candidate selection.
    activity_boundary.detail.update(gate)
    activity_boundary.dependency_details[dependency_path] = issue(2615, state="open")
    preflight = await activity_boundary.execute(
        "github.check_issue_blockers",
        {"repository": REPOSITORY, "issueNumber": 4024},
    )
    assert preflight.outputs["decision"] == "blocked"
    assert preflight.outputs["blockingIssues"][0]["number"] == 2615
    assert all(
        request.method == "GET"
        or request.url.path.endswith("/labels")
        or "/comments" in request.url.path
        for request in activity_boundary.requests
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["unknown", "", "archived"])
async def test_unverifiable_dependency_state_cannot_admit_an_issue(
    activity_boundary, state
):
    activity_boundary.pages[:] = [[issue(body="Depends on #2615.")]]
    activity_boundary.dependency_details[f"/repos/{REPOSITORY}/issues/2615"] = issue(
        2615, state=state
    )
    result = await activity_boundary.execute(
        "github.load_issue_preset_brief",
        {"repository": REPOSITORY, "issueSearch": ""},
    )
    assert result.status == "FAILED"
    assert "prerequisite identity or state is invalid" in result.outputs["error"]
    assert all(request.method == "GET" for request in activity_boundary.requests)


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
    assert "docker" in expanded["capabilities"]
    assert steps[0]["type"] == "tool"
    tool = steps[0]["tool"]
    result = await activity_boundary.execute(tool["id"], tool["inputs"])
    assert result.status == "COMPLETED"
    assert result.outputs["issue"]["number"] == 4025
    assert result.outputs["searchEvidence"] == {
        "fallbackScanning": True,
        "pagesExamined": 1,
        "candidatesExamined": 1,
        "authorScope": "authenticated_user",
        "authorMismatchesSkipped": 0,
        "authenticatedUser": {"id": 4257001, "login": "issue-searcher"},
        "selectedIssueAuthor": {"id": 4257001, "login": "issue-searcher"},
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
    # Req-2 claim already applied in-progress during load; reset to Available
    # so the explicit In Progress step exercises its own mutation path.
    activity_boundary.detail["labels"] = [{"name": "bug"}]
    # No local workspace, issue-number injection, or assistant-text parsing.
    for step in steps[2:4]:
        tool = step["tool"]
        result = await activity_boundary.execute(
            tool["id"], {**tool["inputs"], "previousOutputs": previous}
        )
        assert result.status == "COMPLETED", result.outputs
        assert (
            result.outputs.get("issueRef") == f"{REPOSITORY}#4025"
            or result.outputs.get("issueUrl") == issue()["html_url"]
        )
        workflow._record_assessment_context(result.outputs)
        previous = workflow._merge_trusted_issue_context(result.outputs)
        assert previous["searchEvidence"]["candidatesExamined"] == 1
        assert previous["searchEvidence"]["pagesExamined"] == 1
    mutations = [
        request for request in activity_boundary.requests if request.method != "GET"
    ]
    # 1 claim POST from load (advisory in-progress labels) + 2 from the
    # explicit In Progress step (labels + handoff comment).
    assert [(request.method, request.url.path) for request in mutations] == [
        ("POST", f"/repos/{REPOSITORY}/issues/4025/labels"),
        ("POST", f"/repos/{REPOSITORY}/issues/4025/labels"),
        ("POST", f"/repos/{REPOSITORY}/issues/4025/comments"),
    ]
    assert json.loads(mutations[0].content) == {"labels": ["status: in-progress"]}
    assert result.outputs["mutationOutcome"] == "applied"
    assert set(result.outputs["confirmedLabels"]) == {"bug", "status: in-progress"}
    # Finalization still requires its verification / PR evidence before mutation.
    final_tool = steps[-1]["tool"]
    final = await activity_boundary.execute(
        final_tool["id"], {**final_tool["inputs"], "previousOutputs": previous}
    )
    assert final.status == "FAILED"
    assert [
        request for request in activity_boundary.requests if request.method != "GET"
    ] == mutations


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
    search_requests = [
        request
        for request in activity_boundary.requests
        if request.url.path == "/search/issues"
    ]
    assert len(search_requests) == 1
    assert (
        search_requests[0].url.params["q"]
        == f"dashboard author:issue-searcher repo:{REPOSITORY} is:issue is:open"
    )
    activity_boundary.requests.clear()
    # Req-2 claim mutates the mocked issue labels; reset to Available for the
    # explicit lookup so it exercises direct fetch + readability + claim.
    activity_boundary.detail.clear()
    activity_boundary.detail.update(issue())
    # Existing persisted explicit issue inputs keep their original direct lookup.
    result = await activity_boundary.execute(
        "github.load_issue_preset_brief",
        {"repository": REPOSITORY, "issueNumber": 4025},
    )
    assert result.status == "COMPLETED"
    # 1 detail fetch + 1 comment readability + 4 claim/re-read requests.
    assert len(activity_boundary.requests) == 1 + 5
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


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["", "dashboard"])
async def test_search_skips_in_progress_issues_and_revalidates_fresh_detail(
    activity_boundary, query
):
    activity_boundary.pages[:] = [
        [issue(4026, labels=[{"name": "status: in-progress"}]), issue()]
    ]
    result = await activity_boundary.execute(
        "github.load_issue_preset_brief",
        {"repository": REPOSITORY, "issueSearch": query},
    )
    assert result.status == "COMPLETED"
    assert result.outputs["issue"]["number"] == 4025
    assert result.outputs["searchEvidence"]["candidatesExamined"] == 2
    # A label added between search and brief loading must fail confirmation.
    activity_boundary.detail.update({"labels": [{"name": "status: in-progress"}]})
    result = await activity_boundary.execute(
        "github.load_issue_preset_brief",
        {"repository": REPOSITORY, "issueSearch": query},
    )
    assert result.status == "FAILED"
    assert "could not be confirmed" in result.outputs["error"]
    assert "trustedSource" not in result.outputs


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


@pytest.mark.asyncio
async def test_finalize_with_mixed_labels_and_published_pr_ends_degraded_completed(
    activity_boundary, tmp_path
):
    """MoonLadderStudios/MoonMind#4225: step-09 label conflict steers to needs-attention.

    Preset journey: the search preset selected the issue, step 08 published PR
    4210, and step 09 (``github.update_issue_status`` mode
    ``finalize_after_pr_or_done``) observes the 2026-09-10 conflict
    (``status: in-progress`` + ``status: code-review``). The run must end
    COMPLETED with a degraded attention outcome, not FAILED.
    """
    pr_url = "https://github.com/MoonLadderStudios/MoonMind/pull/4210"
    pr_artifact = tmp_path / "pr-4210.json"
    pr_artifact.write_text(json.dumps({"pullRequestUrl": pr_url}), encoding="utf-8")
    verify_artifact = tmp_path / "verify.json"
    verify_artifact.write_text(json.dumps({"verdict": "FULLY_IMPLEMENTED"}), encoding="utf-8")
    activity_boundary.detail.update(
        {
            "number": 4177,
            "state": "open",
            "labels": [{"name": "status: in-progress"}, {"name": "status: code-review"}],
        }
    )
    result = await activity_boundary.execute(
        "github.update_issue_status",
        {
            "repository": REPOSITORY,
            "issueNumber": 4177,
            "mode": "finalize_after_pr_or_done",
            "pullRequestArtifactPath": str(pr_artifact),
            "verificationArtifactPath": str(verify_artifact),
        },
    )
    assert result.status == "COMPLETED", result.outputs
    assert result.outputs["decision"] == "attention"
    assert result.outputs["degraded"] is True
    assert result.outputs["pullRequestUrl"] == pr_url
    assert result.outputs["previousLifecycleSettled"] == "blocked_mixed"
    assert result.outputs["transition"]["toTarget"] == "to_needs_attention"
    confirmed = [str(label["name"]).lower() for label in activity_boundary.detail["labels"]]
    assert "status: needs-attention" in confirmed
    assert "status: in-progress" in confirmed
    assert "status: code-review" in confirmed
    assert "degraded" in result.outputs["summary"].lower()
    comment_posts = [
        request
        for request in activity_boundary.requests
        if request.method == "POST" and request.url.path.endswith("/comments")
    ]
    assert comment_posts, "expected the PR handoff comment to be posted"
    assert any(pr_url in request.content.decode() for request in comment_posts)
