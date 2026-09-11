"""Replay search-preset publication through the worker and status boundaries."""

from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import unquote

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

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
from moonmind.workflows.temporal.artifacts import (
    LocalTemporalArtifactStore,
    TemporalArtifactRepository,
    TemporalArtifactService,
)
from moonmind.workflows.temporal.story_output_tools import (
    register_story_output_tool_handlers,
)
from moonmind.workflows.temporal.remediation_loop import (
    ConsumedRemediationBudgets,
    RemediationLoopPhase,
    RemediationLoopSpec,
    RemediationLoopState,
)
from moonmind.workflows.temporal.worker_runtime import _build_runtime_planner
from moonmind.workflows.temporal.workflows import run as run_module
from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow

from .helpers import load_replay

pytestmark = [pytest.mark.asyncio, pytest.mark.reliability_journey]


@pytest_asyncio.fixture
async def artifact_service(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/artifacts.db")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            yield TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
    finally:
        await engine.dispose()


@pytest.mark.parametrize(
    "inputs,restart_count",
    [
        ({}, 0),
        ({"run_verify": True}, 0),
        ({"run_verify": False}, 0),
        ({}, 2),
        ({"include_all_authors": False}, 0),
        ({"include_all_authors": True}, 0),
    ],
)
@pytest.mark.parametrize("pr_created", [True, False, "unavailable"])
async def test_search_publication_recovers_missing_pr_before_status(
    tmp_path, monkeypatch, inputs, restart_count, pr_created, artifact_service
):
    evidence = load_replay("issue-search-publication-handoff", "manifest.json")
    repository = evidence["repository"]
    number = evidence["issueNumber"]
    expected = evidence["expected"]
    pr_url = f"https://github.com/{repository}/pull/9999"
    operations = []
    search_user = {"id": 111, "login": "search-user"}
    issue = {
        "number": number,
        "state": "open",
        "title": "Dispose of completed temporary plans",
        "body": "Preserve active dependencies and durable evidence.\n" * 180
        + "Keep the complete acceptance criteria.",
        "html_url": f"https://github.com/{repository}/issues/{number}",
        "labels": [],
        # Realistic author identity for the self-authored default (#4257).
        "user": dict(search_user),
    }

    def github_http(request):
        operations.append((request.method, request.url.path))
        if request.url.path == "/user":
            # Authenticated search account behind the workflow credential.
            return httpx.Response(200, json={**search_user, "type": "user"})
        if "/comments" in request.url.path:
            # Live comment readability gate expects a GitHub comment list.
            return httpx.Response(200, json=[])
        if request.method == "PATCH":
            assert operations[0][0] == "repo.create_pr"
            issue.update(json.loads(request.content))
        if request.method == "POST":
            payload = json.loads(request.content)
            if request.url.path.endswith("/labels"):
                # Targeted lifecycle label additions apply without replacing
                # the issue's other labels (design section 8.1).
                for label in payload.get("labels", []):
                    if label not in issue["labels"]:
                        issue["labels"].append(label)
                return httpx.Response(
                    200, json=[{"name": name} for name in issue["labels"]]
                )
            assert pr_url in payload["body"]
            return httpx.Response(201, json={"id": 1})
        if request.method == "DELETE":
            deleted = unquote(request.url.path.rsplit("/", 1)[-1])
            issue["labels"] = [
                name for name in issue["labels"] if name != deleted
            ]
            return httpx.Response(200, json={})
        result = [issue] if request.url.path.endswith("/issues") else issue
        return httpx.Response(200, json=result)

    client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: client(transport=httpx.MockTransport(github_http), **kwargs),
    )
    monkeypatch.setattr(
        GitHubService,
        "resolve_github_token",
        AsyncMock(return_value=("test-only", None)),
    )
    seeds = tmp_path / "seeds"
    seeds.mkdir()
    shutil.copy(
        Path("api_service/data/presets/github-issue-search-and-implement.yaml"), seeds
    )
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/catalog.db")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(engine)() as session:
            catalog = PresetCatalogService(session)
            await catalog.sync_seed_templates(seed_dir=seeds)
            expanded = await catalog.expand_template(
                slug="github-issue-search-and-implement",
                scope="global",
                scope_ref=None,
                inputs=inputs,
                context={"repository": repository},
            )
    finally:
        await engine.dispose()

    dispatcher = ToolActivityDispatcher()
    register_story_output_tool_handlers(dispatcher)
    snapshot = ToolRegistrySnapshot(
        digest="reg:sha256:search-publication",
        artifact_ref="art:sha256:registry",
        skills=tuple(
            parse_tool_definition(_default_registry_skill_payload(name=name))
            for name in ("github.load_issue_preset_brief", "github.update_issue_status")
        ),
    )
    activities = TemporalSkillActivities(
        dispatcher=dispatcher, artifact_service=artifact_service
    )

    async def invoke(payload):
        return await activities.mm_tool_execute(
            registry_snapshot=snapshot,
            principal="test:search-publication",
            invocation_payload=payload,
            context={
                "namespace": "default",
                "workflow_id": "mm:replay",
                "run_id": "run",
                "node_id": payload["id"],
            },
            idempotency_key=f"{payload['id']}:execute",
        )

    load_tool = expanded["steps"][0]["tool"]
    selected = await invoke(
        {
            "id": "search",
            "tool": {"type": "skill", "name": load_tool["id"]},
            "inputs": load_tool["inputs"],
        }
    )
    assert selected.status == "COMPLETED"
    # Author scope survives catalog expansion into the eligibility decision
    # and durable brief (#4257): omitted and explicit false stay self-only,
    # explicit true broadens to all authors for the same self-authored issue.
    expected_scope = "all" if inputs.get("include_all_authors") is True else "authenticated_user"
    assert selected.outputs["searchEvidence"]["authorScope"] == expected_scope
    assert selected.outputs["searchEvidence"]["selectedIssueAuthor"] == search_user
    if expected_scope == "authenticated_user":
        assert selected.outputs["searchEvidence"]["authenticatedUser"] == search_user
    brief_ref = selected.outputs["briefArtifactRef"]
    _brief, brief_bytes = await artifact_service.read(
        artifact_id=brief_ref, principal="test:search-publication"
    )
    assert json.loads(brief_bytes)["issue"]["body"] == issue["body"]
    assessment, _upload = await artifact_service.create(
        principal="test:search-publication", content_type="application/json"
    )
    assessment = await artifact_service.write_complete(
        artifact_id=assessment.artifact_id,
        principal="test:search-publication",
        content_type="application/json",
        payload=json.dumps({"verdict": evidence["assessmentVerdict"]}).encode(),
    )
    parent = MoonMindRunWorkflow()
    parent._owner_id = "test:search-publication"
    parent._repo = repository
    parent._record_trusted_issue_context(selected.outputs)
    parent._assessment_context = {
        "assessmentVerdict": evidence["assessmentVerdict"],
        "assessmentArtifactRef": assessment.artifact_id,
        "briefArtifactRef": brief_ref,
        "assessedRepository": repository,
        "assessedBranch": "main",
    }
    parent._record_accepted_published_head(evidence)
    parameters = {
        "publishMode": "pr",
        "workflow": {"title": "Implement selected issue", "inputs": inputs},
    }
    # Compile real seed metadata; constructing an annotated node by hand hid
    # the missing annotation in the preceding incident's regression test.
    planner = _build_runtime_planner()
    plan = planner(
        inputs={
            "task": {
                "instructions": "Implement the selected issue and publish its PR.",
                "steps": expanded["steps"],
                "publish": {"mode": "pr"},
                "runtime": {"mode": "omnigent"},
            }
        },
        parameters=parameters,
        snapshot=snapshot,
    )
    final_node = plan["nodes"][-1]
    assert parent._issue_implement_handoff_role(final_node) == "code-review-handoff"
    # The minimized replay resumes just after the publication agent returned
    # the captured false no-change outcome, retaining its accepted branch.
    plan["nodes"] = [final_node]
    plan["edges"] = []
    operations.clear()
    enabled_patches = {
        run_module.RUN_ISSUE_IMPLEMENT_PR_HANDOFF_AUTHORITY_PATCH,
        run_module.RUN_TRUSTED_GITHUB_ISSUE_IDENTITY_PATCH,
        run_module.RUN_MOONSPEC_GATE_PREVIOUS_OUTPUTS_HANDOFF_PATCH,
        run_module.RUN_AUTHORITATIVE_PR_REQUIREMENT_PATCH,
        run_module.RUN_REMEDIATION_ISSUE_AUTHORITY_CONTINUATION_PATCH,
        run_module.RUN_ACCEPTED_PUBLISHED_BRANCH_HANDOFF_PATCH,
    }
    monkeypatch.setattr(run_module.workflow, "patched", enabled_patches.__contains__)
    monkeypatch.setattr(run_module.workflow, "upsert_memo", lambda _memo: None)
    monkeypatch.setattr(
        run_module.workflow, "upsert_search_attributes", lambda _attrs: None
    )
    monkeypatch.setattr(run_module.workflow, "now", lambda: datetime.now(timezone.utc))
    monkeypatch.setattr(
        run_module.workflow,
        "info",
        lambda: SimpleNamespace(
            namespace="default",
            workflow_id="mm:replay",
            run_id="run",
            search_attributes={},
        ),
    )
    monkeypatch.setattr(run_module.workflow, "logger", logging.getLogger(__name__))

    async def wait_condition(predicate, **_kwargs):
        assert predicate()

    monkeypatch.setattr(run_module.workflow, "wait_condition", wait_condition)
    monkeypatch.setattr(run_module.workflow, "all_handlers_finished", lambda: True)

    if restart_count:
        spec = RemediationLoopSpec.model_validate(
            next(
                step["annotations"]["remediationLoop"]
                for step in expanded["steps"]
                if "remediationLoop" in step.get("annotations", {})
            )
        )
        parent._remediation_loop_spec = spec
        parent._remediation_loop_state = RemediationLoopState(
            loopId=spec.loop_id,
            attemptOrdinal=3,
            phase=RemediationLoopPhase.VERIFICATION_PENDING,
            consumedBudgets=ConsumedRemediationBudgets(attempts=3),
        )
        for _ in range(restart_count):
            carried = parent._build_remediation_loop_continue_as_new_input(
                ordered_nodes=[final_node]
            )["remediation_loop_continuation"]
            assert "body" not in json.dumps(carried["trustedIssueContext"])
            restored = MoonMindRunWorkflow()
            restored._owner_id = parent._owner_id
            restored._repo = repository
            restored._remediation_loop_spec = spec
            restored._remediation_loop_continuation = carried
            restored._restore_remediation_loop_continuation(ordered_nodes=[])
            assert restored._assessment_context == parent._assessment_context
            assert restored._issue_implement_pr_required()
            parent = restored
    # The verifier in the resumed run approves the candidate. This must not
    # replace the initial assessment carried across either restart.
    parent._publish_context["moonSpecGate"] = {
        "verdict": evidence["verificationVerdict"],
        "gateResultRef": "art_verify",
    }

    async def execute_activity(name, payload, **_kwargs):
        data = payload if isinstance(payload, dict) else payload.model_dump()
        if name == "artifact.create":
            return ({"artifact_id": "art_manifest"}, {})
        if name == "artifact.read":
            if data["artifact_ref"] == "art_plan":
                return json.dumps(plan).encode()
            return json.dumps(
                {
                    "skills": [
                        _default_registry_skill_payload(
                            name="github.update_issue_status"
                        )
                    ]
                }
            ).encode()
        if name == "repo.create_pr":
            operations.append((name, data))
            assert data["head"] == evidence["acceptedRepositoryEvidence"]["branch"]
            assert data["base"] == "main"
            assert expected["closingLine"] in data["body"].splitlines()
            assert f"{repository}#{number}" in data["title"]
            if pr_created == "unavailable":
                return {"created": False, "summary": "GitHub publication unavailable"}
            return {"url": pr_url, "created": pr_created, "adopted": not pr_created}
        if name == "mm.tool.execute":
            result = await invoke(data["invocation_payload"])
            assert result.status == "COMPLETED", result.outputs
            return result
        raise AssertionError(f"Unexpected activity {name}")

    monkeypatch.setattr(run_module.workflow, "execute_activity", execute_activity)
    if pr_created == "unavailable":
        with pytest.raises(ValueError, match="PR creation returned no URL"):
            await parent._run_execution_stage(
                parameters=parameters, plan_ref="art_plan"
            )
        assert [op[0] for op in operations] == ["repo.create_pr"]
        assert parent._accepted_published_head() == (
            evidence["acceptedRepositoryEvidence"]["branch"],
            evidence["acceptedRepositoryEvidence"]["headSha"],
        )
        return
    await parent._run_execution_stage(parameters=parameters, plan_ref="art_plan")
    assert issue["state"] == expected["issueState"]
    assert expected["issueLabel"] in issue["labels"]
    assert parent._publish_status == expected["publishStatus"]
    assert parent._publish_context["pullRequestUrl"] == pr_url
    assert sum(op[0] == "repo.create_pr" for op in operations) == 1
    assert parent._canonical_github_issue_from_parameters(parameters) == {
        "repository": repository,
        "issueNumber": number,
    }
    merge_request = parent._merge_automation_request(
        {
            **parameters,
            "mergeAutomation": {"enabled": True},
        }
    )
    assert merge_request["postMergeGithub"]["issueNumber"] == number
    assert merge_request["postMergeGithub"]["repository"] == repository
    # Retrying the finalization boundary reuses the confirmed PR.
    assert (
        await parent._ensure_issue_implement_pr_before_status(
            node=final_node, parameters=parameters
        )
        == pr_url
    )
    assert sum(op[0] == "repo.create_pr" for op in operations) == 1
