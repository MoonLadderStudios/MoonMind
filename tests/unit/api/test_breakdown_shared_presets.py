"""Consolidated breakdown workflow tests for MoonLadderStudios/MoonMind#4272.

Covers the five breakdown presets expanding shared components with explicit
provider/child bindings, source-role preservation, issueCreation canonical
field, semantic coverage, ordering/dependency propagation, and disposition
semantics through the real preset -> schema -> tool request path.
"""

from __future__ import annotations

import shutil
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import Base, Preset, PresetScopeType
from api_service.services.presets.catalog import PresetCatalogService
from moonmind.workflows.temporal import story_output_tools as story_tools

pytestmark = [pytest.mark.asyncio]

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PRESET_DIR = _REPO_ROOT / "api_service" / "data" / "presets"
_SKILLS_DIR = _REPO_ROOT / ".agents" / "skills"


@asynccontextmanager
async def _catalog_db(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path}/breakdown_shared.db"
    engine = create_async_engine(db_url, future=True)
    async_session_maker = sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield async_session_maker
    finally:
        await engine.dispose()


def _seed_dir(tmp_path) -> Path:
    seed_dir = tmp_path / "presets"
    seed_dir.mkdir()
    for filename in (
        "jira-breakdown.yaml",
        "jira-breakdown-implement.yaml",
        "jira-breakdown-orchestrate.yaml",
        "github-issue-breakdown-implement.yaml",
        "github-issue-breakdown-orchestrate.yaml",
        "story-breakdown-decompose.yaml",
        "story-breakdown-reconcile.yaml",
        "story-breakdown-publish.yaml",
    ):
        shutil.copy(_PRESET_DIR / filename, seed_dir / filename)
    return seed_dir


async def _load_preset(session, slug: str) -> Preset:
    result = await session.execute(
        select(Preset).where(
            Preset.slug == slug,
            Preset.scope_type == PresetScopeType.GLOBAL,
            Preset.scope_ref.is_(None),
        )
    )
    return result.scalar_one()


_JIRA_INPUTS = {
    "feature_request": "Inline breakdown source.",
    "source_design_path": "",
    "source_issue_key": "",
    "jira_project_key": "MM",
    "jira_issue_type": "Story",
    "jira_dependency_mode": "linear_blocker_chain",
}

_GITHUB_INPUTS = {
    "feature_request": "Inline breakdown source.",
    "source_design_path": "",
    "github_repository": "MoonLadderStudios/MoonMind",
    "publish_mode": "pr",
    "source_issue_key": "",
}


@pytest.mark.parametrize(
    ("slug", "provider", "child"),
    [
        ("jira-breakdown", "jira", "none"),
        ("jira-breakdown-implement", "jira", "jira-implement"),
        ("jira-breakdown-orchestrate", "jira", "jira-orchestrate"),
        ("github-issue-breakdown-implement", "github", "github-issue-implement"),
        ("github-issue-breakdown-orchestrate", "github", "github-issue-orchestrate"),
    ],
)
async def test_all_five_presets_expand_shared_components(tmp_path, slug, provider, child):
    async with _catalog_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = PresetCatalogService(session)
            await service.sync_seed_templates(seed_dir=_seed_dir(tmp_path))
            template = await _load_preset(session, slug)

            slugs = [step.get("slug") for step in template.steps]
            assert slugs[0] == "story-breakdown-decompose"
            assert slugs[-1] == "story-breakdown-publish"
            if slug == "jira-breakdown":
                assert slugs == [
                    "story-breakdown-decompose",
                    "story-breakdown-publish",
                ]
            else:
                assert slugs == [
                    "story-breakdown-decompose",
                    "story-breakdown-reconcile",
                    "story-breakdown-publish",
                ]
            for step in template.steps:
                assert step.get("kind") == "include"
                assert step["inputMapping"]["provider_target"] == provider
            assert template.steps[-1]["inputMapping"]["child_preset"] == child

            inputs = dict(_JIRA_INPUTS if provider == "jira" else _GITHUB_INPUTS)
            if provider == "jira" and "implement" in slug:
                inputs["publish_mode"] = "pr_with_merge_automation"
                inputs["run_verify"] = True
            elif provider == "jira":
                inputs["publish_mode"] = "pr_with_merge_automation"
                inputs["run_verify"] = True
            else:
                inputs["run_verify"] = True
            expanded = await service.expand_template(
                slug=slug,
                scope="global",
                scope_ref=None,
                inputs=inputs,
                context={"targetRuntime": "codex", "repository": "MoonLadderStudios/MoonMind"},
            )

    skills = [(step.get("skill") or step.get("tool"))["id"] for step in expanded["steps"]]
    assert skills[0] in {"jira.load_preset_brief", "moonspec-breakdown"} or skills[0] == "moonspec-breakdown"
    assert "moonspec-breakdown" in skills
    assert any(
        skill_id.startswith("story.create_") for skill_id in skills
    )
    if slug != "jira-breakdown":
        assert "story-reconcile-implementation" in skills
    else:
        assert "story-reconcile-implementation" not in skills

    all_instructions = "\n".join(step.get("instructions", "") for step in expanded["steps"])
    assert "jiraCreation" not in all_instructions
    assert "issueCreation" in all_instructions


