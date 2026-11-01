"""Replay search-preset publication through the worker and status boundaries."""

from __future__ import annotations

import asyncio
import gc
import json
import logging
import shutil
from contextlib import AsyncExitStack
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import unquote
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from temporalio import activity, workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from api_service.db.models import Base
from api_service.services.presets.catalog import PresetCatalogService
from moonmind.workflows.adapters.github_service import GitHubService
from moonmind.workflows.skills.tool_dispatcher import ToolActivityDispatcher
from moonmind.workflows.skills.tool_plan_contracts import parse_tool_definition
from moonmind.workflows.skills.tool_registry import (
    ToolRegistrySnapshot,
    compute_registry_digest,
)
from moonmind.workflows.temporal.activities.repo_activities import (
    repo_create_pr_activity,
)
from moonmind.workflows.temporal.activity_runtime import (
    TemporalIntegrationActivities,
    TemporalSkillActivities,
    _default_registry_skill_payload,
)
from moonmind.workflows.temporal.artifacts import (
    LocalTemporalArtifactStore,
    TemporalArtifactRepository,
    TemporalArtifactService,
)
from moonmind.workflows.temporal.remediation_loop import (
    ConsumedRemediationBudgets,
    RemediationLoopPhase,
    RemediationLoopSpec,
    RemediationLoopState,
)
from moonmind.workflows.temporal.runtime import managed_api_key_resolve
from moonmind.workflows.temporal.story_output_tools import (
    register_story_output_tool_handlers,
)
from moonmind.workflows.temporal.worker_runtime import _build_runtime_planner
from moonmind.workflows.temporal.workflows import merge_automation as merge_module
from moonmind.workflows.temporal.workflows import run as run_module
from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow
from tests.artifact_access import access_for_test
from tests.support.issue_claims import issue_claim_store  # noqa: F401

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
        pytest.param(
            {"constraints": "  Preserve prior work.\nKeep target.  "},
            0,
            id="constraints",
        ),
    ],
)
@pytest.mark.parametrize("pr_created", [True, False, "unavailable"])
async def test_search_publication_recovers_missing_pr_before_status(
    tmp_path, monkeypatch, inputs, restart_count, pr_created, artifact_service
):
    await _search_publication_handoff(
        tmp_path, monkeypatch, inputs, restart_count, pr_created, artifact_service
    )


