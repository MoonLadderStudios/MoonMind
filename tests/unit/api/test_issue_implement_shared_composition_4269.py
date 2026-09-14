"""Shared issue-implementation composition (MoonLadderStudios/MoonMind#4269).

Explicit GitHub, search-selected GitHub, and Jira issues compose one common
assessment/work/verification/publication flow through the shared
``issue-implement-assessment`` and ``issue-implement-work-pr`` includes.
The search preset keeps only selection plus thin provider-typed steps
(blocker, status, existing-PR, finalize); the common stages come from the
shared includes with runtime-bound issue identity (trusted previous step
context or brief artifact, never prose inference).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import yaml
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import Base
from api_service.services.presets.catalog import PresetCatalogService

pytestmark = [pytest.mark.asyncio]

REPO_ROOT = Path(__file__).resolve().parents[3]
PRESET_DIR = REPO_ROOT / "api_service" / "data" / "presets"
SEARCH = "github-issue-search-and-implement"
EXPLICIT = "github-issue-implement"
REPOSITORY = "MoonLadderStudios/MoonMind"


@asynccontextmanager
async def catalog_service(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/catalog_4269.db")
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        async with maker() as session:
            service = PresetCatalogService(session)
            await service.sync_seed_templates(seed_dir=PRESET_DIR)
            yield service
    finally:
        await engine.dispose()


def _raw(slug: str) -> dict:
    return yaml.safe_load((PRESET_DIR / f"{slug}.yaml").read_text())


def _includes(raw: dict) -> list[dict]:
    return [s for s in raw.get("steps", []) if s.get("kind") == "include"]


def _inlined_stage_titles(raw: dict) -> list[str]:
    return [str(s.get("title") or "") for s in raw.get("steps", [])]


async def test_search_composes_shared_assessment_and_work_pr(tmp_path):
    async with catalog_service(tmp_path) as service:
        expanded = await service.expand_template(
            slug=SEARCH,
            scope="global",
            scope_ref=None,
            inputs={},
            context={"repository": REPOSITORY},
        )
    slugs = [item["presetSlug"] for item in expanded["authoredPresets"]]
    assert "issue-implement-assessment" in slugs
    assert "issue-implement-work-pr" in slugs
    titles = [step.get("title", "") for step in expanded["steps"]]
    assert titles[0] == "Resolve GitHub issue and load trusted brief"
    assert titles.count("Assess existing implementation state") == 1
    assert titles.count("Implement the issue") == 1
    assert titles.count("Verify implementation") == 1
    assert titles.count("Remediation loop controller") == 1
    assert titles.count("Create pull request") == 1
    # One selection step only: continuation resumes without a second search.
    assert titles.count("Resolve GitHub issue and load trusted brief") == 1
    assert not any("search" in title.lower() and "resolve" in title.lower() for title in titles[1:])
    # The shared work-pr PR handoff is the single PR publication step.
    pr_steps = [
        step
        for step in expanded["steps"]
        if (step.get("annotations") or {}).get("issueImplementRole")
        == "pull-request-handoff"
    ]
    assert len(pr_steps) == 1


async def test_search_keeps_single_typed_existing_pr_resolution(tmp_path):
    async with catalog_service(tmp_path) as service:
        expanded = await service.expand_template(
            slug=SEARCH,
            scope="global",
            scope_ref=None,
            inputs={},
            context={"repository": REPOSITORY},
        )
    resolver_steps = [
        step
        for step in expanded["steps"]
        if (step.get("annotations") or {}).get("issueImplementRole")
        == "existing-pr-resolution"
    ]
    assert len(resolver_steps) == 1
    tool = resolver_steps[0]["tool"]
    assert tool["id"] == "github.resolve_pull_request_target"
    assert "pullRequest" in tool["inputs"]
    # No bespoke skill-auto resolver remains for this role.
    assert not any(
        (step.get("annotations") or {}).get("issueImplementRole")
        == "existing-pr-resolution"
        and step.get("type") == "skill"
        for step in expanded["steps"]
    )


async def test_explicit_and_search_share_normalized_stage_behavior(tmp_path):
    async with catalog_service(tmp_path) as service:
        explicit = await service.expand_template(
            slug=EXPLICIT,
            scope="global",
            scope_ref=None,
            inputs={
                "github_issue": {"repository": REPOSITORY, "number": 4269},
            },
            context={"repository": REPOSITORY},
        )
        search = await service.expand_template(
            slug=SEARCH,
            scope="global",
            scope_ref=None,
            inputs={},
            context={"repository": REPOSITORY},
        )
    by_title = lambda steps: {step.get("title", ""): step for step in steps}
    explicit_steps = by_title(explicit["steps"])
    search_steps = by_title(search["steps"])
    for title in (
        "Assess existing implementation state",
        "Implement the issue",
        "Verify implementation",
        "Remediation loop controller",
        "Create pull request",
    ):
        assert title in explicit_steps and title in search_steps
    # Same normalized artifact paths flow through the shared stages.
    assert explicit_steps["Assess existing implementation state"]["skill"]["args"][
        "brief_artifact_path"
    ] == search_steps["Assess existing implementation state"]["skill"]["args"][
        "brief_artifact_path"
    ]
    assert explicit_steps["Verify implementation"]["skill"]["args"][
        "verify_artifact_path"
    ] == search_steps["Verify implementation"]["skill"]["args"][
        "verify_artifact_path"
    ]
    # Search binds the runtime-selected identity; explicit binds the concrete ref.
    assert explicit_steps["Assess existing implementation state"]["skill"]["args"][
        "issue_ref"
    ] == f"{REPOSITORY}#4269"
    assert search_steps["Assess existing implementation state"]["skill"]["args"][
        "issue_ref"
    ] in (None, "")
    # Finalize in both requires verification and PR evidence on the same paths.
    for steps in (explicit["steps"], search["steps"]):
        final = steps[-1]
        assert final["tool"]["id"] == "github.update_issue_status"
        assert final["tool"]["inputs"]["verificationArtifactPath"] == (
            "artifacts/github-issue-implement-verify.json"
        )
        assert final["tool"]["inputs"]["pullRequestArtifactPath"] == (
            "artifacts/github-issue-implement-pr.json"
        )
        assert final["tool"]["inputs"]["requireVerification"] is True


async def test_shared_stage_inputs_allow_runtime_bound_identity(tmp_path):
    async with catalog_service(tmp_path) as service:
        for slug, inputs in (
            (
                "issue-implement-assessment",
                {
                    "issue_provider": "github",
                    "brief_artifact_path": "artifacts/b.json",
                    "assessment_artifact_path": "artifacts/a.json",
                },
            ),
            (
                "issue-implement-work-pr",
                {
                    "issue_provider": "github",
                    "brief_artifact_path": "artifacts/b.json",
                    "assessment_artifact_path": "artifacts/a.json",
                    "pr_artifact_path": "artifacts/p.json",
                    "verify_artifact_path": "artifacts/v.json",
                },
            ),
        ):
            expanded = await service.expand_template(
                slug=slug,
                scope="global",
                scope_ref=None,
                inputs=inputs,
                context={},
            )
            assert expanded["steps"], slug


async def test_no_prose_verdict_line_controls_typed_tools():
    for slug in (
        "github-issue-implement",
        "github-issue-search-and-implement",
        "jira-implement",
        "issue-implement-assessment",
        "issue-implement-work-pr",
    ):
        text = (PRESET_DIR / f"{slug}.yaml").read_text()
        assert "Verdict: BLOCKED" not in text, slug


async def test_brief_persistence_stays_with_trusted_owner():
    assessment = (PRESET_DIR / "issue-implement-assessment.yaml").read_text()
    assert "Preserve source content at" not in assessment
    assert "trusted brief tool owns brief persistence" in assessment
    search = (PRESET_DIR / "github-issue-search-and-implement.yaml").read_text()
    assert "Preserve the original loaded brief" not in search


async def test_mechanical_reads_are_conditional_on_identity_freshness():
    for slug, artifact in (
        ("github-issue-implement", "artifacts/github-issue-implement-assessment.json"),
        (
            "github-issue-search-and-implement",
            "artifacts/github-issue-implement-assessment.json",
        ),
        ("issue-implement-work-pr", "assessment_artifact_path"),
    ):
        text = (PRESET_DIR / f"{slug}.yaml").read_text()
        assert "only when identity or freshness requires it" in text, slug
        assert artifact in text, slug


async def test_self_authored_default_survives_shared_expansion(tmp_path):
    async with catalog_service(tmp_path) as service:
        template = await service.get_template(
            slug=SEARCH, scope="global", scope_ref=None
        )
        assert template["defaults"]["include_all_authors"] is False
        defaulted = await service.expand_template(
            slug=SEARCH,
            scope="global",
            scope_ref=None,
            inputs={},
            context={"repository": REPOSITORY},
        )
        opted_in = await service.expand_template(
            slug=SEARCH,
            scope="global",
            scope_ref=None,
            inputs={"include_all_authors": True},
            context={"repository": REPOSITORY},
        )
    assert defaulted["steps"][0]["tool"]["inputs"]["includeAllAuthors"] is False
    assert opted_in["steps"][0]["tool"]["inputs"]["includeAllAuthors"] is True
    # Explicit-issue semantics are unchanged: no author filter exists there.
    explicit_raw = _raw(EXPLICIT)
    assert "include_all_authors" not in yaml.safe_dump(explicit_raw)


async def test_selection_preserves_continuation_without_research():
    text = (PRESET_DIR / f"{SEARCH}.yaml").read_text()
    assert "never repeat search during recovery of an already selected issue" in text
    assert "Preserve the resolved issue and predecessor across replay/retry" in text
    assert "priorWorkPullRequest" in text
    raw = _raw(SEARCH)
    assert len(_includes(raw)) == 2
    assert {item["slug"] for item in _includes(raw)} == {
        "issue-implement-assessment",
        "issue-implement-work-pr",
    }
    titles = _inlined_stage_titles(raw)
    assert "Implement resolved issue" not in titles
    assert "Verify implementation" not in titles


async def test_status_mutation_stays_separate_from_git_publication():
    search = (PRESET_DIR / f"{SEARCH}.yaml").read_text()
    assert "durable failed-attempt finalization boundary" in search
    work_pr = _raw("issue-implement-work-pr")
    pr_step = next(
        step
        for step in work_pr["steps"]
        if (step.get("annotations") or {}).get("issueImplementRole")
        == "pull-request-handoff"
    )
    assert "controlling post-remediation moonspec-verify verdict" in pr_step[
        "instructions"
    ]


async def test_untrusted_identity_cannot_drive_composed_status_step(
    tmp_path, monkeypatch
):
    """The composed status step resolves identity from provenance-checked context.

    Mirrors the production tool boundary: an untrusted or conflicting
    previousOutputs fails before any GitHub mutation.
    """

    from moonmind.workflows.adapters.github_service import GitHubService
    from moonmind.workflows.skills.tool_dispatcher import ToolActivityDispatcher
    from moonmind.workflows.skills.tool_plan_contracts import parse_tool_definition
    from moonmind.workflows.skills.tool_registry import ToolRegistrySnapshot
    from moonmind.workflows.temporal.activity_runtime import (
        TemporalSkillActivities,
        _default_registry_skill_payload,
    )

    requests: list = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={})

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: httpx.AsyncClient(
            transport=httpx.MockTransport(handler), **kwargs
        ),
    )
    monkeypatch.setattr(
        GitHubService,
        "resolve_github_token",
        AsyncMock(return_value=("test-only", None)),
    )
    dispatcher = ToolActivityDispatcher()
    snapshot = ToolRegistrySnapshot(
        digest="reg:4269",
        artifact_ref="art_registry",
        skills=tuple(
            parse_tool_definition(_default_registry_skill_payload(name=name))
            for name in ("github.update_issue_status",)
        ),
    )
    activities = TemporalSkillActivities(
        dispatcher=dispatcher,
        artifact_service=SimpleNamespace(
            read=AsyncMock(return_value=(None, {})),
            create=AsyncMock(),
            write_complete=AsyncMock(),
        ),
    )

    async def execute(inputs):
        return await activities.mm_tool_execute(
            registry_snapshot=snapshot,
            principal="test:4269",
            invocation_payload={
                "id": "status-step",
                "tool": {"type": "skill", "name": "github.update_issue_status"},
                "inputs": inputs,
            },
            context={
                "namespace": "default",
                "workflow_id": "mm:4269",
                "run_id": "run",
                "node_id": "step",
            },
            idempotency_key="status-step:execute",
        )

    async with catalog_service(tmp_path) as service:
        expanded = await service.expand_template(
            slug=SEARCH,
            scope="global",
            scope_ref=None,
            inputs={},
            context={"repository": REPOSITORY},
        )
    status = next(
        step
        for step in expanded["steps"]
        if step.get("title") == "Mark resolved GitHub issue In Progress"
    )
    base_inputs = dict(status["tool"]["inputs"])
    untrusted = await execute(
        {**base_inputs, "previousOutputs": {"issue": {"number": 4269}}}
    )
    assert untrusted.status == "FAILED"
    conflicting = await execute(
        {
            **base_inputs,
            "previousOutputs": {
                "trustedSource": "moonmind.github.get_issue",
                "issue": {
                    "repository": REPOSITORY,
                    "number": 4269,
                },
            },
        }
    )
    assert conflicting.status == "FAILED"
    assert requests == []