async def test_creation_only_vs_composite_choice_is_explicit(tmp_path):
    async with _catalog_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = PresetCatalogService(session)
            await service.sync_seed_templates(seed_dir=_seed_dir(tmp_path))
            creation = await _load_preset(session, "jira-breakdown")
            composite = await _load_preset(session, "jira-breakdown-implement")

    assert len(creation.steps) == 2
    assert len(composite.steps) == 3
    assert creation.steps[-1]["inputMapping"]["child_preset"] == "none"
    assert composite.steps[-1]["inputMapping"]["child_preset"] == "jira-implement"
    assert all(step.get("slug") != "story-breakdown-reconcile" for step in creation.steps)
    assert any(step.get("slug") == "story-breakdown-reconcile" for step in composite.steps)


def test_source_roles_preserved_in_shared_decompose():
    text = (_PRESET_DIR / "story-breakdown-decompose.yaml").read_text(encoding="utf-8")
    assert "input preference chain" in text
    assert "traceability-only" in text
    assert "resolvedSourceDesignPath" in text
    assert "jiraPresetBrief" in text
    assert "source-resolution error" in text
    assert "canonical-declarative" in text
    assert "docs/tmp/" in text
    assert "do not fabricate" in text
    assert "imperative-input" in text
    assert "provider_target" in text


def test_moonspec_breakdown_classifies_by_meaning_and_validates_semantically():
    text = (_SKILLS_DIR / "moonspec-breakdown" / "SKILL.md").read_text(encoding="utf-8")
    assert "not merely by directory prefix" in text
    assert "never automatically canonical" in text
    assert "do not fabricate" in text
    assert "Extension Hooks Contract" in text
    assert "do not accept a literal" in text
    assert "alone never constitutes proof" in text


