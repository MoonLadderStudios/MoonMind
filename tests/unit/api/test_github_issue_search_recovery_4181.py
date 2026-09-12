"""Recovery-aware acceptance coverage for #4181 (search-and-implement preset).

Exercises the preset integration the assessment found missing: seed expansion,
actual Activity inputs, source pinning/replay, publication authority, and
terminal finalization beyond YAML string assertions.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from tests.support.issue_claims import issue_claim_store  # noqa: F401
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
from moonmind.workflows.temporal.github_issue_lifecycle import interpret_issue
from moonmind.workflows.temporal.github_issue_search import (
    is_lifecycle_selectable_candidate,
)
from moonmind.workflows.temporal.story_output_tools import (
    register_story_output_tool_handlers,
)

REPOSITORY = "MoonLadderStudios/MoonMind"
PRESET = "github-issue-search-and-implement"
SEED = Path("api_service/data/presets/github-issue-search-and-implement.yaml")


def _seed_text() -> str:
    return SEED.read_text(encoding="utf-8")


def test_seed_uses_shared_eligibility_language():
    text = _seed_text()
    assert "Available means no MoonMind status label" in text
    assert "Recovery-needed" in text
    assert "mixed/unknown" in text
    assert "unresolved active" in text
    assert "marking status in-progress), or the first unblocked issue skipping in-progress" not in text
    assert "skipping issues already marked status in-progress" not in text
    assert "without an\n          in-progress status" not in text


def test_seed_has_no_new_ordinary_inputs_and_no_second_recovery_preset():
    import yaml

    seed = yaml.safe_load(_seed_text())
    assert sorted(i["name"] for i in seed["inputs"]) == [
        "constraints",
        "issue_search",
        "repository",
        "run_verify",
    ]
    slugs = [
        s.get("preset_slug")
        for s in seed["steps"]
        if isinstance(s, dict) and s.get("type") == "preset"
    ]
    assert slugs == []


def test_seed_composes_recovery_aware_journey():
    text = _seed_text()
    for phrase in [
        "shared lifecycle eligibility decision",
        "bounded fair policy",
        "distinct no-eligible-candidate",
        "Do not silently widen the query",
        "exhausted retry history",
        "announces before expensive assessment",
        "Preserve the resolved issue and predecessor across replay/retry",
        "never repeat search",
        "reset a continuation to the base branch",
        "second PR",
        "failed, blocked, canceled, and exhausted",
        "Keep internal remediation within",
        "inherit cross-device retry/cooldown/hold history",
        "partial PR does not constitute",
        "code-review-handoff",
        "durable failed-attempt finalization boundary",
        "success-ordered preset node",
    ]:
        assert phrase in text, phrase
    # Terminal finalization is owned by the durable boundary, never by a
    # success-ordered preset node: the engine skips remaining plan nodes
    # after a failed/blocked result, so no sequential step may claim the
    # terminal handler role.
    assert "failed-attempt-finalization" not in text


def _issue(number=4025, **overrides):
    base = {
        "number": number,
        "state": "open",
        "title": "Recovery acceptance",
        "body": "Acceptance body.",
        "html_url": f"https://github.com/{REPOSITORY}/issues/{number}",
        "labels": [{"name": "bug"}],
    }
    base.update(overrides)
    return base


@pytest.fixture
def activity_boundary(monkeypatch):
    requests: list = []
    pages: list = [[_issue()]]
    detail = _issue()
    comments = []

    def handler(request):
        requests.append(request)
        if request.url.path == "/user":
            return httpx.Response(200, json={"id": 123, "login": "fixture-owner"})
        if "/comments" in request.url.path:
            if request.method == "GET":
                return httpx.Response(200, json=comments)
            comment = {"id": len(comments) + 1, "body": json.loads(request.content)["body"], "user": {"id": 123}}
            comments.append(comment)
            return httpx.Response(200, json=comment)
        if request.method == "DELETE" and "/labels/" in request.url.path:
            # Simulate GitHub label removal so recovery-claim re-reads settle.
            from urllib.parse import unquote

            removed = unquote(request.url.path.rsplit("/labels/", 1)[1])
            detail["labels"] = [
                label
                for label in detail["labels"]
                if label.get("name") != removed
            ]
            return httpx.Response(200, json=detail["labels"])
        if request.method == "POST" and request.url.path.endswith("/labels"):
            for label in json.loads(request.content)["labels"]:
                if {"name": label} not in detail["labels"]:
                    detail["labels"].append({"name": label})
            return httpx.Response(200, json=detail["labels"])
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
        "github.finalize_failed_attempt",
        "github.resolve_pull_request_target",
    )
    snapshot = ToolRegistrySnapshot(
        digest="reg:sha256:4181",
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
            principal="test:issue-search-4181",
            invocation_payload={
                "id": "step-4181",
                "tool": {"type": "skill", "name": name},
                "inputs": inputs,
            },
            context={
                "namespace": "default",
                "workflow_id": "mm:4181",
                "run_id": "run",
                "node_id": "step",
            },
            idempotency_key="step-4181:execute",
        )

    return SimpleNamespace(
        execute=execute, pages=pages, requests=requests, detail=detail
    )


@pytest.mark.parametrize(
    "labels",
    [
        [{"name": "status: code-review"}],
        [{"name": "status: needs-attention"}],
        [{"name": "status: in-progress"}],
        [{"name": "status: code-review"}, {"name": "status: in-progress"}],
        [{"name": "status: something-new"}],
    ],
)
def test_lifecycle_exclusion_blocks_duplicate_implementation(labels):
    candidate = {"state": "open", "labels": [label["name"] for label in labels]}
    assert not is_lifecycle_selectable_candidate(candidate)
    assert interpret_issue(candidate).settled not in {
        "available",
        "recovery_needed",
    }
    # Active-attempt evidence blocks even when the label is missing.
    assert not is_lifecycle_selectable_candidate(
        {"state": "open", "labels": ["bug"]},
        {"hasUnresolvedActiveAttempt": True},
    )


@pytest.mark.asyncio
async def test_review_labeled_candidate_is_not_selected_twice(activity_boundary):
    activity_boundary.pages[:] = [
        [_issue(100, labels=[{"name": "status: code-review"}]), _issue(101)]
    ]
    activity_boundary.detail.update(_issue(101))
    result = await activity_boundary.execute(
        "github.load_issue_preset_brief",
        {"repository": REPOSITORY, "issueSearch": ""},
    )
    assert result.status == "COMPLETED"
    assert result.outputs["issue"]["number"] == 101
    assert result.outputs["searchEvidence"]["candidatesExamined"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "inputs",
    [{}, {"repository": "", "issue_search": ""}, {"issue_search": "dashboard"}],
)
async def test_omitted_blank_and_nonblank_inputs_traverse_compiled_path(
    tmp_path, activity_boundary, inputs
):
    seeds = tmp_path / "seeds"
    seeds.mkdir()
    shutil.copy(SEED, seeds / SEED.name)
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
    load_tool = steps[0]["tool"]
    assert load_tool["id"] == "github.load_issue_preset_brief"
    result = await activity_boundary.execute(load_tool["id"], load_tool["inputs"])
    assert result.status == "COMPLETED"
    assert result.outputs["issue"]["number"] == 4025
    if inputs.get("issue_search"):
        assert result.outputs["searchEvidence"]["fallbackScanning"] is False
    else:
        assert result.outputs["searchEvidence"]["fallbackScanning"] is True
    # Publication authority is preserved through the compiled plan.
    roles = [
        (s.get("annotations") or {}).get("issueImplementRole")
        for s in steps
        if isinstance(s, dict)
    ]
    assert "pull-request-handoff" in roles
    assert "code-review-handoff" in roles
    # No success-ordered preset node may claim terminal finalization: the
    # engine skips remaining plan nodes after a failed/blocked result, so
    # failed/canceled/exhausted outcomes are owned by the durable
    # failed-attempt boundary plus the reconciler.
    assert "failed-attempt-finalization" not in roles
    tool_ids = [s["tool"]["id"] for s in steps if isinstance(s, dict) and s.get("tool")]
    assert "github.update_issue_status" in tool_ids
    assert "github.finalize_failed_attempt" not in tool_ids
    # The recovery-evidence receive path is compiled into the first step so
    # a scheduler/reconciler-queued continuation reaches the trusted tool.
    # With no continuation context these render empty (correctly ignored
    # downstream: a fresh invocation admits only Available candidates).
    for key in ("predecessorStopped", "handoffUsable", "priorWorkPullRequest"):
        assert key in load_tool["inputs"], key
    assert load_tool["inputs"]["predecessorStopped"] == ""
    assert load_tool["inputs"]["handoffUsable"] == ""
    assert load_tool["inputs"]["priorWorkPullRequest"] == ""
    # The success-path finalization step documents durable terminal ownership.
    finalize = next(
        s for s in steps if s.get("title") == "Finalize resolved GitHub issue status"
    )
    assert finalize["tool"]["inputs"]["mode"] == "finalize_after_pr_or_done"
    assert "durable failed-attempt finalization boundary" in finalize["instructions"]
    # Existing-PR continuation is an explicit journey step, not a hidden fork.
    resolve = next(
        s
        for s in steps
        if s.get("title") == "Resolve existing pull request target"
    )
    assert "github.resolve_pull_request_target" in resolve["instructions"]
    assert "validated current head" in resolve["instructions"]


@pytest.mark.asyncio
async def test_recovery_candidate_skipped_without_handoff_evidence(activity_boundary):
    # A fresh invocation without continuation evidence correctly admits only
    # Available candidates: the Recovery-needed candidate is passed over.
    activity_boundary.pages[:] = [
        [_issue(200, labels=[{"name": "status: recovery-needed"}]), _issue(201)]
    ]
    activity_boundary.detail.update(_issue(201))
    result = await activity_boundary.execute(
        "github.load_issue_preset_brief",
        {"repository": REPOSITORY, "issueSearch": ""},
    )
    assert result.status == "COMPLETED"
    assert result.outputs["issue"]["number"] == 201
    assert "predecessor_stopped" not in result.outputs
    assert "prior_work_pull_request" not in result.outputs


@pytest.mark.asyncio
async def test_recovery_evidence_from_top_level_context_admits_continuation(
    activity_boundary,
):
    # Continuation evidence queued by a scheduler/reconciler arrives through
    # the trusted workflow input channel (top-level context, no new ordinary
    # user inputs) and the brief routes it plus the exact prior PR selector
    # to the start transition and the existing-PR resolution step.
    from moonmind.workflows.temporal.story_output_tools import (
        load_github_issue_preset_brief,
    )

    pr_url = f"https://github.com/{REPOSITORY}/pull/99"
    activity_boundary.pages[:] = [
        [_issue(200, labels=[{"name": "status: recovery-needed"}])]
    ]
    activity_boundary.detail.update(
        _issue(200, labels=[{"name": "status: recovery-needed"}])
    )
    result = await load_github_issue_preset_brief(
        {"repository": REPOSITORY, "issueSearch": ""},
        {
            "predecessorStopped": True,
            "handoffUsable": True,
            "priorWorkPullRequest": pr_url,
        },
    )
    assert result.status == "COMPLETED"
    assert result.outputs["issue"]["number"] == 200
    assert result.outputs["predecessor_stopped"] is True
    assert result.outputs["handoff_usable"] is True
    assert result.outputs["prior_work_pull_request"] == pr_url
    assert result.outputs["priorWorkPullRequest"] == pr_url