async def _search_publication_handoff(
    tmp_path,
    monkeypatch,
    inputs,
    restart_count,
    pr_created,
    artifact_service,
    *,
    finish_case=None,
    parent_id="mm:replay",
):
    evidence = load_replay("issue-search-publication-handoff", "manifest.json")
    repository = evidence["repository"]
    number = evidence["issueNumber"]
    expected = evidence["expected"]
    pr_url = f"https://github.com/{repository}/pull/9999"
    operations = []
    comments = []
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
    remote = {"created": False, "merges": 0, "closes": 0, "guard_start": None}
    pr = {
        "number": 9999,
        "html_url": pr_url,
        "state": "open",
        "merged": False,
        "draft": False,
        "mergeable": True,
        "mergeable_state": "clean",
        "head": {
            "sha": evidence["acceptedRepositoryEvidence"]["headSha"],
            "ref": evidence["acceptedRepositoryEvidence"]["branch"],
            "repo": {"full_name": repository},
        },
        "base": {"ref": "main", "repo": {"full_name": repository}},
    }

    def github_http(request):
        operations.append((request.method, request.url.path))
        if finish_case is not None:
            path = request.url.path
            if path == f"/repos/{repository}":
                return httpx.Response(200, json={"default_branch": "main"})
            if "/commits/" in path:
                ref = path.split("/commits/", 1)[1]
                return httpx.Response(
                    200,
                    json={
                        "sha": "f" * 40 if ref == "main" else ref,
                        "commit": {"tree": {"sha": "e" * 40}},
                    },
                )
            if "/compare/" in path:
                return httpx.Response(200, json={"status": "identical"})
            if path.endswith("/pulls"):
                if request.method == "GET":
                    return httpx.Response(200, json=[pr] if remote["created"] else [])
                assert request.method == "POST" and not remote["created"]
                remote["created"] = True
                data = json.loads(request.content)
                pr.update(
                    {
                        key: data[key]
                        for key in ("title", "body", "draft")
                        if key in data
                    }
                )
                # The response is lost after GitHub accepted the operation.
                if finish_case == "publication_ack":
                    return httpx.Response(
                        500, json={"message": "lost create acknowledgement"}
                    )
                return httpx.Response(201, json=pr)
            if path.endswith("/pulls/9999"):
                return httpx.Response(200, json=pr)
            if path.endswith("/pulls/9999/merge"):
                assert request.method == "PUT" and not pr["merged"]
                remote["merges"] += 1
                pr.update(merged=True, state="closed", merge_commit_sha="f" * 40)
                return httpx.Response(200, json={"merged": True, "sha": "f" * 40})
            if "/check-runs" in path or path.endswith("/reviews"):
                return httpx.Response(
                    200, json={"check_runs": []} if "/check-runs" in path else []
                )
            if path.endswith("/status"):
                return httpx.Response(200, json={"state": "success", "statuses": []})
            if (
                pr["merged"]
                and finish_case == "finalization_forbidden"
                and "/issues/" in path
            ):
                return httpx.Response(403, json={"message": "issue permission denied"})
            if request.method == "PATCH" and path.endswith(f"/issues/{number}"):
                if json.loads(request.content).get("state") == "closed":
                    if finish_case == "finalization_unavailable":
                        return httpx.Response(
                            503, json={"message": "issue update unavailable"}
                        )
                    remote["closes"] += 1
                    issue.update(json.loads(request.content))
                    if finish_case == "issue_ack" and remote["closes"] == 1:
                        return httpx.Response(
                            500, json={"message": "lost issue acknowledgement"}
                        )
                    return httpx.Response(200, json=issue)
        if request.url.path == "/user":
            # Authenticated search account behind the workflow credential.
            return httpx.Response(200, json={**search_user, "type": "user"})
        if "/comments" in request.url.path:
            if request.method == "GET":
                return httpx.Response(200, json=comments)
            if request.method == "POST":
                comment = {
                    "id": len(comments) + 1,
                    "body": json.loads(request.content)["body"],
                    "user": dict(search_user),
                }
                comments.append(comment)
                return httpx.Response(201, json=comment)
            if request.method == "PATCH":
                comment = next(
                    item
                    for item in comments
                    if item["id"] == int(request.url.path.rsplit("/", 1)[1])
                )
                comment["body"] = json.loads(request.content)["body"]
                return httpx.Response(200, json=comment)
        if request.method == "PATCH":
            assert finish_case is not None or operations[0][0] == "repo.create_pr"
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
            issue["labels"] = [name for name in issue["labels"] if name != deleted]
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
    preset_dir = Path("api_service/data/presets")
    for filename in (
        "github-issue-search-and-implement.yaml",
        "issue-implement-assessment.yaml",
        "issue-implement-work-pr.yaml",
    ):
        shutil.copy(preset_dir / filename, seeds / filename)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/catalog.db")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(engine)() as session:
            catalog = PresetCatalogService(session)
            await catalog.sync_seed_templates(seed_dir=seeds)
            expanded = await catalog.expand_template(
                slug="github-issue-search-and-implement",
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
    if finish_case is not None:
        snapshot = ToolRegistrySnapshot(
            digest=compute_registry_digest(skills=snapshot.skills),
            artifact_ref=snapshot.artifact_ref,
            skills=snapshot.skills,
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
                "workflow_id": parent_id,
                **({"execution_owner": f"default/{parent_id}"} if finish_case else {}),
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
    assert selected.status == "COMPLETED", selected.outputs
    assert selected.outputs["constraints"] == inputs.get("constraints", "")
    # Author scope survives catalog expansion into the eligibility decision
    # and durable brief (#4257): omitted and explicit false stay self-only,
    # explicit true broadens to all authors for the same self-authored issue.
    expected_scope = (
        "all" if inputs.get("include_all_authors") is True else "authenticated_user"
    )
    assert selected.outputs["searchEvidence"]["authorScope"] == expected_scope
    assert selected.outputs["searchEvidence"]["selectedIssueAuthor"] == search_user
    if expected_scope == "authenticated_user":
        assert selected.outputs["searchEvidence"]["authenticatedUser"] == search_user
    brief_ref = selected.outputs["briefArtifactRef"]
    _brief, brief_bytes = await artifact_service.read(
        artifact_id=brief_ref, access=access_for_test("test:search-publication")
    )
    brief = json.loads(brief_bytes)
    assert brief["issue"]["body"] == brief["description"] == issue["body"]
    assert brief["issue_ref"] == f"{repository}#{number}"
    assert brief["source_resolution"]["status"] == "complete"
    assert brief["truncated"] is False
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
    if finish_case is not None:
        return SimpleNamespace(
            selected=selected.outputs,
            assessment=parent._assessment_context,
            evidence=evidence,
            parameters=parameters,
            plan=plan,
            snapshot=snapshot,
            invoke=invoke,
            issue=issue,
            pr=pr,
            remote=remote,
            operations=operations,
            comments=comments,
            pr_url=pr_url,
            repository=repository,
            number=number,
        )
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
            task_queue="mm.workflow.user.v2",
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


@workflow.defn
class SearchPublicationFinishJourney:
    """Resume completed implementation at the production publisher/merge owners."""

    def __init__(self):
        self.parent = MoonMindRunWorkflow()

    @workflow.query
    def checkpoint(self) -> dict:
        return {
            "status": self.parent.get_status(),
            "publishContext": self.parent._publish_context,
            "acceptedHead": self.parent._accepted_published_head(),
            "issue": self.parent._trusted_issue_context,
        }

    @workflow.run
    async def run(self, payload: dict) -> dict:
        parent = self.parent
        parent._owner_id = "test:search-publication"
        parent._repo = payload["repository"]
        parent._record_trusted_issue_context(payload["selected"])
        parent._assessment_context = payload["assessment"]
        parent._record_accepted_published_head(payload["evidence"])
        parent._publish_context["moonSpecGate"] = {
            "verdict": payload["evidence"]["verificationVerdict"],
            "gateResultRef": "art_verify",
        }
        parameters = payload["parameters"]
        try:
            await parent._run_execution_stage(
                parameters=(
                    {**parameters, "mergeAutomation": {"enabled": False}}
                    if payload.get("candidateUrl")
                    else parameters
                ),
                plan_ref="art_plan",
            )
            if payload.get("candidateUrl"):
                await parent._maybe_start_merge_gate(
                    parameters=parameters,
                    pull_request_url=payload["candidateUrl"],
                )
        except (ValueError, ApplicationError) as exc:
            parent._set_state(run_module.STATE_FAILED, summary=str(exc))
        else:
            parent._set_state(
                run_module.STATE_COMPLETED, summary="Requested finish confirmed"
            )
        return self.checkpoint()


@workflow.defn(name="MoonMind.UserWorkflow")
class FinishingResolverFixture:
    """Controlled provider transport; result consumption stays in MergeAutomation."""

    @workflow.run
    async def run(self, payload: dict) -> dict:
        return await workflow.execute_activity(
            "qualification.finish_resolver",
            payload,
            task_queue=run_module.INTEGRATIONS_TASK_QUEUE,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RetryPolicy(maximum_attempts=1),
        )


@pytest.mark.integration
@pytest.mark.filterwarnings("error::pytest.PytestUnraisableExceptionWarning")
@pytest.mark.parametrize(
    "finish_case",
    [
        "merged",
        "publication_ack",
        "resolver_dispatch",
        "merge_ack",
        "issue_ack",
        "finalization_unavailable",
        "finalization_forbidden",
        "foreign_pr",
        "foreign_repository",
        "operator_hold",
        "competing_owner",
        "already_merged",
        "cancel",
    ],
)
async def test_search_publication_finishes_same_issue_after_interruption(
    tmp_path,
    monkeypatch,
    artifact_service,
    finish_case,
):
    from temporalio.api.enums.v1 import IndexedValueType
    from temporalio.client import WorkflowFailureError

    from moonmind.workflows.temporal.artifacts import TemporalArtifactActivities
    from tests.integration.reliability.test_resolver_verification_capability_journey import (
        resolver_test_client,
    )
    from tests.unit.workflows.temporal.workflows.test_run_integration import (
        _mock_resilience_policy_envelope,
    )

    parent_id = "search-publication-finish-" + uuid4().hex
    handoff = await _search_publication_handoff(
        tmp_path,
        monkeypatch,
        {},
        0,
        True,
        artifact_service,
        finish_case=finish_case,
        parent_id=parent_id,
    )
    monkeypatch.setenv("GITHUB_TOKEN", "fixture-only")
    # This fixture uses the existing deployment-default repository route. The
    # separate admitted-connection journey exercises its frozen authority.
    monkeypatch.setattr(
        managed_api_key_resolve,
        "load_admitted_repository_access",
        AsyncMock(return_value=("", False)),
    )
    monkeypatch.setattr(
        managed_api_key_resolve,
        "load_repository_connection_for_launch",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        managed_api_key_resolve,
        "acquire_admitted_repository_use",
        AsyncMock(return_value=(None, None)),
    )

    client = await resolver_test_client(
        additional_search_attributes={
            "mm_updated_at": IndexedValueType.INDEXED_VALUE_TYPE_DATETIME,
            "mm_started_at": IndexedValueType.INDEXED_VALUE_TYPE_DATETIME,
            "mm_scheduled_for": IndexedValueType.INDEXED_VALUE_TYPE_DATETIME,
            "mm_integration": IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
            "mm_has_dependencies": IndexedValueType.INDEXED_VALUE_TYPE_BOOL,
            "mm_dependency_count": IndexedValueType.INDEXED_VALUE_TYPE_INT,
            "mm_current_step_order": IndexedValueType.INDEXED_VALUE_TYPE_INT,
            "mm_target_runtime": IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD_LIST,
            "mm_target_skill": IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD_LIST,
            "mm_title": IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD_LIST,
        }
    )
    monkeypatch.setattr(
        run_module.settings,
        "temporal",
        run_module.settings.temporal.model_copy(
            update={"address": client.service_client.config.target_host},
        ),
    )
    from moonmind.workflows.temporal import issue_claim_store as claim_store_module
    from moonmind.workflows.temporal import story_output_tools

    # Renewal follows the real Activity/Temporal ancestry into the same isolated
    # SQL receipt used by the trusted search, never the installed database.
    monkeypatch.setattr(
        claim_store_module, "IssueClaimStore", story_output_tools.IssueClaimStore
    )
    artifacts = TemporalArtifactActivities(artifact_service)
    resolver_started = asyncio.Event()
    resume_resolver = asyncio.Event()
    finalized = []
    resolver_requests = []

    @activity.defn(name="artifact.read")
    async def read(payload: dict) -> bytes:
        ref = payload["artifact_ref"]
        if ref == "art_plan":
            return json.dumps(handoff.plan).encode()
        if ref == handoff.snapshot.artifact_ref:
            return json.dumps(
                {
                    "skills": [
                        _default_registry_skill_payload(name=definition.name)
                        for definition in handoff.snapshot.skills
                    ]
                }
            ).encode()
        return await artifacts.artifact_read(payload)

    @activity.defn(name="artifact.create")
    async def create(payload: dict):
        return await artifacts.artifact_create(**payload)

    @activity.defn(name="artifact.write_complete")
    async def write(payload: dict):
        return await artifacts.artifact_write_complete(payload)

    @activity.defn(name="provider_profile.list")
    async def profiles(payload: dict) -> dict:
        return {"profiles": []}

    @activity.defn(name="resilience.compile_policy")
    async def resilience(payload: dict) -> dict:
        return _mock_resilience_policy_envelope(payload)

    @activity.defn(name="mm.tool.execute")
    async def tool(payload: dict):
        result = await handoff.invoke(payload["invocation_payload"])
        assert result.status == "COMPLETED", result.outputs
        if finish_case in {"operator_hold", "competing_owner"}:
            from moonmind.workflows.temporal.github_issue_attempts import (
                parse_attempt_comment,
                render_attempt_comment,
            )

            own = next(
                comment
                for comment in handoff.comments
                if str(comment["id"])
                == str(handoff.selected["issueClaimLease"]["commentId"])
            )
            previous = parse_attempt_comment(own["body"]).handoff
            contender = replace(
                previous,
                attempt_id="att-other-owner",
                workflow_id="another-parent",
                activity="attention" if finish_case == "operator_hold" else "active",
                operator_hold=finish_case == "operator_hold",
            )
            handoff.comments.append(
                {
                    "id": len(handoff.comments) + 1,
                    "body": render_attempt_comment(contender),
                    "user": own["user"],
                }
            )
            handoff.remote["guard_start"] = len(handoff.operations)
        if finish_case == "already_merged":
            async with httpx.AsyncClient() as github:
                response = await github.put(
                    f"https://api.github.com/repos/{handoff.repository}/pulls/9999/merge",
                    json={"sha": handoff.pr["head"]["sha"]},
                )
            assert response.json()["merged"]
        return result

    @activity.defn(name="github_issue.renew_claim")
    async def renew(payload: dict):
        return await TemporalIntegrationActivities.github_issue_renew_claim(
            object(), payload
        )

    @activity.defn(name="merge_automation.evaluate_readiness")
    async def readiness(payload: dict):
        return await TemporalIntegrationActivities.merge_automation_evaluate_readiness(
            object(), payload
        )

    @activity.defn(name="merge_automation.complete_post_merge_github")
    async def finalize(payload: dict):
        waiting = await client.get_workflow_handle(parent_id).query(
            SearchPublicationFinishJourney.checkpoint,
        )
        assert waiting["status"]["awaiting_external"]
        assert waiting["status"]["state"] == "awaiting_external"
        result = await TemporalIntegrationActivities.merge_automation_complete_post_merge_github(
            object(), payload
        )
        finalized.append(result)
        # Effect accepted, Activity completion lost: Temporal retries the same
        # Activity and the trusted owner must observe closed before mutating.
        if finish_case == "issue_ack" and len(finalized) == 1:
            raise ApplicationError("lost finalization activity acknowledgement")
        return result

    @activity.defn(name="qualification.finish_resolver")
    async def resolver(payload: dict):
        resolver_requests.append(payload)
        resolver_started.set()
        if finish_case in {"resolver_dispatch", "cancel"}:
            await resume_resolver.wait()
        async with httpx.AsyncClient() as github:
            response = await github.put(
                f"https://api.github.com/repos/{handoff.repository}/pulls/9999/merge",
                json={"sha": handoff.pr["head"]["sha"]},
            )
        assert response.json()["merged"]
        if finish_case == "foreign_pr":
            handoff.pr["number"] = 9998
        if finish_case == "merge_ack":
            raise ApplicationError(
                "remote merged before resolver acknowledgement", non_retryable=True
            )
        # Same normalized shape consumed from the real resolver terminal owner;
        # provider work is controlled, never another semantic interpretation.
        return {
            "status": "success",
            "mergeAutomationDisposition": "merged",
            "headSha": handoff.pr["head"]["sha"],
        }

    handoff.parameters["mergeAutomation"] = {
        "enabled": True,
        "checks": "optional",
        "automatedReview": "optional",
        "fallbackPollSeconds": 1,
    }
    payload = {
        "repository": handoff.repository,
        "selected": handoff.selected,
        "assessment": handoff.assessment,
        "evidence": handoff.evidence,
        "parameters": handoff.parameters,
    }
    if finish_case == "foreign_repository":
        payload["candidateUrl"] = "https://github.com/another/repository/pull/9999"
    workflow_types = [
        SearchPublicationFinishJourney,
        merge_module.MoonMindMergeAutomationWorkflow,
        FinishingResolverFixture,
    ]
    child_queue = run_module.settings.temporal.user_workflow_v2_task_queue
    activity_queues = {
        merge_module.ARTIFACTS_TASK_QUEUE,
        run_module.INTEGRATIONS_TASK_QUEUE,
        run_module.DEFAULT_ACTIVITY_CATALOG.resolve_activity(
            "provider_profile.list"
        ).task_queue,
        *(
            run_module.DEFAULT_ACTIVITY_CATALOG.resolve_skill(definition).task_queue
            for definition in handoff.snapshot.skills
        ),
    }
    activities = [
        read,
        create,
        write,
        profiles,
        resilience,
        tool,
        renew,
        readiness,
        finalize,
        resolver,
        repo_create_pr_activity,
    ]
    async with AsyncExitStack() as stack:
        for queue in activity_queues:
            await stack.enter_async_context(
                Worker(client, task_queue=queue, activities=activities)
            )
        worker = Worker(
            client,
            task_queue=child_queue,
            workflows=workflow_types,
            workflow_runner=UnsandboxedWorkflowRunner(),
        )
        await worker.__aenter__()
        try:
            handle = await client.start_workflow(
                SearchPublicationFinishJourney.run,
                payload,
                id=parent_id,
                task_queue=child_queue,
                execution_timeout=timedelta(seconds=90),
            )
            if finish_case in {"resolver_dispatch", "cancel"}:
                await asyncio.wait_for(resolver_started.wait(), timeout=30)
                saved = await handle.query(SearchPublicationFinishJourney.checkpoint)
                assert saved["status"]["awaiting_external"]
                assert saved["status"]["state"] == "awaiting_external"
                assert saved["publishContext"]["pullRequestUrl"] == handoff.pr_url
                if finish_case == "cancel":
                    await handle.cancel()
                    with pytest.raises(WorkflowFailureError):
                        await handle.result()
                    assert handoff.remote["merges"] == 0
                    assert handoff.remote["created"]
                    assert saved["acceptedHead"] == list(
                        handoff.evidence["acceptedRepositoryEvidence"][key]
                        for key in ("branch", "headSha")
                    )
                    return
                await worker.__aexit__(None, None, None)
                worker = Worker(
                    client,
                    task_queue=child_queue,
                    workflows=workflow_types,
                    workflow_runner=UnsandboxedWorkflowRunner(),
                )
                await worker.__aenter__()
                gc.collect()
                try:
                    restored = await handle.query(
                        SearchPublicationFinishJourney.checkpoint
                    )
                except Exception:
                    await handle.terminate(reason="Failed isolated journey cleanup")
                    raise
                assert restored == saved
                resume_resolver.set()
            result = await asyncio.wait_for(handle.result(), timeout=65)
            memo = await (await handle.describe()).memo()
            history = await handle.fetch_history()
        finally:
            await worker.__aexit__(None, None, None)

    guarded = finish_case in {"foreign_repository", "operator_hold", "competing_owner"}
    failed_finalization = finish_case in {
        "finalization_unavailable",
        "finalization_forbidden",
        "foreign_pr",
    }
    expected = "failed" if guarded or failed_finalization else "completed"
    assert result["status"]["state"] == expected, result
    context = result["publishContext"]
    assert context["pullRequestUrl"] == handoff.pr_url
    assert result["acceptedHead"] == [
        handoff.pr["head"]["ref"],
        handoff.pr["head"]["sha"],
    ]
    scheduled = [
        event.activity_task_scheduled_event_attributes.activity_type.name
        for event in history.events
        if event.HasField("activity_task_scheduled_event_attributes")
    ]
    assert scheduled.count("repo.create_pr") == scheduled.count("mm.tool.execute") == 1
    children = [
        event.start_child_workflow_execution_initiated_event_attributes
        for event in history.events
        if event.HasField("start_child_workflow_execution_initiated_event_attributes")
    ]
    assert len(children) == (0 if guarded else 1)

    from api_service.api.routers.executions import _serialize_execution
    from api_service.db.models import (
        MoonMindWorkflowState,
        TemporalExecutionCloseStatus,
        TemporalExecutionOwnerType,
        TemporalExecutionRecord,
        TemporalWorkflowType,
    )

    now = datetime.now(timezone.utc)
    api = _serialize_execution(
        TemporalExecutionRecord(
            namespace=client.namespace,
            workflow_id=parent_id,
            run_id=handle.first_execution_run_id,
            workflow_type=TemporalWorkflowType.USER_WORKFLOW,
            owner_id="test:search-publication",
            owner_type=TemporalExecutionOwnerType.USER,
            state=MoonMindWorkflowState(expected),
            close_status=TemporalExecutionCloseStatus.COMPLETED,
            entry="run",
            memo=memo,
            search_attributes={},
            parameters=handoff.parameters,
            artifact_refs=[],
            created_at=now,
            started_at=now,
            updated_at=now,
            closed_at=now,
            paused=False,
            attention_required=False,
        )
    )
    assert api.objective_outcome == ("failed" if expected == "failed" else "succeeded")
    assert api.pr_url == handoff.pr_url
    if guarded:
        assert (
            handoff.remote["created"]
            and handoff.remote["merges"] == handoff.remote["closes"] == 0
        )
        assert not resolver_requests and not finalized
        if handoff.remote["guard_start"] is not None:
            assert all(
                method == "GET"
                for method, _ in handoff.operations[handoff.remote["guard_start"] :]
            )
        return
    merge_result = context["mergeAutomationResult"]
    disposition = (
        "already_merged" if finish_case in {"merge_ack", "already_merged"} else "merged"
    )
    assert merge_result["status"] == ("failed" if failed_finalization else disposition)
    assert merge_result["postMergeGithub"]["status"] == (
        "failed" if expected == "failed" else "succeeded"
    )
    assert handoff.issue["state"] == ("open" if expected == "failed" else "closed")
    assert handoff.pr["merged"] and handoff.remote["merges"] == 1
    assert handoff.remote["closes"] == (0 if expected == "failed" else 1)
    assert len(resolver_requests) == (0 if finish_case == "already_merged" else 1)
    assert len(merge_result["resolverChildWorkflowIds"]) == len(resolver_requests)
    assert api.merge_automation.status == merge_result["status"]
    assert api.merge_automation.workflow_id == children[0].workflow_id
    assert len(finalized) == (2 if finish_case == "issue_ack" else 1)
    assert (
        sum(
            method == "POST" and path.endswith("/pulls")
            for method, path in handoff.operations
        )
        == 1
    )
    assert not any(
        "/reviews" in path and method == "POST" for method, path in handoff.operations
    )