def test_reconcile_requires_credible_evidence_and_issue_creation_canonical():
    text = (_SKILLS_DIR / "story-reconcile-implementation" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    assert "credible implementation and verification evidence" in text
    assert "Mere existence of code" in text
    assert "Do not create speculative" in text
    assert "do not silently discard" in text
    assert "temporary historical decoder" in text
    assert "issueCreation" in text


def test_new_jira_and_github_paths_consume_issue_creation():
    for filename in (
        "story-breakdown-reconcile.yaml",
        "story-breakdown-publish.yaml",
        "github-issue-breakdown-implement.yaml",
        "github-issue-breakdown-orchestrate.yaml",
        "jira-breakdown.yaml",
        "jira-breakdown-implement.yaml",
        "jira-breakdown-orchestrate.yaml",
    ):
        text = (_PRESET_DIR / filename).read_text(encoding="utf-8")
        assert "jiraCreation" not in text, filename


def test_historical_jira_creation_decoder_with_cutover():
    historical = {
        "id": "STORY-001",
        "summary": "Legacy persisted story",
        "implementationStatus": "fully_implemented",
        "jiraCreation": {"action": "skip", "reason": "Persisted before migration."},
    }
    assert story_tools._story_issue_creation_action(historical) == "skip"
    record = story_tools._story_reconciliation_record(
        {"id": "STORY-001", "summary": "Legacy", "implementationStatus": "fully_implemented"},
        index=1,
        action="skip",
    )
    assert record["issueCreationAction"] == "skip"
    assert "jiraCreationAction" not in record


def test_dispositions_preserve_scope_without_dropping_requirements():
    stories = [
        {
            "id": "STORY-001",
            "summary": "Done",
            "description": "Original scope.",
            "acceptanceCriteria": ["A."],
            "requirements": ["R1."],
            "implementationStatus": "fully_implemented",
            "implementedEvidence": [
                {"requirement": "DESIGN-REQ-001", "status": "met", "evidence": "tests cover A."}
            ],
            "issueCreation": {"action": "skip", "reason": "Evidence."},
        },
        {
            "id": "STORY-002",
            "summary": "Partial",
            "description": "Original broad scope.",
            "acceptanceCriteria": ["A.", "B."],
            "requirements": ["R1.", "R2."],
            "implementationStatus": "partially_implemented",
            "implementedEvidence": [
                {"requirement": "DESIGN-REQ-001", "status": "met", "evidence": "A done."}
            ],
            "remainingWork": {
                "summary": "Finish B",
                "description": "Only B remains.",
                "acceptanceCriteria": ["B."],
                "requirements": ["R2."],
            },
            "issueCreation": {"action": "create_remaining_work_issue", "reason": "B remains."},
        },
        {
            "id": "STORY-003",
            "summary": "Missing",
            "description": "Original scope.",
            "acceptanceCriteria": ["C."],
            "requirements": ["R3."],
            "nonGoals": ["Out of scope stays out."],
            "implementationStatus": "not_implemented",
            "issueCreation": {"action": "create_issue", "reason": "No evidence."},
        },
        {
            "id": "STORY-004",
            "summary": "Unverifiable",
            "description": "Ambiguous scope.",
            "implementationStatus": "unverifiable",
            "issueCreation": {"action": "manual_review", "reason": "Missing context."},
        },
    ]
    eligible, skipped, blocked, partial = story_tools._reconcile_stories_for_issue_creation(
        stories
    )
    assert [s["storyId"] for s in skipped] == ["STORY-001"]
    assert [s["storyId"] for s in blocked] == ["STORY-004"]
    assert any(s.get("originalStorySummary") for s in eligible)
    assert partial and partial[0]["storyId"] == "STORY-002"
    remaining = next(s for s in eligible if s.get("originalStorySummary"))
    assert remaining["acceptanceCriteria"] == ["B."]
    assert remaining["requirements"] == ["R2."]


async def test_child_bindings_propagate_order_hierarchy_and_publish_intent(tmp_path):
    async with _catalog_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = PresetCatalogService(session)
            await service.sync_seed_templates(seed_dir=_seed_dir(tmp_path))
            expanded = await service.expand_template(
                slug="jira-breakdown-implement",
                scope="global",
                scope_ref=None,
                inputs={
                    "feature_request": "Inline source.",
                    "source_design_path": "",
                    "source_issue_key": "MM-1",
                    "jira_project_key": "MM",
                    "jira_issue_type": "Story",
                    "jira_dependency_mode": "linear_blocker_chain",
                    "publish_mode": "pr_with_merge_automation",
                    "run_verify": True,
                },
                context={"targetRuntime": "codex", "repository": "MoonLadderStudios/MoonMind"},
            )
    by_skill = {}
    for step in expanded["steps"]:
        skill_id = (step.get("skill") or step.get("tool"))["id"]
        by_skill.setdefault(skill_id, step)
    create = by_skill["story.create_jira_issues"]
    assert create["storyOutput"]["jira"]["dependencyMode"] == "linear_blocker_chain"
    assert create["storyOutput"]["jira"]["sourceIssueKey"] == "MM-1"
    downstream = by_skill["story.create_jira_implement_tasks"]
    assert "dependsOn" in downstream["instructions"]
    assert "MM-1" in downstream["instructions"]
    assert downstream["jiraOrchestration"]["task"]["inputs"] == {"run_verify": True}
    assert downstream["jiraOrchestration"]["task"]["publish"] == {
        "mode": "pr",
        "mergeAutomation": {"enabled": True},
    }
    assert "must not run implementation inline" in downstream["instructions"]
    assert "A created PR alone is not evidence" in downstream["instructions"]
